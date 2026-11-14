"""Stage 9.1 lead MIDI/QWERTY input, nearest-class mapping and recording.

This module is pure except for one **lazy** ``mido`` import inside
:class:`MidiInputPort`.  It owns five things:

* :class:`MappedNote` / :func:`map_note` -- the pitch-faithful mapping (the
  nearest frequency class of the current JI set, in the register played);
* :data:`KEY_SEMITONES` / :func:`key_midi` -- the QWERTY key map;
* :class:`MidiInputPort` -- one lazy mido input port (open/resolve/close);
* :class:`LeadRecorder` / :func:`quantize_step` -- the pure capture and
  swing-aware quantisation maths;
* :class:`LeadInputRig` -- the runtime: play on Bus 5, record against the
  bridge beat clock, load the take into :class:`control.lead.LeadEngine`.

Mapping maths (documented in ``docs/README_stage9_1.md``)::

    f_in  = 440 * 2 ** ((midi_note - 69) / 12)
    oct   = floor(log2(f_in / fundamental))
    f_oct = f_in / 2 ** oct
    d(r)  = 1200 * log2(f_oct / (fundamental * r))
    ratio = the ratio minimising |d(r)|
    f_out = fundamental * ratio * 2 ** oct
    degree = sorted_ratios.index(ratio)
    note.octave = oct - base_octave

A key exactly on a class returns that float (cents 0), so playback through
``LeadEngine._project_locked`` is bit-exact.  ``cycle``/``free``/``anchor``
mapping modes were considered and deliberately deferred (owner decision 1).
"""

from __future__ import annotations

import json
import math
import threading
import time
from collections.abc import Callable, Mapping, Sequence
from dataclasses import dataclass, replace
from pathlib import Path
from typing import Any

try:  # Support both ``python -m control.lead_input`` and a package import.
    from . import lead as lead_module
    from . import rhythm as rhythm_module
    from .lead import LeadNote
    from .mpe_output import frequency_to_pitch, midi_note_frequency
except ImportError:  # pragma: no cover - exercised only by direct execution
    import lead as lead_module  # type: ignore[no-redef]
    import rhythm as rhythm_module  # type: ignore[no-redef]
    from lead import LeadNote  # type: ignore[no-redef]
    from mpe_output import frequency_to_pitch, midi_note_frequency  # type: ignore[no-redef]

DEFAULT_INPUT_CONFIG_PATH = (
    Path(__file__).resolve().parent.parent / "config" / "lead_input_config.json"
)

INPUT_MATCHES: tuple[str, ...] = ("contains", "exact")
COUNT_IN_OPTIONS: tuple[str, ...] = ("off", "1 beat", "1 bar", "2 bars")
DEFAULT_COUNT_IN = "1 bar"
DEFAULT_QUANTIZE = 1.0
DEFAULT_VELOCITY_SCALE = 1.0
INPUT_BARS_MIN = 1
INPUT_BARS_MAX = 8
DEFAULT_INPUT_BARS = 2
VELOCITY_SCALE_MIN = 0.5
VELOCITY_SCALE_MAX = 2.0
OCTAVE_SHIFT_MIN = -2
OCTAVE_SHIFT_MAX = 2
DEFAULT_MIDI_VELOCITY = 100

#: The QWERTY key map: one octave of semitones above the base note.
KEY_SEMITONES: dict[str, int] = {
    "a": 0,
    "w": 1,
    "s": 2,
    "e": 3,
    "d": 4,
    "f": 5,
    "t": 6,
    "g": 7,
    "y": 8,
    "h": 9,
    "u": 10,
    "j": 11,
}
KEY_BASE_NOTE = 60
#: Minimum window for a flushed recording (protects a zero-length take).
MIN_RECORD_STEPS = 1
#: Poll interval of the record supervisor (never blocks for the whole take).
SUPERVISOR_SECONDS = 0.005
#: A take start this late relative to the intended bar line is moved on.
LATE_START_SECONDS = 0.05


# ---------------------------------------------------------------------------
# Validation helpers
# ---------------------------------------------------------------------------


def _finite(value: object, label: str) -> float:
    try:
        numeric = float(value)  # type: ignore[arg-type]
    except (TypeError, ValueError):
        raise TypeError(f"{label} must be a real number") from None
    if not math.isfinite(numeric):
        raise ValueError(f"{label} must be finite")
    return numeric


def _as_int(value: object, label: str) -> int:
    if isinstance(value, bool):
        raise TypeError(f"{label} must be an integer")
    return int(round(_finite(value, label)))


def validate_midi_note(value: object, label: str = "midi note") -> int:
    """Return an int 0..127 or raise ``TypeError``/``ValueError``."""

    note = _as_int(value, label)
    if not 0 <= note <= 127:
        raise ValueError(f"{label} must be between 0 and 127")
    return note


def validate_velocity(value: object, label: str = "velocity") -> int:
    """Return an int 0..127 (0 is a valid note-off velocity)."""

    velocity = _as_int(value, label)
    if not 0 <= velocity <= 127:
        raise ValueError(f"{label} must be between 0 and 127")
    return velocity


def validate_match(value: object) -> str:
    text = str(value).strip().lower()
    if text not in INPUT_MATCHES:
        raise ValueError("match must be 'contains' or 'exact'")
    return text


def validate_input_channel(value: object) -> int:
    channel = _as_int(value, "channel")
    if channel != 0 and not 1 <= channel <= 16:
        raise ValueError("channel must be 0 (omni) or 1..16")
    return channel


