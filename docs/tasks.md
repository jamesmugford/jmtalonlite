# Tasks

## Completed

- [x] Make the test baseline independent of the host desktop session.
- [x] Replace the Control Mouse notification chain with one state-change action.
- [x] Correct continuous-to-wheel scroll source transitions.
- [x] Keep fallback drag release with the backend that owns the drag.
- [x] Normalize keyboard aliases and validate keycodes before emitting input.
- [x] Prevent stale temporary-modifier cleanup from releasing newer key holds.
- [x] Remove redundant command and action routes.
- [x] Remove historical reload migrations while preserving current reload safety.
- [x] Separate Talon scope management from Wayland lifecycle management.
- [x] Clarify naming, consolidate shared test helpers, and document requirements.
- [x] Preserve raw Wayland app IDs and map verified IDs to Community contexts.
- [x] Remove universal tab overrides in favor of normal app-specific dispatch.

## Planned

- [ ] Support remaining built-in Talon eye-tracking modes.
- [ ] Support physical keyboard input so Talon can listen for hotkeys under
  Wayland.
