"""Exercise live targets and fallback without a Home Assistant runtime."""
import ast
from datetime import datetime, timedelta, timezone
import math
from pathlib import Path
from types import SimpleNamespace
import unittest

source = Path(__file__).parents[1] / "custom_components/unplugged_forecast/coordinator.py"
tree = ast.parse(source.read_text(encoding="utf-8"))
cls = next(node for node in tree.body if isinstance(node, ast.ClassDef) and node.name == "ForecastCoordinator")
methods = [node for node in cls.body if isinstance(node, ast.FunctionDef) and node.name in ("_input", "_charge_target")]
reader = next(node for node in tree.body if isinstance(node, ast.FunctionDef) and node.name == "current_soc")
scope = {"math": math}
exec(compile(ast.Module(body=[reader, *methods], type_ignores=[]), str(source), "exec"), scope)


class ChargeTargetTests(unittest.TestCase):
    def setUp(self):
        self.now = datetime(2026, 11, 27, tzinfo=timezone.utc)
        self.states = {}
        self.c = SimpleNamespace(hass=SimpleNamespace(states=SimpleNamespace(get=self.states.get)),
                                 options={"input_grace_minutes": 10}, _input_cache={}, _input_health={})
        self.c._input = lambda entity, kind, at: scope["_input"](self.c, entity, kind, at)
        self.ev = {"max_soc": 80, "charge_limit_sensor": "number.limit"}

    def target(self, minutes=0):
        return scope["_charge_target"](self.c, self.ev, self.now + timedelta(minutes=minutes))

    def test_live_values_override_both_above_and_below_configured_limit(self):
        for value in (0, 10, 61.5, 90, 100):
            self.states["number.limit"] = SimpleNamespace(state=str(value))
            self.assertEqual(self.target(), (value, "live charge limit"))

    def test_invalid_values_fall_back_without_destroying_the_forecast(self):
        for value in ("unknown", "unavailable", "nan", "inf", "-1", "101", "abc"):
            self.states["number.limit"] = SimpleNamespace(state=value)
            self.assertEqual(self.target(), (80, "configured fallback"))
        self.states.clear()
        self.assertEqual(self.target(), (80, "configured fallback"))

    def test_grace_expires_from_last_valid_read_and_recovers(self):
        self.states["number.limit"] = SimpleNamespace(state="90")
        self.assertEqual(self.target(), (90, "live charge limit"))
        self.states["number.limit"].state = "unavailable"
        self.assertEqual(self.target(5), (90, "cached charge limit"))
        self.assertEqual(self.target(9), (90, "cached charge limit"))
        self.assertEqual(self.target(11), (80, "configured fallback"))
        self.states["number.limit"].state = "70"
        self.assertEqual(self.target(12), (70, "live charge limit"))
        self.assertEqual(self.c._input_health, {})

    def test_optional_entity_absent_or_cleared_preserves_configured_behavior(self):
        for entity in (None, ""):
            self.ev["charge_limit_sensor"] = entity
            self.assertEqual(self.target(), (80, "configured maximum"))
        del self.ev["charge_limit_sensor"]
        self.assertEqual(self.target(), (80, "configured maximum"))
