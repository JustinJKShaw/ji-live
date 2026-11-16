#!/usr/bin/env python3
"""Stage 9.2 pure tests: input override, audible count-in, bend/mod expression.

Covers the new surface only (9.1 is in ``tests/test_lead_input.py``):

* :func:`control.mpe_output.bend_for_frequency` -- the note-number-relative
  14-bit bend maths (including a bend that crosses a semitone);
* the lazy input port's ``pitchwheel`` / CC 1 translation and channel filter;
* the input rig's bend/mod output order and wheel memory;
* :class:`control.lead_input.LeadRecorder` expression capture;
* note validation and the melody/state inline round trip;
* scheduler expression playback (order, gate/next/stop cancellation);
* :class:`control.lead.LeadScheduler` ``override`` / ``release_override``;
* the audible count-in and the held-note seed.

Run from ``ji-live/``::

    uv run --with python-osc python tests/test_lead_expression.py

No mido, PyQt6, sockets, audio or real MIDI ports.  Fake outputs/ports and a
controllable clock keep the checks deterministic; the scheduler/expression
checks use short real-time windows like the existing lead harness.
"""

from __future__ import annotations

import contextlib
import io
import json
import math
import sys
import time
import traceback
import types
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

import control.state as state_module
from control.lead import (
    LeadConfig,
    LeadEngine,
    LeadNote,
    LeadRig,
    normalize_bend_points,
    normalize_mod_points,
)
from control.lead_input import (
    LeadInputRig,
    LeadInputSettings,
    LeadRecorder,
    MidiInputPort,
)
from control.melody import Melody
from control.mpe_output import (
    MPEMessage,
    bend_for_frequency,
    frequency_to_pitch,
    midi_note_frequency,
)
from control.rhythm import RhythmState

RATIOS = (1.0, 9.0 / 8.0, 5.0 / 4.0, 4.0 / 3.0, 3.0 / 2.0, 5.0 / 3.0, 15.0 / 8.0)
FUNDAMENTAL = 220.0


# ---------------------------------------------------------------------------
# Fakes
# ---------------------------------------------------------------------------


class FakePort:
    """Duck-typed MPE output capturing messages and their times."""

    def __init__(self) -> None:
        self.messages: list = []
        self.times: list[float] = []
        self.closed = False

    def send(self, message: MPEMessage) -> None:
        self.messages.append(message)
        self.times.append(time.monotonic())

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

    def bends(self) -> list[int]:
        return [message.pitch for message in self.with_type("pitchwheel")]

    def mods(self) -> list[int]:
        return [
            message.value
            for message in self.with_type("control_change")
            if message.control == 1
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
    def __init__(self) -> None:
        self.base = time.monotonic()

    def time_of_beat(self, beat: float) -> float:
        # Far in the future so the supervisor never advances on its own.
        return self.base + 100.0 + float(beat) * 0.5


class FakeRhythm:
    """Minimal RhythmEngine surface used by the input rig."""

    def __init__(self, *, running: bool = True, bpm: float = 120.0) -> None:
        self.running = running
        self.bpm = bpm
        self.meter_num = 4
        self.meter_den = 4
        self._beat = 0.0
        self.state = RhythmState.create(bpm, 4, 4, running)
        self.clock = FakeClock()

    def beat_at(self, at=None) -> float:
        return self._beat

    def set_running(self, running: bool) -> RhythmState:
        self.running = bool(running)
        self.state = self.state.with_running(self.running)
        return self.state


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
        "bend_range_semitones": 2.0,
    }
    values.update(overrides)
    return LeadInputSettings.create(**values)


