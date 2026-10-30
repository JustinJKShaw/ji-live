"""Low-level MPE pitch-bend / note output for the Stage 6 MIDI rig.

This module deliberately imports :mod:`mido` **lazily**.  The pure test harness
(``test_mpe.py --pure``) and the existing Stage 2/3.5 harnesses must keep
working on machines where ``mido`` is not installed.

Design notes
------------
Two independent IAC buses (drone and arp) each carry a **monophonic** MPE
stream.  One member channel is therefore enough per bus; both buses use MIDI
channel 2 (mido channel index 1).  The MPE master channel (MIDI channel 1) is
never used for note data.

A note is expressed as the nearest equal-tempered MIDI note plus a 14-bit
pitch-bend offset.  Ableton's MPE pitch-bend range is 48 semitones in this rig;
the value is still configurable because a mismatch silently detunes everything.

For testability, an :class:`MPEOutput` can be constructed with a fake output
object.  The fake receives small :class:`MPEMessage` values; the real mido port
is wrapped in an adapter that converts those values to ``mido.Message``
objects.  This keeps the pure tests free of any MIDI dependency.
"""

from __future__ import annotations

import math
from dataclasses import dataclass
from numbers import Real
from typing import Any

DEFAULT_BEND_RANGE_SEMITONES = 48.0
DEFAULT_MEMBER_CHANNEL = 2
BEND_CENTER_14 = 8192
BEND_MAX_14 = 16383
BEND_MIN_CENTERED = -8192
BEND_MAX_CENTERED = 8191


@dataclass(frozen=True, slots=True)
class MPEPitch:
    """The MIDI note and bend needed to sound one frequency.

    Attributes:
        note: MIDI note number in ``0..127`` after any whole-octave transpose.
        bend14: Raw 14-bit bend in ``0..16383`` (8192 = centered).
        bend: Mido-style centered bend in ``-8192..8191``.
        target_frequency: The requested frequency in Hz.
        adjusted_frequency: ``target_frequency`` transposed by whole octaves so
            that ``note`` fits the MIDI range.
        octave_shift: Number of whole octaves added to the target (positive =
            up, negative = down).
        bend_range_semitones: The bend range used for the calculation.
    """

    note: int
    bend14: int
    bend: int
    target_frequency: float
    adjusted_frequency: float
    octave_shift: int
    bend_range_semitones: float

    @property
    def sounding_frequency(self) -> float:
        """Frequency the receiving synth should produce for this message pair."""

        semitones = self.bend / BEND_CENTER_14 * self.bend_range_semitones
        return midi_note_frequency(self.note) * (2.0 ** (semitones / 12.0))

    @property
    def error_cents(self) -> float:
        """Signed tuning error of ``sounding_frequency`` vs the adjusted target."""

        return 1200.0 * math.log2(
            self.sounding_frequency / self.adjusted_frequency
        )


@dataclass(frozen=True, slots=True)
class MPEMessage:
    """A transport-independent MIDI message used by the fake output path."""

    type: str
    channel: int
    note: int | None = None
    velocity: int | None = None
    pitch: int | None = None
    control: int | None = None
    value: int | None = None


def midi_note_frequency(note: int) -> float:
    """Return the 12-TET frequency of a MIDI note (A4 = 440 Hz)."""

    if isinstance(note, bool) or not isinstance(note, int):
        raise TypeError("note must be an integer")
    if not 0 <= note <= 127:
        raise ValueError("note must be between 0 and 127")
    return 440.0 * (2.0 ** ((note - 69) / 12.0))


def frequency_to_pitch(
    frequency: float,
    bend_range_semitones: float = DEFAULT_BEND_RANGE_SEMITONES,
) -> MPEPitch:
    """Convert a frequency in Hz to an MPE ``(note, bend)`` pair.

    Formula (design doc §6.11)::

        n = round(69 + 12 * log2(f / 440))
        d = 12 * log2(f / mtof(n))
        bend14 = 8192 + round(d / R * 8192)

    If ``n`` falls outside ``0..127`` the frequency is transposed by whole
    octaves first.  ``bend14`` is clamped to ``0..16383``.  With the default
    ``R = 48`` the nearest-note deviation is at most half a semitone, so a
    valid note is always reachable and the clamp is only a safety net.
    """

    freq = _positive_float(frequency, "frequency")
    bend_range = _positive_float(
        bend_range_semitones, "bend_range_semitones"
    )

    note = int(round(69.0 + 12.0 * math.log2(freq / 440.0)))
    octave_shift = 0
    while note < 0:
        note += 12
        freq *= 2.0
        octave_shift += 1
    while note > 127:
        note -= 12
        freq *= 0.5
        octave_shift -= 1

    # Defensive: the loops above leave note in range for all finite f > 0.
    note = max(0, min(127, note))
    deviation = 12.0 * math.log2(freq / midi_note_frequency(note))
    bend14 = BEND_CENTER_14 + int(
        round(deviation / bend_range * BEND_CENTER_14)
    )
    bend14 = max(0, min(BEND_MAX_14, bend14))

    return MPEPitch(
        note=note,
        bend14=bend14,
        bend=bend14 - BEND_CENTER_14,
        target_frequency=float(frequency),
        adjusted_frequency=freq,
        octave_shift=octave_shift,
        bend_range_semitones=bend_range,
    )


