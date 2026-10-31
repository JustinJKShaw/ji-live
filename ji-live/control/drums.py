"""Stage 8.1/8.2 drum engine: note map, generated groove, lanes, scheduler.

The drum voice is a new slow-tempo-independent MIDI output, not an MPE voice.
It sends plain note-on/note-off messages on one MIDI channel (default 10, mido
index 9) on ``IAC Driver Bus 3``.  :mod:`mido` is imported **lazily**, only when
a real port is opened; a fake output can be injected for the pure tests.

Semantics (design doc §6.14):

* 16 lanes, default MIDI notes 36-51 (C1 = 36 in the standard numbering; some
  DAWs display that as C3).  The note map and labels live in
  ``config/rhythm_config.json``.
* The pattern grid is 16th notes.  Stage 8.2 generates it from a pure
  :class:`control.groove.Groove`: the ``four`` preset reproduces the Stage 8.1
  four-on-the-floor default exactly, and ``break``/``half``/``techno``,
  density/syncopation ghosts, probability, and seeded reproducibility are
  additive.  Swing delays odd 16th steps in the scheduler; it never changes the
  pattern.  For non-4/4 meters the preset is truncated/tiled against
  ``steps_per_bar``; no polyrhythm logic is attempted.
* A 16-bit ``lane_mask`` gates the pattern (bit 0 = lane 1, default note 36;
  bit 15 = lane 16, default note 51).  A lane toggle takes effect on the next
  pattern step.
* Half/normal/double rate (0.5/1/2) scales the step duration only.  Tempo and
  meter state do not change.
* Every hit is note-on followed by note-off after a short gate (default 50 ms,
  capped at 80% of the step at very fast tempos).  Transport stop sends
  note-offs for any held hit and CC 123 (All Notes Off), so drums cannot leave
  stuck notes.

The scheduler runs in a daemon thread with drift-corrected
``time.monotonic()`` deadlines (the same pattern as :class:`control.arp.SetArp`),
including a per-step swing offset.
It never busy-waits and never blocks the Qt event loop.
"""

from __future__ import annotations

import json
import math
import sys
import threading
import time
from collections.abc import Mapping
from dataclasses import dataclass
from pathlib import Path
from typing import Any, Callable

try:  # Support both ``python -m control.drums`` and direct execution.
    from . import groove, rhythm
    from .groove import Groove, GrooveSpec
    from .rhythm import BPM_DEFAULT, DRUM_RATE_DEFAULT
except ImportError:  # pragma: no cover - exercised only by direct execution
    import groove  # type: ignore[no-redef]
    import rhythm  # type: ignore[no-redef]
    from groove import Groove, GrooveSpec  # type: ignore[no-redef]
    from rhythm import BPM_DEFAULT, DRUM_RATE_DEFAULT  # type: ignore[no-redef]

# ``rhythm`` is used with its module prefix everywhere below.

DEFAULT_PORT = "IAC Driver Bus 3"
DEFAULT_CHANNEL = 10  # 1-based MIDI channel; mido receives channel - 1 (9)
DEFAULT_VELOCITY = 100
DEFAULT_GATE_SECONDS = 0.05
DEFAULT_ENABLED = True
DEFAULT_RATE = DRUM_RATE_DEFAULT
LANE_COUNT = 16
LANE_MASK_ALL = 0xFFFF

DEFAULT_CONFIG_PATH = (
    Path(__file__).resolve().parent.parent / "config" / "rhythm_config.json"
)


@dataclass(frozen=True, slots=True)
class DrumLane:
    """One drum lane: its MIDI note, config/OSC name, and GUI label."""

    note: int
    name: str
    label: str
    role: str = ""


DEFAULT_LANES: tuple[DrumLane, ...] = (
    DrumLane(36, "kick1", "Kick 1", "four-on-the-floor kick"),
    DrumLane(37, "kick2", "Kick 2", "second kick"),
    DrumLane(38, "snare1", "Snare 1", "backbeat snare"),
    DrumLane(39, "snare2", "Snare 2", "second snare"),
    DrumLane(40, "perc1", "Perc 1", "rim/click"),
    DrumLane(41, "perc2", "Perc 2", "clap"),
    DrumLane(42, "perc3", "Perc 3", "closed hat"),
    DrumLane(43, "perc4", "Perc 4", "pedal hat"),
    DrumLane(44, "perc5", "Perc 5", "open hat"),
    DrumLane(45, "perc6", "Perc 6", "low tom"),
    DrumLane(46, "perc7", "Perc 7", "mid tom"),
    DrumLane(47, "perc8", "Perc 8", "high tom"),
    DrumLane(48, "perc9", "Perc 9", "crash"),
    DrumLane(49, "perc10", "Perc 10", "ride"),
    DrumLane(50, "perc11", "Perc 11", "shaker"),
    DrumLane(51, "perc12", "Perc 12", "cowbell"),
)

# bit 0 = kick1 (36), bit 2 = snare1 (38), bit 6 = perc3/closed hat (42).
DEFAULT_LANE_MASK = 0x0045
DEFAULT_KICK_LANE = 0
DEFAULT_SNARE_LANE = 2
DEFAULT_HAT_LANE = 6


# ---------------------------------------------------------------------------
# Config
# ---------------------------------------------------------------------------


