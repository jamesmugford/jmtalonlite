# Potential changes

Candidate improvements from the read-only review of the Talon 1.0 migration at
`0b5d003`. These are proposals, not implementation commitments or completed work.
Code references are relative to the repository root and identify the implementation
at the time of review.

The review covered the handwritten Wayland backend, Talon bridge, tracking modules,
Hyprland layer, vendor builder, launcher examples, and relevant tests. No new test
runs, live-input tests, or restarts were performed during the review. Static
correctness findings are distinguished from risks that still need reproduction.

## Direction

- Target Talon 1.0 and later with the current Python 3.14t ABI.
- Remove historical Talon/runtime compatibility machinery where it is no longer
  needed. Preserve current-generation reload safety and manual enabled state.
- Keep the single Wayland owner thread, connection mailbox, immutable snapshots,
  keyboard preflight, and identity-based press cleanup.
- Prefer local simplifications and a small gaze-specific ownership component over
  a general lifecycle framework.
- Preserve compositor capability fallback and the rule against replaying input
  after potentially partial emission.
- Profile before treating a performance hypothesis as the cause of the documented
  workspace-related eye-tracking latency.

## Highest-priority tracking changes

### 1. Define in-flight gaze retirement

**References:** `plugins/tracking_forwarder/gaze_dispatch.py`, especially `stop()`
and `_deliver()`; `tests/test_gaze_dispatch.py`.

`stop()` invalidates queued deliveries and cancels the pending job. It does not
finish or prevent a callback that has already passed its active-state check.
Consequently, a callback can continue after stop returns, including while a
replacement generation starts or overlay canvases are being closed.

Serialization currently applies within one dispatcher instance, not across old
and new instances during reload. This is a lifecycle gap visible in the code;
overlapping generations have not been demonstrated in normal live use.

**Proposal:**

- Define when stopping becomes complete and when dependent resources may retire.
- Prevent an old generation from issuing stale movement or using retired canvases.
- Coordinate replacement with in-flight delivery completion.
- Account for reentrant stop calls; an unconditional wait can introduce deadlocks.

**Verification:** deterministic tests that pause delivery before and during the
callback, then stop, reload, or replace it. The existing blocked-delivery test
checks non-overlap within one dispatcher but allows the old callback to finish
after stop/restart.

### 2. Separate enabled intent, subscription ownership, and delivery activity

**References:** `control1_pointer_forwarder.py` and `control1_gaze_logger.py` under
`plugins/tracking_forwarder/`, especially `_register_gaze()` and
`_unregister_gaze()`.

If unregister fails, the dispatcher has already stopped but `_registered` remains
true. A later start returns early because `_registered` is true, potentially
leaving a feature apparently enabled with inactive delivery.

**Proposal:** distinguish desired enabled state, owned subscription, active
delivery, and pending retirement. A retained cleanup handle must not be treated
as proof that the feature is operational.

**Verification:** failed unsubscribe followed by explicit start, successful retry,
repeated stop/start, and preservation of enabled intent across reload. Existing
tests retry failed stop through readiness handling but do not cover explicit
start immediately after a failed stop.

### 3. Consolidate gaze subscription ownership

**References:** the pointer forwarder, gaze logger, debug overlay, and
`gaze_dispatch.py` in `plugins/tracking_forwarder/`.

The three features repeat process-context registration, scheduled delivery,
retained callback identities, retirement, and cleanup retry handling.

**Proposal:** separate two responsibilities:

| Component | Responsibility |
| --- | --- |
| `GazeDispatch` | Talon-free coalescing, scheduling, cancellation, and delivery serialization |
| A small Talon gaze-subscription adapter | Process-owned registration, exact teardown, and retirement |

Keep feature behavior and autostart policy explicit in the feature modules. The
pointer forwards positions, the logger formats samples, and the overlay owns
canvases. Keep the shared component specific to gaze subscriptions.

Remove migration-only checks such as `hasattr(_retained_callback, "stop")` once
the retained-state transition is coordinated. Update tests to retain current
subscription objects rather than historical function-only callback records.

**Verification:** run the shared subscription contract against all three features,
including partial cleanup failures and replacement of the exact previous owner.

### 4. Clarify gaze coalescing while a callback is busy

**Reference:** `GazeDispatch.__call__()` in `gaze_dispatch.py`.

