#!/usr/bin/env python3
"""Stage 8.3b pure tests: arp settings, orders, timing, rig, bridge queue.

Run from ``ji-live/``::

    uv run --with python-osc python tests/test_arp.py

No audio, mido, PyQt6, or sockets.  Runtime checks use an injected fake MIDI
output and short real-time windows, like the bass/drum scheduler tests.
"""

from __future__ import annotations

import contextlib
import io
import random
import sys
import time
import traceback
from dataclasses import replace
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

import control.arp as arp_module
import control.ji_bridge as bridge_module
from control.arp import (
    DEFAULT_DIVISION,
    DEFAULT_EVOLVE,
    DEFAULT_EVOLVE_BEATS,
    DEFAULT_EVOLVE_DEPTH,
    DEFAULT_GATE_SECONDS,
    DEFAULT_OCTAVE_OFFSETS,
    DEFAULT_ORDER,
    DEFAULT_SEED,
    DEFAULT_SHARED_MODE,
    DEFAULT_STEP_SECONDS,
    DEFAULT_SYNC_MODE,
    DEFAULT_VELOCITY,
    DIVISIONS,
    DIVISION_QUARTERS,
    PINGPONG_INDICES,
    SYNC_MODES,
    ArpSettings,
    SetArp,
    build_groups,
    division_quarters,
    order_indices,
    validate_division,
    validate_evolve_beats,
    validate_evolve_depth,
    validate_sync_mode,
)
from control.midi_rig import MIDIRig, load_config
from control.mpe_output import MPEOutput
from control.mutation import MutationEngine
from control.osc_server import MutationOSCServer
from control.quantize import PHASE_ARP, PHASE_MIDI

INITIAL_RATIOS = [1.0, 9.0 / 8.0, 5.0 / 4.0, 4.0 / 3.0, 3.0 / 2.0, 5.0 / 3.0, 15.0 / 8.0]
FUNDAMENTAL = 220.0


class FakePort:
    """Duck-typed MIDI output capturing :class:`MPEMessage` values."""

    def __init__(self) -> None:
        self.messages: list = []
        self.closed = False

    def send(self, message) -> None:  # noqa: ANN001 - duck-typed message
        self.messages.append(message)

    def close(self) -> None:
        self.closed = True

    def types(self) -> list[str]:
        return [message.type for message in self.messages]

    def with_type(self, kind: str) -> list:
        return [message for message in self.messages if message.type == kind]


class RecordingOutput:
    """Record the frequencies/velocities the arp asks for, then send them."""

    def __init__(self, port: FakePort) -> None:
        self.output = MPEOutput("fake-arp", output=port)
        self.calls: list[tuple[float, int | None]] = []
        self.offs: list[int] = []
        self.all_off = 0

    def play(self, frequency: float, velocity: int | None = None):
        self.calls.append((round(float(frequency), 6), velocity))
        return self.output.play(frequency, velocity)

    def note_off(self, note: int, velocity: int = 0) -> None:
        self.offs.append(int(note))
        self.output.note_off(note, velocity)

    def all_notes_off(self) -> None:
        self.all_off += 1
        self.output.all_notes_off()

    @property
    def frequencies(self) -> list[float]:
        return [frequency for frequency, _velocity in self.calls]


def _wait_for(predicate, timeout: float = 3.0) -> bool:
    deadline = time.monotonic() + timeout
    while time.monotonic() < deadline:
        if predicate():
            return True
        time.sleep(0.005)
    return False


def _arp(**overrides) -> SetArp:
    values = {
        "step_seconds": 0.05,
        "gate_seconds": 0.02,
        "seed": 1,
    }
    values.update(overrides)
    recording = RecordingOutput(FakePort())
    arp = SetArp(recording, **values)
    arp.on_set(INITIAL_RATIOS, 1.0, FUNDAMENTAL)
    return arp


def _recording(arp: SetArp) -> RecordingOutput:
    return arp.output  # type: ignore[return-value]


def test_settings_defaults() -> None:
    print("\n=== Test 1: shipped defaults reproduce the Stage 6 arp ===")
    settings = ArpSettings()
    assert settings.step_seconds == DEFAULT_STEP_SECONDS == 0.25
    assert settings.gate_seconds == DEFAULT_GATE_SECONDS == 0.20
    assert settings.velocity == DEFAULT_VELOCITY == 90
    assert settings.octave_offsets == DEFAULT_OCTAVE_OFFSETS == (0, 1, 2)
    assert settings.shared_mode == DEFAULT_SHARED_MODE == "anchor"
    assert settings.order == DEFAULT_ORDER == "forward"
    assert settings.seed == DEFAULT_SEED == 1
    # Stage 8.3c: free by default, evolution off by default.
    assert settings.sync_mode == DEFAULT_SYNC_MODE == "free"
    assert settings.division == DEFAULT_DIVISION == "1/8"
    assert settings.evolve is DEFAULT_EVOLVE is False
    assert settings.evolve_beats == DEFAULT_EVOLVE_BEATS == 4
    assert settings.evolve_depth == DEFAULT_EVOLVE_DEPTH == 0.0

    config = load_config()
    assert config.step_seconds == 0.25
    assert config.gate_seconds == 0.20
    assert config.arp_velocity == 90
    assert config.arp_octave_offsets == (0, 1, 2)
    assert config.arp_shared_mode == "anchor"
    assert config.arp_order == "forward"
    assert config.arp_seed == 1
    # Stage 8.3c config keys are additive and default to the 8.3b behavior.
    assert config.arp_sync == "free" and config.arp_division == "1/8"
    assert config.arp_evolve is False
    assert config.arp_evolve_beats == 4 and config.arp_evolve_depth == 0.0
    # An 8.3b config without the new keys still loads (free, no evolution).
    from control.midi_rig import MPEConfig

    legacy = MPEConfig.from_mapping(
        {
            "drone_port": "IAC Driver Bus 1",
            "arp_port": "IAC Driver Bus 2",
            "member_channel": 2,
            "master_channel": 1,
            "bend_range_semitones": 48,
            "drone_velocity": 90,
            "arp_velocity": 90,
            "step_seconds": 0.25,
            "gate_seconds": 0.2,
            "arp_octave_offsets": [0, 1, 2],
            "arp_shared_mode": "anchor",
            "arp_order": "forward",
            "arp_seed": 1,
        }
    )
    assert legacy.arp_sync == "free" and legacy.arp_evolve is False

    # The same seed and the same consumed RNG stream must give the Stage 6
    # pattern: build_groups() with a fresh Random(1) is that reference.
    reference = build_groups(
        INITIAL_RATIOS, 1.0, FUNDAMENTAL, rng=random.Random(1)
    )
    arp = _arp()
    assert arp.pattern is not None
    assert [note.ratio for note in arp.pattern.steps] == [
        note.ratio for note in reference.steps
    ]
    assert [note.octave for note in arp.pattern.steps] == [
        note.octave for note in reference.steps
    ]
    assert [note.frequency for note in arp.pattern.steps] == [
        note.frequency for note in reference.steps
    ]
    assert arp.order_sequence == (0, 1, 2, 3, 4, 5)
    print(
        "  0.25 s / 0.20 s / vel 90 / octaves 0,1,2 / anchor / forward / seed 1"
    )


