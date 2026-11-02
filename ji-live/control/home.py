"""Stage 8.4 home sets, deterministic journeys, and smooth return.

A **home set** is a captured :class:`~control.mutation.SetSnapshot` you can
return to.  Four **slots** (``A``-``D``) hold homes, so ``A -> B -> C -> A``
becomes a kind of set chord progression.  The module is deliberately pure:
standard library only, no OSC, no PyQt6, no ``mido``, no threads in the planner.
It is safe to import from tests and from the bridge.

Vocabulary (kept identical in the code, the GUI and the docs):

===========  ==========================================================
term         meaning
===========  ==========================================================
home set     a captured snapshot you can return to
slot         one of four home slots (``A``-``D``)
distance     classes that still differ from the active home
return       the beat-spaced sequence of batches that lands exactly home
journey      mutations made since the last landing on a home
drift        optional cap on how far a journey may stray from home
back         restore the immediately previous snapshot
===========  ==========================================================

Distance and batches
--------------------

Given the current and home class sets::

    remove   = classes in the current set but not in the home set
    add      = classes in the home set but not in the current set
    distance = max(len(remove), len(add))
    batches  = ceil(distance / changes_per_batch)

``changes_per_batch`` is ``MutationEngine.max_changes`` (the hard per-mutation
cap, default 3), *not* the ``change_count`` UI setting.  A return therefore
takes exactly ``ceil(distance / max_changes)`` quarter beats; one extra beat is
a bug.

Pairing is **voice-led**: removals are ordered by their distance from the home
set (most distant first), and each removal takes the nearest remaining home
class (log-ratio distance in cents) as its replacement.  Leftover removals or
additions are pure edits.  The seed only breaks ties, so the same
``(current, home, max_changes, seed)`` always yields the same plan.

The planner never leaves ``[min_size, max_size]``: when the home set is larger
than the current one the pure additions run first (and the home anchor is added
in the last batch); when it is smaller the pure removals run first.
Replacements do not change the size.
"""

from __future__ import annotations

import json
import math
import random
import threading
from collections import deque
from dataclasses import dataclass
from pathlib import Path
from typing import Any, Mapping

try:  # Support both package imports and direct execution.
    from .mutation import MAX_SET_SIZE, SetSnapshot, fold_ratio
except ImportError:  # pragma: no cover - direct execution
    from mutation import MAX_SET_SIZE, SetSnapshot, fold_ratio  # type: ignore[no-redef]

# ---------------------------------------------------------------------------
# Ranges / defaults (single source of truth for the bridge and the UI)
# ---------------------------------------------------------------------------

SLOT_NAMES: tuple[str, ...] = ("A", "B", "C", "D")
DEFAULT_SLOTS = 4

AUTO_DEFAULT = False
EVERY_MIN = 1
EVERY_MAX = 32
EVERY_DEFAULT = 8

SEED_MIN = 0
SEED_MAX = 2**31 - 1
SEED_DEFAULT = 1

DRIFT_MIN = 0
DRIFT_MAX = MAX_SET_SIZE
DRIFT_DEFAULT = 0  # 0 = unlimited

RETURN_GLIDE_MIN = 0.02
RETURN_GLIDE_MAX = 0.30
RETURN_GLIDE_DEFAULT = 0.06

HISTORY_DEPTH_MIN = 8
HISTORY_DEPTH_MAX = 256
HISTORY_DEPTH_DEFAULT = 64

#: Re-proposals tried before a drifting mutation is accepted as a last resort.
MAX_DRIFT_TRIES = 8

#: Planner size bounds default to the engine's documented policy.
DEFAULT_MIN_SIZE = 3

#: ``config/home_config.json`` next to the project root.
DEFAULT_CONFIG_PATH = (
    Path(__file__).resolve().parent.parent / "config" / "home_config.json"
)

_CONFIG_KEYS: tuple[str, ...] = (
    "slots",
    "auto",
    "every",
    "seed",
    "max_drift",
    "return_glide",
    "history_depth",
)


# ---------------------------------------------------------------------------
# Validation helpers
# ---------------------------------------------------------------------------


