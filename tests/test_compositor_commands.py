import json
import os
import subprocess
import types
import unittest
from pathlib import Path
from unittest.mock import Mock, patch

if __package__:
    from .talon_fakes import FakeApp, FakeContext, FakeModule, load_talon_module
else:
    from talon_fakes import FakeApp, FakeContext, FakeModule, load_talon_module


def load_compositor(name):
    values = {}

    class DeclaringModule(FakeModule):
        def setting(self, name, **kwargs):
            values[f"user.{name}"] = kwargs["default"]

    talon = types.ModuleType("talon")
    talon.Module = DeclaringModule
    talon.Context = FakeContext
    talon.app = FakeApp()
    talon.actions = types.SimpleNamespace(key=Mock())
    talon.settings = types.SimpleNamespace(get=values.__getitem__, values=values)
    path = Path(__file__).resolve().parents[1] / "apps" / name / f"{name}.py"
    module = load_talon_module(
        f"apps.{name}.{name}_under_test", path, {"talon": talon}
    )
    return module, talon


class CompositorActivationTests(unittest.TestCase):
    def test_session_activation_and_explicit_disable_do_not_run_commands(self):
        for name, socket in (("sway", "SWAYSOCK"), ("niri", "NIRI_SOCKET")):
            with self.subTest(compositor=name):
                module, talon = load_compositor(name)
                cases = (
                    ({"XDG_CURRENT_DESKTOP": name.upper()}, True),
                    ({"XDG_CURRENT_DESKTOP": f"GNOME:{name}"}, True),
                    ({"XDG_SESSION_DESKTOP": name}, True),
                    ({socket: "/run/user/1000/compositor.sock"}, True),
                    ({"XDG_CURRENT_DESKTOP": "Hyprland", socket: "stale"}, False),
                    (
                        {"XDG_CURRENT_DESKTOP": "Hyprland", "XDG_SESSION_DESKTOP": name},
                        False,
                    ),
                    ({"XDG_CURRENT_DESKTOP": "GNOME"}, False),
                    ({"XDG_CURRENT_DESKTOP": f"not-{name}"}, False),
                    ({}, False),
                )
                with patch.object(module.subprocess, "run") as run:
                    for environment, expected in cases:
                        with (
                            self.subTest(environment=environment),
                            patch.dict(os.environ, environment, clear=True),
                        ):
                            talon.app.callbacks["ready"]()
                            talon.app.callbacks["ready"]()
                            self.assertEqual(
                                module.tag_ctx.tags,
                                [f"user.{name}"] if expected else [],
                            )
                    with patch.dict(os.environ, {socket: "socket"}, clear=True):
                        talon.settings.values[f"user.{name}_auto_enable"] = False
                        talon.app.callbacks["ready"]()
                        self.assertEqual(module.tag_ctx.tags, [])
                    run.assert_not_called()

    def test_launcher_terminal_and_lock_use_configured_keys(self):
        for name in ("sway", "niri"):
            module, talon = load_compositor(name)
            for setting, action in (
                ("launch", "launch"),
                ("terminal", "shell"),
                ("lock", "lock"),
            ):
                with self.subTest(compositor=name, setting=setting):
                    talon.settings.values[f"user.{name}_{setting}_key"] = "alt-f12"
                    talon.actions.key.reset_mock()
                    getattr(module.Actions, f"{name}_{action}")()
                    talon.actions.key.assert_called_once_with("alt-f12")

    def test_cli_failures_are_reported_without_retry(self):
        for name in ("sway", "niri"):
            module, _talon = load_compositor(name)
            action = module.AppActions.window_close
            for failure in (
                FileNotFoundError("missing compositor CLI"),
                subprocess.TimeoutExpired(name, 1),
            ):
                with (
                    self.subTest(compositor=name, failure=type(failure).__name__),
                    patch.object(module.subprocess, "run", side_effect=failure) as run,
                ):
                    with self.assertRaises(type(failure)):
                        action()
                    run.assert_called_once()
            for stdout, stderr, message in (
                ("command rejected", "", "command rejected"),
                ("", "connection refused", "connection refused"),
            ):
                result = subprocess.CompletedProcess([], 1, stdout, stderr)
                with (
                    self.subTest(compositor=name, message=message),
                    patch.object(module.subprocess, "run", return_value=result) as run,
                ):
                    with self.assertRaisesRegex(RuntimeError, message):
                        action()
                    run.assert_called_once()


