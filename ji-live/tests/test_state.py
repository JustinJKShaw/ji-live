#!/usr/bin/env python3
"""Stage 8.5 performance-state tests: format, library, bridge recall, UI.

Run from ``ji-live/``::

    uv run --with python-osc python tests/test_state.py

No audio, MIDI, Qt, or real sockets: the bridge is built with a fake UDP client,
a fake monotonic clock drives the beat queue, and the voices are real engines
driven through fake MIDI outputs.  The live rig (sclang, Ableton, IAC, the GUI)
remains a human check.
"""

from __future__ import annotations

import contextlib
import io
import json
import math
import shutil
import sys
import tempfile
import traceback
from dataclasses import replace
from pathlib import Path
from types import SimpleNamespace

ROOT = Path(__file__).resolve().parent.parent
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

import control.ji_bridge as bridge_module
from control import state as state_module
from control.arp import ArpSettings, SetArp
from control.bass import BassConfig, BassRig, BassSettings
from control.drone import DroneConfig, DroneRig, DroneSettings
from control.drums import DEFAULT_LANE_MASK, DrumConfig, DrumEngine
from control.groove import GrooveSpec, preset_grid
from control.lead import LeadConfig, LeadRig, LeadSettings
from control.mpe_output import MPEOutput
from control.mutation import MutationEngine, SetSnapshot
from control.osc_server import (
    STATE_DELETE_ADDRESS,
    STATE_LIST_ADDRESS,
    STATE_LOAD_ADDRESS,
    STATE_REQUEST_ADDRESS,
    STATE_SAVE_ADDRESS,
    STATE_STATE_ADDRESS,
    MutationOSCServer,
)
from control.rhythm import RhythmEngine, RhythmState
from ui import osc_control

INITIAL = [1.0, 9.0 / 8.0, 5.0 / 4.0, 4.0 / 3.0, 3.0 / 2.0, 5.0 / 3.0, 15.0 / 8.0]


# ---------------------------------------------------------------------------
# Fixtures
# ---------------------------------------------------------------------------


class FakeClock:
    def __init__(self, now: float = 100.0) -> None:
        self.now = float(now)

    def __call__(self) -> float:
        return self.now

    def advance(self, seconds: float) -> None:
        self.now += float(seconds)


class FakeClient:
    """Minimal SimpleUDPClient stand-in (records every message)."""

    instances: list["FakeClient"] = []

    def __init__(self, host: str, port: int) -> None:
        self.host = host
        self.port = port
        self.sent: list[tuple[str, list]] = []
        FakeClient.instances.append(self)

    def send_message(self, address: str, args: list) -> None:
        self.sent.append((address, list(args)))

    def addresses(self) -> list[str]:
        return [address for address, _ in self.sent]


class FakePort:
    """Duck-typed MIDI output: records messages, opens no port."""

    def __init__(self) -> None:
        self.messages: list = []

    def send(self, message) -> None:
        self.messages.append(message)


class FakeDrums:
    """A real DrumEngine behind the DrumRig-shaped API the bridge calls."""

    def __init__(self) -> None:
        self.engine = DrumEngine(DrumConfig())
        self.port_ok = True

    @property
    def enabled(self) -> bool:
        return self.engine.enabled

    @property
    def rate(self) -> float:
        return self.engine.rate

    @property
    def lane_mask(self) -> int:
        return self.engine.lane_mask

    def set_enabled(self, enabled) -> bool:
        return self.engine.set_enabled(enabled)

    def set_lane(self, note, enabled) -> bool:
        return self.engine.set_lane(note, enabled)

    def set_rate(self, rate) -> float:
        return self.engine.set_rate(rate)

    def set_bpm(self, bpm) -> float:
        return self.engine.set_bpm(bpm)

    def set_meter(self, num, den) -> None:
        self.engine.set_meter(num, den)


