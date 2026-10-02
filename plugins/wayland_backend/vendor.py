"""Load the repository-local PyWayland build without user dependencies."""

from __future__ import annotations

import platform
import sys
import sysconfig
from pathlib import Path

_BUNDLES = {
    "cpython-314t-x86_64-linux-gnu": "cp314-cp314t-linux_x86_64",
}
_MACHINE = "x86_64"
_MINIMUM_GLIBC = (2, 34)
_ROOT = (
    Path(__file__).resolve().parents[2]
    / ".vendor"
    / "pywayland"
)


def activate() -> Path:
    """Put the verified PyWayland bundle first on the import path."""
    soabi = sysconfig.get_config_var("SOABI")
    machine = platform.machine()
    system = platform.system()
    libc_name, libc_version = platform.libc_ver()
    try:
        glibc_version = tuple(map(int, libc_version.split(".")))
    except ValueError:
        glibc_version = ()
    if (
        soabi not in _BUNDLES
        or machine != _MACHINE
        or system != "Linux"
        or libc_name != "glibc"
        or glibc_version < _MINIMUM_GLIBC
    ):
        raise RuntimeError(
            "The bundled PyWayland build requires CPython 3.14t, Linux x86-64, "
            "and glibc 2.34 or newer; got "
            f"{soabi}, {system} {machine}, {libc_name} {libc_version}"
        )

    site_path = _ROOT / _BUNDLES[soabi]

    loaded = sys.modules.get("pywayland")
    if loaded is not None:
        origin = Path(loaded.__file__).resolve()
        if not origin.is_relative_to(site_path):
            raise RuntimeError(f"PyWayland is already loaded from {origin}")

    if not site_path.is_dir():
        raise RuntimeError(f"Bundled PyWayland site is missing: {site_path}")
    site = str(site_path)
    if site in sys.path:
        sys.path.remove(site)
    sys.path.insert(0, site)
    return site_path