@dataclass(frozen=True, slots=True)
class DrumConfig:
    """Validated drum configuration (``config/rhythm_config.json``)."""

    port: str = DEFAULT_PORT
    channel: int = DEFAULT_CHANNEL
    velocity: int = DEFAULT_VELOCITY
    gate_seconds: float = DEFAULT_GATE_SECONDS
    enabled: bool = DEFAULT_ENABLED
    rate: float = DEFAULT_RATE
    lane_mask: int = DEFAULT_LANE_MASK
    lanes: tuple[DrumLane, ...] = DEFAULT_LANES
    bpm: float = BPM_DEFAULT
    meter_num: int = 4
    meter_den: int = 4
    preset: str = groove.DEFAULT_PRESET
    density: float = groove.DENSITY_DEFAULT
    syncopation: float = groove.SYNCOPATION_DEFAULT
    swing: float = groove.SWING_DEFAULT
    probability: float = groove.PROBABILITY_DEFAULT
    seed: int = groove.DEFAULT_SEED

    @classmethod
    def from_mapping(cls, data: Mapping[str, Any]) -> "DrumConfig":
        if not isinstance(data, Mapping):
            raise TypeError("rhythm config must be a mapping")

        port = _nonempty_str(data.get("port", DEFAULT_PORT), "port")
        channel = _int(data.get("channel", DEFAULT_CHANNEL), "channel")
        if not 1 <= channel <= 16:
            raise ValueError("channel must be a 1-based MIDI channel in 1..16")
        velocity = _velocity(data.get("velocity", DEFAULT_VELOCITY), "velocity")
        gate = _positive_float(
            data.get("gate_seconds", DEFAULT_GATE_SECONDS), "gate_seconds"
        )
        if gate > 2.0:
            raise ValueError("gate_seconds must be <= 2.0")
        enabled = _bool(data.get("enabled", DEFAULT_ENABLED), "enabled")
        rate = rhythm.validate_drum_rate(data.get("rate", DEFAULT_RATE))
        lane_mask = _int(data.get("lane_mask", DEFAULT_LANE_MASK), "lane_mask")
        if not 0 <= lane_mask <= LANE_MASK_ALL:
            raise ValueError("lane_mask must be a 16-bit value in 0..65535")

        lanes_raw = data.get("lanes", None)
        lanes = DEFAULT_LANES if lanes_raw is None else _lanes(lanes_raw)

        bpm = rhythm.clamp_bpm(data.get("bpm", BPM_DEFAULT))
        if "meter" in data:
            meter = data["meter"]
            if isinstance(meter, (str, bytes)) or not isinstance(meter, (list, tuple)):
                raise TypeError("meter must be a [numerator, denominator] pair")
            if len(meter) != 2:
                raise ValueError("meter must be a [numerator, denominator] pair")
            meter_num, meter_den = meter
        else:
            meter_num = data.get("meter_num", 4)
            meter_den = data.get("meter_den", 4)
        meter_num = rhythm.clamp_meter_numerator(meter_num)
        meter_den = rhythm.validate_meter_denominator(meter_den)

        # Stage 8.2 groove defaults are additive.  A nested ``"groove"`` map
        # wins over the flat keys so an older config keeps working unchanged.
        groove_data = data.get("groove", {})
        if groove_data is None:
            groove_data = {}
        if not isinstance(groove_data, Mapping):
            raise TypeError("groove config must be a mapping")

        def groove_value(key: str, default: Any) -> Any:
            if key in groove_data:
                return groove_data[key]
            return data.get(key, default)

        spec = GrooveSpec.create(
            groove_value("preset", groove.DEFAULT_PRESET),
            groove_value("density", groove.DENSITY_DEFAULT),
            groove_value("syncopation", groove.SYNCOPATION_DEFAULT),
            groove_value("swing", groove.SWING_DEFAULT),
            groove_value("probability", groove.PROBABILITY_DEFAULT),
            groove_value("seed", groove.DEFAULT_SEED),
        )

        return cls(
            port=port,
            channel=channel,
            velocity=velocity,
            gate_seconds=gate,
            enabled=enabled,
            rate=rate,
            lane_mask=lane_mask,
            lanes=lanes,
            bpm=bpm,
            meter_num=meter_num,
            meter_den=meter_den,
            preset=spec.preset,
            density=spec.density,
            syncopation=spec.syncopation,
            swing=spec.swing,
            probability=spec.probability,
            seed=spec.seed,
        )

    @classmethod
    def from_json(cls, path: str | Path) -> "DrumConfig":
        return load_config(path)


def load_config(path: str | Path = DEFAULT_CONFIG_PATH) -> DrumConfig:
    """Load and validate ``config/rhythm_config.json``.

    A relative path that does not exist in the current directory is also tried
    relative to the ``ji-live`` project root, matching ``mpe_config.json``.
    """

    candidate = Path(path)
    if not candidate.is_absolute() and not candidate.exists():
        project_candidate = Path(__file__).resolve().parent.parent / candidate
        if project_candidate.exists():
            candidate = project_candidate

    try:
        text = candidate.read_text(encoding="utf-8")
    except FileNotFoundError:
        raise FileNotFoundError(f"rhythm config not found: {candidate}") from None

    try:
        data = json.loads(text)
    except json.JSONDecodeError as exc:
        raise ValueError(f"invalid JSON in {candidate}: {exc}") from exc
    return DrumConfig.from_mapping(data)


