#!/usr/bin/env python3
"""Stage 9.1 pure tests: mapping, quantisation, recorder, key map, MIDI port,
input rig, record state machine, melody library, the played-motif policy and the
inline performance-state notes.

Run from ``ji-live/``::

    uv run --with python-osc python tests/test_lead_input.py

No mido, PyQt6, sockets, audio or real MIDI ports.  Fake outputs/ports and a
controllable clock keep every check deterministic (the record supervisor's tiny
real-time polls are replaced by direct state transitions where possible).
"""

from __future__ import annotations

import contextlib
import io
import math
import sys
import tempfile
import time
import traceback
import types
from dataclasses import replace
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

import control.ji_bridge
import control.ji_bridge as bridge_module
import control.lead as lead_module
import control.lead_input as input_module
import control.melody as melody_module
import control.state as state_module
import ui.osc_control  # noqa: F401 - import-time behaviour is under test
import ui.osc_control as ui_osc_control
from control.lead import (
    LeadConfig,
    LeadEngine,
    LeadNote,
    LeadRig,
    LeadScheduler,
    LeadSettings,
    generate_motif,
)
from control.lead_input import (
    KEY_SEMITONES,
    LeadInputRig,
    LeadInputSettings,
    LeadRecorder,
    MappedNote,
    MidiInputPort,
    count_in_beats,
    key_midi,
    map_note,
    quantize_step,
    record_window,
)
from control.melody import Melody, MelodyError, MelodyLibrary
from control.mpe_output import MPEMessage, MPEOutput
from control.mutation import SetSnapshot
from control.rhythm import RhythmEngine, RhythmState, bar_quarters

RATIOS = (1.0, 9.0 / 8.0, 5.0 / 4.0, 4.0 / 3.0, 3.0 / 2.0, 5.0 / 3.0, 15.0 / 8.0)
FUNDAMENTAL = 220.0


# ---------------------------------------------------------------------------
# Fakes
# ---------------------------------------------------------------------------


class FakePort:
    """Duck-typed MPE output capturing messages (like tests/test_lead.py)."""

    def __init__(self) -> None:
        self.messages: list = []
        self.closed = False

    def send(self, message: MPEMessage) -> None:
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


class FakeMido:
    """Fake mido module: list names, open an input, feed messages."""

    def __init__(self, names: tuple[str, ...] = ("Launchkey 49",)) -> None:
        self.names = list(names)
        self.callback = None
        self.opened: list[str] = []
        self.closed = 0

    def get_input_names(self) -> list[str]:
        return list(self.names)

    def open_input(self, name: str, callback=None):
        self.opened.append(name)
        self.callback = callback
        return self

    def close(self) -> None:
        self.closed += 1

    def feed(
        self,
        kind: str,
        *,
        note: int = 0,
        velocity: int = 0,
        channel: int = 0,
        control: int = 0,
        value: int = 0,
        pitch: int = 0,
    ) -> None:
        assert self.callback is not None
        self.callback(
            types.SimpleNamespace(
                type=kind,
                note=note,
                velocity=velocity,
                channel=channel,
                control=control,
                value=value,
                pitch=pitch,
            )
        )


class FakeClock:
    def __init__(self, rhythm: "FakeRhythm") -> None:
        self.rhythm = rhythm

    def time_of_beat(self, beat: float) -> float:
        # Far in the future so the supervisor never advances on its own.
        return time.monotonic() + 100.0 + float(beat) * 0.5


class FakeRhythm:
    """Minimal RhythmEngine surface used by the input rig."""

    def __init__(self, *, running: bool = True, bpm: float = 120.0) -> None:
        self.running = running
        self.bpm = bpm
        self.meter_num = 4
        self.meter_den = 4
        self._beat = 0.0
        self.state = RhythmState.create(bpm, 4, 4, running)
        self.clock = FakeClock(self)

    def beat_at(self, at=None) -> float:
        return self._beat

    def set_running(self, running: bool) -> RhythmState:
        self.running = bool(running)
        self.state = self.state.with_running(self.running)
        return self.state


class FakeRhythmStop(FakeRhythm):
    pass


def _mapper(midi: int, velocity: int) -> MappedNote:
    return MappedNote(
        midi_in=midi,
        frequency=220.0,
        ratio=1.0,
        degree=midi % len(RATIOS),
        octave=0,
        cents=0.0,
    )


def _lead(output=None) -> LeadRig:
    rig = LeadRig(LeadConfig(), output=output if output is not None else FakePort())
    rig.engine.on_set(RATIOS, 1.0, FUNDAMENTAL)
    # Stage 9.2: note entry is gated by port_ok + monitor + Lead enabled.
    rig.engine.set_enabled(True)
    return rig


def _settings(**overrides) -> LeadInputSettings:
    values = {
        "port": "Launchkey",
        "match": "contains",
        "channel": 0,
        "quantize": 1.0,
        "count_in": "off",
        "bars": 1,
        "velocity_scale": 1.0,
        "octave_shift": 0,
    }
    values.update(overrides)
    return LeadInputSettings.create(**values)


def _input_rig(**overrides):
    lead = _lead()
    rhythm = FakeRhythm()
    fake = FakeMido()
    rig = LeadInputRig(lead, rhythm, _settings(**overrides), port=fake)
    assert rig.start()
    return rig, lead, rhythm, fake


# ---------------------------------------------------------------------------
# 1. Mapping
# ---------------------------------------------------------------------------


