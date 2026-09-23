import importlib.util
import sys
import types
import unittest
from pathlib import Path
from unittest.mock import patch


class FakeApps:
    def __init__(self):
        object.__setattr__(self, "declarations", {})

    declarations: dict[str, list[str]]

    def __setattr__(self, name, value):
        self.declarations.setdefault(name, []).append(value)


class FakeModule:
    instances = []

    def __init__(self):
        self.apps = FakeApps()
        self.instances.append(self)


class WaylandAppAliasesTests(unittest.TestCase):
    def test_declares_observed_raw_app_ids(self):
        talon = types.ModuleType("talon")
        setattr(talon, "Module", FakeModule)
        path = (
            Path(__file__).resolve().parents[1]
            / "plugins"
            / "wayland_app_aliases.py"
        )
        spec = importlib.util.spec_from_file_location("wayland_app_aliases_test", path)
        if spec is None or spec.loader is None:
            raise RuntimeError(f"Unable to load {path}")
        module = importlib.util.module_from_spec(spec)

        FakeModule.instances.clear()
        with patch.dict(sys.modules, {"talon": talon}):
            spec.loader.exec_module(module)

        declarations = FakeModule.instances[0].apps.declarations
        self.assertIn("app.name: chromium", declarations["chrome"][0])
        self.assertIn("app.name: chromium-browser", declarations["chrome"][0])
        self.assertIn("app.name: org.gnome.Nautilus", declarations["nautilus"][0])
        self.assertIn("app.name: org.gnome.Evince", declarations["evince"][0])
        self.assertIn("app.name: foot", declarations["wayland_terminal"][0])
        self.assertIn(
            "app.name: org.omarchy.terminal",
            declarations["wayland_terminal"][0],
        )


if __name__ == "__main__":
    unittest.main()
