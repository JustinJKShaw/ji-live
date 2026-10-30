#!/usr/bin/env python3
"""Stage 6 MPE MIDI harness.

Run from ``ji-live/``.

Pure tests (no mido, no MIDI hardware)::

    python test_mpe.py --pure

Hardware checks (human-run, macOS with IAC Driver + Ableton)::

    uv run --with mido --with python-rtmidi python test_mpe.py --ports
    uv run --with mido --with python-rtmidi python test_mpe.py --calibrate
    uv run --with mido --with python-rtmidi python test_mpe.py --run 15

The pure tests use a fake output object.  They do **not** prove that MIDI,
IAC, Ableton, or the MPE bend range works on the human's machine.
"""

from __future__ import annotations

import argparse
import math
import random
import sys
import time
import traceback
from dataclasses import replace
from pathlib import Path

from control.arp import ArpPattern, SetArp, build_groups
from control.drone import AnchorDrone
from control.midi_rig import MIDIRig, load_config
from control.mpe_output import MPEOutput, frequency_to_pitch

ROOT = Path(__file__).resolve().parent
CONFIG_PATH = ROOT / "config" / "mpe_config.json"
FUNDAMENTAL = 220.0
INITIAL_RATIOS = [
    1.0,
    9.0 / 8.0,
    5.0 / 4.0,
    4.0 / 3.0,
    3.0 / 2.0,
    5.0 / 3.0,
    15.0 / 8.0,
]
BASE_12 = [
    1.0,
    16.0 / 15.0,
    9.0 / 8.0,
    6.0 / 5.0,
    5.0 / 4.0,
    4.0 / 3.0,
    7.0 / 5.0,
    3.0 / 2.0,
    8.0 / 5.0,
    5.0 / 3.0,
    15.0 / 8.0,
    7.0 / 4.0,
]


class FakePort:
    """Minimal stand-in for a mido output; records MPEMessage values."""

    def __init__(self) -> None:
        self.messages = []
        self.closed = False

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
            message
            for message in self.messages
            if message.type == message_type
        ]


# ----------------------------------------------------------------------
# Pure tests
# ----------------------------------------------------------------------


def test_pitch_round_trip() -> None:
    print("\n=== Test 1: frequency -> (note, bend) round-trips ===")
    frequencies = [
        55.0,
        110.0,
        220.0,
        261.625565,
        275.0,
        329.627557,
        440.0,
        659.255114,
        880.0,
        1234.567,
        1760.0,
    ]
    for frequency in frequencies:
        pitch = frequency_to_pitch(frequency, 48.0)
        assert 0 <= pitch.note <= 127, pitch
        assert 0 <= pitch.bend14 <= 16383, pitch
        assert abs(pitch.error_cents) < 0.5, (frequency, pitch.error_cents)
        print(
            f"  {frequency:8.2f} Hz -> note={pitch.note:3d} "
            f"bend={pitch.bend:+6d} error={pitch.error_cents:+.3f} cents"
        )


def test_reference_notes() -> None:
    print("\n=== Test 2: 440 Hz and 220 Hz land on centered A notes ===")
    a4 = frequency_to_pitch(440.0, 48.0)
    assert a4.note == 69, a4
    assert a4.bend == 0, a4
    assert a4.bend14 == 8192, a4

    a3 = frequency_to_pitch(220.0, 48.0)
    assert a3.note == 57, a3
    assert a3.bend == 0, a3
    print(f"  440 Hz -> note {a4.note}, bend {a4.bend}")
    print(f"  220 Hz -> note {a3.note}, bend {a3.bend}")


def test_275_hz_negative_bend() -> None:
    print("\n=== Test 3: 275 Hz uses a negative bend, not a new note ===")
    pitch = frequency_to_pitch(275.0, 48.0)
    assert pitch.note == 61, pitch
    assert pitch.bend < 0, pitch
    assert pitch.bend14 < 8192, pitch
    assert abs(pitch.error_cents) < 0.5, pitch
    print(
        f"  275 Hz -> note={pitch.note} bend={pitch.bend} "
        f"({pitch.sounding_frequency:.3f} Hz)"
    )