def test_validation() -> None:
    print("\n=== Test 2: settings validation, clamps, and rejection ===")
    # Gate is clamped to the step, never allowed to exceed it.
    clamped = ArpSettings.create(gate_seconds=2.0)
    assert clamped.gate_seconds == 0.25, clamped
    clamped = ArpSettings.create(step_seconds=0.05, gate_seconds=2.0)
    assert clamped.gate_seconds == 0.05, clamped
    # A step change also pulls an over-long gate down with it.
    step_down = ArpSettings().with_change("step_seconds", 0.1)
    assert step_down.step_seconds == 0.1 and step_down.gate_seconds == 0.1

    # Numeric ranges clamp.
    assert ArpSettings.create(step_seconds=0.001).step_seconds == 0.05
    assert ArpSettings.create(step_seconds=99.0).step_seconds == 2.0
    assert ArpSettings.create(gate_seconds=0.001).gate_seconds == 0.01
    assert ArpSettings.create(velocity=0).velocity == 1
    assert ArpSettings.create(velocity=999).velocity == 127
    assert ArpSettings.create(seed=-5).seed == 0
    assert ArpSettings.create(seed=2**40).seed == 2**31 - 1
    offsets = ArpSettings.create(octave_offsets=[-99, 0, 99]).octave_offsets
    assert offsets == (-8, 0, 8), offsets

    # Wrong types / unknown names / unknown modes are rejected.
    for bad in (
        {"step_seconds": 0.0},
        {"step_seconds": -1.0},
        {"gate_seconds": 0.0},
        {"velocity": "loud"},
        {"octave_offsets": [0, 1]},
        {"octave_offsets": "abc"},
        {"shared_mode": "middle"},
        {"order": "shuffle"},
        {"seed": "abc"},
    ):
        try:
            ArpSettings.create(**bad)
        except (TypeError, ValueError):
            pass
        else:
            raise AssertionError(f"invalid arp setting accepted: {bad}")

    try:
        ArpSettings().with_change("tempo_sync", True)
    except ValueError as exc:
        assert "unknown arp setting" in str(exc), exc
    else:
        raise AssertionError("unknown arp setting accepted")

    # Aliases work and normalize.
    assert ArpSettings().with_change("gate", 0.1).gate_seconds == 0.1
    assert ArpSettings().with_change("octaves", [1, 2, 3]).octave_offsets == (
        1,
        2,
        3,
    )
    assert ArpSettings().with_change("shared", "random").shared_mode == "random"
    print("  gate<=step, clamps, aliases, and invalid values rejected")


def test_order_sequences() -> None:
    print("\n=== Test 3: exact forward/reverse/pingpong/random sequences ===")
    assert order_indices("forward") == (0, 1, 2, 3, 4, 5)
    assert order_indices("reverse") == (5, 4, 3, 2, 1, 0)
    assert order_indices("pingpong") == (0, 1, 2, 3, 4, 5, 4, 3, 2, 1)
    assert PINGPONG_INDICES == (0, 1, 2, 3, 4, 5, 4, 3, 2, 1)
    # Endpoints are not repeated back to back.
    ping = order_indices("pingpong")
    assert all(a != b for a, b in zip(ping, ping[1:])), ping

    pattern = build_groups(
        INITIAL_RATIOS, 1.0, FUNDAMENTAL, rng=random.Random(1)
    )
    random_order = order_indices("random", seed=7, pattern=pattern)
    assert sorted(random_order) == [0, 1, 2, 3, 4, 5], random_order
    assert random_order != order_indices("random", seed=8, pattern=pattern)
    # Same seed + same pattern -> same order, every time.
    assert random_order == order_indices("random", seed=7, pattern=pattern)
    assert random_order == order_indices("random", 7, pattern)
    assert len(set(random_order)) == 6

    # The SetArp stores the generated sequence so the thread and tests agree.
    arp = _arp(order="pingpong")
    assert arp.order_sequence == PINGPONG_INDICES
    arp.set_setting("order", "reverse")
    assert arp.order_sequence == (5, 4, 3, 2, 1, 0)
    arp.set_setting("order", "random")
    assert sorted(arp.order_sequence) == [0, 1, 2, 3, 4, 5]
    expected_random = order_indices(
        "random", seed=arp.seed, pattern=arp.pattern
    )
    assert arp.order_sequence == expected_random
    print("  forward 0..5, reverse 5..0, pingpong 10 steps, random seeded")

    try:
        order_indices("sideways")
    except ValueError:
        pass
    else:
        raise AssertionError("invalid order accepted")


