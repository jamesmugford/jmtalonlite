import os
import sys
import types
import unittest
from contextlib import contextmanager
from pathlib import Path
from unittest.mock import patch


if __package__:
    from .talon_fakes import FakeApp, FakeModule, load_talon_module
else:
    from talon_fakes import FakeApp, FakeModule, load_talon_module


class FakeSettings:
    def __init__(self, values=None):
        self.values = values or {}

    def get(self, name):
        return self.values.get(name, False)


class FakeTrackingSystem:
    def __init__(self):
        self.callbacks = []
        self.register_calls = []
        self.unregister_calls = []

    def register(self, _event, callback):
        self.register_calls.append(callback)
        self.callbacks.append(callback)

    def unregister(self, _event, callback):
        self.unregister_calls.append(callback)
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
    plugins_module = types.ModuleType("talon.plugins")
    plugins_module.eye_mouse = types.SimpleNamespace(
        mouse=types.SimpleNamespace(xy_hist=[], eye_hist=[], delta_hist=[])
    )
    return talon, plugins_module


def load_tracking_module(filename, *, talon, plugins_module):
    root = Path(__file__).resolve().parents[1]
    path = root / "plugins" / "tracking_forwarder" / filename
    module = load_talon_module(
        f"plugins.tracking_forwarder.{path.stem}_under_test",
        path,
        {
            "talon": talon,
            "talon.plugins": plugins_module,
        },
    )
    start_name = f"{path.stem}_start"
    setattr(talon.actions.user, start_name, getattr(module.Actions, start_name))
    return module


@contextmanager
def isolated_tracking_state(prefix):
    keys = (f"{prefix}_callback", f"{prefix}_enabled")
    missing = object()
    saved = {key: getattr(sys, key, missing) for key in keys}
    for key in keys:
        sys.__dict__.pop(key, None)
    try:
        yield keys
    finally:
        for key, value in saved.items():
            if value is missing:
                sys.__dict__.pop(key, None)
            else:
                setattr(sys, key, value)


