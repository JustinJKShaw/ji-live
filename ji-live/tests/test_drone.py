#!/usr/bin/env python3
"""Stage 8.3d pure drone tests: hold is unchanged, pulse, bend, transport gating.

Run from ``ji-live/``::

    uv run --with python-osc python tests/test_drone.py

No audio, mido, PyQt6, or sockets.  Runtime checks use an injected fake MIDI
output and short real-time windows.
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

from control.drone import (
    DEFAULT_DRONE_SEED,
    DEFAULT_HOLD_BEATS,
    DEFAULT_MODE,
    DEFAULT_PULSE_GATE,
    DEFAULT_PULSE_GRID,
    DEFAULT_PULSE_NOTES,
    DEFAULT_PULSE_SPAN,
    DEFAULT_SLIDE_DIRECTION,
    DEFAULT_SLIDE_SECONDS,
    DEFAULT_SEQ_BARS,
    DEFAULT_SEQ_GATE,
    DEFAULT_SEQ_NOTES,
    DEFAULT_SLIDE_SEMITONES,
    DRONE_MODES,
    PULSE_GRIDS,
    SEQ_BARS,
    SLIDE_DIRECTIONS,
    AnchorDrone,
    DroneConfig,
    DroneRig,
    DroneSettings,
    cycle_beats,
    load_config,
    select_target,
    validate_hold_beats,
    validate_mode,
    validate_pulse_gate,
    validate_pulse_grid,
    validate_pulse_notes,
    validate_pulse_span,
    sequence_offsets,
    validate_seed,
    validate_seq_gate,
    validate_seq_bars,
    seq_bars_value,
    validate_seq_notes,
    validate_slide_direction,
    validate_slide_seconds,
    validate_slide_semitones,
    validate_velocity,
)
from control.midi_rig import MIDIRig, load_config as load_mpe_config
from control.mutation import MutationEngine
from control.mpe_output import MPEOutput
from control.osc_server import MutationOSCServer

INITIAL_RATIOS = [1.0, 9.0 / 8.0, 5.0 / 4.0, 4.0 / 3.0, 3.0 / 2.0, 5.0 / 3.0, 15.0 / 8.0]
FUNDAMENTAL = 220.0
CONFIG_PATH = ROOT / "config" / "drone_config.json"


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

    def stream(self) -> list[tuple]:
        return [
            (
                message.type,
                message.channel,
                message.note,
                message.velocity,
                message.pitch,
                message.control,
                message.value,
            )
            for message in self.messages
        ]


def _wait_for(predicate, timeout: float = 3.0) -> bool:
    deadline = time.monotonic() + timeout
    while time.monotonic() < deadline:
        if predicate():
            return True
        time.sleep(0.005)
    return False


def _rig(mode: str = "hold", *, bpm: float = 120.0, **settings) -> tuple[DroneRig, FakePort]:
    fake = FakePort()
    output = MPEOutput("IAC Driver Bus 1", output=fake, label="drone")
    data = {"mode": mode, "velocity": 90}
    data.update(settings)
    config = DroneConfig.from_mapping(data)
    rig = DroneRig(output, config, bpm=bpm)
    rig.on_set(INITIAL_RATIOS, 1.0, FUNDAMENTAL)
    return rig, fake


# ---------------------------------------------------------------------------
# 1. Config / settings
# ---------------------------------------------------------------------------


def test_defaults_and_config() -> None:
    print("\n=== Test 1: drone defaults and config/drone_config.json ===")
    settings = DroneSettings()
    assert settings.mode == DEFAULT_MODE == "hold", "hold ships as the default"
    assert settings.velocity == 90
    assert settings.pulse_notes == DEFAULT_PULSE_NOTES == 4
    assert settings.pulse_grid == DEFAULT_PULSE_GRID == "1/4"
    assert settings.pulse_gate == DEFAULT_PULSE_GATE == 0.5
    assert settings.pulse_octave_span == DEFAULT_PULSE_SPAN == 1
    assert settings.slide_direction == DEFAULT_SLIDE_DIRECTION == "down"
    assert settings.slide_semitones == DEFAULT_SLIDE_SEMITONES == 2
    assert settings.slide_seconds == DEFAULT_SLIDE_SECONDS == 0.4
    assert settings.hold_beats == DEFAULT_HOLD_BEATS == 2.5
    assert settings.seed == DEFAULT_DRONE_SEED == 1
    assert settings.seq_notes == DEFAULT_SEQ_NOTES == 5
    assert settings.seq_bars == DEFAULT_SEQ_BARS == "1"
    assert settings.seq_gate == DEFAULT_SEQ_GATE == 1.0
    assert settings.describe_mode(120.0) == "mode=hold"
    assert DroneSettings.create(mode="pulse").describe_mode(120.0).startswith(
        "mode=pulse notes=4"
    )
    assert DroneSettings.create(mode="bend").describe_mode(120.0).startswith(
        "mode=bend down 2.00 st"
    )
    assert DroneSettings.create(
        mode="sequence"
    ).describe_mode(120.0) == "mode=sequence notes=5 bars=1 gate=1.00"

    config = load_config(CONFIG_PATH)
    assert config.port == "IAC Driver Bus 1"
    assert config.settings == settings, config.settings

    # A rig without a config file still gets the Stage 6 hold drone.
    default = DroneConfig()
    assert default.settings.mode == "hold"
    print("  shipped defaults, JSON round trip, hold fallback")


def test_settings_validation() -> None:
    print("\n=== Test 2: drone settings validation and clamps ===")
    assert [validate_mode(value) for value in DRONE_MODES] == list(DRONE_MODES)
    try:
        validate_mode("wobble")
    except ValueError:
        pass
    else:
        raise AssertionError("unknown mode accepted")

    assert validate_velocity(0) == 1
    assert validate_velocity(999) == 127

    for bad in (1, 9):
        try:
            validate_pulse_notes(bad)
        except ValueError:
            pass
        else:
            raise AssertionError(f"pulse_notes accepted {bad!r}")
    assert validate_pulse_notes(2) == 2 and validate_pulse_notes(8) == 8

    assert [validate_pulse_grid(value) for value in PULSE_GRIDS] == list(
        PULSE_GRIDS
    )
    try:
        validate_pulse_grid("1/8T")
    except ValueError:
        pass
    else:
        raise AssertionError("pulse grid accepted 1/8T")

    assert validate_pulse_gate(0.0) == 0.05
    assert validate_pulse_gate(9.0) == 2.0

    for bad in (-1, 3):
        try:
            validate_pulse_span(bad)
        except ValueError:
            pass
        else:
            raise AssertionError(f"span accepted {bad!r}")

    assert [validate_slide_direction(value) for value in SLIDE_DIRECTIONS] == [
        "down",
        "up",
    ]
    for bad in (0, 13):
        try:
            validate_slide_semitones(bad)
        except ValueError:
            pass
        else:
            raise AssertionError(f"slide_semitones accepted {bad!r}")
    assert validate_slide_seconds(0.0) == 0.05
    assert validate_slide_seconds(9.0) == 4.0
    assert validate_hold_beats(0.0) == 0.25
    assert validate_hold_beats(99.0) == 16.0
    assert validate_seed(-1) == 0
    assert validate_seed(2**40) == 2**31 - 1

    for bad in (1, 9):
        try:
            validate_seq_notes(bad)
        except ValueError:
            pass
        else:
            raise AssertionError(f"seq_notes accepted {bad!r}")
    assert validate_seq_notes(2) == 2 and validate_seq_notes(8) == 8
    assert [validate_seq_bars(value) for value in SEQ_BARS] == list(SEQ_BARS)
    assert validate_seq_bars("2 bars") == "2", "labels are forgiving"
    assert validate_seq_bars(4.0) == "4", "numeric bars are accepted"
    assert seq_bars_value("1") == 1.0 and seq_bars_value("1/4") == 0.25
    for bad in ("1/8", "3", "x"):
        try:
            validate_seq_bars(bad)
        except ValueError:
            pass
        else:
            raise AssertionError(f"seq_bars accepted {bad!r}")
    assert validate_seq_gate(0.0) == 0.1
    assert validate_seq_gate(9.0) == 1.0

    settings = DroneSettings()
    assert settings.with_change("velocity", 40).velocity == 40
    assert settings.with_change("seq_notes", 7).seq_notes == 7
    assert settings.with_change("seq_bars", "2").seq_bars == "2"
    assert settings.with_change("seq_gate", 0.5).seq_gate == 0.5
    assert settings.with_change("pulse_span", 0).pulse_octave_span == 0
    assert settings.with_change("dir", "up").slide_direction == "up"
    assert settings.with_change("semis", 12).slide_semitones == 12
    assert settings.with_change("slide", 1.0).slide_seconds == 1.0
    assert settings.with_change("hold", 4.0).hold_beats == 4.0
    try:
        settings.with_change("cycle_beats", 4.0)
    except ValueError:
        pass
    else:
        raise AssertionError("cycle_beats is derived, not a setter")
    print("  mode/velocity/notes/grid/gate/span/direction/semis/seconds/hold")


def test_cycle_is_derived() -> None:
    print("\n=== Test 3: the bend cycle is derived, not configured ===")
    assert cycle_beats(2.5, 0.4, 120.0) == 4.0, "2.5 + 0.8 -> 4 beats"
    assert cycle_beats(2.5, 0.4, 60.0) == 3.0, "2.5 + 0.4 -> 3 beats"
    assert cycle_beats(2.5, 0.4, 240.0) == 5.0, "2.5 + 1.6 -> 5 beats"
    assert cycle_beats(4.0, 0.5, 120.0) == 5.0, "exact 5.0 stays 5"
    assert cycle_beats(1.0, 0.1, 120.0) == 2.0, "1.2 -> 2"
    settings = DroneSettings.create(hold_beats=2.5, slide_seconds=0.4)
    assert settings.cycle_beats(120.0) == 4.0
    assert "cycle_beats" not in DroneSettings.__dataclass_fields__, (
        "cycle_beats is derived, not a field"
    )
    print("  2.5 + 0.4 s at 120 BPM = 4.000 beats")


# ---------------------------------------------------------------------------
# 2. hold is byte-for-byte the Stage 6 drone
# ---------------------------------------------------------------------------


def _compare_streams(left: list[tuple], right: list[tuple], label: str) -> None:
    assert left == right, f"{label}: {left!r} != {right!r}"


def test_hold_is_unchanged() -> None:
    print("\n=== Test 4: hold sends exactly the Stage 6 message stream ===")
    snapshots = [
        (INITIAL_RATIOS, 1.0, FUNDAMENTAL),
        (INITIAL_RATIOS, 1.0, FUNDAMENTAL),  # unchanged -> nothing
        (INITIAL_RATIOS, 1.0, FUNDAMENTAL * 1.002),  # retune (same note)
        (INITIAL_RATIOS, 3.0 / 2.0, FUNDAMENTAL),  # retrigger (new note)
        ([1.0, 5.0 / 4.0], None, FUNDAMENTAL),  # fallback to ratios[0]
    ]
    anchor_fake = FakePort()
    anchor = AnchorDrone(
        MPEOutput("bus 1", output=anchor_fake, label="drone"), velocity=90
    )
    rig_fake = FakePort()
    rig = DroneRig(
        MPEOutput("bus 1", output=rig_fake, label="drone"),
        DroneConfig(),
        velocity=90,
    )
    for ratios, anchor_ratio, fundamental in snapshots:
        anchor.on_set(ratios, anchor_ratio, fundamental)
        rig.on_set(ratios, anchor_ratio, fundamental)
    _compare_streams(anchor_fake.stream(), rig_fake.stream(), "hold stream")

    # The same note, the same velocity, the same bend sequence.
    anchor.stop()
    rig.stop()
    _compare_streams(anchor_fake.stream(), rig_fake.stream(), "hold stop")

    # hold is never transport-gated and never needs a thread.
    fake2 = FakePort()
    rig2 = DroneRig(
        MPEOutput("bus 1", output=fake2, label="drone"), DroneConfig()
    )
    assert rig2.is_playing is False
    rig2.set_transport(False)
    rig2.on_set([1.0, 3.0 / 2.0], 3.0 / 2.0, FUNDAMENTAL)
    assert fake2.types() == ["pitchwheel", "note_on"], fake2.types()
    rig2.set_transport(True)
    assert rig2.is_playing is False, "hold must not start a scheduler"
    print("  unchanged / retune / retrigger / fallback / stop all identical")


# ---------------------------------------------------------------------------
# 3. pulse
# ---------------------------------------------------------------------------


def test_pulse() -> None:
    print("\n=== Test 5: pulse repeats N notes on the anchor ===")
    rig, fake = _rig(
        "pulse",
        bpm=240.0,
        pulse_notes=4,
        pulse_grid="1/8",
        pulse_gate=0.25,
        pulse_octave_span=1,
        velocity=80,
    )
    assert rig.mode == "pulse"
    rig.set_transport(True)
    try:
        assert _wait_for(
            lambda: len(fake.with_type("note_on")) >= 8, timeout=3.0
        )
    finally:
        rig.set_transport(False)

    onsets = fake.with_type("note_on")
    ons = [message for message, _at in onsets]
    times = [at for _message, at in onsets]

    # N notes per cycle: the pitch sequence repeats every pulse_notes.
    notes = [message.note for message in ons]
    pattern = notes[:4]
    for index, note in enumerate(notes):
        assert note == pattern[index % 4], (notes, pattern)

    # Every note is the anchor pitch class at an allowed octave offset.
    anchor_hz = FUNDAMENTAL
    for message in ons:
        assert message.velocity == 80
        frequency = _midi_note_hz(message.note)  # within a bend of the anchor
        offset = math.log2(frequency / anchor_hz)
        assert -1.6 <= offset <= 1.6, (message.note, offset)

    # Grid timing: 1/8 at 240 BPM = 0.125 s steps.
    deltas = [b - a for a, b in zip(times, times[1:])]
    for delta in deltas[:4]:
        assert 0.06 <= delta <= 0.26, deltas

    # Gate: 0.25 beats at 240 BPM = 0.0625 s.
    offs = fake.with_type("note_off")
    assert offs, "pulse must release each note"
    off_message, off_at = offs[0]
    on_message, on_at = onsets[0]
    assert off_message.note == on_message.note
    assert 0.0 <= off_at - on_at <= 0.25, (off_at - on_at)

    # Monophonic: every note-off precedes the next note-on.
    events = [
        (message.type, message.note)
        for message in fake.messages
        if message.type in ("note_on", "note_off")
    ]
    held = None
    for kind, note in events:
        if kind == "note_on":
            assert held is None, (kind, note, held)
            held = note
        else:
            assert note == held
            held = None

    # Stop sends note-off + CC 123.
    rig.set_transport(False)
    assert fake.messages[-1].type == "control_change"
    assert fake.messages[-1].control == 123
    print(f"  {len(ons)} notes, period 4, 0.125 s grid, gate, CC 123 on stop")


def _midi_note_hz(note: int) -> float:
    return 440.0 * (2.0 ** ((note - 69) / 12.0))


def test_pulse_determinism() -> None:
    print("\n=== Test 6: the pulse octave pattern is seeded ===")
    left, _ = _rig("pulse", pulse_notes=8, pulse_octave_span=2, seed=3)
    right, _ = _rig("pulse", pulse_notes=8, pulse_octave_span=2, seed=3)
    other, _ = _rig("pulse", pulse_notes=8, pulse_octave_span=2, seed=4)
    pattern = [left.pulse_octave(step) for step in range(16)]
    assert pattern == [right.pulse_octave(step) for step in range(16)]
    assert pattern[:8] == pattern[8:16], "the pattern repeats every N"
    assert any(-2 <= value <= 2 for value in pattern)
    assert pattern != [other.pulse_octave(step) for step in range(16)]

    # span 0 is always the anchor octave.
    flat, _ = _rig("pulse", pulse_notes=4, pulse_octave_span=0)
    assert [flat.pulse_octave(step) for step in range(8)] == [0] * 8
    print("  same seed reproduces the pattern; span 0 stays on the anchor")


def test_pulse_switches() -> None:
    print("\n=== Test 7: pulse starts/stops with the transport only ===")
    rig, fake = _rig(
        "pulse", bpm=240.0, pulse_notes=4, pulse_grid="1/8"
    )
    rig.set_transport(False)
    time.sleep(0.3)
    assert fake.messages == [], "pulse must be silent while stopped"
    rig.set_transport(True)
    try:
        assert _wait_for(lambda: fake.with_type("note_on"), timeout=2.0)
    finally:
        rig.set_transport(False)
    count = len(fake.messages)
    time.sleep(0.2)
    assert len(fake.messages) == count, "no note may arrive after the stop"
    print("  silent when stopped, notes only while running, clean stop")


# ---------------------------------------------------------------------------
# 4. bend
# ---------------------------------------------------------------------------


def test_bend_down() -> None:
    print("\n=== Test 8: bend holds then slides down with one bend ===")
    rig, fake = _rig(
        "bend",
        bpm=120.0,
        hold_beats=0.25,
        slide_seconds=0.1,
        slide_semitones=12,
        slide_direction="down",
        velocity=70,
    )
    # 0.25 beat hold + 0.2 beat slide = 0.45 -> derived 1 beat (0.5 s) cycle.
    assert rig.cycle_beats == 1.0
    rig.set_transport(True)
    try:
        assert _wait_for(
            lambda: len(fake.with_type("note_on")) >= 2, timeout=3.0
        )
    finally:
        rig.set_transport(False)

    onsets = fake.with_type("note_on")
    offs = fake.with_type("note_off")
    assert len(onsets) >= 2, "the cycle repeats"
    assert onsets[0][0].velocity == 70
    assert onsets[0][0].note == onsets[1][0].note, "same base note each cycle"
    assert len(offs) >= 1, "the gesture ends with a note-off"

    # Bends between the first note-on and its note-off slide downward,
    # monotonically, and stay inside the 14-bit MPE range.
    first_on_at = onsets[0][1]
    first_off_at = offs[0][1]
    bends = [
        (message.pitch, at) for message, at in fake.with_type("pitchwheel")
    ]
    base_bend = [pitch for pitch, at in bends if at <= first_on_at][-1]
    assert base_bend == 0, base_bend
    slide = [
        pitch for pitch, at in bends if first_on_at < at <= first_off_at
    ]
    assert len(slide) >= 5, f"expected a continuous slide, got {len(slide)}"
    for previous, current in zip(slide, slide[1:]):
        assert current <= previous, (slide,)
    for value in slide:
        assert -8192 <= value <= 8191
    assert slide[-1] < base_bend, slide
    for value in slide:
        assert value <= base_bend, (value, base_bend)
    # 12 st of a 48 st range is a quarter of 8192.
    assert abs((base_bend - slide[-1]) - 2048) <= 40, (
        base_bend - slide[-1]
    )

    # The next cycle returns to the base pitch before the new note-on.
    second_on_at = onsets[1][1]
    before = [
        message.pitch
        for message, at in fake.with_type("pitchwheel")
        if first_off_at <= at <= second_on_at
    ]
    assert before, "a bend must precede the next note-on"
    assert abs(before[-1] - base_bend) <= 2, (before[-1], base_bend)
    print(
        f"  {len(slide)} bend updates over 0.1 s, monotonic, returns to base"
    )


def test_bend_up() -> None:
    print("\n=== Test 9: bend slides up when the direction is up ===")
    rig, fake = _rig(
        "bend",
        bpm=120.0,
        hold_beats=0.25,
        slide_seconds=0.1,
        slide_semitones=5,
        slide_direction="up",
    )
    rig.set_transport(True)
    try:
        assert _wait_for(lambda: fake.with_type("note_off"), timeout=3.0)
    finally:
        rig.set_transport(False)
    onsets = fake.with_type("note_on")
    offs = fake.with_type("note_off")
    bends = [
        (message.pitch, at) for message, at in fake.with_type("pitchwheel")
    ]
    base_bend = [pitch for pitch, at in bends if at <= onsets[0][1]][-1]
    slide = [
        pitch for pitch, at in bends if onsets[0][1] < at <= offs[0][1]
    ]
    assert len(slide) >= 5, slide
    for previous, current in zip(slide, slide[1:]):
        assert current >= previous, slide
    assert slide[-1] > base_bend, slide
    # 5 st of a 48 st range is 5/48 of 8192.
    assert abs((slide[-1] - base_bend) - 853) <= 40, slide[-1] - base_bend
    print(f"  {len(slide)} monotonic upward updates")


def test_bend_retune_during_hold() -> None:
    print("\n=== Test 10: a mutation during the hold retunes, no retrigger ===")
    rig, fake = _rig(
        "bend",
        bpm=120.0,
        hold_beats=2.0,
        slide_seconds=0.1,
        slide_semitones=2,
    )
    rig.set_transport(True)
    try:
        assert _wait_for(lambda: fake.with_type("note_on"), timeout=2.0)
        time.sleep(0.06)
        count = len(fake.with_type("note_on"))
        # Same MIDI note, slightly different frequency: bend-only retune.
        rig.on_set(INITIAL_RATIOS, 1.0, FUNDAMENTAL * 1.002)
        time.sleep(0.08)
        assert len(fake.with_type("note_on")) == count, "no retrigger on retune"
        bends = [m.pitch for m, _at in fake.with_type("pitchwheel")]
        assert bends and bends[-1] != 0, "the retune must be audible as a bend"
    finally:
        rig.set_transport(False)
    print("  bend-only retune during hold_beats")


def test_bend_mutation_during_slide_is_deferred() -> None:
    print("\n=== Test 11: a mutation during the slide waits for the next cycle ===")
    rig, fake = _rig(
        "bend",
        bpm=120.0,
        hold_beats=0.25,
        slide_seconds=0.6,
        slide_semitones=2,
    )
    rig.set_transport(True)
    try:
        assert _wait_for(lambda: fake.with_type("note_on"), timeout=2.0)
        time.sleep(0.2)  # inside the slide (hold is 0.125 s at 120 BPM)
        before = list(fake.messages)
        deliveries = len(fake.with_type("note_on"))
        # A different anchor class would need a new MIDI note; it must wait.
        rig.on_set(INITIAL_RATIOS, 3.0 / 2.0, FUNDAMENTAL)
        time.sleep(0.15)
        assert len(fake.with_type("note_on")) == deliveries, (
            "the slide must not be interrupted by a retrigger"
        )
        assert len(fake.messages) > len(before), "the slide keeps bending"
        assert _wait_for(
            lambda: len(fake.with_type("note_on")) > deliveries, timeout=3.0
        ), "the next cycle must pick up the new base"
        assert fake.with_type("note_on")[-1][0].note != fake.with_type(
            "note_on"
        )[0][0].note
    finally:
        rig.set_transport(False)
    print("  deferred to the next cycle, then applied")


def test_bend_transport_gating() -> None:
    print("\n=== Test 12: bend is transport-gated; hold is not ===")
    rig, fake = _rig("bend", bpm=120.0, hold_beats=0.25, slide_seconds=0.1)
    time.sleep(0.3)
    assert fake.messages == [], "bend must be silent while stopped"
    assert rig.is_playing is False
    rig.set_transport(True)
    try:
        assert _wait_for(lambda: fake.with_type("note_on"), timeout=2.0)
        assert rig.is_playing is True
    finally:
        rig.set_transport(False)
    assert rig.is_playing is False
    assert fake.messages[-1].type == "control_change"
    assert fake.messages[-1].control == 123

    hold_fake = FakePort()
    hold_rig = DroneRig(
        MPEOutput("bus 1", output=hold_fake, label="drone"), DroneConfig()
    )
    hold_rig.set_transport(False)
    hold_rig.on_set(INITIAL_RATIOS, 3.0 / 2.0, FUNDAMENTAL)
    assert hold_fake.types() == ["pitchwheel", "note_on"], hold_fake.types()
    print("  pulse/bend gated, hold always audible (Stage 6/7.1 kept)")


# ---------------------------------------------------------------------------
# 5. sequence (harmonic movement through the set)
# ---------------------------------------------------------------------------


def test_sequence_offsets() -> None:
    print("\n=== Test 13: the sequence contour leaves and returns to the anchor ===")
    assert sequence_offsets(2) == (0, 1)
    assert sequence_offsets(3) == (0, 1, 0)
    assert sequence_offsets(4) == (0, 1, 2, 1)
    assert sequence_offsets(5) == (0, 1, 2, 1, 0)
    assert sequence_offsets(6) == (0, 1, 2, 3, 2, 1)
    assert sequence_offsets(7) == (0, 1, 2, 3, 2, 1, 0)
    assert sequence_offsets(8) == (0, 1, 2, 3, 4, 3, 2, 1)
    for count in range(2, 9):
        pattern = sequence_offsets(count)
        assert len(pattern) == count
        assert pattern[0] == 0, "the figure starts on the anchor"
        if count % 2 == 1:
            assert pattern[-1] == 0, "an odd figure ends on the anchor"
        # It goes up first, then comes back down: one turning point.
        peak = max(pattern)
        assert pattern.index(peak) == peak
        assert pattern[peak:] == tuple(
            range(peak, peak - (len(pattern) - peak), -1)
        )[: len(pattern) - peak]
    print("  2..8 notes: up from the anchor and back (odd lengths end home)")


def test_sequence_frequencies() -> None:
    print("\n=== Test 14: sequence notes climb the current set, octave-wrapping ===")
    rig, _fake = _rig("sequence", seq_notes=5, seq_bars="1")
    freqs = [round(rig.sequence_frequency(offset), 3) for offset in (0, 1, 2, 1, 0)]
    assert freqs[0] == 220.0 and freqs[-1] == 220.0
    assert freqs[1] > freqs[0] and freqs[2] > freqs[1]
    assert freqs[3] == freqs[1]
    assert len(set(freqs)) >= 3, "different classes, not just the anchor"

    # A small set wraps into the next octave instead of stopping.
    rig.on_set([1.0, 5.0 / 4.0, 3.0 / 2.0], 1.0, FUNDAMENTAL)
    climbed = [round(rig.sequence_frequency(offset), 3) for offset in range(5)]
    assert climbed == [220.0, 275.0, 330.0, 440.0, 550.0], climbed

    # The figure follows a mutation (same shape, new classes).
    before = rig.sequence_frequency(1)
    rig.on_set([1.0, 9.0 / 8.0, 4.0 / 3.0, 3.0 / 2.0], 1.0, FUNDAMENTAL)
    assert rig.sequence_frequency(1) != before
    assert rig.sequence_frequency(0) == 220.0, "the anchor still starts it"
    assert rig.sequence_frequency(0) is not None
    print("  anchor / up / down, 3-class octave wrap, mutation-following")


def test_sequence_note_length_is_in_bars() -> None:
    print("\n=== Test 15a: a sequence note lasts a bar (or several) ===")
    rig, _fake = _rig("sequence", bpm=120.0, seq_bars="1")
    # 1 bar at 120 BPM in 4/4 = 2 s.
    assert abs(rig.sequence_note_seconds() - 2.0) < 1e-9
    rig.set_meter(3, 4)
    assert rig.meter == "3/4"
    assert abs(rig.sequence_note_seconds() - 1.5) < 1e-9
    rig.set_meter(4, 4)
    rig.set_setting("seq_bars", "4")
    assert abs(rig.sequence_note_seconds() - 8.0) < 1e-9
    rig.set_setting("seq_bars", "1/4")
    assert abs(rig.sequence_note_seconds() - 0.5) < 1e-9
    # A tempo change retimes the held notes without touching the shape.
    rig.set_bpm(240.0)
    assert abs(rig.sequence_note_seconds() - 0.25) < 1e-9
    rig.set_setting("seq_bars", "1")
    assert abs(rig.sequence_note_seconds() - 1.0) < 1e-9

    # The bridge forwards the meter to the drone rig.
    drone_fake = FakePort()
    midi = MIDIRig(
        replace(load_mpe_config(), step_seconds=0.05, gate_seconds=0.02),
        drone_output=drone_fake,
        arp_output=FakePort(),
    )
    try:
        midi.set_drone_meter(6, 8)
        assert midi.drone_rig.meter == "6/8"
        assert abs(midi.drone_rig.bar_quarters - 3.0) < 1e-9
    finally:
        midi.stop()
    print("  4/4 1 bar = 2 s at 120 BPM; 3/4, 4 bars, tempo and meter follow")


def test_sequence_plays() -> None:
    print("\n=== Test 15: sequence plays N notes per cycle, one bar each ===")
    rig, fake = _rig(
        "sequence",
        bpm=240.0,
        seq_notes=5,
        seq_bars="1/4",
        seq_gate=1.0,
        velocity=80,
    )
    rig.set_transport(False)
    time.sleep(0.2)
    assert fake.messages == [], "sequence must be silent while stopped"
    rig.set_transport(True)
    try:
        assert _wait_for(
            lambda: len(fake.with_type("note_on")) >= 10, timeout=3.0
        )
    finally:
        rig.set_transport(False)

    onsets = fake.with_type("note_on")
    notes = [message.note for message, _at in onsets]
    times = [at for _message, at in onsets]
    pattern = notes[:5]
    assert len(set(pattern)) >= 3, pattern
    for index, note in enumerate(notes):
        assert note == pattern[index % 5], (notes, pattern)

    # 1/4 bar at 240 BPM, 4/4 = one beat = 0.25 s per note.
    deltas = [b - a for a, b in zip(times, times[1:])]
    for delta in deltas[:4]:
        assert 0.18 <= delta <= 0.36, deltas

    # Gate 100%: the note-off lands just before the next note-on.
    offs = fake.with_type("note_off")
    assert offs, "sequence must release each note"
    off_message, off_at = offs[0]
    on_message, on_at = onsets[0]
    assert off_message.note == on_message.note
    # The scheduled gap is min(10 ms, 2% of the note) = 5 ms at this tempo, so
    # the ideal length is ~0.245 s.  A 0.30 s bound tolerates scheduler jitter
    # on slow/CI machines; the monophonic check below still rejects an off that
    # arrives after the next note-on.
    assert 0.15 <= off_at - on_at <= 0.30, (off_at - on_at)

    # Monophonic, and stop sends CC 123.
    events = [
        (message.type, message.note)
        for message in fake.messages
        if message.type in ("note_on", "note_off")
    ]
    held = None
    for kind, note in events:
        if kind == "note_on":
            assert held is None, (kind, note, held)
            held = note
        else:
            assert note == held
            held = None
    assert held is None
    assert fake.messages[-1].type == "control_change"
    assert fake.messages[-1].control == 123
    print(f"  {len(notes)} notes, period 5, 0.25 s per note, gate, clean stop")


def test_gated_modes_explain_themselves() -> None:
    print("\n=== Test 16: a gated mode says why it is silent ===")
    fake = FakePort()
    rig = DroneRig(
        MPEOutput("bus 1", output=fake, label="drone"),
        DroneConfig(),
        bpm=120.0,
    )
    rig.on_set(INITIAL_RATIOS, 1.0, FUNDAMENTAL)
    rig.set_transport(False)
    logged = io.StringIO()
    with contextlib.redirect_stdout(logged):
        rig.set_setting("mode", "sequence")
    text = logged.getvalue()
    assert "starts with the transport (press Play)" in text, text
    assert "[DRONE] sequence started" not in text, text
    assert rig.is_playing is False
    # The transport start is what arms it, and it now says so.
    logged = io.StringIO()
    with contextlib.redirect_stdout(logged):
        rig.set_transport(True)
    assert "[DRONE] sequence started" in logged.getvalue()
    assert rig.is_playing is True
    rig.set_transport(False)
    assert rig.is_playing is False
    print("  armed while stopped, started on Play, stopped on Stop")


def test_sequence_mode_switch() -> None:
    print("\n=== Test 17: hold -> sequence -> hold keeps the bus clean ===")
    fake = FakePort()
    rig = DroneRig(
        MPEOutput("bus 1", output=fake, label="drone"),
        DroneConfig.from_mapping({"mode": "hold"}),
        bpm=240.0,
    )
    rig.on_set(INITIAL_RATIOS, 1.0, FUNDAMENTAL)
    assert fake.types() == ["pitchwheel", "note_on"]
    rig.set_transport(True)
    logged = io.StringIO()
    with contextlib.redirect_stdout(logged):
        rig.set_setting("mode", "sequence")
    assert rig.mode == "sequence"
    assert "[DRONE] mode=sequence notes=5 bars=1 gate=1.00" in (
        logged.getvalue()
    ), logged.getvalue()
    assert "[DRONE] sequence started" in logged.getvalue(), logged.getvalue()
    assert _wait_for(lambda: len(fake.with_type("note_on")) >= 3, timeout=2.0)
    rig.set_transport(False)
    rig.set_setting("mode", "hold")
    rig.set_transport(False)
    # Every note-on is paired before the next one (no stuck note, no overlap).
    events = [
        (message.type, message.note)
        for message in fake.messages
        if message.type in ("note_on", "note_off")
    ]
    held = None
    for kind, note in events:
        if kind == "note_on":
            assert held is None, "a mode switch left a note hanging"
            held = note
        else:
            assert note == held
            held = None
    print("  switching modes note-offs the old gesture first")


# ---------------------------------------------------------------------------
# 6. Rig / bridge integration
# ---------------------------------------------------------------------------


def test_rig_state_and_modes() -> None:
    print("\n=== Test 18: MIDIRig drone state and mode switching ===")
    drone_fake = FakePort()
    rig = MIDIRig(
        replace(load_mpe_config(), step_seconds=0.05, gate_seconds=0.02),
        drone_output=drone_fake,
        arp_output=FakePort(),
    )
    try:
        state = rig.drone_state()
        assert len(state) == 17, state
        assert state[0] == 0, "the rig is not started yet"
        assert state[1] == "hold", state
        assert state[2] == 90 and state[3] == 4 and state[4] == "1/4"
        assert state[7] == "down" and state[8] == 2
        assert state[10] == 2.5 and state[11] == 4.0
        assert state[12] == 1 and state[13] == 0
        assert state[14] == 5 and state[15] == "1" and state[16] == 1.0
        assert rig.drone is not None and rig.drone_rig is not None

        rig.start()
        assert rig.drone_state()[0] == 1, "started + Bus 1 open"
        rig.on_set(INITIAL_RATIOS, 1.0, FUNDAMENTAL)
        rig.set_drone_setting("mode", "pulse")
        assert rig.drone_rig.mode == "pulse"
        assert rig.drone_state()[1] == "pulse"
        rig.set_drone_setting("velocity", 40)
        assert rig.drone_state()[2] == 40
        rig.set_drone_setting("pulse_grid", "1/16")
        assert rig.drone_state()[4] == "1/16"
        rig.set_drone_setting("slide_direction", "up")
        assert rig.drone_state()[7] == "up"
        rig.set_drone_transport(True)
        assert _wait_for(
            lambda: drone_fake.with_type("note_on"), timeout=2.0
        ), "pulse must play through MIDIRig"
        rig.set_drone_transport(False)
        rig.set_drone_setting("mode", "hold")
        assert rig.drone_state()[1] == "hold"
        assert rig.drone_rig.is_playing is False

        # The top-row switch remains the only mute.
        rig.set_drone_enabled(False)
        assert rig.drone_enabled is False
        rig.set_drone_enabled(True)
        assert rig.drone_enabled is True
    finally:
        rig.stop()

    # midi=False: a well-formed default state, no drone, no error.
    plain = MIDIRig(midi=False, drums=False, bass=False)
    try:
        state = plain.drone_state()
        assert len(state) == 17 and state[0] == 0 and state[1] == "hold"
        assert plain.drone is None and plain.drone_rig is None
        try:
            plain.set_drone_setting("mode", "pulse")
        except RuntimeError as exc:
            assert "drone is not configured" in str(exc), exc
        else:
            raise AssertionError("set_drone_setting must raise without a drone")
    finally:
        plain.stop()

    # A bad drone config falls back to the hold drone instead of failing.
    logged = io.StringIO()
    fallback = None
    try:
        with contextlib.redirect_stderr(logged):
            fallback = MIDIRig(
                replace(load_mpe_config(), step_seconds=0.05, gate_seconds=0.02),
                drone_output=FakePort(),
                arp_output=FakePort(),
                drone_config={"mode": "wobble"},
            )
            fallback.start()
        assert fallback.drone_rig is not None
        assert fallback.drone_rig.mode == "hold"
        assert fallback.drone_state()[1] == "hold"
    finally:
        if fallback is not None:
            fallback.stop()
    assert "using mode=hold" in logged.getvalue()
    print("  state shape, mode switches, midi=False, bad config -> hold")


def test_select_target() -> None:
    print("\n=== Test 19: anchor selection matches the hold rule ===")
    assert select_target(INITIAL_RATIOS, 1.0, FUNDAMENTAL) == (
        1.0,
        220.0,
    )
    ratio, frequency = select_target(INITIAL_RATIOS, 3.0 / 2.0, FUNDAMENTAL)
    assert ratio == 3.0 / 2.0 and math.isclose(frequency, 330.0)
    ratio, _frequency = select_target(INITIAL_RATIOS, 7.0 / 4.0, FUNDAMENTAL)
    assert ratio == 1.0, "a missing anchor falls back to the lowest class"
    assert select_target(INITIAL_RATIOS, None, FUNDAMENTAL)[0] == 1.0
    assert select_target([], None, FUNDAMENTAL) is None
    print("  anchor / missing anchor / empty set")


def test_no_mido_import() -> None:
    print("\n=== Test 20: pure construction imports no mido ===")
    assert "mido" not in sys.modules
    DroneRig(MPEOutput("bus", output=FakePort(), label="drone"), DroneConfig())
    DroneSettings.create(mode="bend")
    cycle_beats(2.5, 0.4, 120.0)
    assert "mido" not in sys.modules, "pure construction must not import mido"
    print("  DroneRig/DroneSettings stay mido-free")


def test_osc_handlers() -> None:
    print("\n=== Test 21: /ji/drone/set_* handlers and the state shape ===")
    received: list[tuple[str, object]] = []
    server = MutationOSCServer(
        MutationEngine(INITIAL_RATIOS),
        on_drone_change=lambda key, value: received.append((key, value)),
    )
    server._handle_drone_mode("/ji/drone/set_mode", "bend")
    server._handle_drone_mode("/ji/drone/set_mode", "hold")
    server._handle_drone_mode("/ji/drone/set_mode", "wobble")
    server._handle_drone_pulse_span("/ji/drone/set_pulse_span", 2)
    server._handle_drone_slide_direction("/ji/drone/set_slide_direction", "up")
    server._handle_drone_slide_semitones(
        "/ji/drone/set_slide_semitones", 12
    )
    server._handle_drone_slide_seconds("/ji/drone/set_slide_seconds", 2.0)
    server._handle_drone_hold_beats("/ji/drone/set_hold_beats", 8.0)
    server._handle_drone_seed("/ji/drone/set_seed", 5)
    server._handle_drone_seq_notes("/ji/drone/set_seq_notes", 6)
    server._handle_drone_seq_notes("/ji/drone/set_seq_notes", 99)
    server._handle_drone_seq_bars("/ji/drone/set_seq_bars", "2")
    server._handle_drone_seq_gate("/ji/drone/set_seq_gate", 0.25)
    assert received == [
        ("mode", "bend"),
        ("mode", "hold"),
        ("pulse_octave_span", 2),
        ("slide_direction", "up"),
        ("slide_semitones", 12),
        ("slide_seconds", 2.0),
        ("hold_beats", 8.0),
        ("seed", 5),
        ("seq_notes", 6),
        ("seq_bars", "2"),
        ("seq_gate", 0.25),
    ], received

    state = DroneSettings().state_args(True, False, 120.0)
    assert state == [
        1, "hold", 90, 4, "1/4", 0.5, 1, "down", 2, 0.4, 2.5, 4.0, 1, 0,
        5, "1", 1.0,
    ], state
    assert DroneSettings().state_args(False)[:2] == [0, "hold"]
    assert DroneSettings.create(mode="pulse").state_args(True, True)[13] == 1
    print("  fourteen setters, arity/type rejection, 17-value state")


def test_bridge_end_to_end() -> None:
    print("\n=== Test 22: bridge -> rig -> MIDI for every drone mode ===")
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
    fake = FakePort()
    logged = io.StringIO()
    try:
        with contextlib.redirect_stdout(logged), contextlib.redirect_stderr(
            logged
        ):
            bridge = bridge_module.JIBridge(
                midi=False, listen_port=19120, ui_port=19121, sc_port=19122
            )
            rig = MIDIRig(
                replace(
                    load_mpe_config(), step_seconds=0.05, gate_seconds=0.02
                ),
                drone_output=fake,
                arp_output=FakePort(),
            )
            bridge.midi = rig
            rig.start()
            rig.on_set(INITIAL_RATIOS, 1.0, FUNDAMENTAL)
            bridge.rhythm.set_running(False)

            for mode in ("pulse", "bend", "sequence"):
                fake.messages.clear()
                bridge._on_drone_change("mode", mode)  # GUI mode combo
                bridge._on_rhythm_transport(True)  # GUI Play
                assert _wait_for(
                    lambda: len(fake.with_type("note_on")) >= 2, timeout=3.0
                ), (mode, fake.types(), logged.getvalue()[-900:])
                assert rig.drone_rig.mode == mode
                bridge._on_rhythm_transport(False)
                assert fake.with_type("note_on"), mode

            # hold still plays with the transport stopped (Stage 6/7.1).
            bridge._on_drone_change("mode", "hold")
            fake.messages.clear()
            rig.on_set(INITIAL_RATIOS, 3.0 / 2.0, FUNDAMENTAL)
            assert fake.with_type("note_on"), fake.types()
    finally:
        bridge_module.SimpleUDPClient = original
        if rig is not None:
            rig.stop()
        if bridge is not None:
            bridge.quantizer.stop()
    print("  pulse/bend/sequence start on Play through the bridge; hold does not wait")


def run() -> None:
    print("JI Stage 8.3d drone tests")
    test_defaults_and_config()
    test_settings_validation()
    test_cycle_is_derived()
    test_hold_is_unchanged()
    test_pulse()
    test_pulse_determinism()
    test_pulse_switches()
    test_bend_down()
    test_bend_up()
    test_bend_retune_during_hold()
    test_bend_mutation_during_slide_is_deferred()
    test_bend_transport_gating()
    test_sequence_offsets()
    test_sequence_frequencies()
    test_sequence_note_length_is_in_bars()
    test_sequence_plays()
    test_gated_modes_explain_themselves()
    test_sequence_mode_switch()
    test_rig_state_and_modes()
    test_select_target()
    test_no_mido_import()
    test_osc_handlers()
    test_bridge_end_to_end()
    print("\nALL DRONE TESTS PASSED")


if __name__ == "__main__":
    try:
        run()
    except Exception:
        traceback.print_exc()
        print("\nDRONE TESTS FAILED")
        raise SystemExit(1)
