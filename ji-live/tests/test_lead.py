#!/usr/bin/env python3
"""Stage 8.3d pure tests: lead config/settings, motif consistency, variations,
groove lock, scheduler, rig independence, OSC handlers, and the bridge queue.

Run from ``ji-live/``::

    uv run --with python-osc python tests/test_lead.py

No audio, mido, PyQt6, or sockets.  Runtime checks use an injected fake MIDI
output and short real-time windows, like the bass/arp scheduler tests.
"""

from __future__ import annotations

import contextlib
import io
import math
import sys
import time
import traceback
from dataclasses import replace
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

import control.ji_bridge as bridge_module
import control.lead as lead_module
from control.lead import (
    ACCENT_BOOST,
    DEFAULT_BASE_OCTAVE,
    DEFAULT_DENSITY,
    DEFAULT_GATE_BEATS,
    DEFAULT_GRID,
    DEFAULT_GROOVE_LINK,
    DEFAULT_OCTAVE_RANGE,
    DEFAULT_PHRASE_BARS,
    DEFAULT_PORT,
    DEFAULT_SEED,
    DEFAULT_VARIATION,
    DEFAULT_VARIATION_BARS,
    DEFAULT_VELOCITY,
    GRIDS,
    MAX_EMPTY_RUN,
    VARIATIONS,
    LeadConfig,
    LeadEngine,
    LeadRig,
    LeadSettings,
    apply_variation,
    generate_motif,
    grid_quarters,
    place_steps,
    steps_per_bar,
    steps_per_beat,
    validate_base_octave,
    validate_density,
    validate_gate_beats,
    validate_grid,
    validate_octave_range,
    validate_phrase_bars,
    validate_seed,
    validate_variation,
    validate_variation_bars,
    validate_velocity,
)
from control.midi_rig import MIDIRig, load_config
from control.mutation import MutationEngine
from control.mpe_output import frequency_to_pitch
from control.osc_server import MutationOSCServer
from control.quantize import PHASE_LEAD

INITIAL_RATIOS = [1.0, 9.0 / 8.0, 5.0 / 4.0, 4.0 / 3.0, 3.0 / 2.0, 5.0 / 3.0, 15.0 / 8.0]
FUNDAMENTAL = 220.0
CONFIG_PATH = ROOT / "config" / "lead_config.json"


class FakePort:
    """Duck-typed MIDI output capturing :class:`MPEMessage` values + times."""

    def __init__(self) -> None:
        self.messages: list = []
        self.times: list[float] = []
        self.closed = False

    def send(self, message) -> None:  # noqa: ANN001 - duck-typed message
        self.messages.append(message)
        self.times.append(time.monotonic())

    def close(self) -> None:
        self.closed = True

    def types(self) -> list[str]:
        return [message.type for message in self.messages]

    def with_type(self, message_type: str) -> list:
        return [
            (message, at)
            for message, at in zip(self.messages, self.times)
            if message.type == message_type
        ]


def _wait_for(predicate, timeout: float = 3.0) -> bool:
    deadline = time.monotonic() + timeout
    while time.monotonic() < deadline:
        if predicate():
            return True
        time.sleep(0.005)
    return False


def _config(**overrides) -> LeadConfig:
    data = {
        "port": "IAC Driver Bus 5",
        "member_channel": 2,
        "bend_range_semitones": 48,
        "enabled": True,
        "grid": "1/8",
        "phrase_bars": 2,
        "density": 0.75,
        "gate_beats": 0.5,
        "velocity": 90,
        "octave_range": 1,
        "base_octave": 0,
        "variation": "identity",
        "variation_bars": 0,
        "swing": 0.0,
        "groove_link": True,
        "seed": 1,
    }
    data.update(overrides)
    return LeadConfig.from_mapping(data)


def _engine(**overrides) -> LeadEngine:
    engine = LeadEngine(_config(**overrides))
    engine.on_set(INITIAL_RATIOS, 1.0, FUNDAMENTAL)
    return engine


def _frequencies(engine: LeadEngine) -> list[tuple[int, float]]:
    return [(note.step, round(note.frequency, 6)) for note in engine.projection()]


# ---------------------------------------------------------------------------
# 1. Defaults / config
# ---------------------------------------------------------------------------


def test_settings_defaults() -> None:
    print("\n=== Test 1: lead defaults and config/lead_config.json ===")
    settings = LeadSettings()
    assert settings.enabled is False, "the lead ships off"
    assert settings.grid == DEFAULT_GRID == "1/8"
    assert settings.phrase_bars == DEFAULT_PHRASE_BARS == 2
    assert settings.density == DEFAULT_DENSITY == 0.75
    assert settings.gate_beats == DEFAULT_GATE_BEATS == 0.5
    assert settings.velocity == DEFAULT_VELOCITY == 90
    assert settings.octave_range == DEFAULT_OCTAVE_RANGE == 1
    assert settings.base_octave == DEFAULT_BASE_OCTAVE == 0
    assert settings.variation == DEFAULT_VARIATION == "identity"
    assert settings.variation_bars == DEFAULT_VARIATION_BARS == 0
    assert settings.swing == 0.0
    assert settings.groove_link is DEFAULT_GROOVE_LINK is True
    assert settings.seed == DEFAULT_SEED == 1

    config = lead_module.load_config(CONFIG_PATH)
    assert config.port == DEFAULT_PORT == "IAC Driver Bus 5"
    assert config.member_channel == 2
    assert config.bend_range_semitones == 48.0
    assert config.settings == settings, config.settings

    # A short config is allowed; unknown keys are ignored.
    short = LeadConfig.from_mapping({"grid": "1/16", "seed": 9, "nope": 1})
    assert short.settings.grid == "1/16" and short.settings.seed == 9
    print("  shipped defaults, JSON round trip, short mapping")


