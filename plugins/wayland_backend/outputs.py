"""Wayland output discovery, matching, and immutable status values."""

from __future__ import annotations

import threading
from collections.abc import Callable, Iterable
from dataclasses import dataclass
from typing import Any

from .connection import WaylandConnection, run_cleanup_steps

_CURRENT_MODE = 1
_PHYSICAL_SIZE_TOLERANCE_MM = 5.0


@dataclass(frozen=True, slots=True)
class OutputTarget:
    """Immutable display identity copied from Talon's main screen."""

    name: str
    make: str
    model: str
    physical_width: float
    physical_height: float
    mode_width: int
    mode_height: int
    refresh_millihz: int


@dataclass(frozen=True, slots=True)
class OutputSnapshot:
    """Public immutable state for one advertised Wayland output."""

    id: int
    name: str
    description: str
    make: str
    model: str
    physical_width: int
    physical_height: int
    mode_width: int
    mode_height: int
    refresh_millihz: int
    scale: int
    transform: int


@dataclass(frozen=True, slots=True)
class SelectedOutput:
    """An owner-thread borrow of a matched wl_output and immutable metadata."""

    id: int
    version: int
    proxy: Any
    snapshot: OutputSnapshot


@dataclass(slots=True)
class _Output:
    """Mutable wl_output event state accumulated until a done event."""

    id: int
    version: int
    proxy: Any
    name: str = ""
    description: str = ""
    make: str = ""
    model: str = ""
    physical_width: int = 0
    physical_height: int = 0
    mode_width: int = 0
    mode_height: int = 0
    refresh_millihz: int = 0
    scale: int = 1
    transform: int = 0

    def snapshot(self) -> OutputSnapshot:
        """Return the currently accumulated immutable output state."""
        return OutputSnapshot(
            id=self.id,
            name=self.name,
            description=self.description,
            make=self.make,
            model=self.model,
            physical_width=self.physical_width,
            physical_height=self.physical_height,
            mode_width=self.mode_width,
            mode_height=self.mode_height,
            refresh_millihz=self.refresh_millihz,
            scale=self.scale,
            transform=self.transform,
        )


def _normalized_text(value: str) -> str:
    """Return case-insensitive display metadata without surrounding space."""
    return value.strip().casefold()


def _dimensions_match(
    first_width: float,
    first_height: float,
    second_width: float,
    second_height: float,
    *,
    tolerance: float = 0.0,
) -> bool:
    """Match dimensions directly or with axes swapped for rotated outputs."""
    if min(first_width, first_height, second_width, second_height) <= 0:
        return False

    def close(left: float, right: float) -> bool:
        return abs(left - right) <= tolerance

    return (
        close(first_width, second_width) and close(first_height, second_height)
    ) or (close(first_width, second_height) and close(first_height, second_width))


def choose_output(
    target: OutputTarget,
    outputs: Iterable[OutputSnapshot],
) -> int | None:
    """Return one unambiguous output ID matching a Talon screen target."""
    values = tuple(outputs)
    if target.name:
        matches = [output for output in values if output.name == target.name]
        if len(matches) == 1:
            return matches[0].id

    target_make = _normalized_text(target.make)
    target_model = _normalized_text(target.model)
    if target_make and target_model:
        matches = [
            output
            for output in values
            if _normalized_text(output.make) == target_make
            and _normalized_text(output.model) == target_model
            and _dimensions_match(
                target.physical_width,
                target.physical_height,
                output.physical_width,
                output.physical_height,
                tolerance=_PHYSICAL_SIZE_TOLERANCE_MM,
            )
        ]
        if len(matches) == 1:
            return matches[0].id

    matches = [
        output
        for output in values
        if _dimensions_match(
            target.physical_width,
            target.physical_height,
            output.physical_width,
            output.physical_height,
            tolerance=_PHYSICAL_SIZE_TOLERANCE_MM,
        )
        and _dimensions_match(
            target.mode_width,
            target.mode_height,
            output.mode_width,
            output.mode_height,
        )
        and (
            target.refresh_millihz <= 0
            or output.refresh_millihz <= 0
            or abs(target.refresh_millihz - output.refresh_millihz) <= 1000
        )
    ]
    if len(matches) == 1:
        return matches[0].id
    if len(values) == 1 and _single_output_compatible(target, values[0]):
        return values[0].id
    return None


