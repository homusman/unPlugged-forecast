"""Recorder-backed historical baseload and EV-session-aware forecast."""
from __future__ import annotations

import asyncio
from datetime import datetime, timedelta
import logging
import math

from homeassistant.components.recorder import get_instance
from homeassistant.components.recorder.history import get_significant_states
from homeassistant.components.recorder.statistics import statistics_during_period
from homeassistant.core import callback
from homeassistant.helpers.event import (
    async_call_later, async_track_state_change_event, async_track_time_interval,
)
from homeassistant.util import dt as dt_util

from .const import DEFAULT_EFFICIENCY
from .timeline import construct_forecast
from .learning import (
    candidate_weights, extract_sessions, parse_series, power_multiplier,
    recent_soc_delta, soc_probability,
)

_LOGGER = logging.getLogger(__name__)
HISTORY_REFRESH_MINUTES = 60
ACTIVE_THRESHOLD_KW = 0.25


def unit_to_kw(value, unit):
    mult = power_multiplier(unit)
    if mult is None:
        raise ValueError(f"Power sensor unit must be W, kW or MW, got {unit!r}")
    v = float(value) * mult
    if not math.isfinite(v):
        raise ValueError("Nonfinite power")
    return v


def current_power(hass, entity_id):
    state = hass.states.get(entity_id)
    if not state or state.state in ("unavailable", "unknown"):
        raise ValueError(f"Unavailable power sensor: {entity_id}")
    return unit_to_kw(state.state, state.attributes.get("unit_of_measurement"))


def current_soc(hass, entity_id):
    state = hass.states.get(entity_id)
    if not state or state.state in ("unavailable", "unknown"):
        raise ValueError(f"Unavailable EV SoC: {entity_id}")
    value = float(state.state)
    if not math.isfinite(value) or not 0 <= value <= 100:
        raise ValueError(f"Invalid EV SoC: {entity_id}")
    return value


def state_is_away(hass, entity_id):
    """Optional away indicator: binary_sensor on or device_tracker not_home."""
    if not entity_id:
        return None
    state = hass.states.get(entity_id)
    if not state or state.state in ("unknown", "unavailable"):
        return None
    return state.state.lower() in ("on", "not_home", "away")


