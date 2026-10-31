#!/usr/bin/env python3
"""Stage 8.1 rhythm + drum engine pure tests.

Run from ``ji-live/``::

    uv run --with python-osc python tests/test_rhythm.py

No audio, MIDI hardware, mido, or PyQt6 is required.  The drum scheduler is
exercised with a fake MIDI output; the real IAC Bus 3 path is human-run.
"""

from __future__ import annotations

import math
import sys
import time
import traceback
from pathlib import Path

# Allow both ``python tests/test_rhythm.py`` and ``python -m tests.test_rhythm``.
ROOT = Path(__file__).resolve().parent.parent
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

import control.rhythm as rhythm
from control.drums import (
    DEFAULT_CHANNEL,
    DEFAULT_LANE_MASK,
    DEFAULT_LANES,
    DEFAULT_PORT,
    DrumConfig,
    DrumEngine,
    DrumOutput,
    DrumRig,
    build_default_pattern,
    load_config,
)
from control.midi_rig import MIDIRig

CONFIG_PATH = ROOT / "config" / "rhythm_config.json"


class FakePort:
    """Minimal stand-in for a mido output; records DrumMessage values."""

    def __init__(self, *, fail_open: bool = False) -> None:
        self.messages: list = []
        self.closed = False
        self.fail_open = fail_open

    def send(self, message) -> None:
        if self.closed:
            raise RuntimeError("send after close")
        self.messages.append(message)

    def close(self) -> None:
        self.closed = True

    def types(self) -> list[str]:
        return [message.type for message in self.messages]

    def with_type(self, message_type: str) -> list:
        return [
            message for message in self.messages if message.type == message_type
        ]


# ---------------------------------------------------------------------------
# Tempo / meter / transport
# ---------------------------------------------------------------------------


def test_tempo_meter_transport() -> None:
    print("\n=== Test 1: tempo, meter, transport clamping/validation ===")
    assert rhythm.clamp_bpm(5) == 20.0
    assert rhythm.clamp_bpm(1000) == 300.0
    assert rhythm.clamp_bpm(120.5) == 120.5
    for bad in (float("nan"), float("inf")):
        try:
            rhythm.clamp_bpm(bad)
        except ValueError:
            pass
        else:
            raise AssertionError(f"{bad} bpm accepted")
    try:
        rhythm.clamp_bpm("nope")
    except TypeError:
        pass
    else:
        raise AssertionError("string bpm accepted")

    assert rhythm.clamp_meter_numerator(0) == 1
    assert rhythm.clamp_meter_numerator(99) == 16
    assert rhythm.clamp_meter_numerator(4.6) == 5

    for denominator in rhythm.METER_DENOMINATORS:
        assert rhythm.validate_meter_denominator(denominator) == denominator
    for bad in (0, 3, 5, 32):
        try:
            rhythm.validate_meter_denominator(bad)
        except ValueError:
            pass
        else:
            raise AssertionError(f"denominator {bad} accepted")

    engine = rhythm.RhythmEngine()
    state = engine.state
    assert state.bpm == 120.0 and state.meter == "4/4" and not state.running
    assert engine.set_bpm(140).bpm == 140.0
    assert engine.set_meter(3, 4).meter == "3/4"
    assert engine.set_running(True).running is True
    assert engine.clock.running is True
    assert engine.set_running(False).running is False
    assert engine.clock.running is False
    assert engine.set_bpm(1000).bpm == 300.0
    assert engine.state.as_tempo_args() == [300.0, 3, 4, 0]
    assert engine.state.as_state_args(True, 2.0) == [300.0, 3, 4, 0, 1, 2.0]
    print("  bpm/meter clamps; unsupported denominators rejected; transport clock")