def _as_int(value: Any, label: str) -> int:
    if isinstance(value, bool):
        raise TypeError(f"{label} must be an integer")
    try:
        numeric = float(value)
    except (TypeError, ValueError):
        raise TypeError(f"{label} must be an integer") from None
    if not math.isfinite(numeric):
        raise ValueError(f"{label} must be finite")
    return int(round(numeric))


def _as_float(value: Any, label: str) -> float:
    if isinstance(value, bool):
        raise TypeError(f"{label} must be a real number")
    try:
        numeric = float(value)
    except (TypeError, ValueError):
        raise TypeError(f"{label} must be a real number") from None
    if not math.isfinite(numeric):
        raise ValueError(f"{label} must be finite")
    return numeric


def clamp_every(value: Any) -> int:
    """Return ``every`` clamped to 1..32."""

    return max(EVERY_MIN, min(EVERY_MAX, _as_int(value, "every")))


def clamp_seed(value: Any) -> int:
    """Return the journey seed clamped to 0..2^31-1."""

    return max(SEED_MIN, min(SEED_MAX, _as_int(value, "seed")))


def clamp_drift(value: Any) -> int:
    """Return ``max_drift`` clamped to 0..12 (0 = unlimited)."""

    return max(DRIFT_MIN, min(DRIFT_MAX, _as_int(value, "max_drift")))


def clamp_return_glide(value: Any) -> float:
    """Return the return glide clamped to 0.02..0.30 seconds."""

    return max(
        RETURN_GLIDE_MIN, min(RETURN_GLIDE_MAX, _as_float(value, "return_glide"))
    )


def clamp_history_depth(value: Any) -> int:
    """Return the history depth clamped to 8..256 snapshots."""

    return max(
        HISTORY_DEPTH_MIN, min(HISTORY_DEPTH_MAX, _as_int(value, "history_depth"))
    )


def validate_slot(slot: Any) -> int:
    """Return a 0-based slot index 0..3 or raise ``ValueError``."""

    index = _as_int(slot, "slot")
    if not 0 <= index < DEFAULT_SLOTS:
        raise ValueError(f"slot must be 0..{DEFAULT_SLOTS - 1}, got {slot!r}")
    return index


def slot_name(slot: Any) -> str:
    """``0 -> "A"``; raises for an invalid slot."""

    return SLOT_NAMES[validate_slot(slot)]


# ---------------------------------------------------------------------------
# Config
# ---------------------------------------------------------------------------


@dataclass(frozen=True, slots=True)
class HomeConfig:
    """Validated ``config/home_config.json`` (defaults + clamps)."""

    slots: int = DEFAULT_SLOTS
    auto: bool = AUTO_DEFAULT
    every: int = EVERY_DEFAULT
    seed: int = SEED_DEFAULT
    max_drift: int = DRIFT_DEFAULT
    return_glide: float = RETURN_GLIDE_DEFAULT
    history_depth: int = HISTORY_DEPTH_DEFAULT

    @classmethod
    def create(
        cls,
        slots: Any = DEFAULT_SLOTS,
        auto: Any = AUTO_DEFAULT,
        every: Any = EVERY_DEFAULT,
        seed: Any = SEED_DEFAULT,
        max_drift: Any = DRIFT_DEFAULT,
        return_glide: Any = RETURN_GLIDE_DEFAULT,
        history_depth: Any = HISTORY_DEPTH_DEFAULT,
    ) -> "HomeConfig":
        """Validate/clamp every field; ``slots`` is fixed at four."""

        slot_count = _as_int(slots, "slots")
        if slot_count != DEFAULT_SLOTS:
            raise ValueError(f"slots is fixed at {DEFAULT_SLOTS}")
        return cls(
            slots=slot_count,
            auto=bool(auto),
            every=clamp_every(every),
            seed=clamp_seed(seed),
            max_drift=clamp_drift(max_drift),
            return_glide=clamp_return_glide(return_glide),
            history_depth=clamp_history_depth(history_depth),
        )

    @classmethod
    def from_mapping(cls, data: Mapping[str, Any] | None) -> "HomeConfig":
        """Build a config from a JSON mapping; unknown keys are ignored."""

        if data is None:
            return cls.create()
        if not isinstance(data, Mapping):
            raise TypeError("home config must be a mapping")
        values = {key: data[key] for key in _CONFIG_KEYS if key in data}
        return cls.create(**values)

    @classmethod
    def load(cls, path: str | Path | None = None) -> "HomeConfig":
        """Load and validate the home config (project-relative fallback)."""

        candidate = Path(DEFAULT_CONFIG_PATH if path is None else path)
        if not candidate.is_absolute() and not candidate.exists():
            project_candidate = Path(__file__).resolve().parent.parent / candidate
            if project_candidate.exists():
                candidate = project_candidate

        try:
            text = candidate.read_text(encoding="utf-8")
        except FileNotFoundError:
            raise FileNotFoundError(f"home config not found: {candidate}") from None

        try:
            data = json.loads(text)
        except json.JSONDecodeError as exc:
            raise ValueError(f"invalid JSON in {candidate}: {exc}") from exc
        return cls.from_mapping(data)

    def as_dict(self) -> dict[str, Any]:
        return {
            "slots": self.slots,
            "auto": self.auto,
            "every": self.every,
            "seed": self.seed,
            "max_drift": self.max_drift,
            "return_glide": self.return_glide,
            "history_depth": self.history_depth,
        }


