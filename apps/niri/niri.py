"""Translate Community's i3 command vocabulary into native niri actions."""

import json
import os
import shlex
import subprocess

from talon import Context, Module, actions, app, settings

mod = Module()
tag_ctx = Context()
tag_ctx.matches = "os: linux"
ctx = Context()
ctx.matches = """
os: linux
tag: user.niri
"""

mod.tag("niri", desc="Enable niri voice commands using Community's i3 vocabulary.")
mod.setting(
    "niri_auto_enable", type=bool, default=True, desc="Enable commands in niri."
)
mod.setting(
    "niri_terminal_key",
    type=str,
    default="super-t",
    desc="niri terminal shortcut.",
)
mod.setting(
    "niri_launch_key",
    type=str,
    default="super-d",
    desc="niri application launcher shortcut.",
)
mod.setting(
    "niri_lock_key",
    type=str,
    default="super-alt-l",
    desc="Configured niri lock shortcut.",
)

_RESIZE_AXES = {
    "left": "width",
    "right": "width",
    "width": "width",
    "up": "height",
    "down": "height",
    "height": "height",
}
_WINDOW_MOVES = {
    "left": "consume-or-expel-window-left",
    "right": "consume-or-expel-window-right",
    "up": "move-window-up",
    "down": "move-window-down",
}
_FLOATING_OFFSETS = {
    "left": ("-10", "+0"),
    "right": ("+10", "+0"),
    "up": ("+0", "-10"),
    "down": ("+0", "+10"),
}


def _is_niri() -> bool:
    desktops = os.environ.get("XDG_CURRENT_DESKTOP") or os.environ.get(
        "XDG_SESSION_DESKTOP", ""
    )
    if desktops:
        return "niri" in desktops.casefold().split(":")
    return bool(os.environ.get("NIRI_SOCKET"))


def _niri_msg(*arguments: str) -> str:
    result = subprocess.run(
        ["niri", "msg", *arguments],
        capture_output=True,
        text=True,
        timeout=1,
        check=False,
    )
    if result.returncode:
        detail = result.stderr.strip() or result.stdout.strip()
        raise RuntimeError(f"niri msg failed ({result.returncode}): {detail}")
    return result.stdout


def _action(*arguments: str) -> None:
    _niri_msg("action", *arguments)


def _workspace_number(number: int) -> str:
    if not 1 <= number <= 255:
        raise ValueError("niri workspace indices must be between 1 and 255")
    return str(number)


@ctx.action_class("app")
class AppActions:
    def window_close():
        _action("close-window")


@ctx.action_class("user")
class UserActions:
    def desktop(number: int):
        _action("focus-workspace", _workspace_number(number))

    def desktop_next():
        _action("focus-workspace-down")

    def desktop_last():
        _action("focus-workspace-up")

    def desktop_show():
        _action("toggle-overview")

    def window_move_desktop(desktop_number: int):
        number = _workspace_number(desktop_number)
        _action("move-window-to-workspace", number, "--focus=false")

    def window_move_desktop_left():
        _action("move-window-to-workspace-up", "--focus=false")

    def window_move_desktop_right():
        _action("move-window-to-workspace-down", "--focus=false")


@mod.action_class
class Actions:
    def niri_action(command: str):
        """Send one native niri action, preserving quoted CLI arguments."""
        _action(*shlex.split(command))

    def niri_move_window(direction: str):
        """Move one tiled window, or nudge a floating window by ten pixels."""
        if direction not in _WINDOW_MOVES:
            raise ValueError("Unknown niri movement direction")
        window = json.loads(_niri_msg("--json", "focused-window"))
        if window is None:
            return
        if window["is_floating"]:
            x, y = _FLOATING_OFFSETS[direction]
            _action(
                "move-floating-window", "--id", str(window["id"]), "--x", x, "--y", y
            )
        elif direction in {"left", "right"}:
            _action(_WINDOW_MOVES[direction], "--id", str(window["id"]))
        else:
            _action(_WINDOW_MOVES[direction])

    def niri_resize_window(operation: str, amount: int, directions: str):
        """Resize in ten-pixel steps; directional words select width or height."""
        requested = directions.split()
        if operation not in {"grow", "shrink"} or amount <= 0:
            raise ValueError("Resize requires grow/shrink and a positive amount")
        if not requested or any(
            direction not in _RESIZE_AXES for direction in requested
        ):
            raise ValueError("Unknown niri resize direction")
        change = (1 if operation == "grow" else -1) * 10 * amount
        for axis in dict.fromkeys(_RESIZE_AXES[direction] for direction in requested):
            _action(f"set-window-{axis}", f"{change:+d}")

    def niri_launch():
        """Open the configured niri application launcher."""
        actions.key(settings.get("user.niri_launch_key"))

    def niri_shell():
        """Open a terminal using its configured niri shortcut."""
        actions.key(settings.get("user.niri_terminal_key"))

    def niri_lock():
        """Invoke the configured niri screen-lock shortcut."""
        actions.key(settings.get("user.niri_lock_key"))


def _on_ready() -> None:
    enabled = settings.get("user.niri_auto_enable") and _is_niri()
    tag_ctx.tags = ["user.niri"] if enabled else []


app.register("ready", _on_ready)
