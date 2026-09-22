import importlib.util
import sys
import types
import unittest
from pathlib import Path
from unittest.mock import patch


class FakeContext:
    def action_class(self, _namespace):
        return lambda cls: cls


class FakeModule:
    def action_class(self, cls):
        return cls


class FakeApp:
    def __init__(self):
        self.callbacks = {}

    def register(self, event, callback):
        self.callbacks[event] = callback


class FakeTrackingSystem:
    def __init__(self):
        self.callbacks = []

    def register(self, _event, callback):
        self.callbacks.append(callback)

    def unregister(self, _event, callback):
        if callback in self.callbacks:
            self.callbacks.remove(callback)


class FakeCanvas:
    def __init__(self, *, register_error=None, close_error=None):
        self.register_error = register_error
        self.close_error = close_error
        self.unregister_count = 0
        self.close_count = 0
        self.freeze_count = 0

    def register(self, _event, _callback):
        if self.register_error is not None:
            raise self.register_error

    def unregister(self, _event, _callback):
        self.unregister_count += 1

    def close(self):
        self.close_count += 1
        if self.close_error is not None:
            raise self.close_error

    def freeze(self):
        self.freeze_count += 1


def make_overlay_environment(*, control1_enabled=False):
    talon = types.ModuleType("talon")
    talon.Context = FakeContext
    talon.Module = FakeModule
    talon.actions = types.SimpleNamespace(
        next=lambda: None,
        tracking=types.SimpleNamespace(control1_enabled=lambda: control1_enabled),
    )
    talon.app = FakeApp()
    talon.tracking_system = FakeTrackingSystem()
    talon.ui = types.SimpleNamespace(screens=lambda: ())
    canvas_module = types.ModuleType("talon.canvas")
    canvas_module.Canvas = types.SimpleNamespace(from_screen=lambda _screen: None)
    plugins_module = types.ModuleType("talon.plugins")
    plugins_module.eye_mouse = types.SimpleNamespace(
        mouse=types.SimpleNamespace(xy_hist=[])
    )
    return talon, canvas_module, plugins_module


def load_overlay_module(
    *,
    environment=None,
    clear_state=True,
    legacy_globals=None,
):
    root = Path(__file__).resolve().parents[1]
    if environment is None:
        environment = make_overlay_environment()
    talon, canvas_module, plugins_module = environment
    path = root / "plugins" / "tracking_forwarder" / "control1_debug_overlay.py"
    spec = importlib.util.spec_from_file_location(
        "plugins.tracking_forwarder.control1_debug_overlay_under_test",
        path,
    )
    module = importlib.util.module_from_spec(spec)
    if legacy_globals is not None:
        module.__dict__.update(legacy_globals)
    key = "_jm_talon_lite_control1_overlay_state"
    old_state = getattr(sys, key, None)
    if clear_state and old_state is not None:
        delattr(sys, key)
    with patch.dict(
        sys.modules,
        {
            "talon": talon,
            "talon.canvas": canvas_module,
            "talon.plugins": plugins_module,
        },
    ):
        spec.loader.exec_module(module)
    return module, talon, canvas_module, old_state


class OverlayCleanupTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.module, cls.talon, cls.canvas_module, cls.old_state = load_overlay_module()

    @classmethod
    def tearDownClass(cls):
        key = cls.module._RELOAD_STATE_KEY
        if hasattr(sys, key):
            delattr(sys, key)
        if cls.old_state is not None:
            setattr(sys, key, cls.old_state)

    def setUp(self):
        self.module._overlay_enabled = False
        self.module._gaze_registered = False
        self.module._canvas_entries = []
        self.talon.tracking_system.callbacks.clear()
        self.talon.ui.screens = lambda: ()
        self.canvas_module.Canvas.from_screen = lambda _screen: None
        self.module._publish_reload_state()

    def test_failed_canvas_close_remains_owned_for_retry(self):
        canvas = FakeCanvas(close_error=RuntimeError("close failed"))
        entry = (canvas, object())
        self.module._canvas_entries = [entry]

        with self.assertRaisesRegex(RuntimeError, "close failed"):
            self.module._close_canvases()

        self.assertEqual(self.module._canvas_entries, [entry])
        canvas.close_error = None
        self.module._close_canvases()
        self.assertEqual(self.module._canvas_entries, [])
        self.assertEqual(canvas.close_count, 2)

    def test_partial_canvas_creation_is_closed_before_error_returns(self):
        first = FakeCanvas()
        second = FakeCanvas(register_error=RuntimeError("register failed"))
        canvases = iter((first, second))
        self.canvas_module.Canvas.from_screen = lambda _screen: next(canvases)
        self.talon.ui.screens = lambda: (
            types.SimpleNamespace(
                rect=types.SimpleNamespace(x=0, y=0, width=1, height=1)
            ),
            types.SimpleNamespace(
                rect=types.SimpleNamespace(x=1, y=0, width=1, height=1)
            ),
        )

        with self.assertRaisesRegex(RuntimeError, "register failed"):
            self.module._create_canvases()

        self.assertEqual(first.close_count, 1)
        self.assertEqual(second.close_count, 1)
        self.assertEqual(self.module._canvas_entries, [])

    def test_failed_screen_rebuild_disables_and_unsubscribes_overlay(self):
        canvas = FakeCanvas(register_error=RuntimeError("register failed"))
        self.canvas_module.Canvas.from_screen = lambda _screen: canvas
        self.talon.ui.screens = lambda: (
            types.SimpleNamespace(
                rect=types.SimpleNamespace(x=0, y=0, width=1, height=1)
            ),
        )
        self.module._overlay_enabled = True
        self.module._gaze_registered = True
        self.talon.tracking_system.callbacks.append(self.module._on_gaze)
        self.module._publish_reload_state()

        with self.assertRaisesRegex(RuntimeError, "register failed"):
            self.module._on_screen_change(())

        self.assertFalse(self.module._overlay_enabled)
        self.assertFalse(self.module._gaze_registered)
        self.assertEqual(self.talon.tracking_system.callbacks, [])
        self.assertEqual(self.module._canvas_entries, [])

    def test_enabled_overlay_reloads_with_new_exact_callback_and_canvases(self):
        key = self.module._RELOAD_STATE_KEY
        saved_state = getattr(sys, key, None)
        if saved_state is not None:
            delattr(sys, key)
        environment = make_overlay_environment(control1_enabled=True)
        talon, canvas_module, _plugins_module = environment
        screen = types.SimpleNamespace(
            rect=types.SimpleNamespace(x=0, y=0, width=1, height=1)
        )
        talon.ui.screens = lambda: (screen,)
        first_canvas = FakeCanvas()
        second_canvas = FakeCanvas()
        canvases = iter((first_canvas, second_canvas))
        canvas_module.Canvas.from_screen = lambda _screen: next(canvases)
        second = None
        try:
            first, _talon, _canvas, _old_state = load_overlay_module(
                environment=environment,
            )
            first.Actions.control1_debug_overlay_start()
            self.assertEqual(talon.tracking_system.callbacks, [first._on_gaze])

            second, _talon, _canvas, _old_state = load_overlay_module(
                environment=environment,
                clear_state=False,
            )

            self.assertTrue(second._overlay_enabled)
            self.assertEqual(talon.tracking_system.callbacks, [second._on_gaze])
            self.assertEqual(first_canvas.close_count, 1)
            self.assertEqual(second_canvas.close_count, 0)
        finally:
            if second is not None:
                second._on_quit()
            if hasattr(sys, key):
                delattr(sys, key)
            if saved_state is not None:
                setattr(sys, key, saved_state)

    def test_first_reload_closes_legacy_global_resources(self):
        key = self.module._RELOAD_STATE_KEY
        saved_state = getattr(sys, key, None)
        if saved_state is not None:
            delattr(sys, key)
        environment = make_overlay_environment()
        talon, _canvas_module, _plugins_module = environment
        legacy_canvas = FakeCanvas()

        def legacy_callback(*_args):
            pass

        talon.tracking_system.callbacks.append(legacy_callback)
        loaded = None
        try:
            loaded, _talon, _canvas, _old_state = load_overlay_module(
                environment=environment,
                legacy_globals={
                    "_overlay_enabled": True,
                    "_gaze_registered": True,
                    "_on_gaze": legacy_callback,
                    "_canvas_entries": [(legacy_canvas, object())],
                },
            )

            self.assertEqual(talon.tracking_system.callbacks, [])
            self.assertEqual(legacy_canvas.close_count, 1)
            self.assertTrue(loaded._overlay_enabled)
        finally:
            if loaded is not None:
                loaded._on_quit()
            if hasattr(sys, key):
                delattr(sys, key)
            if saved_state is not None:
                setattr(sys, key, saved_state)


if __name__ == "__main__":
    unittest.main()
