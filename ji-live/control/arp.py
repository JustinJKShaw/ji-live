"""Two-group monophonic set arpeggiator for the Stage 6 MIDI rig.

Each regeneration picks a *shared* ratio from the current frequency set and
builds two three-note groups::

    group A = [shared, a1, a2]
    group B = [shared, b1, b2]

The shared note is the exact same :class:`ArpNote` object (same ratio, same
octave offset, same frequency) in both groups.  For set sizes >= 5 the four
non-shared slots are four distinct classes, so A and B share exactly one ratio.
Below 5 classes the non-shared pool is reused; the arp still runs, but
exact-one-shared/four-distinct is only guaranteed for set sizes >= 5.

Default playback order is ``forward``::

    A1, A2, A3, B1, B2, B3, A1, A2, A3, ...

with the shipped 0.25 s step and 0.20 s gate.  Stage 8.3b adds mutable
parameters without changing those defaults:

===============  ============================================  =============
Setting          Range / values                               Rebuilds?
===============  ============================================  =============
``step_seconds`` 0.05 .. 2.0 s (free-run, not tempo-synced)     no
``gate_seconds`` 0.01 .. 2.0 s, clamped to ``<= step``          no
``velocity``     1 .. 127                                       no
``octave_offsets`` three ints -8 .. +8, positional              yes
``shared_mode``  ``anchor`` | ``random``                        yes
``order``        ``forward``/``reverse``/``pingpong``/``random``  order only
``seed``         0 .. 2**31-1                                  yes
===============  ============================================  =============

Order definitions over the stored six steps ``[A1, A2, A3, B1, B2, B3]``
(indices 0-5):

* ``forward``  -- ``0,1,2,3,4,5`` (the Stage 6 behavior).
* ``reverse``  -- ``5,4,3,2,1,0``.
* ``pingpong`` -- ``0,1,2,3,4,5,4,3,2,1`` (ten steps; the endpoints are not
  repeated back to back).
* ``random``   -- a seeded permutation of ``0..5`` derived from the seed and the
  stored six-step pattern; the same seed plus the same pattern always produces
  the same order.

Any pattern/order/seed change resets the step cursor to the first element of the
new order.  It never cuts or retriggers the currently held note: the change is
stored immediately and the next trigger uses it.  ``step``/``gate``/``velocity``
changes also apply from the next trigger.

Timing uses monotonic, drift-corrected deadlines.  If the thread falls behind it
resumes from *now* instead of bursting through the missed steps.  Every exit
path note-offs a held note and :meth:`SetArp.stop` sends All Notes Off.
"""

from __future__ import annotations

import hashlib
import math
import random
import threading
import time
from dataclasses import dataclass, replace
from fractions import Fraction
from typing import Any

try:  # Support both ``python -m control.arp`` and direct execution.
    from .mpe_output import MPEOutput, MPEPitch
except ImportError:  # pragma: no cover - exercised only by direct execution
    from mpe_output import MPEOutput, MPEPitch

DEFAULT_STEP_SECONDS = 0.25
DEFAULT_GATE_SECONDS = 0.20
DEFAULT_OCTAVE_OFFSETS = (0, 1, 2)
DEFAULT_SHARED_MODE = "anchor"
DEFAULT_ORDER = "forward"
DEFAULT_SEED = 1
DEFAULT_VELOCITY = 90
DEFAULT_VERBOSE_STEPS = False
DEFAULT_SYNC_MODE = "free"
DEFAULT_DIVISION = "1/8"
DEFAULT_EVOLVE = False
DEFAULT_EVOLVE_BEATS = 4
DEFAULT_EVOLVE_DEPTH = 0.0
DEFAULT_BPM = 120.0

STEP_MIN = 0.05
STEP_MAX = 2.0
GATE_MIN = 0.01
GATE_MAX = 2.0
OCTAVE_MIN = -8
OCTAVE_MAX = 8
VELOCITY_MIN = 1
VELOCITY_MAX = 127
SEED_MAX = 2**31 - 1

SHARED_MODES: tuple[str, ...] = ("anchor", "random")
ORDERS: tuple[str, ...] = ("forward", "reverse", "pingpong", "random")

#: Stage 8.3c beat sync.  The arp is free-running by default; in ``beat`` mode
#: the step is one of these divisions of the bridge's quarter-note BPM.
SYNC_MODES: tuple[str, ...] = ("free", "beat")
DIVISIONS: tuple[str, ...] = ("1/4", "1/8", "1/16", "1/8T")
#: Length of one division in quarter notes (a beat is one quarter note).
DIVISION_QUARTERS: dict[str, float] = {
    "1/4": 1.0,
    "1/8": 0.5,
    "1/16": 0.25,
    "1/8T": 1.0 / 3.0,
}

#: Stage 8.3c self-evolution: regenerate the pattern every N quarter beats.
EVOLVE_BEATS_MIN = 1
EVOLVE_BEATS_MAX = 64
BPM_MIN = 20.0
BPM_MAX = 300.0

#: ``pingpong`` visits the six stored steps forward then back without repeating
#: either endpoint back to back.
PINGPONG_INDICES: tuple[int, ...] = (0, 1, 2, 3, 4, 5, 4, 3, 2, 1)

_GROUP_SIZE = 3
_PATTERN_STEPS = 6

#: Setting names that rebuild the stored pattern (and therefore reset the
#: cursor).  ``step``/``gate``/``velocity`` only affect the next trigger.
_PATTERN_KEYS = frozenset({"octave_offsets", "shared_mode", "seed"})
_ORDER_KEYS = frozenset({"order"})
#: Setting names that restart the self-evolution countdown.
_EVOLVE_KEYS = frozenset({"evolve", "evolve_beats", "evolve_depth", "seed"})