def test_out_of_range_transposes_octaves() -> None:
    print("\n=== Test 4: out-of-range frequencies transpose by whole octaves ===")
    low = frequency_to_pitch(5.0, 48.0)
    high = frequency_to_pitch(20000.0, 48.0)
    for pitch in (low, high):
        assert 0 <= pitch.note <= 127, pitch
        assert abs(pitch.error_cents) < 0.5, pitch
    assert low.octave_shift == 1 and low.adjusted_frequency == 10.0, low
    assert high.octave_shift == -1 and high.adjusted_frequency == 10000.0, high
    print(
        f"  5 Hz -> note={low.note} shift={low.octave_shift:+d} "
        f"adjusted={low.adjusted_frequency:.2f} Hz"
    )
    print(
        f"  20000 Hz -> note={high.note} shift={high.octave_shift:+d} "
        f"adjusted={high.adjusted_frequency:.2f} Hz"
    )


def test_group_generation() -> None:
    print("\n=== Test 5: two groups share exactly one ratio (sizes 5-12) ===")
    for size in range(5, 13):
        ratios = BASE_12[:size]
        for seed in range(20):
            pattern = build_groups(
                ratios,
                anchor=ratios[0],
                fundamental=FUNDAMENTAL,
                rng=random.Random(seed),
                shared_mode="anchor",
                octave_offsets=(0, 1, 2),
            )
            assert isinstance(pattern, ArpPattern)
            assert len(pattern.group_a) == 3, pattern
            assert len(pattern.group_b) == 3, pattern
            assert pattern.shared is pattern.group_a[0], pattern
            assert pattern.shared is pattern.group_b[0], pattern

            ratios_a = {note.ratio for note in pattern.group_a}
            ratios_b = {note.ratio for note in pattern.group_b}
            assert ratios_a & ratios_b == {pattern.shared_ratio}, pattern
            assert len(ratios_a & ratios_b) == 1, pattern

            non_shared = {note.ratio for note in pattern.group_a[1:]}
            non_shared |= {note.ratio for note in pattern.group_b[1:]}
            assert len(non_shared) == 4, (size, seed, non_shared)
            assert ratios_a <= set(ratios), (size, seed)
            assert ratios_b <= set(ratios), (size, seed)

            assert pattern.group_a[0].octave == 0, pattern
            assert pattern.group_b[0].octave == 0, pattern
            assert [note.octave for note in pattern.group_a[1:]] == [1, 2]
            assert [note.octave for note in pattern.group_b[1:]] == [1, 2]
            assert (
                pattern.group_a[0].frequency
                == pattern.group_b[0].frequency
                == pattern.shared_frequency
            )
            assert pattern.group_a[0].ratio == pattern.group_b[0].ratio
        print(f"  size={size:2d}: 20 seeds, exactly one shared class")

    # Random shared mode still picks a member of the set.
    for seed in range(20):
        pattern = build_groups(
            BASE_12,
            anchor=1.0,
            fundamental=FUNDAMENTAL,
            rng=random.Random(seed),
            shared_mode="random",
        )
        assert pattern.shared_ratio in BASE_12, pattern
        assert pattern.group_a[0] is pattern.group_b[0]


def test_small_sets_still_run() -> None:
    print("\n=== Test 6: sets below five classes still generate an arp ===")
    for size in range(1, 5):
        ratios = BASE_12[:size]
        pattern = build_groups(
            ratios,
            anchor=ratios[0],
            fundamental=FUNDAMENTAL,
            rng=random.Random(3),
        )
        assert len(pattern.steps) == 6, pattern
        assert pattern.shared is pattern.group_a[0], pattern
        assert pattern.shared is pattern.group_b[0], pattern
        assert all(note.ratio in ratios for note in pattern.steps), pattern
        print(f"  size={size}: A/B generated with reused non-shared pool")


def test_arp_sequence() -> None:
    print("\n=== Test 7: the six-step sequence alternates A and B ===")
    pattern = build_groups(
        INITIAL_RATIOS,
        anchor=1.0,
        fundamental=FUNDAMENTAL,
        rng=random.Random(1),
    )
    steps = pattern.steps
    assert len(steps) == 6, pattern
    assert steps[:3] == pattern.group_a, pattern
    assert steps[3:] == pattern.group_b, pattern
    assert steps[0] is pattern.shared and steps[3] is pattern.shared
    for group in (pattern.group_a, pattern.group_b):
        shared_count = sum(
            1 for note in group if note.ratio == pattern.shared_ratio
        )
        assert shared_count == 1, (group, pattern.shared_ratio)
    assert pattern.steps[0].frequency == pattern.steps[3].frequency
    assert pattern.steps[0].octave == pattern.steps[3].octave
    print(
        "  steps: "
        + " ".join(
            f"{note.ratio:.3f}@{note.octave:+d}" for note in pattern.steps
        )
    )