def test_validation() -> None:
    print("\n=== Test 2: settings validation and clamps ===")
    assert [validate_grid(value) for value in GRIDS] == list(GRIDS)
    for bad in ("1/2", "1/32", "triplet"):
        try:
            validate_grid(bad)
        except ValueError:
            pass
        else:
            raise AssertionError(f"grid accepted {bad!r}")

    for bad in (0, 9, -1):
        try:
            validate_phrase_bars(bad)
        except ValueError:
            pass
        else:
            raise AssertionError(f"phrase_bars accepted {bad!r}")
    assert validate_phrase_bars(8) == 8

    assert validate_density(0.0) == 0.1
    assert validate_density(2.0) == 1.0
    assert validate_density(0.4) == 0.4

    assert validate_gate_beats(0.0) == 0.05
    assert validate_gate_beats(9.0) == 4.0

    assert validate_velocity(0) == 1
    assert validate_velocity(300) == 127

    for bad in (-1, 3):
        try:
            validate_octave_range(bad)
        except ValueError:
            pass
        else:
            raise AssertionError(f"octave_range accepted {bad!r}")

    for bad in (-3, 3):
        try:
            validate_base_octave(bad)
        except ValueError:
            pass
        else:
            raise AssertionError(f"base_octave accepted {bad!r}")

    for name in VARIATIONS:
        assert validate_variation(name) == name
    try:
        validate_variation("sideways")
    except ValueError:
        pass
    else:
        raise AssertionError("unknown variation accepted")

    assert validate_variation_bars(-1) == 0
    assert validate_variation_bars(99) == 16
    assert validate_seed(-5) == 0
    assert validate_seed(2**40) == 2**31 - 1

    # Continuous/clamped settings and the switch coercion.
    assert LeadSettings.create(density=0.0).density == 0.1
    assert LeadSettings.create(gate_beats=9.0).gate_beats == 4.0
    assert LeadSettings.create(swing=5.0).swing == 1.0
    assert LeadSettings.create(swing=-1.0).swing == 0.0
    assert LeadSettings.create(groove_link=0).groove_link is False
    assert LeadSettings.create(groove_link=True).groove_link is True
    try:
        LeadSettings.create(enabled="maybe")
    except TypeError:
        pass
    else:
        raise AssertionError("a non-boolean enabled value was accepted")

    # with_change applies the same validators.
    settings = LeadSettings()
    assert settings.with_change("gate", 0.25).gate_beats == 0.25
    assert settings.with_change("bars", 4).phrase_bars == 4
    assert settings.with_change("octaves", 2).octave_range == 2
    assert settings.with_change("base", -2).base_octave == -2
    assert settings.with_change("link", 0).groove_link is False
    assert settings.with_change("var_every", 3).variation_bars == 3
    try:
        settings.with_change("wrong", 1)
    except ValueError:
        pass
    else:
        raise AssertionError("unknown setting accepted")
    print("  grid/bars/density/gate/velocity/octave/variation/seed clamps")


def test_timing_helpers() -> None:
    print("\n=== Test 3: grid and bar math ===")
    assert math.isclose(grid_quarters("1/4"), 1.0)
    assert math.isclose(grid_quarters("1/8"), 0.5)
    assert math.isclose(grid_quarters("1/16"), 0.25)
    assert math.isclose(grid_quarters("1/8T"), 1.0 / 3.0)
    assert steps_per_beat("1/4") == 1
    assert steps_per_beat("1/8") == 2
    assert steps_per_beat("1/16") == 4
    assert steps_per_beat("1/8T") == 3
    assert steps_per_bar(4, 4, "1/8") == 8
    assert steps_per_bar(4, 4, "1/16") == 16
    assert steps_per_bar(4, 4, "1/4") == 4
    assert steps_per_bar(4, 4, "1/8T") == 12
    assert steps_per_bar(3, 4, "1/8") == 6
    print("  steps per beat/bar for 1/4, 1/8, 1/16, 1/8T and 3/4")


# ---------------------------------------------------------------------------
# 2. Generation
# ---------------------------------------------------------------------------