# ---------------------------------------------------------------------------
# Pattern
# ---------------------------------------------------------------------------


@dataclass(frozen=True, slots=True)
class DrumPattern:
    """One bar of 16th-note steps, each holding zero or more lane indices."""

    steps: tuple[tuple[int, ...], ...]
    meter_num: int
    meter_den: int

    @property
    def bar_steps(self) -> int:
        return len(self.steps)

    def lanes_at(self, step_index: int) -> tuple[int, ...]:
        if not self.steps:
            return ()
        return self.steps[int(step_index) % len(self.steps)]


def build_default_pattern(
    meter_num: int = 4,
    meter_den: int = 4,
    *,
    kick_lane: int = DEFAULT_KICK_LANE,
    snare_lane: int = DEFAULT_SNARE_LANE,
    hat_lane: int = DEFAULT_HAT_LANE,
    lane_count: int = LANE_COUNT,
) -> DrumPattern:
    """Build the deterministic four-on-the-floor pattern for one meter.

    * kick on every quarter-note beat (``step % 4 == 0``);
    * snare on quarter beats 2 and 4, only when the bar contains them;
    * closed hat on offbeat 8ths (``step % 4 == 2``).

    For 6/8 the bar is three quarter notes, so only beat 2 gets a snare; for
    3/4 only beat 2; for 5/4 beats 2 and 4; for 7/8 the bar is 3.5 quarters, so
    the beat-4 snare at step 12 is included.  Lane indices are kept in
    ascending order at each step.
    """

    numerator = rhythm.clamp_meter_numerator(meter_num)
    denominator = rhythm.validate_meter_denominator(meter_den)
    for name, value in (
        ("kick_lane", kick_lane),
        ("snare_lane", snare_lane),
        ("hat_lane", hat_lane),
    ):
        if isinstance(value, bool) or not isinstance(value, int):
            raise TypeError(f"{name} must be an integer")
        if not 0 <= value < lane_count:
            raise ValueError(f"{name} must be 0..{lane_count - 1}")

    total_steps = rhythm.steps_per_bar(numerator, denominator)
    quarters = rhythm.bar_quarters(numerator, denominator)
    steps: list[tuple[int, ...]] = []
    for step in range(total_steps):
        beat = step // rhythm.STEPS_PER_QUARTER
        on_beat = step % rhythm.STEPS_PER_QUARTER == 0
        lanes: list[int] = []
        if on_beat:
            lanes.append(kick_lane)
            if beat in (1, 3) and beat < quarters - 1e-9:
                lanes.append(snare_lane)
        if step % rhythm.STEPS_PER_QUARTER == 2:
            lanes.append(hat_lane)
        steps.append(tuple(sorted(set(lanes))))
    return DrumPattern(
        steps=tuple(steps),
        meter_num=numerator,
        meter_den=denominator,
    )


# ---------------------------------------------------------------------------
# Engine / lane state
# ---------------------------------------------------------------------------


