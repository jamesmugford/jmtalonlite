import importlib.util
import inspect
import os
import sys
import types
import unittest
from contextlib import contextmanager
from pathlib import Path
from unittest.mock import patch


class FakeContext:
    def __init__(self):
        self.matches = ""

    def action_class(self, action_namespace):
        def decorate(cls):
            if action_namespace == "main":
                signature = inspect.signature(cls.key)
                if signature.return_annotation is not inspect.Signature.empty:
                    raise TypeError("main.key must not have a return annotation")
            return cls

        return decorate


class FakeModule:
    def action_class(self, cls):
        for value in vars(cls).values():
            if not callable(value):
                continue
            signature = inspect.signature(value)
            annotations = [
                parameter.annotation for parameter in signature.parameters.values()
            ]
            annotations.append(signature.return_annotation)
            if any(isinstance(annotation, str) for annotation in annotations):
                raise TypeError("Talon action annotations must be runtime types")
        return cls


class FakeApp:
    def __init__(self):
        self.callbacks = {}

    def register(self, event, callback):
        self.callbacks[event] = callback


class FakeCron:
    def __init__(self):
        self.jobs = []

    def after(self, delay, callback):
        job = types.SimpleNamespace(delay=delay, callback=callback, cancelled=False)
        self.jobs.append(job)
        return job

    def cancel(self, job):
        job.cancelled = True


class FakeActions:
    def __init__(self):
        self.next_calls = []

    def next(self, *args):
        self.next_calls.append(args)
        return None


class FakeScopeDeclaration:
    def __init__(self, func):
        self.func = func
        self.update_count = 0

    def update(self):
        self.update_count += 1


class FakeRegistry:
    def __init__(self):
        self.decls = types.SimpleNamespace(apps={})
        self.callbacks = {}

    def register(self, event, callback):
        self.callbacks.setdefault(event, []).append(callback)

    def unregister(self, event, callback):
        callbacks = self.callbacks.get(event, [])
        callbacks.remove(callback)


def load_bridge_module():
    root = Path(__file__).resolve().parents[1]
    talon = types.ModuleType("talon")
    talon.Context = FakeContext
    talon.Module = FakeModule
    talon.actions = FakeActions()
    talon.app = FakeApp()
    talon.cron = FakeCron()
    talon.registry = FakeRegistry()
    rect = types.SimpleNamespace(x=100.0, y=200.0, width=3840.0, height=2160.0)
    main_screen = types.SimpleNamespace(
        name="HDMI-A-1",
        manufacturer="Wacom Tech",
        model="CintiqPro24PT",
        mm_x=530.0,
        mm_y=300.0,
        refresh_rate=60.0,
        scale=1.0,
        rect=rect,
    )
    talon.ui = types.SimpleNamespace(main_screen=lambda: main_screen)
    talon.scope = types.SimpleNamespace(
        scopes={
            "app": FakeScopeDeclaration(lambda: {"original": "app"}),
            "win": FakeScopeDeclaration(lambda: {"original": "win"}),
        }
    )
    path = root / "plugins" / "wayland_runtime.py"
    spec = importlib.util.spec_from_file_location("plugins.wayland_runtime", path)
    module = importlib.util.module_from_spec(spec)
    scopes_spec = importlib.util.spec_from_file_location(
        "plugins.wayland_scopes", root / "plugins" / "wayland_scopes.py"
    )
    scopes_module = importlib.util.module_from_spec(scopes_spec)
    plugins_module = types.ModuleType("talon.plugins")
    plugins_module.eye_mouse = types.SimpleNamespace(main_screen=main_screen)
    talon.eye_mouse = plugins_module.eye_mouse
    old_bridge = getattr(sys, "_jm_talon_lite_wayland_bridge", None)
    if old_bridge is not None:
        delattr(sys, "_jm_talon_lite_wayland_bridge")
    with patch.dict(
        sys.modules,
        {
            "talon": talon,
            "talon.plugins": plugins_module,
            "plugins.wayland_scopes": scopes_module,
        },
    ):
        scopes_spec.loader.exec_module(scopes_module)
        spec.loader.exec_module(module)
    return module, talon


class TalonWaylandBridgeTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.module, cls.talon = load_bridge_module()

    def setUp(self):
        self.module._fallback_held_keys.clear()
        self.module._publish_fallback_keys()

    @contextmanager
    def running_bridge(self, bridge=None):
        if bridge is None:
            bridge = self.module._TalonWaylandBridge()
        with (
            patch.object(bridge.desktop, "start"),
            patch.object(bridge.desktop, "stop"),
            patch.object(
                bridge.desktop,
                "status",
                return_value=types.SimpleNamespace(
                    protocols=(), running=True, error=None
                ),
            ),
            patch.object(bridge.desktop, "window_context_available", return_value=True),
            patch.dict(self.talon.registry.decls.apps, clear=True),
            patch("builtins.print"),
        ):
            try:
                bridge.start()
                yield bridge
            finally:
                bridge.stop()

    def test_window_events_coalesce_into_the_latest_window(self):
        with self.running_bridge() as bridge:
            bridge._queue_active_window(
                bridge._generation, self.module.Window(1, "First", "editor", ())
            )
            job = self.talon.cron.jobs[-1]
            bridge._queue_active_window(
                bridge._generation, self.module.Window(2, "Second", "terminal", ())
            )
            self.assertIs(self.talon.cron.jobs[-1], job)
            self.assertEqual(job.delay, "0ms")
            job.callback()
            self.assertEqual(self.talon.scope.scopes["app"].func()["name"], "Terminal")
            self.assertEqual(self.talon.scope.scopes["win"].func()["title"], "Second")

    def test_new_window_supersedes_delayed_clear_and_stale_job(self):
        with self.running_bridge() as bridge:
            bridge._queue_active_window(
                bridge._generation, self.module.Window(1, "First", "editor", ())
            )
            self.talon.cron.jobs[-1].callback()
            bridge._queue_active_window(bridge._generation, None)
            clear_job = self.talon.cron.jobs[-1]
            self.assertEqual(clear_job.delay, "20ms")
            bridge._queue_active_window(
                bridge._generation, self.module.Window(2, "Second", "terminal", ())
            )
            replacement = self.talon.cron.jobs[-1]
            self.assertTrue(clear_job.cancelled)
            self.assertEqual(replacement.delay, "0ms")
            replacement.callback()
            clear_job.callback()
            self.assertEqual(self.talon.scope.scopes["win"].func()["title"], "Second")

    def test_declaration_updates_refresh_matching_app_aliases(self):
        with self.running_bridge() as bridge:
            bridge._queue_active_window(
                bridge._generation, self.module.Window(1, "Editor", "Code", ())
            )
            self.talon.cron.jobs[-1].callback()
            app_scope = self.talon.scope.scopes["app"]
            self.talon.registry.decls.apps["user.editor"] = [
                types.SimpleNamespace(
                    is_active=lambda: "code" in app_scope.func()["app"]
                )
            ]
            for callback in tuple(self.talon.registry.callbacks["update_decls"]):
                callback(None)
            self.assertEqual(app_scope.func()["app"], {"Code", "code", "user.editor"})

    def test_context_loss_restores_providers_and_recovery_reinstalls_them(self):
        app_scope = self.talon.scope.scopes["app"]
        win_scope = self.talon.scope.scopes["win"]
        original_app, original_win = app_scope.func, win_scope.func
        with self.running_bridge() as bridge:
            with patch.object(
                bridge.desktop, "window_context_available", return_value=False
            ):
                bridge._queue_active_window(bridge._generation, None)
                self.talon.cron.jobs[-1].callback()
                self.assertIs(app_scope.func, original_app)
                self.assertIs(win_scope.func, original_win)
            bridge._queue_active_window(
                bridge._generation, self.module.Window(1, "Recovered", "editor", ())
            )
            self.talon.cron.jobs[-1].callback()
            self.assertEqual(win_scope.func()["title"], "Recovered")
        self.assertIs(app_scope.func, original_app)
        self.assertIs(win_scope.func, original_win)

    def test_replacement_captures_providers_after_predecessor_stops(self):
        app_scope = self.talon.scope.scopes["app"]
        win_scope = self.talon.scope.scopes["win"]
        original_app, original_win = app_scope.func, win_scope.func
        with self.running_bridge():
            installed_app = app_scope.func
            replacement = self.module._TalonWaylandBridge()
            self.assertIs(app_scope.func, installed_app)
            self.assertEqual(len(self.talon.registry.callbacks["update_decls"]), 1)
        with self.running_bridge(replacement):
            pass
        self.assertIs(app_scope.func, original_app)
        self.assertIs(win_scope.func, original_win)

    def test_stop_does_not_overwrite_another_owners_provider(self):
        app_scope = self.talon.scope.scopes["app"]
        original = app_scope.func

        def other_provider():
            return {"app": {"other"}}

        try:
            with self.running_bridge():
                app_scope.func = other_provider
            self.assertIs(app_scope.func, other_provider)
        finally:
            app_scope.func = original

    def test_continuous_scroll_action_delegates_to_desktop(self):
        with patch.object(
            self.module._bridge.desktop,
            "scroll_pointer_continuous",
        ) as scroll:
            self.module.Actions.wayland_pointer_scroll_continuous(0.25, -0.5)

        scroll.assert_called_once_with(0.25, -0.5)

    @classmethod
    def tearDownClass(cls):
        bridge = getattr(sys, "_jm_talon_lite_wayland_bridge", None)
        if bridge is not None:
            bridge.stop()
            delattr(sys, "_jm_talon_lite_wayland_bridge")

    def test_applies_window_values_and_restores_original_scope_functions(self):
        bridge = self.module._TalonWaylandBridge()
        original_app = self.talon.scope.scopes["app"].func
        original_win = self.talon.scope.scopes["win"].func
        window = self.module.Window(1, "Editor", "code", ("activated",))
        status = types.SimpleNamespace(protocols=())
        with (
            patch.object(bridge.desktop, "start"),
            patch.object(bridge.desktop, "stop"),
            patch.object(bridge.desktop, "status", return_value=status),
            patch.object(
                bridge.desktop,
                "window_context_available",
                return_value=True,
            ),
            patch("builtins.print"),
        ):
            bridge.start()
            generation = bridge._generation
            bridge._queue_active_window(generation, window)
            self.talon.cron.jobs[-1].callback()

            self.assertTrue(bridge.scopes.available())
            self.assertEqual(self.talon.scope.scopes["app"].func()["app"], {"code"})
            self.assertEqual(self.talon.scope.scopes["app"].func()["name"], "Code")
            self.assertEqual(self.talon.scope.scopes["win"].func()["title"], "Editor")
            bridge.stop()
        self.assertIs(self.talon.scope.scopes["app"].func, original_app)
        self.assertIs(self.talon.scope.scopes["win"].func, original_win)

    def test_start_delegates_and_warns_only_for_missing_protocols(self):
        bridge = self.module._TalonWaylandBridge()
        complete = (
            ("wl_output", 4),
            ("zwp_virtual_keyboard_manager_v1", 1),
            ("zwlr_virtual_pointer_manager_v1", 2),
            ("zwlr_foreign_toplevel_manager_v1", 3),
        )
        status = types.SimpleNamespace(
            protocols=complete,
            running=True,
            error=None,
        )
        with (
            patch.object(bridge.desktop, "start") as start,
            patch.object(bridge.desktop, "stop"),
            patch.object(bridge.desktop, "status", return_value=status),
            patch.object(
                bridge.desktop,
                "window_context_available",
                return_value=True,
            ),
            patch.object(bridge.scopes, "install") as install,
            patch("builtins.print") as output,
        ):
            bridge.start()
            bridge.start()
            bridge.stop()
        start.assert_called_once_with()
        install.assert_called_once_with()
        output.assert_not_called()

    def test_warns_when_virtual_pointer_cannot_bind_an_output(self):
        bridge = self.module._TalonWaylandBridge()
        status = types.SimpleNamespace(
            protocols=(
                ("wl_output", 4),
                ("zwp_virtual_keyboard_manager_v1", 1),
                ("zwlr_virtual_pointer_manager_v1", 1),
                ("zwlr_foreign_toplevel_manager_v1", 3),
            )
        )
        with (
            patch.object(bridge.desktop, "status", return_value=status),
            patch("builtins.print") as output,
        ):
            bridge._warn_for_missing_protocols()

        self.assertIn("version 2", output.call_args.args[0])

    def test_start_recovers_an_autonomously_stopped_desktop(self):
        bridge = self.module._TalonWaylandBridge()
        bridge._started = True
        bridge._modifier_tokens[1] = (self.module.KeyPress(29),)
        status = types.SimpleNamespace(protocols=(), running=False, error="lost")
        with (
            patch.object(bridge.desktop, "start") as start,
            patch.object(bridge.desktop, "stop") as stop,
            patch.object(bridge.desktop, "status", return_value=status),
            patch.object(
                bridge.desktop,
                "window_context_available",
                return_value=False,
            ),
            patch("builtins.print"),
        ):
            bridge.start()
            start.assert_called_once_with()
            stop.assert_called_once_with()
            bridge.stop()

        self.assertEqual(stop.call_count, 2)

    def test_failed_stop_retains_callback_ownership_for_retry(self):
        bridge = self.module._TalonWaylandBridge()
        bridge._registered_declaration_callback = True
        with (
            patch.object(bridge.desktop, "stop"),
            patch.object(
                bridge,
                "_unregister_declaration_callback",
                side_effect=(RuntimeError("unregister failed"), None),
            ) as unregister,
        ):
            with self.assertRaisesRegex(RuntimeError, "unregister failed"):
                bridge.stop()
            self.assertTrue(bridge._registered_declaration_callback)
            self.assertTrue(bridge._cleanup_pending)
            bridge.stop()

        self.assertEqual(unregister.call_count, 2)
        self.assertFalse(bridge._registered_declaration_callback)
        self.assertFalse(bridge._cleanup_pending)

    def test_failed_scope_update_is_retried_by_repeated_stop(self):
        bridge = self.module._TalonWaylandBridge()
        app_scope = self.talon.scope.scopes["app"]
        win_scope = self.talon.scope.scopes["win"]
        original_app, original_win = app_scope.func, win_scope.func
        bridge.scopes.initialize()
        bridge.scopes.install()

        with (
            patch.object(bridge.desktop, "stop"),
            patch.object(
                app_scope,
                "update",
                side_effect=(RuntimeError("scope update failed"), None),
            ) as update,
        ):
            with self.assertRaisesRegex(RuntimeError, "scope update failed"):
                bridge.stop()
            self.assertIs(app_scope.func, original_app)
            self.assertIs(win_scope.func, original_win)
            self.assertFalse(bridge.scopes.available())
            self.assertTrue(bridge._cleanup_pending)
            bridge.stop()

        self.assertEqual(update.call_count, 2)
        self.assertFalse(bridge._cleanup_pending)

    def test_start_failure_rolls_back_desktop_and_callbacks(self):
        bridge = self.module._TalonWaylandBridge()
        with (
            patch.object(bridge.desktop, "start"),
            patch.object(bridge.desktop, "stop") as stop,
            patch.object(
                bridge.desktop,
                "status",
                side_effect=RuntimeError("status failed"),
            ),
        ):
            with self.assertRaisesRegex(RuntimeError, "status failed"):
                bridge.start()

        stop.assert_called_once_with()
        self.assertFalse(bridge._started)
        self.assertFalse(bridge.scopes.available())
        self.assertEqual(self.talon.registry.callbacks.get("update_decls", []), [])

    def test_stale_window_job_does_not_restore_scopes_after_stop(self):
        bridge = self.module._TalonWaylandBridge()
        status = types.SimpleNamespace(protocols=(), running=True, error=None)
        with (
            patch.object(bridge.desktop, "start"),
            patch.object(bridge.desktop, "stop"),
            patch.object(bridge.desktop, "status", return_value=status),
            patch.object(
                bridge.desktop,
                "window_context_available",
                return_value=True,
            ),
            patch("builtins.print"),
        ):
            bridge.start()
            bridge._queue_active_window(
                bridge._generation,
                self.module.Window(1, "Editor", "code", ("activated",)),
            )
            job = self.talon.cron.jobs[-1]
            bridge.stop()
            job.callback()
        self.assertTrue(job.cancelled)
        self.assertFalse(bridge.scopes.available())

    def test_main_key_falls_back_only_when_native_keyboard_is_unavailable(self):
        bridge = self.module._bridge
        self.talon.actions.next_calls.clear()
        with (
            patch.dict(os.environ, {"XDG_SESSION_TYPE": "wayland"}, clear=True),
            patch.object(bridge.desktop, "keyboard_available", return_value=False),
            patch.object(bridge.desktop, "send_key") as send_key,
        ):
            self.module.MainActions.key("a")
        send_key.assert_not_called()
        self.assertEqual(self.talon.actions.next_calls, [("a",)])

        self.talon.actions.next_calls.clear()
        with (
            patch.dict(os.environ, {"XDG_SESSION_TYPE": "wayland"}, clear=True),
            patch.object(bridge.desktop, "keyboard_available", return_value=True),
            patch.object(bridge.desktop, "send_key") as send_key,
        ):
            self.module.MainActions.key("a")
        send_key.assert_called_once_with("a")
        self.assertEqual(self.talon.actions.next_calls, [])

    def test_fallback_key_hold_stays_with_fallback_until_release(self):
        bridge = self.module._bridge
        self.talon.actions.next_calls.clear()
        with (
            patch.dict(os.environ, {"XDG_SESSION_TYPE": "wayland"}, clear=True),
            patch.object(bridge.desktop, "keyboard_available", return_value=False),
        ):
            self.module.MainActions.key("ctrl:down")

        with (
            patch.dict(os.environ, {"XDG_SESSION_TYPE": "wayland"}, clear=True),
            patch.object(bridge.desktop, "keyboard_available", return_value=True),
            patch.object(bridge.desktop, "send_key") as send_key,
        ):
            self.module.MainActions.key("a")
            self.module.MainActions.key("ctrl:up")
            self.module.MainActions.key("b")

        self.assertEqual(
            self.talon.actions.next_calls,
            [("ctrl:down",), ("a",), ("ctrl:up",)],
        )
        send_key.assert_called_once_with("b")

    def test_temporary_modifier_tokens_release_exactly_once(self):
        bridge = self.module._TalonWaylandBridge()
        pressed = (object(),)
        with (
            patch.object(
                bridge.desktop,
                "press_temporary_modifiers",
                return_value=pressed,
            ),
            patch.object(
                bridge.desktop,
                "release_temporary_modifiers",
            ) as release,
        ):
            token = bridge.begin_temporary_modifiers("ctrl")
            bridge.end_temporary_modifiers(token)
            bridge.end_temporary_modifiers(token)

        release.assert_called_once_with(pressed)

    def test_temporary_token_from_before_reload_cannot_release_a_new_hold(self):
        bridge = self.module._TalonWaylandBridge()
        with patch.object(bridge.desktop, "press_temporary_modifiers", return_value=()):
            old_token = bridge.begin_temporary_modifiers("ctrl")

        self.enterContext(
            patch.object(sys, "_jm_talon_lite_wayland_bridge", self.module._bridge)
        )
        reloaded, _talon = load_bridge_module()
        new_bridge = reloaded._bridge
        new_press = (object(),)
        with (
            patch.object(
                new_bridge.desktop, "press_temporary_modifiers", return_value=new_press
            ),
            patch.object(new_bridge.desktop, "release_temporary_modifiers") as release,
        ):
            new_token = new_bridge.begin_temporary_modifiers("ctrl")
            new_bridge.end_temporary_modifiers(old_token)
            release.assert_not_called()
            self.assertNotEqual(new_token, old_token)

            new_bridge.end_temporary_modifiers(new_token)
            release.assert_called_once_with(new_press)

    def test_fallback_alias_release_allows_native_forwarding_to_resume(self):
        bridge = self.module._bridge
        for pressed, released in (
            ("return", "enter"),
            ("ENTER", "Return"),
            ("win", "super"),
            ("Escape", "esc"),
        ):
            with (
                self.subTest(pressed=pressed, released=released),
                patch.dict(os.environ, {"XDG_SESSION_TYPE": "wayland"}, clear=True),
                patch.object(
                    bridge.desktop, "keyboard_available", return_value=False
                ) as available,
                patch.object(bridge.desktop, "send_key") as send_key,
            ):
                self.module._fallback_held_keys.clear()
                self.talon.actions.next_calls.clear()
                self.module.MainActions.key(f"{pressed}:down")
                available.return_value = True
                self.module.MainActions.key(f"{released}:up")
                self.module.MainActions.key("b")

                self.assertEqual(
                    self.talon.actions.next_calls,
                    [(f"{pressed}:down",), (f"{released}:up",)],
                )
                send_key.assert_called_once_with("b")
                self.assertEqual(self.module._fallback_held_keys, set())

    def test_fallback_tracking_preserves_literal_character_case(self):
        self.module._record_fallback_key_spec("A:down")

        self.assertEqual(self.module._fallback_held_keys, {"key:A"})

        self.module._record_fallback_key_spec("A:up")
        self.assertEqual(self.module._fallback_held_keys, set())

    def test_main_screen_motion_uses_screen_local_normalization(self):
        bridge = self.module._TalonWaylandBridge()
        with patch.object(bridge.desktop, "move_pointer_output_absolute") as move:
            bridge.move_pointer_on_main_screen(
                2020.0,
                1280.0,
                refresh_hover=True,
            )

        target, x, y = move.call_args.args
        self.assertEqual(target.name, "HDMI-A-1")
        self.assertEqual(target.mode_width, 3840)
        self.assertEqual(target.mode_height, 2160)
        self.assertEqual((x, y), (0.5, 0.5))
        self.assertEqual(move.call_args.kwargs, {"refresh_hover": True})

    def test_main_screen_motion_falls_back_to_ui_screen(self):
        bridge = self.module._TalonWaylandBridge()
        original = self.talon.eye_mouse.main_screen
        self.talon.eye_mouse.main_screen = None
        try:
            with patch.object(bridge.desktop, "move_pointer_output_absolute") as move:
                bridge.move_pointer_on_main_screen(100.0, 200.0)
        finally:
            self.talon.eye_mouse.main_screen = original

        _target, x, y = move.call_args.args
        self.assertEqual((x, y), (0.0, 0.0))

    def test_wayland_detection_uses_standard_session_environment(self):
        with patch.dict(os.environ, {"XDG_SESSION_TYPE": "wayland"}, clear=True):
            self.assertTrue(self.module._is_wayland())
        with patch.dict(os.environ, {"XDG_SESSION_TYPE": "x11"}, clear=True):
            self.assertFalse(self.module._is_wayland())


if __name__ == "__main__":
    unittest.main()
