# Talon Lite

A lightweight shim that forwards Talon input to Wayland backends.

This is a drop-in configuration that can be used alongside Talon Community or
another custom configuration.

The supplied command layer uses Community's mouse actions/settings and
`user.unmodified_key` capture, or equivalents supplied by your configuration.
Wheel commands follow standard Talon action dispatch, including app-specific
overrides. Custom input code should call `actions.key()` and `actions.mouse_*()`
to use the native forwarding layer.

> **Native Wayland transition:** Talon Lite recently moved from Dotool to native
> Wayland protocols. The previous implementation remains available on the
> [`legacy/dotool`](https://github.com/jamesmugford/jmtalonlite/tree/legacy/dotool)
> branch, but native Wayland is the supported path going forward.

This takes a progressive-enhancement approach, keeping core features
compositor-agnostic while allowing optional compositor-specific app layers.

Shared compositor actions such as `grow window` and `shrink window` reuse Talon
Community's i3 vocabulary where practical; app layers are not intended as full
i3 ports. Hyprland is simply the first optional implementation, not a preferred
or exclusive compositor. Additional integrations are expected under `apps/` as
the project and its maintainers move between desktop environments.

Input forwarding and window tracking are event-driven.

## Talon support

This project targets Talon 1.0 and later, currently tested with 1.0.0. The bundled
backend requires free-threaded CPython 3.14t. Future Talon releases that change
the Python ABI may require an updated bundle. Talon versions before 1.0 are no
longer supported.

Talon 1.0.0 can crash in its Skia UI initialization when selecting Wayland.
Launch it through XWayland while retaining the compositor socket for this shim:

```sh
JMTALONLITE_WAYLAND_DISPLAY="${WAYLAND_DISPLAY:?Run from your Wayland desktop session}" \
WAYLAND_DISPLAY= /path/to/talon/talon
```

This keeps Talon's UI on XWayland while the shim connects directly to Wayland
for input and application contexts. `JMTALONLITE_WAYLAND_DISPLAY` overrides only the
shim's connection; ordinary launches still use the standard Wayland environment.

**Eye-tracking latency:** On the tested Hyprland/XWayland setup, eye tracking
becomes noticeably slower when Talon's HUD or Settings window remains open on
an inactive workspace. Bringing the window onto the active workspace restores
responsiveness. Avoid leaving these windows open on another workspace; try
closing them when not needed.

### Desktop launcher

Example files are provided in [`examples/`](examples/):

- [`talon-launch`](examples/talon-launch) preserves the Wayland socket and applies
  the XWayland UI workaround.
- [`talon.desktop`](examples/talon.desktop) adds Talon to your application menu.

To install them from this repository:

```sh
mkdir -p "$HOME/.local/bin" "$HOME/.local/share/applications"
cp examples/talon-launch "$HOME/.local/bin/talon-launch"
chmod +x "$HOME/.local/bin/talon-launch"
cp examples/talon.desktop "$HOME/.local/share/applications/talon.desktop"
```

Edit the installed wrapper's `talon_binary` path to match your Talon installation.
In the installed desktop file, replace `Exec=/absolute/path/to/talon-launch` with
the absolute path to your wrapper, such as
`Exec=/home/alex/.local/bin/talon-launch`. Quote the path if it contains spaces.
Desktop files do not expand `$HOME` or `~` in `Exec=`.

Launch Talon from your Wayland desktop's application menu. Installing these files
does not restart a running Talon instance or enable autostart. If an existing
launcher uses the same filename, copy it somewhere safe before replacing it.

## Current features

- Layout-aware native keyboard input
- Eye tracking through Control Mouse (Legacy), including the custom Hiss Mouse
  mode. Gaze is mapped to Talon's main eye-mouse output, so additional monitors
  and compositor scaling do not change its position multipliers.
- Mouse button and scrolling commands such as `touch`, `righty`, `drag`, and
  `wheel up`, including continuous fractional-line scrolling
- Active Wayland application and window-title contexts
- Optional compositor voice-command layers for Hyprland, Sway, and niri

## Scope

**This may not be the Talon you know and love.** Some features you may be familiar with from
Linux X11, macOS, or Windows are unavailable under Wayland.

## Compositor support

Support is capability-based: features depend on the protocols advertised by the
compositor and its version/configuration. The compositor targets are:

- Hyprland
- labwc
- Mir
- niri
- phoc
- river
- Sway
- Wayfire

Optional command layers live under `apps/`: Hyprland uses the Lua-capable
`hyprctl eval` API, Sway uses `swaymsg`, and niri uses `niri msg action`. Sway and
niri closely follow Community's i3 spoken forms; see the
[command reference and setup notes](docs/compositor-commands.md) for equivalents,
shortcut settings, and verification status. These command layers are separate
from shared native Wayland input forwarding.

## Wayland protocols

- `wl_output` - output discovery and main eye-mouse display matching
- `zwp_virtual_keyboard_manager_v1` - keyboard input
- `zwlr_virtual_pointer_manager_v1` - pointer, clicks, scrolling, gaze, hiss, and pop input
- `zwlr_foreign_toplevel_manager_v1` - application and window-title contexts

Output-bound gaze requires virtual-pointer manager version 2 and a uniquely
matched output. Startup reports missing capabilities. Forwarded Talon actions
use the next implementation when the corresponding native capability is
unavailable.

## Installation

```sh
git clone https://github.com/jamesmugford/jmtalonlite $HOME/.talon/user/jmtalonlite
```

The bundled native backend requires Linux x86-64, Talon's CPython 3.14t,
glibc 2.34 or newer, and `libxkbcommon.so.0` (normally installed on Wayland desktops).
PyWayland and the protocol bindings are bundled; no separate input daemon or
uinput setup is needed. The command-layer dependency is described above.
Restart Talon after cloning, and once when updating across the historical
reload-state cleanup. Subsequent script reloads preserve current runtime choices.

Talon's own Tobii udev rule is still required when using an eye tracker and is
installed by Talon's launcher.

> **Arch users:** Talon's Tobii udev rules use the `plugdev` group, which may not
> exist by default. If Talon detects your Tobii tracker but fails to open it with
> `EyeOpenErr: Eye Tracker open failed`, create the group and add your user to
> it:
>
> ```sh
> sudo groupadd -f plugdev
> sudo usermod -aG plugdev "$USER"
> ```
>
> Reboot afterward so your session and Talon inherit the new group membership,
> then reconnect the tracker.

## Speech toggle recipes

Niri: use F8 to toggle Talon's speech.

```kdl
F8 repeat=false allow-inhibiting=false hotkey-overlay-title="Talon Toggle Listen" {
    spawn-sh "printf 'from talon import actions; actions.speech.toggle()\\n' | \"$HOME/.talon/bin/repl\" >/dev/null";
}
```

Hyprland: add this to a loaded Lua config module to use F8 to toggle Talon's
speech. For example, Omarchy loads `~/.config/hypr/bindings.lua` from its main
Hyprland config.

```lua
hl.bind(
  "code:74",
  hl.dsp.exec_cmd(
    [[sh -c 'printf "%s\n" "from talon import actions; actions.speech.toggle()" | "$HOME/.talon/bin/repl" >/dev/null']]
  ),
  { description = "Talon toggle listen" }
)
```

## Planned features

- Remaining built-in Talon eye-tracking modes
- Physical keyboard input so Talon can listen for hotkeys under Wayland

## Development

Current and completed work is tracked in [the task list](docs/tasks.md).

### Wayland application contexts

Wayland exposes an application ID rather than an executable path. Some Talon
Community contexts therefore need an explicit mapping for the ID reported by the
compositor. Contributions for additional verified mappings are welcome. Helper
windows and browser PWAs may have distinct IDs and should be checked separately.

Run the unit and lightweight runtime tests with Talon's bundled Python:

```sh
PYTHONDONTWRITEBYTECODE=1 "$HOME/.talon/bin/python" -m unittest discover -s tests -v
```

The suite uses fake Talon/protocol objects and local resources such as
`libxkbcommon`, temporary descriptors, and socket pairs. It does not inject live
input. A passing fake-based suite does not establish end-to-end support for every
compositor.

If Ruff is installed, run the configured static checks with:

```sh
ruff check apps core plugins tests
ruff format --check apps core plugins tests
```

Rebuild the pinned PyWayland bundle and generated protocol bindings with:

```sh
python tools/build_pywayland_vendor.py
```

The build requires `~/.talon/bin/python`, GCC, binutils, Wayland development
headers, and network access. See `third_party/README.md` for source, patch, and
reconstruction details.