def test_tempo_meter_math() -> None:
    print("\n=== Test 2: bar/beat/step math for 4/4, 3/4, 6/8, 5/4 ===")
    assert math.isclose(rhythm.beat_seconds(120), 0.5)
    assert math.isclose(rhythm.beat_seconds(60), 1.0)

    expected = {
        (4, 4): (4.0, 16, 2.0),
        (3, 4): (3.0, 12, 1.5),
        (6, 8): (3.0, 12, 1.5),
        (5, 4): (5.0, 20, 2.5),
        (7, 8): (3.5, 14, 1.75),
        (1, 16): (0.25, 1, 0.125),
    }
    for (num, den), (quarters, steps, seconds) in expected.items():
        assert math.isclose(rhythm.bar_quarters(num, den), quarters), (num, den)
        assert rhythm.steps_per_bar(num, den) == steps, (num, den)
        assert math.isclose(rhythm.bar_seconds(120, num, den), seconds), (num, den)

    assert rhythm.quarter_indices_in_bar(4, 4) == (0, 1, 2, 3)
    assert rhythm.quarter_indices_in_bar(6, 8) == (0, 1, 2)
    assert rhythm.quarter_indices_in_bar(7, 8) == (0, 1, 2, 3)

    assert math.isclose(rhythm.step_seconds(120), 0.125)
    assert math.isclose(rhythm.step_seconds(120, 2.0), 0.0625)
    assert math.isclose(rhythm.step_seconds(120, 0.5), 0.25)
    try:
        rhythm.step_seconds(120, 1.5)
    except ValueError:
        pass
    else:
        raise AssertionError("unsupported drum rate accepted")

    assert math.isclose(rhythm.beats_to_seconds(4, 120), 2.0)
    assert math.isclose(rhythm.seconds_to_beats(2.0, 120), 4.0)
    print("  4/4=16 steps, 3/4=12, 6/8=12, 5/4=20, 7/8=14; rate scales step only")


def test_loop_quantization_and_validation() -> None:
    print("\n=== Test 3: beat-mode take length, loop validation, quantization ===")
    assert math.isclose(rhythm.loop_take_seconds(4, 120), 2.0)
    assert math.isclose(rhythm.loop_take_seconds(3, 90), 2.0)
    assert math.isclose(rhythm.loop_take_seconds(7, 140), 3.0)
    assert math.isclose(rhythm.loop_take_seconds(0, 120), 0.5)  # clamped to 1 beat
    assert math.isclose(rhythm.loop_take_seconds(999, 120), 32.0)  # 64 beats

    for index in (1, 2, 3, 4):
        assert rhythm.validate_loop_index(index) == index
    for bad in (0, 5, -1):
        try:
            rhythm.validate_loop_index(bad)
        except ValueError:
            pass
        else:
            raise AssertionError(f"loop index {bad} accepted")

    assert rhythm.validate_loop_mode("Free") == "free"
    assert rhythm.validate_loop_mode("beat") == "beat"
    for bad in ("loop", "", "B"):
        try:
            rhythm.validate_loop_mode(bad)
        except ValueError:
            pass
        else:
            raise AssertionError(f"loop mode {bad!r} accepted")

    assert rhythm.clamp_loop_beats(0) == 1.0
    assert rhythm.clamp_loop_beats(1000) == 64.0
    assert rhythm.clamp_loop_seconds(0) == 1.0
    assert rhythm.clamp_loop_seconds(1000) == 30.0

    assert rhythm.quantize_beats(0.1) == 1.0
    assert rhythm.quantize_beats(1.0) == 1.0
    assert rhythm.quantize_beats(1.01) == 2.0
    assert math.isclose(rhythm.next_beat_time(10.0, 0.0, 0.5), 10.5)
    assert math.isclose(rhythm.next_beat_time(10.1, 0.0, 0.5), 10.5)
    assert math.isclose(rhythm.next_beat_time(10.6, 0.0, 0.5), 11.0)
    assert math.isclose(rhythm.next_beat_time(-1.0, 0.0, 0.5), 0.0)
    print("  beats*60/bpm, 1..64 beats, 1..30 s, next-beat quantization")


def test_beat_clock_fake_time() -> None:
    print("\n=== Test 4: BeatClock keeps phase across a tempo change ===")
    fake = {"now": 100.0}
    clock = rhythm.BeatClock(120, now=lambda: fake["now"])
    assert clock.running is False
    clock.start()
    assert clock.beat_at() == 0.0
    fake["now"] = 101.0
    assert math.isclose(clock.beat_at(), 2.0)
    clock.set_bpm(60, at=101.0)
    assert math.isclose(clock.beat_at(101.0), 2.0)
    fake["now"] = 102.0
    assert math.isclose(clock.beat_at(), 3.0)
    target = clock.next_beat_time(1.0, at=102.25)
    assert math.isclose(target, 103.0), target
    clock.stop(at=102.0)
    assert clock.running is False
    assert math.isclose(clock.beat_at(999.0), 3.0)  # frozen
    clock.start(at=200.0)
    assert clock.beat_at(200.0) == 0.0  # transport start re-anchors
    print("  start/stop/set_bpm/next_beat_time are continuous and testable")


