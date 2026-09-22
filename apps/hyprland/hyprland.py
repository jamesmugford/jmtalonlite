import os
import subprocess
import sys
from typing import Union

from talon import Context, Module, app, settings

mod = Module()
tag_ctx = Context()
ctx = Context()

mod.tag("hyprland", desc="Enable Hyprland voice command mappings.")
mod.setting(
    "hyprland_auto_enable",
    type=bool,
    default=True,
    desc="Automatically enable the Hyprland tag when Talon is running under Hyprland.",
)

tag_ctx.matches = r"""
os: linux
"""

ctx.matches = r"""
tag: user.hyprland
"""


def _is_hyprland() -> bool:
    if os.environ.get("HYPRLAND_INSTANCE_SIGNATURE"):
        return True

    desktop_values = (
        os.environ.get("XDG_CURRENT_DESKTOP", ""),
        os.environ.get("XDG_SESSION_DESKTOP", ""),
    )
    return any("hyprland" in value.lower() for value in desktop_values)


def _setting(name: str, default):
    try:
        return settings.get(name)
    except KeyError:
        return default


def _run_hyprctl(*args: str) -> subprocess.CompletedProcess[str] | None:
    try:
        return subprocess.run(
            ["hyprctl", *args],
            capture_output=True,
            text=True,
            check=False,
            timeout=1,
        )
    except Exception as exc:
        print(f"hyprland hyprctl error: {exc}", file=sys.stderr, flush=True)
        return None


def _report_hyprctl_error(result: subprocess.CompletedProcess[str] | None) -> None:
    if result is None:
        return

    if result.returncode != 0:
        message = result.stderr.strip() or result.stdout.strip()
        print(f"hyprland hyprctl error: {message}", file=sys.stderr, flush=True)
        return

    output = result.stdout.strip()
    if output and any(line.strip().lower() != "ok" for line in output.splitlines()):
        print(f"hyprland hyprctl error: {output}", file=sys.stderr, flush=True)


def _hyprctl(*args: str) -> None:
    _report_hyprctl_error(_run_hyprctl("--", *args))


def _eval(code: str) -> None:
    _report_hyprctl_error(_run_hyprctl("eval", code))


def _lua_string(value: str) -> str:
    escapes = {
        "\a": r"\a",
        "\b": r"\b",
        "\f": r"\f",
        "\n": r"\n",
        "\r": r"\r",
        "\t": r"\t",
        "\v": r"\v",
        '"': r"\"",
        "\\": r"\\",
    }
    encoded = []
    for char in value:
        if char in escapes:
            encoded.append(escapes[char])
        elif ord(char) < 32 or ord(char) == 127:
            encoded.append(f"\\{ord(char):03d}")
        else:
            encoded.append(char)
    return '"' + "".join(encoded) + '"'


def _dispatch(dispatcher: str) -> None:
    _eval(f"hl.dispatch({dispatcher})")


def _direction(value: str) -> str:
    directions = {
        "left": "l",
        "right": "r",
        "up": "u",
        "down": "d",
    }
    return directions.get(value, value)


def _workspace_selector(which: Union[str, int]) -> str:
    return _lua_string(str(which))


def _resize_active_window(direction: int) -> None:
    direction = 1 if direction > 0 else -1
    pixels = direction * 100
    _eval(
        "local window = hl.get_active_window(); "
        "if window then "
        f"hl.dispatch(hl.dsp.window.resize({{ x = {pixels}, y = {pixels}, relative = true }})); "
        "if window.floating then hl.dispatch(hl.dsp.window.center()) end "
        "end"
    )


def _focus_mode_toggle() -> None:
    _eval(
        "local window = hl.get_active_window(); "
        "if window and window.workspace then "
        "local target, best_id; "
        "for _, candidate in ipairs(hl.get_windows({ "
        "workspace = window.workspace, floating = not window.floating "
        "})) do "
        "local id = candidate.focus_history_id; "
        "if not target or (id >= 0 and (best_id < 0 or id < best_id)) then "
        "target, best_id = candidate, id "
        "end "
        "end "
        "if target then hl.dispatch(hl.dsp.focus({ window = target })) end "
        "end"
    )


def _move_active_window(direction: str) -> None:
    direction = _direction(direction)
    offsets = {
        "l": (-10, 0),
        "r": (10, 0),
        "u": (0, -10),
        "d": (0, 10),
    }
    if direction not in offsets:
        _dispatch(f"hl.dsp.window.move({{ direction = {_lua_string(direction)} }})")
        return

    x, y = offsets[direction]
    direction = _lua_string(direction)
    _eval(
        "local window = hl.get_active_window(); "
        "if window then "
        "if window.floating then "
        f"hl.dispatch(hl.dsp.window.move({{ x = {x}, y = {y}, relative = true }})) "
        "else "
        f"hl.dispatch(hl.dsp.window.move({{ direction = {direction} }})) "
        "end "
        "end"
    )


@ctx.action_class("app")
class AppActions:
    def window_close():
        _dispatch("hl.dsp.window.close()")


@mod.action_class
class Actions:
    def hyprland_reload():
        """Reload the Hyprland config."""
        _hyprctl("reload")

    def hyprland_focus(what: str):
        """Move focus."""
        direction = _lua_string(_direction(what))
        _dispatch(f"hl.dsp.focus({{ direction = {direction} }})")

    def hyprland_focus_mode_toggle():
        """Switch focus between tiled and floating windows."""
        _focus_mode_toggle()

    def hyprland_move(direction: str):
        """Move the active window in a direction."""
        _move_active_window(direction)

    def hyprland_switch_to_workspace(which: Union[str, int]):
        """Focus the specified workspace."""
        workspace = _workspace_selector(which)
        _dispatch(f"hl.dsp.focus({{ workspace = {workspace} }})")

    def hyprland_move_to_workspace(which: Union[str, int]):
        """Move the active window to the specified workspace."""
        workspace = _workspace_selector(which)
        _dispatch(f"hl.dsp.window.move({{ workspace = {workspace}, follow = false }})")

    def hyprland_move_to_scratchpad():
        """Move the active window to the scratchpad."""
        _dispatch(
            'hl.dsp.window.move({ workspace = "special:scratchpad", follow = false })'
        )

    def hyprland_show_scratchpad():
        """Toggle the scratchpad workspace."""
        _dispatch('hl.dsp.workspace.toggle_special("scratchpad")')

    def hyprland_fullscreen():
        """Toggle fullscreen for the active window."""
        _dispatch('hl.dsp.window.fullscreen({ mode = "fullscreen" })')

    def hyprland_float():
        """Toggle floating for the active window."""
        _dispatch('hl.dsp.window.float({ action = "toggle" })')

    def hyprland_center():
        """Center the active floating window."""
        _dispatch("hl.dsp.window.center()")

    def hyprland_resize_window(direction: int):
        """Grow or shrink the active window."""
        _resize_active_window(direction)


def _on_ready() -> None:
    if _setting("user.hyprland_auto_enable", True) and _is_hyprland():
        tag_ctx.tags = ["user.hyprland"]
        return
    tag_ctx.tags = []


app.register("ready", _on_ready)
_on_ready()