class DrumEngine:
    """Thread-safe drum state: pattern, enabled lanes, rate, and meter."""

    def __init__(self, config: DrumConfig, groove_state: Groove | None = None) -> None:
        if not isinstance(config, DrumConfig):
            raise TypeError("config must be a DrumConfig")
        self.config = config
        self._lock = threading.RLock()
        self._lanes = config.lanes
        self._lane_index = {lane.note: index for index, lane in enumerate(config.lanes)}
        self._enabled = bool(config.enabled)
        self._rate = rhythm.validate_drum_rate(config.rate)
        self._lane_mask = config.lane_mask & LANE_MASK_ALL
        if groove_state is None:
            groove_state = Groove(
                GrooveSpec.create(
                    config.preset,
                    config.density,
                    config.syncopation,
                    config.swing,
                    config.probability,
                    config.seed,
                ),
                meter_num=config.meter_num,
                meter_den=config.meter_den,
                lane_names=[lane.name for lane in config.lanes],
            )
        elif not isinstance(groove_state, Groove):
            raise TypeError("groove_state must be a Groove or None")
        self._groove = groove_state
        self._pattern = self._pattern_from_groove()

    # -- queries --------------------------------------------------------

    @property
    def lanes(self) -> tuple[DrumLane, ...]:
        return self._lanes

    @property
    def enabled(self) -> bool:
        with self._lock:
            return self._enabled

    @property
    def rate(self) -> float:
        with self._lock:
            return self._rate

    @property
    def lane_mask(self) -> int:
        with self._lock:
            return self._lane_mask

    @property
    def velocity(self) -> int:
        return self.config.velocity

    @property
    def gate_seconds(self) -> float:
        return self.config.gate_seconds

    @property
    def pattern(self) -> DrumPattern:
        with self._lock:
            return self._pattern

    @property
    def groove(self) -> Groove:
        """The Stage 8.2 preset/grid/modifier state."""

        return self._groove

    @property
    def groove_spec(self) -> GrooveSpec:
        return self._groove.spec

    @property
    def grid(self) -> groove.GrooveGrid:
        return self._groove.grid

    @property
    def meter(self) -> str:
        pattern = self.pattern
        return f"{pattern.meter_num}/{pattern.meter_den}"

    def lane_for_note(self, note: object) -> DrumLane:
        value = int(round(float(note)))
        for lane in self._lanes:
            if lane.note == value:
                return lane
        raise ValueError(f"unknown drum note {note!r}")

    def lane_enabled(self, note: object) -> bool:
        index = self._index_for_note(note)
        with self._lock:
            return bool(self._lane_mask & (1 << index))

    def lane_states(self) -> dict[int, bool]:
        with self._lock:
            mask = self._lane_mask
        return {
            lane.note: bool(mask & (1 << index))
            for index, lane in enumerate(self._lanes)
        }

    def notes_for_step(self, step_index: int) -> tuple[int, ...]:
        """MIDI notes to trigger at ``step_index`` (empty when disabled).

        The step is interpreted against the generated Stage 8.2 groove: the bar
        index is ``step_index // steps_per_bar`` and the bar is regenerated
        deterministically, so ghost hits are stable for a given bar/seed.
        """

        with self._lock:
            if not self._enabled:
                return ()
            total = self._groove.steps_per_bar
            bar_index, step = divmod(int(step_index), total)
            indices = self._groove.lanes_at(step, bar_index)
            notes = [
                self._lanes[index].note
                for index in indices
                if self._lane_mask & (1 << index)
            ]
        return tuple(notes)

    def swing_offset(self, step_index: int, step_seconds_value: object) -> float:
        """Swing delay (seconds) for a scheduler step."""

        return self._groove.swing_offset(step_index, step_seconds_value)

    def pattern_description(self) -> str:
        """Human-readable summary used in the ``[DRUMS] pattern ...`` log."""

        with self._lock:
            pattern = self._pattern
            mask = self._lane_mask
        if (
            (pattern.meter_num, pattern.meter_den) == (4, 4)
            and self._groove.spec.preset == "four"
            and self._groove.spec.density == 0.0
            and self._groove.spec.syncopation == 0.0
            and self._groove.spec.probability == 0.0
            and len(self._lanes) == LANE_COUNT
            and [lane.name for lane in self._lanes[:7]]
            == [lane.name for lane in DEFAULT_LANES[:7]]
        ):
            return (
                f"pattern 4/4 kick1=quarter "
                f"snare1=2,4 perc3=offbeat8 lanes=0x{mask:04x}"
            )
        parts = []
        for index, lane in enumerate(self._lanes):
            steps = [
                step
                for step, indices in enumerate(pattern.steps)
                if index in indices
            ]
            if steps:
                parts.append(f"{lane.name}@{','.join(str(step) for step in steps)}")
        body = " ".join(parts) if parts else "(empty)"
        return (
            f"pattern {pattern.meter_num}/{pattern.meter_den} {body} "
            f"lanes=0x{mask:04x}"
        )

    # -- mutations ------------------------------------------------------

    def set_enabled(self, enabled: object) -> bool:
        with self._lock:
            self._enabled = bool(enabled)
            return self._enabled

    def set_rate(self, rate: object) -> float:
        value = rhythm.validate_drum_rate(rate)
        with self._lock:
            self._rate = value
            return value

    def set_lane(self, note: object, enabled: object) -> bool:
        index = self._index_for_note(note)
        with self._lock:
            if bool(enabled):
                self._lane_mask = (self._lane_mask | (1 << index)) & LANE_MASK_ALL
            else:
                self._lane_mask = (self._lane_mask & ~(1 << index)) & LANE_MASK_ALL
            return bool(self._lane_mask & (1 << index))

    def set_lane_by_name(self, name: str, enabled: object) -> bool:
        for lane in self._lanes:
            if lane.name == str(name):
                return self.set_lane(lane.note, enabled)
        raise ValueError(f"unknown drum lane {name!r}")

    # -- Stage 8.2 groove (immediate at the engine level) ----------------

    def set_preset(self, preset: object) -> GrooveSpec:
        with self._lock:
            spec = self._groove.set_preset(preset)
            self._refresh_pattern()
            return spec

    def set_groove_modifier(self, name: str, value: object) -> GrooveSpec:
        with self._lock:
            spec = self._groove.set_modifier(name, value)
            self._refresh_pattern()
            return spec

    def set_density(self, value: object) -> GrooveSpec:
        return self.set_groove_modifier("density", value)

    def set_syncopation(self, value: object) -> GrooveSpec:
        return self.set_groove_modifier("syncopation", value)

    def set_swing(self, value: object) -> GrooveSpec:
        return self.set_groove_modifier("swing", value)

    def set_probability(self, value: object) -> GrooveSpec:
        return self.set_groove_modifier("probability", value)

    def set_seed(self, seed: object) -> GrooveSpec:
        with self._lock:
            spec = self._groove.set_seed(seed)
            self._refresh_pattern()
            return spec

    def set_step(self, lane: object, step: object, on: object = True) -> bool:
        with self._lock:
            value = self._groove.set_cell(lane, step, on)
            self._refresh_pattern()
            return value

    def toggle_step(self, lane: object, step: object) -> bool:
        with self._lock:
            value = self._groove.toggle_cell(lane, step)
            self._refresh_pattern()
            return value

    def reset_grid(self) -> groove.GrooveGrid:
        with self._lock:
            grid = self._groove.reset_grid()
            self._refresh_pattern()
            return grid

    def set_meter(self, meter_num: object, meter_den: object) -> DrumPattern:
        numerator = rhythm.clamp_meter_numerator(meter_num)
        denominator = rhythm.validate_meter_denominator(meter_den)
        with self._lock:
            current = self._pattern
            if (numerator, denominator) == (
                current.meter_num,
                current.meter_den,
            ):
                return current
            self._groove.set_meter(numerator, denominator)
            self._refresh_pattern()
            return self._pattern

    def _pattern_from_groove(self) -> DrumPattern:
        bar = self._groove.bar(0)
        return DrumPattern(
            steps=bar.steps,
            meter_num=bar.meter_num,
            meter_den=bar.meter_den,
        )

    def _refresh_pattern(self) -> None:
        self._pattern = self._pattern_from_groove()

    def _make_pattern(self, meter_num: int, meter_den: int) -> DrumPattern:
        """Legacy Stage 8.1 four-on-the-floor pattern (kept for compatibility)."""

        names = [lane.name for lane in self._lanes]

        def index_of(name: str, fallback: int) -> int:
            try:
                return names.index(name)
            except ValueError:
                return min(fallback, len(self._lanes) - 1)

        return build_default_pattern(
            meter_num,
            meter_den,
            kick_lane=index_of("kick1", DEFAULT_KICK_LANE),
            snare_lane=index_of("snare1", DEFAULT_SNARE_LANE),
            hat_lane=index_of("perc3", DEFAULT_HAT_LANE),
            lane_count=len(self._lanes),
        )

    def _index_for_note(self, note: object) -> int:
        value = int(round(float(note)))
        for index, lane in enumerate(self._lanes):
            if lane.note == value:
                return index
        raise ValueError(f"unknown drum note {note!r}")