_SETTING_ALIASES = {
    "step": "step_seconds",
    "step_seconds": "step_seconds",
    "gate": "gate_seconds",
    "gate_seconds": "gate_seconds",
    "velocity": "velocity",
    "octaves": "octave_offsets",
    "octave_offsets": "octave_offsets",
    "shared": "shared_mode",
    "shared_mode": "shared_mode",
    "order": "order",
    "seed": "seed",
    "sync": "sync_mode",
    "sync_mode": "sync_mode",
    "division": "division",
    "evolve": "evolve",
    "evolve_beats": "evolve_beats",
    "evolve_every": "evolve_beats",
    "evolve_depth": "evolve_depth",
    "depth": "evolve_depth",
}


@dataclass(frozen=True, slots=True)
class ArpNote:
    """One arp event: a ratio, its octave offset, and the resulting frequency."""

    ratio: float
    octave: int
    frequency: float


@dataclass(frozen=True, slots=True)
class ArpPattern:
    """Two three-note groups plus the shared note they have in common."""

    group_a: tuple[ArpNote, ArpNote, ArpNote]
    group_b: tuple[ArpNote, ArpNote, ArpNote]
    shared: ArpNote

    @property
    def steps(self) -> tuple[ArpNote, ...]:
        """The six-step A/B sequence."""

        return self.group_a + self.group_b

    @property
    def shared_ratio(self) -> float:
        return self.shared.ratio

    @property
    def shared_frequency(self) -> float:
        return self.shared.frequency


def build_groups(
    ratios: list[float] | tuple[float, ...],
    anchor: float | None,
    fundamental: float,
    rng: random.Random | None = None,
    shared_mode: str = DEFAULT_SHARED_MODE,
    octave_offsets: tuple[int, int, int] | list[int] = DEFAULT_OCTAVE_OFFSETS,
) -> ArpPattern:
    """Build one two-group arp pattern from a snapshot of the frequency set.

    Args:
        ratios: Current octave-reduced classes (unique, 1..12 values).
        anchor: Current anchor ratio, or ``None``.
        fundamental: Engine fundamental in Hz.
        rng: Dedicated ``random.Random``; a fresh one is created if omitted.
        shared_mode: ``"anchor"`` (use the anchor if it is in the set, else a
            random class) or ``"random"``.
        octave_offsets: Positional offsets ``(shared, non_shared_1,
            non_shared_2)``.  The shared offset is identical in both groups.

    Returns:
        A frozen :class:`ArpPattern`.
    """

    ratio_list = _clean_ratios(ratios)
    fund = _positive_float(fundamental, "fundamental")
    if rng is None:
        rng = random.Random(DEFAULT_SEED)
    if shared_mode not in SHARED_MODES:
        raise ValueError("shared_mode must be 'anchor' or 'random'")
    offsets = _clean_offsets(octave_offsets)

    shared = _choose_shared(ratio_list, anchor, rng, shared_mode)
    non_shared = [ratio for ratio in ratio_list if ratio != shared]

    # Four distinct non-shared classes are possible only when the set has at
    # least five classes.  For smaller sets, cycle through what is available.
    if len(non_shared) >= 4:
        picks = rng.sample(non_shared, 4)
    else:
        pool = list(non_shared) or [shared]
        rng.shuffle(pool)
        picks = [pool[index % len(pool)] for index in range(4)]

    shared_note = _make_note(shared, offsets[0], fund)
    group_a = (
        shared_note,
        _make_note(picks[0], offsets[1], fund),
        _make_note(picks[1], offsets[2], fund),
    )
    group_b = (
        shared_note,
        _make_note(picks[2], offsets[1], fund),
        _make_note(picks[3], offsets[2], fund),
    )
    return ArpPattern(group_a=group_a, group_b=group_b, shared=shared_note)


# Common alternative name for callers/tests that think "generate".
generate_groups = build_groups


# ---------------------------------------------------------------------------
# Validation
# ---------------------------------------------------------------------------


def validate_step_seconds(value: object) -> float:
    """Return the step duration clamped to ``0.05 .. 2.0`` seconds."""

    return _clamped_float(value, "step_seconds", STEP_MIN, STEP_MAX)


def validate_gate_seconds(value: object, step_seconds: float = STEP_MAX) -> float:
    """Return the gate clamped to ``0.01 .. min(2.0, step_seconds)``."""

    step = _clamped_float(step_seconds, "step_seconds", STEP_MIN, STEP_MAX)
    return _clamped_float(
        value, "gate_seconds", GATE_MIN, min(GATE_MAX, step)
    )


def validate_velocity(value: object) -> int:
    """Return the note-on velocity clamped to ``1 .. 127``."""

    number = _as_int(value, "velocity")
    return max(VELOCITY_MIN, min(VELOCITY_MAX, number))


def validate_octave_offsets(value: object) -> tuple[int, int, int]:
    """Return three positional octave offsets, each clamped to ``-8 .. 8``."""

    if isinstance(value, (str, bytes)):
        raise TypeError("octave_offsets must contain three integers")
    try:
        values = list(value)  # type: ignore[arg-type]
    except TypeError:
        raise TypeError("octave_offsets must contain three integers") from None
    if len(values) != 3:
        raise ValueError("octave_offsets must contain exactly three integers")
    cleaned = [max(OCTAVE_MIN, min(OCTAVE_MAX, _as_int(item, "octave offset")))
               for item in values]
    return cleaned[0], cleaned[1], cleaned[2]


def validate_shared_mode(value: object) -> str:
    """Return ``anchor`` or ``random``."""

    text = str(value).strip().lower()
    if text not in SHARED_MODES:
        raise ValueError("shared_mode must be 'anchor' or 'random'")
    return text


def validate_order(value: object) -> str:
    """Return one of ``forward``, ``reverse``, ``pingpong``, ``random``."""

    text = str(value).strip().lower()
    if text not in ORDERS:
        raise ValueError("order must be one of " + ", ".join(ORDERS))
    return text


def validate_seed(value: object) -> int:
    """Return an integer seed clamped to ``0 .. 2**31-1``."""

    return max(0, min(SEED_MAX, _as_int(value, "seed")))


