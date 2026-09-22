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

It is entirely event-driven and adds negligible latency.

## Talon support

The latest supported version is `0.4.0-950-bd10`. Support for
`0.4.0-1050-3c4a` is planned, but Talon's UI layer currently crashes under Wayland. If you have it working, please let me
know.

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

## Supported compositors

- Hyprland
- labwc
- Mir
- niri
- phoc
- river
- Sway
- Wayfire

## Wayland protocols

- `wl_output` - output discovery and main eye-mouse display matching
- `zwp_virtual_keyboard_manager_v1` - keyboard input
- `zwlr_virtual_pointer_manager_v1` - pointer, clicks, scrolling, gaze, hiss, and pop input
- `zwlr_foreign_toplevel_manager_v1` - application and window-title contexts

## Installation

```sh
git clone https://github.com/jamesmugford/jmtalonlite $HOME/.talon/user/jmtalonlite
```

Talon Lite is clone-and-run and only requires `libxkbcommon.so.0`, which is
normally already installed on Wayland desktops. Other runtime dependencies and
protocol bindings are bundled, so no additional packages, input daemons, or
uinput setup are required. Restart Talon after cloning.

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

Run the unit and lightweight runtime tests:

```sh
PYTHONDONTWRITEBYTECODE=1 python -m unittest discover -s tests -v
```

If Ruff is installed, run the configured static checks with:

```sh
ruff check apps plugins tests
ruff format --check apps plugins tests
```

Rebuild the pinned PyWayland bundle and generated protocol bindings with:

```sh
python tools/build_pywayland_vendor.py
```

The build requires `~/.talon/bin/python`, GCC, binutils, Wayland development
headers, and network access. See `third_party/README.md` for source, patch, and
reconstruction details.