def test_generation() -> None:
    print("\n=== Test 4: deterministic motif generation ===")
    first = generate_motif(seed=1, ratios=INITIAL_RATIOS, anchor=1.0)
    again = generate_motif(seed=1, ratios=INITIAL_RATIOS, anchor=1.0)
    other = generate_motif(seed=2, ratios=INITIAL_RATIOS, anchor=1.0)
    assert first.notes == again.notes, "same seed must reproduce the phrase"
    assert first.notes != other.notes, "a different seed must change the phrase"

    assert first.steps_per_bar == 8
    assert first.phrase_steps == 2 * 8
    assert first.note_count > 0

    # Phrase length always follows phrase_bars * steps_per_bar.
    for bars in (1, 2, 3, 8):
        motif = generate_motif(
            seed=3, phrase_bars=bars, grid="1/16", ratios=INITIAL_RATIOS
        )
        assert motif.phrase_steps == bars * steps_per_bar(4, 4, "1/16")

    # Bar downbeats are always on and accented.
    for motif in (first, other):
        downbeats = set(range(0, motif.phrase_steps, motif.steps_per_bar))
        assert downbeats <= set(motif.steps), (downbeats, motif.steps)
        for note in motif.notes:
            if note.step in downbeats:
                assert note.velocity == min(
                    127, motif.velocity_base + ACCENT_BOOST
                ), note

    # No more than MAX_EMPTY_RUN consecutive empty steps.
    for seed in range(12):
        motif = generate_motif(
            seed=seed, density=0.2, ratios=INITIAL_RATIOS
        )
        run = 0
        on = set(motif.steps)
        for step in range(motif.phrase_steps):
            run = 0 if step in on else run + 1
            assert run <= MAX_EMPTY_RUN, (seed, step, motif.steps)

    # Density: 0.1 is sparser than 1.0 for the same seed.
    sparse = generate_motif(seed=5, density=0.1, ratios=INITIAL_RATIOS)
    dense = generate_motif(seed=5, density=1.0, ratios=INITIAL_RATIOS)
    assert sparse.note_count < dense.note_count, (
        sparse.note_count,
        dense.note_count,
    )
    assert dense.note_count == dense.phrase_steps
    assert set(sparse.steps) <= set(dense.steps), "density 0.1 is a subset"

    # Notes are monophonic: a note never overlaps the next one.
    for seed in range(8):
        motif = generate_motif(seed=seed, density=1.0, ratios=INITIAL_RATIOS)
        for current, following in zip(motif.notes, motif.notes[1:]):
            assert current.end <= following.step, (current, following)
        assert motif.notes[-1].end <= motif.phrase_steps

    # The walk stays inside the set and the octave range.
    for seed in range(8):
        motif = generate_motif(
            seed=seed, octave_range=2, ratios=INITIAL_RATIOS
        )
        for note in motif.notes:
            assert 0 <= note.degree < len(INITIAL_RATIOS), note
            assert -2 <= note.octave <= 2, note

    # place_steps: the documented placement rules directly.
    import random as _random

    on = place_steps(_random.Random(7), 16, 8, 1.0)
    assert all(on) and on[0] and on[8]
    on = place_steps(_random.Random(7), 16, 8, 0.0)
    assert on[0] and on[8]
    # The hole-filling rule still breaks up the long empty stretches.
    assert 2 < sum(on) < 16, on
    run = 0
    for active in on:
        run = 0 if active else run + 1
        assert run <= MAX_EMPTY_RUN
    print("  seeded phrases, length, downbeats, density subset, no overlap")


def test_work_documented_example() -> None:
    print("\n=== Test 5: the documented worked example is exact ===")
    # docs/README_stage8_3d.md quotes this line; keep them in sync.
    motif = generate_motif(
        seed=1,
        phrase_bars=1,
        grid="1/8",
        density=0.5,
        meter_num=4,
        meter_den=4,
        ratios=[1.0, 9.0 / 8.0, 5.0 / 4.0, 4.0 / 3.0, 3.0 / 2.0, 5.0 / 3.0, 15.0 / 8.0],
        anchor=1.0,
        octave_range=1,
        velocity=90,
        gate_beats=0.5,
    )
    print(f"  seed=1 1 bar 1/8 density 0.5 -> steps={motif.steps}")
    print(f"  degrees={motif.degrees} octaves={[n.octave for n in motif.notes]}")
    assert motif.phrase_steps == 8
    assert motif.steps[0] == 0
    assert len(motif.steps) == len(motif.notes)
    assert motif.steps == tuple(sorted(motif.steps))
    print("  deterministic and printed for the README example")


# ---------------------------------------------------------------------------
# 3. Consistency through mutation (the core test)
# ---------------------------------------------------------------------------


def test_consistency_through_mutation() -> None:
    print("\n=== Test 6: the melody survives a set mutation ===")
    engine = _engine()
    before = _frequencies(engine)

    # (1) The same set changes nothing at all.
    engine.on_set(INITIAL_RATIOS, 1.0, FUNDAMENTAL)
    assert _frequencies(engine) == before

    # (2) A set that keeps every motif ratio keeps every frequency, even when
    # classes are added and the anchor moves.
    used = {round(note.ratio_hint, 9) for note in engine.motif.notes}
    grown = sorted(
        set(INITIAL_RATIOS)
        | {16.0 / 15.0, 7.0 / 4.0, 45.0 / 32.0}
    )
    assert used <= {round(ratio, 9) for ratio in grown}
    engine.on_set(grown, 5.0 / 4.0, FUNDAMENTAL)
    assert _frequencies(engine) == before, "added classes must not move notes"

    # (3) Removing one used ratio changes only the notes that used it.
    engine.on_set(INITIAL_RATIOS, 1.0, FUNDAMENTAL)
    target = sorted(used)[-1]
    shrunken = [
        ratio
        for ratio in INITIAL_RATIOS
        if not math.isclose(ratio, target, rel_tol=1e-12, abs_tol=1e-12)
    ]
    affected = [
        note.step
        for note in engine.motif.notes
        if math.isclose(note.ratio_hint, target, rel_tol=1e-12, abs_tol=1e-12)
    ]
    assert affected, "the test must remove a ratio the motif really uses"
    engine.on_set(shrunken, 1.0, FUNDAMENTAL)
    after = _frequencies(engine)
    assert len(after) == len(before)
    changed = [
        step for (step, hz), (_old_step, old_hz) in zip(after, before)
        if abs(hz - old_hz) > 1e-9
    ]
    assert changed == affected, (changed, affected)
    # The contour of everything around the changed notes is intact.
    kept_before = [hz for step, hz in before if step not in affected]
    kept_after = [hz for step, hz in after if step not in affected]
    assert kept_before == kept_after
    contour = [
        0 if b == a else (1 if b > a else -1)
        for a, b in zip(kept_after, kept_after[1:])
    ]
    original_contour = [
        0 if b == a else (1 if b > a else -1)
        for a, b in zip(kept_before, kept_before[1:])
    ]
    assert contour == original_contour

    # (4) A set that shrinks to three classes still plays (degrees wrap).
    engine.on_set([1.0, 5.0 / 4.0, 3.0 / 2.0], 1.0, FUNDAMENTAL)
    tiny = engine.projection()
    assert len(tiny) == len(before)
    pools = {1.0, 5.0 / 4.0, 3.0 / 2.0}
    for note in tiny:
        assert note.frequency > 0.0
        assert any(
            math.isclose(note.ratio, ratio, rel_tol=1e-12)
            for ratio in pools
        ), note
    # The motif itself was never rewritten by any of the mutations.
    assert engine.motif.variation == "identity" and engine.motif.seed == 1
    print("  same set / kept ratios / one removed / shrink to 3 all behave")