class FakeStateRig:
    """MIDIRig-shaped stand-in with real voice engines and fake outputs."""

    def __init__(self, *, voices: bool = True, drums: bool = True) -> None:
        self.midi_enabled = True
        self.config = SimpleNamespace(
            drone_port="IAC Driver Bus 1", arp_port="IAC Driver Bus 2"
        )
        self.drums = FakeDrums() if drums else None
        self._voices = bool(voices)
        self.bass = BassRig(BassConfig(), output=FakePort()) if voices else None
        self.arp = (
            SetArp(MPEOutput("IAC Driver Bus 2", output=FakePort(), label="arp"))
            if voices
            else None
        )
        self.lead = (
            LeadRig(LeadConfig(), output=FakePort(), bpm=120.0)
            if voices
            else None
        )
        self.drone_rig = (
            DroneRig(
                MPEOutput("IAC Driver Bus 1", output=FakePort(), label="drone"),
                DroneConfig(),
                bpm=120.0,
            )
            if voices
            else None
        )
        self.calls: list[tuple] = []

    # -- port probes ----------------------------------------------------
    @property
    def arp_port_ok(self) -> bool:
        return self.arp is not None

    @property
    def drone_port_ok(self) -> bool:
        return self.drone_rig is not None

    # -- drums ----------------------------------------------------------
    def drums_state(self) -> tuple[bool, bool, float, int]:
        if self.drums is None:
            return False, False, 1.0, DEFAULT_LANE_MASK
        return (
            self.drums.enabled,
            self.drums.port_ok,
            self.drums.rate,
            self.drums.lane_mask,
        )

    def set_drums_enabled(self, enabled: bool) -> bool:
        self.calls.append(("drums_enabled", bool(enabled)))
        return False if self.drums is None else self.drums.set_enabled(enabled)

    def set_drums_lane(self, note: int, enabled: bool) -> bool:
        self.calls.append(("drums_lane", int(note), bool(enabled)))
        if self.drums is None:
            raise RuntimeError("no drums")
        return self.drums.set_lane(note, enabled)

    def set_drums_rate(self, rate: float) -> float:
        self.calls.append(("drums_rate", float(rate)))
        if self.drums is None:
            raise RuntimeError("no drums")
        return self.drums.set_rate(rate)

    def set_drums_bpm(self, bpm: float) -> None:
        if self.drums is not None:
            self.drums.set_bpm(bpm)

    def set_drums_meter(self, num: int, den: int) -> None:
        if self.drums is not None:
            self.drums.set_meter(num, den)

    def set_drums_transport(self, running: bool) -> None:
        return None

    # -- voices ---------------------------------------------------------
    def set_bass_settings(self, settings: BassSettings):
        if self.bass is None:
            raise RuntimeError("bass is not configured")
        return self.bass.set_settings(settings)

    def set_arp_settings(self, settings: ArpSettings):
        if self.arp is None:
            raise RuntimeError("arp is not configured")
        return self.arp.set_settings(settings)

    def set_lead_settings(self, settings: LeadSettings):
        if self.lead is None:
            raise RuntimeError("lead is not configured")
        return self.lead.set_settings(settings)

    def set_lead_variation_index(self, index: int, force: bool = False):
        if self.lead is None:
            raise RuntimeError("lead is not configured")
        return self.lead.set_variation_index(index, force=force)

    def set_drone_settings(self, settings: DroneSettings):
        if self.drone_rig is None:
            raise RuntimeError("drone is not configured")
        return self.drone_rig.set_settings(settings)

    def set_lead_groove_swing(self, swing: float) -> None:
        if self.lead is not None:
            self.lead.set_groove_swing(swing)

    def start(self) -> None:
        return None

    def stop(self) -> None:
        return None

    # Transport/tempo broadcasts the bridge fans out; no-ops are enough here.
    def set_bass_bpm(self, bpm: float) -> None:
        return None

    def set_arp_bpm(self, bpm: float) -> None:
        return None

    def set_lead_bpm(self, bpm: float) -> None:
        return None

    def set_drone_bpm(self, bpm: float) -> None:
        return None

    def set_bass_meter(self, num: int, den: int) -> None:
        return None

    def set_lead_meter(self, num: int, den: int) -> None:
        return None

    def set_drone_meter(self, num: int, den: int) -> None:
        return None

    def set_drums_preset(self, preset: str):
        return None


@contextlib.contextmanager
def fake_bridge(*, rig: bool = True, state_dir: str | None = None, **kwargs):
    """A bridge with fake UDP clients, a fake clock, and a temp state dir."""

    directory = state_dir or tempfile.mkdtemp(prefix="ji-state-test-")
    created = state_dir is None
    original = bridge_module.SimpleUDPClient
    FakeClient.instances = []
    bridge_module.SimpleUDPClient = FakeClient
    bridge = None
    try:
        bridge = bridge_module.JIBridge(state_dir=directory, **kwargs)
        clock = FakeClock(100.0)
        rhythm = RhythmEngine(bpm=120.0, now=clock)
        bridge.rhythm = rhythm
        bridge.quantizer._rhythm = rhythm
        bridge.quantizer._now = clock
        if rig:
            bridge.midi = FakeStateRig()
            # The real bridge shares the drum engine's Groove; mirror that so
            # status reads and the engine never diverge.
            bridge.groove = bridge.midi.drums.engine.groove
        client, ui_client = FakeClient.instances
        yield bridge, client, ui_client, clock, rhythm
    finally:
        if bridge is not None:
            bridge.stop()
        bridge_module.SimpleUDPClient = original
        if created:
            shutil.rmtree(directory, ignore_errors=True)


def tick(bridge, clock, beats: int = 1) -> int:
    applied = 0
    for _ in range(beats):
        clock.advance(0.5)  # 120 BPM
        applied += bridge.quantizer.tick()
    return applied


def updates(client: FakeClient) -> list[list]:
    return [args for address, args in client.sent if address == "/ji/freqset/update"]


def messages(client: FakeClient, address: str) -> list[list]:
    return [args for where, args in client.sent if where == address]


