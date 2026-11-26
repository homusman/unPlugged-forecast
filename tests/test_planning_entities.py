"""Planning controls and sensors are exposed by the actual HA platforms."""
import importlib
from pathlib import Path
import sys
from types import ModuleType, SimpleNamespace
import unittest
from unittest.mock import AsyncMock
from datetime import time

try:
    import homeassistant
except ImportError:
    homeassistant = None


@unittest.skipIf(homeassistant is None, "Home Assistant required for planning platforms")
class PlanningEntityTests(unittest.IsolatedAsyncioTestCase):
    def setUp(self):
        package = ModuleType("unplugged_planning_entities_test")
        package.__path__ = [str(Path(__file__).parents[1] / "custom_components/unplugged_forecast")]
        sys.modules[package.__name__] = package
        self.package = package.__name__
        self.ev = {"id": "car", "name": "Tesla", "start_hour": 18}
        self.c = SimpleNamespace(entry=SimpleNamespace(entry_id="hub"), evs=[self.ev],
                                 planning={}, charge_plans={}, listeners=[], async_set_plan=AsyncMock())
        self.entry = SimpleNamespace(runtime_data=self.c)

    async def entities(self, platform):
        module = importlib.import_module(f"{self.package}.{platform}")
        added = []
        await module.async_setup_entry(None, self.entry, added.extend)
        return added

    async def test_every_control_is_on_the_existing_device_and_has_stable_id(self):
        controls = []
        for platform in ("select", "time", "number", "switch", "button"):
            controls.extend(await self.entities(platform))
        self.assertEqual(len(controls), 6)
        ids = [entity.unique_id for entity in controls]
        self.assertEqual(len(set(ids)), 6)
        for entity in controls:
            self.assertEqual(entity.device_info["identifiers"], {("unplugged_forecast", "hub")})
            self.assertTrue(entity.unique_id.startswith("hub_car_"))
        select, = await self.entities("select")
        self.assertEqual(select.current_option, "Off")
        self.assertIn("Auto", select.options)
        await select.async_select_option("Tomorrow night")
        self.c.async_set_plan.assert_awaited_with("car", "mode", "Tomorrow night")

    async def test_time_driving_reminder_and_replan_controls_delegate_to_coordinator(self):
        start, ready = await self.entities("time")
        self.assertEqual(start.native_value, time(18))
        self.assertEqual(ready.native_value, time(7))
        await ready.async_set_value(time(8, 30))
        self.c.async_set_plan.assert_awaited_with("car", "ready_time", "08:30:00")
        number, = await self.entities("number")
        self.assertEqual(number.native_value, 8)
        await number.async_set_native_value(10)
        self.c.async_set_plan.assert_awaited_with("car", "daily_kwh", 10)
        switch, = await self.entities("switch")
        self.assertFalse(switch.is_on)
        await switch.async_turn_on()
        self.c.async_set_plan.assert_awaited_with("car", "reminders", True)
        button, = await self.entities("button")
        await button.async_press()
        self.c.async_set_plan.assert_awaited_with("car", "replan", None)

    async def test_sensors_publish_dates_energy_status_and_shortfall(self):
        # Existing sensors register immediately; provide their listener hook.
        self.c.add_listener = self.c.listeners.append
        sensors = await self.entities("sensor")
        plans = {entity.key: entity for entity in sensors if hasattr(entity, "ev")}
        self.assertEqual(set(plans), {"next_charge", "charge_energy", "charge_status", "driving_energy"})
        self.c.charge_plans["car"] = {"status": "Scheduled", "start": "2026-11-28T07:00:00+00:00",
                                      "energy_kwh": 20, "daily_driving_kwh": 8, "can_finish": False}
        self.assertIsNotNone(plans["next_charge"].native_value.tzinfo)
        self.assertEqual(plans["charge_energy"].native_value, 20)
        self.assertEqual(plans["charge_status"].native_value, "Window too short")
        self.assertEqual(plans["driving_energy"].native_value, 8)
