"""Local charging window controls."""
from datetime import time
from homeassistant.components.time import TimeEntity
from .planning_entity import PlanningEntity


async def async_setup_entry(hass, entry, async_add_entities):
    async_add_entities([ChargeTime(entry.runtime_data, ev, key, label)
                        for ev in entry.runtime_data.evs
                        for key, label in (("start_time", "Charging starts"), ("ready_time", "Ready by"))])


class ChargeTime(PlanningEntity, TimeEntity):
    def __init__(self, coordinator, ev, key, label):
        super().__init__(coordinator, ev, key, label)
        self.key = key

    @property
    def native_value(self):
        default = f"{int(self.ev['start_hour']):02}:00:00" if self.key == "start_time" else "07:00:00"
        return time.fromisoformat(self.settings.get(self.key, default))

    async def async_set_value(self, value):
        await self.coordinator.async_set_plan(self.ev["id"], self.key, value.replace(microsecond=0).isoformat())