# ---------------------------------------------------------------------------
# Drum map / pattern / lanes
# ---------------------------------------------------------------------------


def test_config_and_note_map() -> None:
    print("\n=== Test 5: drum config and 36-51 note map ===")
    config = load_config(CONFIG_PATH)
    assert config.port == DEFAULT_PORT == "IAC Driver Bus 3"
    assert config.channel == DEFAULT_CHANNEL == 10
    assert config.lane_mask == DEFAULT_LANE_MASK == 0x0045
    assert len(config.lanes) == 16
    assert [lane.note for lane in config.lanes] == list(range(36, 52))
    assert [lane.note for lane in DEFAULT_LANES] == list(range(36, 52))
    assert config.lanes[0].name == "kick1"
    assert config.lanes[0].label == "Kick 1"
    assert config.lanes[2].name == "snare1"
    assert config.lanes[6].name == "perc3"
    assert config.lanes[15].name == "perc12"
    assert config.lanes[15].label == "Perc 12"
    assert config.bpm == 120.0 and (config.meter_num, config.meter_den) == (4, 4)

    assert DrumConfig.from_mapping({}).lanes == DEFAULT_LANES
    for bad in (
        {"channel": 0},
        {"channel": 17},
        {"rate": 1.5},
        {"lane_mask": -1},
        {"lane_mask": 65536},
        {"velocity": 0},
        {"gate_seconds": 0},
    ):
        try:
            DrumConfig.from_mapping(bad)
        except (TypeError, ValueError):
            pass
        else:
            raise AssertionError(f"invalid drum config accepted: {bad}")

    # A fully custom note map still uses lane positions for the mask: bit 0
    # is the first lane (here note 60), not "note - 36".
    custom = [
        {"note": 60 + index, "name": name, "label": label}
        for index, (_note, name, label) in enumerate(
            (lane.note, lane.name, lane.label) for lane in DEFAULT_LANES
        )
    ]
    custom_config = DrumConfig.from_mapping(
        {"lanes": custom, "lane_mask": DEFAULT_LANE_MASK}
    )
    custom_engine = DrumEngine(custom_config)
    assert custom_engine.notes_for_step(0) == (60,)
    assert custom_engine.notes_for_step(4) == (60, 62)
    assert custom_engine.set_lane(60, False) is False
    assert custom_engine.notes_for_step(0) == ()
    assert custom_engine.set_lane(60, True) is True

    duplicate = [
        {"note": 36, "name": f"lane{i}", "label": f"Lane {i}"}
        for i in range(16)
    ]
    try:
        DrumConfig.from_mapping({"lanes": duplicate})
    except ValueError:
        pass
    else:
        raise AssertionError("duplicate lane notes accepted")
    print("  16 lanes 36-51 (C1=36), labels, defaults, invalid maps rejected")


def test_default_pattern() -> None:
    print("\n=== Test 6: deterministic four-on-the-floor pattern ===")
    pattern = build_default_pattern(4, 4)
    assert pattern.bar_steps == 16
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
    assert pattern.steps == expected, pattern.steps
    kick_steps = [i for i, lanes in enumerate(pattern.steps) if 0 in lanes]
    snare_steps = [i for i, lanes in enumerate(pattern.steps) if 2 in lanes]
    hat_steps = [i for i, lanes in enumerate(pattern.steps) if 6 in lanes]
    assert kick_steps == [0, 4, 8, 12]
    assert snare_steps == [4, 12]
    assert hat_steps == [2, 6, 10, 14]

    for num, den, steps in (
        (3, 4, 12),
        (6, 8, 12),
        (5, 4, 20),
        (7, 8, 14),
        (2, 4, 8),
    ):
        built = build_default_pattern(num, den)
        assert built.bar_steps == steps
        for index, lanes in enumerate(built.steps):
            if index % 4 == 0:
                assert 0 in lanes, (num, den, index)
            if index % 4 == 2:
                assert 6 in lanes, (num, den, index)
        snares = [i for i, lanes in enumerate(built.steps) if 2 in lanes]
        quarters = rhythm.bar_quarters(num, den)
        if quarters > 3.0:
            assert snares == [4, 12], (num, den, snares)
        else:
            assert snares == [4], (num, den, snares)

    # 12/8: bar = 6 quarter notes, so both backbeats exist.
    assert build_default_pattern(12, 8).bar_steps == 24
    assert [i for i, lanes in enumerate(build_default_pattern(12, 8).steps) if 2 in lanes] == [4, 12]
    print("  kick=quarter, snare=beats 2/4 where present, hat=offbeat 8ths")