def _lead(output=None, **config) -> LeadRig:
    data = {
        "port": "IAC Driver Bus 5",
        "member_channel": 2,
        "bend_range_semitones": 48,
        "enabled": True,
        "grid": "1/8",
        "phrase_bars": 2,
        "density": 1.0,
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
    data.update(config)
    rig = LeadRig(
        LeadConfig.from_mapping(data),
        output=FakePort() if output is None else output,
        bpm=float(data.get("bpm", 240.0)),
    )
    rig.engine.on_set(RATIOS, 1.0, FUNDAMENTAL)
    return rig


def _input_rig(*, now=None, on_change=None, **overrides):
    lead = _lead()
    rhythm = FakeRhythm()
    fake = FakeMido()
    kwargs = {} if now is None else {"now": now}
    if on_change is not None:
        kwargs["on_change"] = on_change
    rig = LeadInputRig(lead, rhythm, _settings(**overrides), port=fake, **kwargs)
    assert rig.start()
    return rig, lead, rhythm, fake


def _wait_for(predicate, timeout: float = 2.0) -> bool:
    deadline = time.monotonic() + timeout
    while time.monotonic() < deadline:
        if predicate():
            return True
        time.sleep(0.005)
    return False


def _sounding_hz(note: int, bend: int, bend_range: float) -> float:
    return midi_note_frequency(note) * (
        2.0 ** ((bend / 8192.0 * bend_range) / 12.0)
    )


# ---------------------------------------------------------------------------
# 1. Bend maths
# ---------------------------------------------------------------------------


def test_bend_maths() -> None:
    print("\n=== Test 1: bend_for_frequency (note-number-relative 14-bit bend) ===")
    for note, bend_range in ((60, 48.0), (61, 2.0), (69, 48.0), (40, 12.0)):
        assert bend_for_frequency(note, midi_note_frequency(note), bend_range) == 0

    # A full-range value at each edge of the helper's own semitone maths.
    up_half = midi_note_frequency(60) * 2.0 ** (0.5 / 12.0)
    assert bend_for_frequency(60, up_half, 48.0) == round(0.5 / 48.0 * 8192)
    down_half = midi_note_frequency(60) * 2.0 ** (-0.5 / 12.0)
    assert bend_for_frequency(60, down_half, 48.0) == round(
        -0.5 / 48.0 * 8192
    )
    # Controller ranges 2 and 48 (0 is rejected: 0 disables bend in the rig).
    assert bend_for_frequency(60, midi_note_frequency(60) * 2 ** (0.25 / 12), 2.0) == 1024
    assert (
        bend_for_frequency(60, midi_note_frequency(60) * 2 ** (12.0 / 12), 48.0)
        == 2048
    )
    for bad_range in (0.0, -1.0):
        try:
            bend_for_frequency(60, 440.0, bad_range)
        except ValueError:
            pass
        else:
            raise AssertionError(f"accepted a bend range of {bad_range}")

    # Clamping: a target far outside the range never leaves -8192..8191.
    assert bend_for_frequency(60, midi_note_frequency(60) * 2.0, 2.0) == 8191
    assert bend_for_frequency(60, midi_note_frequency(60) / 2.0, 2.0) == -8192

    # Bit-exact with the 9.1 path: a class key with a centred wheel.
    for frequency in (220.0, 220.0 * 5.0 / 4.0, 440.0 * 15.0 / 8.0, 275.0):
        pitch = frequency_to_pitch(frequency, 48.0)
        assert bend_for_frequency(pitch.note, frequency, 48.0) == pitch.bend, (
            frequency,
            pitch,
        )

    # A bend that crosses a semitone keeps the original note number: N=61 with
    # a +1.5 st target sounds exactly that frequency and is never note 63.
    target = midi_note_frequency(61) * 2.0 ** (1.5 / 12.0)
    bend = bend_for_frequency(61, target, 2.0)
    assert bend == round(1.5 / 2.0 * 8192)
    assert abs(_sounding_hz(61, bend, 2.0) - target) < 1e-9
    assert 61 != int(round(69 + 12 * math.log2(target / 440.0)))
    print(
        "  0/half/full/range 2/48, clamping, 9.1 bit-exact, semitone crossing"
    )


# ---------------------------------------------------------------------------
# 2. Port translation
# ---------------------------------------------------------------------------


def test_port_translation() -> None:
    print("\n=== Test 2: pitchwheel / CC 1 translation and the channel filter ===")
    fake = FakeMido(("Launchkey 49",))
    events: list[tuple[str, int, int]] = []
    port = MidiInputPort("Launchkey", callback=lambda *a: events.append(a), port=fake)
    port.open()
    fake.feed("pitchwheel", pitch=-4096)
    fake.feed("control_change", control=1, value=127)
    fake.feed("control_change", control=64, value=127)
    fake.feed("note_on", note=60, velocity=100)
    fake.feed("note_on", note=60, velocity=0)
    fake.feed("control_change", control=74, value=99)
    fake.feed("aftertouch", value=20)
    assert events == [
        ("bend", -4096, 0),
        ("mod", 1, 127),
        ("sustain", 64, 127),
        ("on", 60, 100),
        ("off", 60, 0),
    ], events

    filtered: list[tuple[str, int, int]] = []
    fake2 = FakeMido()
    channel_port = MidiInputPort(
        "Launchkey", channel=2, callback=lambda *a: filtered.append(a), port=fake2
    )
    channel_port.open()
    fake2.feed("pitchwheel", pitch=100, channel=0)
    fake2.feed("pitchwheel", pitch=100, channel=1)
    fake2.feed("control_change", control=1, value=50, channel=0)
    fake2.feed("control_change", control=1, value=50, channel=1)
    fake2.feed("note_on", note=61, velocity=100, channel=1)
    assert filtered == [
        ("bend", 100, 0),
        ("mod", 1, 50),
        ("on", 61, 100),
    ], filtered
    print("  bend/mod/sustain/notes translated, other channel + CC ignored")


# ---------------------------------------------------------------------------
# 3. Input rig expression
# ---------------------------------------------------------------------------


def test_input_rig_expression() -> None:
    print("\n=== Test 3: input rig bend/mod output, order and wheel memory ===")
    rig, lead, _rhythm, _fake = _input_rig()
    output = lead.output._output

    # No note: the wheel is stored and reported, nothing is sent.
    rig.bend(-4096)
    assert output.messages == []
    assert abs(rig.state_args()[14] - (-100.0)) < 1e-9, rig.state_args()
    assert rig.state_args()[15] == 0

    # The same values travel through the port translation (the bend value is
    # carried in the event's second slot).
    rig2, lead2, _rhythm2, fake2 = _input_rig()
    output2 = lead2.output._output
    fake2.feed("pitchwheel", pitch=2048)
    assert abs(rig2.state_args()[14] - 50.0) < 1e-9, rig2.state_args()
    assert output2.messages == []
    fake2.feed("control_change", control=1, value=99)
    assert output2.mods() == [99], output2.mods()
    fake2.feed("note_on", note=64, velocity=100)
    assert abs(output2.with_type("pitchwheel")[-1].pitch) > 0
    before2 = len(output2.messages)
    fake2.feed("pitchwheel", pitch=-2048)
    assert len(output2.messages) == before2 + 1
    assert output2.messages[-1].type == "pitchwheel"
    assert output2.messages[-1].pitch < 0
    rig2.stop()

    # Bend (relative to the chosen class), then mod, then the note-on.
    rig.mod(64)
    rig.note_on(69, 100)
    kinds = output.types()
    assert kinds[-3:] == ["pitchwheel", "control_change", "note_on"], kinds
    assert output.with_type("control_change")[-1].control == 1
    assert output.with_type("control_change")[-1].value == 64
    held_bend = output.with_type("pitchwheel")[-1].pitch
    # MIDI 69 on F=220 is the octave class (440 Hz, N=69), so -100 cents is
    # -100/4800 * 8192 with the 48 st output range.
    assert held_bend == round(-100.0 / 4800.0 * 8192), held_bend

    # A bend while held sends only the pitch wheel (no note-on, no CC 1).
    before = list(output.messages)
    rig.bend(4096)
    assert len(output.messages) == len(before) + 1
    assert output.messages[-1].type == "pitchwheel"
    assert output.messages[-1].pitch > 0

    # Release keeps the physical wheel: the next note starts bent again.
    rig.note_off(69)
    assert output.messages[-1].type == "note_off"
    rig.note_on(69, 100)
    assert output.with_type("pitchwheel")[-1].pitch > 0
    rig.note_off(69)
    rig.stop()

    # Mod is forwarded while the loop sounds (it is channel state) and is
    # coalesced to ~5 ms, never one message per event.  A controllable clock
    # makes the throttle window exact.
    clock = {"now": 100.0}
    rig, lead, _rhythm, fake = _input_rig(now=lambda: clock["now"])
    output = lead.output._output
    rig.mod(0)
    assert output.mods() == [0]
    clock["now"] += 0.001
    image = len(output.messages)
    rig.mod(10)  # inside the 5 ms window: deferred, not sent
    assert len(output.messages) == image
    clock["now"] += 0.001
    rig.mod(20)  # still throttled; coalesced with 10
    assert len(output.messages) == image
    rig.mod(30)
    time.sleep(0.03)  # the single trailing timer flushes the *latest* value
    mods = output.mods()
    assert mods == [0, 30], mods
    # Duplicate values are dropped.
    clock["now"] += 1.0
    rig.mod(30)
    time.sleep(0.02)
    assert output.mods() == [0, 30]
    rig.stop()

    # The wheel path leaves a one-shot diagnostic trail (never per event), so
    # a silent wheel can be triaged from the bridge log.
    rig, lead, _rhythm, fake = _input_rig()
    buffer = io.StringIO()
    with contextlib.redirect_stdout(buffer):
        fake.feed("pitchwheel", pitch=-2048)
        fake.feed("pitchwheel", pitch=-1024)
        fake.feed("control_change", control=1, value=64)
        fake.feed("pitchwheel", pitch=1024)  # still no note held
        fake.feed("note_on", note=64, velocity=100)
        fake.feed("pitchwheel", pitch=2048)
        fake.feed("pitchwheel", pitch=4096)
        fake.feed("note_off", note=64)
        fake.feed("note_on", note=62, velocity=100)
        fake.feed("pitchwheel", pitch=4096)
    text = buffer.getvalue()
    assert text.count("wheel: pitch bend") == 1, text
    assert text.count("wheel: CC 1 (mod) 64 received") == 1, text
    assert text.count("no note held") == 1, text
    assert "sent pitch bend" in text, text
    # The sent line states the configured range and the 2 st interpretation.
    assert "lead bend_range=48 st" in text, text
    assert "if the instrument is 2 st" in text, text
    # One "sent" line per note gesture, not per event.
    assert text.count("sent pitch bend") == 2, text
    # A full wheel is +341 of the 14-bit bend: +2 st at 48 st, +8.3 ¢ at 2 st.
    assert bend_for_frequency(64, midi_note_frequency(64) * 2.0 ** (2.0 / 12.0), 48.0) == 341
    assert abs(341 / 8192.0 * 48.0 * 100.0 - 199.8) < 0.1
    assert abs(341 / 8192.0 * 2.0 * 100.0 - 8.33) < 0.01
    rig.stop()

    # Status pushes are coalesced (~10 Hz), never one GUI message per event.
    changes: list[int] = []
    clock = {"now": 200.0}
    rig, lead, _rhythm, fake = _input_rig(
        now=lambda: clock["now"], on_change=lambda: changes.append(1)
    )
    for index in range(20):
        clock["now"] += 0.001
        fake.feed("pitchwheel", pitch=index * 100)
    assert len(changes) == 1, changes  # the first push, the rest coalesced
    time.sleep(0.15)
    assert len(changes) == 2, changes  # one trailing push with the latest value
    clock["now"] += 1.0
    rig.mod(10)
    assert len(changes) == 3, changes
    rig.stop()

    # A bad value never reaches the wire.
    rig, lead, _rhythm, _fake = _input_rig()
    for bad in (8192, -8193, True):
        try:
            rig.bend(bad)
        except (TypeError, ValueError):
            pass
        else:
            raise AssertionError(f"bend accepted {bad!r}")
    for bad in (128, -1, True):
        try:
            rig.mod(bad)
        except (TypeError, ValueError):
            pass
        else:
            raise AssertionError(f"mod accepted {bad!r}")
    rig.stop()
    print("  bend/mod/note order, bend-only while held, wheel memory, throttle")


# ---------------------------------------------------------------------------
# 4. Recorder expression capture
# ---------------------------------------------------------------------------


def _finish(recorder: LeadRecorder, *, steps: int = 8, start: float = 0.0):
    return recorder.finish(
        start_beat=start,
        grid="1/8",
        strength=1.0,
        swing=0.0,
        step_seconds=0.25,
        steps_per_beat=2,
        phrase_steps=steps,
    )


def test_recorder_expression() -> None:
    print("\n=== Test 4: recorder bend/mod curves ===")
    recorder = LeadRecorder()
    recorder.note_on(60, 100, 0.0, bend_cents=-12.0, mod=64)
    recorder.expression(0.25, bend_cents=-24.0)
    recorder.expression(0.25, bend_cents=-24.0)  # duplicate value coalesced
    recorder.expression(0.5, mod=80)
    recorder.note_off(60, 1.0)
    notes = _finish(recorder)
    assert len(notes) == 1
    note = notes[0]
    assert note.bend_points == ((0.0, -12.0), (0.25, -24.0)), note.bend_points
    assert note.mod_points == ((0.0, 64), (0.5, 80)), note.mod_points

    # A note that starts with a centred wheel seeds nothing.
    recorder = LeadRecorder()
    recorder.note_on(62, 100, 0.0)
    recorder.expression(0.25, bend_cents=5.0)
    recorder.note_off(62, 0.5)
    note = _finish(recorder)[0]
    assert note.bend_points == ((0.25, 5.0),)
    assert note.mod_points == ()

    # Expression with no note (or after its end) is ignored.
    recorder = LeadRecorder()
    recorder.expression(1.0, bend_cents=10.0)
    assert _finish(recorder) == ()
    recorder = LeadRecorder()
    recorder.note_on(60, 100, 0.0)
    recorder.note_off(60, 0.5)
    recorder.expression(0.75, bend_cents=10.0)
    assert _finish(recorder)[0].bend_points == ()

    # Points after the note's off are dropped; a held note ends at the window.
    recorder = LeadRecorder()
    recorder.note_on(60, 100, 0.0)
    recorder.expression(0.5, bend_cents=10.0)
    recorder.note_off(60, 0.75)
    note = _finish(recorder)[0]
    assert note.bend_points == ((0.5, 10.0),)
    recorder = LeadRecorder()
    recorder.note_on(60, 100, 0.0)
    recorder.expression(3.9, bend_cents=10.0)
    note = _finish(recorder)[0]
    assert note.length == 8 and note.bend_points == ((3.9, 10.0),)

    # Retrigger: the previous note closes and starts a fresh curve.
    recorder = LeadRecorder()
    recorder.note_on(60, 100, 0.0)
    recorder.expression(0.5, bend_cents=10.0)
    recorder.note_on(60, 90, 1.0, bend_cents=20.0)
    recorder.expression(1.25, bend_cents=30.0)
    recorder.note_off(60, 2.0)
    notes = _finish(recorder)
    assert len(notes) == 2, notes
    assert notes[0].bend_points == ((0.0, 0.0),) or notes[0].bend_points == (
        (0.5, 10.0),
    )
    assert notes[1].bend_points == ((0.0, 20.0), (0.25, 30.0)), notes[1]

    # Chord reduction keeps only the last note and its points.
    recorder = LeadRecorder()
    recorder.note_on(60, 100, 0.0, bend_cents=10.0)
    recorder.note_on(64, 100, 0.5, bend_cents=20.0)
    recorder.note_off(64, 1.0)
    notes = _finish(recorder)
    assert len(notes) == 1 and notes[0].degree is not None
    assert notes[0].bend_points == ((0.0, 20.0),)

    # Cap: at most 256 points per curve (later points dropped).
    recorder = LeadRecorder()
    recorder.note_on(60, 100, 0.0)
    for index in range(400):
        recorder.expression(index * 0.005, bend_cents=float(index))
    recorder.note_off(60, 2.0)
    note = _finish(recorder)[0]
    assert len(note.bend_points) == 256, len(note.bend_points)
    assert note.bend_points[0] == (0.0, 0.0)
    assert note.bend_points[-1][0] < 2.0

    # An empty take stays empty.
    assert _finish(LeadRecorder()) == ()
    print("  seeding, coalescing, clamp to the note, retrigger, chord, cap 256")


# ---------------------------------------------------------------------------
# 5. Validation and serialisation
# ---------------------------------------------------------------------------


def _note(**overrides) -> LeadNote:
    values = dict(
        step=0,
        length=2,
        degree=2,
        octave=0,
        ratio_hint=1.25,
        velocity=104,
        bend_points=((0.0, -12.0), (0.3125, 0.0)),
        mod_points=((0.0, 64), (0.5, 127)),
    )
    values.update(overrides)
    return LeadNote(**values)


def test_validation_serialisation() -> None:
    print("\n=== Test 5: note validation and the melody/state round trip ===")
    validated = LeadEngine._validate_notes((_note(),), 16)[0]
    assert validated.bend_points == ((0.0, -12.0), (0.3125, 0.0))
    assert validated.mod_points == ((0.0, 64), (0.5, 127))

    # Cents are clamped to +/-4800, duplicates last-wins, values coalesced.
    clamped = normalize_bend_points([(0.0, 9000.0), (1.0, -9000.0)])
    assert clamped == ((0.0, 4800.0), (1.0, -4800.0)), clamped
    assert normalize_bend_points([(0.0, 1.0), (0.5, 1.0), (0.5, 2.0)]) == (
        (0.0, 1.0),
        (0.5, 2.0),
    )
    assert normalize_mod_points([(0.0, 3), (0.25, 3), (0.5, 4)]) == (
        (0.0, 3),
        (0.5, 4),
    )

    # Bad curves are rejected by the shared validator (engine, melody, state).
    for bad in (
        [(-1.0, 10.0)],
        [(0.5, 10.0), (0.25, 10.0)],
        [(float("nan"), 10.0)],
        [(0.0, float("inf"))],
        "nope",
        [(0.0,)],
    ):
        try:
            LeadEngine._validate_notes((_note(bend_points=bad),), 16)
        except (TypeError, ValueError):
            pass
        else:
            raise AssertionError(f"accepted bend_points {bad!r}")
    for bad in ([(0.0, 128)], [(-1.0, 0)], [(0.0, -3)], "nope"):
        try:
            LeadEngine._validate_notes((_note(mod_points=bad),), 16)
        except (TypeError, ValueError):
            pass
        else:
            raise AssertionError(f"accepted mod_points {bad!r}")

    # Melody JSON round trip; old files (no keys) load with empty tuples.
    melody = Melody(
        name="expression take",
        grid="1/8",
        phrase_bars=2,
        meter_num=4,
        meter_den=4,
        bpm=120.0,
        quantize=1.0,
        swing=0.0,
        notes=(_note(),),
        base_notes=(_note(),),
        history=(),
        variation="identity",
        variation_index=0,
        ratios=RATIOS,
        fundamental=FUNDAMENTAL,
    )
    document = json.loads(melody.to_json())
    assert document["version"] == 1
    assert document["notes"][0]["bend_points"] == [[0.0, -12.0], [0.3125, 0.0]]
    assert document["notes"][0]["mod_points"] == [[0.0, 64], [0.5, 127]]
    back = Melody.from_mapping(document)
    assert back.notes == melody.notes
    old = melody.to_mapping()
    for key in ("bend_points", "mod_points"):
        old["notes"][0].pop(key)
        old["base_notes"][0].pop(key)
    restored = Melody.from_mapping(old)
    assert restored.notes[0].bend_points == ()
    assert restored.notes[0].mod_points == ()

    # Inline performance-state section round trip (the state's lead.notes).
    section = state_module.LeadPlaySection.create(
        notes=[_note()], base_notes=[_note()], source="played"
    )
    inline = section.as_dict()
    assert inline["notes"][0]["bend_points"] == [[0.0, -12.0], [0.3125, 0.0]]
    assert state_module.LeadPlaySection.from_mapping(inline) == section
    old_inline = json.loads(json.dumps(inline))
    for key in ("bend_points", "mod_points"):
        old_inline["notes"][0].pop(key)
        old_inline["base_notes"][0].pop(key)
    legacy = state_module.LeadPlaySection.from_mapping(old_inline)
    assert legacy.notes[0].bend_points == ()
    # A bad point aborts the whole section.
    broken = json.loads(json.dumps(inline))
    broken["notes"][0]["bend_points"] = [[0.5, 0.0], [0.25, 1.0]]
    try:
        state_module.LeadPlaySection.from_mapping(broken)
    except state_module.StateError:
        pass
    else:
        raise AssertionError("an unsorted state curve was accepted")
    print("  validate/clamp/reject, melody + state round trip, old files empty")


# ---------------------------------------------------------------------------
# 6. Scheduler expression playback
# ---------------------------------------------------------------------------


def _played_lead(notes, *, gate_beats: float = 0.5) -> tuple[LeadRig, FakePort]:
    fake = FakePort()
    rig = LeadRig(
        LeadConfig.from_mapping(
            {
                "port": "IAC Driver Bus 5",
                "member_channel": 2,
                "bend_range_semitones": 48,
                "enabled": True,
                "grid": "1/8",
                "phrase_bars": 2,
                "density": 1.0,
                "gate_beats": gate_beats,
                "velocity": 90,
                "octave_range": 1,
                "base_octave": 0,
                "variation": "identity",
                "variation_bars": 0,
                "swing": 0.0,
                "groove_link": True,
                "seed": 1,
            }
        ),
        output=fake,
        bpm=240.0,
    )
    rig.engine.on_set(RATIOS, 1.0, FUNDAMENTAL)
    rig.load_motif(
        notes, grid="1/8", phrase_bars=2, meter_num=4, meter_den=4
    )
    return rig, fake


def test_scheduler_expression() -> None:
    print("\n=== Test 6: scheduler expression playback ===")
    # One long note with a bend and a mod curve (offsets in beats: at 240 BPM a
    # 1/8 step is 0.125 s, so 0.25 beats = 62.5 ms).
    note = LeadNote(
        step=0,
        length=16,
        degree=0,
        octave=0,
        ratio_hint=1.0,
        velocity=100,
        bend_points=((0.0, -100.0), (0.25, 100.0)),
        mod_points=((0.5, 64),),
    )
    rig, fake = _played_lead([note])
    rig.set_transport(True)
    try:
        assert _wait_for(lambda: 64 in fake.mods(), timeout=2.0), fake.messages
    finally:
        rig.set_transport(False)
    bends = fake.bends()
    assert len(bends) >= 3, bends
    # Base bend, then the offset-0 bend, then the 0.25-beat bend.
    expected_low = round(-100.0 / 4800.0 * 8192)
    expected_high = round(100.0 / 4800.0 * 8192)
    assert expected_low in bends and expected_high in bends, bends
    order = [
        (message.type, getattr(message, "pitch", None), message.value)
        for message in fake.messages
        if message.type in ("pitchwheel", "note_on", "control_change")
        and message.control in (None, 1)
    ]
    kinds = [item[0] for item in order]
    # CC 1 is stated before the note-on (the note has mod points) ...
    assert kinds[0] == "pitchwheel" and kinds[1] == "control_change", order
    assert kinds[2] == "note_on", order
    # ... and the recorded mod point lands after both curve bends.
    first_on = kinds.index("note_on")
    assert kinds.index("control_change", first_on) > kinds.index(
        "pitchwheel", first_on
    ), order
    assert order.index(("pitchwheel", expected_high, None)) < order.index(
        ("control_change", None, 64)
    ), order

    # A gate that closes early cancels the rest of the curve.
    early = LeadNote(
        step=0,
        length=1,
        degree=0,
        octave=0,
        ratio_hint=1.0,
        velocity=100,
        bend_points=((1.0, 100.0),),
    )
    rig, fake = _played_lead([early], gate_beats=0.1)
    rig.set_transport(True)
    try:
        assert _wait_for(lambda: "note_off" in fake.types(), timeout=2.0)
        time.sleep(0.2)
    finally:
        rig.set_transport(False)
    # Only the note's own base bend (a class note = 0) was sent, no curve bend.
    assert set(fake.bends()) <= {0}, fake.bends()

    # The next note cancels the previous curve.
    later = LeadNote(
        step=0,
        length=1,
        degree=0,
        octave=0,
        ratio_hint=1.0,
        velocity=100,
        bend_points=((2.0, 100.0),),
    )
    second = LeadNote(
        step=1, length=1, degree=0, octave=0, ratio_hint=1.0, velocity=100
    )
    rig, fake = _played_lead([later, second])
    rig.set_transport(True)
    try:
        assert _wait_for(
            lambda: len(fake.with_type("note_on")) >= 2, timeout=2.0
        )
        time.sleep(0.1)
    finally:
        rig.set_transport(False)
    assert round(100.0 / 4800.0 * 8192) not in fake.bends(), fake.bends()

    # stop() cancels a pending curve.
    rig, fake = _played_lead(
        [
            LeadNote(
                step=0,
                length=16,
                degree=0,
                octave=0,
                ratio_hint=1.0,
                velocity=100,
                bend_points=((1.0, 100.0),),
            )
        ]
    )
    rig.set_transport(True)
    assert _wait_for(lambda: len(fake.with_type("note_on")) >= 1, timeout=2.0)
    rig.set_transport(False)
    count = len(fake.messages)
    time.sleep(0.2)
    assert len(fake.messages) == count, fake.messages[count:]

    # A generated line with no points adds no CC 1 and no extra bend.
    rig, fake = _played_lead([])
    rig.clear_melody()
    rig.set_transport(True)
    try:
        assert _wait_for(
            lambda: len(fake.with_type("note_on")) >= 4, timeout=2.0
        )
    finally:
        rig.set_transport(False)
    assert fake.mods() == [], fake.mods()
    # Exactly one bend before each note-on (the generated 9.1 stream).
    kinds = fake.types()
    for position, kind in enumerate(kinds):
        if kind == "note_on":
            assert kinds[position - 1] == "pitchwheel", kinds[position - 3 : position]
    print("  bend order, mod after the note, gate/next/stop cancels, no points")


# ---------------------------------------------------------------------------
# 7. Override
# ---------------------------------------------------------------------------


def _override_rig(gate_notes) -> tuple[LeadRig, FakePort]:
    return _played_lead(gate_notes)


def test_override() -> None:
    print("\n=== Test 7: LeadScheduler override / release_override ===")
    long_note = LeadNote(
        step=0,
        length=16,
        degree=0,
        octave=0,
        ratio_hint=1.0,
        velocity=100,
    )
    rig, fake = _override_rig([long_note])
    scheduler = rig.scheduler
    rig.set_transport(True)
    try:
        assert _wait_for(
            lambda: len(fake.with_type("note_on")) >= 1, timeout=2.0
        )
        assert not scheduler.overridden
        first_note = fake.with_type("note_on")[0].note
        scheduler.override()
        assert scheduler.overridden
        # The loop's sounding note is released immediately.
        assert fake.messages[-1].type == "note_off", fake.messages[-3:]
        assert fake.messages[-1].note == first_note
        assert scheduler.is_running  # never stopped, never reset
        suppress_from = len(fake.messages)
        time.sleep(0.3)
        assert len(fake.messages) == suppress_from, fake.messages[suppress_from:]
        # Release with the gate still open re-attacks the same step.
        scheduler.release_override()
        assert not scheduler.overridden
        attacks = [
            message for message in fake.messages[suppress_from:]
            if message.type == "note_on"
        ]
        assert len(attacks) == 1 and attacks[0].note == first_note, attacks
        # A duplicate release never double-attacks.
        count = len(fake.with_type("note_on"))
        scheduler.release_override()
        assert len(fake.with_type("note_on")) == count
        # A retriggered override then a duplicate override() is harmless.
        scheduler.override()
        scheduler.override()
        assert scheduler.overridden
        scheduler.release_override()
        # The loop kept advancing while overridden (same one-note phrase, so no
        # new attack until the next phrase; the short-note case below proves a
        # later step enters again).
        step_before = scheduler.step_index
        time.sleep(0.3)
        assert scheduler.step_index != step_before
        assert scheduler.is_running
    finally:
        rig.set_transport(False)
    assert not scheduler.overridden
    rig.stop()

    # A gate that has already elapsed is not re-attacked; the next step enters.
    short = LeadNote(
        step=0, length=1, degree=0, octave=0, ratio_hint=1.0, velocity=100
    )
    rig, fake = _override_rig([short] * 4)
    scheduler = rig.scheduler
    rig.set_transport(True)
    try:
        assert _wait_for(
            lambda: len(fake.with_type("note_on")) >= 1, timeout=2.0
        )
        scheduler.override()
        # No step plays while overridden, so after more than one gate
        # (0.5 beats at 240 BPM = 62.5 ms) the last note's window has closed.
        time.sleep(0.15)
        attacks = len(fake.with_type("note_on"))
        scheduler.release_override()
        assert len(fake.with_type("note_on")) == attacks
        assert _wait_for(
            lambda: len(fake.with_type("note_on")) > attacks, timeout=2.0
        )
    finally:
        rig.set_transport(False)
    rig.stop()

    # stop() clears the override; override while stopped releases the note.
    rig, fake = _override_rig([long_note])
    scheduler = rig.scheduler
    scheduler.override()  # not running: no-op apart from the flag
    assert scheduler.overridden
    scheduler.stop()
    assert not scheduler.overridden
    rig.stop()
    print("  note-off + silence, resume attack, no double attack, stop clears")


# ---------------------------------------------------------------------------
# 8. Count-in
# ---------------------------------------------------------------------------


def test_count_in() -> None:
    print("\n=== Test 8: audible count-in and the held-note seed ===")
    # A key during the count-in sounds but is not captured when it ends there.
    rig, lead, rhythm, fake = _input_rig(count_in="1 bar")
    rhythm._beat = 0.0
    assert rig.start_record()
    assert rig.state == "count_in"
    output = lead.output._output
    fake.feed("note_on", note=61, velocity=110)
    assert output.with_type("note_on"), "the count-in must be audible"
    fake.feed("note_off", note=61)
    rig._begin_recording()
    assert rig.state == "recording"
    notes = rig.finish_recording()
    assert notes == () and rig.take_notes == ()
    rig.stop()

    # A key held across the bar line is in the take from step 0, velocity kept.
    rig, lead, rhythm, fake = _input_rig(count_in="1 bar")
    rhythm._beat = 0.0
    assert rig.start_record()
    fake.feed("note_on", note=61, velocity=110)
    rig._begin_recording()
    assert rig._recorder.active_keys() == (61,)
    rhythm._beat = rig._start_beat + 4.0
    notes = rig.finish_recording()
    assert len(notes) == 1, notes
    assert notes[0].step == 0
    assert notes[0].velocity == 110, notes[0].velocity
    assert notes[0].degree == lead.engine.motif.notes[0].degree
    rig.stop()

    # Count-in cancellation is still silent (and releases nothing extra).
    rig, lead, rhythm, fake = _input_rig(count_in="1 bar")
    rhythm._beat = 1.0
    assert rig.start_record()
    buffer = io.StringIO()
    with contextlib.redirect_stdout(buffer):
        rig.stop_record("user")
    assert rig.state == "idle" and rig.take_notes == ()
    assert "recorded" not in buffer.getvalue()
    rig.stop()
    print("  audible + unstored, held note seeded at step 0, silent cancel")


# ---------------------------------------------------------------------------
# 9. Lazy mido
# ---------------------------------------------------------------------------


def test_no_mido_import() -> None:
    print("\n=== Test 9: no mido at import time ===")
    assert "mido" not in sys.modules, "mido was imported by the pure modules"
    print("  lead, lead_input, melody, state and the port stay mido-free")


# ---------------------------------------------------------------------------
# Main
# ---------------------------------------------------------------------------


def run() -> None:
    print("JI Stage 9.2 lead override + expression tests")
    test_bend_maths()
    test_port_translation()
    test_input_rig_expression()
    test_recorder_expression()
    test_validation_serialisation()
    test_scheduler_expression()
    test_override()
    test_count_in()
    test_no_mido_import()
    print("\nALL LEAD EXPRESSION TESTS PASSED")


if __name__ == "__main__":
    try:
        run()
    except Exception:
        traceback.print_exc()
        print("\nLEAD EXPRESSION TESTS FAILED")
        raise SystemExit(1)
