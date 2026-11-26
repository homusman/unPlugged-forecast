"""Driving consumption fallback until sufficient SoC history exists."""
from homeassistant.components.number import NumberEntity, NumberMode
from .planning_entity import PlanningEntity


async def async_setup_entry(hass, entry, async_add_entities):
    async_add_entities([DrivingFallback(entry.runtime_data, ev, "daily_kwh", "Daily driving fallback")
                        for ev in entry.runtime_data.evs])


class DrivingFallback(PlanningEntity, NumberEntity):
    _attr_native_min_value = 0
    _attr_native_max_value = 100
    _attr_native_step = 0.5
    _attr_native_unit_of_measurement = "kWh"
    _attr_mode = NumberMode.BOX
    _attr_icon = "mdi:car-electric"

    @property
    def native_value(self):
        return float(self.settings.get("daily_kwh", 8))

    async def async_set_native_value(self, value):
        await self.coordinator.async_set_plan(self.ev["id"], "daily_kwh", value)
