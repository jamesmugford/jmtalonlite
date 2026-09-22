"""Pure parsing and planning for Talon keyboard specifications."""

from __future__ import annotations

from dataclasses import dataclass
from enum import StrEnum


class KeyAction(StrEnum):
    """A state transition requested by one Talon key token."""

    TAP = "tap"
    DOWN = "down"
    UP = "up"


@dataclass(frozen=True, slots=True)
class KeyStroke:
    """One parsed Talon key token before keymap resolution."""

    modifiers: tuple[str, ...]
    key: str | None
    action: KeyAction
    repeat: int


@dataclass(frozen=True, slots=True)
class ResolvedStroke:
    """One key stroke resolved to compositor keycodes."""

    modifiers: tuple[int, ...]
    keycode: int | None
    action: KeyAction
    repeat: int


@dataclass(frozen=True, slots=True)
class KeyEvent:
    """One desired keycode state transition."""

    keycode: int
    pressed: bool


@dataclass(frozen=True, slots=True)
class KeyPlan:
    """An ordered keyboard event plan and its resulting held state."""

    events: tuple[KeyEvent, ...]
    held_after: frozenset[int]


_MODIFIER_NAMES = frozenset(("ctrl", "alt", "shift", "super"))
_KEY_NAME_ALIASES = {
    "win": "super",
    "return": "enter",
    "escape": "esc",
}


def _canonical_key_name(name: str) -> str:
    """Normalize named keys and aliases without changing literal characters."""
    if len(name) == 1:
        return name
    name = name.lower()
    return _KEY_NAME_ALIASES.get(name, name)


def parse_key_spec(key_spec: str) -> tuple[KeyStroke, ...]:
    """Parse Talon's space-separated key syntax into immutable strokes."""
    if not isinstance(key_spec, str):
        raise TypeError("Talon key spec must be a string")

    strokes = []
    for token in key_spec.split():
        base, action, repeat = _parse_suffix(token)
        parts = base.split("-")
        modifiers = []
        while parts:
            modifier = _canonical_key_name(parts[0])
            if modifier not in _MODIFIER_NAMES:
                break
            parts.pop(0)
            if modifier not in modifiers:
                modifiers.append(modifier)
        key = _canonical_key_name("-".join(parts)) or None
        if key is None and not modifiers:
            raise ValueError(f"Invalid Talon key: {token!r}")
        strokes.append(KeyStroke(tuple(modifiers), key, action, repeat))
    return tuple(strokes)


def modifier_chord(
    key_spec: str,
) -> tuple[tuple[KeyStroke, ...], tuple[KeyStroke, ...]]:
    """Return down and up strokes for one modifier-only Talon chord."""
    strokes = parse_key_spec(key_spec)
    if (
        len(strokes) != 1
        or strokes[0].key is not None
        or strokes[0].action is not KeyAction.TAP
    ):
        raise ValueError("Modified click requires one modifier-only chord")
    modifiers = strokes[0].modifiers
    return (
        (KeyStroke(modifiers, None, KeyAction.DOWN, 1),),
        (KeyStroke(modifiers, None, KeyAction.UP, 1),),
    )


def plan_key_events(
    strokes: tuple[ResolvedStroke, ...],
    held_keys: frozenset[int],
) -> KeyPlan:
    """Plan idempotent key transitions for resolved strokes and held keys."""
    held = set(held_keys)
    events: list[KeyEvent] = []

    def set_pressed(keycode: int, pressed: bool) -> None:
        """Append a transition only when the requested key state changes."""
        if (keycode in held) == pressed:
            return
        events.append(KeyEvent(keycode, pressed))
        if pressed:
            held.add(keycode)
        else:
            held.remove(keycode)

    for stroke in strokes:
        if stroke.action is KeyAction.DOWN:
            for modifier in stroke.modifiers:
                set_pressed(modifier, True)
            if stroke.keycode is not None:
                set_pressed(stroke.keycode, True)
            continue

        if stroke.action is KeyAction.UP:
            if stroke.keycode is not None:
                set_pressed(stroke.keycode, False)
            for modifier in reversed(stroke.modifiers):
                set_pressed(modifier, False)
            continue

        if stroke.keycode is None:
            for _ in range(stroke.repeat):
                pressed_here = tuple(
                    modifier for modifier in stroke.modifiers if modifier not in held
                )
                for modifier in pressed_here:
                    set_pressed(modifier, True)
                for modifier in reversed(pressed_here):
                    set_pressed(modifier, False)
            continue

        pressed_here = tuple(
            modifier for modifier in stroke.modifiers if modifier not in held
        )
        for modifier in pressed_here:
            set_pressed(modifier, True)
        if stroke.keycode in held:
            raise RuntimeError(
                f"Cannot tap keyboard keycode {stroke.keycode} while it is held"
            )
        for _ in range(stroke.repeat):
            set_pressed(stroke.keycode, True)
            set_pressed(stroke.keycode, False)
        for modifier in reversed(pressed_here):
            set_pressed(modifier, False)

    return KeyPlan(tuple(events), frozenset(held))


def _parse_suffix(token: str) -> tuple[str, KeyAction, int]:
    """Split one Talon token into its base, action, and repeat count."""
    if ":" not in token:
        return token, KeyAction.TAP, 1
    base, suffix = token.rsplit(":", 1)
    if suffix in (KeyAction.DOWN, KeyAction.UP):
        if not base:
            raise ValueError(f"Invalid Talon key: {token!r}")
        return base, KeyAction(suffix), 1
    if suffix.isdigit():
        repeat = int(suffix)
        if not base or repeat < 1:
            raise ValueError(f"Invalid Talon key repeat: {token!r}")
        return base, KeyAction.TAP, repeat
    return token, KeyAction.TAP, 1
