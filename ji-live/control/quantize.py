"""Stage 8.2 pure beat-quantized state queue.

Major performance-state changes (mutation/octave, MIDI/drum enables, drum rate
and lanes, groove edits) are applied at the next **quarter-note beat** while the
transport is running.  With the transport stopped, every action applies
immediately; that is the mandatory Stage 7/8.1 compatibility rule.

The queue is deliberately pure: it talks to a rhythm source (usually
:class:`control.rhythm.RhythmEngine`) through ``running`` and
``next_beat_time(1.0)``, and to the rest of the bridge only through the action
callables and an optional ``on_change`` notification.  No OSC, mido, Qt, or
audio imports live here.

Order at a beat
---------------
Actions are grouped into phases and applied in this order:

1. ``mutation``
2. ``octave``
3. ``groove`` (preset/grid/modifiers)
4. ``drums`` (enable/rate/lanes)
5. ``midi`` (drone/arp enables)

Within a phase, actions retain request order (a monotonically increasing
sequence), so the last request generally wins when two actions touch the same
state.  Keys coalesce last-wins: scheduling the same key again before the beat
replaces the action and increments its request count.  Different keys all run.

Threading
---------
``schedule``/``flush``/``tick`` may be called from any thread.  Callbacks run
outside the queue lock.  The worker thread is started explicitly with
:meth:`BeatQuantizer.start` and stopped with :meth:`BeatQuantizer.stop`; the
constructor never creates a thread, so pure tests and ``JIBridge(...)``
constructions stay thread-free until ``JIBridge.start()``.
"""

from __future__ import annotations

import sys
import threading
import time
from dataclasses import dataclass
from typing import Any, Callable, Hashable

# ---------------------------------------------------------------------------
# Phases / order
# ---------------------------------------------------------------------------

PHASE_MUTATION = 0
PHASE_OCTAVE = 1
PHASE_GROOVE = 2
PHASE_DRUMS = 3
PHASE_MIDI = 4

PHASE_NAMES: dict[int, str] = {
    PHASE_MUTATION: "mutation",
    PHASE_OCTAVE: "octave",
    PHASE_GROOVE: "groove",
    PHASE_DRUMS: "drums",
    PHASE_MIDI: "midi",
}

PHASE_ORDER: dict[str, int] = {
    name: phase for phase, name in PHASE_NAMES.items()
}

_APPLY_EPSILON = 1e-9


@dataclass(slots=True)
class PendingAction:
    """One queued action and its scheduling metadata."""

    key: Hashable
    action: Callable[[], Any]
    phase: int
    label: str
    due: float
    sequence: int
    requests: int = 1

    @property
    def order(self) -> tuple[int, int]:
        return self.phase, self.sequence

    @property
    def phase_name(self) -> str:
        return PHASE_NAMES.get(self.phase, str(self.phase))