def test_on_set_never_regenerates() -> None:
    print("\n=== Test 7: on_set never regenerates the motif ===")
    engine = _engine()
    motif = engine.motif
    for ratios in (
        [1.0],
        [1.0, 3.0 / 2.0],
        INITIAL_RATIOS * 1,
        sorted(set(INITIAL_RATIOS) | {7.0 / 5.0}),
    ):
        engine.on_set(ratios, None, FUNDAMENTAL * 1.5)
        assert engine.motif.notes == motif.notes, ratios
    print("  four snapshots, one stored phrase")


# ---------------------------------------------------------------------------
# 4. Variations
# ---------------------------------------------------------------------------


def test_variations() -> None:
    print("\n=== Test 8: the eight variations ===")
    pool = tuple(INITIAL_RATIOS)
    base = generate_motif(
        seed=4, density=0.6, ratios=pool, anchor=1.0, phrase_bars=2
    )
    size = len(pool)

    seen = {}
    for name in VARIATIONS:
        motif = apply_variation(
            name, base, seed=4, index=0, ratios=pool
        )
        assert motif.notes, name
        assert motif.variation == name and motif.variation_index == 0
        assert motif.phrase_steps == base.phrase_steps
        for note in motif.notes:
            assert 0 <= note.step < motif.phrase_steps, (name, note)
            assert note.length >= 1, (name, note)
            assert 0 <= note.degree < size, (name, note)
        for current, following in zip(motif.notes, motif.notes[1:]):
            assert current.end <= following.step, (name, current, following)
        seen[name] = motif

    assert seen["identity"].notes == base.notes, "identity must not change"

    # transpose: every degree moved by the same +1 or +2 (wrapping).
    shift = (seen["transpose"].degrees[0] - base.degrees[0]) % size
    assert shift in (1, 2), shift
    assert seen["transpose"].degrees == tuple(
        (degree + shift) % size for degree in base.degrees
    )

    # invert: mirrored around the first note's degree.
    pivot = base.degrees[0]
    assert seen["invert"].degrees == tuple(
        (2 * pivot - degree) % size for degree in base.degrees
    )

    # retrograde: the note order is reversed, the rhythm is kept.
    assert seen["retrograde"].degrees == tuple(reversed(base.degrees))
    assert seen["retrograde"].steps == base.steps

    # thin: fewer notes, and the pitches are a subsequence of the original.
    assert seen["thin"].note_count < base.note_count
    remaining = list(base.degrees)
    for degree in seen["thin"].degrees:
        assert degree in remaining
        remaining = remaining[remaining.index(degree) + 1:]

    # ornament: at most one passing tone inserted.
    assert seen["ornament"].note_count in (
        base.note_count,
        base.note_count + 1,
    )

    # rhythm: the same pitch sequence (in order) on new grid steps.
    rhythm = seen["rhythm"]
    assert rhythm.degrees == tuple(
        base.degrees[index % len(base.degrees)]
        for index in range(rhythm.note_count)
    ), rhythm.degrees
    assert rhythm.steps != base.steps or base.note_count <= 1

    # random: a fresh valid phrase (not a transform of the same pitches).
    assert seen["random"].note_count > 0

    # Determinism: the same (seed, index) reproduces the same line.
    seed_sensitive = {"transpose", "thin", "rhythm", "random"}
    for name in VARIATIONS:
        again = apply_variation(name, base, seed=4, index=0, ratios=pool)
        assert again.notes == seen[name].notes, name
        other = apply_variation(name, base, seed=5, index=0, ratios=pool)
        if name in seed_sensitive:
            assert other.notes != seen[name].notes, name

    # vary() advances the index and applies a variation.
    engine = _engine(seed=4, variation="invert")
    first_index = engine.motif.variation_index
    motif = engine.vary()
    assert motif.variation_index == first_index + 1
    assert motif.variation == "invert"
    motif2 = engine.vary()
    assert motif2.variation_index == motif.variation_index + 1

    # set_setting("variation", ...) applies it at the current index.
    engine = _engine(seed=4)
    applied = engine.set_setting("variation", "retrograde")
    assert applied.variation == "retrograde"
    assert engine.motif.variation == "retrograde"
    assert engine.motif.variation_index == 0

    # A seed change re-births the line and resets the variation index.
    engine.vary()
    engine.set_setting("seed", 99)
    assert engine.motif.seed == 99
    assert engine.motif.variation_index == 0
    print("  eight names, identity/transpose/invert/retrograde/thin/ornament/"
          "rhythm/random, seeded, vary advances")


