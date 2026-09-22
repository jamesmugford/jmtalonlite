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

- [x] **03 — Fix continuous-to-wheel scroll transitions**
  - Establish the wheel source after addressing each discrete axis.
  - Preserve scaling, direction, continuous scrolling, and frame grouping.
  - Main files: `plugins/wayland_backend/pointer.py`,
    `tests/test_wayland_pointer.py`.
  - **Done when:** continuous scrolling followed by vertical, horizontal, or
    combined wheel scrolling emits the correct source on every axis. A failure
    after emission does not cause replay.

- [x] **04 — Fix fallback drag bookkeeping**
  - Make standard drag/release actions the authoritative bookkeeping boundary.
  - Correct duplicate updates in the Community-shaped toggle chain while
    preserving native toggle and drag-end behavior.
  - Main files: `plugins/mouse_forwarder.py`, `tests/test_mouse_forwarder.py`.
  - **Done when:** fallback drag start/end updates ownership exactly once, and
    release stays with the correct backend after capability changes. Cover
    nested dispatch and failure propagation.

- [x] **05 — Tighten the keyboard input boundary**
  - Normalize documented named aliases, including `win`/`super` and
    `return`/`enter`, consistently with fallback hold tracking.
  - Validate all resolved keycodes, including modifiers, before any emission.
    Preserve literal-character case and existing layout/remapping semantics.
  - Main files: `plugins/wayland_backend/key_spec.py`,
    `plugins/wayland_backend/keyboard.py`, `plugins/wayland_runtime.py`.
  - **Done when:** equivalent named keys release the same tracked hold, and an
    invalid keycode cannot leave a partially emitted chord.

- [x] **06 — Make temporary modifier cleanup lifetime-safe**
  - Associate release records with the actual presses they introduced.
  - Invalidate stale records after keyboard/keymap replacement or
    release-and-repress; keep repeated cleanup harmless.
  - Preserve the existing introduced-press cleanup contract, rather than adding
    independently owned, reference-counted modifier scopes.
  - Main files: `plugins/wayland_backend/keyboard.py`,
    `plugins/wayland_backend/desktop.py`, `plugins/wayland_runtime.py`.
  - **Done when:** old cleanup cannot release a newer hold, preheld modifiers
    remain protected, and repeated cleanup does nothing.

- [x] **07 — Remove redundant command and action routes**
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

- [x] **08 — Simplify historical reload machinery**
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

- [x] **09 — Clarify the runtime's responsibilities**
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
- Committed as `82a8076`.

### 03 — Complete

- Starting revision: `82a8076`.
- Discrete scrolling now sets the wheel source immediately after addressing each
  emitted axis. Both axes still share one wheel frame and one owner-thread
  operation; scaling, signs, and continuous-scroll framing are unchanged.
- Added a narrow test interpreter for the persistent per-axis source behavior in
  Hyprland 0.56.2's `VirtualPointer.cpp`. A nine-case transition matrix covers
  vertical, horizontal, and combined continuous scrolling, then wheel scrolling,
  then continuous scrolling again. Before the fix, the combined-continuous to
  vertical/combined-wheel cases retained the wrong vertical source.
- Added checks that invalid second-axis values emit nothing, source failure
  stops the transaction after the first axis, and the Talon forwarder does not
  replay a failed discrete scroll through fallback.
- Verification with Talon's CPython 3.13 and bytecode writes disabled:
  - All 32 pointer and mouse-forwarder tests passed.
  - All 165 tests passed with `-m unittest discover -s tests`.
  - Talon reloaded the adapter and bridge. Its existing user-thread warning was
    logged, with no import/startup error.
  - A read-only status check inside Talon confirmed a running connection,
    available native pointer, and no backend error. No live scrolling was
    injected; compositor/client visual behavior was not retested.
  - Both Ruff commands remain unavailable because Ruff is not installed.
  - `git diff --check` passed.
- Changes remained uncommitted when piece 04 began.

### 04 — Complete

- Starting revision: `82a8076`, with the completed piece 03 changes in the
  working tree.
- Removed the two outer fallback-state inversions in `mouse_drag_toggle()`.
  Standard delegated toggles call main drag/release actions, which already record
  the actual owner. Native toggle and drag-end behavior are unchanged.
- Added four focused checks using a Community-shaped nested action chain:
  fallback press/release across capability recovery, native capability appearing
  during delegation, capability loss at native preflight, and propagation of an
  unexpected native failure without fallback replay. Three reproduced incorrect
  ownership before the fix.
- Verification with Talon's CPython 3.13 and bytecode writes disabled:
  - All 14 mouse-forwarder tests passed.
  - All 169 tests passed with `-m unittest discover -s tests`.
  - Talon reloaded `mouse_forwarder.py` without an import error. No live clicks,
    drags, restart, or enabled-state changes were requested.
  - Both Ruff commands remain unavailable because Ruff is not installed.
  - `git diff --check` passed.
