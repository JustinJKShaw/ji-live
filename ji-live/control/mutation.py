"""Decentralised mutation engine for a just-intonation pitch-class field.

The FrequencySet is treated as a small, drifting vocabulary of **pitch
classes**, not as a chord to be optimised.  Ratios are octave-reduced to
``[1, 2)``; ``2/1``, ``1/2``, ``4/1`` etc. are the same class.  A separate
voicing layer assigns each class an octave for analysis and future audio use,
so octave movement does not consume extra members of the 12-class budget.

Mutation is intentionally probabilistic and relational:

1. Candidate classes are generated from the current classes multiplied by a
   vocabulary of small rational intervals, plus rational neighbours.  A fixed
   pool is never a hard limit, so the engine cannot dead-end.
2. Candidates are scored by (a) denominator complexity of their ratios to all
   current classes, (b) Sethares roughness of the actual voiced frequencies,
   and (c) a small anchor term and near-duplicate penalty.
3. Candidates are sampled by rank with ``randomness``, ``dissonance_bias`` and
   an explicit ``temperature``.  The best score is a bias, not a mandate.
4. The set size breathes around ``target_size`` (default 7), within
   ``min_size`` and ``max_size`` (12).  Add, remove and replace actions are all
   possible.  The engine never refuses to mutate.
5. One class is designated the anchor/drone.  ``anchor_mobility`` controls how
   likely it is to leave the set: 0.0 = never, 1.0 = the same removal weight as
   any other class.  At 1.0 the anchor is also re-chosen freely when removed.
   The 220 Hz analysis reference itself does not drift.
6. One trigger can contain several edits (``MutationBatch``).  ``max_changes``
   caps the batch width.  ``temperature`` broadens the candidate vocabulary and
   flattens rank selection, allowing more complex/rougher intervals.

Roughness model
---------------
For two actual frequencies f1, f2, with fmin = min(f1, f2) and
delta = abs(f2 - f1):

    cb    = 1.72 * fmin ** 0.65
    rough = exp(-3.5 * delta / cb) - exp(-5.75 * delta / cb)

This is the Plomp-Levelt curve as approximated by Sethares (Tuning, Timbre,
Spectrum, Scale, 1998).  It is a texture term here, not an objective to
minimise; the dominant term is intervallic relatedness.  All unordered
frequency pairs contribute.

No audio and no scsynth calls live in this module.  Stage 3 translates events
into server-side frequency/glide updates.
"""

from __future__ import annotations

import math
import random
from dataclasses import dataclass
from fractions import Fraction
from numbers import Real
from typing import Iterable, Sequence

MAX_SET_SIZE = 12
DEFAULT_TARGET_SIZE = 7
DEFAULT_MIN_SIZE = 3
DEFAULT_ANCHOR_MOBILITY = 0.25   # equivalent to the old 1/4 removal weight
DEFAULT_ANCHOR_WEIGHT = 4.0      # legacy inverse view of the default mobility
DEFAULT_MAX_CHANGES = 3
MIN_CLASS_SEPARATION_CENTS = 15.0
MIN_STEPWISE_MOVE_CENTS = 10.0

STEPWISE_LOW = 0.917
STEPWISE_HIGH = 1.091
_FLOAT_TOLERANCE = 1e-12
_CLASS_DECIMALS = 12
_DENOMINATOR_LIMIT = 16
_TEMPERATURE_DENOMINATOR_LIMIT = 32
_DENOMINATOR_ERROR = 0.03
_INVALID_DENOMINATOR = 32.0

#: Small rational intervals used to generate new classes from the current
#: field.  Both a ratio and its octave-folded inverse are included where useful.
INTERVAL_POOL: tuple[float, ...] = (
    3.0 / 2.0,
    4.0 / 3.0,
    5.0 / 4.0,
    8.0 / 5.0,
    6.0 / 5.0,
    5.0 / 3.0,
    7.0 / 4.0,
    8.0 / 7.0,
    9.0 / 8.0,
    16.0 / 9.0,
    10.0 / 9.0,
    9.0 / 5.0,
    16.0 / 15.0,
    15.0 / 8.0,
    7.0 / 6.0,
    12.0 / 7.0,
    11.0 / 8.0,
    16.0 / 11.0,
    13.0 / 8.0,
    16.0 / 13.0,
    7.0 / 5.0,
    10.0 / 7.0,
    11.0 / 10.0,
    20.0 / 11.0,
    13.0 / 10.0,
    20.0 / 13.0,
    14.0 / 9.0,
    9.0 / 7.0,
    11.0 / 9.0,
    18.0 / 11.0,
    14.0 / 13.0,
    13.0 / 7.0,
    15.0 / 7.0,
    16.0 / 7.0,
)