def test_drone_follows_anchor() -> None:
    print("\n=== Test 8: drone follows the anchor and only retunes when needed ===")
    fake = FakePort()
    output = MPEOutput(
        "fake-drone",
        member_channel=2,
        bend_range_semitones=48.0,
        output=fake,
    )
    drone = AnchorDrone(output)
    ratios = [1.0, 1.01, 9.0 / 8.0, 5.0 / 4.0]

    drone.on_set(ratios, 1.0, FUNDAMENTAL)
    assert fake.types() == ["pitchwheel", "note_on"], fake.types()
    assert fake.messages[-1].note == 57, fake.messages[-1]
    first_count = len(fake.messages)

    # Same anchor: nothing at all, no retrigger.
    drone.on_set(ratios, 1.0, FUNDAMENTAL)
    assert len(fake.messages) == first_count, fake.types()

    # Same MIDI note (57), different bend: pitch wheel only.
    drone.on_set(ratios, 1.01, FUNDAMENTAL)
    changed = fake.messages[first_count:]
    assert [message.type for message in changed] == ["pitchwheel"], changed
    assert changed[0].pitch > 0, changed[0]
    assert len(fake.with_type("note_on")) == 1, fake.types()

    # Different MIDI note: note-off, pitch wheel, note-on.
    before = len(fake.messages)
    drone.on_set(ratios, 9.0 / 8.0, FUNDAMENTAL)
    changed = fake.messages[before:]
    assert [message.type for message in changed] == [
        "note_off",
        "pitchwheel",
        "note_on",
    ], changed
    assert changed[0].note == 57, changed[0]
    assert changed[-1].note == 59, changed[-1]
    assert drone.note == 59, drone.note
    print("  start once; same-anchor no-op; same-note bend; new-note retrigger")


def test_drone_fallback() -> None:
    print("\n=== Test 9: a missing anchor falls back to current_ratios[0] ===")
    fake = FakePort()
    output = MPEOutput("fake-drone", output=fake)
    drone = AnchorDrone(output)
    drone.on_set([1.01, 9.0 / 8.0], None, FUNDAMENTAL)
    assert drone.is_sounding, drone
    assert abs(drone.current_frequency - 222.2) < 1e-9, drone.current_frequency
    assert fake.with_type("note_on"), fake.types()
    print(f"  fallback frequency={drone.current_frequency:.2f} Hz")


def test_stop_sends_all_notes_off() -> None:
    print("\n=== Test 10: stop sends note-off / All Notes Off ===")
    fake = FakePort()
    output = MPEOutput("fake-drone", output=fake)
    drone = AnchorDrone(output)
    drone.on_set(INITIAL_RATIOS, 1.0, FUNDAMENTAL)
    drone.stop()
    assert fake.with_type("note_off"), fake.types()
    controls = fake.with_type("control_change")
    assert controls and controls[-1].control == 123, controls

    arp_fake = FakePort()
    arp_output = MPEOutput("fake-arp", output=arp_fake)
    arp = SetArp(
        arp_output,
        step_seconds=0.03,
        gate_seconds=0.015,
        seed=1,
    )
    arp.on_set(INITIAL_RATIOS, 1.0, FUNDAMENTAL)
    arp.start()
    time.sleep(0.30)
    arp.stop()
    assert arp_fake.with_type("note_on"), arp_fake.types()
    assert arp_fake.with_type("note_off"), arp_fake.types()
    controls = arp_fake.with_type("control_change")
    assert controls and controls[-1].control == 123, controls
    print("  drone and arp both sent note-off + CC123")