def test_lane_mask_and_rate() -> None:
    print("\n=== Test 7: lane mask/toggles and half/normal/double ===")
    engine = DrumEngine(load_config(CONFIG_PATH))
    assert engine.lane_mask == 0x0045
    assert engine.notes_for_step(0) == (36,)
    assert engine.notes_for_step(2) == (42,)
    assert engine.notes_for_step(4) == (36, 38)
    assert engine.notes_for_step(1) == ()

    assert engine.set_lane(36, False) is False
    assert engine.lane_mask == 0x0044
    assert engine.notes_for_step(0) == ()
    assert engine.set_lane(36, True) is True
    assert engine.notes_for_step(0) == (36,)

    # Enabling an empty lane adds no hits in the 8.1 default pattern.
    assert engine.notes_for_step(1) == ()
    engine.set_lane(37, True)
    assert engine.notes_for_step(1) == ()

    for note in range(36, 52):
        engine.set_lane(note, True)
    assert engine.lane_mask == 0xFFFF
    assert len(engine.notes_for_step(0)) >= 1

    try:
        engine.set_lane(60, True)
    except ValueError:
        pass
    else:
        raise AssertionError("unknown lane note accepted")

    assert engine.rate == 1.0
    assert engine.set_rate(2.0) == 2.0
    assert engine.set_rate(0.5) == 0.5
    for bad in (0, 3, float("nan")):
        try:
            engine.set_rate(bad)
        except (TypeError, ValueError):
            pass
        else:
            raise AssertionError(f"drum rate {bad} accepted")

    pattern = engine.set_meter(3, 4)
    assert pattern.bar_steps == 12
    assert engine.meter == "3/4"
    print("  mask bits, disabled lane is silent, rates 0.5/1/2, meter rebuild")


# ---------------------------------------------------------------------------
# Fake-output scheduler
# ---------------------------------------------------------------------------


def test_fake_output_scheduler() -> None:
    print("\n=== Test 8: fake drum output receives note numbers and note-offs ===")
    fake = FakePort()
    config = DrumConfig(bpm=120, meter_num=4, meter_den=4, gate_seconds=0.01)
    rig = DrumRig(config, output=fake)
    assert rig.port_ok and rig.enabled
    assert rig.start() is True
    assert rig.port_ok

    rig.set_transport(True)
    # Wait for at least the beat-2 backbeat (step 4, 0.5 s at 120 BPM) rather
    # than relying on a fixed sleep that can be late on a loaded machine.
    deadline = time.monotonic() + 2.0
    while time.monotonic() < deadline:
        if any(message.note == 38 for message in fake.with_type("note_on")):
            break
        time.sleep(0.02)
    rig.set_transport(False)

    note_ons = fake.with_type("note_on")
    assert note_ons, fake.types()
    notes = [message.note for message in note_ons]
    # 120 BPM: step 0 = kick, step 2 = closed hat, step 4 = kick + snare.
    assert notes[0] == 36, notes
    assert 42 in notes, notes
    assert 38 in notes, notes

    # Every note-on has a matching note-off (short gate, no stuck notes).
    on = [message.note for message in note_ons]
    off = [message.note for message in fake.with_type("note_off")]
    for note in set(on):
        assert off.count(note) >= on.count(note), (note, on, off)
    controls = fake.with_type("control_change")
    assert controls and controls[-1].control == 123, controls
    rig.stop()
    assert fake.closed
    print(f"  {len(note_ons)} note-ons, matching note-offs, CC123 on stop")

    # Disabled drums produce no notes at all.
    fake2 = FakePort()
    rig2 = DrumRig(config, output=fake2)
    rig2.start()
    rig2.set_enabled(False)
    rig2.set_transport(True)
    time.sleep(0.2)
    rig2.set_transport(False)
    assert fake2.with_type("note_on") == [], fake2.types()
    rig2.stop()
    controls = fake2.with_type("control_change")
    assert controls and controls[-1].control == 123
    print("  disabled drums are silent; stop still sends All Notes Off")


