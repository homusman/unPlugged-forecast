"""Regression checks for standalone session-learning functions.

Run with: python -m unittest discover -s tests -v
No Home Assistant installation is necessary for these pure-function tests.
"""
import importlib.util
from pathlib import Path
from datetime import datetime, timedelta, timezone
import unittest

FILE = Path(__file__).resolve().parents[1] / "custom_components" / "unplugged_forecast" / "learning.py"
SPEC = importlib.util.spec_from_file_location("unplugged_learning", FILE)
learning = importlib.util.module_from_spec(SPEC)
SPEC.loader.exec_module(learning)

UTC = timezone.utc


class LearningTest(unittest.TestCase):
    def test_probability_curve(self):
        self.assertAlmostEqual(learning.soc_probability(50, 20, 80, 2), 0.25)
        self.assertEqual(learning.soc_probability(80, 20, 80, 2), 0)
        self.assertEqual(learning.soc_probability(20, 20, 80, 2), 1)

    def test_sessions_use_soc_rise_to_identify_ev(self):
        start = datetime(2026, 11, 5, 18, 0, tzinfo=UTC)
        power = [
            (start - timedelta(minutes=2), 0),
            (start, 6.8),
            (start + timedelta(minutes=30), 6.8),
            (start + timedelta(hours=1), 0),
        ]
        soc = {
            "car_a": [(start, 32), (start + timedelta(minutes=30), 37)],
            "car_b": [(start, 52), (start + timedelta(minutes=30), 52)],
        }
        sessions = learning.extract_sessions(power, soc)
        self.assertEqual(len(sessions), 1)
        self.assertEqual(sessions[0]["ev_id"], "car_a")

    def test_no_false_identification_without_soc_growth(self):
        start = datetime(2026, 11, 5, 18, 0, tzinfo=UTC)
        power = [(start, 7.0), (start + timedelta(minutes=30), 0)]
        soc = {"car_a": [(start, 50), (start + timedelta(minutes=25), 50)]}
        self.assertIsNone(learning.extract_sessions(power, soc)[0]["ev_id"])

    def test_missed_assumed_slot_moves_to_next_day(self):
        now = datetime(2026, 11, 5, 20, 0, tzinfo=UTC)
        ev = {"id": "car_a", "start_hour": 18}
        starts = learning.candidate_weights(now, ev, [], lambda x: x, 48)
        self.assertTrue(starts)
        self.assertEqual(starts[0][0].day, 6)
        self.assertEqual(starts[0][0].hour, 18)

    def test_active_session_soc_delta(self):
        now = datetime(2026, 11, 5, 19, 0, tzinfo=UTC)
        rows = [
            (now - timedelta(minutes=40), 38),
            (now - timedelta(minutes=20), 42),
            (now, 44),
        ]
        self.assertEqual(learning.recent_soc_delta(rows, now-timedelta(hours=1)), 6)


if __name__ == "__main__":
    unittest.main()