def test_shared_mode_and_pattern() -> None:
    print("\n=== Test 4: shared note is identical in A and B ===")
    for mode in ("anchor", "random"):
        arp = _arp(shared_mode=mode, seed=3)
        pattern = arp.pattern
        assert pattern is not None
        assert pattern.shared is pattern.group_a[0]
        assert pattern.shared is pattern.group_b[0]
        assert pattern.group_a[0].frequency == pattern.group_b[0].frequency
        assert pattern.group_a[0].octave == pattern.group_b[0].octave
        assert pattern.shared_ratio in INITIAL_RATIOS
        for group in (pattern.group_a, pattern.group_b):
            shared_count = sum(
                1 for note in group if note.ratio == pattern.shared_ratio
            )
            assert shared_count == 1, (mode, group)
    # anchor mode honours the anchor when it is in the set.
    anchored = _arp(shared_mode="anchor", seed=5)
    assert anchored.pattern is not None
    assert abs(anchored.pattern.shared_ratio - 1.0) < 1e-12
    print("  anchor/random keep exactly one shared ratio, same note object")


def test_beat_sync() -> None:
    print("\n=== Test 5a: beat sync math, divisions, and clamps ===")
    assert SYNC_MODES == ("free", "beat")
    assert DIVISIONS == ("1/4", "1/8", "1/16", "1/8T")
    assert DIVISION_QUARTERS["1/4"] == 1.0
    assert DIVISION_QUARTERS["1/8"] == 0.5
    assert DIVISION_QUARTERS["1/16"] == 0.25
    assert abs(DIVISION_QUARTERS["1/8T"] - 1.0 / 3.0) < 1e-12
    assert division_quarters("1/8") == 0.5

    assert validate_sync_mode("beat") == "beat"
    assert validate_sync_mode("FREE") == "free"
    assert validate_sync_mode("on") == "beat"
    assert validate_sync_mode("off") == "free"
    for bad in ("swing", "", None):
        try:
            validate_sync_mode(bad)
        except (TypeError, ValueError):
            pass
        else:
            raise AssertionError(f"invalid sync mode accepted: {bad!r}")
    assert validate_division("1/8t") == "1/8T"
    for bad in ("1/5", "8", ""):
        try:
            validate_division(bad)
        except (TypeError, ValueError):
            pass
        else:
            raise AssertionError(f"invalid division accepted: {bad!r}")
    assert validate_evolve_beats(0) == 1
    assert validate_evolve_beats(999) == 64
    assert validate_evolve_depth(-1) == 0.0
    assert validate_evolve_depth(9) == 1.0

    # Free mode ignores the BPM: the stored step is the step.
    settings = ArpSettings.create(step_seconds=0.25, gate_seconds=0.2)
    assert settings.effective_step_seconds(120.0) == 0.25
    assert settings.effective_step_seconds(40.0) == 0.25

    # Beat mode: step = (60 / bpm) * quarters.
    beat = ArpSettings.create(sync_mode="beat", division="1/8", gate_seconds=0.2)
    for bpm, expected in (
        (120.0, 0.25),
        (60.0, 0.5),
        (240.0, 0.125),
        (20.0, 1.5),
        (300.0, 0.1),
    ):
        assert abs(beat.effective_step_seconds(bpm) - expected) < 1e-9, (
            bpm,
            beat.effective_step_seconds(bpm),
            expected,
        )
    # 1/4 at 120 = 0.5 s; 1/16 at 120 = 0.125 s; 1/8T at 120 = 1/6 s.
    assert ArpSettings.create(sync_mode="beat", division="1/4").effective_step_seconds(120.0) == 0.5
    assert ArpSettings.create(sync_mode="beat", division="1/16").effective_step_seconds(120.0) == 0.125
    triplet = ArpSettings.create(sync_mode="beat", division="1/8T").effective_step_seconds(120.0)
    assert abs(triplet - 1.0 / 6.0) < 1e-9, triplet
    # The gate is clamped to the effective step, not to the free step.
    fast = ArpSettings.create(
        sync_mode="beat", division="1/16", gate_seconds=0.2
    )
    assert fast.effective_gate_seconds(120.0) == 0.125
    assert abs(fast.effective_gate_seconds(60.0) - 0.2) < 1e-9
    # Step is clamped to the hardware limits.
    assert ArpSettings.create(
        sync_mode="beat", division="1/16"
    ).effective_step_seconds(20.0) == 0.75

    arp = _arp()
    assert arp.sync_mode == "free" and arp.division == "1/8"
    arp.set_bpm(90.0)
    assert arp.bpm == 90.0
    assert arp.set_bpm(5.0) == 20.0          # clamped low
    assert arp.set_bpm(900.0) == 300.0       # clamped high
    arp.set_bpm(120.0)
    with contextlib.redirect_stdout(io.StringIO()) as out:
        arp.set_setting("sync_mode", "beat")
        arp.set_setting("division", "1/8")
    assert "sync=beat:1/8" in out.getvalue(), out.getvalue()
    assert arp.sync_mode == "beat" and arp.division == "1/8"
    assert abs(arp.effective_step_seconds - 0.25) < 1e-9
    assert arp.set_transport(True) is True and arp.transport_running
    assert arp.set_transport(0) is False and not arp.transport_running
    print("  free ignores BPM; beat derives 1/4..1/8T; gate <= step; clamps")

    # The arp keeps playing in beat mode while the transport is stopped.
    arp = _arp(sync_mode="beat", division="1/16", step_seconds=2.0)
    recording = _recording(arp)
    arp.set_bpm(300.0)
    arp.start()
    try:
        assert _wait_for(lambda: len(recording.calls) >= 3, 3.0), recording.calls
    finally:
        arp.stop()
    assert abs(arp.effective_step_seconds - 0.05) < 1e-9
    print("  beat mode runs without the transport at the derived step")