def validate_sync_mode(value: object) -> str:
    """Return ``free`` (default) or ``beat``.

    Accepts the strings ``free``/``beat`` (plus ``off``/``on``, ``sync``) and
    booleans/0/1 where ``True`` means ``beat``.
    """

    if isinstance(value, bool):
        return "beat" if value else "free"
    if isinstance(value, int) and value in (0, 1):
        return "beat" if value else "free"
    if isinstance(value, str):
        text = value.strip().lower()
        if text in ("beat", "sync", "synced", "on", "true", "1"):
            return "beat"
        if text in ("free", "off", "false", "0"):
            return "free"
    raise ValueError("sync_mode must be 'free' or 'beat'")


def validate_division(value: object) -> str:
    """Return one of the beat divisions (``1/4``, ``1/8``, ``1/16``, ``1/8T``)."""

    text = str(value).strip().upper().replace(" ", "")
    if text not in DIVISION_QUARTERS:
        raise ValueError("division must be one of " + ", ".join(DIVISIONS))
    return text


def _as_bool(value: object, label: str) -> bool:
    if isinstance(value, bool):
        return value
    if isinstance(value, int) and value in (0, 1):
        return bool(value)
    if isinstance(value, float) and value in (0.0, 1.0):
        return bool(value)
    raise TypeError(f"{label} must be a boolean or 0/1")


def validate_evolve(value: object) -> bool:
    """Accept a bool or 0/1 for the evolve switch."""

    return _as_bool(value, "evolve")


def validate_evolve_beats(value: object) -> int:
    """Return the evolution period in quarter beats, 1..64."""

    number = _as_int(value, "evolve_beats")
    return max(EVOLVE_BEATS_MIN, min(EVOLVE_BEATS_MAX, number))


def validate_evolve_depth(value: object) -> float:
    """Return the evolution depth clamped to 0..1."""

    return max(0.0, min(1.0, _finite(value, "evolve_depth")))


def division_quarters(value: object) -> float:
    """Quarter notes per step for a division name."""

    return DIVISION_QUARTERS[validate_division(value)]


def normalize_setting_name(name: object) -> str:
    """Map a short or long setting name to its canonical field name."""

    key = str(name).strip().lower()
    canonical = _SETTING_ALIASES.get(key)
    if canonical is None:
        raise ValueError(
            f"unknown arp setting {name!r}; expected step, gate, velocity, "
            "octaves, shared_mode, order, seed"
        )
    return canonical


def order_indices(
    order: str,
    seed: int = DEFAULT_SEED,
    pattern: ArpPattern | None = None,
) -> tuple[int, ...]:
    """Return the visiting order for ``order``; see the module docstring.

    ``random`` needs the seed and (when available) the stored pattern so the
    same seed plus the same pattern reproduces the same permutation.
    """

    name = validate_order(order)
    if name == "forward":
        return tuple(range(_PATTERN_STEPS))
    if name == "reverse":
        return tuple(range(_PATTERN_STEPS - 1, -1, -1))
    if name == "pingpong":
        return PINGPONG_INDICES
    return _random_order(int(seed), pattern)


def _random_order(seed: int, pattern: ArpPattern | None = None) -> tuple[int, ...]:
    """Deterministic seeded permutation of ``0..5`` for ``order == random``."""

    key = "" if pattern is None else "|".join(
        f"{note.ratio:.9f}@{note.octave}" for note in pattern.steps
    )
    digest = hashlib.sha256(f"{int(seed)}:{key}".encode("utf-8")).digest()
    indices = list(range(_PATTERN_STEPS))
    random.Random(int.from_bytes(digest[:8], "big")).shuffle(indices)
    return tuple(indices)


# ---------------------------------------------------------------------------
# Settings
# ---------------------------------------------------------------------------


