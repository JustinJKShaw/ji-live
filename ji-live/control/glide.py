"""Stage 8.4.1 pure stepwise/replaced frequency-class glide planner.

A **gliding replacement** is one :class:`~control.mutation.MutationEvent` with
*both* ``removed_ratio`` and ``new_ratio`` set (the engine's ``stepwise`` and
``replaced`` reasons).  Pure additions and removals are not glides: they snap.

The planner turns ``before``, ``after`` and the applied events into intermediate
:class:`~control.mutation.SetSnapshot` values.  One replaced class ``old`` melts
into ``new`` along a **cents-linear** (log-ratio) path::

    r(t) = old * (new / old) ** (t / T)

so ``r(0) == old`` and ``r(T) == new``; values are folded into ``[1, 2)`` and
rounded to the engine's 12-decimal classes by
:func:`control.mutation.fold_ratio`.  Pure additions/removals are applied at
``t = 0``.  ``snapshot_at(T)`` is exactly ``after``, the engine's authoritative
state.

This module is deliberately pure and standard-library only: no OSC, PyQt6, mido
or threads in the planner.  :class:`GlideScheduler` owns the optional worker
thread; it is created lazily on the first :meth:`GlideScheduler.start` and is
never created by construction, matching ``control.quantize.BeatQuantizer``.
"""

from __future__ import annotations

import math
import sys
import threading
import time
from dataclasses import dataclass
from typing import Any, Callable, Iterable

try:  # Support both package imports and direct execution.
    from .mutation import MutationBatch, MutationEvent, SetSnapshot, fold_ratio
except ImportError:  # pragma: no cover - exercised only by direct execution
    from mutation import (  # type: ignore[no-redef]
        MutationBatch,
        MutationEvent,
        SetSnapshot,
        fold_ratio,
    )

#: Steps per second of a class glide.  The planner clamps it to 5..60 Hz.
GLIDE_RATE_HZ = 30.0
GLIDE_RATE_MIN = 5.0
GLIDE_RATE_MAX = 60.0

#: The minimum `retuneTime` sclang accepts (its receiver uses ``max(glide,
#: 0.005)``).  The bridge uses it for a pure add/remove and for a cancel snap:
#: those changes are instantaneous, the Glide knob does not apply.
SNAP_GLIDE_SECONDS = 0.005

_T_EPSILON = 1e-9


# ---------------------------------------------------------------------------
# Plan
# ---------------------------------------------------------------------------


