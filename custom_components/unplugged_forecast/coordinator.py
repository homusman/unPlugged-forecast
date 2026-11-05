"""Recorder-backed baseload and SoC-informed EV session forecast."""
from __future__ import annotations

from datetime import datetime, timedelta
import asyncio
import logging
import math
from homeassistant.components.recorder import get_instance
from homeassistant.components.recorder.statistics import statistics_during_period
from homeassistant.core import callback
from homeassistant.helpers.event import async_track_state_change_event, async_track_time_interval, async_call_later
from homeassistant.util import dt as dt_util
from .const import DOMAIN, UPDATE_MINUTES, DEFAULT_EFFICIENCY

_LOGGER = logging.getLogger(__name__)


def unit_to_kw(value, unit):
    unit = (unit or "").strip().lower()
    if unit == "w":
        return value / 1000.0
    if unit == "kw":
        return value
    if unit == "mw":
        return value * 1000.0
    raise ValueError(f"Power sensor has unsupported unit {unit!r}; use W, kW or MW")


def row_value(row):
    value = row.get("mean")
    return float(value) if value is not None and math.isfinite(float(value)) else None


def current_power(hass, entity_id):
    state = hass.states.get(entity_id)
    if state is None or state.state in ("unavailable", "unknown"):
        raise ValueError(f"Missing power sensor: {entity_id}")
    return unit_to_kw(float(state.state), state.attributes.get("unit_of_measurement"))


def current_soc(hass, entity_id):
    state = hass.states.get(entity_id)
    if state is None:
        raise ValueError(f"Missing SoC sensor: {entity_id}")
    value = float(state.state)
    if not math.isfinite(value) or not 0 <= value <= 100:
        raise ValueError(f"Invalid EV SoC: {entity_id}")
    return value