Events arriving while `_delivering` is true are discarded. A subsequent tracker
event normally triggers another delivery, but the final update in a burst can be
lost. This is not evidence that the dispatcher causes the workspace latency.

**Proposal:** evaluate a dirty/latest-update marker that permits at most one
follow-up delivery after the running callback finishes, without accumulating a
queue. Establish the intended contract before changing scheduling.

**Verification:** a burst that ends during a blocked callback, callback failure
followed by retry, bounded scheduling, and stop/restart invalidation. Measure
latency before claiming a responsiveness improvement.

## Talon integration boundaries

### 5. Isolate tracking internals and read coherent samples

**References:** tracking modules and
`plugins/wayland_runtime.py::move_pointer_on_main_screen()`.

Knowledge of `talon.plugins.eye_mouse`, `eye_mouse_2`, `tracking_system`, and
`talon.scripting.rctx.main` is scattered across the implementation.

**Proposal:** put Talon-specific tracking access behind a small adapter that
locates filtered samples, reads the eye-mouse screen, owns registration, and
manages the menu hook. Document the tested Talon 1.0 assumptions there.

Entering the `main` resource context changes resource ownership; it does not move
execution onto the operating-system main thread. Earlier live probes showed raw
gaze callbacks on workers and scheduled callbacks on `python:cron` threads. Define
and verify the supported Talon execution boundary instead of assuming
`cron.after()` alone guarantees physical main-thread execution.

The logger reads `xy_hist`, `delta_hist`, and `eye_hist` independently. Under
free-threaded processing these can potentially describe different updates. Use
the applicable Talon synchronization mechanism, once verified, to copy a coherent
immutable sample. This is a sample-consistency risk, not a reproduced live error.

**Verification:** cold startup before tracking objects exist, readiness, current
reload behavior, callback execution context, and coherent sample extraction.

### 6. Give the Control Mouse menu hook symmetric teardown

**Reference:** `plugins/tracking_forwarder/control1_state_events.py`.

The menu callback is replaced and its original stored in menu attributes, but
there is no corresponding restoration when the script retires. Removing or
disabling the script can leave its wrapper attached.

**Proposal:** retain the exact installed wrapper and original callback. Restore
the original only if the current callback is still owned by this module, following
the provider-ownership pattern in `WaylandScopes`.

**Verification:** repeated installation, restoration, reload without stacked
notifications, and another component replacing the callback after installation.

### 7. Serialize bridge publication with lifecycle transitions

**References:** `plugins/wayland_runtime.py::_apply_active_window()`, `stop()`, and
`plugins/wayland_scopes.py`.

The generation check is protected by the bridge lock, but `scopes.update()` runs
after that lock is released. A concurrent stop can occur between validation and
publication, allowing stale publication after teardown. The current stale-job test
covers a job starting after stop, not one already between validation and update.

**Proposal:** establish an execution boundary that serializes lifecycle transitions
and scope publication. Avoid holding locks across arbitrary Talon scope updates
without accounting for reentrancy. Audit compound fallback keyboard/button
ownership decisions under the same free-threaded execution assumptions.

**Verification:** stop interleaved with a validated publication, replacement
generations, capability loss/recovery, and scope-update failure handling. Preserve
the existing generation guards and provider restoration rules.

## Input correctness and cleanup

### 8. Preserve original modified-click errors in fallback

**References:** `plugins/mouse_forwarder.py::_fallback_modified_click()` and
`plugins/wayland_backend/desktop.py::modified_click()`.

The native transaction preserves the click error and attaches cleanup failures as
notes. The fallback uses plain `finally`, so modifier-release failure becomes the
headline exception if the click also fails.

**Proposal:** apply the same first-error policy to both paths: always attempt
release, keep the click error primary, and attach any release failure as a note.

**Verification:** click and release both failing in the native-token fallback and
ordinary key-down/key-up fallback paths. Preserve release ordering.

### 9. Narrow replay-safe fallback exceptions

**Reference:** mouse click, drag, release, and toggle overrides in
`plugins/mouse_forwarder.py`.

These overrides catch generic `ValueError` to support unsupported mouse buttons.
If a `ValueError` originates after partial native emission, the operation would be
replayed through fallback. PyWayland also uses `ValueError`, so the exception type
does not uniquely identify unsupported-button preflight. This is a static
error-classification risk, not an observed live replay.