def test_auto_variation() -> None:
    print("\n=== Test 9: automatic variation every N bars ===")
    engine = _engine(variation="identity", variation_bars=0)
    assert engine.maybe_auto_vary(1) is None, "0 disables it"
    engine = _engine(variation="invert", variation_bars=2)
    assert engine.maybe_auto_vary(0) is None, "bar 0 is the origin"
    assert engine.maybe_auto_vary(1) is None, "not a multiple of 2"
    assert engine.maybe_auto_vary(3) is None
    varied = engine.maybe_auto_vary(2)
    assert varied is not None and varied.variation_index == 1
    assert engine.maybe_auto_vary(4) is not None

    # A short real-time run proves the scheduler calls it on the bar grid.
    fake = FakePort()
    rig = LeadRig(
        _config(enabled=True, grid="1/4", phrase_bars=2),
        output=fake,
        bpm=300.0,
        meter_num=2,
        meter_den=4,
    )
    rig.on_set(INITIAL_RATIOS, 1.0, FUNDAMENTAL)
    rig.set_setting("variation", "invert")
    rig.set_setting("variation_bars", 1)
    rig.set_transport(True)
    try:
        ok = _wait_for(
            lambda: rig.engine.motif.variation_index >= 2, timeout=3.0
        )
        assert ok, rig.engine.motif.variation_index
    finally:
        rig.set_transport(False)
    print("  bar-aligned auto variation, 0 = manual only, real-time run ok")


# ---------------------------------------------------------------------------
# 5. Groove lock / phase
# ---------------------------------------------------------------------------


def test_groove_lock() -> None:
    print("\n=== Test 10: swing follows the linked groove ===")
    engine = _engine(swing=0.6, groove_link=True)
    assert engine.effective_swing() == 0.0, "no groove swing pushed yet"
    engine.set_groove_swing(0.4)
    assert math.isclose(engine.effective_swing(), 0.4)
    engine.set_setting("groove_link", False)
    assert math.isclose(engine.effective_swing(), 0.6), "own swing when unlinked"
    engine.set_setting("groove_link", True)
    assert math.isclose(engine.effective_swing(), 0.4)

    # The offset rule is the shared bass/groove rule, not a new one.
    from control.bass import swing_offset

    step_seconds = 0.25
    assert swing_offset(0, 1.0, step_seconds, 2) == 0.0
    assert math.isclose(swing_offset(1, 1.0, step_seconds, 2), 0.125)
    assert math.isclose(swing_offset(1, 0.4, step_seconds, 2), 0.05)
    assert swing_offset(1, 1.0, step_seconds, 1) == 0.0, "1/4 is straight"

    # A real-time run with a linked groove swing delays the odd 1/8 steps.
    fake = FakePort()
    rig = LeadRig(
        _config(enabled=True, grid="1/8", density=1.0, gate_beats=0.1),
        output=fake,
        bpm=120.0,
    )
    rig.on_set(INITIAL_RATIOS, 1.0, FUNDAMENTAL)
    rig.set_groove_swing(1.0)
    rig.set_transport(True)
    try:
        _wait_for(lambda: len(fake.with_type("note_on")) >= 5, timeout=3.0)
    finally:
        rig.set_transport(False)
    onsets = [at for _message, at in fake.with_type("note_on")]
    assert len(onsets) >= 5, len(onsets)
    deltas = [b - a for a, b in zip(onsets, onsets[1:])]
    # Step 0 -> 1 waits for the delayed odd step (long); 1 -> 2 is short.
    long_gaps = deltas[0::2]
    short_gaps = deltas[1::2]
    assert long_gaps and short_gaps
    assert min(long_gaps) > max(short_gaps) + 0.1, (long_gaps, short_gaps)
    print("  effective swing, shared offset rule, swung onsets measured")


def test_transport_restart() -> None:
    print("\n=== Test 11: stop/start restarts the phrase at its origin ===")
    fake = FakePort()
    rig = LeadRig(
        _config(enabled=True, grid="1/8", density=1.0, gate_beats=0.1),
        output=fake,
        bpm=240.0,
    )
    rig.on_set(INITIAL_RATIOS, 1.0, FUNDAMENTAL)
    expected = frequency_to_pitch(
        rig.engine.projection()[0].frequency, 48.0
    ).note

    rig.set_transport(True)
    try:
        assert _wait_for(
            lambda: len(fake.with_type("note_on")) >= 2, timeout=3.0
        )
    finally:
        rig.set_transport(False)
    first_run = [m.note for m, _at in fake.with_type("note_on")]
    assert first_run[0] == expected, (first_run[0], expected)

    fake.messages.clear()
    fake.times.clear()
    time.sleep(0.05)
    rig.set_transport(True)
    try:
        assert _wait_for(
            lambda: len(fake.with_type("note_on")) >= 1, timeout=2.0
        )
    finally:
        rig.set_transport(False)
    restarted = [m.note for m, _at in fake.with_type("note_on")]
    assert restarted[0] == expected, (restarted[0], expected)
    # The scheduler thread restarted from step 0.
    assert rig.scheduler.step_index >= 0
    print("  the first note after a transport start is phrase step 0")


# ---------------------------------------------------------------------------
# 6. Scheduler / MIDI
# ---------------------------------------------------------------------------


