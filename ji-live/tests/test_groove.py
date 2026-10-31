#!/usr/bin/env python3
"""Stage 8.2 pure groove tests: presets, grid, modifiers, seeded generation.

Run from ``ji-live/``::

    uv run --with python-osc python tests/test_groove.py

No audio, MIDI hardware, mido, PyQt6, sockets, or sleeping.  The optional drum
engine integration at the end uses the pure engine only.
"""

from __future__ import annotations

import random
import sys
import traceback
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

import control.groove as groove
from control.drums import DrumConfig, DrumEngine


def _bar(
    preset: str = "four",
    density: float = 0.0,
    syncopation: float = 0.0,
    swing: float = 0.0,
    probability: float = 0.0,
    seed: int = 1,
    meter: tuple[int, int] = (4, 4),
    bar_index: int = 0,
) -> groove.GrooveBar:
    spec = groove.GrooveSpec.create(
        preset, density, syncopation, swing, probability, seed
    )
    grid = groove.preset_grid(preset, meter[0], meter[1])
    return groove.build_bar(spec, grid, meter[0], meter[1], bar_index)


# ---------------------------------------------------------------------------
# Presets
# ---------------------------------------------------------------------------


def test_four_matches_stage_81() -> None:
    print("\n=== Test 1: 'four' reproduces the Stage 8.1 pattern ===")
    grid = groove.preset_grid("four", 4, 4)
    assert grid.masks[0] == 0x1111  # kick1: 0,4,8,12
    assert grid.masks[2] == 0x1010  # snare1: 4,12
    assert grid.masks[6] == 0x4444  # perc3: 2,6,10,14

    expected = (
        (0,),
        (),
        (6,),
        (),
        (0, 2),
        (),
        (6,),
        (),
        (0,),
        (),
        (6,),
        (),
        (0, 2),
        (),
        (6,),
        (),
    )
    assert _bar("four").steps == expected

    # The Stage 8.1 non-4/4 rule is preserved exactly.
    cases = {
        (3, 4): (12, [0, 4, 8], [4], [2, 6, 10]),
        (6, 8): (12, [0, 4, 8], [4], [2, 6, 10]),
        (5, 4): (20, [0, 4, 8, 12, 16], [4, 12], [2, 6, 10, 14, 18]),
        (7, 8): (14, [0, 4, 8, 12], [4, 12], [2, 6, 10]),
        (2, 4): (8, [0, 4], [4], [2, 6]),
    }
    for meter, (steps, kicks, snares, hats) in cases.items():
        built = _bar("four", meter=meter)
        assert built.bar_steps == steps, (meter, built.bar_steps)
        actual_kicks = [i for i, lanes in enumerate(built.steps) if 0 in lanes]
        actual_snares = [i for i, lanes in enumerate(built.steps) if 2 in lanes]
        actual_hats = [i for i, lanes in enumerate(built.steps) if 6 in lanes]
        assert actual_kicks == kicks, (meter, actual_kicks)
        assert actual_snares == snares, (meter, actual_snares)
        assert actual_hats == hats, (meter, actual_hats)

    # 12/8 is 6 quarters long, but the 8.1 rule still only places snares on
    # quarter beats 2 and 4.
    long_bar = _bar("four", meter=(12, 8))
    assert long_bar.bar_steps == 24
    assert [i for i, lanes in enumerate(long_bar.steps) if 2 in lanes] == [4, 12]
    print("  four = kick quarter, snare 2/4, closed hat offbeat 8ths")


