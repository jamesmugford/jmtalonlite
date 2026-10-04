# Reference: Community apps/i3wm/i3wm.talon; desktop commands use user.desktop().
# Adapted under the MIT license in third_party/community-LICENSE.txt.
os: linux
tag: user.sway
-
desk flip | flipper: user.swaymsg("workspace back_and_forth")

(win | window) left: user.swaymsg("focus left")
(win | window) right: user.swaymsg("focus right")
(win | window) up: user.swaymsg("focus up")
(win | window) down: user.swaymsg("focus down")
(win | window) kill: app.window_close()
(win | window) (stacking | stacked): user.swaymsg("layout stacking")
(win | window) default: user.swaymsg("layout toggle split")
(win | window) tabbed: user.swaymsg("layout tabbed")

(win | window) position <number_small> <number_small>:
    user.swaymsg("move position {number_small_1} ppt {number_small_2} ppt")
(win | window) center: user.swaymsg("move position center")
(win | window) width <number_small>: user.swaymsg("resize set width {number_small} ppt")
(win | window) height <number_small>: user.swaymsg("resize set height {number_small} ppt")
(win | window) grow [<number>] [<user.i3wm_resize_dirs>]:
    user.sway_resize_window("grow", number or 4, i3wm_resize_dirs or "height width")
(win | window) shrink [<number>] [<user.i3wm_resize_dirs>]:
    user.sway_resize_window("shrink", number or 4, i3wm_resize_dirs or "height width")

reload sway config: user.swaymsg("reload")
full screen | scuba: user.swaymsg("fullscreen")
toggle floating: user.swaymsg("floating toggle")
focus floating: user.swaymsg("focus mode_toggle")
resize mode: user.swaymsg("mode resize")
focus parent: user.swaymsg("focus parent")
focus child: user.swaymsg("focus child")

horizontal (shell | terminal):
    user.swaymsg("split h")
    user.sway_shell()
vertical (shell | terminal):
    user.swaymsg("split v")
    user.sway_shell()

(shuffle | move (win | window) [to] port) <number_small>:
    user.window_move_desktop(number_small)
(shuffle | move (win | window) [to]) last port:
    user.swaymsg("move container to workspace back_and_forth")
(shuffle | move) flipper: user.swaymsg("move container to workspace back_and_forth")
(shuffle | move (win | window)) {user.arrow_key}: user.swaymsg("move {arrow_key}")
(win | window) horizontal: user.swaymsg("split h")
(win | window) vertical: user.swaymsg("split v")

make scratch: user.swaymsg("move scratchpad")
[(show | hide)] scratch: user.swaymsg("scratchpad show")
next scratch:
    user.swaymsg("scratchpad show")
    user.swaymsg("scratchpad show")

launch: user.sway_launch()
launch <user.text>:
    user.sway_launch()
    sleep(100ms)
    insert("{text}")
lock screen: user.sway_lock()
launch (shell | terminal) | koopa: user.sway_shell()
new scratch (shell | window):
    user.sway_shell()
    sleep(200ms)
    user.swaymsg("move scratchpad")
    user.swaymsg("scratchpad show")