# ---------------------------------------------------------------------------
# Status snapshot
# ---------------------------------------------------------------------------


@dataclass(frozen=True, slots=True)
class HomeState:
    """Immutable status snapshot behind ``/ji/home/state`` (13 values)."""

    has_home: bool = False
    active_slot: int = -1
    home_classes: int = 0
    distance: int = 0
    pending_batches: int = 0
    history_depth: int = 0
    auto: bool = False
    every: int = EVERY_DEFAULT
    seed: int = SEED_DEFAULT
    drift: int = DRIFT_DEFAULT
    returning: bool = False
    journey_mutations: int = 0
    slot_mask: int = 0

    @property
    def active_name(self) -> str:
        if 0 <= self.active_slot < DEFAULT_SLOTS:
            return SLOT_NAMES[self.active_slot]
        return "-"

    def slot_captured(self, slot: Any) -> bool:
        index = validate_slot(slot)
        return bool(self.slot_mask & (1 << index))

    def as_state_args(self) -> list[int]:
        """The documented 13-value ``/ji/home/state`` payload."""

        return [
            int(bool(self.has_home)),
            int(self.active_slot),
            int(self.home_classes),
            int(self.distance),
            int(self.pending_batches),
            int(self.history_depth),
            int(bool(self.auto)),
            int(self.every),
            int(self.seed),
            int(self.drift),
            int(bool(self.returning)),
            int(self.journey_mutations),
            int(self.slot_mask),
        ]


# ---------------------------------------------------------------------------
# Snapshot coercion helpers
# ---------------------------------------------------------------------------


def _as_classes(value: Any) -> tuple[float, ...]:
    """Return folded, sorted, unique classes from a snapshot-like value."""

    if isinstance(value, SetSnapshot):
        return value.ratios
    ratios = getattr(value, "ratios", None)
    if ratios is None:
        ratios = value
    if isinstance(ratios, (str, bytes)):
        raise TypeError("snapshot ratios must be an iterable of real numbers")
    classes: list[float] = []
    for raw in ratios:
        folded = fold_ratio(raw)
        if folded not in classes:
            classes.append(folded)
    classes.sort()
    return tuple(classes)


def _anchor_of(value: Any) -> float | None:
    anchor = getattr(value, "anchor", None)
    if anchor is None:
        return None
    return fold_ratio(anchor)


def _snapshot_of(value: Any) -> SetSnapshot:
    """Canonical :class:`SetSnapshot` from a snapshot-like value."""

    if isinstance(value, SetSnapshot):
        return value
    return SetSnapshot.create(
        _as_classes(value),
        _anchor_of(value),
        getattr(value, "octaves", None),
    )


def _cents(first: float, second: float) -> float:
    return abs(1200.0 * math.log2(first / second))


# ---------------------------------------------------------------------------
# Planner
# ---------------------------------------------------------------------------