# ---------------------------------------------------------------------------
# MIDI output (lazy mido)
# ---------------------------------------------------------------------------


@dataclass(frozen=True, slots=True)
class DrumMessage:
    """A transport-independent drum MIDI message used by the fake path."""

    type: str
    channel: int
    note: int | None = None
    velocity: int | None = None
    control: int | None = None
    value: int | None = None


class DrumOutput:
    """One plain MIDI drum output port, with a lazy mido adapter."""

    def __init__(
        self,
        port_name: str = DEFAULT_PORT,
        channel: int = DEFAULT_CHANNEL,
        velocity: int = DEFAULT_VELOCITY,
        gate_seconds: float = DEFAULT_GATE_SECONDS,
        output: Any | None = None,
        label: str = "drums",
    ) -> None:
        if not isinstance(port_name, str) or not port_name.strip():
            raise ValueError("port_name must be a non-empty string")
        if isinstance(channel, bool) or not isinstance(channel, int):
            raise TypeError("channel must be an integer")
        if not 1 <= channel <= 16:
            raise ValueError("channel must be a 1-based MIDI channel in 1..16")
        if isinstance(velocity, bool) or not isinstance(velocity, int):
            raise TypeError("velocity must be an integer")
        if not 1 <= velocity <= 127:
            raise ValueError("velocity must be between 1 and 127")
        self.port_name = port_name
        self.channel = channel - 1  # mido / DrumMessage use 0-based
        self.velocity = velocity
        self.gate_seconds = _positive_float(gate_seconds, "gate_seconds")
        self.label = label

        self._output = output
        self._opened = output is not None
        self._mido: Any | None = None

    # -- lifecycle ------------------------------------------------------

    def open(self) -> None:
        """Open the real mido port, or no-op when a fake output was supplied."""

        if self._opened:
            return
        try:
            import mido  # intentionally lazy
        except ImportError as exc:  # pragma: no cover - hardware path
            raise RuntimeError(
                "mido is required for drum MIDI output. Run with: "
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
                f"drum MIDI output {self.port_name!r} not found. "
                f"Available MIDI outputs: {available}. "
                "Check Audio MIDI Setup -> IAC Driver and config/rhythm_config.json."
            )
        try:
            port = mido.open_output(self.port_name)
        except Exception as exc:  # pragma: no cover - hardware path
            raise RuntimeError(
                f"could not open drum MIDI output {self.port_name!r}: {exc}"
            ) from exc
        self._mido = mido
        self._output = _MidoDrumAdapter(mido, port)
        self._opened = True

    def close(self) -> None:
        output = self._output
        self._output = None
        self._opened = False
        if output is not None:
            close = getattr(output, "close", None)
            if callable(close):
                close()

    @property
    def is_open(self) -> bool:
        return self._opened

    # -- messages -------------------------------------------------------

    def note_on(self, note: int, velocity: int | None = None) -> None:
        self._send(
            DrumMessage(
                type="note_on",
                channel=self.channel,
                note=_midi_note(note),
                velocity=self.velocity if velocity is None else _midi_value(velocity, "velocity", 1),
            )
        )

    def note_off(self, note: int) -> None:
        self._send(
            DrumMessage(
                type="note_off",
                channel=self.channel,
                note=_midi_note(note),
                velocity=0,
            )
        )

    def control_change(self, control: int, value: int) -> None:
        self._send(
            DrumMessage(
                type="control_change",
                channel=self.channel,
                control=_midi_value(control, "control", 0),
                value=_midi_value(value, "value", 0),
            )
        )

    def all_notes_off(self) -> None:
        """CC 123 (All Notes Off) on the drum channel."""

        self.control_change(123, 0)

    def _send(self, message: DrumMessage) -> None:
        if not self._opened or self._output is None:
            raise RuntimeError(
                f"{self.label} MIDI output {self.port_name!r} is not open"
            )
        self._output.send(message)