class TrackingReloadTests(unittest.TestCase):
    features = (
        ("control1_pointer_forwarder", "_jm_talon_lite_control1_pointer"),
        ("control1_gaze_logger", "_jm_talon_lite_control1_gaze_logger"),
    )

    def setUp(self):
        self.enterContext(
            patch.dict(os.environ, {"XDG_SESSION_TYPE": "wayland"}, clear=True)
        )

    def test_reload_before_ready_does_not_suppress_autostart(self):
        for feature, prefix in self.features:
            with self.subTest(feature=feature), isolated_tracking_state(prefix) as keys:
                talon, plugins = make_talon(
                    settings={f"user.{feature}_autostart": True}
                )
                load_tracking_module(
                    f"{feature}.py", talon=talon, plugins_module=plugins
                )
                self.assertFalse(hasattr(sys, keys[1]))
                reloaded = load_tracking_module(
                    f"{feature}.py", talon=talon, plugins_module=plugins
                )
                reloaded._on_ready()
                reloaded._on_ready()
                self.assertEqual(talon.tracking_system.callbacks, [reloaded._on_gaze])
                self.assertEqual(
                    talon.tracking_system.register_calls, [reloaded._on_gaze]
                )

    def test_stop_before_ready_overrides_autostart(self):
        for feature, prefix in self.features:
            with self.subTest(feature=feature), isolated_tracking_state(prefix) as keys:
                talon, plugins = make_talon(
                    settings={f"user.{feature}_autostart": True}
                )
                loaded = load_tracking_module(
                    f"{feature}.py", talon=talon, plugins_module=plugins
                )
                loaded._unregister_gaze()
                loaded._on_ready()
                self.assertFalse(getattr(sys, keys[1]))
                self.assertEqual(talon.tracking_system.callbacks, [])

    def test_disabled_startup_policy_is_resolved_once(self):
        for feature, prefix in self.features:
            with self.subTest(feature=feature), isolated_tracking_state(prefix) as keys:
                setting = f"user.{feature}_autostart"
                talon, plugins = make_talon(settings={setting: False})
                loaded = load_tracking_module(
                    f"{feature}.py", talon=talon, plugins_module=plugins
                )
                loaded._on_ready()
                talon.settings.values[setting] = True
                loaded._on_ready()
                self.assertFalse(getattr(sys, keys[1]))
                self.assertEqual(talon.tracking_system.callbacks, [])

    def test_repeated_start_and_stop_register_and_unregister_once(self):
        for feature, prefix in self.features:
            with self.subTest(feature=feature), isolated_tracking_state(prefix):
                talon, plugins = make_talon()
                loaded = load_tracking_module(
                    f"{feature}.py", talon=talon, plugins_module=plugins
                )
                loaded._register_gaze()
                loaded._register_gaze()
                loaded._unregister_gaze()
                loaded._unregister_gaze()
                self.assertEqual(talon.tracking_system.callbacks, [])
                self.assertEqual(
                    talon.tracking_system.register_calls, [loaded._on_gaze]
                )
                self.assertEqual(
                    talon.tracking_system.unregister_calls, [loaded._on_gaze]
                )

    def test_failed_stop_preserves_disabled_intent_and_cleanup_handle(self):
        for feature, prefix in self.features:
            with self.subTest(feature=feature), isolated_tracking_state(prefix) as keys:
                talon, plugins = make_talon()
                loaded = load_tracking_module(
                    f"{feature}.py", talon=talon, plugins_module=plugins
                )
                loaded._register_gaze()
                with patch.object(
                    talon.tracking_system,
                    "unregister",
                    side_effect=RuntimeError("busy"),
                ):
                    with self.assertRaisesRegex(RuntimeError, "busy"):
                        loaded._unregister_gaze()
                self.assertFalse(getattr(sys, keys[1]))
                self.assertIs(getattr(sys, keys[0]), loaded._on_gaze)
                loaded._on_ready()
                self.assertEqual(talon.tracking_system.callbacks, [])
                self.assertFalse(hasattr(sys, keys[0]))

    def test_failed_start_is_retried_at_ready_without_reapplying_autostart(self):
        for feature, prefix in self.features:
            with self.subTest(feature=feature), isolated_tracking_state(prefix) as keys:
                talon, plugins = make_talon(
                    settings={f"user.{feature}_autostart": False}
                )
                loaded = load_tracking_module(
                    f"{feature}.py", talon=talon, plugins_module=plugins
                )
                with patch.object(
                    talon.tracking_system,
                    "register",
                    side_effect=RuntimeError("not ready"),
                ):
                    with self.assertRaisesRegex(RuntimeError, "not ready"):
                        loaded._register_gaze()
                self.assertTrue(getattr(sys, keys[1]))
                self.assertFalse(hasattr(sys, keys[0]))
                loaded._on_ready()
                self.assertEqual(talon.tracking_system.callbacks, [loaded._on_gaze])

    def test_failed_reload_retains_callback_until_retirement_succeeds(self):
        for feature, prefix in self.features:
            with self.subTest(feature=feature), isolated_tracking_state(prefix) as keys:
                talon, plugins = make_talon()
                first = load_tracking_module(
                    f"{feature}.py", talon=talon, plugins_module=plugins
                )
                first._register_gaze()
                with patch.object(
                    talon.tracking_system,
                    "unregister",
                    side_effect=RuntimeError("busy"),
                ):
                    with self.assertRaisesRegex(RuntimeError, "busy"):
                        load_tracking_module(
                            f"{feature}.py", talon=talon, plugins_module=plugins
                        )
                self.assertTrue(getattr(sys, keys[1]))
                self.assertIs(getattr(sys, keys[0]), first._on_gaze)
                reloaded = load_tracking_module(
                    f"{feature}.py", talon=talon, plugins_module=plugins
                )
                self.assertEqual(talon.tracking_system.callbacks, [reloaded._on_gaze])
                self.assertEqual(
                    talon.tracking_system.unregister_calls, [first._on_gaze]
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

    def test_gaze_logger_replaces_retained_callback_and_resumes(self):
        key = "_jm_talon_lite_control1_gaze_logger_callback"
        state_key = "_jm_talon_lite_control1_gaze_logger_enabled"
        saved = getattr(sys, key, None)
        saved_state = getattr(sys, state_key, None)
        setattr(sys, state_key, True)
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
            self.assertFalse(hasattr(sys, callback_key))
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