def rich_state(name: str = "A-drone") -> state_module.PerformanceState:
    """A state that differs from every shipped default."""

    slot_a = SetSnapshot.create(
        [1.0, 9.0 / 8.0, 5.0 / 4.0, 3.0 / 2.0],
        anchor=5.0 / 4.0,
        octaves={1.0: -1, 5.0 / 4.0: 0, 3.0 / 2.0: 1},
    )
    slot_c = SetSnapshot.create(
        [1.0, 6.0 / 5.0, 3.0 / 2.0], anchor=1.0, octaves={6.0 / 5.0: 1}
    )
    freqset = SetSnapshot.create(
        [1.0, 9.0 / 8.0, 5.0 / 4.0, 4.0 / 3.0, 3.0 / 2.0, 5.0 / 3.0],
        anchor=5.0 / 4.0,
        octaves={1.0: -1, 9.0 / 8.0: 0, 5.0 / 4.0: 0, 3.0 / 2.0: 1},
    )
    grid = preset_grid("break", 7, 8)
    grid = grid.with_cell(0, 0, True).with_cell(15, 5, True).with_cell(4, 3, False)

    bass = BassSettings.create(
        enabled=True,
        grid="1/16",
        length_steps=12,
        density=0.4,
        syncopation=0.2,
        swing=0.3,
        gate_beats=2.0,
        accent=0.5,
        root_downbeat=False,
        pitch_mode="walk",
        movement=0.6,
        octave_range=2,
        octave_jump=0.4,
        base_octave=-1,
        seed=77,
        sub_enabled=True,
        sub_octaves=2,
    )
    bass = replace(bass, preset="custom")

    arp = ArpSettings.create(
        step_seconds=0.18,
        gate_seconds=0.09,
        velocity=77,
        octave_offsets=(0, 1, -1),
        shared_mode="random",
        order="pingpong",
        seed=33,
        sync_mode="beat",
        division="1/16",
        evolve=True,
        evolve_beats=3,
        evolve_depth=0.4,
    )
    lead = LeadSettings.create(
        enabled=True,
        grid="1/16",
        phrase_bars=3,
        density=0.5,
        gate_beats=1.25,
        velocity=88,
        octave_range=2,
        base_octave=-1,
        variation="invert",
        variation_bars=4,
        swing=0.15,
        groove_link=False,
        seed=44,
    )
    drone = DroneSettings.create(
        mode="sequence",
        velocity=100,
        pulse_notes=5,
        pulse_grid="1/8",
        pulse_gate=0.45,
        pulse_octave_span=2,
        slide_direction="up",
        slide_semitones=3,
        slide_seconds=0.5,
        hold_beats=3.0,
        seed=21,
        seq_notes=6,
        seq_bars="2",
        seq_gate=1.5,
    )

    return state_module.PerformanceState.create(
        name=name,
        snapshot=freqset,
        fundamental=432.0,
        saved_at="2026-11-02T02:00:00-05:00",
        mutation=state_module.MutationSettings.create(
            randomness=0.31,
            dissonance_bias=-0.2,
            stepwise_bias=0.45,
            temperature=0.6,
            anchor_mobility=0.8,
            change_count=2,
            glide_time=0.27,
            max_changes=4,
        ),
        home=state_module.HomeSection.create(
            slots=(slot_a, None, slot_c, None),
            active_slot=2,
            auto=True,
            every=5,
            seed=7,
            max_drift=3,
            return_glide=0.12,
            journey_seed=99,
            journey_mutations=4,
        ),
        rhythm=RhythmState.create(bpm=143.5, meter_num=7, meter_den=8),
        drums=state_module.DrumsSection.create(
            enabled=True, rate=2.0, lane_mask=0x1234
        ),
        groove=GrooveSpec.create(
            preset="break",
            density=0.4,
            syncopation=0.3,
            swing=0.2,
            probability=0.6,
            seed=12,
        ),
        grid=grid,
        bass=bass,
        arp=arp,
        lead=lead,
        lead_variation_index=3,
        drone=drone,
    )


def write_state(directory: Path, name: str, mapping: dict) -> Path:
    path = Path(directory) / f"{name}.json"
    path.write_text(json.dumps(mapping, indent=2), encoding="utf-8")
    return path


# ---------------------------------------------------------------------------
# 1. Names
# ---------------------------------------------------------------------------


def test_names() -> None:
    print("\n=== Test 1: name rules, normalisation, and case collisions ===")
    assert state_module.normalize_state_name("  A-drone  ") == "A-drone"
    assert state_module.validate_state_name(" A-drone ") == "A-drone"
    for good in ("A", "a", "4 to the floor", "set_1.2-x", "A" * 40, "Z9"):
        assert state_module.validate_state_name(good) == good
    bad = {
        "": "empty",
        "   ": "empty",
        ".hidden": "start",
        "a/b": "only use",
        "a\\b": "only use",
        "a..b": "..",
        "A" * 41: "40",
        "tab\tname": "control",
        "naïve": "only use",
    }
    for value, needle in bad.items():
        try:
            state_module.validate_state_name(value)
        except state_module.StateError as exc:
            assert exc.kind == "name"
            assert needle in str(exc), (value, exc)
        else:
            raise AssertionError(f"name {value!r} was accepted")

    name = state_module.StateName.create("  A-drone ")
    assert name.value == "A-drone"
    assert name.filename == "A-drone.json"
    assert name.key == "a-drone"
    assert str(name) == "A-drone"

    directory = Path(tempfile.mkdtemp(prefix="ji-name-"))
    try:
        library = state_module.StateLibrary(directory)
        library.save(state_module.PerformanceState.create("A-drone"))
        assert library.names() == ("A-drone",)
        # A name that differs only in case cannot become a second file.
        try:
            library.save(state_module.PerformanceState.create("a-drone"))
        except state_module.StateError as exc:
            assert "differ only in case" in str(exc)
        else:
            raise AssertionError("case-only duplicate was accepted")
        assert library.names() == ("A-drone",)
        # The typed-case lookup still finds it (macOS is case-insensitive).
        assert library.load("a-drone").name == "A-drone"
    finally:
        shutil.rmtree(directory, ignore_errors=True)
    print("  trimmed, bounded, path-safe; case-only duplicates refused")


# ---------------------------------------------------------------------------
# 2. Round-trip
# ---------------------------------------------------------------------------


def test_round_trip() -> None:
    print("\n=== Test 2: every in-scope field round-trips exactly ===")
    state = rich_state()
    mapping = state.to_mapping()
    assert mapping["format"] == "ji-performance-state"
    assert mapping["version"] == 1
    assert mapping["saved_at"] == "2026-11-02T02:00:00-05:00"
    assert mapping["name"] == "A-drone"
    assert "loop" not in mapping and "autotune" not in mapping
    assert "running" not in mapping["rhythm"]

    back = state_module.PerformanceState.from_mapping(mapping)
    assert back == state
    assert back.to_mapping() == mapping

    # Spot-check the sections that are easy to lose silently.
    assert back.freqset == state.freqset
    assert back.freqset.anchor == 5.0 / 4.0
    assert dict(back.freqset.octaves)[1.0] == -1
    assert back.fundamental == 432.0
    assert back.mutation.temperature == 0.6 and back.mutation.max_changes == 4
    assert back.home.slots[0] == state.home.slots[0]
    assert back.home.slots[1] is None and back.home.slots[3] is None
    assert back.home.slots[0].anchor == 5.0 / 4.0
    assert dict(back.home.slots[0].octaves)[1.0] == -1
    assert back.home.active_slot == 2
    assert back.home.auto and back.home.every == 5 and back.home.seed == 7
    assert back.home.max_drift == 3
    assert math.isclose(back.home.return_glide, 0.12)
    assert back.home.journey_seed == 99 and back.home.journey_mutations == 4
    assert back.rhythm.bpm == 143.5
    assert (back.rhythm.meter_num, back.rhythm.meter_den) == (7, 8)
    assert back.rhythm.running is False
    assert back.drums.enabled and back.drums.rate == 2.0
    assert back.drums.lane_mask == 0x1234
    assert back.groove.spec.preset == "break"
    assert back.groove.grid == state.groove.grid
    assert len(mapping["groove"]["grid"]) == 32
    assert back.bass == state.bass and back.bass.preset == "custom"
    assert back.arp == state.arp
    assert back.arp.octave_offsets == (0, 1, -1)
    assert back.lead == state.lead
    assert back.lead_variation_index == 3
    assert back.drone == state.drone
    assert back.drone.mode == "sequence" and back.drone.seq_notes == 6
    assert back.field_count() > 100
    print("  freqset/controls/home/rhythm/drums/groove/bass/arp/lead/drone + index")


