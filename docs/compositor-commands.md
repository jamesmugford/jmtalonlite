# Compositor commands

The Sway and niri layers use Talon Community's
[`apps/i3wm/`](https://github.com/talonhub/community/tree/main/apps/i3wm) commands as
their reference: the same spoken forms, standard desktop actions, and resize
defaults where an equivalent exists. The adapted command files retain Community's
[MIT license](../third_party/community-LICENSE.txt).

Each layer invokes the compositor's CLI on demand: `swaymsg` or `niri msg action`.
Calls have a one-second timeout and failures are reported by Talon. No background
IPC connection or polling is used. Niri's directional window move additionally
queries the focused window once to distinguish floating from tiled movement.

## Requirements and activation

- Install Community alongside Talon Lite. These commands use Community's desktop
  commands, `user.arrow_key`, `user.text`, and `user.i3wm_resize_dirs` capture.
- Sway requires `swaymsg` on Talon's `PATH`, with access to its session IPC socket.
- Niri requires `niri` on Talon's `PATH` and its session IPC socket. The mappings
  were checked against the niri 26.04 action API.
- Both layers auto-enable on Talon readiness in their own Linux desktop session.
  `XDG_CURRENT_DESKTOP` takes precedence over `XDG_SESSION_DESKTOP`; `SWAYSOCK` or
  `NIRI_SOCKET` is used as a fallback only when desktop identity is unavailable.
- Disable automatic activation with `user.sway_auto_enable = 0` or
  `user.niri_auto_enable = 0`. The corresponding manual tags are `user.sway` and
  `user.niri`. Leave Community's `user.i3wm` tag disabled in these sessions so its
  `i3-msg` bindings do not compete with the compositor-specific commands.

The adapters have command-construction and failure-path unit tests and have been
loaded by Talon on Hyprland to check registration and inactive scoping. Live Sway
and niri session verification is still pending. Command-layer support is separate
from validating native input, eye tracking, and app contexts on each compositor.

## Shared spoken forms

`win` and `window` are interchangeable below. Ordinary `desk` commands are provided
by Community and reach the compositor through our standard action overrides.

| Spoken form | Sway | niri |
| --- | --- | --- |
| `desk <number>` | Numbered workspace, including workspace 0 | Workspace index 1-255 |
| `desk next/right`, `desk last/left` | Next/previous workspace | Workspace down/up |
| `desk flip` / `flipper` | Previously focused workspace | Previously focused workspace |
| `desk show` | No Sway override | Toggle overview |
| `window move desk <number>` / `shuffle <number>` | Move to workspace without following | Move to workspace without following |
| `window move desk left/right` | Move to previous/next workspace | Move to workspace above/below without following |
| `window left/right` | Directional focus | Focus column left/right, or floating window in that direction |
| `window up/down` | Directional focus | Focus window up/down |
| `window kill` | Close focused container | Close focused window |
| `window center` | Center floating container | Center window |
| `window position <x> <y>` | Floating position in output percentage points | Floating position in working-area percentages |
| `window width/height <number>` | Set dimension in percentage points | Set dimension as working-area percentage |
| `window grow/shrink [amount] [directions]` | Resize selected edges or axes | Resize the corresponding width/height axes |
| `full screen` / `scuba` | Toggle fullscreen | Toggle fullscreen |
| `toggle floating` | Toggle floating | Toggle floating |
| `focus floating` | Switch floating/tiling focus | Switch floating/tiling focus |
| `reload sway config` / `reload niri config` | Reload Sway configuration | Reload niri configuration |

Resize amounts are **ten-pixel steps**, defaulting to **4** (40 logical pixels),
and default directions are `height width`, following Community's i3 convention.
For example, `window grow two width` grows by 20 pixels. Sway uses explicit `px`
units even for tiled windows and attempts each requested direction if one edge
cannot resize; IPC failures still propagate.

Current Community forms are used directly. The deprecated `port <number>`,
`grow window`, and `center window` aliases are not copied. The still-current
`move window to port <number>` and `shuffle <number>` forms are included.

## Sway-specific forms

Sway retains the direct i3 equivalents:

- `window stacking/stacked/tabbed/default` (default toggles the split layout).
- `window horizontal/vertical`, `focus parent/child`, and `resize mode`.
- `horizontal shell/terminal` and `vertical shell/terminal` split before opening
  the configured terminal.
- `shuffle left/right/up/down` or `move window left/right/up/down`.
- `shuffle last port`, `move window to last port`, and `move flipper`.
- `make scratch`, `[show/hide] scratch`, `next scratch`, and
  `new scratch shell/window`.

`resize mode` requires a `resize` binding mode in the Sway configuration. The
compound launcher/scratchpad commands retain Community's short launch delays.
Sway has no i3-style in-place restart command, so configuration reload is supplied
instead of a misleading restart alias. Community's X11-ID-based application
switcher is not copied into this layer.

## Niri equivalents

- `shuffle left/right` moves **one window** with niri's consume-or-expel action;
  `shuffle up/down` reorders that window within its column.
- The same directional moves nudge a floating window by ten logical pixels.
- `move column left/right` explicitly moves an entire column.
- `window tabbed`, `window stacking`, and `window stacked` all select niri's
  tabbed column display, its closest one-visible-window equivalent.
  `window default` returns the column to normal display.
- Resize direction words `left/right/width` select width and `up/down/height`
  select height. Each requested axis is resized once; these do not anchor a
  particular edge as i3/Sway can.
- Workspace numbers are dynamic indices, not persistent i3 workspace numbers.
  Moving to another workspace explicitly uses `--focus=false`.

Niri has no directly equivalent scratchpad, parent/child container traversal,
prospective split orientation, or named i3 resize mode. Those spoken forms are
omitted. Moving to the previously focused workspace is also omitted: the native
action can focus the previous workspace, but exposes no equivalent move target
without adding history tracking or changing focus.

## Launcher, terminal, and lock shortcuts

Like Community's i3 layer, these operations invoke configured key bindings:
`launch [text]`, `launch shell/terminal` / `koopa`, and `lock screen`.

| Setting | Default |
| --- | --- |
| `user.sway_launch_key` | `super-d` |
| `user.sway_terminal_key` | `super-enter` |
| `user.sway_lock_key` | `super-shift-x` (must be bound in your configuration) |
| `user.niri_launch_key` | `super-d` |
| `user.niri_terminal_key` | `super-t` |
| `user.niri_lock_key` | `super-alt-l` |

Override these in your personal `settings.talon` to match your compositor's
bindings. The niri defaults follow its example configuration; its configured
launcher, terminal, and screen-lock programs must be installed.

## Upstream command references

- [Sway commands](https://man.archlinux.org/man/sway.5.en)
- [swaymsg and its exit statuses](https://man.archlinux.org/man/swaymsg.1.en)
- [niri IPC and CLI](https://niri-wm.github.io/niri/IPC.html)
- [niri action reference](https://niri-wm.github.io/niri/niri_ipc/enum.Action.html)