@dataclass(frozen=True, slots=True)
class ArpSettings:
    """Validated, immutable Stage 8.3b arp settings.

    The shipped defaults reproduce the Stage 6 arp exactly.  Values are
    immutable-by-replace so the arp thread can read one consistent snapshot.
    """

    step_seconds: float = DEFAULT_STEP_SECONDS
    gate_seconds: float = DEFAULT_GATE_SECONDS
    velocity: int = DEFAULT_VELOCITY
    octave_offsets: tuple[int, int, int] = DEFAULT_OCTAVE_OFFSETS
    shared_mode: str = DEFAULT_SHARED_MODE
    order: str = DEFAULT_ORDER
    seed: int = DEFAULT_SEED
    sync_mode: str = DEFAULT_SYNC_MODE
    division: str = DEFAULT_DIVISION
    evolve: bool = DEFAULT_EVOLVE
    evolve_beats: int = DEFAULT_EVOLVE_BEATS
    evolve_depth: float = DEFAULT_EVOLVE_DEPTH

    @classmethod
    def create(cls, **values: Any) -> "ArpSettings":
        """Build settings from any subset of fields, clamped and validated."""

        defaults = cls()
        step = validate_step_seconds(
            values.get("step_seconds", defaults.step_seconds)
        )
        gate = validate_gate_seconds(
            values.get("gate_seconds", defaults.gate_seconds), step
        )
        return cls(
            step_seconds=step,
            gate_seconds=gate,
            velocity=validate_velocity(values.get("velocity", defaults.velocity)),
            octave_offsets=validate_octave_offsets(
                values.get("octave_offsets", defaults.octave_offsets)
            ),
            shared_mode=validate_shared_mode(
                values.get("shared_mode", defaults.shared_mode)
            ),
            order=validate_order(values.get("order", defaults.order)),
            seed=validate_seed(values.get("seed", defaults.seed)),
            sync_mode=validate_sync_mode(
                values.get("sync_mode", defaults.sync_mode)
            ),
            division=validate_division(
                values.get("division", defaults.division)
            ),
            evolve=validate_evolve(values.get("evolve", defaults.evolve)),
            evolve_beats=validate_evolve_beats(
                values.get("evolve_beats", defaults.evolve_beats)
            ),
            evolve_depth=validate_evolve_depth(
                values.get("evolve_depth", defaults.evolve_depth)
            ),
        )

    def with_change(self, name: object, value: object) -> "ArpSettings":
        """Return settings with one validated change applied."""

        key = normalize_setting_name(name)
        if key == "step_seconds":
            step = validate_step_seconds(value)
            gate = min(self.gate_seconds, step)
            return replace(self, step_seconds=step, gate_seconds=gate)
        if key == "gate_seconds":
            return replace(self, gate_seconds=validate_gate_seconds(
                value, self.step_seconds
            ))
        if key == "velocity":
            return replace(self, velocity=validate_velocity(value))
        if key == "octave_offsets":
            return replace(self, octave_offsets=validate_octave_offsets(value))
        if key == "shared_mode":
            return replace(self, shared_mode=validate_shared_mode(value))
        if key == "order":
            return replace(self, order=validate_order(value))
        if key == "seed":
            return replace(self, seed=validate_seed(value))
        if key == "sync_mode":
            return replace(self, sync_mode=validate_sync_mode(value))
        if key == "division":
            return replace(self, division=validate_division(value))
        if key == "evolve":
            return replace(self, evolve=validate_evolve(value))
        if key == "evolve_beats":
            return replace(self, evolve_beats=validate_evolve_beats(value))
        return replace(self, evolve_depth=validate_evolve_depth(value))

    @property
    def octaves(self) -> tuple[int, int, int]:
        """Alias used by the GUI/status layer."""

        return self.octave_offsets

    def effective_step_seconds(self, bpm: float = DEFAULT_BPM) -> float:
        """The step the thread actually uses right now.

        ``free`` returns ``step_seconds``; ``beat`` derives it from the BPM and
        the division (e.g. 1/8 at 120 BPM = 0.25 s).
        """

        if self.sync_mode != "beat":
            return float(self.step_seconds)
        beat = 60.0 / max(BPM_MIN, min(BPM_MAX, float(bpm)))
        return max(STEP_MIN, min(STEP_MAX, beat * division_quarters(self.division)))

    def effective_gate_seconds(self, bpm: float = DEFAULT_BPM) -> float:
        """The gate in effect: the requested gate, clamped to the live step."""

        step = self.effective_step_seconds(bpm)
        return max(GATE_MIN, min(step, float(self.gate_seconds)))

    def state_args(
        self, enabled: bool, port_ok: bool, bpm: float = DEFAULT_BPM
    ) -> list[Any]:
        """``/ji/arp/state`` payload (seventeen arguments, documented order)."""

        shared, non_shared_1, non_shared_2 = self.octave_offsets
        step = self.effective_step_seconds(bpm)
        return [
            int(bool(enabled)),
            int(bool(port_ok)),
            float(self.step_seconds),
            float(step),
            float(max(GATE_MIN, min(step, float(self.gate_seconds)))),
            int(self.velocity),
            int(shared),
            int(non_shared_1),
            int(non_shared_2),
            str(self.shared_mode),
            str(self.order),
            int(self.seed),
            str(self.sync_mode),
            str(self.division),
            int(bool(self.evolve)),
            int(self.evolve_beats),
            float(self.evolve_depth),
        ]

    def describe(self) -> str:
        """One compact ``key=value`` line used by the ``[ARP] state`` log."""

        shared, non_shared_1, non_shared_2 = self.octave_offsets
        sync = (
            f"sync={self.sync_mode}"
            if self.sync_mode == "free"
            else f"sync={self.sync_mode}:{self.division}"
        )
        evolve = (
            f" evolve={int(self.evolve_beats)}b/{self.evolve_depth:.2f}"
            if self.evolve
            else ""
        )
        return (
            f"step={self.step_seconds:.3f} gate={self.gate_seconds:.3f} "
            f"velocity={self.velocity} "
            f"octaves={shared},{non_shared_1},{non_shared_2} "
            f"shared={self.shared_mode} order={self.order} seed={self.seed} "
            f"{sync}{evolve}"
        )


# ---------------------------------------------------------------------------
# Runtime
# ---------------------------------------------------------------------------