def test_missing_port_degrades() -> None:
    print("\n=== Test 9: a missing drum port disables drums without raising ===")
    rig = DrumRig(DrumConfig(port="No Such JI Drum Bus"))
    try:
        assert rig.start() is False
        assert rig.port_ok is False
        assert rig.enabled is False
        rig.set_transport(True)  # must be a no-op, not a crash
        rig.set_transport(False)
    finally:
        rig.stop()
    print("  [MIDI] drums disabled ...; drone/arp continue (no exception)")


def test_midi_rig_drums_only() -> None:
    print("\n=== Test 10: MIDIRig can run drums without the MPE buses ===")
    fake = FakePort()
    rig = MIDIRig(
        midi=False,
        drums=True,
        drum_config=DrumConfig(bpm=140, gate_seconds=0.01),
        drum_output=fake,
    )
    assert rig.midi_enabled is False
    assert rig.drone is None and rig.arp is None
    rig.start()
    assert rig.drums_port_ok is True
    rig.set_drums_transport(True)
    deadline = time.monotonic() + 2.0
    while time.monotonic() < deadline and not fake.with_type("note_on"):
        time.sleep(0.02)
    rig.set_drums_transport(False)
    assert fake.with_type("note_on"), fake.types()
    enabled, port_ok, rate, mask = rig.drums_state()
    assert enabled and port_ok and rate == 1.0 and mask == DEFAULT_LANE_MASK
    rig.stop()
    assert fake.closed
    print("  drums-only rig opens just Bus 3 and reports state")


def test_drum_output_validation() -> None:
    print("\n=== Test 11: drum output rejects invalid channel/velocity ===")
    for kwargs in (
        {"channel": 0},
        {"channel": 17},
        {"velocity": 0},
        {"velocity": 128},
        {"gate_seconds": 0},
    ):
        try:
            DrumOutput("fake", **kwargs)
        except (TypeError, ValueError):
            pass
        else:
            raise AssertionError(f"invalid DrumOutput accepted: {kwargs}")
    fake = FakePort()
    output = DrumOutput("fake", channel=10, output=fake)
    output.note_on(36, 100)
    output.note_off(36)
    output.all_notes_off()
    types = [message.type for message in fake.messages]
    assert types == ["note_on", "note_off", "control_change"]
    assert all(message.channel == 9 for message in fake.messages)  # 1-based 10
    output.close()
    try:
        output.note_on(36)
    except RuntimeError:
        pass
    else:
        raise AssertionError("send after close accepted")
    print("  channel 10 -> mido index 9; note-on/off/CC123 only")


def test_invalid_osc_like_values() -> None:
    print("\n=== Test 12: invalid OSC-style values are rejected or clamped ===")
    engine = rhythm.RhythmEngine()
    assert engine.set_bpm(-10).bpm == 20.0
    assert engine.set_bpm(10_000).bpm == 300.0
    try:
        engine.set_meter(4, 3)
    except ValueError:
        pass
    else:
        raise AssertionError("unsupported meter denominator accepted")
    assert engine.set_meter(0, 16).meter == "1/16"
    try:
        rhythm.validate_drum_rate(1.25)
    except ValueError:
        pass
    else:
        raise AssertionError("unsupported drum rate accepted")
    print("  out-of-range tempo/beats clamp; invalid meter/rate raise")


def run_pure() -> None:
    print("JI Stage 8.1 rhythm + drum engine pure tests")
    print(f"config: {CONFIG_PATH}")
    test_tempo_meter_transport()
    test_tempo_meter_math()
    test_loop_quantization_and_validation()
    test_beat_clock_fake_time()
    test_config_and_note_map()
    test_default_pattern()
    test_lane_mask_and_rate()
    test_fake_output_scheduler()
    test_missing_port_degrades()
    test_midi_rig_drums_only()
    test_drum_output_validation()
    test_invalid_osc_like_values()
    print("\nALL RHYTHM TESTS PASSED")


def main() -> int:
    try:
        run_pure()
    except Exception:
        traceback.print_exc()
        print("\nRHYTHM TESTS FAILED", file=sys.stderr)
        return 1
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