class BeatQuantizer:
    """Thread-safe next-quarter-beat action queue.

    Args:
        rhythm: Any object with ``running`` (bool) and
            ``next_beat_time(quantum, at=...)``.  ``RhythmEngine`` satisfies
            this.
        now: Injectable monotonic clock (tests pass a fake callable).
        on_change: Optional callback run after a queue change/apply/flush.  The
            bridge uses it to send ``/ji/quantize/state``.  It must not raise;
            exceptions are logged and swallowed.
    """

    def __init__(
        self,
        rhythm: Any,
        *,
        now: Callable[[], float] = time.monotonic,
        on_change: Callable[[], None] | None = None,
    ) -> None:
        if rhythm is None or not callable(getattr(rhythm, "next_beat_time", None)):
            raise TypeError("rhythm must provide next_beat_time()")
        if not callable(now):
            raise TypeError("now must be callable")
        if on_change is not None and not callable(on_change):
            raise TypeError("on_change must be callable or None")

        self._rhythm = rhythm
        self._now = now
        self._on_change = on_change

        self._lock = threading.RLock()
        self._cond = threading.Condition(self._lock)
        self._pending: dict[Hashable, PendingAction] = {}
        self._sequence = 0
        self._thread: threading.Thread | None = None
        self._stop = threading.Event()

    # ------------------------------------------------------------------
    # Lifecycle
    # ------------------------------------------------------------------

    def start(self) -> None:
        """Start the worker thread.  Idempotent while running."""

        with self._lock:
            if self._thread is not None and self._thread.is_alive():
                return
            self._stop.clear()
            thread = threading.Thread(
                target=self._run_loop,
                name="ji-quantize",
                daemon=True,
            )
            self._thread = thread
        thread.start()
        print("[QUANT] queue started")

    def run(self) -> None:
        """Start the worker thread (non-blocking alias for :meth:`start`)."""

        self.start()

    def stop(self) -> None:
        """Stop the worker thread.  Pending actions are dropped, not applied."""

        with self._lock:
            self._stop.set()
            self._cond.notify_all()
            thread = self._thread
            self._thread = None
        if thread is not None and thread is not threading.current_thread():
            thread.join(timeout=2.0)
        with self._lock:
            dropped = len(self._pending)
            self._pending.clear()
        print("[QUANT] queue stopped")
        if dropped:
            print(f"[QUANT] dropped {dropped} pending actions on bridge stop")

    @property
    def started(self) -> bool:
        thread = self._thread
        return thread is not None and thread.is_alive()

    @property
    def pending_count(self) -> int:
        with self._lock:
            return len(self._pending)

    @property
    def pending_keys(self) -> tuple[Hashable, ...]:
        with self._lock:
            return tuple(self._pending)

    @property
    def running(self) -> bool:
        return bool(getattr(self._rhythm, "running", False))

    # ------------------------------------------------------------------
    # Public queue API
    # ------------------------------------------------------------------

    def schedule(
        self,
        key: Hashable,
        action: Callable[[], Any],
        *,
        phase: int = PHASE_GROOVE,
        label: str | None = None,
        coalesce: bool = True,
    ) -> bool:
        """Queue ``action`` for the next quarter-note beat.

        Returns ``True`` when the action was queued and ``False`` when it ran
        immediately (transport stopped).  With ``coalesce`` (the default) a
        second request with the same key before the beat replaces the action and
        increments its logged request count.  With ``coalesce=False`` a repeated
        key is rejected (the first pending action stays).
        """

        if not callable(action):
            raise TypeError("action must be callable")
        hash(key)  # raise early for unhashable keys
        if phase not in PHASE_NAMES:
            raise ValueError(f"unknown phase {phase!r}")
        name = str(label) if label is not None else str(key)

        if not self.running:
            self._run_immediate(action, name)
            return False

        now = float(self._now())
        with self._lock:
            existing = self._pending.get(key)
            if existing is not None and not coalesce:
                return True
            due = self._next_beat(now)
            self._sequence += 1
            requests = (existing.requests + 1) if existing is not None else 1
            self._pending[key] = PendingAction(
                key=key,
                action=action,
                phase=phase,
                label=name,
                due=due,
                sequence=self._sequence,
                requests=requests,
            )
            count = len(self._pending)
            self._cond.notify_all()

        if existing is not None:
            print(
                f"[QUANT] coalesced {requests} {name} requests "
                f"before beat {self._beat_label(due)}"
            )
        print(
            f"[QUANT] queued {name} ({count} pending) due beat "
            f"{self._beat_label(due)} (in {max(0.0, due - now):.3f} s)"
        )
        self._notify_change()
        return True

    def cancel(self, key: Hashable) -> bool:
        """Cancel a pending action; returns ``True`` when one was removed."""

        with self._lock:
            removed = self._pending.pop(key, None)
            if removed is not None:
                self._cond.notify_all()
        if removed is None:
            return False
        print(f"[QUANT] cancelled {removed.label}")
        self._notify_change()
        return True

    def flush(self, reason: str = "flush") -> int:
        """Apply every pending action immediately, in deterministic order."""

        with self._lock:
            actions = sorted(
                self._pending.values(), key=lambda item: item.order
            )
            self._pending.clear()
        if not actions:
            return 0
        print(
            f"[QUANT] flushed {len(actions)} pending actions on {reason}"
        )
        self._apply(actions, self._now(), log="flushed")
        return len(actions)

    def tick(self, now: float | None = None) -> int:
        """Apply actions that are due; returns the number applied.

        This is the testable body used by the worker thread.  It never sleeps
        and never busy-waits.
        """

        current = float(self._now() if now is None else now)
        with self._lock:
            if not self.running:
                actions = sorted(
                    self._pending.values(), key=lambda item: item.order
                )
                self._pending.clear()
            else:
                actions = sorted(
                    (
                        item
                        for item in self._pending.values()
                        if item.due <= current + _APPLY_EPSILON
                    ),
                    key=lambda item: item.order,
                )
                for item in actions:
                    self._pending.pop(item.key, None)
            if actions:
                self._cond.notify_all()
        if not actions:
            return 0
        self._apply(actions, current, log="applied")
        return len(actions)

    def tempo_changed(self) -> None:
        """Recompute the next beat for every pending action at the new tempo."""

        if self.pending_count == 0:
            return
        now = float(self._now())
        with self._lock:
            due = self._next_beat(now)
            for item in self._pending.values():
                item.due = due
            self._cond.notify_all()
        print(
            f"[QUANT] tempo change: next beat recalculated "
            f"(in {max(0.0, due - now):.3f} s)"
        )
        self._notify_change()

    def transport_changed(self) -> None:
        """Wake the worker after a transport start/stop."""

        with self._lock:
            self._cond.notify_all()

    def notify_tempo_change(self) -> None:
        self.tempo_changed()

    def seconds_to_next_beat(self, now: float | None = None) -> float:
        """Seconds until the next quarter beat, or ``-1.0`` when stopped."""

        if not self.running:
            return -1.0
        current = float(self._now() if now is None else now)
        return max(0.0, self._next_beat(current) - current)

    def snapshot(self) -> tuple[int, float]:
        """``(pending_count, seconds_to_next_beat)`` for status messages."""

        return self.pending_count, self.seconds_to_next_beat()

    # ------------------------------------------------------------------
    # Internals
    # ------------------------------------------------------------------

    def _next_beat(self, now: float) -> float:
        return float(self._rhythm.next_beat_time(1.0, at=now))

    def _beat_label(self, when: float) -> int:
        beat_at = getattr(self._rhythm, "beat_at", None)
        if callable(beat_at):
            try:
                return int(round(float(beat_at(when))))
            except Exception:
                return -1
        return -1

    def _run_immediate(self, action: Callable[[], Any], label: str) -> None:
        print(f"[QUANT] {label} applied immediately (transport stopped)")
        try:
            action()
        except Exception as exc:  # a bad action must not kill the caller
            print(
                f"[QUANT] immediate {label} action failed: {exc!r}",
                file=sys.stderr,
            )
        self._notify_change()

    def _apply(self, actions: list[PendingAction], now: float, log: str) -> None:
        beat = self._beat_label(now)
        names: list[str] = []
        for item in actions:
            if item.phase_name not in names:
                names.append(item.phase_name)
        print(
            f"[QUANT] {log} {len(actions)} actions at beat {beat} "
            f"(order: {', '.join(names)})"
        )
        for item in actions:
            try:
                item.action()
            except Exception as exc:  # a bad action must not kill the queue
                print(
                    f"[QUANT] {item.label} action failed: {exc!r}",
                    file=sys.stderr,
                )
        self._notify_change()

    def _notify_change(self) -> None:
        callback = self._on_change
        if callback is None:
            return
        try:
            callback()
        except Exception as exc:  # status is best-effort
            print(f"[QUANT] on_change failed: {exc!r}", file=sys.stderr)

    def _run_loop(self) -> None:  # pragma: no cover - exercised with a real thread
        while not self._stop.is_set():
            with self._cond:
                if self._stop.is_set():
                    break
                now = float(self._now())
                due = [
                    item
                    for item in self._pending.values()
                    if item.due <= now + _APPLY_EPSILON
                ]
                if not due:
                    if self._pending:
                        wait = min(item.due for item in self._pending.values()) - now
                        timeout = max(0.0, min(0.5, wait))
                    else:
                        timeout = 0.5
                    self._cond.wait(timeout=timeout)
                    continue
                actions = sorted(due, key=lambda item: item.order)
                for item in actions:
                    self._pending.pop(item.key, None)
            if self._stop.is_set():
                break
            self._apply(actions, now, log="applied")


# Convenience alias used in early Stage 8.2 design notes.
StateQueue = BeatQuantizer


__all__ = [
    "BeatQuantizer",
    "PHASE_DRUMS",
    "PHASE_GROOVE",
    "PHASE_MIDI",
    "PHASE_MUTATION",
    "PHASE_NAMES",
    "PHASE_OCTAVE",
    "PHASE_ORDER",
    "PendingAction",
    "StateQueue",
]
