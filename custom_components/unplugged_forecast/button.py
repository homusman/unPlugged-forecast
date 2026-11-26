"""Explicit replanning after a missed or changed charging intention."""
from homeassistant.components.button import ButtonEntity
from .planning_entity import PlanningEntity


async def async_setup_entry(hass, entry, async_add_entities):
    async_add_entities([ReplanCharge(entry.runtime_data, ev, "replan_charge", "Replan charge")
                        for ev in entry.runtime_data.evs])


class ReplanCharge(PlanningEntity, ButtonEntity):
    _attr_icon = "mdi:calendar-refresh"

    async def async_press(self):
        await self.coordinator.async_set_plan(self.ev["id"], "replan", None)