**Proposal:** validate unsupported buttons before emission or use a narrowly
identified preflight exception. Unexpected post-emission failures must propagate.

Availability helpers also catch every exception and return false. Distinguish
expected initialization/capability conditions from broken action implementations
so unexpected failures remain diagnosable.

**Verification:** unsupported-button fallback, genuine capability loss, unexpected
availability failures, and `ValueError` after a recorded partial emission with no
fallback replay.

### 10. Validate coordinates before normalization

**References:** `plugins/wayland_backend/geometry.py` and mouse/screen movement
adaptation in `mouse_forwarder.py` and `wayland_runtime.py`.

Clamping precedes low-level finite-number validation. NaN or infinity can become
ordinary boundary coordinates before the pointer adapter sees them. Missing
screen geometry also produces a synthetic 1x1 rectangle, which can turn real
pixel positions into extreme normalized coordinates.

**Proposal:** reject invalid numerical inputs before normalization, preserve
clamping for valid finite out-of-bounds positions, and treat missing usable screen
geometry as unavailable at the Talon boundary. Keep protocol wire-range checks in
the pointer adapter.

**Verification:** NaN, infinity, invalid dimensions, absent screens, valid boundary
clamping, offset displays, and multi-monitor normalization.

### 11. Make unsubscribe handles independently idempotent

**References:** subscription methods in `seats.py`, `outputs.py`, and `windows.py`
under `plugins/wayland_backend/`.

Subscribing the same callable twice returns two handles, but calling the first
handle twice can remove both subscriptions. Each closure removes a matching
callback rather than retiring only its own subscription.

**Proposal:** give each subscription handle its own identity or active flag.
Preserve the different listener-error policies for seat/output consumers and
window consumers.

**Verification:** duplicate callable subscriptions, repeated unsubscribe on one
handle, independent retirement of the other handle, and callback failure during
notification.

### 12. Reuse ordered cleanup and validate before allocation

**References:** pointer/keyboard destruction, bridge teardown, overlay reload and
canvas cleanup, and `plugins/wayland_backend/xkb.py::XkbKeymap.__init__()`.

Several paths manually duplicate exception collection, note attachment, and
release/destroy nesting despite the existing `run_cleanup_steps()` helper.

**Proposal:** reuse ordered cleanup where policies match. Keep successful-step
ownership updates, retryable failures, and unconditional finalization explicit.

`XkbKeymap.__init__()` allocates a native context before validating lock/group
arguments and before entering its rollback block. Invalid arguments can therefore
leave the context allocated. Validate Python arguments before native allocation
or include the whole allocation sequence in rollback handling.

**Verification:** all applicable cleanup steps run after individual failures,
first errors remain primary, retryable resources remain owned, input releases keep
their ordering, and invalid XKB arguments allocate no resources.

## Vendoring and configuration

### 13. Simplify the single-target bundle specification

**References:** `plugins/wayland_backend/vendor.py`, `third_party/manifest.json`,
`tools/build_pywayland_vendor.py`, and vendor tests.

The runtime has one `_BUNDLES` entry, the manifest has one `targets` entry, and the
builder/tests retain multi-target selection loops. Runtime and build requirements
are also repeated.

**Proposal:** use one explicit bundle specification containing SOABI, directory,
build CFFI version, platform, and glibc requirements. Keep exact ABI validation.
Introduce target selection only when another target is actually supported.

The pinned Python 3.13 wheel is a source of Python files and ABI-independent
private libraries; its extension is replaced with the 3.14t build. It is a valid
build input, not evidence of supported Python 3.13 runtime compatibility.

**Verification:** supported ABI selection, rejection of incompatible ABIs,
provenance, generated protocol hashes, ELF dependencies, and native callbacks.

### 14. Publish rebuilt bundles recoverably and normalize CLI paths early

**Reference:** `tools/build_pywayland_vendor.py`.

The builder removes the destination and copies the new bundle afterward. An
interrupted copy can leave an incomplete destination.

**Proposal:** validate a staged bundle and publish it using a recoverable directory
replacement. Also call `expanduser()` before `is_file()` so a quoted
`--python '~/.talon/bin/python'` is accepted.

**Verification:** publication failure preserves a usable prior bundle, successful
publication has the expected provenance, and CLI path normalization is correct.