class SetArp:
    """Continuous monophonic arp driven by set-update snapshots.

    Args:
        output: An :class:`~control.mpe_output.MPEOutput` (or compatible fake).
        step_seconds: Step duration (default 0.25 s), clamped to 0.05..2.0.
        gate_seconds: Note-on duration within each step (default 0.20 s),
            clamped to 0.01..step.
        octave_offsets: Positional octave offsets (default ``(0, 1, 2)``).
        shared_mode: ``"anchor"`` or ``"random"``.
        seed: Dedicated RNG seed; the same seed reproduces the pattern.
        velocity: Note-on velocity (default 90).
        order: ``forward`` (default), ``reverse``, ``pingpong``, or ``random``.
        verbose_steps: Print the per-step ``[ARP] step=...`` line (opt-in; it is
            off by default so a performance log is not flooded).
    """

    def __init__(
        self,
        output: MPEOutput,
        step_seconds: float = DEFAULT_STEP_SECONDS,
        gate_seconds: float = DEFAULT_GATE_SECONDS,
        octave_offsets: tuple[int, int, int] | list[int] = DEFAULT_OCTAVE_OFFSETS,
        shared_mode: str = DEFAULT_SHARED_MODE,
        seed: int = DEFAULT_SEED,
        velocity: int = DEFAULT_VELOCITY,
        order: str = DEFAULT_ORDER,
        verbose_steps: bool = DEFAULT_VERBOSE_STEPS,
        sync_mode: str = DEFAULT_SYNC_MODE,
        division: str = DEFAULT_DIVISION,
        evolve: bool = DEFAULT_EVOLVE,
        evolve_beats: int = DEFAULT_EVOLVE_BEATS,
        evolve_depth: float = DEFAULT_EVOLVE_DEPTH,
        bpm: float = DEFAULT_BPM,
    ) -> None:
        self.output = output
        self._settings = ArpSettings.create(
            step_seconds=step_seconds,
            gate_seconds=gate_seconds,
            octave_offsets=octave_offsets,
            shared_mode=shared_mode,
            seed=seed,
            velocity=velocity,
            order=order,
            sync_mode=sync_mode,
            division=division,
            evolve=evolve,
            evolve_beats=evolve_beats,
            evolve_depth=evolve_depth,
        )
        self.verbose_steps = bool(verbose_steps)

        self._rng = random.Random(self._settings.seed)
        self._lock = threading.RLock()
        self._stop = threading.Event()
        self._snapshot: tuple[list[float], float | None, float] | None = None
        self._pattern: ArpPattern | None = None
        self._order: tuple[int, ...] = order_indices(
            self._settings.order, seed=self._settings.seed
        )
        self._step_index = 0
        self._last_signature: tuple | None = None
        self._log_next_step = False
        self._thread: threading.Thread | None = None
        # Stage 8.3c runtime: beat sync and self-evolution.
        self._bpm = max(BPM_MIN, min(BPM_MAX, float(bpm)))
        self._transport_running = False
        self._realign_at: float | None = None
        self._evolve_at: float | None = None
        self._evolve_count = 0
        self._last_evolve_at: float | None = None

    # ------------------------------------------------------------------
    # State
    # ------------------------------------------------------------------

    @property
    def settings(self) -> ArpSettings:
        with self._lock:
            return self._settings

    @property
    def pattern(self) -> ArpPattern | None:
        with self._lock:
            return self._pattern

    @property
    def groups(self) -> tuple[tuple[ArpNote, ...], tuple[ArpNote, ...]] | None:
        with self._lock:
            pattern = self._pattern
        if pattern is None:
            return None
        return pattern.group_a, pattern.group_b

    @property
    def shared_ratio(self) -> float | None:
        with self._lock:
            pattern = self._pattern
        return None if pattern is None else pattern.shared_ratio

    @property
    def order(self) -> str:
        with self._lock:
            return self._settings.order

    @property
    def order_sequence(self) -> tuple[int, ...]:
        """The stored visiting order (indices into the six pattern steps)."""

        with self._lock:
            return self._order

    @property
    def step_seconds(self) -> float:
        with self._lock:
            return self._settings.step_seconds

    @property
    def gate_seconds(self) -> float:
        with self._lock:
            return self._settings.gate_seconds

    @property
    def velocity(self) -> int:
        with self._lock:
            return self._settings.velocity

    @property
    def octave_offsets(self) -> tuple[int, int, int]:
        with self._lock:
            return self._settings.octave_offsets

    @property
    def shared_mode(self) -> str:
        with self._lock:
            return self._settings.shared_mode

    @property
    def seed(self) -> int:
        with self._lock:
            return self._settings.seed

    @property
    def snapshot(self) -> tuple[list[float], float | None, float] | None:
        with self._lock:
            return self._snapshot

    @property
    def is_running(self) -> bool:
        thread = self._thread
        return thread is not None and thread.is_alive()

    @property
    def bpm(self) -> float:
        with self._lock:
            return self._bpm

    @property
    def sync_mode(self) -> str:
        with self._lock:
            return self._settings.sync_mode

    @property
    def division(self) -> str:
        with self._lock:
            return self._settings.division

    @property
    def evolve(self) -> bool:
        with self._lock:
            return bool(self._settings.evolve)

    @property
    def evolve_count(self) -> int:
        """How many times the pattern evolved on its own since construction."""

        with self._lock:
            return self._evolve_count

    @property
    def effective_step_seconds(self) -> float:
        with self._lock:
            return self._settings.effective_step_seconds(self._bpm)

    @property
    def effective_gate_seconds(self) -> float:
        with self._lock:
            return self._settings.effective_gate_seconds(self._bpm)

    @property
    def transport_running(self) -> bool:
        with self._lock:
            return self._transport_running

    def state_args(self, enabled: bool, port_ok: bool) -> list[Any]:
        """``/ji/arp/state`` payload for the current settings."""

        with self._lock:
            return self._settings.state_args(enabled, port_ok, self._bpm)

    # ------------------------------------------------------------------
    # Set updates
    # ------------------------------------------------------------------

    def on_set(
        self,
        ratios: list[float] | tuple[float, ...],
        anchor: float | None,
        fundamental: float,
    ) -> ArpPattern:
        """Regenerate the pattern from a snapshot; safe to call before start.

        The new pattern is stored immediately but only takes effect at the next
        step boundary.  No MIDI is sent here, so a held note is not cut.
        """

        with self._lock:
            self._snapshot = (list(ratios), anchor, float(fundamental))
            pattern = self._rebuild_pattern_locked()
            if pattern is None:  # defensive: a snapshot was just stored
                raise RuntimeError("arp pattern regeneration failed")
            self._order = self._order_locked()
            self._step_index = 0
            self._log_next_step = True
            signature = _pattern_signature(pattern)
            changed = signature != self._last_signature
            self._last_signature = signature

        if changed:
            _print_pattern(pattern)
        return pattern

    # Aliases used by callers that think in set-update / regeneration terms.
    update = on_set
    regenerate = on_set

    # ------------------------------------------------------------------
    # Stage 8.3b settings
    # ------------------------------------------------------------------

    def set_setting(self, name: object, value: object) -> ArpSettings:
        """Apply one validated setting change and return the new settings.

        The change is stored immediately; it takes effect at the next trigger
        (or, for pitch settings, at the next step with a rebuilt pattern).  The
        held note is never cut or retriggered here.
        """

        key = normalize_setting_name(name)
        with self._lock:
            before = self._settings
            after = before.with_change(key, value)
            if after == before:
                return before
            note = _clamp_note(key, before, after, value)
            self._settings = after
            if key == "seed":
                # A new seed restarts the pattern RNG stream so the same seed
                # always reproduces the same pattern.
                self._rng = random.Random(after.seed)
                self._last_signature = None
            if key in _PATTERN_KEYS:
                self._rebuild_pattern_locked()
            if key in _PATTERN_KEYS or key in _ORDER_KEYS or key == "seed":
                self._order = self._order_locked()
                self._step_index = 0
            if key in _EVOLVE_KEYS:
                # Restart the countdown so a new rate/depth starts fresh.
                self._evolve_at = self._next_evolve_at_locked()
            self._log_next_step = True

        print(f"[ARP] change applied: {key}={_fmt_value(after, key)}")
        if note is not None:
            print(f"[ARP] {note}")
        print(f"[ARP] state {after.describe()}")
        return after

    def set_settings(self, settings: ArpSettings) -> ArpSettings:
        """Replace every setting at once (used by tests and config reloads)."""

        if not isinstance(settings, ArpSettings):
            raise TypeError("settings must be an ArpSettings")
        with self._lock:
            before = self._settings
            if settings == before:
                return before
            self._settings = settings
            if settings.seed != before.seed:
                self._rng = random.Random(settings.seed)
                self._last_signature = None
            self._rebuild_pattern_locked()
            self._order = self._order_locked()
            self._step_index = 0
            self._log_next_step = True
        print(f"[ARP] state {settings.describe()}")
        return settings

    # Explicit setters, thin wrappers over set_setting().
    def set_step_seconds(self, value: object) -> ArpSettings:
        return self.set_setting("step_seconds", value)

    def set_gate_seconds(self, value: object) -> ArpSettings:
        return self.set_setting("gate_seconds", value)

    def set_velocity(self, value: object) -> ArpSettings:
        return self.set_setting("velocity", value)

    def set_octave_offsets(self, value: object) -> ArpSettings:
        return self.set_setting("octave_offsets", value)

    def set_shared_mode(self, value: object) -> ArpSettings:
        return self.set_setting("shared_mode", value)

    def set_order(self, value: object) -> ArpSettings:
        return self.set_setting("order", value)

    def set_seed(self, value: object) -> ArpSettings:
        return self.set_setting("seed", value)

    def set_verbose_steps(self, enabled: bool) -> bool:
        self.verbose_steps = bool(enabled)
        return self.verbose_steps

    # ------------------------------------------------------------------
    # Stage 8.3c beat sync / transport
    # ------------------------------------------------------------------

    def set_bpm(self, bpm: object) -> float:
        """Set the quarter-note BPM used by ``beat`` sync and the evolve clock.

        A tempo change keeps the current phase (the step duration is derived
        from the tempo, so the next step simply lands later or earlier) and
        restarts the evolution countdown.
        """

        value = max(BPM_MIN, min(BPM_MAX, _finite(bpm, "bpm")))
        with self._lock:
            if value == self._bpm:
                return self._bpm
            self._bpm = value
            self._evolve_at = self._next_evolve_at_locked()
        return value

    def set_transport(self, running: object) -> bool:
        """Record the transport state (used to explain the sync phase)."""

        value = _as_bool(running, "transport")
        with self._lock:
            self._transport_running = bool(value)
        return bool(value)

    def sync_phase(self, delay: float = 0.0) -> None:
        """Snap the next step to a beat boundary ``delay`` seconds from now.

        The bridge calls this when beat sync is turned on, the division
        changes, the transport starts, or the arp is re-enabled.  The cursor
        restarts at the first element of the order; a held note is not cut, the
        snap happens at the next step boundary.
        """

        try:
            seconds = max(0.0, float(delay))
        except (TypeError, ValueError):
            raise TypeError("delay must be a real number") from None
        if not math.isfinite(seconds):
            raise ValueError("delay must be finite")
        with self._lock:
            self._realign_at = time.monotonic() + seconds

    def evolve_now(self) -> int:
        """Regenerate the pattern immediately, as one evolution would.

        Returns the number of evolutions performed (0 when there is no set
        snapshot yet).  Used by tests and handy for a future "evolve now"
        control; the timed evolution uses the same code path.
        """

        with self._lock:
            before = self._evolve_count
            self._evolve_locked()
            self._evolve_at = self._next_evolve_at_locked()
            self._step_index = 0
            return self._evolve_count - before

    def reset_evolution(self) -> None:
        """Restart the evolution countdown without changing the settings."""

        with self._lock:
            self._evolve_at = self._next_evolve_at_locked()

    @property
    def next_evolution_in(self) -> float:
        """Seconds until the next self-evolution, or ``-1`` when disabled."""

        with self._lock:
            if not self._settings.evolve or self._evolve_at is None:
                return -1.0
            return max(0.0, self._evolve_at - time.monotonic())

    # ------------------------------------------------------------------
    # Thread lifecycle
    # ------------------------------------------------------------------

    def start(self) -> None:
        """Start the arp thread.  Idempotent while already running."""

        with self._lock:
            if self._thread is not None and self._thread.is_alive():
                return
            self._stop.clear()
            self._evolve_at = self._next_evolve_at_locked()
            thread = threading.Thread(
                target=self._run,
                name="ji-set-arp",
                daemon=True,
            )
            self._thread = thread
            thread.start()

    def stop(self) -> None:
        """Stop the thread, note-off any held note, and send All Notes Off."""

        self._stop.set()
        thread = self._thread
        if thread is not None and thread is not threading.current_thread():
            thread.join(timeout=2.0)
        with self._lock:
            if self._thread is thread:
                self._thread = None
        self.output.all_notes_off()

    # ------------------------------------------------------------------
    # Thread body
    # ------------------------------------------------------------------

    def _run(self) -> None:  # pragma: no cover - exercised through fake ports
        deadline = time.monotonic()
        held_note: int | None = None

        try:
            while not self._stop.is_set():
                # 1. A pending beat snap wins: wait for the boundary, restart
                #    the order at position 0, and take the next step from there.
                with self._lock:
                    realign_at = self._realign_at
                if realign_at is not None:
                    now = time.monotonic()
                    if realign_at > now and self._stop.wait(realign_at - now):
                        break
                    with self._lock:
                        self._realign_at = None
                        self._step_index = 0
                        self._log_next_step = True
                    deadline = time.monotonic()

                # 2. Self-evolution (no mutation needed): rebuild the pattern
                #    from the stored set snapshot and restart the cycle.
                with self._lock:
                    evolve_at = self._evolve_at
                if evolve_at is not None and time.monotonic() >= evolve_at:
                    with self._lock:
                        self._evolve_locked()
                        self._evolve_at = self._next_evolve_at_locked()
                        self._step_index = 0
                        evolve_at = self._evolve_at
                    deadline = time.monotonic()

                with self._lock:
                    pattern = self._pattern
                    order = self._order
                    settings = self._settings
                    step = settings.effective_step_seconds(self._bpm)
                    gate = settings.effective_gate_seconds(self._bpm)
                    velocity = settings.velocity
                    verbose = self.verbose_steps
                    log_next = self._log_next_step
                    if pattern is None or not order:
                        note = None
                        step_number = 0
                    else:
                        position = self._step_index % len(order)
                        self._step_index = (position + 1) % len(order)
                        note = pattern.steps[order[position]]
                        step_number = position + 1
                        if log_next:
                            self._log_next_step = False
                        else:
                            log_next = False
                    # Re-read the two pending boundaries so the waits below
                    # never overshoot a beat snap or an evolution.
                    realign_at = self._realign_at
                    evolve_at = self._evolve_at

                if note is None:
                    deadline += step
                    wait = _clamp_wait(deadline, realign_at, evolve_at)
                    wait -= time.monotonic()
                    if wait > 0.0 and self._stop.wait(wait):
                        break
                    continue

                # A previous gate longer than the step would still be held.
                if held_note is not None:
                    self.output.note_off(held_note)
                    held_note = None

                pitch: MPEPitch = self.output.play(note.frequency, velocity)
                held_note = pitch.note

                if verbose:
                    print(
                        f"[ARP] step={step_number} freq={note.frequency:.2f} "
                        f"note={pitch.note} bend={pitch.bend}"
                    )
                if log_next:
                    print(
                        f"[ARP] first step after change: order={settings.order} "
                        f"position={step_number} freq={note.frequency:.2f} "
                        f"note={pitch.note}"
                    )

                if self._stop.wait(gate):
                    break
                self.output.note_off(held_note)
                held_note = None

                deadline += step
                now = time.monotonic()
                if deadline < now:
                    # Fell behind (slow output/JIT); resume from now, do not
                    # burst through missed steps.
                    deadline = now
                wait = _clamp_wait(deadline, realign_at, evolve_at) - now
                if wait > 0.0 and self._stop.wait(wait):
                    break
        finally:
            if held_note is not None:
                self.output.note_off(held_note)

    # ------------------------------------------------------------------
    # Internals (call with the lock held)
    #

    def _rebuild_pattern_locked(
        self, shared_mode: str | None = None
    ) -> ArpPattern | None:
        snapshot = self._snapshot
        if snapshot is None:
            # Settings can be changed before the first set update; keep the
            # stored (or absent) pattern and apply the settings on the next
            # on_set/start.
            return self._pattern
        ratios, anchor, fundamental = snapshot
        pattern = build_groups(
            ratios,
            anchor,
            fundamental,
            rng=self._rng,
            shared_mode=shared_mode or self._settings.shared_mode,
            octave_offsets=self._settings.octave_offsets,
        )
        self._pattern = pattern
        return pattern

    def _evolve_interval_locked(self) -> float:
        """Seconds between evolutions: ``evolve_beats`` quarter notes."""

        beat = 60.0 / max(BPM_MIN, min(BPM_MAX, float(self._bpm)))
        return max(0.05, float(self._settings.evolve_beats) * beat)

    def _next_evolve_at_locked(self, now: float | None = None) -> float | None:
        if not self._settings.evolve:
            return None
        if now is None:
            now = time.monotonic()
        return now + self._evolve_interval_locked()

    def _evolve_locked(self) -> None:
        """Regenerate the pattern on its own; call with the lock held.

        Depth 0 re-picks the four non-shared notes only (the shared note keeps
        following ``shared_mode``).  Higher depth may let the shared note move
        freely for that one evolution (probability = depth) and may nudge one
        octave offset by +/-1 (probability = depth/2).
        """

        if self._snapshot is None:
            return
        settings = self._settings
        self._evolve_count += 1
        count = self._evolve_count
        rng = random.Random(f"ji-arp-evolve|{settings.seed}|{count}")
        depth = float(settings.evolve_depth)

        shared_mode = settings.shared_mode
        if depth > 0.0 and rng.random() < depth:
            shared_mode = "random"

        offsets = settings.octave_offsets
        nudged: tuple[int, int] | None = None
        if depth > 0.0 and rng.random() < depth * 0.5:
            index = rng.randrange(3)
            delta = rng.choice((-1, 1))
            values = list(offsets)
            values[index] = max(
                OCTAVE_MIN, min(OCTAVE_MAX, values[index] + delta)
            )
            if tuple(values) != offsets:
                offsets = (values[0], values[1], values[2])
                nudged = (index, offsets[index])
        if offsets != settings.octave_offsets:
            self._settings = replace(settings, octave_offsets=offsets)

        pattern = self._rebuild_pattern_locked(shared_mode)
        self._order = self._order_locked()
        if pattern is None:
            return
        extra = ""
        if nudged is not None:
            extra = f" octave[{nudged[0]}]={nudged[1]:+d}"
        print(
            f"[ARP] evolved #{count} depth={depth:.2f} "
            f"shared={shared_mode} "
            f"shared_ratio={_ratio_label(pattern.shared_ratio)} "
            f"octaves={offsets[0]},{offsets[1]},{offsets[2]}{extra}"
        )

    def _order_locked(self) -> tuple[int, ...]:
        settings = self._settings
        return order_indices(
            settings.order, seed=settings.seed, pattern=self._pattern
        )