def test_midi_rig_with_fake_outputs() -> None:
    print("\n=== Test 11: MIDIRig orchestrates both buses and closes them ===")
    config = replace(
        load_config(CONFIG_PATH),
        step_seconds=0.03,
        gate_seconds=0.015,
    )
    drone_fake = FakePort()
    arp_fake = FakePort()
    rig = MIDIRig(
        config,
        drone_output=drone_fake,
        arp_output=arp_fake,
    )
    rig.start()
    rig.on_set(INITIAL_RATIOS, 1.0, FUNDAMENTAL)
    time.sleep(0.25)
    rig.stop()

    assert drone_fake.with_type("note_on"), drone_fake.types()
    assert arp_fake.with_type("note_on"), arp_fake.types()
    assert drone_fake.with_type("control_change"), drone_fake.types()
    assert arp_fake.with_type("control_change"), arp_fake.types()
    assert drone_fake.closed and arp_fake.closed
    print("  both fake ports received notes and were closed")


def test_config_file() -> None:
    print("\n=== Test 12: config/mpe_config.json is valid and complete ===")
    config = load_config(CONFIG_PATH)
    assert config.drone_port == "IAC Driver Bus 1", config
    assert config.arp_port == "IAC Driver Bus 2", config
    assert config.member_channel == 2, config
    assert config.master_channel == 1, config
    assert config.bend_range_semitones == 48.0, config
    assert config.drone_velocity == 90, config
    assert config.arp_velocity == 90, config
    assert config.step_seconds == 0.25, config
    assert config.gate_seconds == 0.20, config
    assert config.arp_octave_offsets == (0, 1, 2), config
    assert config.arp_shared_mode == "anchor", config
    assert config.arp_seed == 1, config
    print(
        f"  {config.drone_port!r} / {config.arp_port!r}, "
        f"member channel {config.member_channel}, bend +/-"
        f"{config.bend_range_semitones:g} st"
    )


def run_pure() -> None:
    print("JI Stage 6 MPE pure tests")
    print(f"config: {CONFIG_PATH}")
    test_pitch_round_trip()
    test_reference_notes()
    test_275_hz_negative_bend()
    test_out_of_range_transposes_octaves()
    test_group_generation()
    test_small_sets_still_run()
    test_arp_sequence()
    test_drone_follows_anchor()
    test_drone_fallback()
    test_stop_sends_all_notes_off()
    test_midi_rig_with_fake_outputs()
    test_config_file()
    print("\nALL MPE PURE TESTS PASSED")


# ----------------------------------------------------------------------
# Hardware checks (human-run)
# ----------------------------------------------------------------------


def _import_mido():
    try:
        import mido  # hardware-only lazy import
    except ImportError:
        print(
            "[MPE TEST] mido is not installed. Run with:\n"
            "  uv run --with mido --with python-rtmidi "
            "python test_mpe.py --ports",
            file=sys.stderr,
        )
        return None
    return mido


def _check_configured_ports(config, mido) -> tuple[list[str], list[str]]:
    try:
        names = list(mido.get_output_names())
    except Exception as exc:
        raise RuntimeError(
            "could not enumerate MIDI outputs; install python-rtmidi and "
            f"check Audio MIDI Setup ({exc})"
        ) from exc
    print("mido.get_output_names():")
    for name in names:
        print(f"  {name!r}")
    required = [config.drone_port, config.arp_port]
    missing = [name for name in required if name not in names]
    return names, missing


def run_ports(config) -> int:
    mido = _import_mido()
    if mido is None:
        return 1
    _, missing = _check_configured_ports(config, mido)
    if missing:
        print(
            "[PORTS] missing configured MIDI output(s): "
            + ", ".join(repr(name) for name in missing),
            file=sys.stderr,
        )
        print(
            "[PORTS] Audio MIDI Setup -> Window -> Show MIDI Studio -> "
            "IAC Driver: check 'Device is online' and add a second bus if "
            "only 'IAC Driver Bus 1' exists.",
            file=sys.stderr,
        )
        return 1
    print(
        f"[PORTS] found drone={config.drone_port!r} "
        f"and arp={config.arp_port!r}"
    )
    return 0


