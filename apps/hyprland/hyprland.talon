# Hyprland commands auto-enable when Talon starts under Hyprland.
# Reference: Community apps/i3wm/i3wm.talon; desktop commands use user.desktop().
# Adapted under the MIT license in third_party/community-LICENSE.txt.
os: linux
tag: user.hyprland
-
desk flip | flipper: user.hyprland_switch_to_workspace("previous")

(win | window) left: user.hyprland_focus("left")
(win | window) right: user.hyprland_focus("right")
(win | window) up: user.hyprland_focus("up")
(win | window) down: user.hyprland_focus("down")
(win | window) kill: app.window_close()

reload (hyper land | hypr land) config: user.hyprland_reload()

(full screen | scuba): user.hyprland_fullscreen()
toggle floating: user.hyprland_float()
focus floating: user.hyprland_focus_mode_toggle()
(win | window) center: user.hyprland_center()

(win | window) grow [<number>] [<user.i3wm_resize_dirs>]:
    user.hyprland_resize_window("grow", number or 4, i3wm_resize_dirs or "height width")
(win | window) shrink [<number>] [<user.i3wm_resize_dirs>]:
    user.hyprland_resize_window("shrink", number or 4, i3wm_resize_dirs or "height width")

(shuffle | move (win | window) [to] port) <number_small>:
    user.hyprland_move_to_workspace(number_small)
(shuffle | move (win | window) [to]) last port:
    user.hyprland_move_to_workspace("previous")
(shuffle | move) flipper: user.hyprland_move_to_workspace("previous")
(shuffle | move (win | window) left): user.hyprland_move("left")
(shuffle | move (win | window) right): user.hyprland_move("right")
(shuffle | move (win | window) up): user.hyprland_move("up")
(shuffle | move (win | window) down): user.hyprland_move("down")

make scratch: user.hyprland_move_to_scratchpad()
[(show | hide)] scratch: user.hyprland_show_scratchpad()