class ForecastCoordinator:
    def __init__(self, hass, entry):
        self.hass = hass
        self.entry = entry
        self.listeners = []
        self._unsubs = []
        self._timer = None
        self._lock = asyncio.Lock()
        self._baseline = None
        self._history_count = 0
        self.current_kw = None
        self.baseload_kw = None
        self.ev_kw = None
        self.forecast = []
        self.ev_details = []
        self.status = "Waiting for history"
        self.updated_at = None

    @property
    def options(self):
        return {**self.entry.data, **self.entry.options}

    @property
    def evs(self):
        return self.entry.options.get("evs", self.entry.data.get("evs", []))

    def add_listener(self, listener):
        self.listeners.append(listener)

    @callback
    def _notify(self):
        for listener in self.listeners:
            if listener.hass is not None:
                listener.async_write_ha_state()

    async def async_start(self):
        await self.async_refresh_history()
        watched = [self.options["house_load"], self.options["ev_power"]]
        watched.extend(e["soc_sensor"] for e in self.evs)
        self._unsubs.append(async_track_state_change_event(
            self.hass, watched, self._source_changed
        ))
        self._unsubs.append(async_track_time_interval(
            self.hass, self.async_refresh_history, timedelta(minutes=UPDATE_MINUTES)
        ))

    async def async_stop(self):
        for unsub in self._unsubs:
            unsub()
        self._unsubs.clear()
        if self._timer is not None:
            self._timer()
            self._timer = None

    @callback
    def _source_changed(self, event):
        # A short debounce is sufficient to limit HAEO optimizations from
        # continuously reporting power and SoC sensors.
        if self._timer is None:
            self._timer = async_call_later(self.hass, 20, self._on_debounce)

    async def _on_debounce(self, _now):
        self._timer = None
        await self.async_publish()

    def _history_sync(self, start, end, house_id, ev_id):
        # Recorder's blocking statistics query runs in the executor.
        return statistics_during_period(
            self.hass, start, end, {house_id, ev_id},
            "hour", None, {"mean"},
        )

    async def async_refresh_history(self, _now=None):
        async with self._lock:
            try:
                opt = self.options
                now = dt_util.utcnow()
                days = opt["history_days"]
                start = now - timedelta(days=days)
                ids = [opt["house_load"], opt["ev_power"]]
                stats = await get_instance(self.hass).async_add_executor_job(
                    self._history_sync, start, now, *ids
                )

                house_unit = self.hass.states.get(ids[0]).attributes.get("unit_of_measurement")
                ev_unit = self.hass.states.get(ids[1]).attributes.get("unit_of_measurement")
                # Reconcile by UTC hour, then map each hour to local time of day.
                def index_rows(rows, unit):
                    result = {}
                    for row in rows or []:
                        val = row_value(row)
                        if val is None:
                            continue
                        at = row.get("start")
                        if isinstance(at, (float, int)):
                            at = datetime.fromtimestamp(at, dt_util.UTC)
                        elif isinstance(at, str):
                            at = datetime.fromisoformat(at.replace("Z", "+00:00"))
                        if at is None:
                            continue
                        result[at.astimezone(dt_util.UTC).replace(minute=0, second=0, microsecond=0)] = unit_to_kw(val, unit)
                    return result

                house = index_rows(stats.get(ids[0], []), house_unit)
                ev = index_rows(stats.get(ids[1], []), ev_unit)
                values = [[] for _ in range(24)]
                for at, h in house.items():
                    # Do not incorrectly absorb EV spikes when EV history is absent.
                    if at not in ev:
                        continue
                    local = dt_util.as_local(at)
                    values[local.hour].append(max(0.0, h - max(0.0, ev[at])))
                self._history_count = sum(map(len, values))
                self._baseline = [
                    sum(hour) / len(hour) if hour else None for hour in values
                ]
            except Exception as exc:
                _LOGGER.warning("Unable to load energy history: %s", exc)
                self._baseline = None
                self._history_count = 0
        await self.async_publish()

    async def async_publish(self):
        try:
            opt = self.options
            now = dt_util.utcnow()
            home = current_power(self.hass, opt["house_load"])
            ev_measured = max(0.0, current_power(self.hass, opt["ev_power"]))
            live_base = max(0.0, home - ev_measured)

            # Forecast the next session per EV; never assume a session repeats
            # daily within a single forecast horizon.
            sessions = []
            ev_details = []
            active_index = None
            if ev_measured > 0.2 and self.evs:
                # A shared EV power sensor cannot reveal which EV is charging.
                # Select the lowest relative SoC as a documented fallback.
                active_index = min(range(len(self.evs)),
                                   key=lambda i: current_soc(self.hass, self.evs[i]["soc_sensor"]))
            for index, ev in enumerate(self.evs):
                soc = current_soc(self.hass, ev["soc_sensor"])
                low, high = float(ev["min_soc"]), float(ev["max_soc"])
                remaining = max(0.0, (high - soc) / 100.0 * float(ev["capacity_kwh"]))
                fraction = max(0.0, min(1.0, (high - soc) / (high - low)))
                probability = fraction ** float(ev["probability_gamma"])
                active = index == active_index
                power = ev_measured if active else float(ev["charge_power_kw"])
                if active:
                    probability = 1.0
                    start = now
                else:
                    local = dt_util.as_local(now)
                    start_local = local.replace(hour=int(ev["start_hour"]), minute=0,
                                                second=0, microsecond=0)
                    if start_local <= local:
                        start_local += timedelta(days=1)
                    start = start_local.astimezone(dt_util.UTC)
                # Expected charge load = probability-weighted energy over
                # the time required for a full charging event.
                required_ac_kwh = remaining / DEFAULT_EFFICIENCY
                duration_h = required_ac_kwh / power if power > 0 else 0
                if duration_h > 0 and probability > 0:
                    sessions.append({
                        "start": start, "end": start + timedelta(hours=duration_h),
                        "power": power * probability, "name": ev["name"],
                    })
                ev_details.append({
                    "name": ev["name"], "soc": round(soc, 1),
                    "charge_probability": round(probability, 3),
                    "energy_to_target_kwh": round(required_ac_kwh, 2),
                    "expected_energy_kwh": round(required_ac_kwh * probability, 2),
                    "start": start.isoformat(), "active_assumed": active,
                })

            interval = int(opt["interval_minutes"])
            count = math.ceil(int(opt["horizon_hours"]) * 60 / interval)
            period = timedelta(minutes=interval)
            forecast = []
            for i in range(count + 1):
                at = now + i * period
                end = at + period
                local = dt_util.as_local(at)
                hourly = self._baseline[local.hour] if self._baseline is not None else None
                base = hourly if hourly is not None else live_base
                predicted_ev = 0.0
                for session in sessions:
                    overlap_s = max(0.0, (min(end, session["end"]) -
                                         max(at, session["start"])).total_seconds())
                    predicted_ev += session["power"] * overlap_s / period.total_seconds()
                # First point captures actual current consumption. Future
                # forecast bins use cleaned historical baseline plus EV plan.
                value = home if i == 0 else max(0.0, base) + predicted_ev
                forecast.append({"time": at.isoformat(), "value": round(max(0.0, value), 3)})

            self.current_kw = home
            self.baseload_kw = live_base
            self.ev_kw = ev_measured
            self.forecast = forecast
            self.ev_details = ev_details
            self.updated_at = now.isoformat()
            if self._history_count < 24:
                self.status = f"Limited history ({self._history_count} matched hours); live baseline fallback used"
            else:
                self.status = f"OK: {self._history_count} matched non-EV historical hours"
        except Exception as exc:
            self.status = f"Unavailable: {exc}"
            self.forecast = []
            self.current_kw = None
            _LOGGER.warning("unPlugged forecast error: %s", exc)
        self._notify()