# ---------------------------------------------------------------------------
# 3. Defaults, unknown keys, format gates
# ---------------------------------------------------------------------------


def test_defaults_and_errors() -> None:
    print("\n=== Test 3: defaults, unknown keys, bad format/version/values ===")
    minimal = {
        "format": state_module.STATE_FORMAT,
        "version": 1,
        "freqset": {"ratios": [1.0, 1.5], "anchor": 1.0},
        "loop": {"mode": "sync"},
        "autotune": {"retune": 0.1},
        "unknown": {"ignored": True},
    }
    state = state_module.PerformanceState.from_mapping(minimal, name="hand")
    assert state.name == "hand"
    assert state.freqset.ratios == (1.0, 1.5)
    assert state.fundamental == 220.0
    assert state.mutation.glide_time == 0.15
    assert state.mutation.max_changes == 3
    assert state.home.slots == (None, None, None, None)
    assert state.rhythm.bpm == 120.0 and state.rhythm.meter == "4/4"
    assert state.drums.enabled is False
    assert state.drums.lane_mask == DEFAULT_LANE_MASK
    assert state.groove.spec.preset == "four"
    assert state.bass == BassSettings()
    assert state.arp == ArpSettings()
    assert state.lead == LeadSettings()
    assert state.drone == DroneSettings()
    assert "loop" not in state.to_mapping()
    assert "autotune" not in state.to_mapping()

    # An older file with a missing groove grid gets the preset grid back.
    no_grid = dict(minimal)
    no_grid["groove"] = {"preset": "techno", "density": 0.2}
    recovered = state_module.PerformanceState.from_mapping(no_grid, name="old")
    assert recovered.groove.grid == preset_grid("techno", 4, 4)

    cases = [
        ({"format": "nope", "version": 1, "freqset": {"ratios": [1.0]}}, "format"),
        ({"version": 1, "freqset": {"ratios": [1.0]}}, "format"),
        ({"format": state_module.STATE_FORMAT, "version": 2,
          "freqset": {"ratios": [1.0]}}, "version"),
        ({"format": state_module.STATE_FORMAT, "version": 0,
          "freqset": {"ratios": [1.0]}}, "version"),
        ({"format": state_module.STATE_FORMAT, "version": 1}, "freqset"),
        ({"format": state_module.STATE_FORMAT, "version": 1,
          "freqset": {"ratios": []}}, "ratios"),
        ({"format": state_module.STATE_FORMAT, "version": 1,
          "freqset": {"ratios": [1.0], "anchor": 1.5}}, "anchor"),
        ({"format": state_module.STATE_FORMAT, "version": 1,
          "freqset": {"ratios": [1.0], "octaves": [[1.0, 9]]}}, "octave"),
        ({"format": state_module.STATE_FORMAT, "version": 1,
          "freqset": {"ratios": [1.0]}, "mutation": {"randomness": 2}},
         "randomness"),
        ({"format": state_module.STATE_FORMAT, "version": 1,
          "freqset": {"ratios": [1.0]}, "rhythm": {"meter_den": 3}},
         "denominator"),
        ({"format": state_module.STATE_FORMAT, "version": 1,
          "freqset": {"ratios": [1.0]}, "drums": {"rate": 3}}, "rate"),
        ({"format": state_module.STATE_FORMAT, "version": 1,
          "freqset": {"ratios": [1.0]},
          "home": {"slots": [None, None, None, None], "active_slot": 1}},
         "empty slot"),
        ({"format": state_module.STATE_FORMAT, "version": 1,
          "freqset": {"ratios": [1.0]}, "bass": {"grid": "1/3"}}, "bass"),
        ({"format": state_module.STATE_FORMAT, "version": 1,
          "freqset": {"ratios": [1.0]}, "lead": {"variation": "nope"}}, "lead"),
        ({"format": state_module.STATE_FORMAT, "version": 1,
          "freqset": {"ratios": [1.0]}, "groove": {"grid": [0, 0]}}, "grid"),
    ]
    for mapping, needle in cases:
        try:
            state_module.PerformanceState.from_mapping(mapping, name="bad")
        except state_module.StateError as exc:
            assert needle in str(exc), (mapping, exc)
        else:
            raise AssertionError(f"accepted invalid state: {mapping}")
    print("  defaults, preset-grid fallback, unknown keys ignored, bad values aborted")


# ---------------------------------------------------------------------------
# 4. Library
# ---------------------------------------------------------------------------


