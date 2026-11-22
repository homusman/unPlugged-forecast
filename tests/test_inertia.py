"""Transient rejection, persistence, timing and outage regressions."""
from datetime import datetime, timedelta, timezone
import importlib.util
from pathlib import Path
import sys
import unittest

ROOT = Path(__file__).parents[1] / "custom_components/unplugged_forecast"
spec = importlib.util.spec_from_file_location("load_inertia_test", ROOT / "inertia.py")
model = importlib.util.module_from_spec(spec)
sys.modules[spec.name] = model
spec.loader.exec_module(model)
UTC = timezone.utc
NOW = datetime(2026, 11, 22, tzinfo=UTC)


class InertiaTests(unittest.TestCase):
    def test_new_sample_does_not_retroactively_fill_elapsed_time(self):
        state = model.LoadInertia()
        state.update(NOW, 0)
        self.assertEqual(state.update(NOW + timedelta(minutes=1), 2), 0)

    def test_three_minute_microwave_is_small_but_sustained_load_is_learned(self):
        pulse = model.LoadInertia()
        steady = model.LoadInertia()
        pulse.update(NOW, 1.5)
        steady.update(NOW, 1.5)
        peak = 0
        for minute in range(1, 61):
            at = NOW + timedelta(minutes=minute)
            peak = max(peak, pulse.update(at, 1.5 if minute < 3 else 0))
            persistent = steady.update(at, 1.5)
        self.assertLess(peak, 0.06)
        self.assertGreater(persistent, 1.25)

    def test_elapsed_time_not_number_of_events_determines_response(self):
        results = []
        for seconds in (1, 20, 60):
            state = model.LoadInertia()
            for elapsed in range(0, 2401, seconds):
                result = state.update(NOW + timedelta(seconds=elapsed), 1)
            results.append(result)
        self.assertAlmostEqual(results[0], results[1], places=10)
        self.assertAlmostEqual(results[0], results[2], places=10)

    def test_negative_corrections_and_limits(self):
        state = model.LoadInertia()
        for minute in range(121):
            result = state.update(NOW + timedelta(minutes=minute), -20)
        self.assertLess(result, -2.8)
        self.assertGreaterEqual(result, -3)

    def test_stale_evidence_is_not_learned_and_long_gaps_reset(self):
        state = model.LoadInertia()
        state.update(NOW, 2)
        self.assertEqual(state.update(NOW + timedelta(minutes=1), None), 0)
        self.assertFalse(state.learning)
        self.assertEqual(state.update(NOW + timedelta(minutes=2), 2), 0)
        state.update(NOW + timedelta(minutes=3), 2)
        self.assertGreater(state.bias_kw, 0)
        self.assertEqual(state.update(NOW + timedelta(minutes=20), 2), 0)

    def test_legacy_settings_keep_their_mode(self):
        self.assertEqual(model.correction_mode({"live_decay_minutes": 0}), "none")
        self.assertEqual(model.correction_mode({"live_decay_minutes": 30}), "decay")
        self.assertEqual(model.correction_mode({"live_decay_minutes": 0, "live_correction_mode": "inertia"}), "inertia")