def test_other_presets_documented() -> None:
    print("\n=== Test 2: break/half/techno are deterministic and documented ===")
    expected = {
        "break": {
            "kick1": [0, 6, 10],
            "snare1": [4, 12],
            "perc2": [4, 12],
            "perc3": [0, 2, 4, 6, 8, 10, 12, 14],
        },
        "half": {
            "kick1": [0, 8],
            "snare1": [8],
            "perc3": [0, 4, 8, 12],
        },
        "techno": {
            "kick1": [0, 4, 8, 12],
            "perc2": [4, 12],
            "perc3": [0, 2, 4, 6, 8, 10, 12, 14],
            "perc5": [2, 6, 10, 14],
        },
    }
    names = [lane for lane in groove.DEFAULT_LANE_NAMES]
    for preset, roles in expected.items():
        grid = groove.preset_grid(preset, 4, 4)
        for role, steps in roles.items():
            mask = grid.masks[names.index(role)]
            actual = [step for step in range(16) if mask & (1 << step)]
            assert actual == steps, (preset, role, actual)
        assert _bar(preset).steps == _bar(preset).steps

    # Bars shorter than 16 truncate; longer bars tile the template.
    break_3_4 = _bar("break", meter=(3, 4))
    assert break_3_4.bar_steps == 12
    assert [i for i, lanes in enumerate(break_3_4.steps) if 0 in lanes] == [0, 6, 10]
    assert [i for i, lanes in enumerate(break_3_4.steps) if 2 in lanes] == [4]
    assert [i for i, lanes in enumerate(break_3_4.steps) if 6 in lanes] == [
        0, 2, 4, 6, 8, 10
    ]

    break_5_4 = _bar("break", meter=(5, 4))
    assert break_5_4.bar_steps == 20
    assert [i for i, lanes in enumerate(break_5_4.steps) if 0 in lanes] == [
        0, 6, 10, 16
    ]
    assert [i for i, lanes in enumerate(break_5_4.steps) if 2 in lanes] == [4, 12]
    assert [i for i, lanes in enumerate(break_5_4.steps) if 6 in lanes] == list(
        range(0, 20, 2)
    )

    assert _bar("break", meter=(12, 8)).bar_steps == 24
    print("  presets match their templates; short bars truncate, long bars tile")


# ---------------------------------------------------------------------------
# Modifiers
# ---------------------------------------------------------------------------


def test_density_syncopation_probability() -> None:
    print("\n=== Test 3: density/syncopation/probability bounds and 0 identity ===")
    assert groove.clamp_unit(-1) == 0.0
    assert groove.clamp_unit(2) == 1.0
    assert groove.clamp_unit(0.25) == 0.25
    for bad in (float("nan"), float("inf")):
        try:
            groove.clamp_unit(bad)
        except ValueError:
            pass
        else:
            raise AssertionError(f"{bad} accepted")
    try:
        groove.clamp_unit("nope")
    except TypeError:
        pass
    else:
        raise AssertionError("string accepted")

    core = _bar("break").steps
    assert _bar("break", density=0.0).steps == core
    assert _bar("break", syncopation=0.0).steps == core
    assert _bar("break", probability=0.0).steps == core
    assert _bar("break", density=1.0, syncopation=1.0, probability=0.0).steps == core
    assert _bar("break", density=0.0, probability=1.0).steps == core
    assert _bar("break", syncopation=0.0, probability=1.0).steps == core

    # Probability never drops a core grid hit.
    core_cells = {
        (lane, step)
        for step, lanes in enumerate(core)
        for lane in lanes
    }
    for seed in range(5):
        built = _bar(
            "break", density=1.0, syncopation=1.0, probability=1.0, seed=seed
        )
        for lane, step in core_cells:
            assert lane in built.steps[step], (seed, lane, step, built.steps)

    # Density adds only percussion-lane ghosts, and only at empty cells.
    dense = _bar("break", density=1.0, probability=1.0, seed=1)
    added = 0
    for step, lanes in enumerate(dense.steps):
        for lane in lanes:
            if lane not in core[step]:
                assert lane >= 4, (step, lane)
                added += 1
    assert added == 8, added  # round(1.0 * 16 / 2)
    assert _bar("break", density=0.5, probability=1.0, seed=1).steps != core

    # Syncopation adds anticipation 16ths before non-quarter core hits.
    straight = _bar("four").steps
    sync = _bar("four", syncopation=1.0, probability=1.0).steps
    assert sync[1] == (6,)
    assert sync[5] == (6,)
    assert sync[9] == (6,)
    assert sync[13] == (6,)
    for step, lanes in enumerate(straight):
        for lane in lanes:
            assert lane in sync[step], (step, lane, sync[step])

    # Bounds clamp safely rather than producing unsafe values.
    spec = groove.GrooveSpec.create("four", -2, 5, 1.5, 9, -7)
    assert spec.density == 0.0
    assert spec.syncopation == 1.0
    assert spec.swing == 1.0
    assert spec.probability == 1.0
    assert spec.seed >= 0
    print("  zero modifiers are identity; ghosts never replace core hits")