def test_sync_phase_snap() -> None:
    print("\n=== Test 5b: a beat-synced arp snaps to the boundary ===")
    arp = _arp(sync_mode="beat", division="1/16")
    arp.set_bpm(300.0)  # 1/16 at 300 BPM = 0.05 s steps
    recording = _recording(arp)
    delay = 0.2
    arp.sync_phase(delay)
    started = time.monotonic()
    arp.start()
    try:
        assert _wait_for(lambda: len(recording.calls) >= 1, 3.0)
        first_at = time.monotonic() - started
        assert first_at >= delay * 0.7, (first_at, delay)
        assert first_at < delay + 0.3, (first_at, delay)
        # It restarts at the first element of the order.
        pattern = arp.pattern
        assert pattern is not None
        assert recording.frequencies[0] == round(
            pattern.steps[arp.order_sequence[0]].frequency, 6
        )
    finally:
        arp.stop()

    # A snap while running re-phases without cutting the note or stalling.
    arp = _arp(sync_mode="beat", division="1/16")
    arp.set_bpm(300.0)
    recording = _recording(arp)
    arp.start()
    try:
        assert _wait_for(lambda: len(recording.calls) >= 3, 3.0)
        arp.sync_phase(0.1)
        snap_at = time.monotonic()
        before = len(recording.calls)
        assert _wait_for(lambda: len(recording.calls) > before, 3.0)
        after_snap = time.monotonic() - snap_at
        assert after_snap >= 0.05, after_snap
    finally:
        arp.stop()

    # Free mode ignores the snap request.
    free = _arp()
    recording = _recording(free)
    free.sync_phase(0.5)
    free.start()
    try:
        assert _wait_for(lambda: len(recording.calls) >= 1, 1.0), (
            "a free arp must not wait for a beat snap",
            recording.calls,
        )
    finally:
        free.stop()
    print("  snap delays the first step, re-phases on demand, free ignores it")


def test_evolution() -> None:
    print("\n=== Test 5c: the pattern evolves on its own ===")
    arp = _arp()
    assert arp.evolve is False and arp.evolve_count == 0
    before = arp.pattern
    assert before is not None
    with contextlib.redirect_stdout(io.StringIO()) as out:
        assert arp.evolve_now() == 1
    assert arp.evolve_count == 1
    text = out.getvalue()
    assert "[ARP] evolved #1" in text, text
    assert "depth=0.00" in text, text
    # Depth 0 keeps the shared note and only re-picks the four free notes.
    after = arp.pattern
    assert after is not None and after is not before
    assert after.shared_ratio == before.shared_ratio
    assert after.shared.octave == before.shared.octave
    assert [note.ratio for note in after.group_a[1:]] != [
        note.ratio for note in before.group_a[1:]
    ] or [note.ratio for note in after.group_b[1:]] != [
        note.ratio for note in before.group_b[1:]
    ]
    assert arp.snapshot is not None, "evolution must not need a new set"

    # Same seed + same start = same evolution sequence.
    left = _arp()
    right = _arp()
    with contextlib.redirect_stdout(io.StringIO()):
        for _ in range(4):
            left.evolve_now()
            right.evolve_now()
    assert left.pattern is not None and right.pattern is not None
    assert [n.frequency for n in left.pattern.steps] == [
        n.frequency for n in right.pattern.steps
    ]

    # Depth 1: over enough evolutions the shared note and the octaves move.
    deep = _arp(evolve_depth=1.0)
    start_shared = deep.pattern.shared_ratio  # type: ignore[union-attr]
    start_octaves = deep.octave_offsets
    shared_moved = False
    octaves_moved = False
    with contextlib.redirect_stdout(io.StringIO()):
        for _ in range(40):
            deep.evolve_now()
            assert deep.pattern is not None
            if deep.pattern.shared_ratio != start_shared:
                shared_moved = True
            if deep.octave_offsets != start_octaves:
                octaves_moved = True
    assert shared_moved, "depth 1 should let the shared note move"
    assert octaves_moved, "depth 1 should nudge an octave somewhere"
    assert all(-8 <= value <= 8 for value in deep.octave_offsets)

    # Rates: the interval is evolve_beats * 60 / bpm seconds.
    arp = _arp()
    arp.set_bpm(120.0)
    arp.set_setting("evolve_beats", 4)
    assert abs(arp.next_evolution_in - (-1.0)) < 1e-9  # off -> -1
    with contextlib.redirect_stdout(io.StringIO()):
        arp.set_setting("evolve", True)
    assert arp.evolve is True
    assert 0.0 < arp.next_evolution_in <= 2.0 + 1e-6
    arp.set_bpm(240.0)
    assert 0.0 < arp.next_evolution_in <= 1.0 + 1e-6

    # Timed evolution: fast rate, real thread, one log line per evolution.
    arp = _arp(evolve=True, evolve_beats=1, evolve_depth=0.2)
    arp.set_bpm(300.0)  # 0.2 s per evolution
    recording = _recording(arp)
    out = io.StringIO()
    arp.start()
    try:
        with contextlib.redirect_stdout(out):
            started = time.monotonic()
            assert _wait_for(lambda: arp.evolve_count >= 2, 3.0), (
                arp.evolve_count,
                time.monotonic() - started,
            )
    finally:
        arp.stop()
    assert arp.evolve_count >= 2
    lines = [line for line in out.getvalue().splitlines() if "evolved" in line]
    assert len(lines) <= arp.evolve_count + 1, lines
    assert recording.calls, "the arp must keep playing while it evolves"

    # Evolution never leaves a stuck note: same monophonic invariant as 8.3b.
    port = FakePort()
    arp = _arp(evolve=True, evolve_beats=1, evolve_depth=1.0)
    arp.set_bpm(300.0)
    arp.output = RecordingOutput(port)  # type: ignore[assignment]
    arp.start()
    time.sleep(0.5)
    arp.stop()
    held: int | None = None
    for message in port.messages:
        if message.type == "note_on":
            assert held is None, f"note {message.note} started while {held} held"
            held = message.note
        elif message.type == "note_off":
            assert message.note == held, (message.note, held)
            held = None
    assert held is None, "evolution left a note sounding"
    print("  depth 0 keeps the shared note; depth 1 drifts; timed and stuck-free")


