# Vendored Wayland Components

The hidden `.vendor/pywayland/cp314-cp314t-linux_x86_64/` tree contains
PyWayland 0.4.19 for Talon 1.0's free-threaded CPython 3.14t, CFFI 2.1.0,
Linux x86-64 runtime.

The loader checks the exact Python SOABI, distinguishing free-threaded from
GIL builds. The bundle is based on
PyWayland commit `7f48c575076b3e620a6ba3565dc877d3a9e665ff` and uses the shared
libraries bundled in the official manylinux wheel.

The CFFI declaration is patched to expose `wl_display_cancel_read()`. The
extension is relinked to the wheel's private Wayland libraries with an
`$ORIGIN/../pywayland.libs` runtime search path. Talon's CFFI installation is
used rather than vendoring `_cffi_backend`.

Generated bindings use these pinned protocol definitions:

- `wayland.xml`, Wayland 1.26.0 commit
  `87cc8a8728a923fc57938faa81ba0e74f34ecdc7`
- `wlr-virtual-pointer-unstable-v1.xml`, wlr-protocols commit
  `c11408942e2fb54d41dadb84cdf844331076ae11`
- `virtual-keyboard-unstable-v1.xml`, wlroots commit
  `91ef4ce2081fec77d060ce2e9879535697e23b91`
- `wlr-foreign-toplevel-management-unstable-v1.xml`, wlr-protocols commit
  `005d69d048ccceb2af3f5b86665821e8fa9a87b8`

Only `wayland.py` and the three extension bindings are retained in the protocol
package. The stale build source, unrelated stock bindings, and upstream wheel
metadata are removed. The scanner remains because PyWayland's runtime argument
types import it. The removed metadata describes the unmodified wheel and would
be inaccurate for this patched derivative; `manifest.json` and `VENDOR.json`
are the authoritative provenance records instead.

Run `tools/build_pywayland_vendor.py --python /path/to/talon/python` to reconstruct
the matching bundle. The default interpreter is `~/.talon/bin/python`. The script
requires Talon's Python and CFFI version listed in the manifest, GCC, binutils, Wayland
development headers, and network access. Source artifacts and protocol inputs
are hash-pinned, and the resulting ELF dependencies, RPATH, and maximum glibc
symbol version are validated. The host compiler and headers are intentionally
not container-pinned, so reconstruction is not guaranteed to be bit-for-bit.

The pinned 3.13 wheel supplies Python sources and private native libraries.
Its original Python extension is removed and rebuilt for the 3.14t ABI.
Headers come from the target interpreter's include directory or
its Talon virtual environment, never a different installed Python version.
When building from a second Talon installation, use an isolated `HOME` containing
that installation's initialized `.talon/.venv/include` directory.

The vendor tests validate the bundle's provenance and generated protocol hashes.
On the supported interpreter they also exercise a native CFFI callback on an
owner thread using a local socket pair, without connecting to a compositor or
sending input. On 3.14t this also checks that importing and using the extension
does not enable the GIL.