# ----------------------------------------------------------------------
# Helpers
# ----------------------------------------------------------------------


def _clamp_note(
    key: str, before: ArpSettings, after: ArpSettings, requested: object
) -> str | None:
    """Describe an explicit clamp for the ``[ARP]`` log, or return ``None``."""

    if key == "gate_seconds" and after.gate_seconds != _requested_float(requested):
        return (
            f"gate {_requested_float(requested):.3f} s clamped to "
            f"{after.gate_seconds:.3f} s (<= step {after.step_seconds:.3f} s)"
        )
    if key == "step_seconds":
        requested_value = _requested_float(requested)
        if after.step_seconds != requested_value:
            return (
                f"step {requested_value:.3f} s clamped to "
                f"{after.step_seconds:.3f} s ({STEP_MIN}-{STEP_MAX} s)"
            )
        if after.gate_seconds < before.gate_seconds:
            return (
                f"gate reduced to {after.gate_seconds:.3f} s "
                f"(<= new step {after.step_seconds:.3f} s)"
            )
    return None


def _clamp_wait(target: float, *boundaries: float | None) -> float:
    """Return the earliest of ``target`` and any pending boundary deadline."""

    for boundary in boundaries:
        if boundary is not None and boundary < target:
            target = boundary
    return target


def _requested_float(value: object) -> float:
    try:
        return float(value)  # type: ignore[arg-type]
    except (TypeError, ValueError):
        return float("nan")


