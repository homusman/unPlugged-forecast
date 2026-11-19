"""Exercise the actual HA form serialization when HA is available."""
import importlib
from pathlib import Path
import sys
from types import ModuleType
import unittest

try:
    from homeassistant.helpers import config_validation as cv
    import voluptuous_serialize
except ImportError:
    cv = None


@unittest.skipIf(cv is None, "Home Assistant required for form serialization")
class SettingsTests(unittest.TestCase):
    def test_saved_integer_is_displayed_as_matching_string_choice(self):
        package = ModuleType("unplugged_settings_test")
        package.__path__ = [str(Path(__file__).parents[1] / "custom_components/unplugged_forecast")]
        sys.modules[package.__name__] = package
        flow = importlib.import_module(package.__name__ + ".config_flow")
        for saved in (None, 15, 30, 60, "15"):
            schema = flow.main_schema({"interval_minutes": saved})
            fields = voluptuous_serialize.convert(schema, custom_serializer=cv.custom_serializer)
            field = next(f for f in fields if f["name"] == "interval_minutes")
            self.assertEqual(field["default"], str(saved or 30))
            choices = field["selector"]["select"]["options"]
            self.assertIn(field["default"], [option["value"] for option in choices])