def validate_count_in(value: object) -> str:
    text = str(value).strip().lower()
    for option in COUNT_IN_OPTIONS:
        if option == text:
            return option
    raise ValueError(
        "count_in must be one of " + ", ".join(repr(item) for item in COUNT_IN_OPTIONS)
    )


def validate_input_bars(value: object) -> int:
    bars = _as_int(value, "bars")
    if not INPUT_BARS_MIN <= bars <= INPUT_BARS_MAX:
        raise ValueError(f"bars must be {INPUT_BARS_MIN}..{INPUT_BARS_MAX}")
    return bars


def validate_quantize(value: object) -> float:
    return max(0.0, min(1.0, _finite(value, "quantize")))


def validate_velocity_scale(value: object) -> float:
    return max(
        VELOCITY_SCALE_MIN,
        min(VELOCITY_SCALE_MAX, _finite(value, "velocity_scale")),
    )


def validate_octave_shift(value: object) -> int:
    return max(OCTAVE_SHIFT_MIN, min(OCTAVE_SHIFT_MAX, _as_int(value, "octave_shift")))


# ---------------------------------------------------------------------------
# Nearest-class mapping
# ---------------------------------------------------------------------------


@dataclass(frozen=True, slots=True)
class MappedNote:
    """One played key resolved against the current JI set."""

    midi_in: int
    frequency: float
    ratio: float
    degree: int
    octave: int
    cents: float


def map_note(
    midi_note: int,
    ratios: Sequence[float],
    fundamental: float,
    base_octave: int = 0,
) -> MappedNote:
    """Map a MIDI key to the nearest class of the current set (see module doc).

    Args:
        midi_note: The played key, 0..127.
        ratios: The sorted, positive, de-duplicated class list.  An empty or
            fully invalid sequence normalises to ``(1.0,)`` exactly like
            ``lead._clean_ratios``.
        fundamental: The set's fundamental in Hz (> 0, finite).
        base_octave: The lead's base octave; subtracted from the returned
            octave so projection reproduces the sounded frequency.

    Returns:
        A :class:`MappedNote`; ``cents`` is signed (positive = the key is sharp
        of the class).
    """

    note = validate_midi_note(midi_note)
    fund = _finite(fundamental, "fundamental")
    if fund <= 0.0:
        raise ValueError("fundamental must be strictly positive")
    if isinstance(base_octave, bool):
        raise TypeError("base_octave must be an integer")
    base = _as_int(base_octave, "base_octave")
    pool = lead_module._clean_ratios(ratios)

    f_in = midi_note_frequency(note)
    octave = int(math.floor(math.log2(f_in / fund)))
    f_oct = f_in / (2.0**octave)

    best_degree = 0
    best_ratio = pool[0]
    best_cents = 1200.0 * math.log2(f_oct / (fund * pool[0]))
    for index in range(1, len(pool)):
        ratio = pool[index]
        cents = 1200.0 * math.log2(f_oct / (fund * ratio))
        # Strict ``<`` keeps the lower ratio on the (real-just-ratio-impossible)
        # tie, exactly as documented.
        if abs(cents) < abs(best_cents):
            best_degree = index
            best_ratio = ratio
            best_cents = cents

    frequency = fund * best_ratio * (2.0**octave)
    return MappedNote(
        midi_in=note,
        frequency=frequency,
        ratio=best_ratio,
        degree=best_degree,
        octave=octave - base,
        cents=best_cents,
    )


def nearest_frequency(
    midi_note: int,
    ratios: Sequence[float],
    fundamental: float,
    base_octave: int = 0,
) -> float:
    """Convenience: just the sounding frequency of :func:`map_note`."""

    return map_note(midi_note, ratios, fundamental, base_octave).frequency


def key_midi(key: str, octave_shift: int = 0) -> int | None:
    """Return the MIDI note for a QWERTY key, or ``None`` when unmapped.

    ``z``/``x`` shift the octave by -1/+1 in the GUI; the shift is clamped to
    -2..2 and the resulting note is clamped so every key stays in 0..127.
    """

    semitones = KEY_SEMITONES.get(str(key).strip().lower())
    if semitones is None:
        return None
    shift = validate_octave_shift(octave_shift)
    value = KEY_BASE_NOTE + 12 * shift + semitones
    return max(0, min(127, value))


# ---------------------------------------------------------------------------
# Config
# ---------------------------------------------------------------------------


@dataclass(frozen=True, slots=True)
class LeadInputSettings:
    """Validated ``config/lead_input_config.json`` (read-only)."""

    port: str = ""
    match: str = "contains"
    channel: int = 0
    quantize: float = DEFAULT_QUANTIZE
    count_in: str = DEFAULT_COUNT_IN
    bars: int = DEFAULT_INPUT_BARS
    velocity_scale: float = DEFAULT_VELOCITY_SCALE
    octave_shift: int = 0

    def __post_init__(self) -> None:
        if not isinstance(self.port, str):
            raise ValueError("port must be text")
        validate_match(self.match)
        validate_input_channel(self.channel)
        validate_quantize(self.quantize)
        validate_count_in(self.count_in)
        validate_input_bars(self.bars)
        validate_velocity_scale(self.velocity_scale)
        validate_octave_shift(self.octave_shift)

    @classmethod
    def create(cls, **values: Any) -> "LeadInputSettings":
        defaults = cls()
        return cls(
            port=str(values.get("port", defaults.port)).strip(),
            match=validate_match(values.get("match", defaults.match)),
            channel=validate_input_channel(values.get("channel", defaults.channel)),
            quantize=validate_quantize(values.get("quantize", defaults.quantize)),
            count_in=validate_count_in(values.get("count_in", defaults.count_in)),
            bars=validate_input_bars(values.get("bars", defaults.bars)),
            velocity_scale=validate_velocity_scale(
                values.get("velocity_scale", defaults.velocity_scale)
            ),
            octave_shift=validate_octave_shift(
                values.get("octave_shift", defaults.octave_shift)
            ),
        )

    @classmethod
    def from_mapping(cls, data: Mapping[str, Any]) -> "LeadInputSettings":
        if not isinstance(data, Mapping):
            raise TypeError("lead input config must be a mapping")
        keys = (
            "port",
            "match",
            "channel",
            "quantize",
            "count_in",
            "bars",
            "velocity_scale",
            "octave_shift",
        )
        return cls.create(**{key: data[key] for key in keys if key in data})

    @classmethod
    def from_json(cls, path: str | Path) -> "LeadInputSettings":
        return load_config(path)

    def with_change(self, name: object, value: object) -> "LeadInputSettings":
        key = str(name).strip().lower()
        aliases = {
            "count-in": "count_in",
            "count in": "count_in",
            "velocity": "velocity_scale",
            "octave": "octave_shift",
        }
        key = aliases.get(key, key)
        if key not in {
            "port",
            "match",
            "channel",
            "quantize",
            "count_in",
            "bars",
            "velocity_scale",
            "octave_shift",
        }:
            raise ValueError(f"unknown lead input setting {name!r}")
        return replace(self, **{key: value})


