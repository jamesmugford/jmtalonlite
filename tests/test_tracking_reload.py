import importlib.util
import os
import sys
import types
import unittest
from pathlib import Path
from unittest.mock import patch


class FakeModule:
    def setting(self, _name, **_kwargs):
        pass

    def action_class(self, cls):
        return cls


class FakeApp:
    def register(self, _event, _callback):
        pass


class FakeSettings:
    def __init__(self, values=None):
        self.values = values or {}

    def get(self, name):
        return self.values.get(name, False)


class FakeTrackingSystem:
    def __init__(self):
        self.callbacks = []

    def register(self, _event, callback):
        self.callbacks.append(callback)

    def unregister(self, _event, callback):
        if callback in self.callbacks:
            self.callbacks.remove(callback)


def make_talon(*, settings=None):
    talon = types.ModuleType("talon")
    talon.Module = FakeModule
    talon.actions = types.SimpleNamespace(
        mouse_move=lambda _x, _y: None,
        tracking=types.SimpleNamespace(control1_enabled=lambda: False),
        user=types.SimpleNamespace(
            mouse_forwarder_native_pointer_selected=lambda: True,
            wayland_pointer_move_absolute=lambda *_args, **_kwargs: None,
            wayland_pointer_move_main_screen=lambda *_args, **_kwargs: None,
        ),
    )
    talon.app = FakeApp()
    talon.settings = FakeSettings(settings)
    talon.tracking_system = FakeTrackingSystem()
    talon.ui = types.SimpleNamespace(
        register=lambda _event, _callback: None,
        unregistered=[],
        screens=lambda: (),
    )
    talon.ui.unregister = (
        lambda event, callback: talon.ui.unregistered.append((event, callback))
    )
    plugins_module = types.ModuleType("talon.plugins")
    plugins_module.eye_mouse = types.SimpleNamespace(
        mouse=types.SimpleNamespace(xy_hist=[], eye_hist=[], delta_hist=[])
    )
    return talon, plugins_module


def load_tracking_module(filename, *, talon, plugins_module, legacy_globals=None):
    root = Path(__file__).resolve().parents[1]
    path = root / "plugins" / "tracking_forwarder" / filename
    spec = importlib.util.spec_from_file_location(
        f"plugins.tracking_forwarder.{path.stem}_under_test",
        path,
    )
    module = importlib.util.module_from_spec(spec)
    if legacy_globals is not None:
        module.__dict__.update(legacy_globals)
    with patch.dict(
        sys.modules,
        {
            "talon": talon,
            "talon.plugins": plugins_module,
        },
    ):
        spec.loader.exec_module(module)
    return module


