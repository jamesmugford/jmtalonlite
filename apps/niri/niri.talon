# Reference: Community apps/i3wm/i3wm.talon. See docs/compositor-commands.md
# for niri's column/window equivalents and the i3-only forms omitted here.
# Adapted under the MIT license in third_party/community-LICENSE.txt.
os: linux
tag: user.niri
-
desk flip | flipper: user.niri_action("focus-workspace-previous")

(win | window) left: user.niri_action("focus-column-left")
(win | window) right: user.niri_action("focus-column-right")
(win | window) up: user.niri_action("focus-window-up")
(win | window) down: user.niri_action("focus-window-down")
(win | window) kill: app.window_close()
(win | window) (stacking | stacked | tabbed): user.niri_action("set-column-display tabbed")
(win | window) default: user.niri_action("set-column-display normal")

(win | window) position <number_small> <number_small>:
    user.niri_action("move-floating-window --x {number_small_1}% --y {number_small_2}%")
(win | window) center: user.niri_action("center-window")
(win | window) width <number_small>: user.niri_action("set-window-width {number_small}%")
(win | window) height <number_small>: user.niri_action("set-window-height {number_small}%")
(win | window) grow [<number>] [<user.i3wm_resize_dirs>]:
    user.niri_resize_window("grow", number or 4, i3wm_resize_dirs or "height width")
(win | window) shrink [<number>] [<user.i3wm_resize_dirs>]:
    user.niri_resize_window("shrink", number or 4, i3wm_resize_dirs or "height width")

reload niri config: user.niri_action("load-config-file")
full screen | scuba: user.niri_action("fullscreen-window")
toggle floating: user.niri_action("toggle-window-floating")
focus floating: user.niri_action("switch-focus-between-floating-and-tiling")

(shuffle | move (win | window) [to] port) <number_small>:
    user.window_move_desktop(number_small)
(shuffle | move (win | window)) {user.arrow_key}: user.niri_move_window(arrow_key)

# Explicit column movement supplements the single-window shuffle commands.
move column left: user.niri_action("move-column-left")
move column right: user.niri_action("move-column-right")

launch: user.niri_launch()
launch <user.text>:
    user.niri_launch()
    sleep(100ms)
    insert("{text}")
lock screen: user.niri_lock()
launch (shell | terminal) | koopa: user.niri_shell()