def run_calibrate(config) -> int:
    mido = _import_mido()
    if mido is None:
        return 1
    _, missing = _check_configured_ports(config, mido)
    if missing:
        print(
            "[CALIBRATE] missing configured MIDI output(s): "
            + ", ".join(repr(name) for name in missing),
            file=sys.stderr,
        )
        return 1

    print(
        "[CALIBRATE] sending MIDI note 60 (C4, 261.63 Hz) with centered "
        "bend on each bus for ~1 s."
    )
    print(
        "[CALIBRATE] Both Ableton MIDI tracks (Drone = Bus 1, Arp = Bus 2) "
        "should show activity and play C4."
    )
    outputs: list[MPEOutput] = []
    try:
        for label, port, velocity in (
            ("drone", config.drone_port, config.drone_velocity),
            ("arp", config.arp_port, config.arp_velocity),
        ):
            output = MPEOutput(
                port,
                member_channel=config.member_channel,
                bend_range_semitones=config.bend_range_semitones,
                velocity=velocity,
                label=label,
            )
            output.open()
            outputs.append(output)
            print(f"[CALIBRATE] {label}: {port!r} note=60 bend=0 1.0 s")
            output.pitch_bend(0)
            output.note_on(60, velocity)
            time.sleep(1.0)
            output.note_off(60)
            output.all_notes_off()
            time.sleep(0.2)
    finally:
        for output in outputs:
            try:
                output.all_notes_off()
            finally:
                output.close()

    print(
        "[CALIBRATE] done. If either track is silent, check its MIDI From, "
        "Monitor=In, and instrument. If the pitch is not C4, check the MPE "
        "bend range/octave settings."
    )
    return 0


def run_standalone(config, seconds: float) -> int:
    rig = MIDIRig(config)
    print(
        f"[RUN] starting drone + arp for {seconds:g} s from the initial set "
        f"(anchor=1/1, fundamental={FUNDAMENTAL:.0f} Hz)"
    )
    try:
        # Store the snapshot first so start() applies it before the arp thread
        # begins; the first arp step is then immediate.
        rig.on_set(
            ratios=list(INITIAL_RATIOS),
            anchor=1.0,
            fundamental=FUNDAMENTAL,
        )
        rig.start()
        deadline = time.monotonic() + seconds
        while time.monotonic() < deadline:
            time.sleep(min(0.2, max(0.0, deadline - time.monotonic())))
    except KeyboardInterrupt:
        print("\n[RUN] interrupted")
    finally:
        rig.stop()
    print("[RUN] stopped; both buses sent All Notes Off")
    return 0


# ----------------------------------------------------------------------
# CLI
# ----------------------------------------------------------------------


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(
        description="Stage 6 MPE MIDI pure tests and hardware checks"
    )
    parser.add_argument(
        "--pure",
        action="store_true",
        help="run pure unit tests (no mido/hardware)",
    )
    parser.add_argument(
        "--ports",
        action="store_true",
        help="list MIDI outputs and verify both configured IAC buses",
    )
    parser.add_argument(
        "--calibrate",
        action="store_true",
        help="send note 60 on each bus for ~1 s",
    )
    parser.add_argument(
        "--run",
        type=float,
        metavar="SECONDS",
        help="start the drone and arp standalone for SECONDS",
    )
    parser.add_argument(
        "--config",
        default=str(CONFIG_PATH),
        help=f"MPE config path (default: {CONFIG_PATH})",
    )
    return parser


def main(argv: list[str] | None = None) -> int:
    parser = build_parser()
    args = parser.parse_args(argv)

    modes = sum(
        [
            1 if args.pure else 0,
            1 if args.ports else 0,
            1 if args.calibrate else 0,
            1 if args.run is not None else 0,
        ]
    )
    if modes > 1:
        parser.error("choose only one of --pure, --ports, --calibrate, --run")
    if modes == 0:
        args.pure = True
    if args.run is not None:
        if not math.isfinite(args.run) or args.run <= 0.0:
            parser.error("--run must be a positive number of seconds")

    if args.pure:
        try:
            run_pure()
        except Exception:
            traceback.print_exc()
            return 1
        return 0

    try:
        config = load_config(args.config)
        if args.ports:
            return run_ports(config)
        if args.calibrate:
            return run_calibrate(config)
        return run_standalone(config, args.run)
    except Exception as exc:
        print(f"[MPE TEST] error: {exc}", file=sys.stderr)
        return 1


if __name__ == "__main__":
    raise SystemExit(main())