def history_index(rows, multiplier):
    data = {}
    for row in rows or []:
        val = row.get("mean")
        if val is None:
            continue
        try:
            value = float(val) * multiplier
            at = row.get("start")
            if isinstance(at, (int, float)):
                at = datetime.fromtimestamp(at, dt_util.UTC)
            elif isinstance(at, str):
                at = datetime.fromisoformat(at.replace("Z", "+00:00"))
            if not at or not math.isfinite(value):
                continue
            at = at.astimezone(dt_util.UTC).replace(minute=0, second=0, microsecond=0)
            data[at] = value
        except (ValueError, TypeError):
            continue
    return data


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
        self._sessions = []
        self._soc_history = {}
        self._live_soc = {}
        # Last valid readings; a temporary HA unavailable state does not
        # immediately invalidate a slow-moving forecast.
        self._input_cache = {}
        self._input_health = {}
        self._active_since = None
        self.current_kw = None
        self.baseload_kw = None
        self.ev_kw = None
        self.forecast = []
        self.ev_details = []
        self.status = "Waiting for history"
        self.updated_at = None
        self._last_emitted = None

    @property
    def options(self):
        return {**self.entry.data, **self.entry.options}

    @property
    def evs(self):
        return self.entry.options.get("evs", self.entry.data.get("evs", []))

    def _input(self, entity_id, kind, now):
        """Read a live sensor with a bounded, observable last-good-value grace.

        Cache age is measured from the last *valid* reading, never refreshed
        by repeated unavailable events. This prevents stale values persisting
        indefinitely. Missing optional away states degrade to None.
        """
        state = self.hass.states.get(entity_id)
        try:
            if kind == "power":
                value = current_power(self.hass, entity_id)
            elif kind == "soc":
                value = current_soc(self.hass, entity_id)
            elif kind == "away":
                if not state or state.state in ("unknown", "unavailable"):
                    raise ValueError(f"Unavailable away sensor: {entity_id}")
                value = state_is_away(self.hass, entity_id)
            else:
                raise ValueError(f"Unexpected input kind: {kind}")
        except (ValueError, TypeError) as exc:
            cached = self._input_cache.get((entity_id, kind))
            age = (now - cached[1]).total_seconds() if cached else None
            grace_s = int(self.options.get("input_grace_minutes", 10)) * 60
            if cached is not None and age is not None and 0 <= age <= grace_s:
                self._input_health[entity_id] = {
                    "status": "stale", "age_seconds": round(age),
                    "grace_seconds": grace_s, "source_state": state.state if state else "missing",
                }
                return cached[0]
            self._input_health[entity_id] = {
                "status": "expired" if cached else "missing",
                "age_seconds": round(age) if age is not None else None,
                "grace_seconds": grace_s, "source_state": state.state if state else "missing",
            }
            if kind == "away":
                return None
            raise ValueError(f"{exc}; cached reading expired or absent") from exc
        self._input_cache[(entity_id, kind)] = (value, now)
        self._input_health.pop(entity_id, None)
        return value

    def add_listener(self, entity):
        self.listeners.append(entity)

    @callback
    def _notify(self):
        # Avoid making HAEO recalculate on each sensor event when neither
        # the live demand nor the future forecast changed materially.
        # A charging transition changes predicted EV power by far more than
        # the 100 W deadband and always publishes.
        signature = (
            self.status,
            self.current_kw,
            tuple((p["time"], p["value"]) for p in self.forecast),
        )
        previous = self._last_emitted
        changed = previous is None or previous[0] != signature[0]
        if not changed:
            old_now, old_forecast = previous[1], previous[2]
            new_now, new_forecast = signature[1], signature[2]
            if old_now is None or new_now is None:
                changed = old_now != new_now
            else:
                changed = abs(old_now - new_now) >= 0.10
            if not changed:
                if len(old_forecast) != len(new_forecast):
                    changed = True
                else:
                    changed = any(
                        ta != tb or abs(va - vb) >= 0.10
                        for (ta, va), (tb, vb) in zip(old_forecast, new_forecast)
                    )
        if not changed:
            return
        self._last_emitted = signature
        for listener in self.listeners:
            if listener.hass is not None:
                listener.async_write_ha_state()

    async def async_start(self):
        await self.async_refresh_history()
        watched = {self.options["house_load"], self.options["ev_power"]}
        watched.update(ev["soc_sensor"] for ev in self.evs)
        now = dt_util.utcnow()
        # Prime all configured inputs, including optional away states.
        for entity, kind in [(self.options["house_load"], "power"),
                             (self.options["ev_power"], "power")]:
            try:
                self._input(entity, kind, now)
            except ValueError:
                pass
        for ev in self.evs:
            if ev.get("away_sensor"):
                self._input(ev["away_sensor"], "away", now)
            try:
                self._live_soc.setdefault(ev["id"], []).append(
                    (now, self._input(ev["soc_sensor"], "soc", now))
                )
            except ValueError:
                pass
        watched.update(ev["away_sensor"] for ev in self.evs if ev.get("away_sensor"))
        self._unsubs.append(async_track_state_change_event(
            self.hass, list(watched), self._source_changed
        ))
        self._unsubs.append(async_track_time_interval(
            self.hass, self.async_refresh_history, timedelta(minutes=HISTORY_REFRESH_MINUTES)
        ))
        self._unsubs.append(async_track_time_interval(
            self.hass, self.async_publish, timedelta(minutes=5)
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
        entity_id = event.data.get("entity_id")
        now = dt_util.utcnow()
        for ev in self.evs:
            if entity_id != ev["soc_sensor"]:
                continue
            new = event.data.get("new_state")
            try:
                value = float(new.state)
                if not math.isfinite(value) or not 0 <= value <= 100:
                    break
                samples = self._live_soc.setdefault(ev["id"], [])
                samples.append((now, value))
                cutoff = now - timedelta(hours=12)
                self._live_soc[ev["id"]] = [
                    (at, val) for at, val in samples if at >= cutoff
                ]
            except (AttributeError, TypeError, ValueError):
                pass
            break
        if self._timer is None:
            self._timer = async_call_later(self.hass, 20, self._on_debounce)

    async def _on_debounce(self, _now):
        self._timer = None
        await self.async_publish()

    def _history_sync(self, start, end, house_id, ev_id, soc_ids):
        stats = statistics_during_period(
            self.hass, start, end, {house_id, ev_id}, "hour", None, {"mean"}
        )
        raw = get_significant_states(
            self.hass, start, end,
            [ev_id, *soc_ids], include_start_time_state=True,
            significant_changes_only=True, no_attributes=True,
        )
        return stats, raw

    async def async_refresh_history(self, _now=None):
        async with self._lock:
            try:
                opt = self.options
                now = dt_util.utcnow()
                days = int(opt["history_days"])
                house_id, ev_id = opt["house_load"], opt["ev_power"]
                ids = list({e["soc_sensor"] for e in self.evs})
                start = now - timedelta(days=max(days, 14))
                stats, raw = await get_instance(self.hass).async_add_executor_job(
                    self._history_sync, start, now, house_id, ev_id, ids
                )
                # Baseload uses only the configured N-day window, even if
                # EV-learning examines a longer period.
                house_state = self.hass.states.get(house_id)
                ev_state = self.hass.states.get(ev_id)
                if not house_state or not ev_state:
                    raise ValueError("History input sensor missing")
                house_factor = power_multiplier(house_state.attributes.get("unit_of_measurement"))
                ev_factor = power_multiplier(ev_state.attributes.get("unit_of_measurement"))
                if house_factor is None or ev_factor is None:
                    raise ValueError("History sensors need W/kW/MW power units")
                house = history_index(stats.get(house_id), house_factor)
                ev_data = history_index(stats.get(ev_id), ev_factor)
                daily_hours = [[] for _ in range(24)]
                recent_start = now - timedelta(days=days)
                for at, h in house.items():
                    if at < recent_start or at not in ev_data:
                        continue
                    daily_hours[dt_util.as_local(at).hour].append(
                        max(0.0, h - max(0.0, ev_data[at]))
                    )
                self._baseline = [
                    sum(samples) / len(samples) if samples else None
                    for samples in daily_hours
                ]
                self._history_count = sum(len(samples) for samples in daily_hours)

                power_rows = [
                    (stamp, value * ev_factor)
                    for stamp, value in parse_series(raw.get(ev_id))
                ]
                # Extend current on/off condition to 'now' so a session
                # still in progress appears as an active training example.
                if power_rows:
                    live_ev = self._input(ev_id, "power", now)
                    power_rows.append((now, max(0.0, live_ev)))
                soc_history = {
                    ev["id"]: parse_series(raw.get(ev["soc_sensor"]))
                    for ev in self.evs
                }
                self._sessions = extract_sessions(power_rows, soc_history)
                self._soc_history = soc_history
            except Exception as exc:
                _LOGGER.warning("Unable to refresh load/session history: %s", exc)
                # Retain last valid learned data rather than silently
                # overwriting it with a new empty model.
                self.status = f"History refresh failed: {exc}"
        await self.async_publish()

    def _select_active_ev(self, now):
        """Attribute ongoing charge using SoC increases, then explicit fallback."""
        gains = {}
        start = self._active_since or now - timedelta(hours=3)
        for ev in self.evs:
            rows = self._soc_history.get(ev["id"], [])
            # Include live values since the last hourly history fetch.
            recent = list(rows) + list(self._live_soc.get(ev["id"], []))
            try:
                soc = self._input(ev["soc_sensor"], "soc", now)
                if ev["soc_sensor"] not in self._input_health:
                    recent.append((now, soc))
            except ValueError:
                continue
            recent.sort(key=lambda row: row[0])
            gains[ev["id"]] = recent_soc_delta(recent, start - timedelta(minutes=10))
        if gains:
            winner = max(gains, key=gains.get)
            if gains[winner] >= 0.5:
                return winner, "SoC increase", gains
        if not self.evs:
            return None, "no EV configured", gains
        # Uncertain fallback, never label as a positive identification.
        # A missing SoC must not invalidate the entire forecast. Only
        # consider EVs whose live SoC is valid for uncertain attribution.
        eligible = []
        for ev in self.evs:
            try:
                soc = self._input(ev["soc_sensor"], "soc", now)
            except (ValueError, TypeError):
                continue
            eligible.append((ev, soc))
        if not eligible:
            return None, "no EV with valid SoC", gains
        ev, _ = min(
            eligible,
            key=lambda item: (
                item[1] - float(item[0]["min_soc"])
            ) / max(1.0, float(item[0]["max_soc"]) - float(item[0]["min_soc"])),
        )
        return ev["id"], "lowest relative SoC (unconfirmed)", gains

    async def async_publish(self, _now=None):
        try:
            opt = self.options
            now = dt_util.utcnow()
            horizon = int(opt["horizon_hours"])
            house = self._input(opt["house_load"], "power", now)
            measured_ev = max(0.0, self._input(opt["ev_power"], "power", now))
            base_live = max(0.0, house - measured_ev)
            charging = measured_ev >= ACTIVE_THRESHOLD_KW
            if charging and self._active_since is None:
                # Recorder can supply more accurate start through session history.
                ongoing = [
                    s for s in self._sessions
                    if s["end"] >= now - timedelta(minutes=30)
                ]
                self._active_since = ongoing[-1]["start"] if ongoing else now
            elif not charging:
                self._active_since = None
            active_ev_id, identification, gains = (
                self._select_active_ev(now) if charging else (None, "inactive", {})
            )

            sessions = []
            details = []
            unavailable_evs = []
            local_now = dt_util.as_local(now)
            for ev in self.evs:
                try:
                    soc = self._input(ev["soc_sensor"], "soc", now)
                except (ValueError, TypeError) as exc:
                    # Missing EV SoC -> assume no *future* charging demand for
                    # this EV. Do not let one unavailable MQTT sensor erase the
                    # independent household baseload forecast. Keep actual
                    # measured house and EV power for current-state reporting.
                    unavailable_evs.append(ev["name"])
                    details.append({
                        "name": ev["name"], "soc": None,
                        "charge_probability": 0.0,
                        "energy_to_target_kwh": 0.0,
                        "expected_energy_kwh": 0.0,
                        "start": None, "active_assumed": False,
                        "identification": f"SoC unavailable; future EV charging omitted ({exc})",
                        "soc_gains": {}, "away": self._input(ev["away_sensor"], "away", now) if ev.get("away_sensor") else None,
                        "candidate_sessions": [],
                        "learned_sessions": sum(
                            1 for session in self._sessions if session["ev_id"] == ev["id"]
                        ),
                    })
                    continue
                low = float(ev["min_soc"])
                high = float(ev["max_soc"])
                capacity = float(ev["capacity_kwh"])
                remaining_kwh = max(0.0, (high - soc) / 100.0 * capacity)
                ac_kwh = remaining_kwh / DEFAULT_EFFICIENCY
                p_soc = soc_probability(soc, low, high, float(ev["probability_gamma"]))
                active = ev["id"] == active_ev_id
                away = self._input(ev["away_sensor"], "away", now) if ev.get("away_sensor") else None
                identity = identification if active else "not charging"
                candidate_info = []
                if active:
                    # Always honour a detected session, including surprise
                    # charging near/above the configured normal maximum.
                    # If the configured target is already passed, reserve
                    # one hour provisionally until charger power stops.
                    power = measured_ev
                    unknown_target = ac_kwh <= 0.01
                    duration_h = ac_kwh / power if not unknown_target else 1.0
                    sessions.append({
                        "start": now, "end": now + timedelta(hours=duration_h),
                        "power": power, "ev_id": ev["id"]
                    })
                    probability = 1.0
                    start_iso = now.isoformat()
                    if unknown_target:
                        identity += "; target already reached, 1h provisional"
                else:
                    # A car known to be away may return needing a charge.
                    # This only increases the likelihood, not the predicted
                    # arrival time; a binary away flag cannot tell us ETA.
                    probability = min(1.0, p_soc * (1.25 if away else 1.0))
                    weights = candidate_weights(
                        local_now, ev, self._sessions, dt_util.as_local, horizon
                    )
                    # If currently away, arrival cannot be inferred from
                    # away=true alone; the start-time distribution remains
                    # probabilistic, rather than assuming the EV is home.
                    total_weight = sum(weight for _, weight in weights)
                    power = float(ev["charge_power_kw"])
                    duration_h = ac_kwh / power if power else 0
                    for at, weight in weights:
                        chance = probability * weight / total_weight if total_weight else 0
                        if chance <= 0 or duration_h <= 0:
                            continue
                        start = at.astimezone(dt_util.UTC)
                        sessions.append({
                            "start": start, "end": start + timedelta(hours=duration_h),
                            "power": power * chance, "ev_id": ev["id"]
                        })
                        candidate_info.append({
                            "start": start.isoformat(), "probability": round(chance, 4)
                        })
                    start_iso = candidate_info[0]["start"] if candidate_info else None
                details.append({
                    "name": ev["name"], "soc": round(soc, 1),
                    "charge_probability": round(probability, 4),
                    "energy_to_target_kwh": round(ac_kwh, 2),
                    "expected_energy_kwh": round(ac_kwh * probability, 2),
                    "start": start_iso, "active_assumed": active,
                    "identification": identity, "soc_gains": gains if active else {},
                    "away": away, "candidate_sessions": candidate_info[:18],
                    "learned_sessions": sum(
                        1 for s in self._sessions if s["ev_id"] == ev["id"]
                    ),
                })
            forecast = construct_forecast(
                now=now,
                horizon_hours=horizon,
                interval_minutes=int(opt["interval_minutes"]),
                base_by_hour=self._baseline,
                live_non_ev_kw=base_live,
                sessions=sessions,
                localize=dt_util.as_local,
                decay_minutes=float(opt.get("live_decay_minutes", 30)),
                actual_house_kw=house,
            )
            self.current_kw = house
            self.baseload_kw = base_live
            self.ev_kw = measured_ev
            self.forecast = forecast
            self.ev_details = details
            self.updated_at = now.isoformat()
            coverage = f"{self._history_count} baseline hours; {len(self._sessions)} historical sessions"
            if self._history_count < 24:
                self.status = f"Limited history: {coverage}"
            else:
                self.status = f"OK: {coverage}"
            if self._input_health:
                self.status += "; inputs: " + ", ".join(
                    f"{entity} {info['status']}"
                    for entity, info in sorted(self._input_health.items())
                )
            if unavailable_evs:
                self.status += (
                    "; EV SoC unavailable, charging forecast omitted for: "
                    + ", ".join(unavailable_evs)
                )
        except Exception as exc:
            self.status = f"Unavailable: {exc}"
            self.forecast = []
            self.current_kw = None
            _LOGGER.warning("unPlugged forecast error: %s", exc)
        self._notify()
