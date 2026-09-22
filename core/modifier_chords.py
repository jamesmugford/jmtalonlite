"""Compact spoken modifier chords for Talon Lite.

This module owns the vocabulary and normalises every combination into
Talon's ctrl-alt-shift-super key notation.

It has no dependency on talonhub/community.
"""

from talon import Context, Module

mod = Module()
ctx = Context()

mod.list(
    "talon_lite_modifier",
    desc="Single-word and expanded modifier combinations",
)

ctx.lists["user.talon_lite_modifier"] = {
    # Atomic modifiers
    "control": "ctrl",
    "troll": "ctrl",
    "alt": "alt",
    "shift": "shift",
    "super": "super",
    "win": "super",

    # Two modifiers
    #
    # colt  = COntroL + alT
    # crush = CR from control + SH from shift; U is spoken glue
    # twin  = T from troll + WIN
    # ash   = A from alt + SH from shift
    # walt  = W from win + ALT
    # swish = S from shift + WI from win + SH from shift
    "colt": "ctrl-alt",
    "crush": "ctrl-shift",
    "twin": "ctrl-super",
    "ash": "alt-shift",
    "walt": "alt-super",
    "swish": "shift-super",

    # Three modifiers
    #
    # trash = TRoll + Alt + SHift
    # claw  = Control + aLt + Win
    # twist = Troll + WIn + Shift's outer S...T sounds
    # swat  = Shift + Win + AlT
    "trash": "ctrl-alt-shift",
    "claw": "ctrl-alt-super",
    "twist": "ctrl-shift-super",
    "swat": "alt-shift-super",

    # All four modifiers
    #
    # squash = /k/ from control + W from win + A from alt + SH from shift.
    # S and U provide the natural spoken construction.
    "squash": "ctrl-alt-shift-super",
}

_MODIFIER_ORDER = ("ctrl", "alt", "shift", "super")


@mod.capture(rule="{user.talon_lite_modifier}+")
def talon_lite_modifiers(m) -> str:
    """Combine modifier words, remove duplicates and use canonical ordering."""

    requested = {
        modifier
        for chord in m.talon_lite_modifier_list
        for modifier in chord.split("-")
    }

    return "-".join(
        modifier
        for modifier in _MODIFIER_ORDER
        if modifier in requested
    )
