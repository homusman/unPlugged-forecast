"""Configuration and options flows for unPlugged Forecast."""
from __future__ import annotations

import uuid
import voluptuous as vol

from homeassistant import config_entries
from homeassistant.helpers import selector

from .const import (
    DOMAIN, DEFAULT_DAYS, DEFAULT_HORIZON, DEFAULT_INTERVAL,
    DEFAULT_CHARGE_POWER, DEFAULT_START_HOUR, DEFAULT_GAMMA,
)

def main_schema(values=None):
    data = values or {}
    return vol.Schema({
        vol.Required("house_load", default=data.get("house_load", "")):
            selector.EntitySelector(selector.EntitySelectorConfig(domain="sensor")),
        vol.Required("ev_power", default=data.get("ev_power", "")):
            selector.EntitySelector(selector.EntitySelectorConfig(domain="sensor")),
        vol.Required("history_days", default=data.get("history_days", DEFAULT_DAYS)):
            vol.All(vol.Coerce(int), vol.Range(min=1, max=30)),
        vol.Required("horizon_hours", default=data.get("horizon_hours", DEFAULT_HORIZON)):
            vol.All(vol.Coerce(int), vol.Range(min=6, max=72)),
        vol.Required("interval_minutes", default=data.get("interval_minutes", DEFAULT_INTERVAL)):
            vol.In([15, 30, 60]),
        vol.Required("live_decay_minutes", default=data.get("live_decay_minutes", 30)):
            vol.All(vol.Coerce(int), vol.Range(min=0, max=240)),
        vol.Required("input_grace_minutes", default=data.get("input_grace_minutes", 10)):
            vol.All(vol.Coerce(int), vol.Range(min=0, max=120)),
    })

def ev_schema(values=None):
    data = values or {}
    return vol.Schema({
        vol.Required("name", default=data.get("name", "EV")): str,
        vol.Required("min_soc", default=data.get("min_soc", 20.0)):
            vol.All(vol.Coerce(float), vol.Range(min=0, max=99)),
        vol.Required("max_soc", default=data.get("max_soc", 80.0)):
            vol.All(vol.Coerce(float), vol.Range(min=1, max=100)),
        vol.Required("capacity_kwh", default=data.get("capacity_kwh", 60.0)):
            vol.All(vol.Coerce(float), vol.Range(min=1, max=250)),
        vol.Required("soc_sensor", default=data.get("soc_sensor", "")):
            selector.EntitySelector(selector.EntitySelectorConfig(domain="sensor")),
        vol.Optional("away_sensor", description={"suggested_value": data.get("away_sensor")}):
            selector.EntitySelector(selector.EntitySelectorConfig(
                domain=["binary_sensor", "device_tracker"]
            )),
        vol.Required("charge_power_kw", default=data.get("charge_power_kw", DEFAULT_CHARGE_POWER)):
            vol.All(vol.Coerce(float), vol.Range(min=0.1, max=50)),
        vol.Required("start_hour", default=data.get("start_hour", DEFAULT_START_HOUR)):
            vol.All(vol.Coerce(int), vol.Range(min=0, max=23)),
        vol.Required("probability_gamma", default=data.get("probability_gamma", DEFAULT_GAMMA)):
            vol.All(vol.Coerce(float), vol.Range(min=0.2, max=8)),
    })

def valid_ev(data):
    return data["min_soc"] < data["max_soc"] and bool(data["name"].strip())

class UnpluggedFlow(config_entries.ConfigFlow, domain=DOMAIN):
    VERSION = 1

    async def async_step_user(self, user_input=None):
        if user_input is not None:
            await self.async_set_unique_id("unplugged_forecast")
            self._abort_if_unique_id_configured()
            return self.async_create_entry(
                title="unPlugged Forecast",
                data={**user_input, "evs": []},
            )
        return self.async_show_form(step_id="user", data_schema=main_schema())

    @staticmethod
    def async_get_options_flow(config_entry):
        return UnpluggedOptionsFlow()

class UnpluggedOptionsFlow(config_entries.OptionsFlow):
    def __init__(self):
        self._ev_id = None

    async def async_step_init(self, user_input=None):
        return self.async_show_menu(
            step_id="init",
            menu_options=["settings", "add_ev", "edit_ev", "remove_ev"],
        )

    async def async_step_settings(self, user_input=None):
        if user_input is not None:
            opts = dict(self.config_entry.options)
            opts.update(user_input)
            return self.async_create_entry(title="", data=opts)
        return self.async_show_form(
            step_id="settings",
            data_schema=main_schema({**self.config_entry.data, **self.config_entry.options}),
        )

    def _evs(self):
        return list(self.config_entry.options.get("evs", self.config_entry.data.get("evs", [])))

    async def async_step_add_ev(self, user_input=None):
        if user_input is not None:
            if not valid_ev(user_input):
                return self.async_show_form(step_id="add_ev", data_schema=ev_schema(user_input),
                                            errors={"base": "invalid_soc_range"})
            evs = self._evs()
            evs.append({**user_input, "id": uuid.uuid4().hex[:12]})
            return self.async_create_entry(title="", data={**self.config_entry.options, "evs": evs})
        return self.async_show_form(step_id="add_ev", data_schema=ev_schema())

    async def async_step_edit_ev(self, user_input=None):
        if user_input is None:
            evs = self._evs()
            if not evs:
                return self.async_abort(reason="no_evs")
            return self.async_show_form(
                step_id="edit_ev",
                data_schema=vol.Schema({
                    vol.Required("ev_id"): selector.SelectSelector(
                        selector.SelectSelectorConfig(options=[
                            selector.SelectOptionDict(value=e["id"], label=e["name"])
                            for e in evs
                        ])
                    )
                }),
            )
        self._ev_id = user_input["ev_id"]
        return await self.async_step_edit_ev_details()

    async def async_step_edit_ev_details(self, user_input=None):
        evs = self._evs()
        existing = next((e for e in evs if e["id"] == self._ev_id), None)
        if existing is None:
            return self.async_abort(reason="no_evs")
        if user_input is not None:
            if not valid_ev(user_input):
                return self.async_show_form(
                    step_id="edit_ev_details", data_schema=ev_schema(user_input),
                    errors={"base": "invalid_soc_range"},
                )
            evs = [{**user_input, "id": e["id"]} if e["id"] == self._ev_id else e for e in evs]
            return self.async_create_entry(title="", data={**self.config_entry.options, "evs": evs})
        return self.async_show_form(step_id="edit_ev_details", data_schema=ev_schema(existing))

    async def async_step_remove_ev(self, user_input=None):
        evs = self._evs()
        if not evs:
            return self.async_abort(reason="no_evs")
        if user_input is not None:
            evs = [e for e in evs if e["id"] != user_input["ev_id"]]
            return self.async_create_entry(title="", data={**self.config_entry.options, "evs": evs})
        return self.async_show_form(
            step_id="remove_ev",
            data_schema=vol.Schema({
                vol.Required("ev_id"): selector.SelectSelector(
                    selector.SelectSelectorConfig(options=[
                        selector.SelectOptionDict(value=e["id"], label=e["name"]) for e in evs
                    ])
                )
            }),
        )
