"""Forecast and diagnostic sensors."""
from __future__ import annotations
from datetime import datetime
from .planning_entity import PlanningEntity

from homeassistant.components.sensor import SensorEntity, SensorDeviceClass, SensorStateClass
from .const import DOMAIN
from .timeline import daily_forecast_energy
from homeassistant.util import dt as dt_util
from homeassistant.const import EntityCategory


async def async_setup_entry(hass, entry, async_add_entities):
    coordinator = entry.runtime_data
    async_add_entities([
        UnpluggedSensor(coordinator, "forecast", "Load forecast"),
        UnpluggedSensor(coordinator, "baseload", "Live non-EV baseload"),
        UnpluggedSensor(coordinator, "ev_load", "Live EV charging load"),
        UnpluggedSensor(coordinator, "status", "Forecast status"),
        UnpluggedSensor(coordinator, "daily_energy", "Daily baseline energy"),
        *[ChargePlanSensor(coordinator, ev, key, label) for ev in coordinator.evs
          for key, label in (("next_charge", "Next planned charge"),
                             ("charge_energy", "Planned charge energy"),
                             ("charge_status", "Charge plan status"),
                             ("driving_energy", "Estimated daily driving energy"))],
    ])


class UnpluggedSensor(SensorEntity):
    _attr_should_poll = False
    _attr_has_entity_name = True

    def __init__(self, coordinator, key, name):
        self.coordinator = coordinator
        self.key = key
        self._attr_name = name
        self._attr_unique_id = f"{coordinator.entry.entry_id}_{key}"
        coordinator.add_listener(self)
        if key == "daily_energy":
            self._attr_device_class = SensorDeviceClass.ENERGY
            self._attr_native_unit_of_measurement = "kWh"
            self._attr_entity_category = EntityCategory.DIAGNOSTIC
        elif key != "status":
            self._attr_device_class = SensorDeviceClass.POWER
            self._attr_native_unit_of_measurement = "kW"
            self._attr_state_class = SensorStateClass.MEASUREMENT

    @property
    def device_info(self):
        return {
            "identifiers": {(DOMAIN, self.coordinator.entry.entry_id)},
            "name": "unPlugged Forecast",
            "manufacturer": "homusman",
            "model": "Probabilistic EV load forecasting",
        }

    @property
    def available(self):
        if self.key == "daily_energy":
            baseline = self.coordinator._baseline
            return bool(baseline) and all(value is not None for value in baseline)
        return self.key == "status" or self.coordinator.current_kw is not None

    @property
    def native_value(self):
        c = self.coordinator
        if self.key == "daily_energy":
            return round(sum(c._baseline), 3) if self.available else None
        if self.key == "forecast":
            return round(c.current_kw, 3) if c.current_kw is not None else None
        if self.key == "baseload":
            return round(c.baseload_kw, 3) if c.baseload_kw is not None else None
        if self.key == "ev_load":
            return round(c.ev_kw, 3) if c.ev_kw is not None else None
        return c.status

    @property
    def extra_state_attributes(self):
        c = self.coordinator
        if self.key == "daily_energy":
            return {
                "profile_hours_available": sum(v is not None for v in (c._baseline or [])),
                "description": "24-hour historical non-EV baseline; excludes EV forecasts and live correction",
            }
        if self.key == "forecast":
            return {
                "forecast": c.forecast,
                "interpolation_mode": "previous",
                "ev_predictions": c.ev_details,
                "matched_history_hours": c._history_count,
                "updated_at": c.updated_at,
                "input_health": c._input_health,
                "history_diagnostics": dict(c.history_diagnostics),
                "live_correction": dict(c.correction_diagnostics),
                "forecast_interval_minutes": int(c.options["interval_minutes"]),
                "daily_forecast_energy": daily_forecast_energy(
                    c.forecast, dt_util.utcnow(), int(c.options.get("horizon_hours", 48)),
                    dt_util.as_local,
                ) if c.current_kw is not None else {},
            }
        return None


class ChargePlanSensor(PlanningEntity, SensorEntity):
    def __init__(self, coordinator, ev, key, label):
        super().__init__(coordinator, ev, key, label)
        self.key = key
        if key == "next_charge":
            self._attr_device_class = SensorDeviceClass.TIMESTAMP
        elif key in ("charge_energy", "driving_energy"):
            self._attr_device_class = SensorDeviceClass.ENERGY
            self._attr_native_unit_of_measurement = "kWh"

    @property
    def native_value(self):
        if self.key == "next_charge":
            value = self.plan.get("start")
            return datetime.fromisoformat(value) if value and self.plan.get("status") in ("Scheduled", "Charging") else None
        if self.key == "charge_energy":
            return self.plan.get("energy_kwh", 0)
        if self.key == "driving_energy":
            return self.plan.get("daily_driving_kwh")
        status = self.plan.get("status", "Off")
        return "Window too short" if status == "Scheduled" and not self.plan.get("can_finish", True) else status
