"""Choose a persistent charging intention for each EV."""
from homeassistant.components.select import SelectEntity
from .planning import MODES
from .planning_entity import PlanningEntity


async def async_setup_entry(hass, entry, async_add_entities):
    async_add_entities([ChargeSchedule(entry.runtime_data, ev) for ev in entry.runtime_data.evs])


class ChargeSchedule(PlanningEntity, SelectEntity):
    _attr_options = MODES
    _attr_icon = "mdi:calendar-clock"

    def __init__(self, coordinator, ev):
        super().__init__(coordinator, ev, "charge_schedule", "Charge schedule")

    @property
    def current_option(self):
        return self.settings.get("mode", "Off")

    async def async_select_option(self, option):
        await self.coordinator.async_set_plan(self.ev["id"], "mode", option)