def test_library() -> None:
    print("\n=== Test 4: save/list/load/delete/.tmp/overwrite/read-only ===")
    directory = Path(tempfile.mkdtemp(prefix="ji-lib-"))
    try:
        library = state_module.StateLibrary(directory)
        assert library.names() == () and library.count() == 0
        try:
            library.load("nope")
        except state_module.StateError as exc:
            assert exc.kind == "missing"
        else:
            raise AssertionError("missing state loaded")

        first = rich_state("A-drone")
        path = library.save(first)
        assert path == directory / "A-drone.json"
        assert not list(directory.glob("*.tmp")), "no .tmp left after a save"
        assert library.names() == ("A-drone",)
        assert library.load("A-drone") == first

        # Saving over a name replaces it atomically.
        second = replace(first, fundamental=440.0)
        library.save(second)
        assert library.load("A-drone").fundamental == 440.0
        assert not list(directory.glob("*.tmp"))

        # Sorting is case-insensitive, then case-sensitive.
        library.save(state_module.PerformanceState.create("b mix"))
        library.save(state_module.PerformanceState.create("Blue"))
        assert library.names() == ("A-drone", "b mix", "Blue")

        assert library.delete("b mix") is True
        assert library.delete("b mix") is False
        assert library.names() == ("A-drone", "Blue")

        # A corrupt file is refused, not deleted, and does not break listing.
        write_state(directory, "corrupt", {"format": "nope"})
        try:
            library.load("corrupt")
        except state_module.StateError as exc:
            assert "not a ji-performance-state" in str(exc)
        else:
            raise AssertionError("corrupt state loaded")
        assert "corrupt" in library.names()

        # A read-only directory fails cleanly and leaves the old file intact.
        readonly = Path(tempfile.mkdtemp(prefix="ji-ro-"))
        try:
            read_lib = state_module.StateLibrary(readonly)
            keep = read_lib.save(state_module.PerformanceState.create("keep"))
            before = keep.read_text(encoding="utf-8")
            readonly.chmod(0o500)
            try:
                read_lib.save(state_module.PerformanceState.create("new"))
            except state_module.StateError as exc:
                assert exc.kind == "io"
            else:
                raise AssertionError("save into a read-only dir succeeded")
            assert keep.read_text(encoding="utf-8") == before
            assert read_lib.names() == ("keep",)
        finally:
            readonly.chmod(0o700)
            shutil.rmtree(readonly, ignore_errors=True)

        # A missing directory is created on the first save.
        nested = directory / "nested" / "deeper"
        assert not nested.exists()
        state_module.StateLibrary(nested).save(
            state_module.PerformanceState.create("first")
        )
        assert nested.is_dir()
    finally:
        shutil.rmtree(directory, ignore_errors=True)
    print("  atomic writes, overwrite, sort, delete no-op, corrupt/read-only safe")


# ---------------------------------------------------------------------------
# 5. Stopped load
# ---------------------------------------------------------------------------


def test_stopped_load() -> None:
    print("\n=== Test 5: stopped load is immediate: one update, one set change ===")
    with fake_bridge(seed=1) as (bridge, client, ui, clock, rhythm):
        state = rich_state()
        bridge.states.save(state)
        # Move away from the saved colour first.
        bridge.request_mutation()
        bridge._on_rhythm_tempo(90.0)
        bridge._on_rhythm_meter(4, 4)
        bridge._on_drums_preset("four")
        clock.now += 5.0
        client.sent.clear()
        ui.sent.clear()

        bridge.request_state_load("A-drone")
        sent = updates(client)
        assert len(sent) == 1, sent
        assert math.isclose(sent[0][1], state.mutation.glide_time), sent[0]
        assert bridge.quantizer.pending_count == 0
        assert len(messages(ui, "/ji/home/state")) == 1
        assert len(messages(ui, STATE_STATE_ADDRESS)) == 1
        assert len(messages(ui, STATE_LIST_ADDRESS)) == 1
        status = messages(ui, STATE_STATE_ADDRESS)[0]
        assert status == [1, "", "A-drone", "loaded"], status
        assert messages(ui, STATE_LIST_ADDRESS)[0] == [1, "A-drone"]
        assert bridge.engine.snapshot() == state.freqset
        print("  one /ji/freqset/update with the saved glide; one status each")


# ---------------------------------------------------------------------------
# 6. Running load
# ---------------------------------------------------------------------------


def test_running_load() -> None:
    print("\n=== Test 6: running load waits exactly one beat, then one update ===")
    with fake_bridge(seed=2) as (bridge, client, ui, clock, rhythm):
        bridge.states.save(rich_state())
        bridge._on_rhythm_transport(True)
        tick(bridge, clock)
        client.sent.clear()
        ui.sent.clear()

        bridge.request_state_load("A-drone")
        assert bridge.quantizer.pending_count == 1
        assert updates(client) == [], "nothing before the beat"
        assert ("state", "load") in bridge.quantizer.pending_keys

        applied = tick(bridge, clock)
        assert applied == 1
        assert bridge.quantizer.pending_count == 0
        assert len(updates(client)) == 1
        assert len(messages(ui, "/ji/home/state")) == 1
        assert len(messages(ui, STATE_STATE_ADDRESS)) == 1
        # The transport state is untouched: a state is a colour, not a play
        # command.
        assert bridge.rhythm.running is True
        print("  queued on PHASE_STATE; one beat, one update, transport unchanged")


def test_load_cancels_return() -> None:
    print("\n=== Test 7: a load cancels a pending return with one line ===")
    with fake_bridge(seed=3) as (bridge, client, ui, clock, rhythm):
        bridge.request_home_capture(0)
        bridge.states.save(rich_state())
        bridge._on_rhythm_transport(True)
        bridge.home.begin_return(0, 3)  # pretend a multi-beat return is in flight
        assert bridge.home.returning

        out = io.StringIO()
        with contextlib.redirect_stdout(out):
            bridge.request_state_load("A-drone")
            tick(bridge, clock)
        text = out.getvalue()
        assert "[STATE] return cancelled by state load" in text, text
        assert bridge.home.returning is False
        assert bridge.home.pending_batches == 0
        assert len(updates(client)) == 1
        print("  return state dropped before the recalled set lands")


