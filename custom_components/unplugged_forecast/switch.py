"""Opt-in Home Assistant charge reminders."""
from homeassistant.components.switch import SwitchEntity
from .planning_entity import PlanningEntity


async def async_setup_entry(hass, entry, async_add_entities):
    async_add_entities([ChargeReminders(entry.runtime_data, ev, "charge_reminders", "Charge reminders")
                        for ev in entry.runtime_data.evs])


class ChargeReminders(PlanningEntity, SwitchEntity):
    _attr_icon = "mdi:bell-outline"

    @property
    def is_on(self):
        return self.settings.get("reminders", False)

    async def async_turn_on(self, **kwargs):
        await self.coordinator.async_set_plan(self.ev["id"], "reminders", True)

    async def async_turn_off(self, **kwargs):
        await self.coordinator.async_set_plan(self.ev["id"], "reminders", False)