def test_mapping_worked_values() -> None:
    print("\n=== Test 1: nearest-class mapping (documented worked values) ===")
    cases = (
        (61, 0, 5.0 / 4.0, 2, 0, 13.686286),
        (64, 0, 3.0 / 2.0, 4, 0, -1.9550009),
        (69, 1, 1.0, 0, 1, 0.0),
        (66, 0, 5.0 / 3.0, 5, 0, 15.641170),
    )
    for midi, octave, ratio, degree, note_octave, cents in cases:
        mapped = map_note(midi, RATIOS, FUNDAMENTAL)
        assert math.isclose(mapped.ratio, ratio, rel_tol=1e-12), (midi, mapped)
        assert mapped.degree == degree, (midi, mapped)
        assert mapped.octave == note_octave, (midi, mapped)
        assert math.isclose(mapped.cents, cents, abs_tol=0.01), (midi, mapped)
        assert math.isclose(
            mapped.frequency,
            FUNDAMENTAL * ratio * 2.0**octave,
            rel_tol=1e-12,
        ), (midi, mapped)

    # Every equal-tempered class maps to itself with 0 cents (a synthetic
    # 12-class ET set whose classes are exact MIDI notes).
    et = tuple(2.0 ** (index / 12.0) for index in range(12))
    for index, ratio in enumerate(et):
        midi = 69 + index
        mapped = map_note(midi, et, 440.0)
        assert mapped.degree == index, (midi, mapped)
        assert mapped.ratio == ratio
        assert abs(mapped.cents) < 1e-9, (midi, mapped)
        assert math.isclose(mapped.frequency, 440.0 * ratio, rel_tol=1e-12)

    # Above and below the fundamental, and a folded pitch exactly at 2F.
    below = map_note(56, RATIOS, FUNDAMENTAL)
    assert below.octave == -1 and below.ratio == 15.0 / 8.0
    above = map_note(81, RATIOS, FUNDAMENTAL)
    assert above.octave == 2 and above.ratio == 1.0 and above.cents == 0.0
    assert math.isclose(above.frequency, 880.0)

    # Sorted degree order for a 3-class set.
    three = map_note(64, (1.0, 1.25, 1.5), 220.0)
    assert three.degree == 2 and three.ratio == 1.5

    # base_octave is subtracted so projection reproduces f_out.
    for base in (-2, 0, 2):
        mapped = map_note(69, RATIOS, FUNDAMENTAL, base)
        assert mapped.octave == 1 - base
        engine = LeadEngine(LeadConfig())
        engine.on_set(RATIOS, 1.0, FUNDAMENTAL)
        engine.load_motif(
            (
                LeadNote(
                    step=0,
                    length=1,
                    degree=mapped.degree,
                    octave=mapped.octave,
                    ratio_hint=mapped.ratio,
                    velocity=90,
                ),
            ),
            grid="1/8",
            phrase_bars=1,
        )
        engine.set_setting("base_octave", base)
        projected = engine.projection()[0]
        assert math.isclose(
            projected.frequency, mapped.frequency, rel_tol=1e-12
        ), (base, projected.frequency, mapped.frequency)

    # Empty/invalid ratios normalise to (1.0,) like lead._clean_ratios.
    empty = map_note(69, (), FUNDAMENTAL)
    assert empty.ratio == 1.0 and empty.degree == 0
    bad = map_note(69, (-1.0, float("nan"), 0.0), FUNDAMENTAL)
    assert bad.ratio == 1.0

    for call in (
        lambda: map_note(128, RATIOS, 220.0),
        lambda: map_note(-1, RATIOS, 220.0),
        lambda: map_note(True, RATIOS, 220.0),
        lambda: map_note(60, RATIOS, 0.0),
        lambda: map_note(60, RATIOS, float("inf")),
    ):
        try:
            call()
        except (TypeError, ValueError):
            pass
        else:
            raise AssertionError("invalid map_note input accepted")
    print("  worked table, self-mapping 0 cents, 2F fold, base_octave, errors")


# ---------------------------------------------------------------------------
# 2. Quantiser
# ---------------------------------------------------------------------------


def test_quantizer() -> None:
    print("\n=== Test 2: swing-aware quantiser (stored step is unswung) ===")
    # 120 BPM: one 1/8 step = 0.25 s.  Record from beat 4.
    args = dict(
        grid_quarters=0.5,
        strength=1.0,
        swing=0.0,
        step_seconds=0.25,
        steps_per_beat=2,
    )
    positions = (4.02, 4.52, 5.08, 6.06)
    assert [quantize_step(position, 4.0, **args) for position in positions] == [
        0,
        1,
        2,
        4,
    ]

    # Rounding at .5 uses Python's round (documented, deterministic).
    assert quantize_step(4.25, 4.0, **args) == 0
    assert quantize_step(4.26, 4.0, **args) == 1

    # Strength: 1 pulls to the swung grid, 0 is as played (still rounded).
    strength_args = dict(
        grid_quarters=0.5,
        swing=0.5,
        step_seconds=0.25,
        steps_per_beat=2,
    )
    played = 4.6  # s = 1.2, odd step
    weak = quantize_step(played, 4.0, strength=0.0, **strength_args)
    half = quantize_step(played, 4.0, strength=0.5, **strength_args)
    exact = quantize_step(played, 4.0, strength=1.0, **strength_args)
    assert weak == 1, weak
    assert half == 1, half
    assert exact == 1, exact  # swung 1.25 rounds back to the unswung step 1

    # Swing is applied to odd steps only, and the stored step never moves.
    straight = quantize_step(4.6, 4.0, 0.5, 1.0, 0.0, 0.25, 2)
    swung = quantize_step(4.6, 4.0, 0.5, 1.0, 0.5, 0.25, 2)
    even = quantize_step(5.1, 4.0, 0.5, 1.0, 0.5, 0.25, 2)
    assert straight == swung == 1
    assert even == 2

    # 1/8T grid: 1/3 quarter per step and 3 steps per beat.
    triplet = dict(
        grid_quarters=1.0 / 3.0,
        strength=1.0,
        swing=0.0,
        step_seconds=1.0 / 6.0,
        steps_per_beat=3,
    )
    assert quantize_step(4.0, 4.0, **triplet) == 0
    assert quantize_step(4.34, 4.0, **triplet) == 1
    assert quantize_step(4.67, 4.0, **triplet) == 2

    # 3/4 (3 quarters) and 7/8 (3.5 quarters) count-in ceil rule.
    assert count_in_beats("1 bar", bar_quarters(3, 4)) == 3.0
    assert count_in_beats("1 bar", bar_quarters(6, 8)) == 3.0
    assert count_in_beats("1 bar", bar_quarters(7, 8)) == 4.0
    assert count_in_beats("1 bar", bar_quarters(5, 8)) == 3.0
    assert count_in_beats("2 bars", bar_quarters(7, 8)) == 8.0
    assert count_in_beats("1 beat", 4.0) == 1.0
    assert count_in_beats("off", 4.0) == 0.0
    # start is always the next bar line.
    assert record_window(4.2, 0.0, 4.0, 2) == (8.0, 16.0)
    assert record_window(8.0, 0.0, 4.0, 2) == (8.0, 16.0)
    assert record_window(4.2, 3.0, 4.0, 1) == (8.0, 12.0)
    assert record_window(8.0, 4.0, 4.0, 1) == (12.0, 16.0)
    print("  straight/swung/triplet grids, strength, 3/4, 5/8, 7/8, bar lines")


