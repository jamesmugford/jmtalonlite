# Cleanup plan

## Goal

Make the configuration smaller, clearer, and easier to maintain while preserving
its working user experience. Prefer fewer competing code paths and explicit
ownership over clever abstractions or a line-count target.

This is the agreed task list. Complete one numbered piece, record its changes and
verification, then stop before starting the next piece. New unrelated findings
belong in the backlog; they do not automatically expand the scope. Commit and
push only when requested.

## Working contract

- Keep the standard Talon API and normal Community/app-specific dispatch.
- Preserve spoken commands, settings, gaze mapping, and scrolling behavior except
  for the specific bug fixes below.
- Keep current-generation reload safety and manual enabled/disabled state.
- Keep the single Wayland owner thread, protocol cleanup ordering, and the rule
  against replaying input after potentially partial emission.
- Standard Talon actions participate in shim ownership tracking. Direct `ctrl`
  calls bypass it; do not build speculative tracking for arbitrary custom code.
- Do not edit `../jmtalonfacegestures` or `~/Projects/jmfacegestures`, or change
  face-gesture enabled state.
- Coordinate any Talon restart or live-input test before interrupting input.
  Muting speech alone does not necessarily stop gaze, hiss, or face gestures.

## Elegance criteria

Review each piece against these questions:

1. Does it reduce the number of concepts needed to understand the feature?
2. Is each piece of state owned in one clear place?
3. Does each abstraction justify its existence?
4. Do names explain intent, units, and ownership?
5. Is the ordinary execution path easy to follow?
6. Is useful behavior preserved without speculative machinery?

Reject indirection that does not improve these answers. Necessary correctness
state may add lines; fewer lines alone is not the measure of success.

## Tasks

- [x] **01 — Make the test baseline reliable**
  - Hold simulated session environments throughout relevant imports and test
    actions, rather than only during module import.
  - Make non-Wayland cases explicit and establish the baseline with Talon's
    supported CPython 3.13.
  - Main files: `tests/test_mouse_forwarder.py`, `tests/test_tracking_reload.py`.
  - **Done when:** tests exercise their intended paths regardless of the host
    desktop. Add production bug regressions with their respective fixes below.

- [x] **02 — Repair Control Mouse notifications**
  - Use one `control1_state_changed(enabled)` notification with a concrete default
    implementation and an overlay consumer.
  - Remove the superseded start/stop notification chain and misleading unused
    lifecycle actions. Preserve menu-triggered state notifications.
  - Main files: `plugins/tracking_forwarder/control1_state_events.py` and
    `plugins/tracking_forwarder/control1_debug_overlay.py`.
  - **Done when:** enabling and disabling reach overlay synchronization without
    calling an unimplemented action.

- [ ] **03 — Fix continuous-to-wheel scroll transitions**
  - Establish the wheel source after addressing each discrete axis.
  - Preserve scaling, direction, continuous scrolling, and frame grouping.
  - Main files: `plugins/wayland_backend/pointer.py`,
    `tests/test_wayland_pointer.py`.
  - **Done when:** continuous scrolling followed by vertical, horizontal, or
    combined wheel scrolling emits the correct source on every axis. A failure
    after emission does not cause replay.

- [ ] **04 — Fix fallback drag bookkeeping**
  - Make standard drag/release actions the authoritative bookkeeping boundary.
  - Correct duplicate updates in the Community-shaped toggle chain while
    preserving native toggle and drag-end behavior.
  - Main files: `plugins/mouse_forwarder.py`, `tests/test_mouse_forwarder.py`.
  - **Done when:** fallback drag start/end updates ownership exactly once, and
    release stays with the correct backend after capability changes. Cover
    nested dispatch and failure propagation.

- [ ] **05 — Tighten the keyboard input boundary**
  - Normalize documented named aliases, including `win`/`super` and
    `return`/`enter`, consistently with fallback hold tracking.
  - Validate all resolved keycodes, including modifiers, before any emission.
    Preserve literal-character case and existing layout/remapping semantics.
  - Main files: `plugins/wayland_backend/key_spec.py`,
    `plugins/wayland_backend/keyboard.py`, `plugins/wayland_runtime.py`.
  - **Done when:** equivalent named keys release the same tracked hold, and an
    invalid keycode cannot leave a partially emitted chord.