def load_config(path: str | Path | None = None) -> LeadInputSettings:
    """Load and validate the lead input config (same fallback as the others)."""

    candidate = Path(path) if path is not None else DEFAULT_INPUT_CONFIG_PATH
    if not candidate.is_absolute() and not candidate.exists():
        project_candidate = Path(__file__).resolve().parent.parent / candidate
        if project_candidate.exists():
            candidate = project_candidate
    try:
        text = candidate.read_text(encoding="utf-8")
    except FileNotFoundError:
        raise FileNotFoundError(f"lead input config not found: {candidate}") from None
    try:
        data = json.loads(text)
    except json.JSONDecodeError as exc:
        raise ValueError(f"invalid JSON in {candidate}: {exc}") from exc
    return LeadInputSettings.from_mapping(data)


# ---------------------------------------------------------------------------
# Lazy mido input port
# ---------------------------------------------------------------------------


class MidiInputPort:
    """One lazy ``mido`` input port.

    A fake module (with ``get_input_names()`` and
    ``open_input(name, callback=...)``) can be injected as ``port`` for the
    pure tests, exactly like ``MPEOutput(output=...)``.  No ``mido`` import
    happens until :meth:`available`/:meth:`open` is called on a real port.
    """

    def __init__(
        self,
        port_name: str,
        *,
        match: str = "contains",
        channel: int = 0,
        callback: Callable[[str, int, int], None] | None = None,
        port: Any | None = None,
    ) -> None:
        if not isinstance(port_name, str):
            raise ValueError("port_name must be text")
        self.port_name = port_name.strip()
        self.match = validate_match(match)
        self.channel = validate_input_channel(channel)
        self.callback = callback
        self._fake = port
        self._module: Any | None = None
        self._input: Any | None = None
        self._lost = False
        self.resolved_name = ""

    # -- module / enumeration ------------------------------------------

    def _load_module(self) -> Any:
        if self._fake is not None:
            return self._fake
        if self._module is None:
            import mido  # intentionally lazy

            self._module = mido
        return self._module

    def available(self) -> tuple[str, ...]:
        """Return ``mido.get_input_names()`` (lazy), names as text."""

        module = self._load_module()
        try:
            names = list(module.get_input_names())
        except Exception as exc:
            raise RuntimeError(
                f"could not enumerate MIDI inputs; is python-rtmidi installed? "
                f"({exc})"
            ) from exc
        return tuple(str(name) for name in names)

    def resolve(self) -> str:
        """Return the single matching input name (never silently picks one)."""

        names = self.available()
        listed = ", ".join(repr(name) for name in names) or "(none)"
        if not self.port_name:
            raise ValueError(
                f"no input port configured; available: {listed}"
            )
        needle = self.port_name.casefold()
        if self.match == "exact":
            hits = [name for name in names if name.casefold() == needle]
        else:
            hits = [name for name in names if needle in name.casefold()]
        if not hits:
            raise ValueError(
                f"no input matched {self.port_name!r}; available: {listed}"
            )
        if len(hits) > 1:
            matched = ", ".join(repr(name) for name in hits)
            raise ValueError(
                f"{len(hits)} inputs matched {self.port_name!r}; use a more "
                f"specific name: {matched}"
            )
        return hits[0]

    # -- lifecycle ------------------------------------------------------

    def open(self) -> None:
        """Resolve and open the port, routing messages to :meth:`_on_message`."""

        if self._input is not None:
            return
        name = self.resolve()
        module = self._load_module()
        try:
            self._input = module.open_input(name, callback=self._on_message)
        except Exception as exc:
            raise RuntimeError(f"could not open input {name!r}: {exc}") from None
        self.resolved_name = name
        self._lost = False

    def close(self) -> None:
        """Close the port; safe to call more than once."""

        port = self._input
        self._input = None
        self._lost = False
        self.resolved_name = ""
        if port is None:
            return
        close = getattr(port, "close", None)
        if callable(close):
            try:
                close()
            except Exception as exc:  # pragma: no cover - hardware path
                print(f"[LEAD-IN] close failed: {exc}")

    @property
    def is_open(self) -> bool:
        return self._input is not None

    # -- message translation -------------------------------------------

    def _on_message(self, message: Any) -> None:
        """Translate one mido message; a lost device is one reported line."""

        if self._lost:
            return
        try:
            self._dispatch(message)
        except Exception as exc:  # a disappearing device must not kill the rig
            self._lost = True
            try:
                if self.callback is not None:
                    self.callback("lost", 0, 0)
            except Exception:
                pass
            print(
                f"[LEAD-IN] input lost: {exc}; the rest of the rig continues"
            )

    def _dispatch(self, message: Any) -> None:
        """Translate one mido message; runs on the reader thread (no I/O)."""

        kind = getattr(message, "type", None)
        if kind not in ("note_on", "note_off", "control_change"):
            return  # pitch bend, aftertouch, clock: ignored silently
        channel = getattr(message, "channel", 0)
        try:
            channel_number = int(channel) + 1
        except (TypeError, ValueError):
            return
        if self.channel and channel_number != self.channel:
            return
        if kind == "control_change":
            if int(getattr(message, "control", -1)) == 64:
                self._emit("sustain", 64, int(getattr(message, "value", 0)))
            return
        note = int(getattr(message, "note", -1))
        velocity = int(getattr(message, "velocity", 0))
        if kind == "note_on" and velocity > 0:
            self._emit("on", note, velocity)
        else:  # note_off, or note_on velocity 0
            self._emit("off", note, 0)

    def _emit(self, kind: str, midi: int, value: int) -> None:
        callback = self.callback
        if callback is None:
            return
        try:
            callback(kind, int(midi), int(value))
        except Exception as exc:  # the reader thread must never die
            print(f"[LEAD-IN] input callback failed: {exc!r}")