# ---------------------------------------------------------------------------
# 3. Recorder
# ---------------------------------------------------------------------------


def _recorder() -> LeadRecorder:
    return LeadRecorder(mapper=_mapper)


def _finish(recorder: LeadRecorder, start: float = 4.0, steps: int = 8):
    return recorder.finish(
        start_beat=start,
        grid="1/8",
        strength=1.0,
        swing=0.0,
        step_seconds=0.25,
        steps_per_beat=2,
        phrase_steps=steps,
    )


def test_recorder() -> None:
    print("\n=== Test 3: recorder (monophonic last, window, length) ===")
    recorder = _recorder()
    recorder.note_on(60, 100, 4.0)
    recorder.note_off(60, 4.6)
    notes = _finish(recorder)
    assert len(notes) == 1 and notes[0].step == 0 and notes[0].length == 1

    # A re-struck key closes the previous note at the new onset.
    recorder = _recorder()
    recorder.note_on(60, 100, 4.0)
    recorder.note_on(60, 90, 4.5)
    recorder.note_off(60, 5.0)
    notes = _finish(recorder)
    assert [note.step for note in notes] == [0, 1]
    assert notes[0].length == 1 and notes[1].length == 1
    assert notes[0].velocity == 100 and notes[1].velocity == 90

    # A chord keeps the last note and counts the earlier one as reduced.
    recorder = _recorder()
    recorder.note_on(60, 100, 4.0)
    recorder.note_on(64, 90, 4.2)
    assert recorder.dropped == 1
    recorder.note_off(64, 4.8)
    recorder.note_off(60, 4.8)  # already reduced: ignored
    notes = _finish(recorder)
    assert len(notes) == 1 and notes[0].step == 0

    # A note held past the window closes at the window end.
    recorder = _recorder()
    recorder.note_on(60, 100, 4.0)
    notes = _finish(recorder, steps=8)
    assert notes[0].length == 8

    # Zero/negative length becomes one step.
    recorder = _recorder()
    recorder.note_on(60, 100, 4.0)
    recorder.note_off(60, 4.0)
    notes = _finish(recorder)
    assert notes[0].length == 1

    # Notes outside [start, end) are dropped.
    recorder = _recorder()
    recorder.note_on(60, 100, 3.9)
    recorder.note_off(60, 4.1)
    recorder.note_on(64, 100, 8.1)
    recorder.note_off(64, 8.2)
    assert _finish(recorder) == ()

    # Empty take.
    empty = LeadRecorder(mapper=_mapper)
    assert _finish(empty) == ()
    print("  pairs, retrigger, chord reduction, held/zero/outside, empty")


# ---------------------------------------------------------------------------
# 4. Key map
# ---------------------------------------------------------------------------


def test_key_map() -> None:
    print("\n=== Test 4: QWERTY key map ===")
    notes = {key_midi(key) for key in KEY_SEMITONES}
    assert len(notes) == 12 and min(notes) == 60 and max(notes) == 71
    assert key_midi("a") == 60 and key_midi("j") == 71
    assert key_midi("z") is None
    assert key_midi("q") is None
    assert key_midi("a", -2) == 36
    assert key_midi("j", 2) == 95
    assert key_midi("a", -99) == 36  # shift clamped to -2
    assert key_midi("j", 99) == 95  # shift clamped to +2
    assert key_midi("A") == 60
    print("  12 distinct semitones, z/x octave clamp, unmapped keys")


# ---------------------------------------------------------------------------
# 5. Port adapter
# ---------------------------------------------------------------------------


def test_port_adapter() -> None:
    print("\n=== Test 5: lazy MIDI input adapter (fake module) ===")
    fake = FakeMido(("Launchkey 49", "Launchkey Mini", "Other"))
    events: list[tuple[str, int, int]] = []
    port = MidiInputPort(
        "launchkey 49", match="contains", callback=lambda *args: events.append(args), port=fake
    )
    assert port.available() == ("Launchkey 49", "Launchkey Mini", "Other")
    assert port.resolve() == "Launchkey 49"
    port.open()
    assert port.is_open and fake.opened == ["Launchkey 49"]
    fake.feed("note_on", note=60, velocity=100)
    fake.feed("note_on", note=60, velocity=0)
    fake.feed("note_off", note=62, velocity=64)
    fake.feed("control_change", control=64, value=127)
    fake.feed("pitchwheel", pitch=-2048)
    fake.feed("control_change", control=1, value=64)
    fake.feed("control_change", control=74, value=99)
    fake.feed("aftertouch", value=40)
    fake.feed("clock")
    assert events == [
        ("on", 60, 100),
        ("off", 60, 0),
        ("off", 62, 0),
        ("sustain", 64, 127),
        ("bend", -2048, 0),
        ("mod", 1, 64),
    ], events
    port.close()
    assert fake.closed == 1

    exact = MidiInputPort("Launchkey", match="exact", port=FakeMido(("Launchkey", "Launchkey Mini")))
    assert exact.resolve() == "Launchkey"
    for port_name, names, match in (
        ("Foo", ("Launchkey 49",), "contains"),
        ("MIDI", ("MIDI 1", "MIDI 2"), "contains"),
        ("launchkey", ("Launchkey 49",), "exact"),
    ):
        probe = MidiInputPort(port_name, match=match, port=FakeMido(names))
        try:
            probe.resolve()
        except ValueError as exc:
            message = str(exc)
            assert "available" not in message or names[0] in message
            if len(names) > 1:
                assert "more specific" in message
        else:
            raise AssertionError(f"{port_name!r} resolved instead of raising")

    # Channel filter: channel 2 accepts mido channel index 1 only.
    filtered: list[tuple[str, int, int]] = []
    fake2 = FakeMido()
    channel_port = MidiInputPort(
        "Launchkey", channel=2, callback=lambda *a: filtered.append(a), port=fake2
    )
    channel_port.open()
    fake2.feed("note_on", note=60, velocity=100, channel=0)
    fake2.feed("note_on", note=61, velocity=100, channel=1)
    fake2.feed("pitchwheel", pitch=200, channel=0)
    fake2.feed("pitchwheel", pitch=200, channel=1)
    fake2.feed("control_change", control=1, value=90, channel=0)
    fake2.feed("control_change", control=1, value=90, channel=1)
    assert filtered == [
        ("on", 61, 100),
        ("bend", 200, 0),
        ("mod", 1, 90),
    ], filtered

    # A disappearing device is one [LEAD-IN] line, never a fatal error.
    lost = MidiInputPort("Launchkey", callback=lambda *a: None, port=FakeMido())
    lost.open()
    buffer = io.StringIO()
    broken = types.SimpleNamespace(type="note_on", note="x", velocity=1, channel=0)
    with contextlib.redirect_stdout(buffer):
        lost._on_message(broken)
        lost._on_message(broken)
    text = buffer.getvalue()
    assert text.count("input lost") == 1, text
    assert "the rest of the rig continues" in text
    print("  contains/exact, 0/>1 matches raise, velocity 0 off, sustain, channel,")
    print("  pitchwheel/CC1 translated with the channel filter, others ignored")
    print("  lost device reported once")


