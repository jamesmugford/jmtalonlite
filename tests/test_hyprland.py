import io
import os
import subprocess
import types
import unittest
from pathlib import Path
from unittest.mock import patch


if __package__:
    from .talon_fakes import FakeApp, FakeContext, FakeModule, load_talon_module
else:
    from talon_fakes import FakeApp, FakeContext, FakeModule, load_talon_module


class _FakeSettings:
    def get(self, _name):
        raise KeyError


def _load_hyprland_module():
    talon = types.ModuleType("talon")
    talon.Context = FakeContext
    talon.Module = FakeModule
    talon.app = FakeApp()
    talon.settings = _FakeSettings()

    path = Path(__file__).resolve().parents[1] / "apps" / "hyprland" / "hyprland.py"
    return load_talon_module("test_hyprland_module", path, {"talon": talon})


class HyprlandTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.hyprland = _load_hyprland_module()

    def test_detects_hyprland_session(self):
        with patch.dict(
            os.environ, {"HYPRLAND_INSTANCE_SIGNATURE": "instance"}, clear=True
        ):
            self.assertTrue(self.hyprland._is_hyprland())

        with patch.dict(os.environ, {"XDG_CURRENT_DESKTOP": "Hyprland"}, clear=True):
            self.assertTrue(self.hyprland._is_hyprland())

        with patch.dict(os.environ, {"XDG_CURRENT_DESKTOP": "sway"}, clear=True):
            self.assertFalse(self.hyprland._is_hyprland())

    def test_eval_uses_native_lua_api(self):
        result = subprocess.CompletedProcess([], 0, "ok\n", "")
        with patch.object(self.hyprland, "_run_hyprctl", return_value=result) as run:
            self.hyprland._eval("return true")

        run.assert_called_once_with("eval", "return true")

    def test_eval_reports_exit_zero_error_reply(self):
        result = subprocess.CompletedProcess([], 0, "error: bad Lua\n", "")
        with patch.object(self.hyprland, "_run_hyprctl", return_value=result):
            with patch.object(self.hyprland.sys, "stderr", new=io.StringIO()) as stderr:
                self.hyprland._eval("invalid")

        self.assertIn("hyprland hyprctl error: error: bad Lua", stderr.getvalue())

    def test_directional_focus(self):
        with patch.object(self.hyprland, "_eval") as evaluate:
            self.hyprland.Actions.hyprland_focus("left")
            evaluate.assert_called_once_with(
                'hl.dispatch(hl.dsp.focus({ direction = "l" }))'
            )

    def test_directional_move_uses_i3_pixel_delta_for_floating_windows(self):
        cases = {
            "left": ('direction = "l"', "x = -10, y = 0"),
            "right": ('direction = "r"', "x = 10, y = 0"),
            "up": ('direction = "u"', "x = 0, y = -10"),
            "down": ('direction = "d"', "x = 0, y = 10"),
        }

        for direction, (tiled_move, floating_move) in cases.items():
            with self.subTest(direction=direction):
                with patch.object(self.hyprland, "_eval") as evaluate:
                    self.hyprland.Actions.hyprland_move(direction)

                code = evaluate.call_args.args[0]
                self.assertIn("hl.get_active_window()", code)
                self.assertIn("if window.floating then", code)
                self.assertIn(f"{floating_move}, relative = true", code)
                self.assertIn(tiled_move, code)

    def test_workspace_navigation_and_silent_move(self):
        with patch.object(self.hyprland, "_eval") as evaluate:
            self.hyprland.Actions.hyprland_switch_to_workspace(3)
            evaluate.assert_called_once_with(
                'hl.dispatch(hl.dsp.focus({ workspace = "3" }))'
            )

        with patch.object(self.hyprland, "_eval") as evaluate:
            self.hyprland.Actions.hyprland_move_to_workspace("previous")
            evaluate.assert_called_once_with(
                'hl.dispatch(hl.dsp.window.move({ workspace = "previous", follow = false }))'
            )

    def test_workspace_selector_is_lua_quoted(self):
        with patch.object(self.hyprland, "_eval") as evaluate:
            self.hyprland.Actions.hyprland_switch_to_workspace('name"; error("x")')

        evaluate.assert_called_once_with(
            'hl.dispatch(hl.dsp.focus({ workspace = "name\\"; error(\\"x\\")" }))'
        )

    def test_lua_string_escapes_control_characters(self):
        self.assertEqual(
            self.hyprland._lua_string('a\0b\n"\\'),
            r'"a\000b\n\"\\"',
        )

    def test_scratchpad_uses_special_workspace(self):
        with patch.object(self.hyprland, "_eval") as evaluate:
            self.hyprland.Actions.hyprland_move_to_scratchpad()
            evaluate.assert_called_once_with(
                'hl.dispatch(hl.dsp.window.move({ workspace = "special:scratchpad", follow = false }))'
            )

        with patch.object(self.hyprland, "_eval") as evaluate:
            self.hyprland.Actions.hyprland_show_scratchpad()
            evaluate.assert_called_once_with(
                'hl.dispatch(hl.dsp.workspace.toggle_special("scratchpad"))'
            )

    def test_focus_floating_switches_to_opposite_mode(self):
        with patch.object(self.hyprland, "_eval") as evaluate:
            self.hyprland.Actions.hyprland_focus_mode_toggle()

        code = evaluate.call_args.args[0]
        self.assertIn("hl.get_active_window()", code)
        self.assertIn(
            "workspace = window.workspace, floating = not window.floating", code
        )
        self.assertIn("candidate.focus_history_id", code)
        self.assertIn("id >= 0", code)
        self.assertIn("id < best_id", code)
        self.assertIn("hl.dsp.focus({ window = target })", code)

    def test_resize_recenters_only_floating_windows(self):
        with patch.object(self.hyprland, "_eval") as evaluate:
            self.hyprland.Actions.hyprland_resize_window("grow", 4, "height width")

        code = evaluate.call_args.args[0]
        self.assertIn(
            "hl.dsp.window.resize({ x = 40, y = 40, relative = true })", code
        )
        self.assertIn("if window.floating then", code)
        self.assertIn("hl.dsp.window.center()", code)

        with patch.object(self.hyprland, "_eval") as evaluate:
            self.hyprland.Actions.hyprland_resize_window("shrink", 4, "height width")

        self.assertIn("x = -40, y = -40", evaluate.call_args.args[0])

    def test_resize_selects_each_axis_once(self):
        cases = (
            ("grow", 2, "left right width", "x = 20, y = 0"),
            ("shrink", 3, "up down height", "x = 0, y = -30"),
            ("grow", 1, "left down", "x = 10, y = 10"),
        )
        for operation, amount, directions, expected in cases:
            with self.subTest(directions=directions):
                with patch.object(self.hyprland, "_eval") as evaluate:
                    self.hyprland.Actions.hyprland_resize_window(
                        operation, amount, directions
                    )
                evaluate.assert_called_once()
                self.assertIn(expected, evaluate.call_args.args[0])

    def test_invalid_resize_does_not_dispatch(self):
        for arguments in (
            ("toggle", 4, "width"),
            ("grow", 0, "width"),
            ("grow", 4, ""),
            ("grow", 4, "diagonal"),
        ):
            with self.subTest(arguments=arguments):
                with patch.object(self.hyprland, "_eval") as evaluate:
                    with self.assertRaises(ValueError):
                        self.hyprland.Actions.hyprland_resize_window(*arguments)
                evaluate.assert_not_called()

    def test_standard_desktop_actions(self):
        cases = (
            ("desktop", (3,), 'hl.dispatch(hl.dsp.focus({ workspace = "3" }))'),
            ("desktop_next", (), 'hl.dispatch(hl.dsp.focus({ workspace = "e+1" }))'),
            ("desktop_last", (), 'hl.dispatch(hl.dsp.focus({ workspace = "e-1" }))'),
            ("window_move_desktop", (3,), "3"),
            ("window_move_desktop_left", (), "e-1"),
            ("window_move_desktop_right", (), "e+1"),
        )
        for name, arguments, expected in cases:
            with self.subTest(action=name):
                if name.startswith("window_move"):
                    expected = (
                        'hl.dispatch(hl.dsp.window.move({ workspace = '
                        f'"{expected}", follow = false }}))'
                    )
                with patch.object(self.hyprland, "_eval") as evaluate:
                    getattr(self.hyprland.UserActions, name)(*arguments)
                evaluate.assert_called_once_with(expected)

    def test_basic_window_dispatchers(self):
        cases = (
            (
                self.hyprland.AppActions.window_close,
                "hl.dispatch(hl.dsp.window.close())",
            ),
            (
                self.hyprland.Actions.hyprland_fullscreen,
                'hl.dispatch(hl.dsp.window.fullscreen({ mode = "fullscreen" }))',
            ),
            (
                self.hyprland.Actions.hyprland_float,
                'hl.dispatch(hl.dsp.window.float({ action = "toggle" }))',
            ),
            (
                self.hyprland.Actions.hyprland_center,
                "hl.dispatch(hl.dsp.window.center())",
            ),
        )

        for action, expected in cases:
            with self.subTest(action=action.__name__):
                with patch.object(self.hyprland, "_eval") as evaluate:
                    action()
                evaluate.assert_called_once_with(expected)


if __name__ == "__main__":
    unittest.main()