class SwayCommandTests(unittest.TestCase):
    def setUp(self):
        self.module, self.talon = load_compositor("sway")
        self.cli = self.enterContext(
            patch.object(
                self.module.subprocess,
                "run",
                return_value=subprocess.CompletedProcess(
                    [], 0, '[{"success": true}]', ""
                ),
            )
        )

    def test_command_is_one_ipc_argument_without_shell_expansion(self):
        command = 'workspace "named workspace; $(literal)"'
        self.module.Actions.swaymsg(command)
        self.cli.assert_called_once_with(
            ["swaymsg", "--raw", "--", command],
            capture_output=True,
            text=True,
            timeout=1,
            check=False,
        )

    def test_standard_desktop_actions_and_close_use_sway_ipc(self):
        actions = self.module.UserActions
        cases = (
            (self.module.AppActions.window_close, (), "kill"),
            (actions.desktop, (3,), "workspace number 3"),
            (actions.desktop, (0,), "workspace number 0"),
            (actions.desktop_next, (), "workspace next"),
            (actions.desktop_last, (), "workspace prev"),
            (actions.window_move_desktop, (4,), "move container to workspace number 4"),
            (actions.window_move_desktop_left, (), "move container to workspace prev"),
            (actions.window_move_desktop_right, (), "move container to workspace next"),
        )
        for action, arguments, expected in cases:
            with self.subTest(action=action.__name__):
                self.cli.reset_mock()
                action(*arguments)
                self.assertEqual(self.cli.call_args.args[0][-1], expected)
                self.cli.assert_called_once()

    def test_resize_uses_ten_pixel_steps_and_attempts_all_axes(self):
        self.cli.side_effect = (
            subprocess.CompletedProcess([], 2, "edge cannot move", ""),
            subprocess.CompletedProcess([], 0, "ok", ""),
        )
        self.module.Actions.sway_resize_window("grow", 4, "height width")
        self.assertEqual(
            [item.args[0][-1] for item in self.cli.call_args_list],
            ["resize grow height 40 px", "resize grow width 40 px"],
        )

    def test_resize_preserves_edge_directions_and_shrink(self):
        self.module.Actions.sway_resize_window("shrink", 2, "left up")
        self.assertEqual(
            [item.args[0][-1] for item in self.cli.call_args_list],
            ["resize shrink left 20 px", "resize shrink up 20 px"],
        )

    def test_resize_still_reports_ipc_failures(self):
        self.cli.return_value = subprocess.CompletedProcess([], 1, "", "no IPC socket")
        with self.assertRaisesRegex(RuntimeError, "no IPC socket"):
            self.module.Actions.sway_resize_window("grow", 4, "height width")
        self.cli.assert_called_once()

    def test_ordinary_command_rejection_is_an_error(self):
        self.cli.return_value = subprocess.CompletedProcess([], 2, "bad command", "")
        with self.assertRaisesRegex(RuntimeError, "bad command"):
            self.module.Actions.swaymsg("invalid")

    def test_invalid_resize_and_workspace_are_rejected_before_ipc(self):
        for arguments in (
            ("grow", 0, "width"),
            ("invalid", 4, "width"),
            ("grow", 4, "width unknown"),
            ("grow", 4, ""),
        ):
            with self.subTest(arguments=arguments), self.assertRaises(ValueError):
                self.module.Actions.sway_resize_window(*arguments)
        for action in (
            self.module.UserActions.desktop,
            self.module.UserActions.window_move_desktop,
        ):
            with self.assertRaises(ValueError):
                action(-1)
        self.cli.assert_not_called()


