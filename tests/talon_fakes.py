"""Small shared declaration fakes and isolated loading for Talon-facing tests."""

import importlib.util
import sys
from contextlib import contextmanager
from pathlib import Path
from types import ModuleType, SimpleNamespace
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


class FakeResourceContexts:
    """Model the owner used for Talon 1.0's filtered gaze subscriptions."""

    def __init__(self):
        self.active = "user.test"
        self.main = SimpleNamespace(enter=self.enter_main)

    @contextmanager
    def enter_main(self):
        previous = self.active
        self.active = "main"
        try:
            yield
        finally:
            self.active = previous


class FakeCron:
    def __init__(self):
        self.jobs = []

    def after(self, delay, callback):
        job = SimpleNamespace(delay=delay, callback=callback, cancelled=False)
        self.jobs.append(job)
        return job

    def cancel(self, job):
        job.cancelled = True


def load_talon_module(
    name: str, path: Path, dependencies: dict[str, ModuleType]
) -> ModuleType:
    """Load one fresh module with temporary fake imports, restoring them afterward."""
    if "talon.plugins" in dependencies:
        dependencies["talon"].plugins = dependencies["talon.plugins"]
    spec = importlib.util.spec_from_file_location(name, path)
    module = importlib.util.module_from_spec(spec)
    with patch.dict(sys.modules, {name: module, **dependencies}):
        spec.loader.exec_module(module)
    return module