def test_seeded_reproducibility() -> None:
    print("\n=== Test 4: same seed/bar reproducible; different seed differs ===")
    kwargs = dict(
        preset="break",
        density=0.8,
        syncopation=0.6,
        probability=1.0,
        seed=7,
    )
    bars = [_bar(bar_index=index, **kwargs).steps for index in range(4)]
    assert bars == [_bar(bar_index=index, **kwargs).steps for index in range(4)]
    assert len(set(bars)) > 1, "ghosts should vary by bar index"

    other_seed = dict(kwargs)
    other_seed["seed"] = 8
    assert _bar(bar_index=0, **other_seed).steps != bars[0]

    # Ambient random state must not influence generation.
    random.seed(12345)
    first = _bar(bar_index=2, **kwargs).steps
    random.seed(99999)
    random.random()
    second = _bar(bar_index=2, **kwargs).steps
    assert first == second
    print("  seeded bars are stable; ambient random state is ignored")


def test_swing() -> None:
    print("\n=== Test 5: swing offset 0..1 ===")
    step = 0.125
    assert groove.swing_offset(1, 0.0, step) == 0.0
    assert groove.swing_offset(1, 1.0, step) == step * 0.5
    assert groove.swing_offset(1, 0.5, step) == step * 0.25
    for even in (0, 2, 4, 14):
        assert groove.swing_offset(even, 1.0, step) == 0.0
    assert groove.swing_offset(5, -1.0, step) == 0.0
    assert groove.swing_offset(5, 9.0, step) == step * 0.5
    try:
        groove.swing_offset(1, 0.5, 0.0)
    except ValueError:
        pass
    else:
        raise AssertionError("zero step length accepted")
    print("  even steps straight; odd steps up to 50% late")


# ---------------------------------------------------------------------------
# Grid
# ---------------------------------------------------------------------------


def test_grid_validation_and_encoding() -> None:
    print("\n=== Test 6: grid bit/mask encoding and validation ===")
    empty = groove.GrooveGrid.empty()
    assert empty.masks == (0,) * 16
    assert empty.payload() == [0] * 32

    grid = empty.with_cell(0, 0, True)
    assert grid.is_on(0, 0) and not grid.is_on(0, 1)
    assert grid.payload()[:2] == [1, 0]
    grid = grid.with_cell(15, 31, True)
    assert grid.is_on(15, 31)
    assert grid.payload()[30:] == [0, 0x8000]
    updated = grid.with_cell(0, 0, False)
    assert not updated.is_on(0, 0)
    assert grid.is_on(0, 0)  # immutable
    assert updated.toggled(0, 0).is_on(0, 0)

    pairs = grid.pairs()
    assert len(pairs) == 16
    assert groove.GrooveGrid.from_pairs(pairs) == grid
    assert groove.GrooveGrid.from_bits({0: [0, 4, 8], 6: [2]}).is_on(6, 2)

    for bad in (
        lambda: groove.validate_lane(16),
        lambda: groove.validate_lane(-1),
        lambda: groove.validate_step(32),
        lambda: groove.validate_step(-1),
        lambda: groove.GrooveGrid(tuple([0] * 15)),
        lambda: groove.GrooveGrid(tuple([1 << 32] + [0] * 15)),
    ):
        try:
            bad()
        except (TypeError, ValueError):
            pass
        else:
            raise AssertionError("invalid grid value accepted")

    # 16/2 is 128 steps; the editable grid is capped at 32 and generated bars
    # repeat those 32 columns across the rest of the bar.
    state = groove.Groove(spec=groove.GrooveSpec.create("four"), meter_num=16, meter_den=2)
    assert state.steps_per_bar == 128
    assert state.grid_steps == 32
    assert state.bar(0).bar_steps == 128
    assert state.lanes_at(0, 0) == state.lanes_at(32, 0)

    editor = groove.Groove()
    assert editor.grid_steps == 16
    assert editor.set_cell(1, 3, True) is True
    assert editor.grid.is_on(1, 3)
    for bad in (
        lambda: editor.set_cell(16, 0, True),
        lambda: editor.set_cell(0, 32, True),
    ):
        try:
            bad()
        except (TypeError, ValueError):
            pass
        else:
            raise AssertionError("invalid cell accepted")
    print("  immutable bit grid, 32-int payload, lane 0..15 / step 0..31")