- Pieces 03 and 04 were committed together as `73985a2`.

### 05 — Complete

- Starting revision: `73985a2`.
- Added one parser helper for canonical named keys, with three explicit aliases:
  `win` to `super`, `return` to `enter`, and `escape` to `esc`. Named-key case is
  normalized; literal-character case and punctuation are preserved. Modifier
  aliases are recognized before splitting off the main key and are deduplicated.
- Fallback bookkeeping now uses the parser's canonical key name directly instead
  of independently case-folding it. Paired alias releases clear the tracked hold
  and allow subsequent native forwarding to resume; fallback receives the
  original Talon arguments.
- Main and modifier keycodes are validated during resolution, before planning
  and emitting any events. Rejected requests leave preheld keys, XKB state, and
  connection availability intact, including when a valid token precedes the bad
  one or an invalid release would otherwise produce no transition.
- Added ten tests across parsing, the keyboard adapter, and the Talon bridge.
  They failed against the old code and pass with the boundary fixes.
- Verification with Talon's CPython 3.13 and bytecode writes disabled:
  - All 47 focused parser, keyboard, and bridge tests passed.
  - All 179 tests passed with `-m unittest discover -s tests`.
  - A read-only check inside Talon confirmed the reloaded bridge has an available
    keyboard and pointer, no backend error, and the corrected alias parser.
    No live key presses were injected.
  - Both Ruff commands remain unavailable because Ruff is not installed.
  - `git diff --check` passed.
- The spoken modifier vocabulary and Hyprland configuration were not changed.
  General keyboard-state planning remains deferred.
- Committed as `e66bc65`.

### 06 — Complete

- Starting revision: `e66bc65`.
- Added immutable, identity-based `KeyPress` records. An ordered map of current
  presses replaces the held-key list, preserving reverse-order release without
  a second state collection or per-key generation counters.
- Temporary operations return only the presses they introduce. Cleanup checks
  each record against the current press on the owner thread, skipping expired
  records while still releasing valid parts of a partially stale chord.
- Existing key-up and teardown paths retire records, so cleanup cannot cross a
  release/repress, keymap replacement, or keyboard recreation. Repeated down and
  preheld-modifier behavior retain the introduced-press contract; no independent
  reference-counted modifier scopes were added.
- Talon's integer handles now use a process-retained allocator so an old handle
  cannot name a new hold after a bridge reload. Initial adoption continues the
  preceding bridge's counter. Public action signatures are unchanged.
- Reused `run_cleanup_steps()` to attempt all applicable releases after an
  individual failure while preserving the first useful error.
- Added eight regressions covering stale presses, partial staleness, unchanged
  keymaps, repeated down/cleanup, release failures, and token reuse after reload.
  Six failed against the preceding implementation.
- Verification with Talon's CPython 3.13 and bytecode writes disabled:
  - All 53 focused keyboard, facade, and bridge tests passed.
  - All 187 tests passed with `-m unittest discover -s tests`.
  - The facade/bridge subset passed again (26 tests) after removing an unnecessary
    concrete-type dependency from mocked facade tests.
  - Talon initially reported an import-order error for the new record type;
    dependency reload recovered. A cached test-package type import was replaced
    with opaque test values, and that test module then reloaded successfully.
  - A read-only check inside Talon confirmed available keyboard/pointer output,
    no backend error, and the retained handle allocator. No live keys or clicks
    were injected and no restart or enabled-state changes were requested.
  - Both Ruff commands remain unavailable because Ruff is not installed.
  - `git diff --check` passed.
- Committed as `2cf3d88`.

### 07 — Complete

- Starting revision: `2cf3d88`.
- Removed four custom directional scroll actions, four duplicate directional
  overrides, their declarations, and the two one-axis forwarding helpers. The
  sixteen wheel command forms now call the standard directional actions, which
  reach the shared `mouse_scroll` boundary through Community's defaults or a
  configuration's equivalent implementation.
- Replaced the three calls to the trivial key-chord action with direct `key(...)`
  calls and removed that wrapper. The spoken modifier vocabulary is unchanged.
- Removed the unused modifier-up plan: `modifier_chord()` now returns only down
  strokes, while release continues to use the actual press identities from
  piece 06. Removed the keyboard module's unused export list and imports used
  only for that list.
- Clarified the command-layer dependencies and normal-dispatch behavior in the
  README and corrected the modifier grammar's capture-dependency comment.
- Net reduction: 139 lines of production Python, without adding a replacement
  forwarding abstraction.