- [ ] **06 — Make temporary modifier cleanup lifetime-safe**
  - Associate release records with the actual presses they introduced.
  - Invalidate stale records after keyboard/keymap replacement or
    release-and-repress; keep repeated cleanup harmless.
  - Preserve the existing introduced-press cleanup contract, rather than adding
    independently owned, reference-counted modifier scopes.
  - Main files: `plugins/wayland_backend/keyboard.py`,
    `plugins/wayland_backend/desktop.py`, `plugins/wayland_runtime.py`.
  - **Done when:** old cleanup cannot release a newer hold, preheld modifiers
    remain protected, and repeated cleanup does nothing.

- [ ] **07 — Remove redundant command and action routes**
  - Route wheel commands through standard directional actions and the main
    `mouse_scroll` override. Remove duplicate directional forwarding and unused
    one-axis helpers.
  - Replace the trivial key-chord wrapper with direct `key(...)` calls and remove
    associated dead declarations and unused re-exports.
  - Main files: `plugins/mouse_forwarder.py`, `plugins/mouse_forwarder.talon`,
    `core/modifier_chords.py`, `core/modifier_chords.talon`.
  - **Done when:** the same spoken commands use normal Talon dispatch through
    fewer implementations. Touch, drag, and modified-click semantics remain
    intact. Normal dispatch intentionally honors app overrides and removes
    incidental retries through redundant wrappers.

- [ ] **08 — Simplify historical reload machinery**
  - Remove retired runtime migrations, old-global recovery, obsolete retained
    formats, and migration-only tests.
  - Replace historical duplicate-draining loops with exact callback ownership
    and idempotent registration. Preserve current reload and failed-cleanup
    handling.
  - Distinguish unresolved startup intent from explicitly enabled/disabled state.
  - Main areas: `plugins/wayland_runtime.py`, `plugins/tracking_forwarder/`,
    and their lifecycle tests.
  - **Done when:** reload leaves one active resource owner, manual disable
    survives reload, autostart resolves correctly, and failed releases retain
    their cleanup handles.
  - **Deployment:** coordinate a Talon restart for this compatibility cut; do not
    deploy it through an uncontrolled sequence of live reloads.

- [ ] **09 — Clarify the runtime's responsibilities**
  - Extract scope/provider management and application-alias handling into one
    focused Talon-side component.
  - Keep backend lifecycle, window-event scheduling, and generation guards under
    the bridge's ownership. Constructors must not register or connect resources.
  - Main area: `plugins/wayland_runtime.py`.
  - **Done when:** scope management is understandable independently, with unchanged
    event ordering, startup, restoration, and teardown. This is a structural
    extraction, not a scheduling or threading rewrite.

- [ ] **10 — Finish naming, local duplication, and documentation**
  - Improve ambiguous registry-ID names and vague diagnostic locals.
  - Reuse existing cleanup aggregation where policies match and consolidate
    small, genuinely identical test scaffolding.
  - Document dependencies, runtime requirements, and normal dispatch.
  - **Done when:** the final diff is coherent, documentation matches behavior,
    and every completed piece has recorded verification. Run the complete
    supported-Python suite and configured Ruff checks when available.

## Verification and operating procedure

- Begin a bug fix with a focused reproducer. Use existing fakes and real XKB only
  where relevant; do not create a general Talon/compositor simulator.
- Run focused tests after each change and the full suite between pieces. Use
  Talon's CPython 3.13 and `PYTHONDONTWRITEBYTECODE=1`.
- For piece 01, run under simulated Wayland, X11, and headless session conditions.
  Each test still establishes its own intended environment.
- Run `ruff check apps plugins tests` and `ruff format --check apps plugins tests`
  when Ruff is available. Report unavailable checks instead of installing tools
  silently. Include `core` when its Python code is changed.
