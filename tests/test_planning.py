"""Charging intentions remain stable across clock changes and restarts."""
from datetime import datetime, timedelta, timezone
import importlib.util
import json
from pathlib import Path
import unittest
from zoneinfo import ZoneInfo

spec = importlib.util.spec_from_file_location("planning_test", Path(__file__).parents[1] / "custom_components/unplugged_forecast/planning.py")
planning = importlib.util.module_from_spec(spec)
spec.loader.exec_module(planning)
TZ = ZoneInfo("Australia/Sydney")
UTC = timezone.utc


class PlanningTests(unittest.TestCase):
    def setUp(self):
        self.now = datetime(2026, 11, 27, 10, tzinfo=TZ).astimezone(UTC)
        self.ev = {"id": "car", "start_hour": 18, "min_soc": 20, "capacity_kwh": 60, "charge_power_kw": 7}
        self.settings = {}

    def update(self, now=None, soc=50, target=80, active=False, power=3, rows=()):
        return planning.update_plan(self.settings, self.ev, now or self.now, soc, target,
                                    active, power, rows, lambda at: at.astimezone(TZ))

    def test_default_off_and_skip_do_not_create_intentions(self):
        self.assertEqual(self.update()["status"], "Off")
        self.settings["mode"] = "Skip"
        self.assertEqual(self.update()["status"], "Skipped")
        self.assertNotIn("plan", self.settings)

    def test_manual_date_is_concrete_and_survives_restart_and_midnight(self):
        self.settings["mode"] = "Tomorrow night"
        first = self.update()
        self.assertEqual(datetime.fromisoformat(first["start"]).astimezone(TZ).day, 28)
        self.settings = json.loads(json.dumps(self.settings))
        later = self.update(self.now + timedelta(hours=15))
        self.assertEqual(first["start"], later["start"])
        self.assertEqual(first["ready_by"], later["ready_by"])

    def test_each_manual_option_offsets_the_calendar_date(self):
        for offset, mode in enumerate(planning.MODES[2:-1]):
            self.settings = {"mode": mode}
            result = self.update()
            day = datetime.fromisoformat(result["start"]).astimezone(TZ).date()
            self.assertEqual(day, self.now.astimezone(TZ).date() + timedelta(days=offset))

    def test_projected_driving_increases_energy_before_future_session(self):
        self.settings = {"mode": "Tomorrow night", "daily_kwh": 8}
        result = self.update()
        self.assertGreater(result["energy_kwh"], 20)
        self.assertLess(result["projected_soc"], 50)
        self.assertEqual(result["driving_source"], "configured fallback")

    def test_auto_date_is_retained_when_soc_improves(self):
        self.settings = {"mode": "Auto"}
        first = self.update(soc=70)
        later = self.update(self.now + timedelta(hours=1), soc=75)
        self.assertEqual(first["start"], later["start"])
        self.assertGreater(datetime.fromisoformat(first["start"]), self.now + timedelta(days=1))

    def test_auto_can_bring_forward_a_reserve_risk(self):
        self.settings = {"mode": "Auto"}
        first = self.update(soc=70)
        later = self.update(self.now + timedelta(hours=1), soc=22)
        self.assertLess(later["start"], first["start"])
        self.assertIn("reserve", self.settings["plan"]["reason"])

    def test_active_session_uses_current_soc_and_measured_power(self):
        self.settings = {"mode": "Tonight"}
        first = self.update(active=True, power=6)
        second = self.update(active=True, power=3)
        self.assertEqual(first["energy_kwh"], 20)
        self.assertEqual(second["energy_kwh"], 20)
        self.assertEqual(second["power_kw"], 3)
        self.assertEqual(second["status"], "Charging")

    def test_manual_completion_and_auto_next_plan(self):
        self.settings = {"mode": "Tonight"}
        self.update(active=True)
        completed = self.update(active=False, soc=80)
        self.assertEqual(completed["status"], "Completed")
        self.assertEqual(completed["energy_kwh"], 0)
        self.settings["mode"] = "Auto"
        next_plan = self.update(soc=80)
        self.assertEqual(next_plan["status"], "Scheduled")
        self.assertGreater(next_plan["start"], completed["start"])

    def test_missed_and_incomplete_are_visible_without_silent_date_movement(self):
        for active, expected in ((False, "Missed"), (True, "Incomplete")):
            self.settings = {"mode": "Tonight"}
            first = self.update(active=active)
            late = self.update(self.now + timedelta(days=1))
            self.assertEqual(late["status"], expected)
            self.assertEqual(late["start"], first["start"])

    def test_window_shortfall_and_missing_soc(self):
        self.settings = {"mode": "Tonight", "start_time": "23:00:00", "ready_time": "23:30:00"}
        result = self.update()
        self.assertFalse(result["can_finish"])
        self.assertGreater(result["unserved_energy_kwh"], 0)
        self.assertEqual(self.update(soc=None)["status"], "Awaiting SoC")
        self.assertIn("plan", self.settings)

    def test_driving_rate_excludes_charge_gains_and_deduplicates_samples(self):
        rows = [(self.now + timedelta(hours=i*12), value)
                for i, value in enumerate((80, 70, 60, 50, 80, 70, 60, 50, 40))]
        expected = 70 * 0.6 / 4
        self.assertAlmostEqual(planning.driving_energy(rows + rows, 60), expected)
        self.assertIsNone(planning.driving_energy(rows[:3], 60))
        self.settings = {"mode": "Auto"}
        self.assertEqual(self.update(rows=rows)["driving_source"], "SoC history")

    def test_dst_and_overnight_windows_use_elapsed_utc_hours(self):
        day = datetime(2026, 10, 3).date()
        start, end = planning.window(day, {"start_time": "18:00:00", "ready_time": "07:00:00"}, self.ev, TZ)
        self.assertEqual((end-start).total_seconds()/3600, 12)
        self.assertEqual(end.astimezone(TZ).hour, 7)

    def test_zero_driving_and_zero_charge_target(self):
        self.settings = {"mode": "Auto", "daily_kwh": 0}
        self.assertEqual(self.update()["status"], "No charging needed")
        self.settings = {"mode": "Auto"}
        self.assertEqual(self.update(target=0)["status"], "No charging needed")


    def test_unplanned_charge_consumes_the_planned_session_and_moves_its_start(self):
        self.settings = {"mode": "Auto"}
        original = self.update(soc=70)
        active = self.update(soc=70, active=True)
        self.assertLess(active["start"], original["start"])
        self.assertEqual(active["start"], self.now.isoformat())
        complete = self.update(soc=80, active=True)
        self.assertEqual(complete["status"], "Completed")
        self.assertEqual(complete["energy_kwh"], 0)
        same = self.update(soc=80, active=True)
        self.assertEqual(same["start"], complete["start"])
        self.assertEqual(same["status"], "Completed")