# ---------------------------------------------------------------------------
# 6. Input rig
# ---------------------------------------------------------------------------


def test_input_rig_notes() -> None:
    print("\n=== Test 6: input rig note ownership (fake output + fake port) ===")
    rig, lead, rhythm, fake = _input_rig()
    output = lead.output
    # Stage 9.2: a key sounds at any time (no Live toggle); with the mod wheel
    # at 0 the note-on is preceded by CC 1 0 (the input always states it).
    rig.note_on(61, 100)
    kinds = output._output.types()
    assert kinds == ["pitchwheel", "control_change", "note_on"], kinds
    # The bend precedes the note-on and the mapped frequency is the nearest class.
    note_on = output._output.with_type("note_on")[0]
    assert note_on.note == 61
    assert note_on.channel == 1  # member channel 2 -> mido index 1

    # Retrigger releases the held note before the next bend/CC/note-on.
    rig.note_on(64, 90)
    kinds = output._output.types()
    assert kinds[-4:] == ["note_off", "pitchwheel", "control_change", "note_on"], kinds

    # A mismatched note-off is ignored (the held note keeps sounding).
    before = len(output._output.messages)
    rig.note_off(99)
    assert len(output._output.messages) == before
    rig.note_off(64)
    assert output._output.types()[-1] == "note_off"

    # stop() sends note-off + CC 123 and closes the port.
    rig.note_on(61, 100)
    rig.stop()
    messages = output._output.messages
    assert any(
        message.type == "control_change" and message.control == 123
        for message in messages
    )
    assert fake.closed >= 1

    # Velocity scaling and octave-base mapping still use the set.
    rig2, lead2, _rhythm2, _fake2 = _input_rig(velocity_scale=0.5)
    rig2.note_on(69, 127)
    note = lead2.output._output.with_type("note_on")[-1]
    assert note.velocity == 64, note.velocity
    rig2.stop()
    print("  bend-before-on, retrigger off, mismatch ignored, CC 123, velocity")


def test_input_rig_sustain() -> None:
    print("\n=== Test 7: sustain (CC 64) defers the note-off ===")
    rig, lead, _rhythm, fake = _input_rig()
    rig.note_on(60, 100)
    fake.feed("control_change", control=64, value=127)
    fake.feed("note_off", note=60)
    assert lead.output._output.types()[-1] == "note_on"
    fake.feed("control_change", control=64, value=0)
    assert lead.output._output.types()[-1] == "note_off"
    rig.stop()
    print("  deferred release on key-up, released on pedal-up")


# ---------------------------------------------------------------------------
# 7. Record state machine
# ---------------------------------------------------------------------------