# ---------------------------------------------------------------------------
# Quantisation (pure)
# ---------------------------------------------------------------------------


def quantize_step(
    position_beats: float,
    start_beat: float,
    grid_quarters: float,
    strength: float,
    swing: float,
    step_seconds: float,
    steps_per_beat: int,
) -> int:
    """Quantise one beat position to a grid step on the **swung** grid.

    The stored step is always the *unswung* integer: playback re-applies
    :func:`control.bass.swing_offset`, so a take lands where it was played and
    still follows later groove changes.  ``strength`` 1 = exact grid, 0 = as
    played (still rounded to the nearest integer step).
    """

    grid = _finite(grid_quarters, "grid_quarters")
    if grid <= 0.0:
        raise ValueError("grid_quarters must be strictly positive")
    start = _finite(start_beat, "start_beat")
    position = _finite(position_beats, "position_beats")
    amount = max(0.0, min(1.0, _finite(strength, "strength")))
    step_length = _finite(step_seconds, "step_seconds")
    if step_length <= 0.0:
        raise ValueError("step_seconds must be strictly positive")

    s = (position - start) / grid
    k = round(s)
    swung = k + (
        lead_module.swing_offset(k, swing, step_length, steps_per_beat) / step_length
    )
    s_q = s + amount * (swung - s)
    return int(round(s_q))


# ---------------------------------------------------------------------------
# Recorder (pure)
# ---------------------------------------------------------------------------


class LeadRecorder:
    """Capture played notes and turn them into quantised ``LeadNote`` values.

    Monophonic reduction is fixed to ``last``: when a chord arrives, the earlier
    held note is dropped and counted in :attr:`dropped`.  A re-struck key closes
    the previous note at the new onset.
    """

    def __init__(
        self,
        mapper: Callable[[int, int], MappedNote] | None = None,
    ) -> None:
        self._mapper = mapper if mapper is not None else _default_mapper
        self._lock = threading.RLock()
        self._records: list[dict[str, Any]] = []
        self._active: dict[int, dict[str, Any]] = {}
        self._dropped = 0

    @property
    def dropped(self) -> int:
        with self._lock:
            return self._dropped

    @property
    def note_count(self) -> int:
        with self._lock:
            return len(self._records)

    def active_keys(self) -> tuple[int, ...]:
        with self._lock:
            return tuple(self._active)

    def reset(self) -> None:
        with self._lock:
            self._records = []
            self._active = {}
            self._dropped = 0

    def note_on(self, midi: int, velocity: int, beat: float) -> None:
        """Capture one note-on at the absolute (quarter-note) ``beat``."""

        note = validate_midi_note(midi)
        vel = validate_velocity(velocity)
        position = _finite(beat, "beat")
        mapped = self._mapper(note, max(1, vel))
        with self._lock:
            # Chord reduction: a new key drops every other held note (last wins
            # the single member channel).
            for held in list(self._active):
                record = self._active.pop(held)
                if held == note:
                    record["off"] = position  # retrigger: close at new onset
                else:
                    try:
                        self._records.remove(record)
                    except ValueError:  # pragma: no cover - defensive
                        pass
                    self._dropped += 1
            record = {
                "on": position,
                "off": None,
                "midi": note,
                "velocity": vel,
                "degree": mapped.degree,
                "octave": mapped.octave,
                "ratio_hint": mapped.ratio,
            }
            self._records.append(record)
            self._active[note] = record

    def note_off(self, midi: int, beat: float) -> None:
        """Close the held note for ``midi`` (a mismatched key is ignored)."""

        note = validate_midi_note(midi)
        position = _finite(beat, "beat")
        with self._lock:
            record = self._active.pop(note, None)
            if record is not None:
                record["off"] = position

    def finish(
        self,
        *,
        start_beat: float,
        grid: str,
        strength: float,
        swing: float,
        step_seconds: float,
        steps_per_beat: int,
        phrase_steps: int,
    ) -> tuple[LeadNote, ...]:
        """Return the quantised take and reset the capture."""

        grid_name = lead_module.validate_grid(grid)
        grid_quarters = lead_module.GRID_QUARTERS[grid_name]
        start = _finite(start_beat, "start_beat")
        total = max(MIN_RECORD_STEPS, int(phrase_steps))
        end = start + total * grid_quarters
        step_length = _finite(step_seconds, "step_seconds")
        subdivision = max(1, int(steps_per_beat))

        with self._lock:
            records = sorted(self._records, key=lambda item: item["on"])
            self._records = []
            self._active = {}
            dropped = self._dropped
            self._dropped = 0

        notes: list[LeadNote] = []
        for record in records:
            on_beat = float(record["on"])
            if not start - 1e-9 <= on_beat < end - 1e-9:
                continue
            off_beat = record["off"]
            off_beat = end if off_beat is None else float(off_beat)
            off_beat = min(max(off_beat, on_beat), end)
            on_step = quantize_step(
                on_beat,
                start,
                grid_quarters,
                strength,
                swing,
                step_length,
                subdivision,
            )
            off_step = quantize_step(
                off_beat,
                start,
                grid_quarters,
                strength,
                swing,
                step_length,
                subdivision,
            )
            step = max(0, min(total - 1, on_step))
            length = max(1, min(off_step - step, total - step))
            notes.append(
                LeadNote(
                    step=step,
                    length=length,
                    degree=int(record["degree"]),
                    octave=int(record["octave"]),
                    ratio_hint=float(record["ratio_hint"]),
                    velocity=int(record["velocity"]),
                )
            )
        notes.sort(key=lambda note: note.step)
        _ = dropped  # exposed through ``dropped`` before ``finish`` resets it
        return tuple(notes)