def _single_output_compatible(
    target: OutputTarget,
    output: OutputSnapshot,
) -> bool:
    """Allow a sole-output fallback only when known metadata does not conflict."""
    if target.name and output.name and target.name != output.name:
        return False
    target_make = _normalized_text(target.make)
    output_make = _normalized_text(output.make)
    if target_make and output_make and target_make != output_make:
        return False
    target_model = _normalized_text(target.model)
    output_model = _normalized_text(output.model)
    if target_model and output_model and target_model != output_model:
        return False
    if (
        min(target.physical_width, target.physical_height) > 0
        and min(output.physical_width, output.physical_height) > 0
        and not _dimensions_match(
            target.physical_width,
            target.physical_height,
            output.physical_width,
            output.physical_height,
            tolerance=_PHYSICAL_SIZE_TOLERANCE_MM,
        )
    ):
        return False
    if (
        min(target.mode_width, target.mode_height) > 0
        and min(output.mode_width, output.mode_height) > 0
        and not _dimensions_match(
            target.mode_width,
            target.mode_height,
            output.mode_width,
            output.mode_height,
        )
    ):
        return False
    return True


class OutputRegistry:
    """Own every wl_output and publish committed output changes."""

    interface_name = "wl_output"
    multiple = True

    def __init__(self, connection: WaylandConnection) -> None:
        """Create an empty output registry attached to a connection."""
        self._connection = connection
        self._lock = threading.Lock()
        self._listener_lock = threading.Lock()
        self._outputs: dict[int, _Output] = {}
        self._snapshots: dict[int, OutputSnapshot] = {}
        self._listeners: list[Callable[[], None]] = []

    def bind(
        self, registry: Any, global_id: int, version: int, interface: type
    ) -> int:
        """Bind one wl_output and subscribe to its metadata events."""
        negotiated = min(version, interface.version)
        proxy = registry.bind(global_id, interface, negotiated)
        output = _Output(global_id, negotiated, proxy)
        with self._lock:
            self._outputs[global_id] = output
        proxy.dispatcher["geometry"] = self._connection.guard(
            lambda _proxy, x, y, width, height, subpixel, make, model, transform: (
                self._set_geometry(global_id, width, height, make, model, transform)
            )
        )
        proxy.dispatcher["mode"] = self._connection.guard(
            lambda _proxy, flags, width, height, refresh: self._set_mode(
                global_id, flags, width, height, refresh
            )
        )
        proxy.dispatcher["done"] = self._connection.guard(
            lambda _proxy: self._commit(global_id)
        )
        proxy.dispatcher["scale"] = self._connection.guard(
            lambda _proxy, scale: self._set_scale(global_id, scale)
        )
        proxy.dispatcher["name"] = self._connection.guard(
            lambda _proxy, value: self._set_name(global_id, value)
        )
        proxy.dispatcher["description"] = self._connection.guard(
            lambda _proxy, value: self._set_description(global_id, value)
        )
        return negotiated

    def remove(self, global_id: int) -> None:
        """Publish output removal before releasing its proxy."""
        with self._lock:
            output = self._outputs.pop(global_id, None)
            had_snapshot = self._snapshots.pop(global_id, None) is not None
        if output is None:
            return
        steps = []
        if had_snapshot:
            steps.append(("output listeners", self._notify))
        steps.append((f"output {output.id}", lambda: self._release(output)))
        run_cleanup_steps(steps)

    def ready(self) -> None:
        """Complete initialization without additional protocol requests."""

    def close(self) -> None:
        """Release all outputs after publishing an empty snapshot set."""
        with self._lock:
            outputs = tuple(self._outputs.values())
            self._outputs.clear()
            had_snapshots = bool(self._snapshots)
            self._snapshots.clear()
        steps = []
        if had_snapshots:
            steps.append(("output listeners", self._notify))
        steps.extend(
            (f"output {output.id}", lambda output=output: self._release(output))
            for output in outputs
        )
        run_cleanup_steps(steps)

    def subscribe(self, callback: Callable[[], None]) -> Callable[[], None]:
        """Invoke a listener on the owner thread; return an idempotent unsubscribe."""
        with self._listener_lock:
            self._listeners.append(callback)

        def unsubscribe() -> None:
            """Remove this callback if it remains subscribed."""
            with self._listener_lock:
                if callback in self._listeners:
                    self._listeners.remove(callback)

        return unsubscribe

    def match(self, target: OutputTarget) -> SelectedOutput | None:
        """Borrow the unambiguously matched output for owner-thread use."""
        with self._lock:
            output_id = choose_output(target, self._snapshots.values())
            output = self._outputs.get(output_id)
            snapshot = self._snapshots.get(output_id)
            if output is None or snapshot is None:
                return None
            return SelectedOutput(output.id, output.version, output.proxy, snapshot)

    def snapshots(self) -> tuple[OutputSnapshot, ...]:
        """Return committed output snapshots ordered by registry ID."""
        with self._lock:
            return tuple(self._snapshots[key] for key in sorted(self._snapshots))

    def _set_geometry(
        self,
        output_id: int,
        physical_width: int,
        physical_height: int,
        make: str,
        model: str,
        transform: int,
    ) -> None:
        """Apply one output geometry event to pending state."""
        with self._lock:
            output = self._outputs.get(output_id)
            if output is None:
                return
            output.physical_width = physical_width
            output.physical_height = physical_height
            output.make = make
            output.model = model
            output.transform = transform
        self._commit_legacy(output_id)

    def _set_mode(
        self,
        output_id: int,
        flags: int,
        width: int,
        height: int,
        refresh_millihz: int,
    ) -> None:
        """Apply a current-mode event to pending state."""
        if not flags & _CURRENT_MODE:
            return
        with self._lock:
            output = self._outputs.get(output_id)
            if output is None:
                return
            output.mode_width = width
            output.mode_height = height
            output.refresh_millihz = refresh_millihz
        self._commit_legacy(output_id)

    def _set_scale(self, output_id: int, scale: int) -> None:
        """Apply one output scale event to pending state."""
        with self._lock:
            output = self._outputs.get(output_id)
            if output is None:
                return
            output.scale = scale
        self._commit_legacy(output_id)

    def _set_name(self, output_id: int, name: str) -> None:
        """Apply one stable output-name event to pending state."""
        with self._lock:
            output = self._outputs.get(output_id)
            if output is None:
                return
            output.name = name
        self._commit_legacy(output_id)

    def _set_description(self, output_id: int, description: str) -> None:
        """Apply one output-description event to pending state."""
        with self._lock:
            output = self._outputs.get(output_id)
            if output is None:
                return
            output.description = description
        self._commit_legacy(output_id)

    def _commit_legacy(self, output_id: int) -> None:
        """Publish each event for wl_output v1, which has no done event."""
        with self._lock:
            output = self._outputs.get(output_id)
            legacy = output is not None and output.version < 2
        if legacy:
            self._commit(output_id)

    def _commit(self, output_id: int) -> None:
        """Atomically publish all metadata accumulated for one output."""
        with self._lock:
            output = self._outputs.get(output_id)
            if output is None:
                return
            snapshot = output.snapshot()
            changed = snapshot != self._snapshots.get(output_id)
            self._snapshots[output_id] = snapshot
        if changed:
            self._notify()

    def _notify(self) -> None:
        """Invoke every listener outside locks and preserve their failures."""
        with self._listener_lock:
            listeners = tuple(self._listeners)
        run_cleanup_steps(
            (f"output listener {index}", listener)
            for index, listener in enumerate(listeners, start=1)
        )

    @staticmethod
    def _release(output: _Output) -> None:
        """Release an output with the request supported by its version."""
        if output.proxy.destroyed:
            return
        if output.version >= 3:
            output.proxy.release()
        else:
            output.proxy._destroy()
