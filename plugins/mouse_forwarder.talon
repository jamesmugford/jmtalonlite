tag: user.wayland_mouse_forwarder
-
touch: user.mouse_forwarder_touch()

righty: mouse_click(1)

mid click: mouse_click(2)

<user.talon_lite_modifiers> touch:
    user.mouse_forwarder_modified_click(talon_lite_modifiers, 0)

<user.talon_lite_modifiers> righty:
    user.mouse_forwarder_modified_click(talon_lite_modifiers, 1)

(dub click | duke):
    mouse_click(0)
    mouse_click(0)

(trip click | trip lick):
    mouse_click(0)
    mouse_click(0)
    mouse_click(0)

left drag | drag | drag start: user.mouse_drag(0)

right drag | righty drag: user.mouse_drag(1)

end drag | drag end: user.mouse_drag_end()

wheel down: user.mouse_scroll_down()

wheel down here:
    user.mouse_move_center_active_window()
    user.mouse_scroll_down()

wheel tiny [down]: user.mouse_scroll_down(0.2)

wheel tiny [down] here:
    user.mouse_move_center_active_window()
    user.mouse_scroll_down(0.2)

wheel up: user.mouse_scroll_up()

wheel up here:
    user.mouse_move_center_active_window()
    user.mouse_scroll_up()

wheel tiny up: user.mouse_scroll_up(0.2)

wheel tiny up here:
    user.mouse_move_center_active_window()
    user.mouse_scroll_up(0.2)

wheel left: user.mouse_scroll_left()

wheel left here:
    user.mouse_move_center_active_window()
    user.mouse_scroll_left()

wheel tiny left: user.mouse_scroll_left(0.5)

wheel tiny left here:
    user.mouse_move_center_active_window()
    user.mouse_scroll_left(0.5)

wheel right: user.mouse_scroll_right()

wheel right here:
    user.mouse_move_center_active_window()
    user.mouse_scroll_right()

wheel tiny right: user.mouse_scroll_right(0.5)

wheel tiny right here:
    user.mouse_move_center_active_window()
    user.mouse_scroll_right(0.5)
