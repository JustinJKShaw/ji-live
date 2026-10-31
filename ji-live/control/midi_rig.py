"""MIDI rig orchestrator for Stage 6 MPE and the Stage 8.1 drum output.

The rig owns two independent monophonic MPE outputs and, optionally, one plain
drum output::

    AnchorDrone -> IAC Driver Bus 1 -> Ableton "Drone" MIDI track
    SetArp      -> IAC Driver Bus 2 -> Ableton "Arp"   MIDI track
    DrumRig     -> IAC Driver Bus 3 -> Ableton "Drums" MIDI track  (optional)

Both buses use MIDI member channel 2 (mido index 1).  Ableton's MPE pitch-bend
range is configured to 48 semitones; keep ``bend_range_semitones`` in
``config/mpe_config.json`` equal to it.

No mido import happens at module import time or in the constructor.  Ports are
opened by :meth:`MIDIRig.start`, so the existing Stage 2/3.5 Python harnesses can
import the bridge without mido installed.  Fake output objects can be injected
for the pure tests.
"""

from __future__ import annotations

import json
import math
import sys
import threading
from collections.abc import Mapping
from dataclasses import dataclass
from pathlib import Path
from typing import Any

try:  # Support both ``python -m control.midi_rig`` and direct execution.
    from .arp import SetArp
    from .drone import AnchorDrone
    from .drums import DEFAULT_LANE_MASK, DRUM_RATE_DEFAULT, DrumRig
    from .mpe_output import MPEOutput
except ImportError:  # pragma: no cover - exercised only by direct execution
    from arp import SetArp
    from drone import AnchorDrone
    from drums import DEFAULT_LANE_MASK, DRUM_RATE_DEFAULT, DrumRig
    from mpe_output import MPEOutput

DEFAULT_CONFIG_PATH = (
    Path(__file__).resolve().parent.parent / "config" / "mpe_config.json"
)


@dataclass(frozen=True, slots=True)
class MPEConfig:
    """Validated Stage 6 MIDI configuration."""

    drone_port: str
    arp_port: str
    member_channel: int
    master_channel: int
    bend_range_semitones: float
    drone_velocity: int
    arp_velocity: int
    step_seconds: float
    gate_seconds: float
    arp_octave_offsets: tuple[int, int, int]
    arp_shared_mode: str
    arp_seed: int

    @classmethod
    def from_mapping(cls, data: Mapping[str, Any]) -> MPEConfig:
        if not isinstance(data, Mapping):
            raise TypeError("MPE config must be a mapping")

        drone_port = _nonempty_str(_required(data, "drone_port"), "drone_port")
        arp_port = _nonempty_str(_required(data, "arp_port"), "arp_port")
        if drone_port == arp_port:
            raise ValueError("drone_port and arp_port must be different buses")

        member_channel = _int(
            _required(data, "member_channel"), "member_channel"
        )
        master_channel = _int(
            _required(data, "master_channel"), "master_channel"
        )
        if not 2 <= member_channel <= 16:
            raise ValueError(
                "member_channel must be 2..16 (channel 1 is the MPE master)"
            )
        if not 1 <= master_channel <= 16:
            raise ValueError("master_channel must be 1..16")
        if member_channel == master_channel:
            raise ValueError("member_channel must differ from master_channel")

        bend_range = _positive_float(
            _required(data, "bend_range_semitones"), "bend_range_semitones"
        )
        drone_velocity = _velocity(
            _required(data, "drone_velocity"), "drone_velocity"
        )
        arp_velocity = _velocity(
            _required(data, "arp_velocity"), "arp_velocity"
        )
        step_seconds = _positive_float(
            _required(data, "step_seconds"), "step_seconds"
        )
        gate_seconds = _positive_float(
            _required(data, "gate_seconds"), "gate_seconds"
        )
        if gate_seconds > step_seconds:
            raise ValueError("gate_seconds must be <= step_seconds")
        offsets = _offsets(
            _required(data, "arp_octave_offsets"), "arp_octave_offsets"
        )
        shared_mode = _required(data, "arp_shared_mode")
        if shared_mode not in ("anchor", "random"):
            raise ValueError("arp_shared_mode must be 'anchor' or 'random'")
        seed = _int(_required(data, "arp_seed"), "arp_seed")

        return cls(
            drone_port=drone_port,
            arp_port=arp_port,
            member_channel=member_channel,
            master_channel=master_channel,
            bend_range_semitones=bend_range,
            drone_velocity=drone_velocity,
            arp_velocity=arp_velocity,
            step_seconds=step_seconds,
            gate_seconds=gate_seconds,
            arp_octave_offsets=offsets,
            arp_shared_mode=shared_mode,
            arp_seed=seed,
        )

    @classmethod
    def from_json(cls, path: str | Path) -> MPEConfig:
        return load_config(path)