# ---------------------------------------------------------------------------
# 8. Restored values
# ---------------------------------------------------------------------------


def test_restored_values() -> None:
    print("\n=== Test 8: every recalled value lands on the live rig ===")
    with fake_bridge(seed=4) as (bridge, client, ui, clock, rhythm):
        state = rich_state()
        bridge.states.save(state)
        rig = bridge.midi

        # Make the live rig different from the file first.
        bridge.request_mutation()
        bridge._on_rhythm_tempo(80.0)
        bridge._on_drums_preset("four")
        bridge.midi.bass.set_settings(BassSettings())
        bridge.midi.arp.set_settings(ArpSettings())
        bridge.midi.lead.set_settings(LeadSettings())
        bridge.midi.drone_rig.set_settings(DroneSettings())
        client.sent.clear()
        ui.sent.clear()

        bridge.request_state_load("A-drone")

        assert bridge.engine.snapshot() == state.freqset
        assert bridge.engine.anchor_ratio == state.freqset.anchor
        assert bridge.engine.fundamental == 432.0
        assert dict(bridge.engine.analysis_voicing)[1.0] == -1

        server = bridge.server
        assert math.isclose(server.randomness, 0.31)
        assert math.isclose(server.dissonance_bias, -0.2)
        assert math.isclose(server.stepwise_bias, 0.45)
        assert math.isclose(server.temperature, 0.6)
        assert math.isclose(server.anchor_mobility, 0.8)
        assert math.isclose(server.glide_time, 0.27)
        assert server.change_count == 2

        assert bridge.home.slots == state.home.slots
        assert bridge.home.active_slot == 2
        config = bridge.home.config
        assert config.auto and config.every == 5 and config.seed == 7
        assert config.max_drift == 3
        assert math.isclose(config.return_glide, 0.12)
        assert bridge.home.journey_seed == 99
        assert bridge.home.journey_mutations == 4

        assert bridge.rhythm.bpm == 143.5
        rhythm_state = bridge.rhythm.state
        assert (rhythm_state.meter_num, rhythm_state.meter_den) == (7, 8)
        assert bridge.rhythm.running is False

        drums = bridge.midi.drums
        assert drums.enabled is True
        assert drums.rate == 2.0
        assert drums.lane_mask == 0x1234
        assert bridge.groove.spec == state.groove.spec
        assert bridge.groove.grid == state.groove.grid

        assert rig.bass.engine.settings == state.bass
        assert rig.bass.engine.settings.preset == "custom"
        assert rig.arp.settings == state.arp
        assert rig.lead.engine.settings == state.lead
        assert rig.lead.engine.motif.variation_index == 3
        assert rig.drone_rig.settings == state.drone

        # Home state and the usual voice statuses are refreshed once each.
        for address in (
            "/ji/home/state",
            "/ji/rhythm/state",
            "/ji/drums/state",
            "/ji/drums/groove",
            "/ji/drums/grid",
            "/ji/bass/state",
            "/ji/arp/state",
            "/ji/lead/state",
            "/ji/drone/state",
        ):
            assert len(messages(ui, address)) == 1, (address, ui.addresses())
        print("  set, controls, home, rhythm, drums, groove, all four voices")


def test_load_is_idempotent() -> None:
    print("\n=== Test 9: loading the same file twice changes nothing ===")
    with fake_bridge(seed=5) as (bridge, client, ui, clock, rhythm):
        bridge.states.save(rich_state())
        bridge.request_state_load("A-drone")
        first = (
            bridge.engine.snapshot(),
            bridge.home.slots,
            bridge.home.active_slot,
            bridge.home.history_depth,
            bridge.groove.grid,
            bridge.midi.bass.engine.settings,
            bridge.midi.arp.settings,
            bridge.midi.lead.engine.settings,
            bridge.midi.drone_rig.settings,
            (bridge.rhythm.state.bpm, bridge.rhythm.state.meter_num,
             bridge.rhythm.state.meter_den),
        )
        client.sent.clear()
        bridge.request_state_load("A-drone")
        assert len(updates(client)) == 1, "still one update, not two"
        second = (
            bridge.engine.snapshot(),
            bridge.home.slots,
            bridge.home.active_slot,
            bridge.home.history_depth,
            bridge.groove.grid,
            bridge.midi.bass.engine.settings,
            bridge.midi.arp.settings,
            bridge.midi.lead.engine.settings,
            bridge.midi.drone_rig.settings,
            (bridge.rhythm.state.bpm, bridge.rhythm.state.meter_num,
             bridge.rhythm.state.meter_den),
        )
        assert first == second
        print("  same file twice: identical live state, one history entry")


# ---------------------------------------------------------------------------
# 10. Restart / library sharing
# ---------------------------------------------------------------------------


def test_restart_with_same_dir() -> None:
    print("\n=== Test 10: a restarted bridge loads a state saved by the first ===")
    directory = Path(tempfile.mkdtemp(prefix="ji-restart-"))
    original = bridge_module.SimpleUDPClient
    FakeClient.instances = []
    bridge_module.SimpleUDPClient = FakeClient
    first = second = None
    try:
        first = bridge_module.JIBridge(state_dir=str(directory))
        first.states.save(rich_state("persist-me"))
        assert first.states.names() == ("persist-me",)
        first.stop()
        first = None

        FakeClient.instances = []
        second = bridge_module.JIBridge(state_dir=str(directory))
        assert second.states.names() == ("persist-me",)
        second.request_state_load("persist-me")
        assert second.engine.snapshot() == rich_state().freqset
        print("  bridge B sees bridge A's file and recalls it")
    finally:
        for bridge in (first, second):
            if bridge is not None:
                bridge.stop()
        bridge_module.SimpleUDPClient = original
        shutil.rmtree(directory, ignore_errors=True)


# ---------------------------------------------------------------------------
# 11. Missing buses
# ---------------------------------------------------------------------------


