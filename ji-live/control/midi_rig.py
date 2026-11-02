"""MIDI rig orchestrator for Stage 6 MPE, 8.1 drums, 8.3a bass, 8.3d lead.

The rig owns the monophonic MPE outputs and, optionally, one plain drum output
and one plain bass output::

    DroneRig (hold/pulse/bend) -> Bus 1 -> Ableton "Drone" MIDI track
    SetArp                    -> Bus 2 -> Ableton "Arp"   MIDI track
    DrumRig                   -> Bus 3 -> Ableton "Drums" MIDI track  (optional)
    BassRig                   -> Bus 4 -> Ableton "Bass"/"Sub" tracks (optional)
    LeadRig                   -> Bus 5 -> Ableton "Lead"  MIDI track  (optional)

The lead is independent of ``--midi`` exactly like the bass: a missing Bus 5
disables only the lead, and ``lead=False`` (the default) never opens it.

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
    from . import arp as arp_module
    from .arp import DEFAULT_ORDER, ArpSettings, SetArp
    from .bass import BassRig
    from .drone import DRONE_MODES as _DRONE_MODES
    from .drone import DroneConfig, DroneRig, DroneSettings
    from .drone import load_config as load_drone_config
    from .drums import DEFAULT_LANE_MASK, DRUM_RATE_DEFAULT, DrumRig
    from .lead import LeadRig, LeadSettings
    from .mpe_output import MPEOutput
except ImportError:  # pragma: no cover - exercised only by direct execution
    import arp as arp_module  # type: ignore[no-redef]
    from arp import DEFAULT_ORDER, ArpSettings, SetArp  # type: ignore[no-redef]
    from bass import BassRig  # type: ignore[no-redef]
    from drone import (  # type: ignore[no-redef]
        DRONE_MODES as _DRONE_MODES,
        DroneConfig,
        DroneRig,
        DroneSettings,
    )
    from drone import load_config as load_drone_config  # type: ignore[no-redef]
    from drums import (  # type: ignore[no-redef]
        DEFAULT_LANE_MASK,
        DRUM_RATE_DEFAULT,
        DrumRig,
    )
    from lead import LeadRig, LeadSettings  # type: ignore[no-redef]
    from mpe_output import MPEOutput  # type: ignore[no-redef]

DEFAULT_CONFIG_PATH = (
    Path(__file__).resolve().parent.parent / "config" / "mpe_config.json"
)


def drone_member_modes() -> str:
    """``hold|pulse|bend|sequence`` for the start-up log line."""

    return "|".join(_DRONE_MODES)


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
    arp_order: str
    arp_seed: int
    # Stage 8.3c: optional beat sync + self-evolution (older configs default).
    arp_sync: str = arp_module.DEFAULT_SYNC_MODE
    arp_division: str = arp_module.DEFAULT_DIVISION
    arp_evolve: bool = arp_module.DEFAULT_EVOLVE
    arp_evolve_beats: int = arp_module.DEFAULT_EVOLVE_BEATS
    arp_evolve_depth: float = arp_module.DEFAULT_EVOLVE_DEPTH

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
        # Stage 8.3b: additive.  An older config without "arp_order" keeps the
        # Stage 6 forward order instead of failing to load.
        order = _order(data.get("arp_order", DEFAULT_ORDER), "arp_order")
        seed = _int(_required(data, "arp_seed"), "arp_seed")
        # Stage 8.3c keys are additive: an 8.3b config loads as free/no-evolve.
        sync = arp_module.validate_sync_mode(
            data.get("arp_sync", arp_module.DEFAULT_SYNC_MODE)
        )
        division = arp_module.validate_division(
            data.get("arp_division", arp_module.DEFAULT_DIVISION)
        )
        evolve = arp_module.validate_evolve(
            data.get("arp_evolve", arp_module.DEFAULT_EVOLVE)
        )
        evolve_beats = arp_module.validate_evolve_beats(
            data.get("arp_evolve_beats", arp_module.DEFAULT_EVOLVE_BEATS)
        )
        evolve_depth = arp_module.validate_evolve_depth(
            data.get("arp_evolve_depth", arp_module.DEFAULT_EVOLVE_DEPTH)
        )

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
            arp_order=order,
            arp_seed=seed,
            arp_sync=sync,
            arp_division=division,
            arp_evolve=evolve,
            arp_evolve_beats=evolve_beats,
            arp_evolve_depth=evolve_depth,
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
        bass: Build the Stage 8.3a bass output (default ``False``).  Works with
            or without ``midi``/``drums``; a missing Bus 4 disables bass only.
        bass_config: A :class:`~control.bass.BassConfig`, mapping, path, or
            ``None`` (default ``config/bass_config.json``).
        bass_output: Optional fake output for the bass tests.
        lead: Build the Stage 8.3d lead output on Bus 5 (default ``False``).
            Works with or without ``midi``/``drums``/``bass``; a missing Bus 5
            disables the lead only and never raises from :meth:`start`.
        lead_config: A :class:`~control.lead.LeadConfig`, mapping, path, or
            ``None`` (default ``config/lead_config.json``).
        lead_output: Optional fake output for the lead tests.
        drone_config: A :class:`~control.drone.DroneConfig`, mapping, path, or
            ``None`` (default ``config/drone_config.json``).  It only supplies
            the drone mode/parameters; the bus stays ``mpe_config.drone_port``.
        bpm: Initial quarter-note tempo forwarded to the drone/lead.
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
        bass: bool = False,
        bass_config: Any | None = None,
        bass_output: Any | None = None,
        lead: bool = False,
        lead_config: Any | None = None,
        lead_output: Any | None = None,
        drone_config: Any | None = None,
        bpm: float = 120.0,
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
            # Stage 8.3d: the drone gains pulse/bend modes on top of the
            # untouched Stage 6 hold drone (self.drone keeps working).
            drone_cfg: Any | None = None
            try:
                if isinstance(drone_config, (str, Path)):
                    drone_cfg = load_drone_config(drone_config)
                else:
                    drone_cfg = drone_config
                self.drone_rig: DroneRig | None = DroneRig(
                    self.drone_output,
                    drone_cfg,
                    velocity=None if drone_cfg is not None else cfg.drone_velocity,
                    bpm=bpm,
                )
            except Exception as exc:  # a bad drone config keeps the hold drone
                print(
                    f"[DRONE] config failed: {exc}; using mode=hold",
                    file=sys.stderr,
                )
                self.drone_rig = DroneRig(
                    self.drone_output,
                    DroneConfig(),
                    velocity=cfg.drone_velocity,
                    bpm=bpm,
                )
            self.drone = self.drone_rig.anchor
            self.arp: SetArp | None = SetArp(
                self.arp_output,
                step_seconds=cfg.step_seconds,
                gate_seconds=cfg.gate_seconds,
                octave_offsets=cfg.arp_octave_offsets,
                shared_mode=cfg.arp_shared_mode,
                seed=cfg.arp_seed,
                velocity=cfg.arp_velocity,
                order=cfg.arp_order,
                sync_mode=cfg.arp_sync,
                division=cfg.arp_division,
                evolve=cfg.arp_evolve,
                evolve_beats=cfg.arp_evolve_beats,
                evolve_depth=cfg.arp_evolve_depth,
            )
        else:
            self.drone_output = None
            self.arp_output = None
            self.drone = None
            self.drone_rig = None
            self.arp = None

        self.drums: DrumRig | None = (
            DrumRig(drum_config, output=drum_output) if drums else None
        )

        # Stage 8.3a bass: independent of the MPE buses.  A bad bass config
        # disables only the bass; the rest of the rig keeps running.
        self.bass: BassRig | None = None
        if bass:
            try:
                self.bass = BassRig(bass_config, output=bass_output)
            except Exception as exc:
                print(
                    f"[MIDI] bass disabled: {exc}; drone/arp/drums continue",
                    file=sys.stderr,
                )
                self.bass = None

        # Stage 8.3d lead: independent of the MPE buses.  A bad lead config or
        # a missing Bus 5 disables only the lead.
        self.lead: LeadRig | None = None
        if lead:
            try:
                self.lead = LeadRig(lead_config, output=lead_output, bpm=bpm)
            except Exception as exc:
                print(
                    f"[LEAD] disabled: {exc}; drone/arp/drums/bass continue",
                    file=sys.stderr,
                )
                self.lead = None
        self._lead_requested = bool(lead)

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
            if self.drone_rig is not None:
                print(
                    f"[DRONE] modes={drone_member_modes()} "
                    f"mode={self.drone_rig.mode} "
                    "(pulse/bend/sequence wait for the transport)"
                )
            print(
                f"[MIDI] arp port={self.config.arp_port} "
                f"channel={self.config.member_channel} "
                f"step={self.config.step_seconds:.2f}s "
                f"gate={self.config.gate_seconds:.2f}s "
                f"order={self.config.arp_order} "
                f"sync={self.config.arp_sync}"
            )

        drum_ok = True
        if self.drums is not None:
            drum_ok = self.drums.start()

        bass_ok = True
        if self.bass is not None:
            bass_ok = self.bass.start()

        # Stage 8.3d lead: never fatal.  A missing Bus 5 only disables the lead.
        lead_ok = False
        if self.lead is not None:
            lead_ok = self.lead.start()

        try:
            if self._midi_enabled:
                if snapshot is not None:
                    self._apply(snapshot)
                self.drone_rig.start()
                if self._arp_enabled:
                    self.arp.start()
        except Exception:
            self._started = False
            if self.lead is not None and lead_ok:
                self.lead.stop()
            if self._midi_enabled:
                self.arp.stop()
                self.drone_rig.stop()
                self.drone_output.close()
                self.arp_output.close()
            if self.drums is not None and drum_ok:
                self.drums.stop()
            if self.bass is not None and bass_ok:
                self.bass.stop()
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

        if self.bass is not None:
            try:
                self.bass.stop()
            except Exception as exc:  # bass is non-critical
                print(f"[MIDI] bass stop failed: {exc}", file=sys.stderr)

        if self.lead is not None:
            try:
                self.lead.stop()
            except Exception as exc:  # lead is non-critical
                print(f"[MIDI] lead stop failed: {exc}", file=sys.stderr)

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
                self.drone_rig.stop()
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
                if self.drone_rig is not None:
                    self.drone_rig.on_set(ratios, anchor, fundamental)
                else:
                    self.drone.on_set(ratios, anchor, fundamental)
            else:
                print("[MIDI] drone enabled; waiting for the next set update")
            if self.drone_rig is not None:
                self.drone_rig.start()
        else:
            if self.drone_rig is not None:
                self.drone_rig.stop()
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
    # Stage 8.3b arp parameters
    # ------------------------------------------------------------------

    @property
    def arp_port_ok(self) -> bool:
        """True when the rig is started and the arp MIDI output is open."""

        output = self.arp_output
        if not self._midi_enabled or output is None:
            return False
        with self._lock:
            started = self._started
        return bool(started and getattr(output, "is_open", False))

    @property
    def arp_settings(self) -> ArpSettings:
        """Current arp settings (shipped defaults when the arp is absent)."""

        if self.arp is None:
            return ArpSettings()
        return self.arp.settings

    def arp_state(self) -> list[Any]:
        """``/ji/arp/state`` payload, always well-formed.

        With ``midi=False`` (or a missing Bus 2) this reports disabled and
        ``port_ok = 0`` so the GUI shows a waiting state without crashing.
        """

        enabled = self.arp_enabled
        if self.arp is None:
            return ArpSettings().state_args(False, False)
        return self.arp.state_args(enabled, self.arp_port_ok)

    def set_arp_setting(self, name: str, value: object) -> object:
        """Apply one arp setting; quantified/queued by the bridge, not here."""

        if self.arp is None or not self._midi_enabled:
            raise RuntimeError("arp is not configured (start with --midi)")
        return self.arp.set_setting(name, value)

    def set_arp_bpm(self, bpm: float) -> None:
        """Feed the bridge's canonical BPM to beat sync and the evolve clock."""

        if self.arp is not None:
            self.arp.set_bpm(bpm)

    def set_arp_transport(self, running: bool) -> None:
        if self.arp is not None:
            self.arp.set_transport(running)

    def sync_arp_phase(self, delay: float) -> None:
        """Snap the synced arp to a beat boundary ``delay`` seconds from now."""

        if self.arp is not None:
            self.arp.sync_phase(delay)

    def set_arp_settings(self, settings: ArpSettings) -> object:
        if self.arp is None or not self._midi_enabled:
            raise RuntimeError("arp is not configured (start with --midi)")
        return self.arp.set_settings(settings)

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
        # Stage 8.3a: the bass is independent of the MPE buses and must receive
        # snapshots even in bass-only mode (midi=False).
        if self.bass is not None:
            try:
                self.bass.on_set(snapshot[0], anchor, snapshot[2])
            except Exception as exc:  # bass is non-critical
                print(f"[BASS] set update failed: {exc}", file=sys.stderr)
        if self.lead is not None:
            try:
                self.lead.on_set(snapshot[0], anchor, snapshot[2])
            except Exception as exc:  # lead is non-critical
                print(f"[LEAD] set update failed: {exc}", file=sys.stderr)
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

    # -- Stage 8.3a bass --------------------------------------------------

    @property
    def bass_port(self) -> str:
        return "" if self.bass is None else self.bass.port

    @property
    def bass_enabled(self) -> bool:
        return False if self.bass is None else self.bass.enabled

    @property
    def bass_port_ok(self) -> bool:
        return False if self.bass is None else self.bass.port_ok

    def bass_state(self) -> list[Any] | None:
        """``/ji/bass/state`` payload, or ``None`` without a bass rig."""

        if self.bass is None:
            return None
        return self.bass.state_args()

    def set_bass_transport(self, running: bool) -> None:
        if self.bass is not None:
            self.bass.set_transport(running)

    def set_bass_bpm(self, bpm: float) -> None:
        if self.bass is not None:
            self.bass.set_bpm(bpm)

    def set_bass_meter(self, meter_num: int, meter_den: int) -> None:
        if self.bass is not None:
            self.bass.set_meter(meter_num, meter_den)

    def set_bass_setting(self, name: str, value: object) -> object:
        if self.bass is None:
            raise RuntimeError("bass is not configured")
        return self.bass.set_setting(name, value)

    def set_bass_enabled(self, enabled: bool) -> bool:
        if self.bass is None:
            return False
        self.bass.set_setting("enabled", enabled)
        return self.bass.enabled

    def set_bass_note_callback(self, callback: Any | None) -> None:
        if self.bass is not None:
            self.bass.set_note_callback(callback)

    # -- Stage 8.3d lead --------------------------------------------------

    @property
    def lead_port(self) -> str:
        return "" if self.lead is None else self.lead.port

    @property
    def lead_requested(self) -> bool:
        """True when the rig was built with ``lead=True`` (flag or config)."""

        return bool(getattr(self, "_lead_requested", False))

    @property
    def lead_enabled(self) -> bool:
        return False if self.lead is None else self.lead.enabled

    @property
    def lead_port_ok(self) -> bool:
        return False if self.lead is None else self.lead.port_ok

    @property
    def lead_playing(self) -> bool:
        return False if self.lead is None else self.lead.playing

    def lead_state(self) -> list[Any]:
        """``/ji/lead/state`` payload; always well-formed."""

        if self.lead is None:
            return LeadSettings().state_args(False, False)
        return self.lead.state_args()

    def set_lead_transport(self, running: bool) -> None:
        if self.lead is not None:
            self.lead.set_transport(running)

    def set_lead_bpm(self, bpm: float) -> None:
        if self.lead is not None:
            self.lead.set_bpm(bpm)

    def set_lead_meter(self, meter_num: int, meter_den: int) -> None:
        if self.lead is not None:
            self.lead.set_meter(meter_num, meter_den)

    def set_lead_setting(self, name: str, value: object) -> object:
        if self.lead is None:
            raise RuntimeError("lead is not configured (start with --lead)")
        return self.lead.set_setting(name, value)

    def set_lead_enabled(self, enabled: bool) -> bool:
        if self.lead is None:
            return False
        return self.lead.set_enabled(enabled)

    def vary_lead(self) -> object:
        if self.lead is None:
            raise RuntimeError("lead is not configured (start with --lead)")
        return self.lead.vary()

    def set_lead_groove_swing(self, swing: float) -> None:
        if self.lead is not None:
            self.lead.set_groove_swing(swing)

    def set_lead_note_callback(self, callback: Any | None) -> None:
        if self.lead is not None:
            self.lead.set_note_callback(callback)

    # -- Stage 8.3d drone modes -------------------------------------------

    @property
    def drone_port_ok(self) -> bool:
        if not self._midi_enabled or self.drone_output is None:
            return False
        with self._lock:
            started = self._started
        return bool(started and getattr(self.drone_output, "is_open", False))

    def drone_state(self) -> list[Any]:
        """``/ji/drone/state`` payload; always well-formed."""

        if self.drone_rig is None:
            return DroneSettings().state_args(False)
        return self.drone_rig.state_args(self.drone_port_ok)

    def set_drone_setting(self, name: str, value: object) -> object:
        if self.drone_rig is None or not self._midi_enabled:
            raise RuntimeError("drone is not configured (start with --midi)")
        return self.drone_rig.set_setting(name, value)

    def set_drone_bpm(self, bpm: float) -> None:
        if self.drone_rig is not None:
            self.drone_rig.set_bpm(bpm)

    def set_drone_transport(self, running: bool) -> None:
        if self.drone_rig is not None:
            self.drone_rig.set_transport(running)

    def set_drone_meter(self, meter_num: int, meter_den: int) -> None:
        """Forward the meter; the ``sequence`` note length is in bars."""

        if self.drone_rig is not None:
            self.drone_rig.set_meter(meter_num, meter_den)

    # ------------------------------------------------------------------
    # Internals
    # ------------------------------------------------------------------

    def _apply(self, snapshot: tuple[list[float], float | None, float]) -> None:
        if not self._midi_enabled:
            return
        ratios, anchor, fundamental = snapshot
        if self._drone_enabled:
            if self.drone_rig is not None:
                self.drone_rig.on_set(ratios, anchor, fundamental)
            else:
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


def _order(value: Any, label: str) -> str:
    """Validate a Stage 8.3b arp order name."""

    return arp_module.validate_order(value)

