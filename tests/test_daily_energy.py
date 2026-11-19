"""Energy diagnostics account for partial days and actual elapsed time."""
from datetime import datetime, timedelta, timezone
import importlib.util
from pathlib import Path
import unittest
from zoneinfo import ZoneInfo

spec = importlib.util.spec_from_file_location(
    "energy_timeline", Path(__file__).parents[1] / "custom_components/unplugged_forecast/timeline.py"
)
timeline = importlib.util.module_from_spec(spec)
spec.loader.exec_module(timeline)


class EnergyTests(unittest.TestCase):
    def test_partial_intervals_and_no_extra_terminal_bucket(self):
        now = datetime(2026, 11, 20, 23, 45, tzinfo=timezone.utc)
        points = [{"time": (now.replace(minute=30) + timedelta(minutes=30 * i)).isoformat(),
                   "value": 2} for i in range(4)]
        result = timeline.daily_forecast_energy(points, now, 1, lambda t: t)
        self.assertEqual(result, {
            "2026-11-20": {"energy_kwh": 0.5, "coverage_hours": 0.25},
            "2026-11-21": {"energy_kwh": 1.5, "coverage_hours": 0.75},
        })

    def test_daylight_saving_days_have_23_or_25_hours(self):
        zone = ZoneInfo("Australia/Sydney")
        for month, day, hours in ((10, 4, 23), (4, 5, 25)):
            start = datetime(2026, month, day, tzinfo=zone).astimezone(timezone.utc)
            points = [{"time": (start + timedelta(hours=i)).isoformat(), "value": 1}
                      for i in range(hours + 1)]
            result = timeline.daily_forecast_energy(points, start, hours, lambda t: t.astimezone(zone))
            self.assertEqual(list(result.values()), [{"energy_kwh": hours, "coverage_hours": hours}])

    def test_empty_forecast_has_no_energy_estimate(self):
        self.assertEqual(timeline.daily_forecast_energy([], datetime.now(timezone.utc), 48, lambda t: t), {})