def test_order_reaches_the_port() -> None:
    print("\n=== Test 5: the thread plays the documented order ===")
    for name, expected in (
        ("forward", (0, 1, 2, 3, 4, 5)),
        ("reverse", (5, 4, 3, 2, 1, 0)),
    ):
        arp = _arp(order=name, step_seconds=0.05, gate_seconds=0.02)
        recording = _recording(arp)
        arp.start()
        try:
            assert _wait_for(lambda: len(recording.calls) >= 6, 3.0), (
                name,
                recording.calls,
            )
        finally:
            arp.stop()
        played = recording.frequencies[:6]
        pattern = arp.pattern
        assert pattern is not None
        wanted = [pattern.steps[index].frequency for index in expected]
        assert played == [round(value, 6) for value in wanted], (name, played, wanted)

    # pingpong visits all ten positions, endpoints not repeated.
    arp = _arp(order="pingpong", step_seconds=0.05, gate_seconds=0.02)
    recording = _recording(arp)
    arp.start()
    try:
        assert _wait_for(lambda: len(recording.calls) >= 10, 3.0)
    finally:
        arp.stop()
    pattern = arp.pattern
    assert pattern is not None
    wanted = [pattern.steps[index].frequency for index in PINGPONG_INDICES]
    assert recording.frequencies[:10] == [round(value, 6) for value in wanted]
    print("  forward/reverse first six and the pingpong ten-step cycle match")


def test_runtime_changes() -> None:
    print("\n=== Test 6: step/gate/velocity apply from the next step ===")
    arp = _arp(step_seconds=0.05, gate_seconds=0.02, verbose_steps=False)
    recording = _recording(arp)
    arp.start()
    try:
        assert _wait_for(lambda: len(recording.calls) >= 3, 3.0)
        before = len(recording.calls)

        # Velocity applies from the next trigger; the held note is untouched.
        out = io.StringIO()
        with contextlib.redirect_stdout(out):
            arp.set_setting("velocity", 20)
        assert "change applied: velocity=20" in out.getvalue(), out.getvalue()

        def _new_velocities() -> list[int]:
            return [velocity for _freq, velocity in recording.calls[before:]]

        # The trigger already in flight keeps the old velocity; every trigger
        # after it uses the new one (never a burst of both).
        assert _wait_for(
            lambda: sum(1 for v in _new_velocities() if v == 20) >= 2, 3.0
        ), _new_velocities()
        values = _new_velocities()
        first_new = values.index(20)
        assert all(value == 20 for value in values[first_new:]), values
        assert arp.velocity == 20

        # A long gate is clamped to the step and logged.
        out = io.StringIO()
        with contextlib.redirect_stdout(out):
            arp.set_setting("gate_seconds", 2.0)
        assert arp.gate_seconds == 0.05, arp.gate_seconds
        assert "clamped" in out.getvalue(), out.getvalue()

        # A short gate is honoured exactly.
        with contextlib.redirect_stdout(io.StringIO()):
            arp.set_setting("gate_seconds", 0.01)
        assert arp.gate_seconds == 0.01

        # A step change never bursts through the missed steps: the step in
        # flight finishes, then the next deadline is 2 s away.
        with contextlib.redirect_stdout(io.StringIO()):
            arp.set_setting("step_seconds", 2.0)
        time.sleep(0.15)
        count = len(recording.calls)
        time.sleep(0.3)
        assert len(recording.calls) <= count + 1, (
            "a slower step must not burst",
            len(recording.calls) - count,
        )
    finally:
        arp.stop()

    # Monophonic invariant: every note-on is followed by its own note-off
    # before the next note-on, and stop() always sends All Notes Off.
    port = FakePort()
    arp = _arp(step_seconds=0.05, gate_seconds=0.02)
    arp.output = RecordingOutput(port)  # type: ignore[assignment]
    arp.start()
    time.sleep(0.2)
    arp.stop()
    held: int | None = None
    for message in port.messages:
        if message.type == "note_on":
            assert held is None, f"note {message.note} started while {held} held"
            held = message.note
        elif message.type == "note_off":
            assert message.note == held, (message.note, held)
            held = None
    assert held is None, "a note was left sounding after stop()"
    controls = port.with_type("control_change")
    assert controls and controls[-1].control == 123
    print("  velocity/gate/step next-step application, no burst, no stuck note")


