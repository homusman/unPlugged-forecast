"""Shared device linkage and lifecycle for per-EV planning entities."""
from homeassistant.helpers.entity import Entity
from .const import DOMAIN


class PlanningEntity(Entity):
    _attr_should_poll = False
    _attr_has_entity_name = True

    def __init__(self, coordinator, ev, key, label):
        self.coordinator = coordinator
        self.ev = ev
        self._attr_unique_id = f"{coordinator.entry.entry_id}_{ev['id']}_{key}"
        self._attr_name = f"{ev['name']} {label}"

    async def async_added_to_hass(self):
        await super().async_added_to_hass()
        self.coordinator.add_listener(self)
        self.async_on_remove(lambda: self.coordinator.listeners.remove(self))

    @property
    def device_info(self):
        return {"identifiers": {(DOMAIN, self.coordinator.entry.entry_id)}}

    @property
    def settings(self):
        return self.coordinator.planning.get(self.ev["id"], {})

    @property
    def plan(self):
        return self.coordinator.charge_plans.get(self.ev["id"], {})

    @property
    def extra_state_attributes(self):
        return dict(self.plan)