def _fmt_value(settings: ArpSettings, key: str) -> str:
    if key == "step_seconds":
        return f"{settings.step_seconds:.3f}"
    if key == "gate_seconds":
        return f"{settings.gate_seconds:.3f}"
    if key == "octave_offsets":
        return ",".join(str(item) for item in settings.octave_offsets)
    return str(getattr(settings, key))


def _finite(value: object, label: str) -> float:
    try:
        numeric = float(value)
    except (TypeError, ValueError):
        raise TypeError(f"{label} must be a real number") from None
    if not math.isfinite(numeric):
        raise ValueError(f"{label} must be finite")
    return numeric


def _as_int(value: object, label: str) -> int:
    if isinstance(value, bool):
        raise TypeError(f"{label} must be an integer")
    if isinstance(value, int):
        return value
    try:
        numeric = float(value)  # type: ignore[arg-type]
    except (TypeError, ValueError):
        raise TypeError(f"{label} must be an integer") from None
    if not math.isfinite(numeric):
        raise ValueError(f"{label} must be finite")
    return int(round(numeric))


def _clamped_float(value: object, label: str, low: float, high: float) -> float:
    number = _positive_float(value, label)
    return max(low, min(high, number))


def _choose_shared(
    ratios: list[float],
    anchor: float | None,
    rng: random.Random,
    shared_mode: str,
) -> float:
    if shared_mode == "anchor" and anchor is not None:
        try:
            anchor_value = float(anchor)
        except (TypeError, ValueError):
            anchor_value = None
        if anchor_value is not None:
            for ratio in ratios:
                if math.isclose(
                    ratio, anchor_value, rel_tol=0.0, abs_tol=1e-12
                ):
                    return ratio
    return rng.choice(ratios)