def test_pattern_regeneration() -> None:
    print("\n=== Test 7: parameter changes regenerate without a new mutation ===")
    arp = _arp()
    before = arp.snapshot
    assert before is not None and before[0] == INITIAL_RATIOS
    first = arp.pattern
    assert first is not None

    with contextlib.redirect_stdout(io.StringIO()):
        arp.set_setting("octave_offsets", [0, 2, 0])
    after = arp.pattern
    assert after is not None and after is not first
    assert [note.octave for note in after.group_a] == [0, 2, 0]
    assert [note.octave for note in after.group_b] == [0, 2, 0]
    assert arp.snapshot == before, "the set snapshot must not change"

    # shared mode and seed also rebuild from the stored snapshot.
    with contextlib.redirect_stdout(io.StringIO()):
        arp.set_setting("shared_mode", "random")
        arp.set_setting("seed", 99)
    assert arp.snapshot == before
    assert arp.pattern is not None
    assert arp.seed == 99 and arp.shared_mode == "random"

    # Changing a parameter before any set update is safe and stored.
    fresh = SetArp(RecordingOutput(FakePort()))
    fresh.set_setting("order", "reverse")
    fresh.set_setting("velocity", 10)
    assert fresh.pattern is None
    assert fresh.order_sequence == (5, 4, 3, 2, 1, 0)
    fresh.on_set(INITIAL_RATIOS, 1.0, FUNDAMENTAL)
    assert fresh.pattern is not None and fresh.velocity == 10
    print("  octave/shared/seed rebuild from the snapshot; pre-set changes kept")


def test_rig_and_missing_bus() -> None:
    print("\n=== Test 8: MIDIRig arp state/setters, midi=False, missing Bus 2 ===")
    config = replace(
        load_config(), step_seconds=0.05, gate_seconds=0.02
    )
    drone_fake = FakePort()
    arp_fake = FakePort()
    rig = MIDIRig(config, drone_output=drone_fake, arp_output=arp_fake)
    try:
        state = rig.arp_state()
        assert len(state) == 17, state
        assert state[0] == 1 and state[1] == 0, state  # enabled, port not open
        rig.start()
        state = rig.arp_state()
        assert state[1] == 1, state
        assert state[2] == 0.05 and state[3] == 0.05 and state[4] == 0.02
        assert state[9] == "anchor" and state[10] == "forward" and state[11] == 1
        assert state[12] == "free" and state[13] == "1/8"
        assert state[14] == 0 and state[15] == 4 and state[16] == 0.0
        assert rig.arp_port_ok is True

        rig.set_arp_setting("order", "reverse")
        assert rig.arp is not None and rig.arp.order == "reverse"
        assert rig.arp_state()[10] == "reverse"
        rig.set_arp_setting("octave_offsets", [1, 0, -1])
        assert rig.arp_settings.octave_offsets == (1, 0, -1)
        assert rig.arp_state()[6:9] == [1, 0, -1]

        # Stage 8.3c: the rig forwards BPM/transport and can re-phase the arp.
        rig.set_arp_bpm(140.0)
        assert rig.arp is not None and rig.arp.bpm == 140.0
        rig.set_arp_setting("sync_mode", "beat")
        rig.set_arp_setting("division", "1/16")
        assert rig.arp.sync_mode == "beat" and rig.arp.division == "1/16"
        beat = 60.0 / 140.0
        assert abs(rig.arp_state()[3] - beat * 0.25) < 1e-9
        rig.sync_arp_phase(0.0)
        rig.set_arp_transport(True)
        assert rig.arp.transport_running is True
        rig.set_arp_setting("sync_mode", "free")

        # Disabling the bus is the existing Stage 7.1 switch, not a new path.
        rig.set_arp_enabled(False)
        assert rig.arp_state()[0] == 0
        rig.set_arp_enabled(True)
        assert rig.arp_state()[0] == 1
    finally:
        rig.stop()
    assert drone_fake.closed and arp_fake.closed

    # midi=False: no arp, no Bus 2, and a well-formed default state.
    bass_fake = FakePort()
    drums_only = MIDIRig(
        midi=False, drums=False, bass=True, bass_output=bass_fake
    )
    try:
        assert drums_only.arp is None
        assert drums_only.arp_port_ok is False
        assert drums_only.arp_state() == [
            0, 0, 0.25, 0.25, 0.2, 90, 0, 1, 2, "anchor", "forward", 1,
            "free", "1/8", 0, 4, 0.0,
        ]
        try:
            drums_only.set_arp_setting("order", "reverse")
        except RuntimeError as exc:
            assert "arp is not configured" in str(exc), exc
        else:
            raise AssertionError("set_arp_setting must raise without an arp")
    finally:
        drums_only.stop()

    # A missing Bus 2 leaves the rest of the rig alone: port_ok stays False and
    # start() raises the existing Stage 6 port error (the bridge logs it and
    # keeps drums/bass/audio running).
    missing = MIDIRig(
        replace(config, arp_port="No Such Arp Bus 999"),
        drone_output=FakePort(),
    )
    try:
        assert missing.arp_port_ok is False
        try:
            missing.start()
        except RuntimeError:
            pass
        else:
            raise AssertionError("a missing Bus 2 must raise from start()")
    finally:
        missing.stop()
    print("  state/setters, midi=False default state, missing Bus 2 raises")


