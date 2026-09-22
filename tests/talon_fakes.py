"""Small shared declaration fakes and isolated loading for Talon-facing tests."""

import importlib.util
import sys
from pathlib import Path
from types import ModuleType
from unittest.mock import patch


class FakeContext:
    def __init__(self):
        self.matches = ""
        self.tags = []

    def action_class(self, _namespace):
        return lambda cls: cls


class FakeModule:
    def tag(self, _name, *, desc):
        pass

    def setting(self, _name, **_kwargs):
        pass

    def action_class(self, cls):
        return cls


class FakeApp:
    def __init__(self):
        self.callbacks = {}

    def register(self, event, callback):
        self.callbacks[event] = callback


def load_talon_module(
    name: str, path: Path, dependencies: dict[str, ModuleType]
) -> ModuleType:
    """Load one fresh module with temporary fake imports, restoring them afterward."""
    spec = importlib.util.spec_from_file_location(name, path)
    module = importlib.util.module_from_spec(spec)
    with patch.dict(sys.modules, {name: module, **dependencies}):
        spec.loader.exec_module(module)
    return module