class TrackingReloadTests(unittest.TestCase):
    def setUp(self):
        self.enterContext(
            patch.dict(os.environ, {"XDG_SESSION_TYPE": "wayland"}, clear=True)
        )

    def test_pointer_forwarder_unregisters_legacy_screen_callback(self):
        talon, plugins_module = make_talon()

        def legacy_screen_callback(_screens):
            pass

        load_tracking_module(
            "control1_pointer_forwarder.py",
            talon=talon,
            plugins_module=plugins_module,
            legacy_globals={"_on_screen_change": legacy_screen_callback},
        )

        self.assertEqual(
            talon.ui.unregistered,
            [("screen_change", legacy_screen_callback)],
        )

    def test_pointer_forwarder_sends_raw_control_mouse_point_to_main_screen(self):
        callback_key = "_jm_talon_lite_control1_pointer_callback"
        state_key = "_jm_talon_lite_control1_pointer_enabled"
        saved_callback = getattr(sys, callback_key, None)
        saved_state = getattr(sys, state_key, None)
        for retained_key in (callback_key, state_key):
            if hasattr(sys, retained_key):
                delattr(sys, retained_key)
        talon, plugins_module = make_talon()
        talon.actions.tracking.control1_enabled = lambda: True
        plugins_module.eye_mouse.mouse.xy_hist = [
            types.SimpleNamespace(x=1920.0, y=1080.0)
        ]
        calls = []
        talon.actions.user.wayland_pointer_move_main_screen = (
            lambda *args, **kwargs: calls.append((args, kwargs))
        )
        loaded = None
        try:
            loaded = load_tracking_module(
                "control1_pointer_forwarder.py",
                talon=talon,
                plugins_module=plugins_module,
            )
            loaded._on_gaze()
            self.assertEqual(
                calls,
                [((1920.0, 1080.0), {"refresh_hover": True})],
            )
            fallback_calls = []
            talon.actions.mouse_move = (
                lambda x, y: fallback_calls.append((x, y))
            )

            def unavailable(*_args, **_kwargs):
                raise loaded.CapabilityUnavailable("output unavailable")

            talon.actions.user.wayland_pointer_move_main_screen = unavailable
            loaded._on_gaze()
            self.assertEqual(fallback_calls, [(1920.0, 1080.0)])
        finally:
            if loaded is not None:
                loaded._unregister_gaze()
            for retained_key in (callback_key, state_key):
                if hasattr(sys, retained_key):
                    delattr(sys, retained_key)
            if saved_callback is not None:
                setattr(sys, callback_key, saved_callback)
            if saved_state is not None:
                setattr(sys, state_key, saved_state)

    def test_gaze_logger_removes_legacy_global_callback(self):
        key = "_jm_talon_lite_control1_gaze_logger_callback"
        state_key = "_jm_talon_lite_control1_gaze_logger_enabled"
        saved = getattr(sys, key, None)
        saved_state = getattr(sys, state_key, None)
        for retained_key in (key, state_key):
            if hasattr(sys, retained_key):
                delattr(sys, retained_key)
        talon, plugins_module = make_talon()

        def legacy_callback(*_args):
            pass

        talon.tracking_system.callbacks.append(legacy_callback)
        try:
            load_tracking_module(
                "control1_gaze_logger.py",
                talon=talon,
                plugins_module=plugins_module,
                legacy_globals={"_on_gaze": legacy_callback},
            )
            self.assertEqual(talon.tracking_system.callbacks, [])
        finally:
            if hasattr(sys, key):
                delattr(sys, key)
            if hasattr(sys, state_key):
                delattr(sys, state_key)
            if saved is not None:
                setattr(sys, key, saved)
            if saved_state is not None:
                setattr(sys, state_key, saved_state)

    def test_gaze_logger_replaces_retained_callback_and_resumes(self):
        key = "_jm_talon_lite_control1_gaze_logger_callback"
        state_key = "_jm_talon_lite_control1_gaze_logger_enabled"
        saved = getattr(sys, key, None)
        saved_state = getattr(sys, state_key, None)
        if hasattr(sys, state_key):
            delattr(sys, state_key)
        talon, plugins_module = make_talon()

        def retained_callback(*_args):
            pass

        talon.tracking_system.callbacks.append(retained_callback)
        setattr(sys, key, retained_callback)
        loaded = None
        try:
            loaded = load_tracking_module(
                "control1_gaze_logger.py",
                talon=talon,
                plugins_module=plugins_module,
            )
            self.assertEqual(talon.tracking_system.callbacks, [loaded._on_gaze])
            self.assertIs(getattr(sys, key), loaded._on_gaze)
        finally:
            if loaded is not None:
                loaded._unregister_gaze()
            if hasattr(sys, key):
                delattr(sys, key)
            if hasattr(sys, state_key):
                delattr(sys, state_key)
            if saved is not None:
                setattr(sys, key, saved)
            if saved_state is not None:
                setattr(sys, state_key, saved_state)

    def test_pointer_forwarder_resumes_legacy_registered_state(self):
        key = "_jm_talon_lite_control1_pointer_callback"
        state_key = "_jm_talon_lite_control1_pointer_enabled"
        saved = getattr(sys, key, None)
        saved_state = getattr(sys, state_key, None)
        for retained_key in (key, state_key):
            if hasattr(sys, retained_key):
                delattr(sys, retained_key)
        talon, plugins_module = make_talon()

        def legacy_callback(*_args):
            pass

        talon.tracking_system.callbacks.append(legacy_callback)
        loaded = None
        try:
            loaded = load_tracking_module(
                "control1_pointer_forwarder.py",
                talon=talon,
                plugins_module=plugins_module,
                legacy_globals={
                    "_registered": True,
                    "_on_gaze": legacy_callback,
                },
            )
            self.assertEqual(talon.tracking_system.callbacks, [loaded._on_gaze])
            self.assertTrue(loaded._registered)
        finally:
            if loaded is not None:
                loaded._unregister_gaze()
            if hasattr(sys, key):
                delattr(sys, key)
            if hasattr(sys, state_key):
                delattr(sys, state_key)
            if saved is not None:
                setattr(sys, key, saved)
            if saved_state is not None:
                setattr(sys, state_key, saved_state)

    def test_reload_does_not_reapply_tracking_autostart_after_disable(self):
        cases = (
            (
                "control1_gaze_logger.py",
                "_jm_talon_lite_control1_gaze_logger_callback",
                "_jm_talon_lite_control1_gaze_logger_enabled",
                "user.control1_gaze_logger_autostart",
            ),
            (
                "control1_pointer_forwarder.py",
                "_jm_talon_lite_control1_pointer_callback",
                "_jm_talon_lite_control1_pointer_enabled",
                "user.control1_pointer_forwarder_autostart",
            ),
        )
        for filename, callback_key, state_key, setting_name in cases:
            with self.subTest(filename=filename):
                saved_callback = getattr(sys, callback_key, None)
                saved_state = getattr(sys, state_key, None)
                if hasattr(sys, callback_key):
                    delattr(sys, callback_key)
                setattr(sys, state_key, False)
                talon, plugins_module = make_talon(settings={setting_name: True})
                try:
                    loaded = load_tracking_module(
                        filename,
                        talon=talon,
                        plugins_module=plugins_module,
                    )
                    loaded._on_ready()
                    self.assertEqual(talon.tracking_system.callbacks, [])
                    self.assertFalse(loaded._registered)
                finally:
                    for retained_key in (callback_key, state_key):
                        if hasattr(sys, retained_key):
                            delattr(sys, retained_key)
                    if saved_callback is not None:
                        setattr(sys, callback_key, saved_callback)
                    if saved_state is not None:
                        setattr(sys, state_key, saved_state)

    def test_failed_pointer_import_retains_enabled_resume_marker(self):
        callback_key = "_jm_talon_lite_control1_pointer_callback"
        state_key = "_jm_talon_lite_control1_pointer_enabled"
        saved_callback = getattr(sys, callback_key, None)
        saved_state = getattr(sys, state_key, None)
        talon, _plugins_module = make_talon()

        def retained_callback(*_args):
            pass

        talon.tracking_system.callbacks.append(retained_callback)
        setattr(sys, callback_key, retained_callback)
        setattr(sys, state_key, True)
        try:
            with self.assertRaises(ImportError):
                load_tracking_module(
                    "control1_pointer_forwarder.py",
                    talon=talon,
                    plugins_module=types.ModuleType("talon.plugins"),
                )
            self.assertEqual(talon.tracking_system.callbacks, [])
            self.assertTrue(getattr(sys, state_key))
            self.assertIs(getattr(sys, callback_key), retained_callback)
        finally:
            for retained_key in (callback_key, state_key):
                if hasattr(sys, retained_key):
                    delattr(sys, retained_key)
            if saved_callback is not None:
                setattr(sys, callback_key, saved_callback)
            if saved_state is not None:
                setattr(sys, state_key, saved_state)

    def test_failed_first_pointer_import_does_not_suppress_autostart_retry(self):
        callback_key = "_jm_talon_lite_control1_pointer_callback"
        state_key = "_jm_talon_lite_control1_pointer_enabled"
        saved_callback = getattr(sys, callback_key, None)
        saved_state = getattr(sys, state_key, None)
        for retained_key in (callback_key, state_key):
            if hasattr(sys, retained_key):
                delattr(sys, retained_key)
        talon, plugins_module = make_talon(
            settings={"user.control1_pointer_forwarder_autostart": True}
        )
        loaded = None
        try:
            with self.assertRaises(ImportError):
                load_tracking_module(
                    "control1_pointer_forwarder.py",
                    talon=talon,
                    plugins_module=types.ModuleType("talon.plugins"),
                )
            self.assertFalse(hasattr(sys, state_key))

            loaded = load_tracking_module(
                "control1_pointer_forwarder.py",
                talon=talon,
                plugins_module=plugins_module,
            )
            talon.actions.user.control1_pointer_forwarder_start = (
                loaded.Actions.control1_pointer_forwarder_start
            )
            loaded._on_ready()

            self.assertTrue(loaded._registered)
            self.assertEqual(talon.tracking_system.callbacks, [loaded._on_gaze])
        finally:
            if loaded is not None:
                loaded._unregister_gaze()
            for retained_key in (callback_key, state_key):
                if hasattr(sys, retained_key):
                    delattr(sys, retained_key)
            if saved_callback is not None:
                setattr(sys, callback_key, saved_callback)
            if saved_state is not None:
                setattr(sys, state_key, saved_state)


if __name__ == "__main__":
    unittest.main()
