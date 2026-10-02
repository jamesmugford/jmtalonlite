"""Coalesce raw gaze events into serialized Talon-scheduled deliveries."""

import threading
from collections.abc import Callable
from typing import Any


class GazeDispatch:
    """Own one cancellable, serialized delivery for a gaze subscription."""

    def __init__(self, callback: Callable[[], None], cron: Any) -> None:
        self._callback = callback
        self._cron = cron
        self._lock = threading.Lock()
        self._active = False
        self._delivering = False
        self._pending: object | None = None
        self._job: Any = None

    def start(self) -> None:
        with self._lock:
            self._active = True

    def stop(self) -> None:
        with self._lock:
            self._active = False
            self._pending = None
            job, self._job = self._job, None
        if job is not None:
            self._cron.cancel(job)

    def __call__(self, *_args: Any) -> None:
        """Called by raw tracking workers; never access Talon state here."""
        with self._lock:
            if not self._active or self._delivering or self._pending is not None:
                return
            token = object()
            self._pending = token
        try:
            job = self._cron.after("0ms", lambda: self._deliver(token))
        except Exception:
            with self._lock:
                if self._pending is token:
                    self._pending = None
            raise
        with self._lock:
            stale = self._pending is not token
            if not stale:
                self._job = job
        if stale:
            self._cron.cancel(job)

    def _deliver(self, token: object) -> None:
        """Called by Talon's scheduler; consume the latest filtered sample."""
        with self._lock:
            if not self._active or self._pending is not token:
                return
            self._pending = None
            self._job = None
            self._delivering = True
        try:
            self._callback()
        finally:
            with self._lock:
                self._delivering = False