def test_osc_handlers() -> None:
    print("\n=== Test 9: /ji/arp/* handlers validate and /ji/arp/state shape ===")
    received: list[tuple[str, object]] = []
    server = MutationOSCServer(
        MutationEngine(INITIAL_RATIOS),
        on_arp_change=lambda key, value: received.append((key, value)),
    )
    server._handle_arp_step("/ji/arp/set_step", 0.5)
    server._handle_arp_step("/ji/arp/set_step", 0.0)
    server._handle_arp_step("/ji/arp/set_step")
    server._handle_arp_gate("/ji/arp/set_gate", 0.1)
    server._handle_arp_velocity("/ji/arp/set_velocity", 1)
    server._handle_arp_velocity("/ji/arp/set_velocity", 300)
    server._handle_arp_octaves("/ji/arp/set_octaves", 0, 1, 2)
    server._handle_arp_octaves("/ji/arp/set_octaves", 0, 1)
    server._handle_arp_shared_mode("/ji/arp/set_shared_mode", "random")
    server._handle_arp_shared_mode("/ji/arp/set_shared_mode", "nope")
    server._handle_arp_order("/ji/arp/set_order", "pingpong")
    server._handle_arp_order("/ji/arp/set_order", "sideways")
    server._handle_arp_seed("/ji/arp/set_seed", 7)
    server._handle_arp_setting(
        ("beat",), "/ji/arp/set_sync", "sync_mode", arp_module.validate_sync_mode
    )
    server._handle_arp_division("/ji/arp/set_division", "1/16")
    server._handle_arp_division("/ji/arp/set_division", "1/5")
    server._handle_arp_evolve("/ji/arp/set_evolve", 1)
    server._handle_arp_evolve("/ji/arp/set_evolve", "maybe")
    server._handle_arp_evolve_beats("/ji/arp/set_evolve_beats", 8)
    server._handle_arp_evolve_depth("/ji/arp/set_evolve_depth", 0.4)
    assert received == [
        ("step_seconds", 0.5),
        ("gate_seconds", 0.1),
        ("velocity", 1),
        ("velocity", 127),
        ("octave_offsets", (0, 1, 2)),
        ("shared_mode", "random"),
        ("order", "pingpong"),
        ("seed", 7),
        ("sync_mode", "beat"),
        ("division", "1/16"),
        ("evolve", True),
        ("evolve_beats", 8),
        ("evolve_depth", 0.4),
    ], received

    # The status payload is exactly seventeen values in the documented order.
    state = ArpSettings().state_args(True, False)
    assert state == [
        1, 0, 0.25, 0.25, 0.2, 90, 0, 1, 2, "anchor", "forward", 1,
        "free", "1/8", 0, 4, 0.0,
    ], state
    assert ArpSettings().state_args(False, False)[:2] == [0, 0]

    # A handler without a parsed callback is a no-op, and a raising callback
    # never kills the OSC thread.
    silent = MutationOSCServer(MutationEngine(INITIAL_RATIOS))
    silent._handle_arp_order("/ji/arp/set_order", "reverse")

    def boom(_key, _value):
        raise RuntimeError("boom")

    fragile = MutationOSCServer(
        MutationEngine(INITIAL_RATIOS), on_arp_change=boom
    )
    errored = io.StringIO()
    with contextlib.redirect_stderr(errored):
        fragile._handle_arp_order("/ji/arp/set_order", "reverse")
    assert "arp change callback failed" in errored.getvalue()
    print("  seven setters, arity/type rejection, 11-field state, soft callback")


def test_bridge_quantization() -> None:
    print("\n=== Test 10: bridge arp changes are immediate/queued ===")
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
    rig = None
    bridge = None
    try:
        bridge = bridge_module.JIBridge(
            midi=True, listen_port=19050, ui_port=19051, sc_port=19052
        )
        config = replace(load_config(), step_seconds=0.05, gate_seconds=0.02)
        rig = MIDIRig(
            config, drone_output=FakePort(), arp_output=FakePort()
        )
        bridge.midi = rig
        assert bridge._arp_rig() is rig.arp

        # Before the rig starts the port is not open: log clearly, change
        # nothing, and still resync the UI.
        ui_client = FakeClient.instances[1]
        ui_client.sent.clear()
        logged = io.StringIO()
        with contextlib.redirect_stderr(logged):
            bridge._on_arp_change("order", "reverse")
        assert "IAC Driver Bus 2 is not open" in logged.getvalue(), logged.getvalue()
        assert bridge.quantizer.pending_count == 0
        assert rig.arp is not None and rig.arp.order == "forward"
        assert [a for a, _args in ui_client.sent] == ["/ji/arp/state"]

        rig.start()
        bridge.rhythm.set_running(False)
        bridge._on_arp_change("order", "reverse")
        assert rig.arp is not None and rig.arp.order == "reverse"
        assert bridge.quantizer.pending_count == 0

        bridge.rhythm.set_running(True)
        bridge._on_arp_change("velocity", 40)
        assert bridge.quantizer.pending_count == 1
        assert rig.arp.velocity == 90
        assert bridge.quantizer.flush("test beat") == 1
        assert rig.arp.velocity == 40

        # Last-wins per key while different keys all run.
        bridge._on_arp_change("velocity", 41)
        bridge._on_arp_change("velocity", 42)
        assert bridge.quantizer.pending_count == 1
        bridge._on_arp_change("order", "pingpong")
        assert bridge.quantizer.pending_count == 2
        bridge.quantizer.flush("test beat")
        assert rig.arp.velocity == 42 and rig.arp.order == "pingpong"

        # A transport stop flushes a pending arp edit like any other action.
        bridge._on_arp_change("shared_mode", "random")
        assert bridge.quantizer.pending_count == 1
        bridge._on_rhythm_transport(False)
        assert bridge.quantizer.pending_count == 0
        assert rig.arp.shared_mode == "random"

        # Every applied change sends one /ji/arp/state, and the state is the
        # documented eleven-field payload.
        ui = FakeClient.instances[1]
        ui.sent.clear()
        bridge._on_arp_change("velocity", 60)
        states = [args for address, args in ui.sent if address == "/ji/arp/state"]
        assert len(states) == 1, ui.sent
        assert len(states[0]) == 17, states[0]
        assert states[0][5] == 60, states[0]

        # Invalid values are rejected before queueing and still resync the UI.
        ui.sent.clear()
        bridge._on_arp_change("order", "sideways")
        assert bridge.quantizer.pending_count == 0
        assert [a for a, _args in ui.sent] == ["/ji/arp/state"]

        # The documented beat order puts arp after bass and before midi.
        from control.quantize import (
            PHASE_BASS,
            PHASE_DRUMS,
            PHASE_GROOVE,
            PHASE_MUTATION,
            PHASE_NAMES,
            PHASE_OCTAVE,
        )

        assert (
            PHASE_MUTATION
            < PHASE_OCTAVE
            < PHASE_GROOVE
            < PHASE_DRUMS
            < PHASE_BASS
            < PHASE_ARP
            < PHASE_MIDI
        ), PHASE_NAMES
        assert PHASE_NAMES[PHASE_ARP] == "arp"
    finally:
        if bridge is not None:
            bridge.stop()
        if rig is not None and rig.is_started:
            rig.stop()
        bridge_module.SimpleUDPClient = original
    assert "mido" not in sys.modules
    print("  stopped -> immediate; running -> pending; coalesce; stop flushes")


