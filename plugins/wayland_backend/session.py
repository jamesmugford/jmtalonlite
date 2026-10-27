"""Pure Wayland session detection."""

from collections.abc import Mapping


def is_wayland_session(environment: Mapping[str, str]) -> bool:
    """Recognize native sessions, including Talon's explicit socket override."""
    return bool(
        environment.get("WAYLAND_DISPLAY")
        or environment.get("JMTALONLITE_WAYLAND_DISPLAY")
        or environment.get("SWAYSOCK")
        or environment.get("XDG_SESSION_TYPE", "").casefold() == "wayland"
    )