def test_record_state_machine() -> None:
    print("\n=== Test 8: record state machine ===")
    # Count-in ceil is pure and covered above; here: skip, cancel, keep, abort.
    rig, lead, rhythm, fake = _input_rig()
    rhythm._beat = 0.0
    assert rig.start_record()
    assert rig.state == "recording", rig.state
    fake.feed("note_on", note=61, velocity=100)
    fake.feed("note_off", note=61)
    rhythm.running = False
    rhythm.state = rhythm.state.with_running(False)
    rig.stop_record("user")
    assert rig.state == "idle"
    assert len(rig.take_notes) == 1
    assert lead.engine.motif.source == "played"
    assert not lead.scheduler.is_running
    rig.stop()

    # Second Rec during the count-in skips it (window opens at the next bar).
    rig, lead, rhythm, _fake = _input_rig(count_in="2 bars")
    rhythm._beat = 4.2
    assert rig.start_record()
    assert rig.state == "count_in"
    first_start = rig._start_beat
    assert rig.start_record()
    assert rig._start_beat == 8.0 and rig._start_beat <= first_start
    assert rig.state == "count_in"
    rig.stop_record("user")  # cancels silently
    assert rig.state == "idle"
    rig.stop()

    # Stop during the count-in cancels without a take or a log line.
    rig, lead, rhythm, _fake = _input_rig(count_in="1 bar")
    rhythm._beat = 1.0
    assert rig.start_record()
    assert rig.state == "count_in"
    buffer = io.StringIO()
    with contextlib.redirect_stdout(buffer):
        rig.stop_record("user")
    assert rig.state == "idle" and rig.take_notes == ()
    assert "recorded" not in buffer.getvalue()
    rig.stop()

    # A transport stop finishes the take, keeps it and does not auto-play.
    rig, lead, rhythm, fake = _input_rig()
    rhythm._beat = 0.0
    assert rig.start_record()
    fake.feed("note_on", note=61, velocity=100)
    fake.feed("note_off", note=61)
    rhythm.set_running(False)
    buffer = io.StringIO()
    with contextlib.redirect_stdout(buffer):
        rig.transport_stopped()
    assert "recording aborted (transport stop); take kept" in buffer.getvalue()
    assert len(rig.take_notes) == 1
    assert lead.source == "played"  # the take is ready for the next Play
    assert not lead.scheduler.is_running
    rig.stop()

    # A transport stop during a count-in cancels it (no silent take).
    rig, lead, rhythm, _fake = _input_rig(count_in="1 bar")
    rhythm._beat = 1.0
    assert rig.start_record() and rig.state == "count_in"
    rhythm.set_running(False)
    rig.transport_stopped()
    assert rig.state == "idle"
    assert lead.engine.motif.source == "generated"
    assert rig.take_notes == ()
    rig.stop()

    # An empty take keeps the current line (never installs a silent motif).
    rig, lead, rhythm, _fake = _input_rig()
    rhythm._beat = 0.0
    assert rig.start_record() and rig.state == "recording"
    rhythm.set_running(False)
    buffer = io.StringIO()
    with contextlib.redirect_stdout(buffer):
        assert rig.finish_recording() == ()
    assert "recorded 0 notes" in buffer.getvalue()
    assert lead.engine.motif.source == "generated"
    assert lead.source == "generated"  # the label matches the line that stayed
    rig.stop()

    # A take can be loaded as the motif and reverted.
    rig, lead, rhythm, fake = _input_rig()
    rhythm._beat = 0.0
    assert rig.start_record()
    fake.feed("note_on", note=61, velocity=100)
    rhythm._beat = 0.6
    fake.feed("note_off", note=61)
    rhythm.running = False
    rhythm.state = rhythm.state.with_running(False)
    notes = rig.finish_recording()
    assert notes and lead.engine.motif.source == "played"
    assert lead.engine.settings.gate_mode == "note"
    rig.stop()
    print("  skip, cancel, keep partial, abort on stop, load, no auto-play")


# ---------------------------------------------------------------------------
# 8. Melody library
# ---------------------------------------------------------------------------


def _melody(name: str = "intro line") -> Melody:
    notes = (
        LeadNote(step=0, length=1, degree=2, octave=0, ratio_hint=1.25, velocity=104),
        LeadNote(step=2, length=2, degree=4, octave=0, ratio_hint=1.5, velocity=90),
    )
    return Melody(
        name=name,
        grid="1/8",
        phrase_bars=2,
        meter_num=4,
        meter_den=4,
        bpm=120.0,
        quantize=1.0,
        swing=0.0,
        notes=notes,
        base_notes=notes,
        history=(("invert", 1),),
        variation="identity",
        variation_index=1,
        ratios=RATIOS,
        fundamental=220.0,
    )


def test_melody_library() -> None:
    print("\n=== Test 9: melody library (save/load/delete, atomic) ===")
    with tempfile.TemporaryDirectory() as directory:
        library = MelodyLibrary(directory)
        melody = _melody()
        path = library.save(melody)
        assert path.exists() and path.name == "intro line.json"
        assert library.count() == 1 and library.names() == ("intro line",)
        loaded = library.load("intro line")
        assert loaded == melody, (loaded, melody)
        assert loaded.history == (("invert", 1),)
        assert loaded.notes == melody.notes
        assert not list(Path(directory).glob("*.tmp"))

        # Case-only duplicate is refused.
        try:
            library.save(replace(melody, name="Intro Line"))
        except MelodyError as exc:
            assert "case" in str(exc)
        else:
            raise AssertionError("case-only duplicate accepted")

        # Bad names.
        for bad in ("", "  ", ".hidden", "a/b", "..", "x" * 41):
            try:
                library.save(replace(melody, name=bad))
            except MelodyError:
                pass
            else:
                raise AssertionError(f"bad name accepted: {bad!r}")

        # Newer version refused with one clear error.
        document = melody.to_mapping()
        document["version"] = 2
        try:
            Melody.from_mapping(document)
        except MelodyError as exc:
            assert exc.kind == "format" and "version 2" in str(exc)
        else:
            raise AssertionError("a newer melody version was accepted")

        # A wrong format is refused.
        bad_doc = melody.to_mapping()
        bad_doc["format"] = "other"
        try:
            Melody.from_mapping(bad_doc)
        except MelodyError as exc:
            assert exc.kind == "format"
        else:
            raise AssertionError("a wrong format was accepted")

        # Step outside the phrase is refused.
        bad_doc = melody.to_mapping()
        bad_doc["notes"][0]["step"] = 999
        try:
            Melody.from_mapping(bad_doc)
        except MelodyError:
            pass
        else:
            raise AssertionError("an out-of-range step was accepted")

        # List sorting + delete.
        library.save(_melody("zeta"))
        library.save(_melody("Alpha"))
        assert library.names() == ("Alpha", "intro line", "zeta")
        assert library.delete("intro line") is True
        assert library.delete("intro line") is False
        assert library.count() == 2
    print("  round trip, names, case duplicate, version, temp cleanup, sort, delete")


# ---------------------------------------------------------------------------
# 9. Engine policy
# ---------------------------------------------------------------------------


def _played_engine() -> LeadEngine:
    engine = LeadEngine(LeadConfig())
    engine.on_set(RATIOS, 1.0, FUNDAMENTAL)
    notes = (
        LeadNote(step=0, length=2, degree=2, octave=0, ratio_hint=1.25, velocity=100),
        LeadNote(step=3, length=1, degree=4, octave=0, ratio_hint=1.5, velocity=90),
        LeadNote(step=6, length=2, degree=0, octave=1, ratio_hint=1.0, velocity=80),
    )
    engine.load_motif(notes, grid="1/8", phrase_bars=2)
    return engine