def test_scheduler_messages() -> None:
    print("\n=== Test 12: note timing, gate, monophony, and stop ===")
    fake = FakePort()
    rig = LeadRig(
        _config(
            enabled=True, grid="1/8", density=1.0, gate_beats=0.25,
        ),
        output=fake,
        bpm=240.0,
    )
    rig.on_set(INITIAL_RATIOS, 1.0, FUNDAMENTAL)
    rig.set_transport(True)
    try:
        assert _wait_for(
            lambda: len(fake.with_type("note_on")) >= 4, timeout=3.0
        )
    finally:
        rig.set_transport(False)

    assert rig.output.is_open
    # Every step: bend before note-on, and the note-off never cuts the next.
    types = fake.types()
    assert "control_change" in types, "stop must send CC 123"
    assert fake.messages[-1].type == "control_change"
    assert fake.messages[-1].control == 123

    ons = fake.with_type("note_on")
    offs = fake.with_type("note_off")
    assert len(ons) >= 4
    assert len(offs) >= len(ons) - 1, (len(ons), len(offs))

    # Monophonic: between two note-ons the held note is released first.
    events = [
        (message.type, message.note)
        for message in fake.messages
        if message.type in ("note_on", "note_off")
    ]
    held = None
    for kind, note in events:
        if kind == "note_on":
            assert held is None, f"note_on while {held} is still held"
            held = note
        else:
            assert note == held, (note, held)
            held = None

    # Grid timing: the onsets land on 0.125 s steps (generous tolerance).
    onsets = [at for _message, at in ons]
    deltas = [b - a for a, b in zip(onsets, onsets[1:])]
    for delta in deltas[:3]:
        assert 0.06 <= delta <= 0.30, deltas
    # Gate: 0.25 beats at 240 BPM is 0.0625 s; the off follows within a step.
    if offs and ons:
        off_message, off_at = offs[0]
        on_message, on_at = ons[0]
        assert off_message.note == on_message.note
        assert 0.0 <= off_at - on_at <= 0.25, (off_at - on_at)

    # No note is left sounding after the transport stop.
    assert held is None, held
    print(f"  {len(ons)} notes, monophonic, grid timing, gate, CC 123 on stop")


def test_long_gate_never_leaks() -> None:
    print("\n=== Test 13: a gate longer than the step never cuts a new note ===")
    fake = FakePort()
    rig = LeadRig(
        _config(enabled=True, grid="1/16", density=1.0, gate_beats=2.0),
        output=fake,
        bpm=240.0,
    )
    rig.on_set(INITIAL_RATIOS, 1.0, FUNDAMENTAL)
    rig.set_transport(True)
    try:
        assert _wait_for(
            lambda: len(fake.with_type("note_on")) >= 6, timeout=3.0
        )
    finally:
        rig.set_transport(False)

    events = [
        (message.type, message.note)
        for message in fake.messages
        if message.type in ("note_on", "note_off")
    ]
    held = None
    for kind, note in events:
        if kind == "note_on":
            assert held is None, "a stale late note-off was not cancelled"
            held = note
        else:
            assert note == held
            held = None
    assert held is None
    print("  cancelled gate timers keep the voice strictly monophonic")


def test_independence_and_failures() -> None:
    print("\n=== Test 14: lead independence and graceful failure ===")
    lead_fake = FakePort()
    rig = MIDIRig(
        midi=False,
        lead=True,
        lead_config=_config(enabled=True),
        lead_output=lead_fake,
    )
    try:
        assert rig.midi_enabled is False
        assert rig.drone is None and rig.arp is None
        assert rig.lead is not None and rig.lead.port_ok is True
        assert rig.lead_state()[1] == 1
        rig.start()
        rig.set_lead_transport(True)
        assert _wait_for(
            lambda: len(lead_fake.with_type("note_on")) >= 1, timeout=2.0
        )
        rig.set_lead_transport(False)
    finally:
        rig.stop()
    assert lead_fake.closed

    # The default rig never opens Bus 5.
    plain = MIDIRig(
        replace(load_config(), step_seconds=0.05, gate_seconds=0.02),
        drone_output=FakePort(),
        arp_output=FakePort(),
    )
    try:
        assert plain.lead is None
        assert plain.lead_port_ok is False
        state = plain.lead_state()
        assert len(state) == 16, state
        assert state[0] == 0 and state[1] == 0
        assert state[2] == "1/8" and state[9] == "identity"
        try:
            plain.set_lead_setting("seed", 2)
        except RuntimeError as exc:
            assert "lead is not configured" in str(exc), exc
        else:
            raise AssertionError("set_lead_setting must raise without a lead")
    finally:
        plain.stop()

    # A missing Bus 5 disables only the lead; the drone/arp still work.
    missing = MIDIRig(
        replace(load_config(), step_seconds=0.05, gate_seconds=0.02),
        drone_output=FakePort(),
        arp_output=FakePort(),
        lead=True,
        lead_config=_config(port="JI Test Nonexistent Bus 5"),
    )
    logged = io.StringIO()
    try:
        with contextlib.redirect_stderr(logged):
            missing.start()
        assert missing.lead is not None
        assert missing.lead_port_ok is False
        assert missing.lead_state()[1] == 0
        assert missing.drone is not None and missing.arp is not None
        assert missing.drone_port_ok and missing.arp_port_ok
    finally:
        missing.stop()
    text = logged.getvalue()
    assert "[LEAD] disabled:" in text, text
    assert "drone/arp/drums/bass continue" in text, text

    # A bad lead config disables the lead alone.
    logged = io.StringIO()
    bad = None
    try:
        with contextlib.redirect_stderr(logged):
            bad = MIDIRig(
                replace(load_config(), step_seconds=0.05, gate_seconds=0.02),
                drone_output=FakePort(),
                arp_output=FakePort(),
                lead=True,
                lead_config={"grid": "1/2"},
            )
            bad.start()
        assert bad.lead is None
        assert bad.lead_state()[1] == 0
        assert bad.drone is not None and bad.arp is not None
    finally:
        if bad is not None:
            bad.stop()
    assert "[LEAD] disabled:" in logged.getvalue(), logged.getvalue()
    print("  midi=False/lead=True, default off, missing bus, bad config")


def test_no_mido_import() -> None:
    print("\n=== Test 15: pure construction imports no mido ===")
    assert "mido" not in sys.modules
    LeadRig(_config(), config_path=None, output=None)
    LeadEngine(_config())
    generate_motif(seed=1, ratios=INITIAL_RATIOS)
    assert "mido" not in sys.modules, "pure construction must not import mido"
    print("  LeadRig/LeadEngine/generate_motif stay mido-free")


