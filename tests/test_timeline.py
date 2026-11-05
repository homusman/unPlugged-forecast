"""Regression tests for HAEO forecast timing stability and EV session accounting."""
import importlib.util
from pathlib import Path
from datetime import datetime, timedelta, timezone
import unittest

FILE = Path(__file__).resolve().parents[1] / "custom_components" / "unplugged_forecast" / "timeline.py"
SPEC = importlib.util.spec_from_file_location("unplugged_timeline", FILE)
timeline = importlib.util.module_from_spec(SPEC)
SPEC.loader.exec_module(timeline)

UTC = timezone.utc


class TimelineTest(unittest.TestCase):
    def test_stable_grid_independent_of_refresh_second(self):
        a = datetime(2026, 11, 6, 4, 1, 5, tzinfo=UTC)
        b = a + timedelta(seconds=20)
        self.assertEqual(timeline.aligned_start(a, 30), timeline.aligned_start(b, 30))

    def test_forecast_does_not_spike_at_first_point(self):
        now = datetime(2026, 11, 6, 4, 2, 0, tzinfo=UTC)
        args = dict(
            horizon_hours=6, interval_minutes=30,
            base_by_hour=[2.0] * 24, live_non_ev_kw=2.0,
            sessions=[], localize=lambda x: x.astimezone(UTC),
            decay_minutes=30, actual_house_kw=5.0,
        )
        prediction = timeline.construct_forecast(now=now, **args)
        self.assertEqual(prediction[0]["value"], 2.0)
        self.assertEqual(prediction[1]["value"], 2.0)

    def test_active_charging_present_in_first_interval(self):
        now = datetime(2026, 11, 6, 4, 2, 0, tzinfo=UTC)
        sess = [{
            "start": now, "end": now + timedelta(hours=2),
            "power": 3.0, "ev_id": "ev",
        }]
        args = dict(
            horizon_hours=6, interval_minutes=30,
            base_by_hour=[2.0] * 24, live_non_ev_kw=2.0,
            sessions=sess, localize=lambda x: x.astimezone(UTC),
            decay_minutes=30, actual_house_kw=5.0,
        )
        a = timeline.construct_forecast(now=now, **args)
        b = timeline.construct_forecast(now=now + timedelta(seconds=20), **args)
        self.assertEqual(a[0]["value"], 5.0)
        self.assertEqual(b[0]["value"], 5.0)
        self.assertEqual(a[0]["time"], b[0]["time"])

    def test_ev_session_energy_finite(self):
        start = datetime(2026, 11, 6, 4, tzinfo=UTC)
        sess = [{
            "start": start, "end": start + timedelta(minutes=15),
            "power": 4, "ev_id": "ev",
        }]
        self.assertAlmostEqual(
            timeline.session_kw(start, start + timedelta(minutes=30), sess),
            2.0,
        )


if __name__ == "__main__":
    unittest.main()
