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

The latest supported version is `0.4.0-950-bd10`. Support for
`0.4.0-1050-3c4a` is planned, but Talon's UI layer currently crashes under Wayland.
If you have it working, please let me know.

## Current features

- Layout-aware native keyboard input
- Eye tracking through Control Mouse (Legacy), including the custom Hiss Mouse
  mode. Gaze is mapped to Talon's main eye-mouse output, so additional monitors
  and compositor scaling do not change its position multipliers.
- Mouse button and scrolling commands such as `touch`, `righty`, `drag`, and
  `wheel up`, including continuous fractional-line scrolling
- Active Wayland application and window-title contexts
- Optional compositor voice-command app layers (currently Hyprland, but more are to be added)

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

The optional `apps/hyprland/` voice-command layer requires the Lua-capable
`hyprctl eval` API. It is separate from the shared native Wayland forwarding.

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

The bundled native backend requires Linux x86-64, Talon's CPython 3.13, glibc 2.34
or newer, and `libxkbcommon.so.0` (normally installed on Wayland desktops).
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

The incremental cleanup tasks and acceptance criteria are tracked in
[the cleanup plan](docs/cleanup-plan.md).

`plugins/wayland_runtime.py` owns lifecycle and scheduled event delivery;
`plugins/wayland_scopes.py` owns Talon app/window scope providers and aliases.
It exposes the raw Wayland app ID unchanged through Talon's `app.name` field.
`plugins/wayland_app_aliases.py` extends Community's Linux app detection for
Chromium (`chromium` and `chromium-browser`), Nautilus (`org.gnome.Nautilus`),
and Evince (`org.gnome.Evince`) without editing Community files. It also groups
Foot and Omarchy Terminal under a local terminal context that enables Community's
shell, Git, kubectl, and Readline command sets.

### Wayland application contexts

The foreign-toplevel protocol exposes an application ID and window title, but
not an executable path. Talon Community application definitions that rely on
`app.exe`, or expect a different `app.name`, may therefore not activate
automatically.

`plugins/wayland_app_aliases.py` maps verified raw Wayland app IDs to existing
Community application aliases. Contributions for additional observed mappings
are welcome so Community contexts work across more Wayland applications.

Mappings should be verified against real application windows. Helper processes,
preview windows, and browser PWAs may use distinct IDs and should not
automatically inherit their parent application's context. An alias enables an
existing Community context; it does not provide actions that Community has not
implemented for Linux.

The protocol adapters and transport remain Talon-free in `plugins/wayland_backend/`.

Run the unit and lightweight runtime tests with Talon's bundled Python:

```sh
PYTHONDONTWRITEBYTECODE=1 "$HOME/.talon/bin/python" -m unittest discover -s tests -v
```

The suite uses fake Talon/protocol objects and local resources such as
`libxkbcommon`, temporary descriptors, and socket pairs. It does not inject live
input. A passing fake-based suite does not establish end-to-end support for every
compositor; live checks are recorded separately in the cleanup plan.

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