class HomePlanner:
    """Pure distance / voice-led pairing / batch planning."""

    def __init__(
        self, *, min_size: int = DEFAULT_MIN_SIZE, max_size: int = MAX_SET_SIZE
    ) -> None:
        if isinstance(min_size, bool) or not isinstance(min_size, int):
            raise TypeError("min_size must be an integer")
        if isinstance(max_size, bool) or not isinstance(max_size, int):
            raise TypeError("max_size must be an integer")
        if not 1 <= min_size <= max_size <= MAX_SET_SIZE:
            raise ValueError("min_size/max_size must satisfy 1 <= min <= max <= 12")
        self.min_size = min_size
        self.max_size = max_size

    # -- distance -------------------------------------------------------

    def distance(self, current: Any, home: Any) -> int:
        """``max(len(remove), len(add))`` between two snapshots."""

        current_set = set(_as_classes(current))
        home_set = set(_as_classes(home))
        return max(len(current_set - home_set), len(home_set - current_set))

    # -- planning -------------------------------------------------------

    def plan(
        self, current: Any, home: Any, max_changes: int, seed: int
    ) -> list[list[tuple[float | None, float | None]]]:
        """Batches of ``(remove_ratio, add_ratio)``; applying them lands home."""

        if isinstance(max_changes, bool) or not isinstance(max_changes, int):
            raise TypeError("max_changes must be an integer")
        if not 1 <= max_changes <= MAX_SET_SIZE:
            raise ValueError(f"max_changes must be between 1 and {MAX_SET_SIZE}")
        if isinstance(seed, bool) or not isinstance(seed, int):
            raise TypeError("seed must be an integer")

        current_classes = _as_classes(current)
        home_classes = _as_classes(home)
        if not current_classes or not home_classes:
            raise ValueError("current and home must each contain a class")
        if not self.min_size <= len(current_classes) <= self.max_size:
            raise ValueError(
                f"current size {len(current_classes)} is outside "
                f"{self.min_size}..{self.max_size}"
            )
        if not self.min_size <= len(home_classes) <= self.max_size:
            raise ValueError(
                f"home size {len(home_classes)} is outside "
                f"{self.min_size}..{self.max_size}"
            )

        home_anchor = _anchor_of(home)
        if home_anchor is not None and home_anchor not in home_classes:
            raise ValueError("home anchor is not in the home set")

        current_set = set(current_classes)
        home_set = set(home_classes)
        remove = sorted(current_set - home_set)
        add = sorted(home_set - current_set)
        distance = max(len(remove), len(add))
        if distance == 0:
            return []

        batches = math.ceil(distance / max_changes)
        rng = random.Random(seed)

        # 1. Removals, most distant from the home set first.  The seed only
        #    breaks exact ties, so equal-distance classes stay reproducible.
        def home_distance(ratio: float) -> float:
            return min(_cents(ratio, target) for target in home_classes)

        removals = sorted(
            remove, key=lambda ratio: (-home_distance(ratio), rng.random())
        )

        # 2. Voice-led pairing: each removal takes the nearest remaining home
        #    class it could become.  Ties are seeded.
        add_pool = list(add)
        pairs: list[tuple[float | None, float | None]] = []
        leftovers: list[tuple[float | None, float | None]] = []
        for removed in removals:
            if not add_pool:
                leftovers.append((removed, None))
                continue
            nearest = min(
                add_pool, key=lambda target: (_cents(target, removed), rng.random())
            )
            add_pool.remove(nearest)
            pairs.append((removed, nearest))
        pure_adds: list[tuple[float | None, float | None]] = [
            (None, target) for target in add_pool
        ]

        # 3. Size-safe order: grow first when home is bigger, shrink first when
        #    home is smaller; replacements never change the size.
        if len(remove) > len(add):
            edits = leftovers + pairs
        elif len(add) > len(remove):
            edits = pure_adds + pairs
        else:
            edits = list(pairs)

        # 4. The home anchor is (re)added in the last batch.
        if home_anchor is not None and home_anchor not in current_set:
            for index, (_removed, added) in enumerate(edits):
                if added == home_anchor:
                    edits.append(edits.pop(index))
                    break
            else:  # pragma: no cover - unreachable with consistent inputs
                raise RuntimeError("home anchor is missing from the plan")

        # 5. Even batches, last one holding the anchor edit.
        count = len(edits)
        base, extra = divmod(count, batches)
        sizes = [base + (1 if index < extra else 0) for index in range(batches)]
        plan: list[list[tuple[float | None, float | None]]] = []
        start = 0
        for size in sizes:
            plan.append(edits[start : start + size])
            start += size
        return plan


