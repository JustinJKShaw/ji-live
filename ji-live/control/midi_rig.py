"""MIDI rig orchestrator for Stage 6: anchor drone + set arp.

The rig owns two independent monophonic MPE outputs::

    AnchorDrone -> IAC Driver Bus 1 -> Ableton "Drone" MIDI track
    SetArp      -> IAC Driver Bus 2 -> Ableton "Arp"   MIDI track

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
import threading
from collections.abc import Mapping
from dataclasses import dataclass
from pathlib import Path
from typing import Any

try:  # Support both ``python -m control.midi_rig`` and direct execution.
    from .arp import SetArp
    from .drone import AnchorDrone
    from .mpe_output import MPEOutput
except ImportError:  # pragma: no cover - exercised only by direct execution
    from arp import SetArp
    from drone import AnchorDrone
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
    """Own the Stage 6 drone and arp outputs and their threads.

    Args:
        config: An :class:`MPEConfig`, a mapping, a path, or ``None`` (use
            ``config_path`` or the default config).
        config_path: Alternative config path when ``config`` is ``None``.
        drone_output: Optional fake output for tests.
        arp_output: Optional fake output for tests.
    """

    def __init__(
        self,
        config: MPEConfig | Mapping[str, Any] | str | Path | None = None,
        *,
        config_path: str | Path | None = None,
        drone_output: Any | None = None,
        arp_output: Any | None = None,
    ) -> None:
        if config is None:
            config = config_path if config_path is not None else DEFAULT_CONFIG_PATH
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
        self.drone_output = MPEOutput(
            cfg.drone_port,
            member_channel=cfg.member_channel,
            bend_range_semitones=cfg.bend_range_semitones,
            velocity=cfg.drone_velocity,
            output=drone_output,
            label="drone",
        )
        self.arp_output = MPEOutput(
            cfg.arp_port,
            member_channel=cfg.member_channel,
            bend_range_semitones=cfg.bend_range_semitones,
            velocity=cfg.arp_velocity,
            output=arp_output,
            label="arp",
        )
        self.drone = AnchorDrone(self.drone_output, velocity=cfg.drone_velocity)
        self.arp = SetArp(
            self.arp_output,
            step_seconds=cfg.step_seconds,
            gate_seconds=cfg.gate_seconds,
            octave_offsets=cfg.arp_octave_offsets,
            shared_mode=cfg.arp_shared_mode,
            seed=cfg.arp_seed,
            velocity=cfg.arp_velocity,
        )

        self._lock = threading.Lock()
        self._started = False
        self._snapshot: tuple[list[float], float | None, float] | None = None

    # ------------------------------------------------------------------
    # Lifecycle
    # ------------------------------------------------------------------

    def start(self) -> None:
        """Open both IAC ports and start the arp thread.

        The drone starts on the first :meth:`on_set`.  If a set snapshot was
        supplied before ``start``, it is applied immediately; otherwise the arp
        thread waits for the bridge's initial set update.
        """

        with self._lock:
            if self._started:
                return
            try:
                self.drone_output.open()
                self.arp_output.open()
            except Exception:
                self.drone_output.close()
                self.arp_output.close()
                raise
            self._started = True
            snapshot = self._snapshot

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

        try:
            if snapshot is not None:
                self._apply(snapshot)
            self.arp.start()
        except Exception:
            self._started = False
            self.arp.stop()
            self.drone.stop()
            self.drone_output.close()
            self.arp_output.close()
            raise

    def stop(self) -> None:
        """Stop the arp thread, send All Notes Off on both buses, close ports."""

        with self._lock:
            started = self._started
            self._started = False
            self._snapshot = None

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
        if started:
            self._apply(snapshot)

    update = on_set

    # ------------------------------------------------------------------
    # Internals
    # ------------------------------------------------------------------

    def _apply(self, snapshot: tuple[list[float], float | None, float]) -> None:
        ratios, anchor, fundamental = snapshot
        self.drone.on_set(ratios, anchor, fundamental)
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