def test_missing_bus_degrades_one_voice() -> None:
    print("\n=== Test 11: a missing bus degrades one voice with one line ===")
    with fake_bridge(seed=6) as (bridge, client, ui, clock, rhythm):
        bridge.states.save(rich_state())
        bridge.midi.bass = None  # Bus 4 disappears after the save
        out = io.StringIO()
        with contextlib.redirect_stdout(out):
            bridge.request_state_load("A-drone")
        text = out.getvalue()
        assert text.count("bass skipped") == 1, text
        assert "[STATE] loaded A-drone" in text
        # The rest of the state still landed.
        assert bridge.midi.arp.settings == rich_state().arp
        assert bridge.midi.lead.engine.settings == rich_state().lead
        assert bridge.midi.drone_rig.settings == rich_state().drone
        assert bridge.rhythm.bpm == 143.5
        assert len(updates(client)) == 1

    with fake_bridge(seed=7) as (bridge, client, ui, clock, rhythm):
        bridge.states.save(rich_state())
        bridge.midi.drums.port_ok = False
        out = io.StringIO()
        with contextlib.redirect_stdout(out):
            bridge.request_state_load("A-drone")
        text = out.getvalue()
        assert text.count("drums skipped") == 1, text
        assert bridge.midi.bass.engine.settings == rich_state().bass
        assert len(updates(client)) == 1
    print("  only the missing voice is skipped; everything else is recalled")


# ---------------------------------------------------------------------------
# 12. Exclusions / purity
# ---------------------------------------------------------------------------


def test_no_loop_or_autotune() -> None:
    print("\n=== Test 12: no loop or autotune keys are written or read ===")
    mapping = rich_state().to_mapping()
    text = json.dumps(mapping)
    assert "loop" not in text.lower()
    assert "autotune" not in text.lower()
    for section in mapping.values():
        if isinstance(section, dict):
            assert "loop" not in section
            assert "autotune" not in section

    # A loop/autotune key in a hand-edited file is ignored, not restored.
    data = dict(mapping)
    data["loop"] = {"mode": "sync"}
    data["autotune"] = {"retune": 0.5}
    loaded = state_module.PerformanceState.from_mapping(data, name="edited")
    assert "loop" not in loaded.to_mapping()
    assert "autotune" not in loaded.to_mapping()
    print("  loops and autotune are absent from the format, in and out")


def test_no_mido() -> None:
    print("\n=== Test 13: pure construction never imports mido ===")
    assert "mido" not in sys.modules
    state_module.PerformanceState.create("pure")
    with fake_bridge(rig=False, seed=8) as (bridge, client, ui, clock, rhythm):
        assert bridge.states.count() == 0
    assert "mido" not in sys.modules
    print("  state module and a no-MIDI bridge never import mido")


# ---------------------------------------------------------------------------
# 14. Invalid / corrupt files abort the load
# ---------------------------------------------------------------------------


def test_invalid_file_aborts() -> None:
    print("\n=== Test 14: a bad file changes nothing and logs one line ===")
    with fake_bridge(seed=9) as (bridge, client, ui, clock, rhythm):
        bridge.request_mutation()
        bridge._on_rhythm_tempo(110.0)
        before = (
            bridge.engine.snapshot(),
            bridge.rhythm.bpm,
            bridge.groove.grid,
            bridge.home.slots,
        )
        directory = bridge.states.directory
        # 1. Invalid value.
        mapping = rich_state().to_mapping()
        mapping["mutation"]["randomness"] = 2.0
        write_state(directory, "broken", mapping)
        client.sent.clear()
        out = io.StringIO()
        with contextlib.redirect_stdout(out):
            bridge.request_state_load("broken")
        text = out.getvalue()
        assert '[STATE] invalid state "broken":' in text, text
        assert updates(client) == []
        assert bridge.quantizer.pending_count == 0
        assert (
            bridge.engine.snapshot(),
            bridge.rhythm.bpm,
            bridge.groove.grid,
            bridge.home.slots,
        ) == before

        # 2. Wrong format.
        write_state(directory, "wrong", {"format": "x", "version": 1})
        out = io.StringIO()
        with contextlib.redirect_stdout(out):
            bridge.request_state_load("wrong")
        assert '[STATE] invalid state "wrong":' in out.getvalue()
        assert updates(client) == []

        # 3. Invalid JSON.
        (directory / "garbage.json").write_text("{nope", encoding="utf-8")
        out = io.StringIO()
        with contextlib.redirect_stdout(out):
            bridge.request_state_load("garbage")
        assert "invalid JSON" in out.getvalue()
        assert updates(client) == []

        # 4. Missing name.
        out = io.StringIO()
        with contextlib.redirect_stdout(out):
            bridge.request_state_load("ghost")
        text = out.getvalue()
        assert '[STATE] gap: no state named "ghost"' in text, text
        assert updates(client) == []
        status = messages(ui, STATE_STATE_ADDRESS)[-1]
        assert status[3] == "no such state", status
        print("  invalid value/format/JSON/missing: no update, no state change")


# ---------------------------------------------------------------------------
# 15. OSC surface
# ---------------------------------------------------------------------------