class _MidoDrumAdapter:
    """Translate :class:`DrumMessage` into ``mido.Message``."""

    def __init__(self, mido_module: Any, port: Any) -> None:
        self._mido = mido_module
        self._port = port

    def send(self, message: DrumMessage) -> None:
        if message.type in ("note_on", "note_off"):
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
        else:  # pragma: no cover - only the three types above are used
            raise ValueError(f"unsupported drum message type {message.type!r}")
        self._port.send(outgoing)

    def close(self) -> None:
        self._port.close()


# ---------------------------------------------------------------------------
# Scheduler
# ---------------------------------------------------------------------------


class DrumScheduler:
    """Daemon-thread scheduler for the 16th-note drum pattern.

    The thread owns drift-corrected deadlines and sends note-on/note-off pairs
    with a short gate.  Tempo/rate changes affect the next step interval; meter
    and lane changes are picked up on the next step.  ``stop()`` sends
    note-offs for the currently held step plus All Notes Off.
    """

    def __init__(
        self,
        output: DrumOutput,
        engine: DrumEngine,
        *,
        bpm: float = BPM_DEFAULT,
        gate_seconds: float | None = None,
        now: Callable[[], float] = time.monotonic,
    ) -> None:
        if not callable(now):
            raise TypeError("now must be callable")
        self.output = output
        self.engine = engine
        self._now = now
        self._bpm = rhythm.clamp_bpm(bpm)
        gate = engine.gate_seconds if gate_seconds is None else float(gate_seconds)
        self.gate_seconds = _positive_float(gate, "gate_seconds")
        self._lock = threading.Lock()
        self._stop = threading.Event()
        self._thread: threading.Thread | None = None
        self._step_index = 0
        self._held: set[int] = set()
        self._swing_logged = False

    @property
    def bpm(self) -> float:
        with self._lock:
            return self._bpm

    @property
    def step_index(self) -> int:
        with self._lock:
            return self._step_index

    @property
    def is_running(self) -> bool:
        thread = self._thread
        return thread is not None and thread.is_alive()

    def set_bpm(self, bpm: object) -> float:
        value = rhythm.clamp_bpm(bpm)
        with self._lock:
            self._bpm = value
            return value

    def start(self) -> None:
        """Start the scheduler at step 0.  Idempotent while running."""

        with self._lock:
            if self._thread is not None and self._thread.is_alive():
                return
            if not self.output.is_open:
                raise RuntimeError("drum output is not open")
            self._stop.clear()
            self._step_index = 0
            self._held = set()
            self._swing_logged = False
            thread = threading.Thread(
                target=self._run,
                name="ji-drums",
                daemon=True,
            )
            self._thread = thread
            thread.start()

    def stop(self) -> None:
        """Stop the thread, note-off held hits, and send All Notes Off."""

        self._stop.set()
        thread = self._thread
        if thread is not None and thread is not threading.current_thread():
            thread.join(timeout=2.0)
        with self._lock:
            if self._thread is thread:
                self._thread = None
            held = set(self._held)
            self._held = set()
        try:
            for note in sorted(held):
                self.output.note_off(note)
        finally:
            self.output.all_notes_off()

    # -- thread body ----------------------------------------------------

    def _run(self) -> None:  # pragma: no cover - exercised with a fake port
        deadline = self._now()
        step = 0
        try:
            while not self._stop.is_set():
                with self._lock:
                    bpm = self._bpm
                rate = self.engine.rate
                step_length = rhythm.step_seconds(bpm, rate)

                # Stage 8.2 swing: delay odd 16ths by up to 50% of a step.  The
                # nominal deadline still advances by exactly one step, so swing
                # never accumulates drift and never fires a step early.
                offset = self.engine.swing_offset(step, step_length)
                # Cap the gate at the time left before the next nominal step as
                # well as the 8.1 80%-of-a-step ceiling, so a swung note-off can
                # never run into the following step at fast tempos.
                gate_limit = max(
                    0.001,
                    min(step_length * 0.8, max(0.001, step_length - offset)),
                )
                gate = min(self.gate_seconds, gate_limit)
                if offset > 0.0:
                    target = deadline + offset
                    now = self._now()
                    if target > now:
                        if self._stop.wait(target - now):
                            break
                    if not self._swing_logged:
                        print(
                            f"[DRUMS] swing={self.engine.groove_spec.swing:.2f} "
                            f"step={step} offset={offset:.3f} s"
                        )
                        self._swing_logged = True

                notes = self.engine.notes_for_step(step)

                if notes:
                    with self._lock:
                        self._held = set(notes)
                    for note in notes:
                        self.output.note_on(note)
                    stopped = self._stop.wait(gate)
                    for note in notes:
                        self.output.note_off(note)
                    with self._lock:
                        self._held = set()
                    if stopped:
                        break

                with self._lock:
                    self._step_index = step
                step += 1
                deadline += step_length
                now = self._now()
                if deadline < now:
                    # Fell behind (slow output); resume from now, never burst
                    # through missed steps.
                    deadline = now
                wait = deadline - now
                if wait > 0.0 and self._stop.wait(wait):
                    break
        except Exception as exc:  # a closed/broken port must not crash quietly
            print(f"[DRUMS] scheduler error: {exc}", file=sys.stderr)
        finally:
            with self._lock:
                held = set(self._held)
                self._held = set()
            try:
                for note in sorted(held):
                    self.output.note_off(note)
            except Exception:
                pass