#: Higher-limit intervals unlocked by temperature.  These are deliberately
#: stranger than INTERVAL_POOL: primes 17, 19, 23, 29 and 31 are represented.
#: At temperature 0 none are used, so the Stage 2 palette is unchanged.
EXTENDED_INTERVAL_POOL: tuple[float, ...] = (
    17.0 / 16.0,
    19.0 / 16.0,
    21.0 / 16.0,
    23.0 / 16.0,
    25.0 / 16.0,
    27.0 / 16.0,
    29.0 / 16.0,
    31.0 / 16.0,
    17.0 / 12.0,
    19.0 / 12.0,
    23.0 / 12.0,
    25.0 / 12.0,
    11.0 / 7.0,
    13.0 / 7.0,
    15.0 / 7.0,
    16.0 / 7.0,
    17.0 / 10.0,
    19.0 / 10.0,
    21.0 / 10.0,
    23.0 / 10.0,
    11.0 / 6.0,
    13.0 / 6.0,
    17.0 / 6.0,
    19.0 / 6.0,
    13.0 / 5.0,
    17.0 / 5.0,
    19.0 / 5.0,
    17.0 / 9.0,
    19.0 / 9.0,
    23.0 / 9.0,
    19.0 / 8.0,
    21.0 / 8.0,
    23.0 / 8.0,
    25.0 / 8.0,
    27.0 / 8.0,
    29.0 / 8.0,
    31.0 / 8.0,
)


def roughness(f1: float, f2: float) -> float:
    """Return the Sethares/Plomp-Levelt roughness of two frequencies in Hz."""

    f1 = _as_positive_float(f1, "f1")
    f2 = _as_positive_float(f2, "f2")

    fmin = min(f1, f2)
    delta = abs(f2 - f1)
    cb = 1.72 * (fmin ** 0.65)
    x = delta / cb
    return math.exp(-3.5 * x) - math.exp(-5.75 * x)


def fold_ratio(ratio: float) -> float:
    """Fold a positive ratio into the octave class ``[1, 2)``.

    The result is rounded to 12 decimals so that classes produced by different
    multiplication paths compare as one class.  Octave duplicates are removed
    by :meth:`MutationEngine.set_ratios`.
    """

    value = _as_positive_float(ratio, "ratio")
    while value >= 2.0:
        value *= 0.5
    while value < 1.0:
        value *= 2.0
    if math.isclose(value, 2.0, rel_tol=0.0, abs_tol=1e-12):
        value = 1.0
    return round(value, _CLASS_DECIMALS)


@dataclass(frozen=True, slots=True)
class MutationEvent:
    """A proposed class-field edit.

    ``removed_ratio`` and ``new_ratio`` may each be ``None``: an event can add,
    remove, or replace.  ``new_octave``/``removed_octave`` are voicing hints for
    Stage 3 and do not affect pitch-class identity.
    """

    removed_ratio: float | None
    new_ratio: float | None
    glide_time: float
    reason: str
    new_octave: int | None = None
    removed_octave: int | None = None


@dataclass(frozen=True, slots=True)
class MutationBatch:
    """Several ordered edits applied by one trigger.

    ``events`` are already applied when a batch is returned by
    :meth:`MutationEngine.mutate_many`.  The batch carries one glide time, so
    the bridge sends one final FrequencySet instead of intermediate sets.
    """

    events: tuple[MutationEvent, ...]
    glide_time: float

    @property
    def count(self) -> int:
        return len(self.events)


@dataclass(frozen=True, slots=True)
class OctaveEvent:
    """A voicing-only move: one class changes register by one octave."""

    ratio: float
    old_octave: int
    new_octave: int
    glide_time: float
    reason: str