@dataclass(frozen=True, slots=True)
class GlidePlan:
    """One bounded class melt.

    Args:
        before: Snapshot taken immediately before the change.
        after: The engine's authoritative snapshot after the change.
        pairs: ``(old, new)`` folded ratios for every gliding replacement.
        pure_adds: Folded ratios added at ``t = 0``.
        pure_removes: Folded ratios removed at ``t = 0``.
        duration: Glide time in seconds (the Glide knob).
        rate: Steps per second, clamped to 5..60.
        steps: Number of segments; the plan owns ``steps + 1`` snapshots.
    """

    before: SetSnapshot
    after: SetSnapshot
    pairs: tuple[tuple[float, float], ...]
    pure_adds: tuple[float, ...]
    pure_removes: tuple[float, ...]
    duration: float
    rate: float
    steps: int

    def __post_init__(self) -> None:  # pragma: no cover - built by the factory
        if not isinstance(self.before, SetSnapshot) or not isinstance(
            self.after, SetSnapshot
        ):
            raise ValueError("before/after must be SetSnapshot values")
        if not self.pairs:
            raise ValueError("a glide needs at least one replacement")
        if self.duration <= 0.0 or not math.isfinite(self.duration):
            raise ValueError("duration must be finite and strictly positive")
        if not math.isfinite(self.rate) or self.rate <= 0.0:
            raise ValueError("rate must be finite and strictly positive")
        if isinstance(self.steps, bool) or not isinstance(self.steps, int):
            raise ValueError("steps must be an integer")
        if self.steps < 1:
            raise ValueError("steps must be at least 1")

    # -- geometry -------------------------------------------------------

    @property
    def final_snapshot(self) -> SetSnapshot:
        """The engine's authoritative ``after`` snapshot."""

        return self.after

    def moving_ratio(self, pair_index: int, t: float) -> float:
        """One pair's folded class at time ``t`` seconds."""

        old, new = self.pairs[pair_index]
        u = self._progress(t)
        return fold_ratio(old * (new / old) ** u)

    def snapshot_at(self, t: float) -> SetSnapshot:
        """The broadcast set at time ``t`` (0 ≤ t ≤ ``duration``).

        ``t <= 0`` is the before set with the pure edits applied, the
        replacement still at its old ratio.  ``t >= duration`` is ``after``
        exactly.  Intermediate anchors follow the rule in the stage prompt.
        """

        value = self._as_time(t)
        if value >= self.duration:
            return self.after

        ratios = set(self._base_ratios())
        moving: dict[float, float] = {}
        for old, new in self.pairs:
            folded = fold_ratio(old * (new / old) ** self._progress(value))
            moving[fold_ratio(old)] = folded
            ratios.add(folded)
        anchor = self._anchor_at(ratios, moving)
        return SetSnapshot.create(sorted(ratios), anchor)

    # -- internals ------------------------------------------------------

    def _as_time(self, t: Any) -> float:
        try:
            value = float(t)
        except (TypeError, ValueError):
            raise ValueError("t must be a real number") from None
        if math.isnan(value):
            raise ValueError("t must be a real number")
        return value

    def _progress(self, t: float) -> float:
        if t <= 0.0:
            return 0.0
        if t >= self.duration:
            return 1.0
        return float(t) / self.duration

    def _base_ratios(self) -> frozenset[float]:
        """``before`` minus every removal/replaced old, plus every pure add."""

        base = set(self.before.ratios)
        for ratio in self.pure_removes:
            base.discard(ratio)
        for old, _new in self.pairs:
            base.discard(old)
        for ratio in self.pure_adds:
            base.add(ratio)
        return frozenset(base)

    def _anchor_at(
        self, ratios: set[float], moving: dict[float, float]
    ) -> float | None:
        before_anchor = self.before.anchor
        if before_anchor is None:
            return None

        # A replaced anchor travels with its pair: the class itself melts.
        if before_anchor in moving:
            candidate = moving[before_anchor]
            if candidate in ratios:
                return candidate
            return self._nearest(ratios, before_anchor)

        if before_anchor in ratios:
            return before_anchor

        # The anchor was a pure removal: use the engine's final anchor when it
        # is already present, otherwise the nearest surviving class.
        after_anchor = self.after.anchor
        if after_anchor is not None and after_anchor in ratios:
            return after_anchor
        return self._nearest(ratios, before_anchor)

    @staticmethod
    def _nearest(ratios: Iterable[float], target: float) -> float | None:
        classes = list(ratios)
        if not classes:
            return None
        return min(classes, key=lambda ratio: abs(math.log2(ratio / target)))


# ---------------------------------------------------------------------------
# Plan factory
# ---------------------------------------------------------------------------


def _clamp_rate(rate: Any) -> float:
    try:
        value = float(rate)
    except (TypeError, ValueError):
        raise ValueError("rate must be a real number") from None
    if not math.isfinite(value) or value <= 0.0:
        raise ValueError("rate must be finite and strictly positive")
    return max(GLIDE_RATE_MIN, min(GLIDE_RATE_MAX, value))


def _snapshot(value: Any, label: str) -> SetSnapshot:
    """Canonicalise a snapshot-like object; raise ``ValueError`` otherwise."""

    if isinstance(value, SetSnapshot):
        return value
    ratios = getattr(value, "ratios", None)
    anchor = getattr(value, "anchor", None)
    if ratios is None:
        raise ValueError(f"{label} must be a SetSnapshot")
    try:
        return SetSnapshot.create(ratios, anchor)
    except (TypeError, ValueError) as exc:
        raise ValueError(f"invalid {label}: {exc}") from None


def _events(value: Any) -> tuple[MutationEvent, ...]:
    """Flatten ``MutationEvent`` / ``MutationBatch`` / an iterable of events."""

    if isinstance(value, MutationEvent):
        items: tuple[Any, ...] = (value,)
    elif isinstance(value, MutationBatch):
        items = value.events
    else:
        if isinstance(value, (str, bytes)):
            raise ValueError("events must be MutationEvent values")
        try:
            items = tuple(value)
        except TypeError:
            raise ValueError("events must be MutationEvents") from None
    cleaned: list[MutationEvent] = []
    for item in items:
        if not isinstance(item, MutationEvent):
            raise ValueError("events must be MutationEvents")
        cleaned.append(item)
    if not cleaned:
        raise ValueError("at least one MutationEvent is required")
    return tuple(cleaned)