def _default_mapper(midi: int, velocity: int) -> MappedNote:
    """Mapping used when a recorder is built without a set (pure tests)."""

    return map_note(midi, (1.0,), 440.0)


# ---------------------------------------------------------------------------
# Input rig
# ---------------------------------------------------------------------------

_COUNT_IN_BEATS = {"off": 0.0, "1 beat": 1.0}


def count_in_beats(count_in: str, bar_quarters: float) -> float:
    """Whole quarter beats a count-in vocabulary item means (ceil bar rule)."""

    option = validate_count_in(count_in)
    bar = max(0.0, float(bar_quarters))
    whole_bar = float(math.ceil(bar - 1e-9)) if bar > 0.0 else 0.0
    if option == "off":
        return 0.0
    if option == "1 beat":
        return 1.0
    if option == "1 bar":
        return whole_bar
    return 2.0 * whole_bar


def record_window(
    now_beat: float,
    count_beats: float,
    bar_quarters: float,
    bars: int,
) -> tuple[float, float]:
    """Return ``(start_beat, end_beat)`` for a take requested at ``now_beat``."""

    bar = _finite(bar_quarters, "bar_quarters")
    if bar <= 0.0:
        raise ValueError("bar_quarters must be strictly positive")
    span = validate_input_bars(bars)
    start = math.ceil((float(now_beat) + max(0.0, float(count_beats))) / bar) * bar
    return float(start), float(start + span * bar)