### 15. Align session detection with the explicit socket override

**References:** `plugins/wayland_backend/session.py`, `connection.py`, and launcher
examples.

The connection understands `JMTALONLITE_WAYLAND_DISPLAY`, but session detection
does not. If the override is the available signal and standard session variables
are absent, the connection is configured but the Talon bridge declines to start.

**Proposal:** define one consistent policy for the explicit override. Keep socket
selection explicit rather than discovering arbitrary sockets.

**Verification:** normal Wayland launch, the XWayland UI workaround, explicit
socket paths, and X11/headless environments.

## Tests and smaller elegance changes

### 16. Consolidate focused test scaffolding

- Reuse shared `FakeCron` in the bridge tests.
- Share the tracking fake that models resource-context filtering.
- Use `isolated_tracking_state()` consistently instead of repeated retained-state
  save/delete/restore blocks.
- Standardize test imports to avoid loading the same backend under both
  `plugins.*` and top-level `wayland_backend.*` names.
- Keep the bridge's stronger Talon action-signature checks distinct from the basic
  declaration fake; they test a different contract.
- Improve the alias assertion where `"app.name: chromium"` also matches the
  `chromium-browser` declaration as a substring. Verify independent declarations
  or meaningful matching behavior instead.
- Add a mailbox regression proving that a claimed command exceeding its queue
  timeout still reports its eventual result without replay.

Keep `unittest` and the existing lightweight native smoke tests. Prioritize
observable lifecycle and failure behavior over tests that repeat implementation
text or private field assignments.

### 17. Low-risk local cleanup

| Area | Candidate change |
| --- | --- |
| `pyproject.toml` | Align Ruff's target with Python 3.14 |
| Tracking state | Give lifecycle-bearing retained state named fields |
| Screen adaptation | Extract Talon screen to `OutputTarget` conversion |
| Scroll forwarding | Extract duplicated per-axis calculation without changing transaction/remainder semantics |
| Pop and touch | Reuse the existing drag-end-or-click action |
| Hyprland annotations | Replace `Union[str, int]` with `str \| int` |
| Longer Hyprland Lua snippets | Use readable multiline code |
| Hyprland subprocess handling | Catch expected subprocess/OS exceptions rather than every exception |
| README installation guidance | Replace the historical reload-migration restart instruction with current guidance |

The small pure parsing, geometry, scrolling, and selection functions are already
useful seams. The review does not justify replacing them with a broader framework.

## Suggested implementation batches

1. **Gaze retirement and failure recovery:** candidates 1, 2, and the scheduling
   contract in 4.
2. **Shared gaze ownership:** candidate 3, including retirement of historical
   callback shapes.
3. **Talon tracking boundary:** candidates 5 and 6.
4. **Input error boundaries:** candidates 8 and 9.
5. **Validation and allocation:** candidate 10 and the XKB constructor fix in 12.
6. **Subscription handles:** candidate 11.
7. **Bridge concurrency:** candidate 7.
8. **Vendor build and publication:** candidates 13 and 14.
9. **Session configuration:** candidate 15.
10. **Test/helper and documentation cleanup:** candidates 16, 17, and remaining
    cleanup reuse from 12.

Start with batches 1-4: they offer the strongest combination of correctness and
simplification. Track accepted work as concise tasks in `docs/tasks.md` rather
than maintaining an execution diary here.

Before implementation, incorporate the upstream README wording edit recorded in
`origin/main` as `2d7e947`; the reviewed checkout was one commit behind it.

## Verification strategy

- Establish a focused regression before each substantive behavior change.
- Use deterministic events/barriers for threading tests rather than timing-based
  sleeps.
- Run the relevant tests, then broaden verification when new changes or concerns
  warrant it.
- Run the full supported-interpreter suite at integration boundaries:

  ```sh
  PYTHONDONTWRITEBYTECODE=1 "$HOME/.talon/bin/python" -m unittest discover -s tests -v
  ```

- Run configured Ruff checks when available, including `core` when relevant.
- Coordinate cold-start and normal-use tracking checks after lifecycle changes.
- Keep live input tests separate from pure/native callback tests and explicitly
  coordinate them with the user.
- Treat the documented HUD/Settings workspace latency as a separate observation;
  none of these proposals establishes its cause or guarantees its resolution.