def test_engine_policy() -> None:
    print("\n=== Test 10: played-motif policy, variations, revert ===")
    engine = _played_engine()
    motif = engine.motif
    assert motif.source == "played"
    assert engine.settings.gate_mode == "note"
    assert engine.settings.grid == "1/8" and engine.settings.phrase_bars == 2
    assert motif.base_notes == motif.notes

    # grid rescale: 1/8 -> 1/16 doubles every stored step.
    engine.set_setting("grid", "1/16")
    assert engine.motif.phrase_steps == 32
    assert [note.step for note in engine.motif.notes] == [0, 6, 12]
    assert [note.length for note in engine.motif.notes] == [4, 2, 4]
    assert engine.motif.source == "played"

    # phrase_bars crop and tile.
    engine.set_setting("phrase_bars", 1)
    assert engine.motif.phrase_steps == 16
    assert all(note.step < 16 for note in engine.motif.notes)
    engine.set_setting("phrase_bars", 2)
    steps = [note.step for note in engine.motif.notes]
    assert len(steps) >= 6 and max(steps) >= 16

    # density and seed never regenerate a played line.
    before = engine.motif.notes
    engine.set_setting("density", 0.2)
    assert engine.motif.notes == before
    engine.set_setting("seed", 77)
    assert engine.motif.notes == before
    assert engine.motif.history == () and engine.motif.variation_index == 0

    # octave_range clamps recorded octaves only when > 0.
    engine = _played_engine()
    engine.set_setting("octave_range", 0)
    assert engine.motif.notes[2].octave == 1
    engine.set_setting("octave_range", 1)
    assert all(abs(note.octave) <= 1 for note in engine.motif.notes)

    # A set mutation keeps the contour: surviving ratios keep the pitch.
    engine = _played_engine()
    frequencies = {
        note.step: round(note.frequency, 6) for note in engine.projection()
    }
    engine.on_set((1.0, 9.0 / 8.0, 4.0 / 3.0, 3.0 / 2.0), 1.0, FUNDAMENTAL)
    projected = {note.step: round(note.frequency, 6) for note in engine.projection()}
    # 5/4 was removed -> only that note moves; 3/2 and 1/1 keep their pitch.
    assert projected[0] != frequencies[0], (projected, frequencies)
    assert projected[3] == frequencies[3], (projected, frequencies)
    assert projected[6] == frequencies[6], (projected, frequencies)
    assert engine.motif.source == "played"

    # Vary compounds and appends history.
    engine = _played_engine()
    base = engine.motif.notes
    engine.set_setting("variation", "invert")
    engine.vary()
    history = engine.motif.history
    assert len(history) == 2 and history[0][0] == "invert" and history[1][0] == "invert"
    assert engine.motif.variation_index == 1
    assert engine.motif.source == "played" and engine.motif.base_notes == base

    # random keeps the Revert target.
    engine.set_setting("variation", "random")
    engine.vary()
    assert engine.motif.variation == "random"
    assert engine.motif.source == "played"
    assert engine.motif.base_notes == base
    assert len(engine.motif.history) == 4

    # Revert restores the recorded base exactly.
    reverted = engine.revert()
    assert reverted.notes == base
    assert reverted.history == () and reverted.variation_index == 0
    assert reverted.source == "played"

    # set_variation_index replays the stored (name, index) history exactly.
    engine = _played_engine()
    engine.set_setting("variation", "invert")
    engine.vary()
    engine.set_setting("variation", "rhythm")
    engine.vary()
    captured = engine.motif.notes
    captured_history = engine.motif.history
    assert len(captured_history) == 4
    engine.set_variation_index(2, force=True)
    assert engine.motif.notes == captured
    assert engine.motif.history == captured_history
    engine.set_variation_index(0)
    assert engine.motif.notes == engine.motif.base_notes
    assert engine.motif.history == ()

    # A target past the stored history keeps applying the selected variation.
    engine.set_variation_index(6)
    assert engine.motif.variation_index == 6
    assert len(engine.motif.history) == 6

    # After a seed change the notes stay, the history clears; a self-contained
    # state with the saved line and no history installs that line exactly.
    engine = _played_engine()
    engine.vary()
    varied = engine.motif.notes
    base = engine.motif.base_notes
    engine.set_setting("seed", 9)
    assert engine.motif.notes == varied and engine.motif.history == ()
    fresh = LeadEngine(LeadConfig())
    fresh.on_set(RATIOS, 1.0, FUNDAMENTAL)
    fresh.load_motif(varied, base_notes=base, history=(), variation_index=0)
    assert fresh.motif.notes == varied
    assert fresh.motif.base_notes == base
    fresh.revert()
    assert fresh.motif.notes == base

    # Loading a melody installs it without generating.
    engine = _played_engine()
    assert engine.motif.source == "played"
    engine.clear_melody()
    assert engine.motif.source == "generated"
    assert engine.motif.base_notes == () and engine.motif.history == ()

    # The generated line is unchanged when the source is generated.
    first = LeadEngine(LeadConfig())
    second = LeadEngine(LeadConfig())
    first.on_set(RATIOS, 1.0, FUNDAMENTAL)
    second.on_set(RATIOS, 1.0, FUNDAMENTAL)
    assert first.motif.notes == second.motif.notes
    assert first.motif.source == "generated"
    assert first.state_args(True, True) == second.state_args(True, True)
    assert len(first.state_args(True, True)) == 17
    print("  rescale, crop/tile, density/seed, octaves, mutation, vary, random,")
    print("  revert, history replay, clear, generated stream + 17 values")


def test_gate_mode() -> None:
    print("\n=== Test 11: gate_mode note vs beat ===")
    port = FakePort()
    output = MPEOutput("IAC Driver Bus 5", output=port)
    engine = LeadEngine(LeadConfig())
    engine.on_set(RATIOS, 1.0, FUNDAMENTAL)
    engine.load_motif(
        (LeadNote(step=0, length=4, degree=0, octave=0, ratio_hint=1.0, velocity=90),),
        grid="1/8",
        phrase_bars=1,
    )
    scheduler = LeadScheduler(output, engine, bpm=300.0)

    def run(seconds: float) -> list[str]:
        port.messages.clear()
        scheduler.start()
        time.sleep(seconds)
        observed = port.types()
        scheduler.stop()
        return observed

    note_types = run(0.2)
    # 1/8 at 300 BPM = 0.1 s; a 4-step note gates for 0.4 s in note mode.
    assert "note_off" not in note_types, note_types

    engine.set_setting("gate_mode", "beat")
    beat_types = run(0.2)
    assert "note_off" in beat_types, beat_types
    print("  note length wins in note mode; the gate knob caps in beat mode")


