"""Test input grace policy without requiring a Home Assistant installation."""
import ast
from datetime import datetime, timedelta, timezone
from pathlib import Path
from types import SimpleNamespace
import unittest

SOURCE = Path(__file__).resolve().parents[1] / "custom_components" / "unplugged_forecast" / "coordinator.py"
tree = ast.parse(SOURCE.read_text())
cls = next(n for n in tree.body if isinstance(n, ast.ClassDef) and n.name == "ForecastCoordinator")
method = next(n for n in cls.body if isinstance(n, ast.FunctionDef) and n.name == "_input")
scope = {
    "timedelta": timedelta,
    "current_power": lambda hass, entity: float(hass.states.get(entity).state),
    "current_soc": lambda hass, entity: float(hass.states.get(entity).state),
    "state_is_away": lambda hass, entity: hass.states.get(entity).state == "on",
}
exec(compile(ast.Module(body=[method], type_ignores=[]), str(SOURCE), "exec"), scope)
read_input = scope["_input"]
UTC = timezone.utc


class FakeCoordinator:
    def __init__(self, grace=10):
        self.options = {"input_grace_minutes": grace}
        self._input_cache = {}
        self._input_health = {}
        self.states = SimpleNamespace(values={})
        self.states.get = self.states.values.get
        self.hass = SimpleNamespace(states=self.states)

    def read(self, entity, kind, at):
        return read_input(self, entity, kind, at)

    def set(self, entity, value):
        self.states.values[entity] = SimpleNamespace(state=value)


class InputGraceTest(unittest.TestCase):
    def setUp(self):
        self.c = FakeCoordinator()
        self.t = datetime(2026, 11, 7, tzinfo=UTC)
        self.c.set("sensor.house", "2.5")

    def test_grace_and_expiry_are_measured_from_last_valid(self):
        self.assertEqual(self.c.read("sensor.house", "power", self.t), 2.5)
        self.c.set("sensor.house", "unavailable")
        self.assertEqual(self.c.read("sensor.house", "power", self.t + timedelta(minutes=4)), 2.5)
        self.assertEqual(self.c._input_health["sensor.house"]["status"], "stale")
        self.assertEqual(self.c.read("sensor.house", "power", self.t + timedelta(minutes=9)), 2.5)
        with self.assertRaises(ValueError):
            self.c.read("sensor.house", "power", self.t + timedelta(minutes=11))
        self.assertEqual(self.c._input_health["sensor.house"]["status"], "expired")

    def test_recovery_resets_age(self):
        self.c.read("sensor.house", "power", self.t)
        self.c.set("sensor.house", "unavailable")
        self.c.read("sensor.house", "power", self.t + timedelta(minutes=8))
        self.c.set("sensor.house", "3.0")
        self.assertEqual(self.c.read("sensor.house", "power", self.t + timedelta(minutes=9)), 3.0)
        self.assertNotIn("sensor.house", self.c._input_health)
        self.c.set("sensor.house", "unavailable")
        self.assertEqual(self.c.read("sensor.house", "power", self.t + timedelta(minutes=17)), 3.0)

    def test_no_value_at_startup_fails_immediately(self):
        self.c.set("sensor.house", "unavailable")
        with self.assertRaises(ValueError):
            self.c.read("sensor.house", "power", self.t)

    def test_away_sensor_grace_preserves_boolean_false(self):
        self.c.set("binary_sensor.away", "off")
        self.assertIs(self.c.read("binary_sensor.away", "away", self.t), False)
        self.c.set("binary_sensor.away", "unavailable")
        self.assertIs(self.c.read("binary_sensor.away", "away", self.t + timedelta(minutes=5)), False)
        self.assertIsNone(self.c.read("binary_sensor.away", "away", self.t + timedelta(minutes=11)))


if __name__ == "__main__":
    unittest.main()