# ---------------------------------------------------------------------------
# Rig orchestration
# ---------------------------------------------------------------------------


class DrumRig:
    """Own the drum output, engine, and scheduler behind one small API."""

    def __init__(
        self,
        config: DrumConfig | Mapping[str, Any] | str | Path | None = None,
        *,
        config_path: str | Path | None = None,
        output: Any | None = None,
    ) -> None:
        if config is None:
            config = config_path if config_path is not None else DEFAULT_CONFIG_PATH
        if isinstance(config, DrumConfig):
            cfg = config
        elif isinstance(config, (str, Path)):
            cfg = load_config(config)
        elif isinstance(config, Mapping):
            cfg = DrumConfig.from_mapping(config)
        else:
            raise TypeError("config must be a DrumConfig, mapping, path, or None")

        self.config = cfg
        self.engine = DrumEngine(cfg)
        self.output = DrumOutput(
            cfg.port,
            channel=cfg.channel,
            velocity=cfg.velocity,
            gate_seconds=cfg.gate_seconds,
            output=output,
            label="drums",
        )
        self.scheduler = DrumScheduler(
            self.output,
            self.engine,
            bpm=cfg.bpm,
            gate_seconds=cfg.gate_seconds,
        )
        self._port_ok = output is not None
        self._started = False
        self._transport_running = False

    # -- lifecycle ------------------------------------------------------

    def start(self) -> bool:
        """Open the port; a missing Bus 3 disables drums but never raises."""

        if self._started:
            return self._port_ok
        self._started = True
        if not self.output.is_open:
            try:
                self.output.open()
            except Exception as exc:
                self._port_ok = False
                self.engine.set_enabled(False)
                print(
                    f"[MIDI] drums disabled: {exc}; drone/arp continue",
                    file=sys.stderr,
                )
                return False
        self._port_ok = True
        print(
            f"[DRUMS] port={self.config.port} channel={self.config.channel} "
            f"rate={self.engine.rate:.1f}"
        )
        print(f"[DRUMS] {self.engine.pattern_description()}")
        print(
            f"[DRUMS] enabled={int(self.engine.enabled)} "
            f"lanes=0x{self.engine.lane_mask:04x}"
        )
        spec = self.engine.groove_spec
        print(
            f"[GROOVE] preset={spec.preset} density={spec.density:.2f} "
            f"sync={spec.syncopation:.2f} swing={spec.swing:.2f} "
            f"probability={spec.probability:.2f} seed={spec.seed}"
        )
        if self._transport_running and self.engine.enabled:
            self.scheduler.start()
        return True

    def stop(self) -> None:
        try:
            if self.output.is_open:
                self.scheduler.stop()
        finally:
            self.output.close()
        self._started = False
        self._port_ok = False

    # -- state ----------------------------------------------------------

    @property
    def port_ok(self) -> bool:
        return self._port_ok

    @property
    def enabled(self) -> bool:
        return self.engine.enabled and self._port_ok

    @property
    def rate(self) -> float:
        return self.engine.rate

    @property
    def lane_mask(self) -> int:
        return self.engine.lane_mask

    @property
    def port(self) -> str:
        return self.config.port

    @property
    def groove(self) -> Groove:
        return self.engine.groove

    def groove_state(self) -> tuple[str, float, float, float, float, int, int]:
        """``(preset, density, sync, swing, probability, seed, grid_steps)``."""

        spec = self.engine.groove_spec
        return (
            spec.preset,
            float(spec.density),
            float(spec.syncopation),
            float(spec.swing),
            float(spec.probability),
            int(spec.seed),
            int(self.engine.groove.grid_steps),
        )

    def grid_payload(self) -> list[int]:
        return self.engine.grid.payload()

    # -- mutations ------------------------------------------------------

    def set_transport(self, running: object) -> None:
        self._transport_running = bool(running)
        if self._transport_running:
            if self._port_ok and self.engine.enabled:
                self.scheduler.start()
        else:
            if self.scheduler.is_running:
                self.scheduler.stop()

    def set_enabled(self, enabled: object) -> bool:
        if not self._port_ok:
            return False
        self.engine.set_enabled(enabled)
        if self.engine.enabled:
            if self._transport_running:
                self.scheduler.start()
        else:
            if self.scheduler.is_running:
                self.scheduler.stop()
        return True

    def set_lane(self, note: object, enabled: object) -> bool:
        return self.engine.set_lane(note, enabled)

    def set_rate(self, rate: object) -> float:
        return self.engine.set_rate(rate)

    def set_bpm(self, bpm: object) -> float:
        return self.scheduler.set_bpm(bpm)

    def set_meter(self, meter_num: object, meter_den: object) -> DrumPattern:
        before = self.engine.pattern
        pattern = self.engine.set_meter(meter_num, meter_den)
        if pattern is not before:
            if self._port_ok:
                print(f"[DRUMS] {self.engine.pattern_description()}")
            spec = self.engine.groove_spec
            print(
                f"[GROOVE] grid reloaded for meter={pattern.meter_num}/"
                f"{pattern.meter_den} steps={self.engine.groove.grid_steps} "
                f"(preset={spec.preset})"
            )
            if self.engine.groove.steps_per_bar > self.engine.groove.grid_steps:
                print(
                    f"[GROOVE] grid capped at {self.engine.groove.grid_steps} "
                    f"editable columns (bar has "
                    f"{self.engine.groove.steps_per_bar} steps)"
                )
        return pattern

    # -- Stage 8.2 groove -------------------------------------------------

    def set_preset(self, preset: object) -> GrooveSpec:
        return self.engine.set_preset(preset)

    def set_groove_modifier(self, name: str, value: object) -> GrooveSpec:
        return self.engine.set_groove_modifier(name, value)

    def set_swing(self, value: object) -> GrooveSpec:
        return self.engine.set_swing(value)

    def set_seed(self, seed: object) -> GrooveSpec:
        return self.engine.set_seed(seed)

    def set_step(self, lane: object, step: object, on: object = True) -> bool:
        return self.engine.set_step(lane, step, on)

    def reset_grid(self) -> groove.GrooveGrid:
        return self.engine.reset_grid()