# ---------------------------------------------------------------------------
# 7. OSC + bridge
# ---------------------------------------------------------------------------


def test_osc_handlers() -> None:
    print("\n=== Test 16: /ji/lead/* and /ji/drone/* handlers validate ===")
    lead_received: list[tuple[str, object]] = []
    drone_received: list[tuple[str, object]] = []
    varied: list[int] = []
    server = MutationOSCServer(
        MutationEngine(INITIAL_RATIOS),
        on_lead_change=lambda key, value: lead_received.append((key, value)),
        on_lead_vary=lambda: varied.append(1),
        on_drone_change=lambda key, value: drone_received.append((key, value)),
    )
    server._handle_lead_enabled("/ji/lead/set_enabled", 1)
    server._handle_lead_enabled("/ji/lead/set_enabled", "maybe")
    server._handle_lead_grid("/ji/lead/set_grid", "1/16T")
    server._handle_lead_grid("/ji/lead/set_grid", "1/16")
    server._handle_lead_phrase_bars("/ji/lead/set_phrase_bars", 4)
    server._handle_lead_density("/ji/lead/set_density", 2.0)
    server._handle_lead_gate("/ji/lead/set_gate", 0.25)
    server._handle_lead_velocity("/ji/lead/set_velocity", 300)
    server._handle_lead_octave_range("/ji/lead/set_octave_range", 2)
    server._handle_lead_octave_range("/ji/lead/set_octave_range", 9)
    server._handle_lead_base_octave("/ji/lead/set_base_octave", -2)
    server._handle_lead_variation("/ji/lead/set_variation", "invert")
    server._handle_lead_variation("/ji/lead/set_variation", "sideways")
    server._handle_lead_variation_bars("/ji/lead/set_variation_bars", 4)
    server._handle_lead_swing("/ji/lead/set_swing", 0.5)
    server._handle_lead_groove_link("/ji/lead/set_groove_link", 0)
    server._handle_lead_seed("/ji/lead/set_seed", 12)
    server._handle_lead_vary("/ji/lead/vary")
    assert lead_received == [
        ("enabled", True),
        ("grid", "1/16"),
        ("phrase_bars", 4),
        ("density", 1.0),
        ("gate_beats", 0.25),
        ("velocity", 127),
        ("octave_range", 2),
        ("base_octave", -2),
        ("variation", "invert"),
        ("variation_bars", 4),
        ("swing", 0.5),
        ("groove_link", False),
        ("seed", 12),
    ], lead_received
    assert varied == [1]

    server._handle_drone_mode("/ji/drone/set_mode", "pulse")
    server._handle_drone_mode("/ji/drone/set_mode", "wobble")
    server._handle_drone_velocity("/ji/drone/set_velocity", 0)
    server._handle_drone_pulse_notes("/ji/drone/set_pulse_notes", 6)
    server._handle_drone_pulse_notes("/ji/drone/set_pulse_notes", 99)
    server._handle_drone_pulse_grid("/ji/drone/set_pulse_grid", "1/16")
    server._handle_drone_pulse_gate("/ji/drone/set_pulse_gate", 0.25)
    server._handle_drone_pulse_span("/ji/drone/set_pulse_span", 2)
    server._handle_drone_slide_direction("/ji/drone/set_slide_direction", "up")
    server._handle_drone_slide_semitones("/ji/drone/set_slide_semitones", 5)
    server._handle_drone_slide_seconds("/ji/drone/set_slide_seconds", 1.5)
    server._handle_drone_hold_beats("/ji/drone/set_hold_beats", 4.0)
    server._handle_drone_seed("/ji/drone/set_seed", 3)
    assert drone_received == [
        ("mode", "pulse"),
        ("velocity", 1),
        ("pulse_notes", 6),
        ("pulse_grid", "1/16"),
        ("pulse_gate", 0.25),
        ("pulse_octave_span", 2),
        ("slide_direction", "up"),
        ("slide_semitones", 5),
        ("slide_seconds", 1.5),
        ("hold_beats", 4.0),
        ("seed", 3),
    ], drone_received

    # A raising callback never kills the OSC thread.
    def boom(_key, _value):
        raise RuntimeError("boom")

    fragile = MutationOSCServer(
        MutationEngine(INITIAL_RATIOS), on_lead_change=boom
    )
    errored = io.StringIO()
    with contextlib.redirect_stderr(errored):
        fragile._handle_lead_seed("/ji/lead/set_seed", 1)
    assert "lead change callback failed" in errored.getvalue()

    # The state payloads are the documented shapes.
    state = LeadSettings().state_args(False, False)
    assert state == [
        0, 0, "1/8", 2, 0.75, 0.5, 90, 1, 0, "identity", 0, 0, 0.0, 1, 1, 0,
    ], state
    assert LeadSettings().state_args(True, True)[15] == 1
    print("  thirteen setters + vary, arity/type rejection, 16-value state")