def frequency_to_note_bend(
    frequency: float,
    bend_range_semitones: float = DEFAULT_BEND_RANGE_SEMITONES,
) -> tuple[int, int]:
    """Convenience wrapper returning ``(note, bend14)``."""

    pitch = frequency_to_pitch(frequency, bend_range_semitones)
    return pitch.note, pitch.bend14


# Short alias used by the design-doc snippets and by manual calibration code.
freq_to_mpe = frequency_to_note_bend


class MPEOutput:
    """One monophonic MPE output port.

    Args:
        port_name: Exact MIDI output name, e.g. ``"IAC Driver Bus 1"``.
        member_channel: 1-based MIDI member channel (default 2).  Mido receives
            ``member_channel - 1``.
        bend_range_semitones: Must match the receiving instrument's MPE bend
            range.  Default 48.
        velocity: Default note-on velocity for :meth:`play`.
        output: Optional fake/duck-typed output with ``send(message)``.  When
            supplied, ``mido`` is never imported.  The fake receives
            :class:`MPEMessage` values.
        label: Short label used in error messages.
    """

    def __init__(
        self,
        port_name: str,
        member_channel: int = DEFAULT_MEMBER_CHANNEL,
        bend_range_semitones: float = DEFAULT_BEND_RANGE_SEMITONES,
        velocity: int = 90,
        output: Any | None = None,
        label: str = "MPE",
    ) -> None:
        if not isinstance(port_name, str) or not port_name.strip():
            raise ValueError("port_name must be a non-empty string")
        if isinstance(member_channel, bool) or not isinstance(member_channel, int):
            raise TypeError("member_channel must be an integer")
        if not 2 <= member_channel <= 16:
            raise ValueError(
                "member_channel must be a 1-based MIDI channel in 2..16 "
                "(channel 1 is the MPE master channel)"
            )
        bend_range = _positive_float(
            bend_range_semitones, "bend_range_semitones"
        )
        velocity = _midi_value(velocity, "velocity", allow_zero=False)

        self.port_name = port_name
        self.member_channel = member_channel
        self.channel = member_channel - 1  # mido / MPEMessage use 0-based
        self.bend_range_semitones = bend_range
        self.velocity = velocity
        self.label = label

        self._output = output
        self._opened = output is not None
        self._real = False
        self._mido: Any | None = None

    # ------------------------------------------------------------------
    # Port lifecycle
    # ------------------------------------------------------------------

    def open(self) -> None:
        """Open the real mido port, or no-op when a fake output was supplied.

        Raises:
            RuntimeError: mido is missing, the port cannot be enumerated, or
                the exact configured port name is not present.  The available
                names are included so a typo/missing IAC bus is obvious.
        """

        if self._opened:
            return

        try:
            import mido  # intentionally lazy
        except ImportError as exc:  # pragma: no cover - hardware path
            raise RuntimeError(
                "mido is required for MIDI output. Run with: "
                "uv run --with mido --with python-rtmidi python ..."
            ) from exc

        try:
            names = list(mido.get_output_names())
        except Exception as exc:  # pragma: no cover - hardware path
            raise RuntimeError(
                "could not enumerate MIDI outputs; is python-rtmidi installed? "
                f"({exc})"
            ) from exc

        if self.port_name not in names:
            available = ", ".join(repr(name) for name in names) or "(none)"
            raise RuntimeError(
                f"{self.label} MIDI output {self.port_name!r} not found. "
                f"Available MIDI outputs: {available}. "
                "Check Audio MIDI Setup -> IAC Driver and the config file."
            )

        try:
            port = mido.open_output(self.port_name)
        except Exception as exc:  # pragma: no cover - hardware path
            raise RuntimeError(
                f"could not open {self.label} MIDI output "
                f"{self.port_name!r}: {exc}"
            ) from exc

        self._mido = mido
        self._output = _MidoOutputAdapter(mido, port)
        self._real = True
        self._opened = True

    def close(self) -> None:
        """Close the port.  Safe to call more than once."""

        output = self._output
        self._output = None
        self._opened = False
        self._real = False
        if output is None:
            return
        close = getattr(output, "close", None)
        if callable(close):
            close()

    @property
    def is_open(self) -> bool:
        return self._opened

    # ------------------------------------------------------------------
    # Messages
    # ------------------------------------------------------------------

    def pitch_bend(self, pitch: int) -> None:
        """Send a centered 14-bit pitch bend (mido range ``-8192..8191``)."""

        value = _centered_bend(pitch)
        self._send(MPEMessage(type="pitchwheel", channel=self.channel, pitch=value))

    def note_on(self, note: int, velocity: int | None = None) -> None:
        """Send a note-on on the member channel."""

        note = _midi_value(note, "note")
        vel = self.velocity if velocity is None else _midi_value(
            velocity, "velocity", allow_zero=True
        )
        self._send(
            MPEMessage(
                type="note_on",
                channel=self.channel,
                note=note,
                velocity=vel,
            )
        )

    def note_off(self, note: int, velocity: int = 0) -> None:
        """Send a note-off on the member channel."""

        note = _midi_value(note, "note")
        vel = _midi_value(velocity, "velocity", allow_zero=True)
        self._send(
            MPEMessage(
                type="note_off",
                channel=self.channel,
                note=note,
                velocity=vel,
            )
        )

    def control_change(self, control: int, value: int) -> None:
        """Send a control change on the member channel."""

        control = _midi_value(control, "control")
        value = _midi_value(value, "value", allow_zero=True)
        self._send(
            MPEMessage(
                type="control_change",
                channel=self.channel,
                control=control,
                value=value,
            )
        )

    def all_notes_off(self) -> None:
        """Send CC 123 (All Notes Off) on the member channel."""

        self.control_change(123, 0)

    def retune(self, frequency: float) -> MPEPitch:
        """Pitch-bend an already sounding note to ``frequency``.

        Returns the pitch calculation so callers can log/inspect it.  This does
        not send a note-on; callers that need a new note use :meth:`play`.
        """

        pitch = frequency_to_pitch(frequency, self.bend_range_semitones)
        self.pitch_bend(pitch.bend)
        return pitch

    def play(self, frequency: float, velocity: int | None = None) -> MPEPitch:
        """Send pitch bend *then* note-on, so the first sample is in tune."""

        pitch = frequency_to_pitch(frequency, self.bend_range_semitones)
        self.pitch_bend(pitch.bend)
        self.note_on(pitch.note, velocity)
        return pitch

    # Convenience aliases for callers that prefer explicit message names.
    send_pitch_bend = pitch_bend
    send_note_on = note_on
    send_note_off = note_off

    # ------------------------------------------------------------------
    # Internals
    # ------------------------------------------------------------------

    def _send(self, message: MPEMessage) -> None:
        if not self._opened or self._output is None:
            raise RuntimeError(
                f"{self.label} MIDI output {self.port_name!r} is not open"
            )
        self._output.send(message)