- Verification with Talon's CPython 3.13 and bytecode writes disabled:
  - Two shared-boundary tests passed before and after deletion: normal wheel
    units in all four directions and accumulation of tiny wheel amounts.
  - All 59 focused mouse, key-spec, keyboard, and facade tests passed.
  - All 189 tests passed with `-m unittest discover -s tests`.
  - Talon reloaded the Python and grammar files without import or parse errors.
    The existing owner-thread startup warning was logged during backend reload.
  - A read-only live registry check confirmed standard directional defaults are
    available, local directional overrides and removed aliases are absent, and
    the modifier helper has its new return shape. Native keyboard and pointer
    output were available with no backend error. No live input was injected.
  - Ruff checks including `core` were attempted; Ruff remains unavailable.
  - `git diff --check` passed and no production references to the removed
    actions or one-axis helpers remain.
- Changes remained uncommitted when piece 08 began.

### 08 — Complete

- Starting revision: `2cf3d88`, with completed piece 07 changes in the working tree.
- With permission, recorded the current feature choices, stopped Talon before
  production edits, and relaunched it after tests passed. The recorded choices
  matched after restart: pointer forwarding, hiss mouse, and speech enabled;
  gaze logging, overlay, Control Mouse, and face scrolling disabled. Settings and
  both face-gesture repositories were left unchanged.
- Removed retired runtime/scope/job migrations, old-global recovery, alternate
  overlay state formats, historical menu-wrapper recovery, and the initial
  modifier-token counter migration. Current bridge replacement, handle allocation,
  fallback ownership, and the current retained state formats remain supported.
- Replaced the sixteen-attempt gaze-unregister loops with exact callback
  retirement and guarded registration. Successful retirement forgets the inactive
  callback; failure retains its handle for retry. Retirement still occurs before
  dependent imports, so a failed replacement import does not leave old resources
  active after successful cleanup.
- Startup now distinguishes unresolved intent from explicit enabled/disabled
  choices. Pointer/logger readiness resolves defaults once and reconciles
  ownership, including retrying failed starts/stops. Hiss startup records the
  disabled default without turning off independently enabled Control Mouse.
- Removed five migration-only tests and their legacy-global injection scaffolding.
  Retained current reload and cleanup tests, and added coverage for startup
  ordering, exact-once registration, explicit decisions before ready, cleanup
  retries, failed imports, and retained overlay resources.
- Verification with Talon's CPython 3.13 and bytecode writes disabled:
  - New startup tests reproduced the old behavior before production changes.
  - A final failure-path check caught and corrected a readiness-retry gap before
    completion.
  - All 196 tests passed with `-m unittest discover -s tests -q`.
  - Cold startup loaded the updated modules successfully; the existing Community
    deprecation and Wayland owner-thread warnings were reported.
  - Live checks confirmed matching restart choices, available native keyboard and
    pointer output, no backend error, and correct callback ownership after the
    final tracking-script reload. No synthetic keys, clicks, or scrolling were
    injected.
  - Both Ruff checks remain unavailable because Ruff is not installed.
  - `git diff --check` passed. Removed migration names and repeated unregister
    loops have no remaining production references.
- Pieces 07 and 08 were committed together as `71817b3`.

### 09 — Complete

- Starting revision: `71817b3`.
- Extracted `WaylandScopes` into `plugins/wayland_scopes.py`. It owns provider
  identities, installation/restoration, pending restoration updates, published
  window values, and application-alias matching. Construction does not capture or
  change Talon scopes; initialization occurs after the preceding bridge retires.
- The bridge retains desktop lifecycle, subscription ownership, job scheduling,
  coalescing, delayed clears, cancellation, and generation/revision checks. Its
  declaration callback delegates alias refresh to the scope component. App/window
  actions now read that component directly rather than adding bridge wrappers.
- Updated the repository layout documentation. The new component remains outside
  the Talon-free backend and has no independent startup hooks or worker thread.
- Added six behavior tests before extraction, all passing on the previous code:
  latest-window coalescing, delayed-clear replacement, alias refresh, capability
  loss/recovery, deferred provider capture during replacement, and respecting
  another owner's provider. Existing restoration-failure retries remain covered.
- Verification with Talon's CPython 3.13 and bytecode writes disabled:
  - All 24 bridge tests passed before and after extraction.
  - All 202 tests passed with `-m unittest discover -s tests -q`.
  - Talon reloaded the new component and runtime without an import error; the
    existing owner-thread startup warning was logged.
  - A read-only live check confirmed that the installed app/window providers are
    owned by the new component, publish valid values, and coexist with available
    native keyboard/pointer output and no backend error. No live input was
    injected and no full Talon restart was needed.
  - Ruff remains unavailable. `git diff --check` passed.
- Next task: **10 — Finish naming, local duplication, and documentation**.

## Deferred backlog

These are outside this cleanup unless separately approved:

- Caps Lock behavior and the user's Hyprland customizations.
- General keyboard-state planning and implicit/shared-modifier ownership redesign.
- New mechanisms for discovering external keyboard layout state.
- Unverified callback leaks or changes to Talon's threading/scheduling model.
- Generic lifecycle, listener, transaction, or dependency-injection frameworks.
- Unrelated vendor build-tool improvements.

Add newly discovered unrelated issues here, not to the numbered work in progress.