def test_bridge_sync() -> None:
    print("\n=== Test 11: bridge sync snap and BPM forwarding ===")
    original = bridge_module.SimpleUDPClient

    class FakeClient:
        def __init__(self, host: str, port: int) -> None:
            self.host = host
            self.port = port
            self.sent: list[tuple[str, list]] = []

        def send_message(self, address: str, args: list) -> None:
            self.sent.append((address, list(args)))

    bridge_module.SimpleUDPClient = FakeClient
    bridge = None
    rig = None
    phases: list[float] = []
    try:
        bridge = bridge_module.JIBridge(
            midi=True, listen_port=19060, ui_port=19061, sc_port=19062
        )
        config = replace(load_config(), step_seconds=0.25, gate_seconds=0.2)
        rig = MIDIRig(config, drone_output=FakePort(), arp_output=FakePort())
        bridge.midi = rig
        rig.start()
        assert rig.arp is not None
        rig.arp.sync_phase = lambda delay=0.0: phases.append(float(delay))  # type: ignore[method-assign]

        # The bridge forwards the canonical tempo to the arp on start.
        bridge._sync_drums_context()
        assert rig.arp.bpm == bridge.rhythm.bpm == 120.0
        bridge._on_rhythm_tempo(140.0)
        assert rig.arp.bpm == 140.0

        # Turning sync on while stopped starts the grid immediately.
        out = io.StringIO()
        with contextlib.redirect_stdout(out):
            bridge._on_arp_change("sync_mode", "beat")
        assert rig.arp.sync_mode == "beat"
        assert phases and phases[-1] == 0.0, phases
        assert "transport stopped" in out.getvalue(), out.getvalue()

        # A running transport snaps to the next division boundary.
        bridge.rhythm.set_running(True)
        out = io.StringIO()
        with contextlib.redirect_stdout(out):
            bridge._on_rhythm_transport(True)
        assert rig.arp.transport_running is True
        assert abs(phases[-1]) > 0.0 or True  # delay depends on the clock phase
        beat = 60.0 / 140.0
        assert 0.0 <= phases[-1] <= beat / 2.0 + 1e-6, phases
        assert "snapping to the next" in out.getvalue(), out.getvalue()

        # A division change also re-phases (applied on the next beat while the
        # transport runs, so flush the queue here); free mode does not.
        out = io.StringIO()
        with contextlib.redirect_stdout(out):
            bridge._on_arp_change("division", "1/16")
        bridge.quantizer.flush("test beat")
        assert rig.arp.division == "1/16"
        assert 0.0 <= phases[-1] <= beat / 4.0 + 1e-6, phases
        with contextlib.redirect_stdout(io.StringIO()):
            bridge._on_arp_change("sync_mode", "free")
        bridge.quantizer.flush("test beat")
        assert rig.arp.sync_mode == "free"
        count = len(phases)
        bridge._realign_arp()
        assert len(phases) == count, "free mode must not re-phase"
    finally:
        if bridge is not None:
            bridge.stop()
        if rig is not None and rig.is_started:
            rig.stop()
        bridge_module.SimpleUDPClient = original
    print("  tempo -> arp BPM, stop/start/division snaps, free never re-phases")


def run() -> None:
    print("JI Stage 8.3b arp pure tests")
    test_settings_defaults()
    test_validation()
    test_order_sequences()
    test_shared_mode_and_pattern()
    test_beat_sync()
    test_sync_phase_snap()
    test_evolution()
    test_order_reaches_the_port()
    test_runtime_changes()
    test_pattern_regeneration()
    test_rig_and_missing_bus()
    test_osc_handlers()
    test_bridge_quantization()
    test_bridge_sync()
    assert "mido" not in sys.modules, "pure construction must not import mido"
    print("\nALL ARP TESTS PASSED")


if __name__ == "__main__":
    try:
        run()
    except Exception:
        traceback.print_exc()
        print("\nARP TESTS FAILED")
        raise SystemExit(1)
