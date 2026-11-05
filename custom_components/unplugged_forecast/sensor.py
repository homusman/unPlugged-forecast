"""Forecast and diagnostic sensors."""
from __future__ import annotations

from homeassistant.components.sensor import SensorEntity, SensorDeviceClass, SensorStateClass
from .const import DOMAIN


async def async_setup_entry(hass, entry, async_add_entities):
    coordinator = entry.runtime_data
    async_add_entities([
        UnpluggedSensor(coordinator, "forecast", "Load forecast"),
        UnpluggedSensor(coordinator, "baseload", "Live non-EV baseload"),
        UnpluggedSensor(coordinator, "ev_load", "Live EV charging load"),
        UnpluggedSensor(coordinator, "status", "Forecast status"),
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
        if key != "status":
            self._attr_device_class = SensorDeviceClass.POWER
            self._attr_native_unit_of_measurement = "kW"
            self._attr_state_class = SensorStateClass.MEASUREMENT

    @property
    def device_info(self):
        return {
            "identifiers": {(DOMAIN, self.coordinator.entry.entry_id)},
            "name": "unPlugged Forecast",
            "manufacturer": "homusman",
            "model": "Probabilistic EV load forecasting v0.1",
        }

    @property
    def available(self):
        return self.key == "status" or self.coordinator.current_kw is not None

    @property
    def native_value(self):
        c = self.coordinator
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
        if self.key == "forecast":
            return {
                "forecast": c.forecast,
                "ev_predictions": c.ev_details,
                "matched_history_hours": c._history_count,
                "updated_at": c.updated_at,
            }
        return None