# ---------------------------------------------------------------------------
# 10. Performance-state inline notes
# ---------------------------------------------------------------------------


def test_state_inline_notes() -> None:
    print("\n=== Test 12: inline played notes in performance states ===")
    notes = (
        LeadNote(step=0, length=1, degree=2, octave=0, ratio_hint=1.25, velocity=104),
        LeadNote(step=2, length=2, degree=4, octave=0, ratio_hint=1.5, velocity=90),
    )
    play = state_module.LeadPlaySection.create(
        notes=notes,
        base_notes=notes,
        history=(("invert", 1),),
        melody="intro line",
        source="played",
    )
    engine = _played_engine()
    snapshot = SetSnapshot.create(
        RATIOS, 1.0, tuple((ratio, 0) for ratio in RATIOS)
    )
    state = state_module.PerformanceState.create(
        "played-state",
        snapshot=snapshot,
        fundamental=FUNDAMENTAL,
        lead=LeadSettings.create(grid="1/8", phrase_bars=2),
        lead_variation_index=1,
        lead_play=play,
    )
    document = state.to_mapping()
    assert document["version"] == 1
    assert document["lead"]["source"] == "played"
    assert document["lead"]["melody"] == "intro line"
    assert document["lead"]["history"] == [["invert", 1]]
    back = state_module.PerformanceState.from_mapping(document)
    assert back.lead_play == play
    assert back.lead_variation_index == 1

    # An old document without the four keys loads unchanged as generated.
    old = state.to_mapping()
    for key in ("notes", "base_notes", "history", "melody", "source"):
        old["lead"].pop(key)
    loaded = state_module.PerformanceState.from_mapping(old)
    assert loaded.lead_play == state_module.LeadPlaySection()
    assert loaded.lead_play.source == "generated"
    assert loaded.to_mapping()["version"] == 1

    # A bad note aborts the whole load.
    bad = state.to_mapping()
    bad["lead"]["notes"][0]["velocity"] = 0
    try:
        state_module.PerformanceState.from_mapping(bad)
    except state_module.StateError:
        pass
    else:
        raise AssertionError("a bad inline note was accepted")

    # Bad source and out-of-phrase steps are refused.
    for mutate in ("source", "step"):
        broken = state.to_mapping()
        if mutate == "source":
            broken["lead"]["source"] = "sideways"
        else:
            broken["lead"]["notes"][0]["step"] = 999
        try:
            state_module.PerformanceState.from_mapping(broken)
        except state_module.StateError:
            pass
        else:
            raise AssertionError(f"bad {mutate} accepted")

    # Recall order: load the inline notes, replay the index, then Revert works.
    engine = LeadEngine(LeadConfig())
    engine.set_settings(state.lead)
    engine.load_motif(
        back.lead_play.notes,
        grid=state.lead.grid,
        phrase_bars=state.lead.phrase_bars,
        meter_num=state.rhythm.meter_num,
        meter_den=state.rhythm.meter_den,
        name=back.lead_play.melody,
        base_notes=back.lead_play.base_notes,
        history=back.lead_play.history,
        variation=state.lead.variation,
        variation_index=back.lead_variation_index,
    )
    engine.set_variation_index(back.lead_variation_index, force=True)
    reverted = engine.revert()
    assert reverted.notes == notes
    print("  round trip, old keys default, bad note aborts, recall then Revert")


# ---------------------------------------------------------------------------
# 11. No mido import
# ---------------------------------------------------------------------------


def test_bridge_melody_actions() -> None:
    print("\n=== Test 13b: bridge melody save/load/delete + Revert ===")
    notes = (
        LeadNote(step=0, length=2, degree=2, octave=0, ratio_hint=1.25, velocity=100),
        LeadNote(step=3, length=1, degree=4, octave=0, ratio_hint=1.5, velocity=90),
    )
    with tempfile.TemporaryDirectory() as directory:
        lead = _lead()
        lead.load_motif(notes, grid="1/8", phrase_bars=2)
        bridge = control.ji_bridge.JIBridge(
            listen_port=19120,
            ui_port=19121,
            sc_port=19122,
            melody_dir=directory,
        )
        try:
            bridge.midi = types.SimpleNamespace(lead=lead)
            bridge._on_lead_melody_save("intro line")
            assert (Path(directory) / "intro line.json").exists()
            assert lead.engine.motif.melody_name == "intro line"

            # A fresh lead (same bridge, different bus object) loads the file.
            other = _lead()
            bridge.midi = types.SimpleNamespace(lead=other)
            bridge._on_lead_melody_load("intro line")
            assert other.engine.motif.source == "played"
            assert other.engine.motif.notes == notes
            assert other.engine.settings.grid == "1/8"
            assert other.engine.settings.phrase_bars == 2
            assert other.engine.settings.gate_mode == "note"
            assert other.source == "played"

            # Variations compound; Revert restores the recorded take.
            other.set_setting("variation", "transpose")
            assert other.engine.motif.notes != notes
            other.revert()
            assert other.engine.motif.notes == notes

            bridge._on_lead_melody_delete("intro line")
            assert not (Path(directory) / "intro line.json").exists()
            assert bridge.melodies.count() == 0
        finally:
            bridge.quantizer.stop()
    print("  save -> fresh lead load -> vary -> Revert -> delete")


def test_no_mido_import() -> None:
    print("\n=== Test 13: no mido import at module import time ===")
    for module in (
        "control.lead_input",
        "control.melody",
        "control.lead",
        "control.ji_bridge",
        "ui.osc_control",
    ):
        assert module in sys.modules, module
    assert "mido" not in sys.modules, "mido was imported eagerly"
    print("  lead_input, melody, lead, ji_bridge and osc_control import cleanly")