class _MidoOutputAdapter:
    """Translate :class:`MPEMessage` into ``mido.Message`` for a real port."""

    def __init__(self, mido_module: Any, port: Any) -> None:
        self._mido = mido_module
        self._port = port

    def send(self, message: MPEMessage) -> None:
        if message.type == "pitchwheel":
            outgoing = self._mido.Message(
                "pitchwheel",
                channel=message.channel,
                pitch=message.pitch,
            )
        elif message.type in ("note_on", "note_off"):
            outgoing = self._mido.Message(
                message.type,
                channel=message.channel,
                note=message.note,
                velocity=message.velocity,
            )
        elif message.type == "control_change":
            outgoing = self._mido.Message(
                "control_change",
                channel=message.channel,
                control=message.control,
                value=message.value,
            )
        else:  # pragma: no cover - only the four message types above are used
            raise ValueError(f"unsupported MIDI message type {message.type!r}")
        self._port.send(outgoing)

    def close(self) -> None:
        self._port.close()


def _positive_float(value: Real, label: str) -> float:
    if isinstance(value, bool) or not isinstance(value, Real):
        raise TypeError(f"{label} must be a real number")
    try:
        number = float(value)
    except (TypeError, ValueError, OverflowError) as exc:
        raise TypeError(f"{label} must be a real number") from exc
    if not math.isfinite(number) or number <= 0.0:
        raise ValueError(f"{label} must be finite and strictly positive")
    return number


def _midi_value(value: Real, label: str, allow_zero: bool = True) -> int:
    if isinstance(value, bool) or not isinstance(value, Real):
        raise TypeError(f"{label} must be a real number")
    try:
        number = float(value)
    except (TypeError, ValueError, OverflowError) as exc:
        raise TypeError(f"{label} must be a real number") from exc
    if not math.isfinite(number):
        raise ValueError(f"{label} must be finite")
    rounded = int(round(number))
    low = 0 if allow_zero else 1
    if not low <= rounded <= 127:
        raise ValueError(f"{label} must be between {low} and 127")
    return rounded


def _centered_bend(value: Real) -> int:
    if isinstance(value, bool) or not isinstance(value, Real):
        raise TypeError("pitch must be a real number")
    try:
        number = float(value)
    except (TypeError, ValueError, OverflowError) as exc:
        raise TypeError("pitch must be a real number") from exc
    if not math.isfinite(number):
        raise ValueError("pitch must be finite")
    rounded = int(round(number))
    if not BEND_MIN_CENTERED <= rounded <= BEND_MAX_CENTERED:
        raise ValueError("pitch must be between -8192 and 8191")
    return rounded