def load_config(path: str | Path = DEFAULT_CONFIG_PATH) -> MPEConfig:
    """Load and validate ``config/mpe_config.json``.

    A relative path that does not exist in the current directory is also tried
    relative to the ``ji-live`` project root, so the documented invocation works
    from either ``ji-live/`` or the repository root.
    """

    candidate = Path(path)
    if not candidate.is_absolute() and not candidate.exists():
        project_candidate = (
            Path(__file__).resolve().parent.parent / candidate
        )
        if project_candidate.exists():
            candidate = project_candidate

    try:
        text = candidate.read_text(encoding="utf-8")
    except FileNotFoundError:
        raise FileNotFoundError(f"MPE config not found: {candidate}") from None

    try:
        data = json.loads(text)
    except json.JSONDecodeError as exc:
        raise ValueError(f"invalid JSON in {candidate}: {exc}") from exc
    return MPEConfig.from_mapping(data)


class MIDIRig:
    """Own the MPE drone/arp outputs and the optional Stage 8.1 drum output.

    Args:
        config: An :class:`MPEConfig`, a mapping, a path, or ``None`` (use
            ``config_path`` or the default config).
        config_path: Alternative config path when ``config`` is ``None``.
        drone_output: Optional fake output for tests.
        arp_output: Optional fake output for tests.
        midi: Build the Stage 6 drone/arp buses (default ``True``).  Passing
            ``False`` is used by ``--drums`` without ``--midi``; only the drum
            output is created.
        drums: Build the Stage 8.1 drum output (default ``False``).  A missing
            Bus 3 disables drums but never raises from :meth:`start`.
        drum_config: A :class:`~control.drums.DrumConfig`, mapping, path, or
            ``None`` (default ``config/rhythm_config.json``).
        drum_output: Optional fake output for the drum tests.
    """

    def __init__(
        self,
        config: MPEConfig | Mapping[str, Any] | str | Path | None = None,
        *,
        config_path: str | Path | None = None,
        drone_output: Any | None = None,
        arp_output: Any | None = None,
        midi: bool = True,
        drums: bool = False,
        drum_config: Any | None = None,
        drum_output: Any | None = None,
    ) -> None:
        self._midi_enabled = bool(midi)
        cfg: MPEConfig | None = None
        if self._midi_enabled:
            if config is None:
                config = (
                    config_path if config_path is not None else DEFAULT_CONFIG_PATH
                )
            if isinstance(config, MPEConfig):
                cfg = config
            elif isinstance(config, (str, Path)):
                cfg = load_config(config)
            elif isinstance(config, Mapping):
                cfg = MPEConfig.from_mapping(config)
            else:
                raise TypeError(
                    "config must be an MPEConfig, mapping, path, or None"
                )

        self.config = cfg
        if cfg is not None:
            self.drone_output: MPEOutput | None = MPEOutput(
                cfg.drone_port,
                member_channel=cfg.member_channel,
                bend_range_semitones=cfg.bend_range_semitones,
                velocity=cfg.drone_velocity,
                output=drone_output,
                label="drone",
            )
            self.arp_output: MPEOutput | None = MPEOutput(
                cfg.arp_port,
                member_channel=cfg.member_channel,
                bend_range_semitones=cfg.bend_range_semitones,
                velocity=cfg.arp_velocity,
                output=arp_output,
                label="arp",
            )
            self.drone: AnchorDrone | None = AnchorDrone(
                self.drone_output, velocity=cfg.drone_velocity
            )
            self.arp: SetArp | None = SetArp(
                self.arp_output,
                step_seconds=cfg.step_seconds,
                gate_seconds=cfg.gate_seconds,
                octave_offsets=cfg.arp_octave_offsets,
                shared_mode=cfg.arp_shared_mode,
                seed=cfg.arp_seed,
                velocity=cfg.arp_velocity,
            )
        else:
            self.drone_output = None
            self.arp_output = None
            self.drone = None
            self.arp = None

        self.drums: DrumRig | None = (
            DrumRig(drum_config, output=drum_output) if drums else None
        )

        self._lock = threading.Lock()
        self._started = False
        self._snapshot: tuple[list[float], float | None, float] | None = None
        # Stage 7.1 UI toggles.  Both buses default on; the ports stay open
        # while a bus is muted so re-enabling is immediate.
        self._drone_enabled = True
        self._arp_enabled = True

    # ------------------------------------------------------------------
    # Lifecycle
    # ------------------------------------------------------------------

    def start(self) -> None:
        """Open the configured ports and start the arp / drums.

        The Stage 6 drone starts on the first :meth:`on_set`.  If a set
        snapshot was supplied before ``start``, it is applied immediately.
        A missing drum port is handled by :class:`~control.drums.DrumRig`:
        it prints ``[MIDI] drums disabled: ...; drone/arp continue`` and the
        drone/arp/audio paths keep running.
        """

        with self._lock:
            if self._started:
                return
            if self._midi_enabled:
                try:
                    self.drone_output.open()
                    self.arp_output.open()
                except Exception:
                    self.drone_output.close()
                    self.arp_output.close()
                    raise
            self._started = True
            snapshot = self._snapshot

        if self._midi_enabled:
            print(
                f"[MIDI] drone port={self.config.drone_port} "
                f"channel={self.config.member_channel} "
                f"bend_range={self.config.bend_range_semitones:g}"
            )
            print(
                f"[MIDI] arp port={self.config.arp_port} "
                f"channel={self.config.member_channel} "
                f"step={self.config.step_seconds:.2f}s "
                f"gate={self.config.gate_seconds:.2f}s"
            )

        drum_ok = True
        if self.drums is not None:
            drum_ok = self.drums.start()

        try:
            if self._midi_enabled:
                if snapshot is not None:
                    self._apply(snapshot)
                if self._arp_enabled:
                    self.arp.start()
        except Exception:
            self._started = False
            if self._midi_enabled:
                self.arp.stop()
                self.drone.stop()
                self.drone_output.close()
                self.arp_output.close()
            if self.drums is not None and drum_ok:
                self.drums.stop()
            raise

    def stop(self) -> None:
        """Stop all voices, send All Notes Off, and close the ports.

        A drum port that never opened is skipped safely, so a missing Bus 3
        cannot block shutdown.
        """

        with self._lock:
            started = self._started
            self._started = False
            self._snapshot = None

        if self.drums is not None:
            try:
                self.drums.stop()
            except Exception as exc:  # drums are non-critical
                print(f"[MIDI] drum stop failed: {exc}", file=sys.stderr)

        if not self._midi_enabled:
            return

        if not started:
            self.drone_output.close()
            self.arp_output.close()
            return

        try:
            self.arp.stop()
        finally:
            try:
                self.drone.stop()
            finally:
                for output in (self.drone_output, self.arp_output):
                    try:
                        output.all_notes_off()
                    finally:
                        output.close()
                print("[MIDI] stopped")

    @property
    def is_started(self) -> bool:
        with self._lock:
            return self._started

    @property
    def midi_enabled(self) -> bool:
        return self._midi_enabled

    @property
    def drum_port(self) -> str:
        return "" if self.drums is None else self.drums.port

    @property
    def drums_enabled(self) -> bool:
        return False if self.drums is None else self.drums.enabled

    @property
    def drums_port_ok(self) -> bool:
        return False if self.drums is None else self.drums.port_ok

    @property
    def drum_rate(self) -> float:
        return DRUM_RATE_DEFAULT if self.drums is None else self.drums.rate

    @property
    def drum_lane_mask(self) -> int:
        return DEFAULT_LANE_MASK if self.drums is None else self.drums.lane_mask

    def drums_state(self) -> tuple[bool, bool, float, int]:
        """``(enabled, port_ok, rate, lane_mask)`` for ``/ji/drums/state``."""

        if self.drums is None:
            return False, False, DRUM_RATE_DEFAULT, DEFAULT_LANE_MASK
        return (
            self.drums.enabled,
            self.drums.port_ok,
            self.drums.rate,
            self.drums.lane_mask,
        )

    # ------------------------------------------------------------------
    # Stage 7.1 per-bus UI toggles
    # ------------------------------------------------------------------

    @property
    def drone_enabled(self) -> bool:
        with self._lock:
            return self._drone_enabled if self._midi_enabled else False

    @property
    def arp_enabled(self) -> bool:
        with self._lock:
            return self._arp_enabled if self._midi_enabled else False

    def set_drone_enabled(self, enabled: bool) -> None:
        """Mute or unmute the drone without closing its MIDI port.

        Disabling note-offs the held drone note and sends All Notes Off on the
        drone bus.  Enabling re-applies the last set snapshot, which note-ons
        the current anchor again.  Both operations are no-ops if the requested
        state is already active.  With ``midi=False`` (drums-only rig) this is
        a logged no-op.
        """

        if not self._midi_enabled:
            print("[MIDI] drone switch ignored: MIDI buses are disabled")
            return

        enabled = bool(enabled)
        with self._lock:
            if enabled == self._drone_enabled:
                return
            self._drone_enabled = enabled
            started = self._started
            snapshot = self._snapshot

        if not started:
            print(f"[MIDI] drone {'enabled' if enabled else 'disabled'} (awaiting start)")
            return

        if enabled:
            if snapshot is not None:
                ratios, anchor, fundamental = snapshot
                self.drone.on_set(ratios, anchor, fundamental)
            else:
                print("[MIDI] drone enabled; waiting for the next set update")
        else:
            self.drone.stop()
            print("[MIDI] drone disabled")

    def set_arp_enabled(self, enabled: bool) -> None:
        """Start or stop the arp thread without closing its MIDI port."""

        if not self._midi_enabled:
            print("[MIDI] arp switch ignored: MIDI buses are disabled")
            return

        enabled = bool(enabled)
        with self._lock:
            if enabled == self._arp_enabled:
                return
            self._arp_enabled = enabled
            started = self._started
            snapshot = self._snapshot

        if not started:
            print(f"[MIDI] arp {'enabled' if enabled else 'disabled'} (awaiting start)")
            return

        if enabled:
            if snapshot is not None:
                ratios, anchor, fundamental = snapshot
                self.arp.on_set(ratios, anchor, fundamental)
            self.arp.start()
            print("[MIDI] arp enabled")
        else:
            self.arp.stop()
            print("[MIDI] arp disabled")

    # ------------------------------------------------------------------
    # Set updates (snapshot from the bridge)
    # ------------------------------------------------------------------

    def on_set(
        self,
        ratios: list[float] | tuple[float, ...],
        anchor: float | None,
        fundamental: float,
    ) -> None:
        """Apply one engine snapshot to the drone and arp.

        The bridge takes the snapshot after releasing the engine lock; the MIDI
        threads never read the engine directly.
        """

        snapshot = (list(ratios), anchor, float(fundamental))
        with self._lock:
            self._snapshot = snapshot
            started = self._started
        if started and self._midi_enabled:
            self._apply(snapshot)

    update = on_set

    # ------------------------------------------------------------------
    # Stage 8.1 drum output
    # ------------------------------------------------------------------

    def set_drums_enabled(self, enabled: bool) -> bool:
        if self.drums is None:
            return False
        return self.drums.set_enabled(enabled)

    def set_drums_lane(self, note: int, enabled: bool) -> bool:
        if self.drums is None:
            raise RuntimeError("drums are not configured")
        return self.drums.set_lane(note, enabled)

    def set_drums_rate(self, rate: float) -> float:
        if self.drums is None:
            raise RuntimeError("drums are not configured")
        return self.drums.set_rate(rate)

    def set_drums_transport(self, running: bool) -> None:
        if self.drums is not None:
            self.drums.set_transport(running)

    def set_drums_bpm(self, bpm: float) -> None:
        if self.drums is not None:
            self.drums.set_bpm(bpm)

    def set_drums_meter(self, meter_num: int, meter_den: int) -> None:
        if self.drums is not None:
            self.drums.set_meter(meter_num, meter_den)

    # -- Stage 8.2 groove -------------------------------------------------

    def drums_groove_state(
        self,
    ) -> tuple[str, float, float, float, float, int, int] | None:
        """``(preset, density, sync, swing, probability, seed, grid_steps)``."""

        if self.drums is None:
            return None
        return self.drums.groove_state()

    def drums_grid(self) -> list[int] | None:
        if self.drums is None:
            return None
        return self.drums.grid_payload()

    def set_drums_preset(self, preset: str) -> object:
        if self.drums is None:
            raise RuntimeError("drums are not configured")
        return self.drums.set_preset(preset)

    def set_drums_groove(self, name: str, value: float) -> object:
        if self.drums is None:
            raise RuntimeError("drums are not configured")
        return self.drums.set_groove_modifier(name, value)

    def set_drums_seed(self, seed: int) -> object:
        if self.drums is None:
            raise RuntimeError("drums are not configured")
        return self.drums.set_seed(seed)

    def set_drums_step(self, lane: int, step: int, on: bool) -> bool:
        if self.drums is None:
            raise RuntimeError("drums are not configured")
        return self.drums.set_step(lane, step, on)

    def reset_drums_grid(self) -> object:
        if self.drums is None:
            raise RuntimeError("drums are not configured")
        return self.drums.reset_grid()

    # ------------------------------------------------------------------
    # Internals
    # ------------------------------------------------------------------

    def _apply(self, snapshot: tuple[list[float], float | None, float]) -> None:
        if not self._midi_enabled:
            return
        ratios, anchor, fundamental = snapshot
        if self._drone_enabled:
            self.drone.on_set(ratios, anchor, fundamental)
        if self._arp_enabled:
            self.arp.on_set(ratios, anchor, fundamental)


# ----------------------------------------------------------------------
# Validation helpers
# ----------------------------------------------------------------------


def _required(data: Mapping[str, Any], key: str) -> Any:
    if key not in data:
        raise ValueError(f"MPE config is missing required key {key!r}")
    return data[key]


def _nonempty_str(value: Any, label: str) -> str:
    if not isinstance(value, str) or not value.strip():
        raise TypeError(f"{label} must be a non-empty string")
    return value


def _int(value: Any, label: str) -> int:
    if isinstance(value, bool) or not isinstance(value, int):
        raise TypeError(f"{label} must be an integer")
    return value


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


def _offsets(value: Any, label: str) -> tuple[int, int, int]:
    if isinstance(value, (str, bytes)):
        raise TypeError(f"{label} must contain three integers")
    try:
        values = list(value)
    except TypeError:
        raise TypeError(f"{label} must contain three integers") from None
    if len(values) != 3:
        raise ValueError(f"{label} must contain exactly three integers")
    cleaned: list[int] = []
    for item in values:
        number = _int(item, label)
        if not -8 <= number <= 8:
            raise ValueError(f"{label} offsets must be between -8 and 8")
        cleaned.append(number)
    return cleaned[0], cleaned[1], cleaned[2]