def plan_return(
    current: Any,
    home: Any,
    max_changes: int,
    seed: int,
    *,
    min_size: int = DEFAULT_MIN_SIZE,
    max_size: int = MAX_SET_SIZE,
) -> list[list[tuple[float | None, float | None]]]:
    """Batches of ``(remove_ratio, add_ratio)``; applying them lands home."""

    return HomePlanner(min_size=min_size, max_size=max_size).plan(
        current, home, max_changes, seed
    )


# ---------------------------------------------------------------------------
# Manager
# ---------------------------------------------------------------------------


class HomeManager:
    """Slots, active home, journey bookkeeping, history, and planner cache.

    One re-entrant lock guards the mutable bookkeeping because the bridge calls
    this class from the OSC thread and from the beat-queue thread.  The planner
    itself stays pure and lock-free.
    """

    def __init__(
        self,
        config: HomeConfig | Mapping[str, Any] | None = None,
        *,
        planner: HomePlanner | None = None,
    ) -> None:
        if config is None:
            resolved = HomeConfig.create()
        elif isinstance(config, HomeConfig):
            resolved = config
        elif isinstance(config, Mapping):
            resolved = HomeConfig.from_mapping(config)
        else:
            raise TypeError("config must be a HomeConfig, a mapping, or None")
        self._lock = threading.RLock()
        self.config = resolved
        self._planner = planner or HomePlanner()
        self._slots: list[SetSnapshot | None] = [None] * DEFAULT_SLOTS
        self._active_slot = -1
        self._history: deque[SetSnapshot] = deque(maxlen=resolved.history_depth)
        self._redo: deque[SetSnapshot] = deque(maxlen=resolved.history_depth)
        self._journey_mutations = 0
        self._journey_seed = resolved.seed
        self._returning = False
        self._return_slot = -1
        self._return_total = 0
        self._return_index = 0
        self._plan_cache: dict[tuple[Any, ...], list[list[tuple[Any, Any]]]] = {}

    # -- config ---------------------------------------------------------

    @property
    def planner(self) -> HomePlanner:
        return self._planner

    def update_config(self, **changes: Any) -> HomeConfig:
        """Validate/clamp new config values and resize the history."""

        values = self.config.as_dict()
        for key, value in changes.items():
            if key in values:
                values[key] = value
        resolved = HomeConfig.from_mapping(values)
        with self._lock:
            self.config = resolved
            if "seed" in changes:
                # Keep /ji/home/state's seed equal to the configured seed even
                # before a journey starts (the GUI syncs the spin from it).
                self._journey_seed = resolved.seed
            if self._history.maxlen != resolved.history_depth:
                self._history = deque(
                    self._history, maxlen=resolved.history_depth
                )
                self._redo = deque(self._redo, maxlen=resolved.history_depth)
            self._plan_cache.clear()
        return resolved

    # -- slots ----------------------------------------------------------

    @property
    def slots(self) -> tuple[SetSnapshot | None, ...]:
        with self._lock:
            return tuple(self._slots)

    def slot_snapshot(self, slot: Any) -> SetSnapshot | None:
        index = validate_slot(slot)
        with self._lock:
            return self._slots[index]

    def capture(self, slot: Any, snapshot: Any) -> SetSnapshot:
        """Store ``snapshot`` in ``slot`` and make that slot active."""

        index = validate_slot(slot)
        canonical = _snapshot_of(snapshot)
        with self._lock:
            self._slots[index] = canonical
            self._active_slot = index
        return canonical

    def clear(self, slot: Any) -> bool:
        """Forget one slot; returns ``True`` when it held a home."""

        index = validate_slot(slot)
        with self._lock:
            existed = self._slots[index] is not None
            self._slots[index] = None
            if self._active_slot == index:
                self._active_slot = -1
            return existed

    def go(self, slot: Any) -> SetSnapshot | None:
        """Make a captured slot active and return its snapshot."""

        index = validate_slot(slot)
        with self._lock:
            snapshot = self._slots[index]
            if snapshot is None:
                return None
            self._active_slot = index
            return snapshot

    def set_active_slot(self, slot: Any) -> None:
        index = validate_slot(slot)
        with self._lock:
            self._active_slot = index

    @property
    def active_slot(self) -> int:
        with self._lock:
            return self._active_slot

    @property
    def active_name(self) -> str:
        index = self.active_slot
        return SLOT_NAMES[index] if 0 <= index < DEFAULT_SLOTS else "-"

    @property
    def home(self) -> SetSnapshot | None:
        """The active home snapshot, or ``None``."""

        with self._lock:
            if self._active_slot < 0:
                return None
            return self._slots[self._active_slot]

    @property
    def has_home(self) -> bool:
        with self._lock:
            return any(snapshot is not None for snapshot in self._slots)

    @property
    def slot_mask(self) -> int:
        with self._lock:
            mask = 0
            for index, snapshot in enumerate(self._slots):
                if snapshot is not None:
                    mask |= 1 << index
            return mask

    def distance(self, current: Any) -> int:
        """Distance from ``current`` to the active home (0 without a home)."""

        with self._lock:
            home = self.home
        if home is None:
            return 0
        return self._planner.distance(current, home)

    # -- planning -------------------------------------------------------

    def plan(
        self, current: Any, home: Any, max_changes: int, seed: int
    ) -> list[list[tuple[float | None, float | None]]]:
        """Planner cache: repeated requests for one position reuse the plan."""

        current_snapshot = _snapshot_of(current)
        home_snapshot = _snapshot_of(home)
        key = (
            current_snapshot,
            home_snapshot,
            int(max_changes),
            int(seed),
            self._planner.min_size,
            self._planner.max_size,
        )
        with self._lock:
            cached = self._plan_cache.get(key)
            if cached is not None:
                return [list(batch) for batch in cached]
        plan = self._planner.plan(current_snapshot, home_snapshot, max_changes, seed)
        with self._lock:
            if len(self._plan_cache) >= 16:
                self._plan_cache.clear()
            self._plan_cache[key] = [list(batch) for batch in plan]
        return plan

    # -- history --------------------------------------------------------

    @property
    def history(self) -> tuple[SetSnapshot, ...]:
        with self._lock:
            return tuple(self._history)

    @property
    def history_depth(self) -> int:
        with self._lock:
            return len(self._history)

    @property
    def can_back(self) -> bool:
        with self._lock:
            return len(self._history) >= 2

    @property
    def can_redo(self) -> bool:
        with self._lock:
            return bool(self._redo)

    def record(self, snapshot: Any) -> SetSnapshot:
        """Record an applied set change; any forward change clears redo."""

        canonical = _snapshot_of(snapshot)
        with self._lock:
            if self._history and self._history[-1] == canonical:
                return canonical
            self._history.append(canonical)
            self._redo.clear()
        return canonical

    def back(self) -> SetSnapshot | None:
        """Restore the previous snapshot; ``None`` at the oldest entry."""

        with self._lock:
            if len(self._history) < 2:
                return None
            current = self._history.pop()
            self._redo.append(current)
            return self._history[-1]

    def redo(self) -> SetSnapshot | None:
        """Re-apply the last undone snapshot (no UI surface in 8.4)."""

        with self._lock:
            if not self._redo:
                return None
            snapshot = self._redo.pop()
            self._history.append(snapshot)
            return snapshot

    # -- return bookkeeping ---------------------------------------------

    def begin_return(self, slot: Any, batches: int) -> None:
        index = validate_slot(slot)
        with self._lock:
            self._active_slot = index
            total = max(0, int(batches))
            self._returning = total > 0
            self._return_slot = index if total > 0 else -1
            self._return_total = total
            self._return_index = 0

    def note_return_step(self) -> None:
        with self._lock:
            if not self._returning:
                return
            self._return_index += 1
            if self._return_index >= self._return_total:
                self._returning = False

    def cancel_return(self) -> bool:
        """Drop the pending return state; ``True`` when one was active."""

        with self._lock:
            if not self._returning:
                return False
            self._returning = False
            self._return_slot = -1
            self._return_total = 0
            self._return_index = 0
            return True

    @property
    def returning(self) -> bool:
        with self._lock:
            return self._returning

    @property
    def return_slot(self) -> int:
        with self._lock:
            return self._return_slot

    @property
    def return_total(self) -> int:
        with self._lock:
            return self._return_total

    @property
    def return_index(self) -> int:
        with self._lock:
            return self._return_index

    @property
    def pending_batches(self) -> int:
        with self._lock:
            if not self._returning:
                return 0
            return max(0, self._return_total - self._return_index)

    # -- journeys -------------------------------------------------------

    @property
    def journey_mutations(self) -> int:
        with self._lock:
            return self._journey_mutations

    @property
    def journey_seed(self) -> int:
        with self._lock:
            return self._journey_seed

    def start_journey(self, seed: int | None = None) -> bool:
        """Reset the counter for a new journey; requires an active home."""

        if self.home is None:
            return False
        value = self.config.seed if seed is None else clamp_seed(seed)
        with self._lock:
            self._journey_seed = value
            self._journey_mutations = 0
        return True

    def note_mutation(self) -> int:
        """Count one applied mutation; returns the new counter."""

        with self._lock:
            self._journey_mutations += 1
            return self._journey_mutations

    def reset_journey(self) -> None:
        with self._lock:
            self._journey_mutations = 0

    def journey_due(self) -> bool:
        """True when auto is on and the journey has reached ``every``."""

        with self._lock:
            return (
                self.config.auto
                and self._active_slot >= 0
                and self._journey_mutations >= self.config.every
            )

    # -- status ---------------------------------------------------------

    def state(self, current: Any = None) -> HomeState:
        """Build the immutable ``/ji/home/state`` snapshot."""

        with self._lock:
            home = self._slots[self._active_slot] if self._active_slot >= 0 else None
            home_classes = 0 if home is None else len(home.ratios)
            if home is None or current is None:
                distance = 0
            else:
                distance = self._planner.distance(current, home)
            return HomeState(
                has_home=any(snapshot is not None for snapshot in self._slots),
                active_slot=self._active_slot,
                home_classes=home_classes,
                distance=distance,
                pending_batches=(
                    max(0, self._return_total - self._return_index)
                    if self._returning
                    else 0
                ),
                history_depth=len(self._history),
                auto=self.config.auto,
                every=self.config.every,
                seed=self._journey_seed,
                drift=self.config.max_drift,
                returning=self._returning,
                journey_mutations=self._journey_mutations,
                slot_mask=sum(
                    1 << index
                    for index, snapshot in enumerate(self._slots)
                    if snapshot is not None
                ),
            )


__all__ = [
    "AUTO_DEFAULT",
    "DEFAULT_CONFIG_PATH",
    "DEFAULT_MIN_SIZE",
    "DEFAULT_SLOTS",
    "DRIFT_DEFAULT",
    "DRIFT_MAX",
    "DRIFT_MIN",
    "EVERY_DEFAULT",
    "EVERY_MAX",
    "EVERY_MIN",
    "HISTORY_DEPTH_DEFAULT",
    "HISTORY_DEPTH_MAX",
    "HISTORY_DEPTH_MIN",
    "HomeConfig",
    "HomeManager",
    "HomePlanner",
    "HomeState",
    "MAX_DRIFT_TRIES",
    "RETURN_GLIDE_DEFAULT",
    "RETURN_GLIDE_MAX",
    "RETURN_GLIDE_MIN",
    "SEED_DEFAULT",
    "SEED_MAX",
    "SEED_MIN",
    "SLOT_NAMES",
    "clamp_drift",
    "clamp_every",
    "clamp_history_depth",
    "clamp_return_glide",
    "clamp_seed",
    "plan_return",
    "slot_name",
    "validate_slot",
]
