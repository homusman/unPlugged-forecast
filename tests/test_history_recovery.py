"""Exercise history recovery and preserve valid data on empty/error responses."""
import importlib
from pathlib import Path
import sys
from types import ModuleType, SimpleNamespace
import unittest
from unittest.mock import AsyncMock, patch

try:
    import homeassistant
except ImportError:
    homeassistant = None


@unittest.skipIf(homeassistant is None, "Home Assistant required for coordinator")
class HistoryRecoveryTests(unittest.IsolatedAsyncioTestCase):
    def setUp(self):
        package = ModuleType("unplugged_history_test")
        package.__path__ = [str(Path(__file__).parents[1] / "custom_components/unplugged_forecast")]
        sys.modules[package.__name__] = package
        self.module = importlib.import_module(package.__name__ + ".coordinator")
        entry = SimpleNamespace(data={"house_load": "sensor.house", "ev_power": "sensor.ev",
                                      "history_days": 7, "evs": []}, options={})
        hass = SimpleNamespace(states=SimpleNamespace(get=lambda _: SimpleNamespace(
            attributes={"unit_of_measurement": "kW"})))
        self.c = self.module.ForecastCoordinator(hass, entry)
        self.c.async_publish = AsyncMock()

    async def test_empty_startup_retries_then_recovers(self):
        now = self.module.dt_util.utcnow().replace(minute=0, second=0, microsecond=0)
        stats = {"sensor.house": [{"start": now, "mean": 2}],
                 "sensor.ev": [{"start": now, "mean": 0.5}]}
        query = AsyncMock(side_effect=[({}, {}), (stats, {})])
        with patch.object(self.module, "get_instance", return_value=SimpleNamespace(async_add_executor_job=query)):
            await self.c.async_refresh_history()
            self.assertEqual(self.c.history_diagnostics["status"], "retrying")
            self.assertEqual(self.c._history_count, 0)
            await self.c._retry_history()
            self.assertEqual(query.await_count, 1)
            self.c._history_retry_at = None  # Simulate the retry becoming due.
            await self.c._retry_history()
            self.assertEqual(self.c.history_diagnostics["status"], "ready")
            self.assertEqual(self.c._history_count, 1)
            self.assertIn(1.5, self.c._baseline)
            await self.c._retry_history()
            self.assertEqual(query.await_count, 2)

    async def test_empty_refresh_preserves_previous_model_and_explains_counts(self):
        self.c._baseline = [2] * 24
        self.c._history_count = 48
        self.c._sessions = [{"sentinel": True}]
        query = AsyncMock(return_value=({}, {}))
        with patch.object(self.module, "get_instance", return_value=SimpleNamespace(async_add_executor_job=query)):
            await self.c.async_refresh_history()
        self.assertEqual(self.c._baseline, [2] * 24)
        self.assertEqual(self.c._history_count, 48)
        self.assertEqual(self.c._sessions, [{"sentinel": True}])
        self.assertEqual(self.c.history_diagnostics["matched_hours_returned"], 0)
        self.assertIn("No overlapping", self.c.history_diagnostics["last_error"])

    async def test_retry_does_not_queue_behind_an_active_refresh(self):
        self.c.async_refresh_history = AsyncMock()
        async with self.c._lock:
            await self.c._retry_history()
        self.c.async_refresh_history.assert_not_awaited()

    async def test_inertia_learns_non_ev_load_and_ignores_ev_power(self):
        from datetime import timedelta
        states = {"sensor.house": 2, "sensor.ev": 0}
        self.c.hass.states.get = lambda entity: SimpleNamespace(
            state=str(states[entity]), attributes={"unit_of_measurement": "kW"})
        self.c.entry.data.update(horizon_hours=6, interval_minutes=30,
                                 live_correction_mode="inertia", live_decay_minutes=0)
        self.c._baseline = [1] * 24
        self.c._history_count = 578
        # Use the real publishing path rather than the history-test stub.
        publish = self.module.ForecastCoordinator.async_publish
        start = self.module.dt_util.utcnow()
        for minute in range(41):
            with patch.object(self.module.dt_util, "utcnow", return_value=start + timedelta(minutes=minute)):
                await publish(self.c)
        self.assertEqual(self.c.current_kw, 2)
        self.assertGreater(self.c.forecast[0]["value"], 1.65)
        self.assertEqual(self.c.forecast[-1]["value"], 1)
        self.assertTrue(self.c.correction_diagnostics["learning"])
        state_type = type(self.c._inertia)
        self.c._inertia = state_type()
        states["sensor.ev"] = 1  # Entire deviation is now measured EV demand.
        for minute in range(41, 81):
            with patch.object(self.module.dt_util, "utcnow", return_value=start + timedelta(minutes=minute)):
                await publish(self.c)
        self.assertEqual(self.c.correction_diagnostics["applied_correction_kw"], 0)