- Unit tests run in a separate process with fake Talon/protocol APIs and do not
  send live input. Production edits may trigger Talon reloads; agree on pauses
  before disruptive changes, restarts, or live-input checks.
- After each piece, report changes, acceptance results, blockers, and any new
  backlog entries. Stop before the next numbered piece.

## Progress and verification

### 01 — Complete

- Starting revision: `f7011e2`.
- Reproduced five failures in the 16 mouse-forwarder/tracking-reload tests under
  simulated X11 using Talon's CPython 3.13: four scroll tests and one gaze test
  depended on the ambient desktop environment.
- Moved mouse-test session setup into the class fixture so it covers imports,
  actions, and cleanup. Tracking tests establish their session in each test's
  fixture. Both use unittest-managed restoration of the original environment.
- Removed three redundant per-test Wayland patches. Added explicit X11/headless
  scroll fallback coverage and strengthened the drag test to prove subsequent
  native routing resumes after the fallback hold is released.
- Focused verification: all 17 mouse-forwarder/tracking-reload tests pass under
  simulated X11.
- Full-suite verification with Talon's CPython 3.13 and bytecode writes disabled:

  | Simulated host environment | Session variables | Result |
  | --- | --- | --- |
  | Wayland | `XDG_SESSION_TYPE=wayland`, `WAYLAND_DISPLAY=wayland-test`, `SWAYSOCK` unset | 153 passed |
  | X11 | `XDG_SESSION_TYPE=x11`, `WAYLAND_DISPLAY` and `SWAYSOCK` unset | 153 passed |
  | Headless | All three session variables unset | 153 passed |

  Each run used `-m unittest discover -s tests -v` with
  `$HOME/.local/opt/talon/resources/python/bin/python3` and `TMPDIR=/tmp/opencode`.
- Both configured Ruff commands were attempted; neither could run because Ruff
  is not installed.
- `git diff --check` passed.
- Changes are limited to test code and documentation. No live input or Talon
  restart was needed. Committed as `89ad55d`.

### 02 — Complete

- Starting revision: `89ad55d`.
- Replaced the three-action notification chain with
  `control1_state_changed(enabled)`, using Talon's concrete `actions.skip()`
  default. The overlay now implements that same hook and chains to the default
  before synchronizing its gaze subscription and marker.
- Removed the superseded started/stopped hooks and the unused state-event
  start/stop/running actions. Preserved menu interception, diagnostic
  `control1_state_emit_now()`, and current reload-resource handling.
- Added eight focused regressions using the existing overlay fakes and an
  explicitly wired notification chain. They cover the default without a consumer,
  both state transitions, repeated state, disabled overlays, menu installation
  without wrapper stacking, unchanged/missing menus, diagnostic emission, and
  toggle failure propagation. Six failed against the old notification chain.
- Verification with Talon's CPython 3.13 and bytecode writes disabled:
  - All 16 notification, overlay, and hiss tests passed.
  - All 161 tests passed with `-m unittest discover -s tests -v`.
  - Talon reloaded both production files without an import error.
  - A live `control1_state_emit_now()` diagnostic succeeded through Talon's real
    action dispatcher and reached overlay synchronization exactly once. Control
    Mouse and overlay enablement both remained false; no live toggle, click,
    movement, restart, or face-gesture change was requested.
  - Talon rejected profiler-based observation (`sys.setprofile`). A temporary
    wrapper verified the synchronization call instead and was restored in
    `finally`.
  - Both Ruff commands remain unavailable because Ruff is not installed.
  - `git diff --check` passed.
- Next task: **03 — Fix continuous-to-wheel scroll transitions**.

## Deferred backlog

These are outside this cleanup unless separately approved:

- Caps Lock behavior and the user's Hyprland customizations.
- General keyboard-state planning and implicit/shared-modifier ownership redesign.
- New mechanisms for discovering external keyboard layout state.
- Unverified callback leaks or changes to Talon's threading/scheduling model.
- Generic lifecycle, listener, transaction, or dependency-injection frameworks.
- Unrelated vendor build-tool improvements.

Add newly discovered unrelated issues here, not to the numbered work in progress.