class MutationEngine:
    """Maintain a drifting, octave-reduced pitch-class field.

    Args:
        ratios: Initial pitch classes, expressed as ratios.  Values are
            octave-folded and deduplicated.
        fundamental: Hz used to convert ratios into analysis frequencies.
        target_size: Preferred number of classes (default 7).
        max_size: Hard ceiling (12).
        min_size: Soft floor (3); the action policy strongly prefers adding
            below this.
        anchor_ratio: Class to protect as the drone.  Defaults to 1/1 if
            present, otherwise the first class.
        octave_min/octave_max: Allowed voicing octaves relative to the
            fundamental.  Must include 0.
        seed: Optional RNG seed for reproducible runs.
        max_changes: Maximum edits in one :meth:`mutate_many` batch (default
            3; the OSC/bridge change-count control is clamped to this).
        anchor_mobility: 0..1.  Relative removal weight of the anchor class:
            0.0 = never removed by a mutation, 0.25 = old 1/4 weight,
            1.0 = same weight as any other class, and free re-anchoring.
    """

    def __init__(
        self,
        ratios: Sequence[float],
        fundamental: float = 220.0,
        target_size: int = DEFAULT_TARGET_SIZE,
        max_size: int = MAX_SET_SIZE,
        min_size: int = DEFAULT_MIN_SIZE,
        anchor_ratio: float | None = None,
        octave_min: int = -1,
        octave_max: int = 2,
        seed: int | None = None,
        max_changes: int = DEFAULT_MAX_CHANGES,
        anchor_mobility: float = DEFAULT_ANCHOR_MOBILITY,
    ) -> None:
        self.fundamental = _as_positive_float(fundamental, "fundamental")

        if isinstance(max_size, bool) or not isinstance(max_size, int):
            raise TypeError("max_size must be an integer")
        if not 1 <= max_size <= MAX_SET_SIZE:
            raise ValueError(f"max_size must be between 1 and {MAX_SET_SIZE}")
        if isinstance(target_size, bool) or not isinstance(target_size, int):
            raise TypeError("target_size must be an integer")
        if not 1 <= target_size <= max_size:
            raise ValueError("target_size must be between 1 and max_size")
        if isinstance(min_size, bool) or not isinstance(min_size, int):
            raise TypeError("min_size must be an integer")
        if not 1 <= min_size <= target_size:
            raise ValueError("min_size must be between 1 and target_size")
        if isinstance(max_changes, bool) or not isinstance(max_changes, int):
            raise TypeError("max_changes must be an integer")
        if not 1 <= max_changes <= MAX_SET_SIZE:
            raise ValueError(f"max_changes must be between 1 and {MAX_SET_SIZE}")
        anchor_mobility = _as_bounded_float(
            anchor_mobility, "anchor_mobility", 0.0, 1.0
        )

        if isinstance(octave_min, bool) or not isinstance(octave_min, int):
            raise TypeError("octave_min must be an integer")
        if isinstance(octave_max, bool) or not isinstance(octave_max, int):
            raise TypeError("octave_max must be an integer")
        if octave_min > octave_max:
            raise ValueError("octave_min must be <= octave_max")
        if not octave_min <= 0 <= octave_max:
            raise ValueError("the octave range must include 0 for the base octave")

        self.target_size = target_size
        self.max_size = max_size
        self.min_size = min_size
        self.max_changes = max_changes
        self.octave_min = octave_min
        self.octave_max = octave_max
        self._anchor_mobility = anchor_mobility

        self._rng = random.Random(seed)
        self._ratios: list[float] = []
        self._octaves: dict[float, int] = {}
        self._anchor_ratio: float | None = None
        self._last_removed_ratio: float | None = None

        self.set_ratios(ratios)
        if anchor_ratio is not None:
            self.set_anchor(anchor_ratio)

    # ------------------------------------------------------------------
    # Public state
    # ------------------------------------------------------------------

    @property
    def current_ratios(self) -> list[float]:
        """Octave-reduced pitch classes, unique, at most ``max_size``."""

        return list(self._ratios)

    @property
    def analysis_voicing(self) -> dict[float, int]:
        """Copy of the class -> octave map used for analysis and scoring.

        This is a representative voicing, not a global register assignment for
        live inputs.  Stage 3/4 may replace it with actual voice frequencies.
        """

        return dict(self._octaves)

    @property
    def current_octaves(self) -> dict[float, int]:
        """Deprecated alias for :attr:`analysis_voicing`."""

        return self.analysis_voicing

    @property
    def current_frequencies(self) -> list[float]:
        """Actual voiced frequencies in Hz for the current analysis voicing."""

        return [
            self._frequency(ratio, self._octaves[ratio])
            for ratio in self._ratios
        ]

    @property
    def anchor_ratio(self) -> float | None:
        """The protected drone class, if any."""

        return self._anchor_ratio

    @property
    def anchor_mobility(self) -> float:
        """Relative removal weight of the anchor class (0.0 .. 1.0)."""

        return self._anchor_mobility

    @property
    def anchor_weight(self) -> float:
        """Legacy inverse view: 4.0 at the default mobility of 0.25."""

        if self._anchor_mobility <= 0.0:
            return math.inf
        return 1.0 / self._anchor_mobility

    def set_anchor_mobility(self, value: float) -> None:
        """Set how freely the anchor class may leave the set.

        ``0.0`` makes the anchor unremovable by mutation; ``1.0`` gives it the
        same removal weight as every other class and re-anchors freely.
        """

        self._anchor_mobility = _as_bounded_float(
            value, "anchor_mobility", 0.0, 1.0
        )

    @property
    def last_removed_ratio(self) -> float | None:
        return self._last_removed_ratio

    def set_ratios(self, ratios: Sequence[float]) -> None:
        """Replace and canonicalise the class set.

        All voicings reset to octave 0.  The existing anchor is retained if it
        survives; otherwise 1/1 is preferred, then the first class.
        """

        old_anchor = self._anchor_ratio
        cleaned = self._clean_ratios(ratios)
        if len(cleaned) > self.max_size:
            raise ValueError(
                f"frequency set has {len(cleaned)} unique classes; "
                f"max_size is {self.max_size}"
            )

        self._ratios = cleaned
        self._octaves = {ratio: 0 for ratio in cleaned}
        self._last_removed_ratio = None
        if old_anchor in cleaned:
            self._anchor_ratio = old_anchor
        elif 1.0 in cleaned:
            self._anchor_ratio = 1.0
        elif cleaned:
            self._anchor_ratio = cleaned[0]
        else:
            self._anchor_ratio = None

    def set_anchor(self, ratio: float) -> None:
        """Designate an existing class as the protected drone."""

        folded = fold_ratio(ratio)
        if folded not in self._ratios:
            raise ValueError("anchor ratio is not in the current class set")
        self._anchor_ratio = folded

    def set_analysis_octave(self, ratio: float, octave: int) -> None:
        """Set the analysis voicing octave for one class."""

        folded = fold_ratio(ratio)
        if folded not in self._octaves:
            raise ValueError("ratio is not in the current class set")
        if isinstance(octave, bool) or not isinstance(octave, int):
            raise TypeError("octave must be an integer")
        if not self.octave_min <= octave <= self.octave_max:
            raise ValueError("octave is outside the allowed range")
        self._octaves[folded] = octave

    def set_octave(self, ratio: float, octave: int) -> None:
        """Deprecated alias for :meth:`set_analysis_octave`."""

        self.set_analysis_octave(ratio, octave)

    # ------------------------------------------------------------------
    # Mutation
    # ------------------------------------------------------------------

    def propose_mutation(
        self,
        randomness: float = 0.55,
        dissonance_bias: float = 0.0,
        stepwise_bias: float = 0.25,
        glide_time: float = 0.0,
        temperature: float = 0.0,
    ) -> MutationEvent:
        """Propose one add, remove, or replace without changing state.

        The method never raises because it ran out of palette.  Candidate
        classes are regenerated from the current field every time.

        ``temperature`` (0..1) unlocks higher-limit intervals and flattens the
        rank selection.  At 0.0 the Stage 2 palette/scoring is unchanged; at
        1.0 extended intervals are available and every rank is equally likely
        when ``dissonance_bias`` is 0.
        """

        randomness = _as_bounded_float(randomness, "randomness", 0.0, 1.0)
        dissonance_bias = _as_bounded_float(
            dissonance_bias, "dissonance_bias", -1.0, 1.0
        )
        stepwise_bias = _as_bounded_float(stepwise_bias, "stepwise_bias", 0.0, 1.0)
        glide_time = _as_nonnegative_float(glide_time, "glide_time")
        temperature = _as_bounded_float(temperature, "temperature", 0.0, 1.0)

        current = list(self._ratios)
        if not current:
            return MutationEvent(
                removed_ratio=None,
                new_ratio=1.0,
                glide_time=glide_time,
                reason="added",
                new_octave=0,
            )

        action = self._choose_action(len(current))
        stepwise = (
            stepwise_bias > 0.0
            and self._last_removed_ratio is not None
            and self._rng.random() < stepwise_bias
        )

        # A stepwise request should produce a new class, not just vanish.
        if stepwise and action == "remove":
            action = "replace"

        removed: float | None = None
        removed_octave: int | None = None
        if action in ("remove", "replace"):
            removed = self._choose_removal(current)
            removed_octave = self._octaves[removed]

        if action == "remove":
            return MutationEvent(
                removed_ratio=removed,
                new_ratio=None,
                glide_time=glide_time,
                reason="removed",
                removed_octave=removed_octave,
            )

        remaining = [ratio for ratio in current if ratio != removed]
        generated = self._generate_candidates(current, temperature)
        candidates = generated
        if action == "replace":
            step_reference = removed
        else:
            step_reference = self._last_removed_ratio if stepwise else None

        if stepwise and step_reference is not None:
            near = [
                candidate
                for candidate in generated
                if _within_stepwise_window(candidate, step_reference)
                and self._pitch_class_distance(candidate, step_reference)
                >= MIN_STEPWISE_MOVE_CENTS
            ]
            if near:
                candidates = near
            else:
                # No usefully distinct class in the small window; fall back to
                # a normal mutation rather than refusing to move.
                stepwise = False
                candidates = generated

        if not candidates:
            candidates = [self._emergency_candidate(current)]

        # Avoid near-unison classes: they destabilise octave-equivalent
        # membership and are perceptually hard to distinguish.  Compare with
        # the field that will actually remain after the edit.
        separated = [
            candidate
            for candidate in candidates
            if all(
                self._pitch_class_distance(candidate, other)
                >= MIN_CLASS_SEPARATION_CENTS
                for other in remaining
            )
        ]
        if separated:
            candidates = separated

        scored: list[tuple[float, float, int]] = []
        for candidate in candidates:
            if action == "replace" and removed is not None:
                octave = removed_octave if removed_octave is not None else 0
            else:
                octave = self._choose_new_octave(candidate, current)
            score = self._candidate_score(
                candidate, remaining, octave, temperature
            )
            scored.append((score, candidate, octave))
        scored.sort(key=lambda item: item[0])

        effective_randomness = randomness + temperature * (1.0 - randomness)
        _, selected, selected_octave = self._weighted_rank_choice(
            scored, effective_randomness, dissonance_bias
        )

        if action == "replace":
            reason = "stepwise" if stepwise else "replaced"
        else:
            reason = "added"

        return MutationEvent(
            removed_ratio=removed,
            new_ratio=selected,
            glide_time=glide_time,
            reason=reason,
            new_octave=selected_octave,
            removed_octave=removed_octave,
        )

    def mutate_many(
        self,
        count: int = 1,
        randomness: float = 0.55,
        dissonance_bias: float = 0.0,
        stepwise_bias: float = 0.25,
        glide_time: float = 0.0,
        temperature: float = 0.0,
    ) -> MutationBatch:
        """Apply ``count`` sequential edits and return them as one batch.

        The edits are generated against the evolving set, so later edits see
        earlier ones.  If any step fails, the engine is rolled back to its
        starting state and the exception is re-raised.  ``count`` is capped by
        ``max_changes``; action policy still keeps the set within 3..12.
        """

        if isinstance(count, bool) or not isinstance(count, int):
            raise TypeError("count must be an integer")
        if not 1 <= count <= self.max_changes:
            raise ValueError(f"count must be between 1 and {self.max_changes}")

        randomness = _as_bounded_float(randomness, "randomness", 0.0, 1.0)
        dissonance_bias = _as_bounded_float(
            dissonance_bias, "dissonance_bias", -1.0, 1.0
        )
        stepwise_bias = _as_bounded_float(stepwise_bias, "stepwise_bias", 0.0, 1.0)
        glide_time = _as_nonnegative_float(glide_time, "glide_time")
        temperature = _as_bounded_float(temperature, "temperature", 0.0, 1.0)

        snapshot = self._snapshot()
        events: list[MutationEvent] = []
        try:
            for _ in range(count):
                event = self.propose_mutation(
                    randomness=randomness,
                    dissonance_bias=dissonance_bias,
                    stepwise_bias=stepwise_bias,
                    glide_time=glide_time,
                    temperature=temperature,
                )
                self.apply(event)
                events.append(event)
        except Exception:
            self._restore(snapshot)
            raise

        return MutationBatch(tuple(events), glide_time)

    def apply(self, event: MutationEvent) -> None:
        """Commit a mutation event to the class set and voicing."""

        if not isinstance(event, MutationEvent):
            raise TypeError("event must be a MutationEvent")
        _as_nonnegative_float(event.glide_time, "event.glide_time")

        removed = None
        if event.removed_ratio is not None:
            removed = fold_ratio(event.removed_ratio)
        new_ratio = None
        if event.new_ratio is not None:
            new_ratio = fold_ratio(event.new_ratio)

        if removed is None and new_ratio is None:
            raise ValueError("mutation event must add, remove, or replace")

        updated = list(self._ratios)
        octaves = dict(self._octaves)
        removed_octave = octaves.get(removed) if removed is not None else None
        if removed is not None:
            updated = [ratio for ratio in updated if ratio != removed]
            octaves.pop(removed, None)

        added = False
        if new_ratio is not None:
            if new_ratio not in updated:
                updated.append(new_ratio)
                added = True
            if added:
                if event.new_octave is None:
                    if removed_octave is not None:
                        octave = removed_octave
                    elif self.octave_min <= 0 <= self.octave_max:
                        octave = 0
                    elif self.octave_min > 0:
                        octave = self.octave_min
                    else:
                        octave = self.octave_max
                elif isinstance(event.new_octave, bool) or not isinstance(
                    event.new_octave, int
                ):
                    raise TypeError("event.new_octave must be an integer or None")
                elif not self.octave_min <= event.new_octave <= self.octave_max:
                    raise ValueError("event.new_octave is outside the allowed range")
                else:
                    octave = event.new_octave
                octaves[new_ratio] = octave

        if len(updated) > self.max_size:
            raise ValueError(
                f"mutation would create {len(updated)} classes; "
                f"max_size is {self.max_size}"
            )

        old_anchor = self._anchor_ratio
        self._ratios = updated
        self._octaves = octaves
        self._anchor_ratio = self._reanchored(old_anchor)

        if removed is not None:
            self._last_removed_ratio = removed

    # ------------------------------------------------------------------
    # Octave / register movement
    # ------------------------------------------------------------------

    def propose_octave_motion(
        self,
        ratio: float | None = None,
        direction: int | None = None,
        glide_time: float = 0.0,
    ) -> OctaveEvent:
        """Propose moving one class up or down one octave.

        This does not alter the class set or its size.  ``anchor_mobility``
        controls how likely the anchor is to be chosen: at 0.0 it is never
        moved, at 1.0 it has no precedence.
        """

        glide_time = _as_nonnegative_float(glide_time, "glide_time")
        if not self._ratios:
            raise RuntimeError("no pitch classes are available to move")

        if direction is not None:
            if isinstance(direction, bool) or direction not in (-1, 1):
                raise ValueError("direction must be -1, +1, or None")
            directions = [direction]
        else:
            directions = [1, -1]
            self._rng.shuffle(directions)

        if ratio is not None:
            chosen = fold_ratio(ratio)
            if chosen not in self._octaves:
                raise ValueError("ratio is not in the current class set")
            ratios = [chosen]
        else:
            weights = [
                self._anchor_mobility if r == self._anchor_ratio else 1.0
                for r in self._ratios
            ]
            if sum(weights) <= 0.0:
                # Only the anchor exists and mobility is 0: there is no other
                # class to move, so allow the anchor rather than dead-ending.
                weights = [1.0 for _ in self._ratios]
            chosen = self._rng.choices(self._ratios, weights=weights, k=1)[0]
            ratios = [chosen] + [r for r in self._ratios if r != chosen]

        for candidate in ratios:
            current_octave = self._octaves[candidate]
            for delta in directions:
                new_octave = current_octave + delta
                if self.octave_min <= new_octave <= self.octave_max:
                    return OctaveEvent(
                        ratio=candidate,
                        old_octave=current_octave,
                        new_octave=new_octave,
                        glide_time=glide_time,
                        reason="octave_up" if delta > 0 else "octave_down",
                    )

        raise RuntimeError("no octave motion is possible within the allowed range")

    def apply_octave_motion(self, event: OctaveEvent) -> None:
        """Commit an octave/register move."""

        if not isinstance(event, OctaveEvent):
            raise TypeError("event must be an OctaveEvent")
        folded = fold_ratio(event.ratio)
        if folded not in self._octaves:
            raise ValueError("event ratio is not in the current class set")
        if isinstance(event.new_octave, bool) or not isinstance(
            event.new_octave, int
        ):
            raise TypeError("event.new_octave must be an integer")
        if not self.octave_min <= event.new_octave <= self.octave_max:
            raise ValueError("event.new_octave is outside the allowed range")
        _as_nonnegative_float(event.glide_time, "event.glide_time")
        self._octaves[folded] = event.new_octave

    # ------------------------------------------------------------------
    # Analysis / scoring
    # ------------------------------------------------------------------

    def roughness_score(self) -> float:
        """Total Sethares roughness over all pairs of actual voiced frequencies."""

        frequencies = self.current_frequencies
        total = 0.0
        for i, first in enumerate(frequencies):
            for second in frequencies[i + 1 :]:
                total += roughness(first, second)
        return float(total)

    # ------------------------------------------------------------------
    # Internals
    # ------------------------------------------------------------------

    def _choose_action(self, size: int) -> str:
        """Choose add/remove/replace, biased toward target_size."""

        add_weight = (
            max(0.0, float(self.target_size + 1 - size))
            if size < self.max_size
            else 0.0
        )
        remove_weight = (
            max(0.0, float(size - (self.target_size - 1)))
            if size > self.min_size
            else 0.0
        )
        replace_weight = 1.25 if size >= 1 else 0.0

        actions: list[str] = []
        weights: list[float] = []
        if add_weight > 0.0:
            actions.append("add")
            weights.append(add_weight)
        if remove_weight > 0.0:
            actions.append("remove")
            weights.append(remove_weight)
        if replace_weight > 0.0:
            actions.append("replace")
            weights.append(replace_weight)

        if not actions:
            return "replace"
        return self._rng.choices(actions, weights=weights, k=1)[0]

    def _choose_removal(self, current: Sequence[float]) -> float:
        """Choose a class to remove, weighted by ``anchor_mobility``."""

        frequencies = {
            ratio: self._frequency(ratio, self._octaves[ratio])
            for ratio in current
        }
        weights: list[float] = []
        for ratio in current:
            contribution = sum(
                roughness(frequencies[ratio], frequencies[other])
                for other in current
                if other != ratio
            )
            weight = 1.0 + contribution
            if ratio == self._anchor_ratio:
                weight *= self._anchor_mobility
            weights.append(weight)
        if sum(weights) <= 0.0:
            # Only the anchor is present and mobility is 0; allow it rather
            # than raising from random.choices.
            weights = [1.0 for _ in current]
        return self._rng.choices(list(current), weights=weights, k=1)[0]

    def _generate_candidates(
        self, current: Sequence[float], temperature: float = 0.0
    ) -> list[float]:
        """Generate new classes from the current field.

        At ``temperature`` 0 this is the Stage 2 palette.  Above 0, some
        higher-limit intervals are admitted (all of them at 1.0) and rational
        neighbours are scanned with a higher denominator limit.  The result is
        deliberately larger than the final palette; scoring decides what is
        useful, and a final rational scan guarantees a non-empty result.
        """

        current_set = set(current)
        candidates: dict[float, None] = {}

        def add(value: float) -> None:
            folded = fold_ratio(value)
            if folded not in current_set:
                candidates[folded] = None

        for base in current:
            for interval in INTERVAL_POOL:
                add(base * interval)
            if temperature > 0.0:
                for interval in EXTENDED_INTERVAL_POOL:
                    if temperature >= 1.0 or self._rng.random() < temperature:
                        add(base * interval)

        denominator_limit = _DENOMINATOR_LIMIT + int(
            round(temperature * (_TEMPERATURE_DENOMINATOR_LIMIT - _DENOMINATOR_LIMIT))
        )
        seeds = list(current)
        if self._anchor_ratio is not None and self._anchor_ratio not in seeds:
            seeds.append(self._anchor_ratio)
        for base in seeds:
            fraction = Fraction(base).limit_denominator(denominator_limit)
            for delta in (-2, -1, 1, 2):
                numerator = fraction.numerator + delta * fraction.denominator
                if numerator > 0:
                    add(numerator / fraction.denominator)

        if not candidates:
            candidates[self._emergency_candidate(current)] = None
        return list(candidates)

    def _candidate_score(
        self,
        candidate: float,
        other_ratios: Sequence[float],
        octave: int,
        temperature: float = 0.0,
    ) -> float:
        denominator_limit = _DENOMINATOR_LIMIT + int(
            round(temperature * (_TEMPERATURE_DENOMINATOR_LIMIT - _DENOMINATOR_LIMIT))
        )
        if other_ratios:
            denominator = sum(
                self._relative_denominator(candidate, other, denominator_limit)
                for other in other_ratios
            ) / len(other_ratios)
            novelty = min(
                self._pitch_class_distance(candidate, other)
                for other in other_ratios
            )
        else:
            denominator = 0.0
            novelty = 600.0

        anchor_term = 0.0
        if self._anchor_ratio is not None:
            anchor_term = self._relative_denominator(
                candidate, self._anchor_ratio, denominator_limit
            )

        candidate_frequency = self._frequency(candidate, octave)
        rough = sum(
            roughness(
                candidate_frequency,
                self._frequency(other, self._octaves[other]),
            )
            for other in other_ratios
        )
        close_penalty = max(0.0, 25.0 - novelty) / 25.0

        # Denominator is primary; roughness is texture, not the objective.
        # Temperature relaxes the complexity/roughness/anchor penalties so
        # stranger intervals can rank higher before selection is flattened.
        complexity_weight = 1.0 - 0.5 * temperature
        roughness_weight = 0.35 * (1.0 - 0.5 * temperature)
        anchor_weight = 0.15 * (1.0 - 0.5 * temperature)
        return (
            complexity_weight * denominator
            + roughness_weight * rough
            + anchor_weight * anchor_term
            + 0.35 * close_penalty
        )

    def _weighted_rank_choice(
        self,
        scored: Sequence[tuple[float, float, int]],
        randomness: float,
        dissonance_bias: float,
    ) -> tuple[float, float, int]:
        if len(scored) == 1:
            return scored[0]

        alpha = -10.0 * (1.0 - randomness) + 10.0 * dissonance_bias
        count = len(scored)
        if abs(alpha) < _FLOAT_TOLERANCE:
            return self._rng.choice(list(scored))

        weights = [
            math.exp(alpha * (index / (count - 1)))
            for index in range(count)
        ]
        total = math.fsum(weights)
        if not math.isfinite(total) or total <= 0.0:
            return self._rng.choice(list(scored))
        return self._rng.choices(list(scored), weights=weights, k=1)[0]

    def _choose_new_octave(
        self, candidate: float, current: Sequence[float]
    ) -> int:
        """Place a new class near the current analysis register."""

        octaves = list(range(self.octave_min, self.octave_max + 1))
        if not current:
            return 0 if self.octave_min <= 0 <= self.octave_max else octaves[0]

        frequencies = [
            self._frequency(ratio, self._octaves[ratio]) for ratio in current
        ]
        target = sorted(frequencies)[len(frequencies) // 2]
        best = min(
            octaves,
            key=lambda octave: abs(
                math.log2(self._frequency(candidate, octave) / target)
            ),
        )
        if self._rng.random() < 0.12:
            return self._rng.choice(octaves)
        return best

    def _reanchored(self, old_anchor: float | None) -> float | None:
        if not self._ratios:
            return None
        if self._anchor_ratio in self._ratios:
            return self._anchor_ratio
        if self._anchor_mobility >= 1.0:
            # Maximum mobility: the new anchor is not biased toward the old
            # one; any surviving class can become the drone.
            return self._rng.choice(self._ratios)
        if old_anchor is not None:
            return min(
                self._ratios,
                key=lambda ratio: abs(math.log(ratio / old_anchor)),
            )
        if 1.0 in self._ratios:
            return 1.0
        return self._ratios[0]

    def _snapshot(
        self,
    ) -> tuple[list[float], dict[float, int], float | None, float | None]:
        return (
            list(self._ratios),
            dict(self._octaves),
            self._anchor_ratio,
            self._last_removed_ratio,
        )

    def _restore(
        self,
        snapshot: tuple[list[float], dict[float, int], float | None, float | None],
    ) -> None:
        ratios, octaves, anchor, last_removed = snapshot
        self._ratios = list(ratios)
        self._octaves = dict(octaves)
        self._anchor_ratio = anchor
        self._last_removed_ratio = last_removed

    def _emergency_candidate(self, current: Sequence[float]) -> float:
        current_set = set(current)
        for denominator in range(2, 2049):
            for numerator in range(1, denominator):
                candidate = fold_ratio(numerator / denominator)
                if candidate not in current_set:
                    return candidate
        raise RuntimeError("could not generate a new pitch class")  # unreachable

    def _frequency(self, ratio: float, octave: int) -> float:
        return float(ratio * self.fundamental * (2.0 ** octave))

    @staticmethod
    def _relative_denominator(
        first: float,
        second: float,
        limit: int = _DENOMINATOR_LIMIT,
    ) -> float:
        relative = max(first / second, second / first)
        fraction = Fraction(relative).limit_denominator(limit)
        error = abs(relative - float(fraction))
        if error > _DENOMINATOR_ERROR:
            return _INVALID_DENOMINATOR
        return float(fraction.denominator)

    @staticmethod
    def _pitch_class_distance(first: float, second: float) -> float:
        return abs(1200.0 * math.log2(first / second))

    def _clean_ratios(self, ratios: Iterable[float]) -> list[float]:
        if isinstance(ratios, (str, bytes)):
            raise TypeError("ratios must be an iterable of real numbers")

        cleaned: list[float] = []
        for raw in ratios:
            ratio = fold_ratio(raw)
            if ratio not in cleaned:
                cleaned.append(ratio)
        return cleaned


def _within_stepwise_window(candidate: float, reference: float) -> bool:
    relative = candidate / reference
    return STEPWISE_LOW <= relative <= STEPWISE_HIGH


def _as_positive_float(value: Real, label: str) -> float:
    if isinstance(value, bool) or not isinstance(value, Real):
        raise TypeError(f"{label} must be a real number")
    try:
        number = float(value)
    except (TypeError, ValueError, OverflowError) as exc:
        raise TypeError(f"{label} must be a real number") from exc
    if not math.isfinite(number) or number <= 0.0:
        raise ValueError(f"{label} must be finite and strictly positive")
    return number


def _as_bounded_float(value: Real, label: str, low: float, high: float) -> float:
    number = _as_real_float(value, label)
    if not low <= number <= high:
        raise ValueError(f"{label} must be in [{low}, {high}]")
    return number


def _as_real_float(value: Real, label: str) -> float:
    if isinstance(value, bool) or not isinstance(value, Real):
        raise TypeError(f"{label} must be a real number")
    try:
        number = float(value)
    except (TypeError, ValueError, OverflowError) as exc:
        raise TypeError(f"{label} must be a real number") from exc
    if not math.isfinite(number):
        raise ValueError(f"{label} must be finite")
    return number


def _as_nonnegative_float(value: Real, label: str) -> float:
    number = _as_real_float(value, label)
    if number < 0.0:
        raise ValueError(f"{label} must be non-negative")
    return number