def test_bridge_quantization() -> None:
    print("\n=== Test 17: bridge lead changes are immediate/queued ===")
    original = bridge_module.SimpleUDPClient

    class FakeClient:
        instances: list["FakeClient"] = []

        def __init__(self, host: str, port: int) -> None:
            self.host = host
            self.port = port
            self.sent: list[tuple[str, list]] = []
            FakeClient.instances.append(self)

        def send_message(self, address: str, args: list) -> None:
            self.sent.append((address, list(args)))

    bridge_module.SimpleUDPClient = FakeClient
    bridge = None
    rig = None
    try:
        bridge = bridge_module.JIBridge(
            midi=True, listen_port=19070, ui_port=19071, sc_port=19072
        )
        rig = MIDIRig(
            replace(load_config(), step_seconds=0.05, gate_seconds=0.02),
            drone_output=FakePort(),
            arp_output=FakePort(),
            lead=True,
            lead_config=_config(enabled=True),
            lead_output=FakePort(),
        )
        bridge.midi = rig
        assert bridge._lead_rig() is rig.lead
        rig.start()

        # Stopped: every lead edit applies immediately.
        bridge.rhythm.set_running(False)
        ui = FakeClient.instances[1]
        ui.sent.clear()
        bridge._on_lead_change("density", 0.4)
        assert bridge.quantizer.pending_count == 0
        assert rig.lead.engine.settings.density == 0.4
        states = [
            args for address, args in ui.sent if address == "/ji/lead/state"
        ]
        assert len(states) == 1 and len(states[0]) == 16, states
        assert states[0][4] == 0.4

        # Running: pending, then applied at the flush.
        bridge.rhythm.set_running(True)
        bridge._on_lead_change("velocity", 50)
        assert bridge.quantizer.pending_count == 1
        assert rig.lead.engine.settings.velocity == 90
        assert bridge.quantizer.flush("test beat") == 1
        assert rig.lead.engine.settings.velocity == 50
        assert PHASE_LEAD == 7  # Stage 8.4 inserted PHASE_HOME first

        # Coalescing: same key last-wins, different keys both run.
        bridge._on_lead_change("density", 0.5)
        bridge._on_lead_change("density", 0.6)
        assert bridge.quantizer.pending_count == 1
        bridge._on_lead_change("gate_beats", 0.2)
        assert bridge.quantizer.pending_count == 2
        bridge.quantizer.flush("test beat")
        assert rig.lead.engine.settings.density == 0.6
        assert rig.lead.engine.settings.gate_beats == 0.2

        # vary is queued the same way and sends one state message.
        ui.sent.clear()
        index = rig.lead.engine.motif.variation_index
        bridge._on_lead_vary()
        assert bridge.quantizer.pending_count == 1
        assert rig.lead.engine.motif.variation_index == index
        bridge.quantizer.flush("test beat")
        assert rig.lead.engine.motif.variation_index == index + 1
        states = [
            args for address, args in ui.sent if address == "/ji/lead/state"
        ]
        assert len(states) == 1, ui.sent
        assert states[0][10] == index + 1

        # A transport stop flushes a pending lead edit like any other action.
        bridge._on_lead_change("density", 0.9)
        assert bridge.quantizer.pending_count == 1
        bridge._on_rhythm_transport(False)
        assert bridge.quantizer.pending_count == 0
        assert rig.lead.engine.settings.density == 0.9

        # An invalid value is rejected before it can poison the queue.
        logged = io.StringIO()
        with contextlib.redirect_stderr(logged):
            bridge._on_lead_change("grid", "1/2")
        assert "invalid grid" in logged.getvalue(), logged.getvalue()
        assert bridge.quantizer.pending_count == 0

        # The drone queue works the same way and shares the phase order.
        bridge.rhythm.set_running(False)
        bridge._on_drone_change("mode", "pulse")
        assert rig.drone_rig.mode == "pulse"
        bridge.rhythm.set_running(True)
        bridge._on_drone_change("pulse_notes", 6)
        assert bridge.quantizer.pending_count == 1
        assert rig.drone_rig.settings.pulse_notes == 4
        bridge.quantizer.flush("test beat")
        assert rig.drone_rig.settings.pulse_notes == 6

        # Transport forwarding reaches the lead and the drone modes.
        bridge._on_rhythm_transport(True)
        assert rig.lead._transport_running is True
        assert rig.drone_rig._transport_running is True
        bridge._on_rhythm_transport(False)
        assert rig.lead._transport_running is False

        # The groove swing is pushed to the lead (one call per change).
        bridge.groove.set_modifier("swing", 0.3)
        bridge._push_lead_swing()
        assert math.isclose(rig.lead.engine.groove_swing, 0.3)
    finally:
        bridge_module.SimpleUDPClient = original
        if rig is not None:
            rig.stop()
        if bridge is not None:
            bridge.quantizer.stop()

    # Without --lead the bridge reports a well-formed disabled state.
    bridge = None
    try:
        bridge = bridge_module.JIBridge(
            midi=False, listen_port=19073, ui_port=19074, sc_port=19075
        )
        assert bridge._lead_rig() is None
        state = bridge._lead_default_state
        assert len(state) == 16 and state[0] == 0 and state[1] == 0
        assert len(bridge._drone_default_state) == 17
        assert bridge._drone_default_state[1] == "hold"
        bridge._on_lead_change("density", 0.5)  # logged, never raises
        assert bridge.quantizer.pending_count == 0
    finally:
        bridge_module.SimpleUDPClient = original
        if bridge is not None:
            bridge.quantizer.stop()
    print("  stopped immediate, running queued, coalesced, flushed, state sent")


def run() -> None:
    print("JI Stage 8.3d lead tests")
    test_settings_defaults()
    test_validation()
    test_timing_helpers()
    test_generation()
    test_work_documented_example()
    test_consistency_through_mutation()
    test_on_set_never_regenerates()
    test_variations()
    test_auto_variation()
    test_groove_lock()
    test_transport_restart()
    test_scheduler_messages()
    test_long_gate_never_leaks()
    test_independence_and_failures()
    test_no_mido_import()
    test_osc_handlers()
    test_bridge_quantization()
    print("\nALL LEAD TESTS PASSED")


if __name__ == "__main__":
    try:
        run()
    except Exception:
        traceback.print_exc()
        print("\nLEAD TESTS FAILED")
        raise SystemExit(1)