def build_glide_plan(
    before: SetSnapshot,
    after: SetSnapshot,
    events: MutationEvent | MutationBatch | Iterable[MutationEvent],
    duration: float,
    rate: float = GLIDE_RATE_HZ,
) -> GlidePlan | None:
    """Build the bounded melt for one applied change.

    Returns ``None`` when there is no gliding replacement (a pure add/remove),
    when ``duration <= 0``, or when the computed endpoint would not equal
    ``after`` (a defensive consistency check).  Malformed input — a non-snapshot,
    a non-event, a non-finite duration/rate, a replacement whose old class is
    absent, or two replacements of one class — raises ``ValueError``.
    """

    start = _snapshot(before, "before")
    finish = _snapshot(after, "after")
    applied = _events(events)

    pairs: list[tuple[float, float]] = []
    pure_adds: list[float] = []
    pure_removes: list[float] = []
    seen_old: set[float] = set()
    for event in applied:
        old_raw = event.removed_ratio
        new_raw = event.new_ratio
        if old_raw is None and new_raw is None:
            raise ValueError("a mutation event must add, remove, or replace")
        try:
            if old_raw is not None and new_raw is not None:
                old = fold_ratio(old_raw)
                new = fold_ratio(new_raw)
                if old in seen_old:
                    raise ValueError("two replacements share one removed class")
                seen_old.add(old)
                if old not in start.ratios:
                    raise ValueError(
                        f"replaced class {old:.6f} is not in the before set"
                    )
                pairs.append((old, new))
            elif old_raw is not None:
                folded = fold_ratio(old_raw)
                if folded not in pure_removes:
                    pure_removes.append(folded)
            else:
                folded = fold_ratio(new_raw)  # type: ignore[arg-type]
                if folded not in pure_adds:
                    pure_adds.append(folded)
        except TypeError as exc:
            raise ValueError(f"invalid event ratio: {exc}") from None

    if not pairs:
        return None

    try:
        duration_value = float(duration)
    except (TypeError, ValueError):
        raise ValueError("duration must be a real number") from None
    if not math.isfinite(duration_value):
        raise ValueError("duration must be finite")
    if duration_value <= 0.0:
        return None

    rate_value = _clamp_rate(rate)
    steps = max(1, int(math.floor(duration_value * rate_value + 0.5)))

    plan = GlidePlan(
        before=start,
        after=finish,
        pairs=tuple(pairs),
        pure_adds=tuple(pure_adds),
        pure_removes=tuple(pure_removes),
        duration=duration_value,
        rate=rate_value,
        steps=steps,
    )

    # Defensive endpoint check: the events must explain the engine's final set.
    final_ratios = set(plan._base_ratios())
    final_ratios.update(new for _old, new in pairs)
    if tuple(sorted(final_ratios)) != finish.ratios:
        return None
    if finish.anchor is not None and finish.anchor not in final_ratios:
        return None
    return plan


# ---------------------------------------------------------------------------
# Scheduler
# ---------------------------------------------------------------------------