def test_stage92_count_in_override_and_state() -> None:
    print("\n=== Test 14: Stage 9.2 count-in, override order, 16-value state ===")
    # The count-in is audible; a key held at the bar line is seeded at step 0.
    rig, lead, rhythm, fake = _input_rig(count_in="1 bar")
    rhythm._beat = 0.0
    assert rig.start_record() and rig.state == "count_in"
    output = lead.output._output
    fake.feed("note_on", note=61, velocity=108)
    assert output.with_type("note_on"), "the count-in must sound"
    rig._begin_recording()
    rhythm._beat = rig._start_beat + 4.0
    notes = rig.finish_recording()
    assert len(notes) == 1 and notes[0].step == 0
    assert notes[0].velocity == 108
    rig.stop()

    # Override call order: the first key calls override() before its bend and
    # the last key-up sends the note-off before release_override().
    class SpyScheduler:
        def __init__(self, output, log) -> None:
            self.output = output
            self.log = log
            self.is_running = False
            self.step_index = 3

        def override(self) -> None:
            self.log.append(("override", len(self.output.messages)))

        def release_override(self) -> None:
            self.log.append(("release_override", len(self.output.messages)))

        def clear_override(self) -> None:
            self.log.append(("clear_override", len(self.output.messages)))

        def cancel_expression(self) -> None:
            pass

        def start(self, at_time=None) -> None:
            pass

        def stop(self) -> None:
            pass

    rig, lead, _rhythm, _fake = _input_rig()
    log: list[tuple[str, int]] = []
    lead.scheduler = SpyScheduler(lead.output._output, log)
    lead._source = "generated"
    rig.note_on(61, 100)
    assert log and log[0][0] == "override" and log[0][1] == 0, log
    # Retriggering a held key must not override again.
    rig.note_on(64, 100)
    assert [entry[0] for entry in log] == ["override"], log
    rig.note_off(64)
    assert [entry[0] for entry in log] == ["override", "release_override"], log
    # The input note-off is already in the stream before release_override().
    last = lead.output._output.messages[-1]
    assert last.type == "note_off"
    # A mismatched key-up never releases the override.
    rig.note_on(61, 100)
    log.clear()
    rig.note_off(99)
    assert log == []
    rig.note_off(61)
    assert [entry[0] for entry in log] == ["release_override"]
    rig.stop()

    # /ji/lead/input/state is 16 values; 15/16 are the wheel and the monitor.
    rig, lead, _rhythm, _fake = _input_rig()
    args = rig.state_args()
    assert len(args) == 16, args
    assert args[14] == 0.0 and args[15] == 0
    rig.bend(4096)
    rig.mod(64)
    args = rig.state_args()
    assert abs(args[14] - 100.0) < 1e-9 and args[15] == 64, args
    # The GUI layer parses the live 16 values and the legacy 14.
    parsed = ui_osc_control.parse_lead_input_state(list(args))
    assert abs(parsed.last_bend_cents - 100.0) < 1e-9 and parsed.last_mod == 64
    legacy = ui_osc_control.parse_lead_input_state(list(args[:14]))
    assert legacy.last_bend_cents == 0.0 and legacy.last_mod == 0
    # ``live`` is the monitor flag (port open + the compatibility flag on),
    # not a source: it stays 1 whatever the lead is doing.
    assert args[0] == 1
    lead.set_source("played")
    assert rig.state_args()[9] == "played"
    lead.set_source("live")  # deprecated alias, mapped at the boundary
    assert rig.state_args()[9] in ("generated", "played")
    assert rig.state_args()[0] == 1
    assert rig.set_monitor(False) is False
    assert rig.state_args()[0] == 0
    rig.note_on(61, 100)
    assert not lead.output._output.with_type("note_on"), "monitor off must be silent"
    assert rig.set_monitor(True) is True
    rig.stop()

    # bend_range_semitones: shipped default, valid range, 0 disables, a bad
    # value disables only the input (config load raises and the bridge logs it).
    from control.lead_input import DEFAULT_INPUT_CONFIG_PATH, validate_bend_range

    assert input_module.load_config(DEFAULT_INPUT_CONFIG_PATH).bend_range_semitones == 2.0
    assert validate_bend_range(0) == 0.0
    assert validate_bend_range(48) == 48.0
    for bad in (-0.1, 48.1, float("nan")):
        try:
            validate_bend_range(bad)
        except ValueError:
            pass
        else:
            raise AssertionError(f"accepted bend range {bad!r}")
    try:
        _settings(bend_range_semitones=99)
    except ValueError:
        pass
    else:
        raise AssertionError("a bad bend range reached the settings")
    settings = _settings(bend_range_semitones=0.0)
    assert settings.bend_range_semitones == 0.0
    assert settings.with_change("bend_range_semitones", 12.0).bend_range_semitones == 12.0
    # Bend disabled: the wheel is reported but no bend is sent.
    rig, lead, _rhythm, _fake = _input_rig(bend_range_semitones=0.0)
    rig.bend(8191)
    rig.note_on(69, 100)
    assert all(message.pitch == 0 for message in lead.output._output.with_type("pitchwheel"))
    rig.stop()
    print("  audible count-in + seed, override order, 16 values, bend config")


def run() -> None:
    print("JI Stage 9.1 lead input tests")
    test_mapping_worked_values()
    test_quantizer()
    test_recorder()
    test_key_map()
    test_port_adapter()
    test_input_rig_notes()
    test_input_rig_sustain()
    test_record_state_machine()
    test_melody_library()
    test_engine_policy()
    test_gate_mode()
    test_state_inline_notes()
    test_bridge_melody_actions()
    test_stage92_count_in_override_and_state()
    test_no_mido_import()
    print("\nALL LEAD INPUT TESTS PASSED")


if __name__ == "__main__":
    try:
        run()
    except Exception:
        traceback.print_exc()
        print("\nLEAD INPUT TESTS FAILED")
        raise SystemExit(1)
