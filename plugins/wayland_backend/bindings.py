"""Dynamic loading for supported PyWayland protocol interfaces."""

from __future__ import annotations

from dataclasses import dataclass
from typing import Any


@dataclass(frozen=True, slots=True)
class WaylandBindings:
    """Dynamically loaded PyWayland types and native display functions."""

    Display: type
    ffi: Any
    lib: Any
    interfaces: dict[str, type]


def load_wayland_bindings() -> WaylandBindings:
    """Activate and load the repository's supported PyWayland interfaces."""
    from .vendor import activate

    activate()
    from pywayland import ffi, lib
    from pywayland.client import Display
    from pywayland.protocol.ext_data_control_v1 import ExtDataControlManagerV1
    from pywayland.protocol.virtual_keyboard_unstable_v1 import (
        ZwpVirtualKeyboardManagerV1,
    )
    from pywayland.protocol.wayland import WlOutput, WlSeat
    from pywayland.protocol.wlr_foreign_toplevel_management_unstable_v1 import (
        ZwlrForeignToplevelManagerV1,
    )
    from pywayland.protocol.wlr_virtual_pointer_unstable_v1 import (
        ZwlrVirtualPointerManagerV1,
    )

    if not hasattr(lib, "wl_display_cancel_read"):
        raise RuntimeError("Bundled PyWayland is missing wl_display_cancel_read")
    interfaces = (
        ExtDataControlManagerV1,
        WlSeat,
        WlOutput,
        ZwpVirtualKeyboardManagerV1,
        ZwlrVirtualPointerManagerV1,
        ZwlrForeignToplevelManagerV1,
    )
    return WaylandBindings(
        Display=Display,
        ffi=ffi,
        lib=lib,
        interfaces={interface.name: interface for interface in interfaces},
    )