class LeadInputRig:
    """Runtime: play keys onto Bus 5, record the take, load it as the motif."""

    def __init__(
        self,
        lead_rig: Any,
        rhythm: Any,
        settings: LeadInputSettings,
        *,
        port: Any | None = None,
        library: Any | None = None,
        now: Callable[[], float] = time.monotonic,
        on_change: Callable[[], None] | None = None,
        on_note: Callable[[float, int, int], None] | None = None,
    ) -> None:
        if isinstance(settings, Mapping):
            settings = LeadInputSettings.from_mapping(settings)
        if not isinstance(settings, LeadInputSettings):
            raise TypeError("settings must be a LeadInputSettings")
        if rhythm is None or not hasattr(rhythm, "clock"):
            raise TypeError("rhythm must expose beat_at/running/clock")
        if not callable(now):
            raise TypeError("now must be callable")
        self.lead = lead_rig
        self.rhythm = rhythm
        self.settings = settings
        self.library = library
        self._now = now
        self._on_change = on_change
        self._on_note = on_note
        self._lock = threading.RLock()
        if (
            port is not None
            and callable(getattr(port, "open", None))
            and callable(getattr(port, "close", None))
            and callable(getattr(port, "available", None))
        ):
            # An already-built port (a real MidiInputPort or a test double).
            self._port = port
            if hasattr(port, "callback") and getattr(port, "callback") is None:
                port.callback = self._on_message
        else:
            self._port = MidiInputPort(
                settings.port,
                match=settings.match,
                channel=settings.channel,
                callback=self._on_message,
                port=port,
            )
        self._port_ok = False
        self._port_name = ""
        self._held_midi: int | None = None
        self._held_note: int | None = None
        self._key_down = False
        self._sustain = False
        self._last_in: int | None = None
        self._last_out: int | None = None
        self._last_cents = 0.0
        self._last_status_key: tuple[float, int] | None = None
        self._state = "idle"  # idle | count_in | recording
        self._generation = 0
        self._start_beat = 0.0
        self._end_beat = 0.0
        self._count_beats = 0.0
        self._note_count = 0
        self._take_notes: tuple[LeadNote, ...] = ()
        self._recorder = LeadRecorder(mapper=self._map_for_record)
        self._count_in_dropped = 0
        self._thread: threading.Thread | None = None
        self._stop = threading.Event()
        self._live_pref = False
        self._running = False

    # -- status ---------------------------------------------------------

    @property
    def port_ok(self) -> bool:
        return self._port_ok

    @property
    def port_name(self) -> str:
        return self._port_name

    @property
    def live(self) -> bool:
        return bool(self._port_ok and self.lead.source in ("live", "record"))

    @property
    def recording(self) -> bool:
        with self._lock:
            return self._state in ("count_in", "recording")

    @property
    def state(self) -> str:
        with self._lock:
            return self._state

    @property
    def take_notes(self) -> tuple[LeadNote, ...]:
        return self._take_notes

    def state_args(self) -> list[Any]:
        """``/ji/lead/input/state`` -- fourteen values in the documented order."""

        with self._lock:
            state = self._state
            start_beat = self._start_beat
            recording = state in ("count_in", "recording")
        count = 0.0
        if state == "count_in":
            try:
                count = max(0.0, start_beat - float(self.rhythm.beat_at()))
            except Exception:
                count = 0.0
        try:
            source = self.lead.source
        except Exception:
            source = "generated"
        try:
            melody = self.lead.engine.motif.melody_name
        except Exception:
            melody = ""
        return [
            int(self.live),
            int(self._port_ok),
            self._port_name,
            float(self.settings.quantize),
            self.settings.count_in,
            int(self.settings.bars),
            int(recording),
            float(count),
            int(self._note_count),
            source,
            melody,
            int(-1 if self._last_in is None else self._last_in),
            int(-1 if self._last_out is None else self._last_out),
            float(self._last_cents),
        ]

    # -- lifecycle ------------------------------------------------------

    def start(self) -> bool:
        """Open the input port; a failure disables the input only."""

        if not self.lead.port_ok:
            print("[LEAD-IN] unavailable: start with --lead for Bus 5")
            return False
        self._running = True
        try:
            if not self.settings.port.strip():
                available = self._available_label()
                print(
                    "[LEAD-IN] disabled: no input port configured; "
                    f"available: {available}"
                )
                return False
            self._port.open()
        except (ValueError, RuntimeError, OSError) as exc:
            self._port_ok = False
            self._port_name = ""
            available = self._available_label()
            print(f"[LEAD-IN] disabled: {exc}; available: {available}")
            return False
        self._port_ok = True
        self._port_name = (
            getattr(self._port, "resolved_name", "") or self.settings.port
        )
        print(
            f"[LEAD-IN] input={self._port_name} channel={self.settings.channel} "
            f"match={self.settings.match} quantize={self.settings.quantize:.2f} "
            f"count-in={self.settings.count_in} bars={self.settings.bars}"
        )
        print("[LEAD-IN] notes sound as the nearest class of the current set (map=nearest)")
        return True

    def _available_label(self) -> str:
        try:
            names = self._port.available()
        except Exception:
            return "(unknown)"
        return ", ".join(repr(name) for name in names) or "(none)"

    def stop(self) -> None:
        """Release every note, cancel a take, and close the port."""

        self._running = False
        with self._lock:
            self._generation += 1
            self._state = "idle"
            self._count_beats = 0.0
        self._recorder.reset()
        self._release_all()
        try:
            self._port.close()
        finally:
            self._port_ok = False
            self._port_name = ""

    def refresh(self) -> bool:
        """Close and re-open the port (the bridge stays authoritative)."""

        try:
            self._port.close()
        except Exception:
            pass
        self._port_ok = False
        print("[LEAD-IN] refreshing the input port")
        return self.start()

    def release_all(self) -> None:
        """Release any held input note (source/Lead/enable exit paths)."""

        self._release_all()

    def _release_all(self) -> None:
        with self._lock:
            held = self._held_note
            self._held_note = None
            self._held_midi = None
            self._key_down = False
            self._sustain = False
        if held is not None:
            try:
                self.lead.output.note_off(int(held))
            except Exception:
                pass
        try:
            self.lead.output.all_notes_off()
        except Exception:
            pass

    # -- note entry (controller and QWERTY) -----------------------------

    def note_on(self, midi: int, velocity: int) -> None:
        note = validate_midi_note(midi)
        vel = validate_velocity(velocity)
        if not self.live:
            return  # the Live/Rec gate is the decided model
        with self._lock:
            if self._state == "count_in":
                return  # the count-in is silent and captures nothing
        engine = self.lead.engine
        base = engine.settings.base_octave
        mapped = map_note(note, engine.ratios, engine.fundamental, base)
        out_velocity = max(
            1,
            min(
                127,
                int(round(max(1, vel) * self.settings.velocity_scale)),
            ),
        )
        pitched = frequency_to_pitch(
            mapped.frequency, self.lead.config.bend_range_semitones
        )
        with self._lock:
            previous = self._held_note
            self._held_note = None
            self._held_midi = None
        if previous is not None:
            try:
                self.lead.output.note_off(int(previous))
            except Exception:
                pass
        try:
            self.lead.output.pitch_bend(pitched.bend)
            self.lead.output.note_on(pitched.note, out_velocity)
        except Exception as exc:
            print(f"[LEAD-IN] output failed: {exc}")
            return
        with self._lock:
            self._held_midi = note
            self._held_note = int(pitched.note)
            self._key_down = True
            self._last_in = note
            self._last_out = int(pitched.note)
            self._last_cents = float(mapped.cents)
            recording = self._state == "recording"
        if recording:
            self._recorder.note_on(note, out_velocity, self.rhythm.beat_at())
        self._notify_note(mapped.frequency, int(pitched.note), out_velocity)

    def note_off(self, midi: int) -> None:
        note = validate_midi_note(midi)
        beat = self.rhythm.beat_at()
        with self._lock:
            recording = self._state == "recording"
            is_held = self._held_midi == note
            if is_held:
                self._key_down = False
                defer = self._sustain
            else:
                defer = True
        if recording:
            self._recorder.note_off(note, beat)
        if not is_held or defer:
            return
        self._release_held()

    def sustain(self, down: bool) -> None:
        with self._lock:
            self._sustain = bool(down)
            release = (not self._sustain) and self._held_midi is not None and not self._key_down
        if release:
            self._release_held()

    def _release_held(self) -> None:
        with self._lock:
            held = self._held_note
            self._held_note = None
            self._held_midi = None
        if held is not None:
            try:
                self.lead.output.note_off(int(held))
            except Exception:
                pass

    def _map_for_record(self, midi: int, velocity: int) -> MappedNote:
        engine = self.lead.engine
        return map_note(
            midi, engine.ratios, engine.fundamental, engine.settings.base_octave
        )

    def _on_message(self, kind: str, midi: int, value: int) -> None:
        if not self._running:
            return
        if kind == "lost":
            self._release_all()
            self._notify_change()
            return
        if kind == "on":
            self.note_on(midi, value)
        elif kind == "off":
            self.note_off(midi)
        elif kind == "sustain":
            self.sustain(value >= 64)

    def set_note_callback(
        self, callback: Callable[[float, int, int], None] | None
    ) -> None:
        self._on_note = callback

    def set_change_callback(self, callback: Callable[[], None] | None) -> None:
        self._on_change = callback

    def _notify_note(self, hz: float, midi: int, velocity: int) -> None:
        callback = self._on_note
        if callback is None:
            return
        # Same dedupe as LeadRig.set_note_callback: one status per distinct note.
        key = (round(float(hz), 6), int(midi))
        if key == self._last_status_key:
            return
        self._last_status_key = key
        try:
            callback(float(hz), int(midi), int(velocity))
        except Exception as exc:
            print(f"[LEAD-IN] note callback failed: {exc!r}")

    def _notify_change(self) -> None:
        callback = self._on_change
        if callback is None:
            return
        try:
            callback()
        except Exception as exc:
            print(f"[LEAD-IN] change callback failed: {exc!r}")

    # -- settings -------------------------------------------------------

    def set_setting(self, name: object, value: object) -> LeadInputSettings:
        self.settings = self.settings.with_change(name, value)
        self._notify_change()
        return self.settings

    # -- recording ------------------------------------------------------

    def start_record(self) -> bool:
        """Request a take (count-in then a bar-aligned window)."""

        if not self.lead.port_ok or not self._port_ok:
            print("[LEAD-IN] record needs an open input (press Live and check the port)")
            return False
        if not self.rhythm.running:
            print("[LEAD-IN] record needs the transport (press Play)")
            return False
        bar_quarters = rhythm_module.bar_quarters(
            self.rhythm.state.meter_num, self.rhythm.state.meter_den
        )
        now_beat = float(self.rhythm.beat_at())
        with self._lock:
            if self._state == "count_in":
                # The loop convention: a second Rec skips the count-in and opens
                # the window at the next bar line.
                self._count_beats = 0.0
                self._start_beat, self._end_beat = record_window(
                    now_beat, 0.0, bar_quarters, self.settings.bars
                )
                print("[LEAD-IN] count-in skipped (second Rec)")
                skipped = True
            elif self._state == "recording":
                return False
            else:
                skipped = False
                count = count_in_beats(self.settings.count_in, bar_quarters)
                self._start_beat, self._end_beat = record_window(
                    now_beat, count, bar_quarters, self.settings.bars
                )
                self._count_beats = max(0.0, self._start_beat - now_beat)
                self._state = "count_in"
                self._generation += 1
                generation = self._generation
                self._note_count = 0
                self._take_notes = ()
                self._count_in_dropped = 0
                print(
                    f"[LEAD-IN] record requested (count-in "
                    f"{self.settings.count_in}, {self.settings.bars} bars) "
                    f"at beat {int(round(now_beat))}"
                )
                self._live_pref = True
                self._start_supervisor(generation)
                self._release_held()
                self.lead.set_source("record")
        if skipped:
            self._count_beats = 0.0
            self._notify_change()
            return True
        # A take requested exactly on a bar line with no count-in starts now.
        if self._count_beats <= 1e-9:
            self._begin_recording()
        else:
            self._notify_change()
        return True

    def _start_supervisor(self, generation: int) -> None:
        self._stop.clear()
        thread = threading.Thread(
            target=self._supervise,
            args=(generation,),
            name="ji-lead-input",
            daemon=True,
        )
        self._thread = thread
        thread.start()

    def _supervise(self, generation: int) -> None:
        while not self._stop.wait(SUPERVISOR_SECONDS):
            with self._lock:
                if generation != self._generation:
                    return
                state = self._state
            if state == "idle":
                return
            if not self.rhythm.running:
                self.transport_stopped()
                return
            now = self._now()
            if state == "count_in":
                try:
                    start_at = self.rhythm.clock.time_of_beat(self._start_beat)
                except Exception:
                    start_at = now
                if now >= start_at - 1e-6:
                    self._begin_recording()
            elif state == "recording":
                try:
                    end_at = self.rhythm.clock.time_of_beat(self._end_beat)
                except Exception:
                    end_at = now
                if now >= end_at - 1e-6:
                    self.finish_recording()
                    return

    def _begin_recording(self) -> None:
        with self._lock:
            if self._state != "count_in":
                return
            self._count_beats = 0.0
            self._state = "recording"
            self._recorder.reset()
            start = self._start_beat
            end = self._end_beat
        print(
            f"[LEAD-IN] recording started at beat {int(round(start))} "
            f"(window {int(round(start))}..{int(round(end))})"
        )
        self._notify_change()

    def stop_record(self, reason: str = "user") -> None:
        """Stop the take: cancel a count-in, or keep a partial recording."""

        with self._lock:
            state = self._state
        if state == "idle":
            return
        if state == "count_in":
            with self._lock:
                self._generation += 1
                self._state = "idle"
                self._count_beats = 0.0
            self._notify_change()
            return
        self.finish_recording(reason=reason)

    def finish_recording(
        self, *, aborted: bool = False, reason: str = "user"
    ) -> tuple[LeadNote, ...]:
        """Close the take, quantise, load it into the lead and play it back."""

        with self._lock:
            if self._state == "idle":
                return ()
            was_count_in = self._state == "count_in"
            self._generation += 1
            self._state = "idle"
            self._count_beats = 0.0
            start = self._start_beat
            end = self._end_beat
            generation = self._generation
        if was_count_in and not aborted:
            return ()  # Stop during count-in cancels silently
        _ = generation
        now_beat = float(self.rhythm.beat_at())
        for midi in self._recorder.active_keys():
            self._recorder.note_off(midi, now_beat)
        engine = self.lead.engine
        grid = engine.settings.grid
        bpm = self.rhythm.state.bpm
        grid_quarters = lead_module.GRID_QUARTERS[grid]
        step_seconds = (60.0 / bpm) * grid_quarters
        steps_per_beat = lead_module.steps_per_beat(grid)
        meter = self.rhythm.state
        phrase_steps = max(
            1,
            lead_module.steps_per_bar(meter.meter_num, meter.meter_den, grid)
            * int(self.settings.bars),
        )
        dropped = self._recorder.dropped
        notes = self._recorder.finish(
            start_beat=start,
            grid=grid,
            strength=self.settings.quantize,
            swing=engine.effective_swing(),
            step_seconds=step_seconds,
            steps_per_beat=steps_per_beat,
            phrase_steps=phrase_steps,
        )
        self._take_notes = notes
        self._note_count = len(notes)
        self._count_in_dropped = dropped

        if not notes:
            # Nothing was played inside the window: keep the current line and
            # report the empty take instead of installing a silent motif.
            print(
                f"[LEAD-IN] recorded 0 notes / {self.settings.bars} bars "
                f"(grid {grid}; nothing loaded)"
            )
            if aborted and self._live_pref:
                self.lead.set_source("live")
            else:
                self.lead.set_source("played")
            self._notify_change()
            return ()

        if aborted:
            print("[LEAD-IN] recording aborted (transport stop); take kept")
        self.lead.load_motif(
            notes,
            grid=grid,
            phrase_bars=int(self.settings.bars),
            meter_num=meter.meter_num,
            meter_den=meter.meter_den,
            name="",
            base_notes=notes,
        )
        print(
            f"[LEAD-IN] recorded {len(notes)} notes / {self.settings.bars} bars "
            f"(grid {grid}, quant {self.settings.quantize * 100:.0f} %, "
            f"{dropped} chord notes reduced)"
        )

        if self.rhythm.running and not aborted:
            target = self._playback_start_beat(now_beat, end)
            at_time = self.rhythm.clock.time_of_beat(target)
            self.lead.set_source("played", at_time=at_time)
        elif aborted:
            # A transport stop never restarts the sequencer (owner decision 2:
            # live keeps monitoring); the take is ready for the next Play.
            source = "live" if self._live_pref else "played"
            self.lead.set_source(source)
        else:
            self.lead.set_source("played")
        self._notify_change()
        return notes

    def _playback_start_beat(self, now_beat: float, end_beat: float) -> float:
        bar_quarters = rhythm_module.bar_quarters(
            self.rhythm.state.meter_num, self.rhythm.state.meter_den
        )
        bar = max(1e-9, float(bar_quarters))
        # The take ends on a bar line; start there unless it has already been
        # missed (then the next bar line, so the phrase re-enters at step 0).
        late = LATE_START_SECONDS * max(0.0, float(self.rhythm.state.bpm)) / 60.0
        if end_beat >= now_beat - late:
            return end_beat
        return math.ceil((now_beat + 1e-9) / bar) * bar

    def transport_stopped(self) -> None:
        """Bridge hook: a transport stop finishes an in-flight take (kept)."""

        with self._lock:
            state = self._state
            if state == "count_in":
                # A count-in is not a take: a transport stop cancels it.
                self._generation += 1
                self._state = "idle"
                self._count_beats = 0.0
                self._notify_change()
                return
        if state == "recording":
            self.finish_recording(aborted=True)