def test_osc_handlers() -> None:
    print("\n=== Test 15: /ji/state/* handlers and reply shapes ===")
    calls: list[tuple] = []
    engine = MutationEngine(INITIAL)
    server = MutationOSCServer(
        engine,
        on_state_save=lambda name: calls.append(("save", name)),
        on_state_load=lambda name: calls.append(("load", name)),
        on_state_delete=lambda name: calls.append(("delete", name)),
        on_state_request=lambda: calls.append(("request",)),
    )
    server._handle_state_save("/ji/state/save", "A-drone")
    server._handle_state_load("/ji/state/load", "B")
    server._handle_state_delete("/ji/state/delete", "C")
    server._handle_state_request("/ji/state/request")
    assert calls == [
        ("save", "A-drone"),
        ("load", "B"),
        ("delete", "C"),
        ("request",),
    ]

    # Arity and type errors are logged and never raise into the OSC thread.
    errors = io.StringIO()
    with contextlib.redirect_stderr(errors):
        server._handle_state_save("/ji/state/save")
        server._handle_state_save("/ji/state/save", "a", "b")
        server._handle_state_load("/ji/state/load", 1.5)
        server._handle_state_request("/ji/state/request", 1)
    text = errors.getvalue()
    assert "expects one state name" in text
    assert "expects a state name string" in text
    assert "takes no arguments" in text
    # The request handler warns about extras but still refreshes the list.
    assert calls[4:] == [("request",)], calls
    assert len(calls) == 5, "rejected name messages never reach the bridge"

    # A callback that raises is logged, not fatal.
    def explode(_name: str) -> None:
        raise RuntimeError("boom")

    server._on_state_save = explode
    errors = io.StringIO()
    with contextlib.redirect_stderr(errors):
        server._handle_state_save("/ji/state/save", "safe")
    assert "state save callback failed" in errors.getvalue()

    # The two reply shapes.
    with fake_bridge(seed=10) as (bridge, client, ui, clock, rhythm):
        bridge.request_state_save("A-drone")
        assert messages(ui, STATE_LIST_ADDRESS)[-1] == [1, "A-drone"]
        assert messages(ui, STATE_STATE_ADDRESS)[-1] == [
            1,
            "A-drone",
            "",
            "saved",
        ]
        bridge.request_state_load("A-drone")
        assert messages(ui, STATE_STATE_ADDRESS)[-1] == [
            1,
            "A-drone",
            "A-drone",
            "loaded",
        ]
        bridge.request_state_delete("A-drone")
        assert messages(ui, STATE_LIST_ADDRESS)[-1] == [0]
        assert messages(ui, STATE_STATE_ADDRESS)[-1] == [0, "", "", "deleted"]

        # Bad names never reach the file system and still reply.
        bridge.request_state_save("..")
        assert messages(ui, STATE_LIST_ADDRESS)[-1] == [0]
        status = messages(ui, STATE_STATE_ADDRESS)[-1]
        assert status[0] == 0 and "invalid state" in status[3], status
        assert bridge.states.names() == ()

        # Delete of a name that is gone is one logged no-op.
        out = io.StringIO()
        with contextlib.redirect_stdout(out):
            bridge.request_state_delete("A-drone")
        assert '[STATE] gap: no state named "A-drone"' in out.getvalue()
        print("  arity/type/soft callbacks, list/state shapes, invalid names")


# ---------------------------------------------------------------------------
# 16. UI layer
# ---------------------------------------------------------------------------


def test_ui_layer() -> None:
    print("\n=== Test 16: UI builders, parser, store, and status line ===")
    assert osc_control.STATE_SAVE == "/ji/state/save"
    assert osc_control.STATE_LIST == "/ji/state/list"
    assert osc_control.build_state_save("  A-drone ") == (
        "/ji/state/save",
        ["A-drone"],
    )
    assert osc_control.build_state_load("A") == ("/ji/state/load", ["A"])
    assert osc_control.build_state_delete("A") == ("/ji/state/delete", ["A"])
    assert osc_control.build_state_request() == ("/ji/state/request", [])
    try:
        osc_control.build_state_save("   ")
    except ValueError:
        pass
    else:
        raise AssertionError("empty name accepted by the UI builder")

    names = osc_control.parse_state_list([2, "A-drone", "b mix"])
    assert names == ("A-drone", "b mix")
    try:
        osc_control.parse_state_list([2, "only-one"])
    except ValueError:
        pass
    else:
        raise AssertionError("mismatched state list accepted")

    empty = osc_control.parse_state_status([0, "", "", ""])
    assert empty.status_line() == "State: none saved"
    saved = osc_control.parse_state_status([3, "A-drone", "b mix", "loaded"])
    assert saved.status_line() == (
        "State: 3 saved | saved A-drone | loaded b mix"
    )
    failed = osc_control.parse_state_status(
        [3, "A-drone", "b mix", "invalid state: bad anchor"]
    )
    assert failed.is_error
    assert failed.status_line() == "State: error invalid state: bad anchor"
    try:
        osc_control.parse_state_status([1, "A"])
    except ValueError:
        pass
    else:
        raise AssertionError("short state status accepted")

    store = osc_control.StatusStore()
    assert store.state is None and store.state_names == ()
    assert store.apply("/ji/state/list", [1, "A-drone"])
    assert store.state_names == ("A-drone",)
    assert store.apply("/ji/state/state", [1, "A-drone", "", "saved"])
    assert store.state is not None and store.state.count == 1
    assert store.bridge_seen

    with fake_bridge(seed=11) as (bridge, client, ui, clock, rhythm):
        bridge.server._handle_ui_hello("/ji/ui/hello")
        assert STATE_LIST_ADDRESS in ui.addresses()
        assert STATE_STATE_ADDRESS in ui.addresses()
    print("  builders, strict parsers, store, hello replies, status text")


# ---------------------------------------------------------------------------


def run() -> None:
    print("JI Stage 8.5 performance-state tests", flush=True)
    test_names()
    test_round_trip()
    test_defaults_and_errors()
    test_library()
    test_stopped_load()
    test_running_load()
    test_load_cancels_return()
    test_restored_values()
    test_load_is_idempotent()
    test_restart_with_same_dir()
    test_missing_bus_degrades_one_voice()
    test_no_loop_or_autotune()
    test_no_mido()
    test_invalid_file_aborts()
    test_osc_handlers()
    test_ui_layer()
    print("\nALL STATE TESTS PASSED")


if __name__ == "__main__":
    try:
        run()
    except Exception:
        traceback.print_exc()
        print("\nSTATE TESTS FAILED", file=sys.stderr)
        raise SystemExit(1)
