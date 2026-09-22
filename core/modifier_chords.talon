# Global Talon Lite modifier grammar.
#
# Requires <user.unmodified_key> from Talon Community or an equivalent capture.
#
# Examples:
#   walt left       -> super-alt-left
#   trash tab       -> ctrl-alt-shift-tab
#   squash space    -> ctrl-alt-shift-super-space
#
# Expanded combinations also work in either order:
#   win alt left
#   alt win left
#   troll shift tab

<user.talon_lite_modifiers> <user.unmodified_key>:
    key("{talon_lite_modifiers}-{unmodified_key}")

crisp: key("ctrl-space")

# Tap modifier keys without supplying another key.
press <user.talon_lite_modifiers>:
    key(talon_lite_modifiers)