class GlideScheduler:
    """Step clock for one :class:`GlidePlan` plus a lazy worker thread.

    Args:
        on_step: ``on_step(snapshot, final)``; called once per step.  Exceptions
            are logged and swallowed so a bad voice can never strand the glide.
        now: Injectable monotonic clock (tests pass a fake callable).
        sleep: Optional injectable sleep used by the worker instead of the
            condition wait.  ``None`` (the default) waits on the condition.
        threaded: When ``False`` no worker is ever created; tests drive
            :meth:`tick` with a fake clock.  The bridge uses the default.

    Step 0 is delivered synchronously from :meth:`start`; later steps are
    delivered at exactly ``k * duration / steps``.  The final snapshot is
    ``plan.after``.  The worker is created lazily on the first ``start``.
    """

    def __init__(
        self,
        on_step: Callable[[SetSnapshot, bool], None],
        *,
        now: Callable[[], float] | None = None,
        sleep: Callable[[float], Any] | None = None,
        threaded: bool = True,
    ) -> None:
        if not callable(on_step):
            raise TypeError("on_step must be callable")
        clock = time.monotonic if now is None else now
        if not callable(clock):
            raise TypeError("now must be callable")
        if sleep is not None and not callable(sleep):
            raise TypeError("sleep must be callable or None")

        self._on_step = on_step
        self._now = clock
        self._sleep = sleep
        self._threaded = bool(threaded)

        self._lock = threading.RLock()
        self._cond = threading.Condition(self._lock)
        self._thread: threading.Thread | None = None
        self._plan: GlidePlan | None = None
        self._started_at = 0.0
        self._next_index = 0
        self._active = False

    # ------------------------------------------------------------------
    # State
    # ------------------------------------------------------------------

    @property
    def active(self) -> bool:
        with self._lock:
            return self._active

    @property
    def plan(self) -> GlidePlan | None:
        with self._lock:
            return self._plan

    @property
    def started(self) -> bool:
        thread = self._thread
        return thread is not None and thread.is_alive()

    @property
    def progress(self) -> float:
        """Elapsed fraction of the current plan (0..1; 0 when inactive)."""

        with self._lock:
            plan = self._plan
            if not self._active or plan is None:
                return 0.0
            elapsed = self._now() - self._started_at
        return max(0.0, min(1.0, elapsed / plan.duration))

    # ------------------------------------------------------------------
    # Lifecycle
    # ------------------------------------------------------------------

    def start(self, plan: GlidePlan) -> bool:
        """Begin streaming ``plan``; step 0 is delivered before returning.

        Starting while a plan is active drops the previous plan (no snap — the
        bridge snaps explicitly).  Returns ``True`` when the plan was accepted.
        """

        if not isinstance(plan, GlidePlan):
            raise TypeError("plan must be a GlidePlan")
        thread: threading.Thread | None = None
        needs_thread = False
        with self._cond:
            self._plan = plan
            self._started_at = float(self._now())
            self._next_index = 1
            self._active = True
            self._cond.notify_all()
            thread = self._thread
            needs_thread = self._threaded and (
                thread is None or not thread.is_alive()
            )
            if needs_thread:
                thread = threading.Thread(
                    target=self._run,
                    name="ji-glide",
                    daemon=True,
                )
                self._thread = thread
            # Step 0 is the t=0 set: pure edits applied, replacements at old.
            self._deliver(plan.snapshot_at(0.0), False)
        if needs_thread and thread is not None:
            thread.start()
        return True

    def cancel(self, *, snap: bool = True) -> bool:
        """Stop the stream; with ``snap`` deliver ``plan.after`` once.

        Idempotent.  Returns whether a glide was active.  The snap delivery is
        a normal (non-transient) step so the UI and history receive the final
        set exactly once.
        """

        with self._cond:
            active = self._active
            plan = self._plan
            self._active = False
            self._plan = None
            self._next_index = 0
            self._cond.notify_all()
        if active and snap and plan is not None:
            self._deliver(plan.final_snapshot, True)
        return active

    def stop(self) -> bool:
        """Stop the worker and drop any active plan (no snap)."""

        with self._cond:
            active = self._active
            self._active = False
            self._plan = None
            self._next_index = 0
            self._cond.notify_all()
            thread = self._thread
            self._thread = None
        if thread is not None and thread is not threading.current_thread():
            thread.join(timeout=2.0)
        return active

    def tick(self, now: float | None = None) -> int:
        """Apply every step that is due at ``now``; return how many ran."""

        with self._cond:
            at = float(self._now()) if now is None else float(now)
            return self._tick_locked(at)

    # ------------------------------------------------------------------
    # Internals
    # ------------------------------------------------------------------

    def _due_time_locked(self, index: int) -> float:
        plan = self._plan
        if plan is None:
            return math.inf
        return self._started_at + plan.duration * (index / plan.steps)

    def _tick_locked(self, now: float) -> int:
        plan = self._plan
        if not self._active or plan is None:
            return 0
        applied = 0
        while self._next_index <= plan.steps:
            due = self._due_time_locked(self._next_index)
            if now + _T_EPSILON < due:
                break
            index = self._next_index
            self._next_index += 1
            final = index >= plan.steps
            if final:
                snapshot = plan.final_snapshot
                self._active = False
                self._plan = None
                self._next_index = 0
                self._cond.notify_all()
            else:
                snapshot = plan.snapshot_at(plan.duration * (index / plan.steps))
            self._deliver(snapshot, final)
            applied += 1
            if final:
                break
        return applied

    def _deliver(self, snapshot: SetSnapshot, final: bool) -> None:
        try:
            self._on_step(snapshot, final)
        except Exception as exc:  # a voice failure must not strand the glide
            print(f"[GLIDE] step failed: {exc}", file=sys.stderr)

    def _run(self) -> None:  # pragma: no cover - real thread, tested separately
        while True:
            sleep_for = None
            with self._cond:
                if not self._active or self._plan is None:
                    return
                due = self._due_time_locked(self._next_index)
                wait = due - float(self._now())
                if wait > 0.0:
                    if self._sleep is None:
                        self._cond.wait(wait)
                        continue
                    sleep_for = wait
            if sleep_for is not None:
                self._sleep(sleep_for)  # type: ignore[misc]
                continue
            with self._cond:
                if not self._active or self._plan is None:
                    return
                self._tick_locked(float(self._now()))