def _make_note(ratio: float, octave: int, fundamental: float) -> ArpNote:
    return ArpNote(
        ratio=ratio,
        octave=octave,
        frequency=float(ratio * fundamental * (2.0 ** octave)),
    )


def _clean_ratios(
    ratios: list[float] | tuple[float, ...],
) -> list[float]:
    if isinstance(ratios, (str, bytes)):
        raise TypeError("ratios must be an iterable of real numbers")
    cleaned: list[float] = []
    for raw in ratios:
        value = _positive_float(raw, "ratio")
        if value not in cleaned:
            cleaned.append(value)
    if not cleaned:
        raise ValueError("ratios must not be empty")
    return cleaned


def _clean_offsets(
    offsets: tuple[int, ...] | list[int],
) -> tuple[int, int, int]:
    return validate_octave_offsets(offsets)


def _pattern_signature(pattern: ArpPattern) -> tuple:
    def note_key(note: ArpNote) -> tuple[float, int, float]:
        return (note.ratio, note.octave, round(note.frequency, 6))

    return (
        tuple(note_key(note) for note in pattern.group_a),
        tuple(note_key(note) for note in pattern.group_b),
    )


def _print_pattern(pattern: ArpPattern) -> None:
    group_a = ", ".join(_ratio_label(note.ratio) for note in pattern.group_a)
    group_b = ", ".join(_ratio_label(note.ratio) for note in pattern.group_b)
    print(
        f"[ARP] shared={_ratio_label(pattern.shared_ratio)} "
        f"({pattern.shared_frequency:.2f} Hz)  "
        f"A=[{group_a}]  B=[{group_b}]"
    )


def _ratio_label(ratio: float) -> str:
    fraction = Fraction(ratio).limit_denominator(64)
    if abs(float(fraction) - ratio) <= max(1e-6, abs(ratio) * 1e-6):
        return f"{fraction.numerator}/{fraction.denominator}"
    return f"{ratio:.6f}"


def _positive_float(value: float, label: str) -> float:
    if isinstance(value, bool):
        raise TypeError(f"{label} must be a real number")
    try:
        number = float(value)
    except (TypeError, ValueError):
        raise TypeError(f"{label} must be a real number") from None
    if not math.isfinite(number) or number <= 0.0:
        raise ValueError(f"{label} must be finite and strictly positive")
    return number


__all__ = [
    "ArpNote",
    "ArpPattern",
    "ArpSettings",
    "DEFAULT_GATE_SECONDS",
    "DEFAULT_OCTAVE_OFFSETS",
    "DEFAULT_ORDER",
    "DEFAULT_SEED",
    "DEFAULT_SHARED_MODE",
    "DEFAULT_STEP_SECONDS",
    "DEFAULT_VELOCITY",
    "GATE_MAX",
    "GATE_MIN",
    "OCTAVE_MAX",
    "OCTAVE_MIN",
    "ORDERS",
    "PINGPONG_INDICES",
    "SEED_MAX",
    "SHARED_MODES",
    "STEP_MAX",
    "STEP_MIN",
    "SetArp",
    "build_groups",
    "generate_groups",
    "normalize_setting_name",
    "order_indices",
    "validate_gate_seconds",
    "validate_octave_offsets",
    "validate_order",
    "validate_seed",
    "validate_shared_mode",
    "validate_step_seconds",
    "validate_velocity",
]