def test_stateful_groove() -> None:
    print("\n=== Test 7: preset/grid/meter state transitions ===")
    state = groove.Groove()
    assert state.spec.preset == "four"
    assert state.grid == groove.preset_grid("four", 4, 4)
    assert state.as_status_args() == [
        "four", 0.0, 0.0, 0.0, 0.0, 1, 16
    ]

    state.set_cell(4, 1, True)
    assert state.grid.is_on(4, 1)
    state.set_preset("break")
    assert not state.grid.is_on(4, 1), "preset change discards manual edits"
    assert state.grid == groove.preset_grid("break", 4, 4)

    state.set_density(0.5)
    state.set_density(9)
    assert state.spec.density == 1.0
    state.set_seed(42)
    assert state.spec.seed == 42

    changed = state.set_meter(3, 4)
    assert changed == groove.preset_grid("break", 3, 4)
    assert state.steps_per_bar == 12
    assert state.grid_steps == 12
    assert state.spec.density == 1.0  # modifiers survive meter change

    state.set_cell(0, 1, True)
    state.reset_grid()
    assert not state.grid.is_on(0, 1)
    print("  presets reload the grid; modifiers survive meter changes")


# ---------------------------------------------------------------------------
# Drum engine integration
# ---------------------------------------------------------------------------


def test_drum_engine_integration() -> None:
    print("\n=== Test 8: DrumEngine uses the generated groove and swing ===")
    engine = DrumEngine(DrumConfig(bpm=120, gate_seconds=0.01))
    assert engine.notes_for_step(0) == (36,)
    assert engine.notes_for_step(2) == (42,)
    assert engine.notes_for_step(4) == (36, 38)
    assert engine.notes_for_step(1) == ()

    engine.set_preset("half")
    assert engine.notes_for_step(0) == (36, 42)
    assert engine.notes_for_step(4) == (42,)
    assert engine.notes_for_step(8) == (36, 38, 42)

    # A manual cell edit adds a note; a preset reload discards it.
    engine.set_step(0, 1, True)
    assert 36 in engine.notes_for_step(1)
    engine.set_preset("half")
    assert 36 not in engine.notes_for_step(1)

    # Ghosts require probability > 0, and the lane mask still gates them.
    engine.set_preset("break")
    engine.set_lane(43, True)  # perc4: a default-off lane the ghosts use
    engine.set_density(1.0)
    engine.set_probability(0.0)
    core_notes = [engine.notes_for_step(step) for step in range(16)]
    engine.set_probability(1.0)
    ghost_notes = [engine.notes_for_step(step) for step in range(16)]
    assert ghost_notes != core_notes
    for straight, ghosted in zip(core_notes, ghost_notes):
        assert set(straight) <= set(ghosted), (straight, ghosted)

    engine.set_probability(0.0)
    engine.set_density(0.0)
    engine.set_seed(5)
    engine.set_swing(1.0)
    assert engine.swing_offset(0, 0.125) == 0.0
    assert engine.swing_offset(1, 0.125) == 0.0625
    assert engine.groove_spec.swing == 1.0

    pattern = engine.set_meter(7, 8)
    assert pattern.bar_steps == 14
    assert engine.groove.grid_steps == 14
    assert engine.groove.spec.swing == 1.0
    print("  engine preset/grid/ghost/swing/meter integration works")


# ---------------------------------------------------------------------------
# Runner
# ---------------------------------------------------------------------------


def run_pure() -> None:
    print("JI Stage 8.2 pure groove tests")
    test_four_matches_stage_81()
    test_other_presets_documented()
    test_density_syncopation_probability()
    test_seeded_reproducibility()
    test_swing()
    test_grid_validation_and_encoding()
    test_stateful_groove()
    test_drum_engine_integration()
    print("\nALL GROOVE TESTS PASSED")


def main() -> int:
    try:
        run_pure()
    except Exception:
        traceback.print_exc()
        print("\nGROOVE TESTS FAILED", file=sys.stderr)
        return 1
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