class NiriCommandTests(unittest.TestCase):
    def setUp(self):
        self.module, self.talon = load_compositor("niri")
        self.cli = self.enterContext(
            patch.object(
                self.module.subprocess,
                "run",
                return_value=subprocess.CompletedProcess([], 0, "Handled", ""),
            )
        )

    def arguments(self):
        return [item.args[0][2:] for item in self.cli.call_args_list]

    def test_quoted_and_negative_arguments_are_preserved_without_a_shell(self):
        self.module.Actions.niri_action('focus-workspace "named workspace; $(literal)"')
        self.cli.assert_called_once_with(
            ["niri", "msg", "action", "focus-workspace", "named workspace; $(literal)"],
            capture_output=True,
            text=True,
            timeout=1,
            check=False,
        )
        self.cli.reset_mock()
        self.module.Actions.niri_action("set-window-width -40")
        self.assertEqual(self.arguments(), [["action", "set-window-width", "-40"]])

    def test_standard_desktop_actions_keep_workspace_moves_unfocused(self):
        actions = self.module.UserActions
        cases = (
            (self.module.AppActions.window_close, (), ["close-window"]),
            (actions.desktop, (3,), ["focus-workspace", "3"]),
            (actions.desktop_next, (), ["focus-workspace-down"]),
            (actions.desktop_last, (), ["focus-workspace-up"]),
            (actions.desktop_show, (), ["toggle-overview"]),
            (
                actions.window_move_desktop,
                (4,),
                ["move-window-to-workspace", "4", "--focus=false"],
            ),
            (
                actions.window_move_desktop_left,
                (),
                ["move-window-to-workspace-up", "--focus=false"],
            ),
            (
                actions.window_move_desktop_right,
                (),
                ["move-window-to-workspace-down", "--focus=false"],
            ),
        )
        for action, arguments, expected in cases:
            with self.subTest(action=action.__name__):
                self.cli.reset_mock()
                action(*arguments)
                self.assertEqual(self.arguments(), [["action", *expected]])

    def test_tiled_moves_target_one_window_instead_of_a_whole_column(self):
        for direction, expected in (
            ("left", ["consume-or-expel-window-left", "--id", "42"]),
            ("right", ["consume-or-expel-window-right", "--id", "42"]),
            ("up", ["move-window-up"]),
            ("down", ["move-window-down"]),
        ):
            with self.subTest(direction=direction):
                self.cli.reset_mock()
                self.cli.return_value.stdout = json.dumps(
                    {"id": 42, "is_floating": False}
                )
                self.module.Actions.niri_move_window(direction)
                self.assertEqual(
                    self.arguments(),
                    [["--json", "focused-window"], ["action", *expected]],
                )

    def test_floating_moves_use_ten_pixel_offsets_and_window_id(self):
        for direction, x, y in (
            ("left", "-10", "+0"),
            ("right", "+10", "+0"),
            ("up", "+0", "-10"),
            ("down", "+0", "+10"),
        ):
            with self.subTest(direction=direction):
                self.cli.reset_mock()
                self.cli.return_value.stdout = json.dumps(
                    {"id": 42, "is_floating": True}
                )
                self.module.Actions.niri_move_window(direction)
                self.assertEqual(
                    self.arguments(),
                    [
                        ["--json", "focused-window"],
                        [
                            "action", "move-floating-window", "--id", "42",
                            "--x", x, "--y", y,
                        ],
                    ],
                )

    def test_empty_workspace_does_not_emit_a_move(self):
        self.cli.return_value.stdout = "null"
        self.module.Actions.niri_move_window("left")
        self.assertEqual(self.arguments(), [["--json", "focused-window"]])

    def test_bad_query_response_does_not_guess_a_move(self):
        self.cli.return_value.stdout = "invalid JSON"
        with self.assertRaises(json.JSONDecodeError):
            self.module.Actions.niri_move_window("left")
        self.assertEqual(self.arguments(), [["--json", "focused-window"]])

    def test_resize_maps_directions_to_axes_once_and_preserves_order(self):
        self.module.Actions.niri_resize_window("grow", 4, "height width")
        self.assertEqual(
            self.arguments(),
            [
                ["action", "set-window-height", "+40"],
                ["action", "set-window-width", "+40"],
            ],
        )
        self.cli.reset_mock()
        self.module.Actions.niri_resize_window("shrink", 2, "left right up height")
        self.assertEqual(
            self.arguments(),
            [
                ["action", "set-window-width", "-20"],
                ["action", "set-window-height", "-20"],
            ],
        )

    def test_invalid_arguments_are_rejected_before_query_or_action(self):
        with self.assertRaises(ValueError):
            self.module.Actions.niri_move_window("invalid")
        for arguments in (
            ("grow", 0, "width"),
            ("invalid", 4, "width"),
            ("grow", 4, "width unknown"),
            ("grow", 4, ""),
        ):
            with self.subTest(arguments=arguments), self.assertRaises(ValueError):
                self.module.Actions.niri_resize_window(*arguments)
        for number in (0, 256):
            for action in (
                self.module.UserActions.desktop,
                self.module.UserActions.window_move_desktop,
            ):
                with self.subTest(number=number), self.assertRaises(ValueError):
                    action(number)
        self.cli.assert_not_called()


if __name__ == "__main__":
    unittest.main()