__all__ = [
    "COUNT_IN_OPTIONS",
    "DEFAULT_COUNT_IN",
    "DEFAULT_INPUT_BARS",
    "DEFAULT_INPUT_CONFIG_PATH",
    "DEFAULT_MIDI_VELOCITY",
    "DEFAULT_QUANTIZE",
    "DEFAULT_VELOCITY_SCALE",
    "INPUT_BARS_MAX",
    "INPUT_BARS_MIN",
    "INPUT_MATCHES",
    "KEY_BASE_NOTE",
    "KEY_SEMITONES",
    "LeadInputRig",
    "LeadInputSettings",
    "LeadRecorder",
    "MappedNote",
    "MidiInputPort",
    "OCTAVE_SHIFT_MAX",
    "OCTAVE_SHIFT_MIN",
    "SUPERVISOR_SECONDS",
    "VELOCITY_SCALE_MAX",
    "VELOCITY_SCALE_MIN",
    "count_in_beats",
    "key_midi",
    "load_config",
    "map_note",
    "nearest_frequency",
    "quantize_step",
    "record_window",
    "validate_count_in",
    "validate_input_bars",
    "validate_input_channel",
    "validate_match",
    "validate_midi_note",
    "validate_octave_shift",
    "validate_quantize",
    "validate_velocity",
    "validate_velocity_scale",
]
