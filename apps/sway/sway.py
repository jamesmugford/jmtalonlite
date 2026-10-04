"""Small swaymsg adapter following Community's i3 command conventions."""

import os
import subprocess

from talon import Context, Module, actions, app, settings

mod = Module()
tag_ctx = Context()
tag_ctx.matches = "os: linux"
ctx = Context()
ctx.matches = """
os: linux
tag: user.sway
"""

mod.tag("sway", desc="Enable Sway voice commands using Community's i3 vocabulary.")
mod.setting(
    "sway_auto_enable", type=bool, default=True, desc="Enable commands in Sway."
)
mod.setting(
    "sway_terminal_key",
    type=str,
    default="super-enter",
    desc="Sway terminal shortcut.",
)
mod.setting(
    "sway_launch_key",
    type=str,
    default="super-d",
    desc="Sway application launcher shortcut.",
)
mod.setting(
    "sway_lock_key",
    type=str,
    default="super-shift-x",
    desc="Configured Sway lock shortcut.",
)

_RESIZE_DIRECTIONS = {"left", "right", "up", "down", "width", "height"}


def _is_sway() -> bool:
    desktops = os.environ.get("XDG_CURRENT_DESKTOP") or os.environ.get(
        "XDG_SESSION_DESKTOP", ""
    )
    if desktops:
        return "sway" in desktops.casefold().split(":")
    return bool(os.environ.get("SWAYSOCK"))


def _swaymsg(command: str, *, allow_command_failure: bool = False) -> None:
    result = subprocess.run(
        ["swaymsg", "--raw", "--", command],
        capture_output=True,
        text=True,
        timeout=1,
        check=False,
    )
    # swaymsg distinguishes a rejected compositor command (2) from an IPC error (1).
    if result.returncode and not (allow_command_failure and result.returncode == 2):
        detail = result.stderr.strip() or result.stdout.strip()
        raise RuntimeError(f"swaymsg failed ({result.returncode}): {detail}")


def _workspace_number(number: int) -> str:
    if number < 0:
        raise ValueError("Sway workspace numbers must be non-negative")
    return str(number)


@ctx.action_class("app")
class AppActions:
    def window_close():
        _swaymsg("kill")


@ctx.action_class("user")
class UserActions:
    def desktop(number: int):
        _swaymsg(f"workspace number {_workspace_number(number)}")

    def desktop_next():
        _swaymsg("workspace next")

    def desktop_last():
        _swaymsg("workspace prev")

    def window_move_desktop(desktop_number: int):
        number = _workspace_number(desktop_number)
        _swaymsg(f"move container to workspace number {number}")

    def window_move_desktop_left():
        _swaymsg("move container to workspace prev")

    def window_move_desktop_right():
        _swaymsg("move container to workspace next")


@mod.action_class
class Actions:
    def swaymsg(command: str):
        """Send one Sway command through its native IPC client."""
        _swaymsg(command)

    def sway_resize_window(operation: str, amount: int, directions: str):
        """Grow or shrink in the requested directions in ten-pixel steps."""
        axes = directions.split()
        if operation not in {"grow", "shrink"} or amount <= 0:
            raise ValueError("Resize requires grow/shrink and a positive amount")
        if not axes or any(axis not in _RESIZE_DIRECTIONS for axis in axes):
            raise ValueError("Unknown Sway resize direction")
        for axis in axes:
            # Like Community, try every direction even if a tiled edge cannot move.
            # Explicit px avoids Sway interpreting tiled resize amounts as percent.
            _swaymsg(
                f"resize {operation} {axis} {10 * amount} px",
                allow_command_failure=True,
            )

    def sway_launch():
        """Open the configured Sway application launcher."""
        actions.key(settings.get("user.sway_launch_key"))

    def sway_shell():
        """Open a terminal using its configured Sway shortcut."""
        actions.key(settings.get("user.sway_terminal_key"))

    def sway_lock():
        """Invoke the configured Sway screen-lock shortcut."""
        actions.key(settings.get("user.sway_lock_key"))


def _on_ready() -> None:
    enabled = settings.get("user.sway_auto_enable") and _is_sway()
    tag_ctx.tags = ["user.sway"] if enabled else []


app.register("ready", _on_ready)