# ---------------------------------------------------------------------------
# Validation helpers
# ---------------------------------------------------------------------------


def _nonempty_str(value: Any, label: str) -> str:
    if not isinstance(value, str) or not value.strip():
        raise TypeError(f"{label} must be a non-empty string")
    return value


def _int(value: Any, label: str) -> int:
    if isinstance(value, bool) or not isinstance(value, int):
        raise TypeError(f"{label} must be an integer")
    return value


def _bool(value: Any, label: str) -> bool:
    if isinstance(value, bool):
        return value
    if isinstance(value, int) and value in (0, 1):
        return bool(value)
    raise TypeError(f"{label} must be a boolean or 0/1")


def _velocity(value: Any, label: str) -> int:
    number = _int(value, label)
    if not 1 <= number <= 127:
        raise ValueError(f"{label} must be between 1 and 127")
    return number


def _positive_float(value: Any, label: str) -> float:
    if isinstance(value, bool):
        raise TypeError(f"{label} must be a real number")
    try:
        number = float(value)
    except (TypeError, ValueError):
        raise TypeError(f"{label} must be a real number") from None
    if not math.isfinite(number) or number <= 0.0:
        raise ValueError(f"{label} must be finite and strictly positive")
    return number


def _midi_value(value: Any, label: str, low: int) -> int:
    try:
        number = int(round(float(value)))
    except (TypeError, ValueError):
        raise TypeError(f"{label} must be a real number") from None
    if not low <= number <= 127:
        raise ValueError(f"{label} must be between {low} and 127")
    return number


def _midi_note(value: Any) -> int:
    return _midi_value(value, "note", 0)


def _lanes(value: Any) -> tuple[DrumLane, ...]:
    if isinstance(value, (str, bytes)):
        raise TypeError("lanes must be a list of lane definitions")
    try:
        items = list(value)
    except TypeError:
        raise TypeError("lanes must be a list of lane definitions") from None
    if len(items) != LANE_COUNT:
        raise ValueError(f"lanes must contain exactly {LANE_COUNT} definitions")
    lanes: list[DrumLane] = []
    for item in items:
        if isinstance(item, DrumLane):
            lanes.append(item)
            continue
        if not isinstance(item, Mapping):
            raise TypeError("each lane must be a mapping")
        note = _int(item.get("note"), "lane note")
        if not 0 <= note <= 127:
            raise ValueError("lane note must be between 0 and 127")
        name = _nonempty_str(item.get("name"), "lane name")
        label = _nonempty_str(item.get("label", name), "lane label")
        role = str(item.get("role", ""))
        lanes.append(DrumLane(note, name, label, role))
    notes = [lane.note for lane in lanes]
    names = [lane.name for lane in lanes]
    if len(set(notes)) != len(notes):
        raise ValueError("lane MIDI notes must be unique")
    if len(set(names)) != len(names):
        raise ValueError("lane names must be unique")
    return tuple(lanes)


__all__ = [
    "DEFAULT_CHANNEL",
    "DEFAULT_CONFIG_PATH",
    "DEFAULT_GATE_SECONDS",
    "DEFAULT_LANE_MASK",
    "DEFAULT_LANES",
    "DEFAULT_PORT",
    "DEFAULT_RATE",
    "DEFAULT_VELOCITY",
    "DrumConfig",
    "DrumEngine",
    "DrumLane",
    "DrumMessage",
    "DrumOutput",
    "DrumPattern",
    "DrumRig",
    "DrumScheduler",
    "LANE_COUNT",
    "LANE_MASK_ALL",
    "build_default_pattern",
    "load_config",
]
