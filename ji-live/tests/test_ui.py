#!/usr/bin/env python3
"""Stage 7 GUI harness: pure OSC tests plus an optional headless Qt smoke test.

Run from ``ji-live/``.

Pure tests (no PyQt6, no sockets, no audio/MIDI)::

    uv run --with python-osc python tests/test_ui.py --pure

Optional offscreen Qt smoke test (needs PyQt6)::

    QT_QPA_PLATFORM=offscreen uv run --with python-osc --with PyQt6 \
      python tests/test_ui.py --qt

The Qt test uses a fake controller and never opens a real OSC port.  Neither
mode proves that the GUI controls the human's live rig; that is the Stage 7
human acceptance check.
"""

from __future__ import annotations

import argparse
import os
import shutil
import sys
import tempfile
import time
import traceback
from pathlib import Path
from types import SimpleNamespace

# Allow both ``python tests/test_ui.py`` and ``python -m tests.test_ui``.
_ROOT = Path(__file__).resolve().parent.parent
if str(_ROOT) not in sys.path:
    sys.path.insert(0, str(_ROOT))

import control.ji_bridge as bridge_module
from control.mutation import MutationEngine
from control.osc_server import (
    ARP_SET_GATE_ADDRESS,
    ARP_SET_OCTAVES_ADDRESS,
    ARP_SET_ORDER_ADDRESS,
    ARP_SET_SEED_ADDRESS,
    ARP_SET_SHARED_MODE_ADDRESS,
    ARP_SET_STEP_ADDRESS,
    ARP_SET_VELOCITY_ADDRESS,
    BASS_SET_DENSITY_ADDRESS,
    BASS_SET_ENABLED_ADDRESS,
    BASS_SET_HIT_ADDRESS,
    BASS_SET_HITS_ENABLED_ADDRESS,
    BASS_SET_PITCH_MODE_ADDRESS,
    BASS_SET_PRESET_ADDRESS,
    BASS_SET_SUB_OCTAVES_ADDRESS,
    BASS_CLEAR_HITS_ADDRESS,
    BASS_COPY_HITS_ADDRESS,
    BREATH_SET_ARP_LOCAL_ADDRESS,
    BREATH_SET_ARP_SYNC_ADDRESS,
    BREATH_SET_DEPTH_ADDRESS,
    BREATH_SET_ENABLED_ADDRESS,
    BREATH_SET_INTENSITY_ADDRESS,
    BREATH_SET_PERIOD_ADDRESS,
    BREATH_SET_PHASE_ADDRESS,
    BREATH_SET_SHAPE_ADDRESS,
    DRUMS_RESET_GRID_ADDRESS,
    DRUMS_SET_DENSITY_ADDRESS,
    DRUMS_SET_ENABLED_ADDRESS,
    DRUMS_SET_LANE_ADDRESS,
    DRUMS_SET_PRESET_ADDRESS,
    DRUMS_SET_PROBABILITY_ADDRESS,
    DRUMS_SET_RATE_ADDRESS,
    DRUMS_SET_SEED_ADDRESS,
    DRUMS_SET_STEP_ADDRESS,
    DRUMS_SET_SWING_ADDRESS,
    DRUMS_SET_SYNCOPATION_ADDRESS,
    MIDI_SET_ENABLED_ADDRESS,
    MutationOSCServer,
    RHYTHM_REQUEST_ADDRESS,
    RHYTHM_SET_METER_ADDRESS,
    RHYTHM_SET_TEMPO_ADDRESS,
    RHYTHM_TRANSPORT_ADDRESS,
    UI_HELLO_ADDRESS,
)
from ui import osc_control
from ui.osc_control import (
    ARP_DEFAULT_DIVISION,
    ARP_DEFAULT_EVOLVE,
    ARP_DEFAULT_OCTAVES,
    ARP_DIVISIONS,
    ARP_SYNC_MODES,
    ARP_DEFAULT_ORDER,
    ARP_DEFAULT_SEED,
    ARP_ORDERS,
    ARP_PARAMS,
    ARP_SHARED_MODES,
    AUTOTUNE_PARAMS,
    ArpStatus,
    DRONE_DEFAULT_MODE,
    DRONE_DEFAULT_PULSE_GRID,
    DRONE_DEFAULT_SEQ_BARS,
    DRONE_DEFAULT_SEQ_NOTES,
    DRONE_DEFAULT_PULSE_NOTES,
    DRONE_DEFAULT_PULSE_SPAN,
    LEAD_DEFAULT_BASE_OCTAVE,
    LEAD_DEFAULT_GRID,
    LEAD_DEFAULT_OCTAVE_RANGE,
    LEAD_DEFAULT_PHRASE_BARS,
    LEAD_DEFAULT_SEED,
    LEAD_DEFAULT_VARIATION,
    LEAD_DEFAULT_VARIATION_BARS,
    BASS_PRESET_DISPLAY,
    BASS_HITS_MAX,
    BassNoteStatus,
    BassStatus,
    BreathStatus,
    BREATH_DEFAULT_ARP_LOCAL,
    BREATH_DEFAULT_ARP_SYNC,
    BREATH_DEFAULT_DEPTH,
    BREATH_DEFAULT_INTENSITY,
    BREATH_DEFAULT_PERIOD_BARS,
    BREATH_DEFAULT_PHASE,
    BREATH_DEFAULT_SHAPE,
    BREATH_PERIOD_BARS_MAX,
    BREATH_PERIOD_BARS_MIN,
    BREATH_SHAPES,
    build_bass_hit,
    build_bass_hits_enabled,
    build_bass_clear_hits,
    build_bass_copy_hits,
    build_breath_param,
    parse_bass_hits,
    parse_breath_state,
    BridgeControlState,
    DRUM_LANE_DEFAULT_MASK,
    DrumStatus,
    FrequencySet,
    GrooveStatus,
    LoopConfig,
    MidiStatus,
    MidiToggleState,
    OSCController,
    QuantizeStatus,
    HOME_SLOT_NAMES,
    build_home_back,
    build_home_capture,
    build_home_clear,
    build_home_go,
    build_home_jump,
    build_home_return,
    build_state_delete,
    build_state_load,
    build_state_request,
    build_state_save,
    parse_state_list,
    parse_state_status,
    build_set_home_auto,
    build_set_home_drift,
    build_set_home_every,
    build_set_home_glide,
    build_set_home_seed,
    build_set_home_setting,
    parse_home_state,
    RhythmStatus,
    StatusStore,
    build_loop_clear,
    build_loop_dub,
    build_loop_fx_fire,
    build_loop_mem_clear,
    build_loop_mem_recall,
    build_loop_mem_save,
    build_loop_play,
    build_loop_record,
    build_loop_set_beats,
    build_loop_set_default_length,
    build_loop_set_length,
    build_loop_set_mode,
    build_loop_stop,
    build_loop_stop_all,
    build_loop_stop_record,
    build_set_loop_edit,
    build_request_state,
    build_set_arp_division,
    build_set_arp_evolve,
    build_set_arp_evolve_beats,
    build_set_arp_evolve_depth,
    build_set_arp_gate,
    build_set_arp_octaves,
    build_set_arp_sync,
    build_set_arp_order,
    build_set_arp_seed,
    build_set_arp_setting,
    build_set_arp_shared_mode,
    build_set_arp_step,
    build_set_arp_velocity,
    build_reset_drum_grid,
    build_rhythm_request,
    build_set_autotune,
    build_set_bass_base_octave,
    build_set_bass_enabled,
    build_set_bass_grid,
    build_set_bass_length,
    build_set_bass_octave_range,
    build_set_bass_param,
    build_set_bass_pitch_mode,
    build_set_bass_preset,
    build_set_bass_seed,
    build_set_bass_sub_enabled,
    build_set_bass_sub_octaves,
    build_set_drum_density,
    build_set_drum_lane,
    build_set_drum_probability,
    build_set_drum_rate,
    build_set_drum_seed,
    build_set_drum_step,
    build_set_drum_swing,
    build_set_drum_syncopation,
    build_set_drums_enabled,
    build_set_drums_preset,
    build_set_groove_modifier,
    build_set_meter,
    build_set_midi_enabled,
    build_set_mutation,
    build_set_tempo,
    build_transport,
    build_trigger_mutation,
    build_trigger_octave,
    build_ui_hello,
    drum_lane_mask,
    drum_lane_states,
    grid_steps_for_meter,
    parse_arp_state,
    parse_autotune_state,
    parse_bass_note,
    parse_bass_state,
    parse_cpu,
    parse_drums_state,
    parse_freqset,
    parse_grid,
    parse_groove,
    parse_loop_config,
    parse_loop_count,
    parse_loop_edit,
    parse_loop_state,
    parse_loop_mem,
    LoopEdit,
    LoopMemory,
    LOOP_COUNT_IN_OPTIONS,
    LOOP_DIVISION_LABELS,
    LOOP_DIVISIONS,
    LOOP_EDIT_PARAMS,
    LOOP_GRAIN_BURST_DEFAULT,
    LOOP_GRAIN_BREATH_DEFAULT,
    LOOP_GRAIN_BREATH_RATE_DEFAULT,
    LOOP_GRAIN_BREATH_RATE_MAX,
    LOOP_GRAIN_BREATH_RATE_MIN,
    LOOP_GRAIN_DEPTH_DEFAULT,
    LOOP_GRAIN_FILTER_DEFAULT,
    LOOP_GRAIN_FILTER_RATE_DEFAULT,
    LOOP_GRAIN_FILTER_RATE_MAX,
    LOOP_GRAIN_FILTER_RATE_MIN,
    LOOP_GRAIN_FREEZE_DEFAULT,
    LOOP_GRAIN_MELT_DEFAULT,
    LOOP_GRAIN_MORPH_DEFAULT,
    LOOP_GRAIN_MOTIONS,
    LOOP_GRAIN_MOTION_DEFAULT,
    LOOP_GRAIN_PITCH_DEFAULT,
    LOOP_GRAIN_PITCH_JITTER_DEFAULT,
    LOOP_GRAIN_PITCH_SNAP_DEFAULT,
    LOOP_GRAIN_POS_DEFAULT,
    LOOP_GRAIN_RATE_DEFAULT,
    LOOP_GRAIN_RATE_MAX,
    LOOP_GRAIN_SCATTER_DEFAULT,
    LOOP_GRAIN_SIZE_DEFAULT_MS,
    LOOP_GRAIN_SIZE_JITTER_DEFAULT,
    LOOP_GRAIN_SMEAR_DEFAULT,
    LOOP_GRAIN_SPEED_DEFAULT,
    LOOP_GRAIN_SPEED_MAX,
    LOOP_GRAIN_SPEED_MIN,
    LOOP_MEM_SLOTS,
    LOOP_MEM_SLOT_NAMES,
    LOOP_HARM_MODES,
    LOOP_HARM_MODE_DEFAULT,
    LOOP_HARM_MIX_DEFAULT,
    LOOP_HARM_TIME_MIN,
    LOOP_HARM_TIME_MAX,
    LOOP_HARM_TIME_DEFAULT,
    LOOP_HARM_TIME_SYNCS,
    LOOP_HARM_TIME_SYNC_DEFAULT,
    LOOP_HARM_STEPS_MIN,
    LOOP_HARM_STEPS_MAX,
    LOOP_HARM_STEPS_DEFAULT,
    LOOP_HARM_SPACING_MIN,
    LOOP_HARM_SPACING_MAX,
    LOOP_HARM_SPACING_DEFAULT,
    LOOP_HARM_FALLOFF_MIN,
    LOOP_HARM_FALLOFF_MAX,
    LOOP_HARM_FALLOFF_DEFAULT,
    LOOP_HARM_AUTO_DEFAULT,
    LOOP_HARM_MAX_SECONDS,
    harm_time_beats,
    harm_time_seconds,
    LOOP_RATES,
    LOOP_RATE_LABELS,
    LOOP_RATE_VALUES,
    ji_ratio_label,
    loop_rate_label,
    loop_rate_value,
    loop_snap_pitch,
    LOOP_STATES,
    parse_midi_status,
    parse_midi_state,
    parse_quantize,
    parse_rhythm_state,
    parse_ui_state,
)

INITIAL = [1.0, 9.0 / 8.0, 5.0 / 4.0, 4.0 / 3.0, 3.0 / 2.0, 5.0 / 3.0, 15.0 / 8.0]


# ---------------------------------------------------------------------------
# Fakes
# ---------------------------------------------------------------------------


class FakeClient:
    """Minimal SimpleUDPClient stand-in that records messages."""

    instances: list["FakeClient"] = []

    def __init__(self, host: str, port: int) -> None:
        self.host = host
        self.port = port
        self.sent: list[tuple[str, list]] = []
        self.closed = False
        FakeClient.instances.append(self)

    def send_message(self, address: str, args: list) -> None:
        if self.closed:
            raise RuntimeError("send after close")
        self.sent.append((address, list(args)))

    def close(self) -> None:
        self.closed = True

    def addresses(self) -> list[str]:
        return [address for address, _ in self.sent]


class FakeDrumRig:
    """Stand-in for DrumRig's state/mutations (no mido, no ports)."""

    def __init__(self) -> None:
        self.enabled = True
        self.port_ok = True
        self.rate = 1.0
        self.lane_mask = DRUM_LANE_DEFAULT_MASK
        self.config = SimpleNamespace(bpm=120.0, meter_num=4, meter_den=4)
        self.calls: list[tuple] = []

    def drums_state(self) -> tuple[bool, bool, float, int]:
        return self.enabled, self.port_ok, self.rate, self.lane_mask

    def set_enabled(self, enabled: bool) -> bool:
        self.enabled = bool(enabled)
        self.calls.append(("enabled", self.enabled))
        return True

    def set_lane(self, note: int, enabled: bool) -> bool:
        bit = note - 36
        if enabled:
            self.lane_mask |= 1 << bit
        else:
            self.lane_mask &= ~(1 << bit)
        self.calls.append(("lane", note, bool(enabled)))
        return True

    def set_rate(self, rate: float) -> float:
        self.rate = float(rate)
        self.calls.append(("rate", self.rate))
        return self.rate


class FakeMidiRig:
    """Stand-in for MIDIRig's per-bus switches (no mido, no ports)."""

    def __init__(self, drums: FakeDrumRig | None = None) -> None:
        self.config = SimpleNamespace(
            drone_port="IAC Driver Bus 1", arp_port="IAC Driver Bus 2"
        )
        self.drone_enabled = True
        self.arp_enabled = True
        self.midi_enabled = True
        self.drums = drums
        self.calls: list[tuple[str, bool]] = []
        self.drum_calls: list[tuple] = []

    def set_drone_enabled(self, enabled: bool) -> None:
        self.drone_enabled = bool(enabled)
        self.calls.append(("drone", bool(enabled)))

    def set_arp_enabled(self, enabled: bool) -> None:
        self.arp_enabled = bool(enabled)
        self.calls.append(("arp", bool(enabled)))

    def drums_state(self) -> tuple[bool, bool, float, int]:
        if self.drums is None:
            return False, False, 1.0, DRUM_LANE_DEFAULT_MASK
        return self.drums.drums_state()

    def set_drums_enabled(self, enabled: bool) -> bool:
        self.drum_calls.append(("enabled", bool(enabled)))
        if self.drums is None:
            return False
        return self.drums.set_enabled(enabled)

    def set_drums_lane(self, note: int, enabled: bool) -> bool:
        self.drum_calls.append(("lane", int(note), bool(enabled)))
        if self.drums is None:
            raise RuntimeError("no drums")
        return self.drums.set_lane(note, enabled)

    def set_drums_rate(self, rate: float) -> float:
        self.drum_calls.append(("rate", float(rate)))
        if self.drums is None:
            raise RuntimeError("no drums")
        return self.drums.set_rate(rate)

    def set_drums_transport(self, running: bool) -> None:
        self.drum_calls.append(("transport", bool(running)))

    def set_drums_bpm(self, bpm: float) -> None:
        self.drum_calls.append(("bpm", float(bpm)))

    def set_drums_meter(self, num: int, den: int) -> None:
        self.drum_calls.append(("meter", int(num), int(den)))


class FakeListener:
    def __init__(self, host: str, port: int, handler) -> None:
        self.host = host
        self.port = port
        self.handler = handler
        self.started = False
        self.stopped = False
        self.start_count = 0
        self.order: list[str] = []

    def start(self) -> None:
        self.started = True
        self.start_count += 1
        self.order.append("listen")

    def stop(self) -> None:
        self.stopped = True
        self.order.append("stop")


# ---------------------------------------------------------------------------
# Message builders
# ---------------------------------------------------------------------------


def test_mutation_builders() -> None:
    print("\n=== Test 1: mutation message builders ===")
    expected = {
        "randomness": "/ji/mutation/set_randomness",
        "dissonance_bias": "/ji/mutation/set_dissonance_bias",
        "stepwise_bias": "/ji/mutation/set_stepwise_bias",
        "temperature": "/ji/mutation/set_temperature",
        "anchor_mobility": "/ji/mutation/set_anchor_mobility",
        "change_count": "/ji/mutation/set_change_count",
        "glide_time": "/ji/mutation/set_glide_time",
    }
    for key, address in expected.items():
        built_address, args = build_set_mutation(key, 0.5)
        assert built_address == address, (key, built_address)
        assert len(args) == 1
        assert isinstance(args[0], (int, float))

    assert build_set_mutation("randomness", 1.5) == (
        "/ji/mutation/set_randomness",
        [1.0],
    )
    assert build_set_mutation("randomness", -0.5)[1] == [0.0]
    assert build_set_mutation("dissonance_bias", -2.0)[1] == [-1.0]
    assert build_set_mutation("dissonance_bias", 2.0)[1] == [1.0]
    assert build_set_mutation("stepwise_bias", 2.0)[1] == [1.0]
    assert build_set_mutation("temperature", 2.0)[1] == [1.0]
    assert build_set_mutation("anchor_mobility", 2.0)[1] == [1.0]
    assert build_set_mutation("glide_time", 5.0)[1] == [2.0]

    count_address, count_args = build_set_mutation(
        "change_count", 99, max_changes=5
    )
    assert count_address == "/ji/mutation/set_change_count"
    assert count_args == [5]
    assert isinstance(count_args[0], int)
    assert build_set_mutation("change_count", 0, max_changes=5)[1] == [1]
    assert build_set_mutation("change_count", 3.6, max_changes=5)[1] == [4]

    assert build_trigger_mutation() == ("/ji/mutation/trigger", [])
    assert build_trigger_octave() == ("/ji/mutation/octave", [])

    try:
        build_set_mutation("nope", 0.5)
    except ValueError:
        pass
    else:
        raise AssertionError("unknown mutation parameter was accepted")
    print("  addresses, types, change_count bounds, and clamping are correct")


def test_autotune_builders() -> None:
    print("\n=== Test 2: autotune message builders ===")
    for channel in (0, 1):
        for spec in AUTOTUNE_PARAMS:
            address, args = build_set_autotune(
                channel, spec.key, spec.default
            )
            assert address == "/ji/autotune/set"
            assert args[0] == channel and isinstance(args[0], int)
            assert args[1] == spec.key and isinstance(args[1], str)
            assert isinstance(args[2], float)

    assert build_set_autotune(0, "retuneTime", 0.0)[1] == [0, "retuneTime", 0.001]
    assert build_set_autotune(1, "retuneTime", 99.0)[1] == [1, "retuneTime", 2.0]
    assert build_set_autotune(0, "mix", -1.0)[1] == [0, "mix", 0.0]
    assert build_set_autotune(0, "windowSize", 0.0)[1] == [0, "windowSize", 0.02]
    assert build_set_autotune(0, "pitchRangeLimit", 12.6)[1] == [
        0,
        "pitchRangeLimit",
        13.0,
    ]
    assert build_set_autotune(1, "tartiniThreshold", 2.0)[1] == [
        1,
        "tartiniThreshold",
        1.0,
    ]
    assert build_set_autotune(1, "inputGain", 3.0)[1] == [1, "inputGain", 2.0]

    for bad in (
        lambda: build_set_autotune(2, "mix", 0.5),
        lambda: build_set_autotune(-1, "mix", 0.5),
        lambda: build_set_autotune(0, "arbitrary", 0.5),
        lambda: build_set_autotune(0, "freqBus", 1.0),
    ):
        try:
            bad()
        except ValueError:
            pass
        else:
            raise AssertionError("invalid autotune channel/param was accepted")
    print("  both channels, all six params, and whitelist checks are correct")


def test_loop_builders() -> None:
    print("\n=== Test 3: loop message builders ===")
    assert build_loop_record(1) == ("/ji/loop/record", [1])
    assert build_loop_record(1, 8.0) == (
        "/ji/loop/record_length",
        [1, 8.0],
    )
    assert build_loop_record(4, 0.0)[1] == [4, 1.0]
    assert build_loop_record(4, 99.0)[1] == [4, 30.0]
    assert unique_type(build_loop_record(3)[1][0]) is int

    for index in (1, 2, 3, 4):
        assert build_loop_stop_record(index) == (
            "/ji/loop/stop_record",
            [index],
        )
        assert build_loop_play(index) == ("/ji/loop/play", [index])
        assert build_loop_stop(index) == ("/ji/loop/stop", [index])
        assert build_loop_clear(index) == ("/ji/loop/clear", [index])

    assert build_loop_stop_all() == ("/ji/loop/stop_all", [])
    assert build_loop_set_default_length(8.0) == (
        "/ji/loop/set_default_length",
        [8.0],
    )
    assert build_loop_set_default_length(-1.0)[1] == [1.0]
    assert build_loop_set_default_length(100.0)[1] == [30.0]
    assert build_set_midi_enabled(True, False) == (
        "/ji/midi/set_enabled",
        [1, 0],
    )
    assert build_request_state() == ("/ji/status/query", [])
    assert build_ui_hello() == ("/ji/ui/hello", [])

    for bad_index in (0, 5, -2):
        for build in (
            lambda i=bad_index: build_loop_record(i),
            lambda i=bad_index: build_loop_play(i),
            lambda i=bad_index: build_loop_clear(i),
        ):
            try:
                build()
            except ValueError:
                pass
            else:
                raise AssertionError(f"invalid loop index {bad_index} accepted")
    print("  record/record_length/transport/clear/all/query builders are correct")


def test_loop_edit_builders_and_state() -> None:
    print("\n=== Test 3c: Stage 8.6 loop edit rates/builders/parsers ===")

    # -- the permitted ratios (§4.2) -------------------------------------
    assert LOOP_RATES == (
        ("2/1", 2.0),
        ("3/2", 1.5),
        ("5/3", 1.6666667),
        ("5/4", 1.25),
        ("4/3", 1.3333333),
        ("1/1", 1.0),
        ("3/4", 0.75),
        ("4/5", 0.8),
        ("2/3", 0.6666667),
        ("3/5", 0.6),
        ("1/2", 0.5),
    )
    assert len(LOOP_RATES) == 11
    assert LOOP_RATE_LABELS[0] == "2/1" and LOOP_RATE_LABELS[-1] == "1/2"
    assert LOOP_RATE_VALUES[5] == 1.0
    assert loop_rate_label(1.5) == "3/2"
    assert loop_rate_label(0.66) == "2/3"
    assert loop_rate_value("3/5") == 0.6
    for bad_rate in ("7/1", "", None):
        try:
            loop_rate_value(bad_rate)
        except ValueError:
            pass
        else:
            raise AssertionError(f"unknown rate {bad_rate!r} accepted")

    # -- divisions -------------------------------------------------------
    assert LOOP_DIVISIONS == (
        ("1/16", 0.0625),
        ("1/8", 0.125),
        ("1/4", 0.25),
        ("1/2", 0.5),
        ("1", 1.0),
    )
    assert LOOP_DIVISION_LABELS == ("1/16", "1/8", "1/4", "1/2", "1")
    assert LOOP_COUNT_IN_OPTIONS == ("off", "1 beat", "1 bar", "2 bars")
    assert LOOP_MEM_SLOTS == 4 and LOOP_MEM_SLOT_NAMES == ("A", "B", "C", "D")
    # Stage 8.6.1: 23 edit params, 5 motion names.
    assert LOOP_GRAIN_MOTIONS == (
        "off",
        "forward",
        "backward",
        "pingpong",
        "drift",
    )
    assert LOOP_GRAIN_MOTION_DEFAULT == "forward"
    assert len(LOOP_EDIT_PARAMS) == 37
    assert {spec.name for spec in LOOP_EDIT_PARAMS} == {
        "reverse",
        "rate",
        "gain",
        "mode",
        "sync",
        "division",
        "grain_rate",
        "grain_size",
        "grain_pos",
        "grain_scatter",
        "grain_pitch",
        "grain_size_jitter",
        "grain_pitch_jitter",
        "grain_pitch_snap",
        "grain_smear",
        "grain_freeze",
        "grain_motion",
        "grain_speed",
        "grain_depth",
        "grain_burst",
        "grain_morph",
        "grain_melt",
        "grain_filter",
        "grain_filter_rate",
        "grain_breath",
        "grain_breath_rate",
        "grain_reverse",
        "count_in",
        "auto_play",
        "harm_mode",
        "harm_mix",
        "harm_time",
        "harm_time_sync",
        "harm_steps",
        "harm_spacing",
        "harm_falloff",
        "harm_auto",
    }
    specs = {spec.name: spec for spec in LOOP_EDIT_PARAMS}
    # ranges / defaults / kinds of the new parameters (mirror sclang's clamps)
    assert specs["grain_rate"].low == 0.5
    assert specs["grain_rate"].high == LOOP_GRAIN_RATE_MAX == 80.0
    assert specs["grain_rate"].default == LOOP_GRAIN_RATE_DEFAULT == 20.0
    assert specs["grain_size"].default == LOOP_GRAIN_SIZE_DEFAULT_MS == 120.0
    assert specs["grain_pos"].default == LOOP_GRAIN_POS_DEFAULT == 0.5
    assert specs["grain_scatter"].default == LOOP_GRAIN_SCATTER_DEFAULT == 0.1
    assert specs["grain_speed"].low == LOOP_GRAIN_SPEED_MIN == 0.001
    assert specs["grain_speed"].high == LOOP_GRAIN_SPEED_MAX == 0.5
    assert specs["grain_speed"].default == LOOP_GRAIN_SPEED_DEFAULT == 0.03
    assert specs["grain_depth"].default == LOOP_GRAIN_DEPTH_DEFAULT == 0.6
    assert (
        specs["grain_size_jitter"].default
        == LOOP_GRAIN_SIZE_JITTER_DEFAULT
        == 0.2
    )
    assert (
        specs["grain_pitch_jitter"].default
        == LOOP_GRAIN_PITCH_JITTER_DEFAULT
        == 0.0
    )
    assert specs["grain_pitch_snap"].default == LOOP_GRAIN_PITCH_SNAP_DEFAULT == 1
    assert specs["grain_pitch_snap"].kind == "bool"
    assert specs["grain_smear"].default == LOOP_GRAIN_SMEAR_DEFAULT == 0.0
    assert specs["grain_freeze"].default == LOOP_GRAIN_FREEZE_DEFAULT == 0
    assert specs["grain_freeze"].kind == "bool"
    assert specs["grain_motion"].kind == "choice"
    assert specs["grain_motion"].choices == LOOP_GRAIN_MOTIONS
    assert specs["grain_motion"].default == "forward"
    assert specs["grain_burst"].default == LOOP_GRAIN_BURST_DEFAULT == 0.6
    assert specs["grain_pitch"].low == 0.25 and specs["grain_pitch"].high == 4.0
    assert specs["grain_morph"].default == LOOP_GRAIN_MORPH_DEFAULT == 0.0
    assert specs["grain_morph"].low == 0.0 and specs["grain_morph"].high == 1.0
    assert specs["grain_melt"].default == LOOP_GRAIN_MELT_DEFAULT == 0
    assert specs["grain_melt"].kind == "bool"
    assert specs["grain_filter"].default == LOOP_GRAIN_FILTER_DEFAULT == 0.0
    assert specs["grain_filter"].low == 0.0 and specs["grain_filter"].high == 1.0
    assert specs["grain_filter_rate"].low == LOOP_GRAIN_FILTER_RATE_MIN == 0.01
    assert specs["grain_filter_rate"].high == LOOP_GRAIN_FILTER_RATE_MAX == 2.0
    assert (
        specs["grain_filter_rate"].default
        == LOOP_GRAIN_FILTER_RATE_DEFAULT
        == 0.12
    )
    assert specs["grain_breath"].default == LOOP_GRAIN_BREATH_DEFAULT == 0.0
    assert specs["grain_breath"].low == 0.0 and specs["grain_breath"].high == 1.0
    assert specs["grain_breath_rate"].low == LOOP_GRAIN_BREATH_RATE_MIN == 0.02
    assert specs["grain_breath_rate"].high == LOOP_GRAIN_BREATH_RATE_MAX == 1.0
    assert (
        specs["grain_breath_rate"].default
        == LOOP_GRAIN_BREATH_RATE_DEFAULT
        == 0.08
    )
    # Stage 9.0 harmonic-series delay FX: exact ranges/defaults/kinds.
    assert specs["harm_mode"].kind == "choice"
    assert specs["harm_mode"].choices == LOOP_HARM_MODES
    assert specs["harm_mode"].default == LOOP_HARM_MODE_DEFAULT == "off"
    assert specs["harm_mix"].low == 0.0 and specs["harm_mix"].high == 1.0
    assert specs["harm_mix"].default == LOOP_HARM_MIX_DEFAULT == 0.35
    assert specs["harm_time"].low == LOOP_HARM_TIME_MIN == 0.05
    assert specs["harm_time"].high == LOOP_HARM_TIME_MAX == 2.0
    assert specs["harm_time"].default == LOOP_HARM_TIME_DEFAULT == 0.5
    assert specs["harm_time_sync"].kind == "choice"
    assert specs["harm_time_sync"].choices == LOOP_HARM_TIME_SYNCS
    assert specs["harm_time_sync"].default == LOOP_HARM_TIME_SYNC_DEFAULT == "free"
    assert specs["harm_steps"].kind == "int"
    assert specs["harm_steps"].low == LOOP_HARM_STEPS_MIN == 1
    assert specs["harm_steps"].high == LOOP_HARM_STEPS_MAX == 8
    assert specs["harm_steps"].default == LOOP_HARM_STEPS_DEFAULT == 4
    assert specs["harm_spacing"].low == LOOP_HARM_SPACING_MIN == 0.0
    assert specs["harm_spacing"].high == LOOP_HARM_SPACING_MAX == 1.0
    assert specs["harm_spacing"].default == LOOP_HARM_SPACING_DEFAULT == 0.08
    assert specs["harm_falloff"].low == LOOP_HARM_FALLOFF_MIN == 0.0
    assert specs["harm_falloff"].high == LOOP_HARM_FALLOFF_MAX == 1.0
    assert specs["harm_falloff"].default == LOOP_HARM_FALLOFF_DEFAULT == 0.5
    assert specs["harm_auto"].default == LOOP_HARM_AUTO_DEFAULT == 1
    assert specs["harm_auto"].kind == "bool"
    assert LOOP_HARM_MAX_SECONDS == 2.0

    # -- builders --------------------------------------------------------
    for index in (1, 2, 3, 4):
        assert build_loop_dub(index) == ("/ji/loop/dub", [index])
    for bad_index in (0, 5, -1):
        try:
            build_loop_dub(bad_index)
        except ValueError:
            pass
        else:
            raise AssertionError(f"invalid dub index {bad_index} accepted")

    assert build_set_loop_edit(1, "reverse", True) == (
        "/ji/loop/edit_set",
        [1, "reverse", 1],
    )
    assert build_set_loop_edit(1, "reverse", 0) == (
        "/ji/loop/edit_set",
        [1, "reverse", 0],
    )
    # rate snaps to the nearest permitted ratio
    assert build_set_loop_edit(2, "rate", 5.0)[1] == [2, "rate", 2.0]
    assert build_set_loop_edit(2, "rate", 0.1)[1] == [2, "rate", 0.5]
    assert build_set_loop_edit(2, "rate", 1.44)[1] == [2, "rate", 1.5]
    assert build_set_loop_edit(2, "rate", 0.6666667)[1] == [2, "rate", 0.6666667]
    assert build_set_loop_edit(1, "gain", 9)[1] == [1, "gain", 1.0]
    assert build_set_loop_edit(1, "gain", -1)[1] == [1, "gain", 0.0]
    assert build_set_loop_edit(1, "mode", "grain")[1] == [1, "mode", 1]
    assert build_set_loop_edit(1, "mode", "normal")[1] == [1, "mode", 0]
    assert build_set_loop_edit(1, "sync", "beat")[1] == [1, "sync", 1]
    assert build_set_loop_edit(1, "division", "1/8")[1] == [1, "division", "1/8"]
    assert build_set_loop_edit(1, "grain_rate", 99)[1] == [1, "grain_rate", 80.0]
    assert build_set_loop_edit(1, "grain_rate", 0)[1] == [1, "grain_rate", 0.5]
    assert build_set_loop_edit(1, "grain_size", 1000)[1] == [1, "grain_size", 500.0]
    assert build_set_loop_edit(1, "grain_size", 1)[1] == [1, "grain_size", 5.0]
    assert build_set_loop_edit(1, "grain_pos", 2)[1] == [1, "grain_pos", 1.0]
    assert build_set_loop_edit(1, "grain_scatter", -1)[1] == [
        1,
        "grain_scatter",
        0.0,
    ]
    assert build_set_loop_edit(1, "grain_pitch", 9)[1] == [1, "grain_pitch", 4.0]
    assert build_set_loop_edit(1, "grain_pitch", 0.01)[1] == [
        1,
        "grain_pitch",
        0.25,
    ]
    assert build_set_loop_edit(1, "grain_reverse", 0.5)[1] == [
        1,
        "grain_reverse",
        0.5,
    ]
    assert build_set_loop_edit(1, "count_in", "2 bars")[1] == [
        1,
        "count_in",
        "2 bars",
    ]
    assert build_set_loop_edit(1, "auto_play", True)[1] == [1, "auto_play", 1]
    assert build_set_loop_edit(1, "auto_play", False)[1] == [1, "auto_play", 0]

    # -- Stage 8.6.1 builders: clamps, choice validation, 0/1 ------------
    for motion in LOOP_GRAIN_MOTIONS:
        assert build_set_loop_edit(1, "grain_motion", motion)[1] == [
            1,
            "grain_motion",
            motion,
        ]
    for bad_motion in ("Forward", "bounce", "", 3):
        try:
            build_set_loop_edit(1, "grain_motion", bad_motion)
        except ValueError:
            pass
        else:
            raise AssertionError(f"bad motion {bad_motion!r} accepted")
    assert build_set_loop_edit(1, "grain_speed", 9)[1] == [
        1,
        "grain_speed",
        LOOP_GRAIN_SPEED_MAX,
    ]
    assert build_set_loop_edit(1, "grain_speed", 0)[1] == [
        1,
        "grain_speed",
        LOOP_GRAIN_SPEED_MIN,
    ]
    assert build_set_loop_edit(1, "grain_speed", 0.05)[1] == [
        1,
        "grain_speed",
        0.05,
    ]
    assert build_set_loop_edit(1, "grain_depth", 5)[1] == [1, "grain_depth", 1.0]
    assert build_set_loop_edit(1, "grain_depth", -5)[1] == [
        1,
        "grain_depth",
        0.0,
    ]
    assert build_set_loop_edit(1, "grain_size_jitter", 2)[1] == [
        1,
        "grain_size_jitter",
        1.0,
    ]
    assert build_set_loop_edit(1, "grain_pitch_jitter", -2)[1] == [
        1,
        "grain_pitch_jitter",
        0.0,
    ]
    assert build_set_loop_edit(1, "grain_smear", 9)[1] == [1, "grain_smear", 1.0]
    assert build_set_loop_edit(1, "grain_burst", 9)[1] == [1, "grain_burst", 1.0]
    assert build_set_loop_edit(1, "grain_morph", 2)[1] == [1, "grain_morph", 1.0]
    assert build_set_loop_edit(1, "grain_morph", -1)[1] == [
        1,
        "grain_morph",
        0.0,
    ]
    assert build_set_loop_edit(1, "grain_morph", 0.45)[1] == [
        1,
        "grain_morph",
        0.45,
    ]
    assert build_set_loop_edit(1, "grain_melt", True)[1] == [1, "grain_melt", 1]
    assert build_set_loop_edit(1, "grain_melt", False)[1] == [
        1,
        "grain_melt",
        0,
    ]
    assert build_set_loop_edit(1, "grain_filter", 9)[1] == [
        1,
        "grain_filter",
        1.0,
    ]
    assert build_set_loop_edit(1, "grain_filter_rate", 9)[1] == [
        1,
        "grain_filter_rate",
        LOOP_GRAIN_FILTER_RATE_MAX,
    ]
    assert build_set_loop_edit(1, "grain_filter_rate", 0)[1] == [
        1,
        "grain_filter_rate",
        LOOP_GRAIN_FILTER_RATE_MIN,
    ]
    assert build_set_loop_edit(1, "grain_breath", 9)[1] == [
        1,
        "grain_breath",
        1.0,
    ]
    assert build_set_loop_edit(1, "grain_breath", -1)[1] == [
        1,
        "grain_breath",
        0.0,
    ]
    assert build_set_loop_edit(1, "grain_breath_rate", 9)[1] == [
        1,
        "grain_breath_rate",
        LOOP_GRAIN_BREATH_RATE_MAX,
    ]
    assert build_set_loop_edit(1, "grain_breath_rate", 0)[1] == [
        1,
        "grain_breath_rate",
        LOOP_GRAIN_BREATH_RATE_MIN,
    ]
    for bit in (
        "grain_pitch_snap",
        "grain_freeze",
        "grain_melt",
        "harm_auto",
    ):
        assert build_set_loop_edit(1, bit, True)[1] == [1, bit, 1]
        assert build_set_loop_edit(1, bit, False)[1] == [1, bit, 0]
        assert build_set_loop_edit(1, bit, 7)[1] == [1, bit, 1]
        assert build_set_loop_edit(1, bit, 0)[1] == [1, bit, 0]

    # Stage 9.0 harm builders: modes are a closed vocabulary, numbers clamp,
    # harm_steps is an int on the wire.
    for mode in LOOP_HARM_MODES:
        assert build_set_loop_edit(2, "harm_mode", mode)[1] == [
            2,
            "harm_mode",
            mode,
        ]
    for bad_mode in ("Cascade", "on", "cascade ", 1):
        try:
            build_set_loop_edit(1, "harm_mode", bad_mode)
        except ValueError:
            pass
        else:
            raise AssertionError(f"bad harm mode {bad_mode!r} accepted")
    assert build_set_loop_edit(1, "harm_mix", 9)[1] == [1, "harm_mix", 1.0]
    assert build_set_loop_edit(1, "harm_mix", -1)[1] == [1, "harm_mix", 0.0]
    assert build_set_loop_edit(1, "harm_time", 9)[1] == [
        1,
        "harm_time",
        LOOP_HARM_TIME_MAX,
    ]
    assert build_set_loop_edit(1, "harm_time", 0)[1] == [
        1,
        "harm_time",
        LOOP_HARM_TIME_MIN,
    ]
    for sync in LOOP_HARM_TIME_SYNCS:
        assert build_set_loop_edit(1, "harm_time_sync", sync)[1] == [
            1,
            "harm_time_sync",
            sync,
        ]
    for bad_sync in ("1/64", "4/4", 0.25):
        try:
            build_set_loop_edit(1, "harm_time_sync", bad_sync)
        except ValueError:
            pass
        else:
            raise AssertionError(f"bad harm time sync {bad_sync!r} accepted")
    assert build_set_loop_edit(1, "harm_steps", 9)[1] == [1, "harm_steps", 8]
    assert build_set_loop_edit(1, "harm_steps", 0)[1] == [1, "harm_steps", 1]
    assert build_set_loop_edit(1, "harm_steps", 3.6)[1] == [1, "harm_steps", 4]
    assert isinstance(build_set_loop_edit(1, "harm_steps", 4)[1][2], int)
    assert build_set_loop_edit(1, "harm_spacing", 9)[1] == [
        1,
        "harm_spacing",
        LOOP_HARM_SPACING_MAX,
    ]
    assert build_set_loop_edit(1, "harm_falloff", -1)[1] == [
        1,
        "harm_falloff",
        LOOP_HARM_FALLOFF_MIN,
    ]
    # /ji/loop/fx_fire: UI -> sclang only, no bridge.
    assert build_loop_fx_fire(3) == ("/ji/loop/fx_fire", [3])
    for bad_index in (0, 5, -1, None):
        try:
            build_loop_fx_fire(bad_index)
        except (ValueError, TypeError):
            pass
        else:
            raise AssertionError(f"bad fx fire index {bad_index!r} accepted")

    for bad_param in ("tune", "mix", "reverse2", ""):
        try:
            build_set_loop_edit(1, bad_param, 1)
        except ValueError:
            pass
        else:
            raise AssertionError(f"unknown edit param {bad_param!r} accepted")
    for bad_count in ("3 bars", "off ", 7):
        try:
            build_set_loop_edit(1, "count_in", bad_count)
        except ValueError:
            pass
        else:
            raise AssertionError(f"bad count-in {bad_count!r} accepted")
    try:
        build_set_loop_edit(1, "division", "1/32")
    except ValueError:
        pass
    else:
        raise AssertionError("unknown division accepted")

    # memory builders: slots clamp, indices stay strict
    assert build_loop_mem_save(0, 1) == ("/ji/loop/mem_save", [0, 1])
    assert build_loop_mem_save(9, 4) == ("/ji/loop/mem_save", [3, 4])
    assert build_loop_mem_save(-3, 1) == ("/ji/loop/mem_save", [0, 1])
    assert build_loop_mem_recall(2, 3) == ("/ji/loop/mem_recall", [2, 3])
    assert build_loop_mem_recall(99, 1) == ("/ji/loop/mem_recall", [3, 1])
    assert build_loop_mem_clear(1) == ("/ji/loop/mem_clear", [1])
    assert build_loop_mem_clear(99) == ("/ji/loop/mem_clear", [3])
    for bad_index in (0, 5):
        try:
            build_loop_mem_save(0, bad_index)
        except ValueError:
            pass
        else:
            raise AssertionError(f"invalid mem index {bad_index} accepted")

    # -- LoopEdit defaults / helpers -------------------------------------
    default = LoopEdit()
    assert default.count_in == "off"
    assert default.auto_play == 0 and default.auto_plays is False
    assert default.is_grain is False and default.is_beat is False
    assert default.rate == 1.0 and default.rate_label == "1/1"
    assert default.gain == 0.7
    # Stage 8.6.1 cloud defaults
    assert default.grain_motion == "forward"
    assert default.grain_speed == 0.03
    assert default.grain_depth == 0.6
    assert default.grain_size_jitter == 0.2
    assert default.grain_pitch_jitter == 0.0
    assert default.grain_pitch_snap == 1 and default.is_snapped is True
    assert default.grain_smear == 0.0
    assert default.grain_freeze == 0 and default.is_frozen is False
    assert default.grain_rate == 20.0
    assert default.grain_size_ms == 120.0
    assert default.grain_pos == 0.5
    assert default.grain_scatter == 0.1
    assert default.grain_pitch == 1.0
    assert default.effective_pitch == 1.0
    assert default.pitch_label == "1/1"
    assert default.grain_morph == 0.0 and default.is_morphed is False
    assert default.grain_melt == 0 and default.is_melting is False
    assert default.grain_filter == 0.0
    assert default.grain_filter_rate == 0.12
    assert default.grain_breath == 0.0
    assert default.grain_breath_rate == 0.08
    # Stage 9.0 harm FX defaults (off, no clutter in the status line).
    assert default.harm_mode == LOOP_HARM_MODE_DEFAULT == "off"
    assert default.harm_mix == LOOP_HARM_MIX_DEFAULT == 0.35
    assert default.harm_time == LOOP_HARM_TIME_DEFAULT == 0.5
    assert default.harm_time_sync == LOOP_HARM_TIME_SYNC_DEFAULT == "free"
    assert default.harm_steps == LOOP_HARM_STEPS_DEFAULT == 4
    assert default.harm_spacing == LOOP_HARM_SPACING_DEFAULT == 0.08
    assert default.harm_falloff == LOOP_HARM_FALLOFF_DEFAULT == 0.5
    assert default.harm_auto == LOOP_HARM_AUTO_DEFAULT == 1
    assert default.is_harm is False
    assert "harm" not in default.status_line()
    assert LoopEdit(mode=1).is_grain is True
    assert LoopEdit(sync=1).is_beat is True
    # Stage 9.0 count-in rule: ceil to the next whole quarter beat.
    assert LoopEdit(index=3, count_in="off").count_in_beats(4) == 0
    assert LoopEdit(count_in="1 beat").count_in_beats(4) == 1
    assert LoopEdit(count_in="1 beat").count_in_beats(3) == 1
    assert LoopEdit(count_in="1 bar").count_in_beats(4) == 4
    assert LoopEdit(count_in="1 bar").count_in_beats(3) == 3
    assert LoopEdit(count_in="1 bar").count_in_beats(3.0) == 3
    assert LoopEdit(count_in="1 bar").count_in_beats(2.5) == 3
    assert LoopEdit(count_in="1 bar").count_in_beats(3.5) == 4
    assert LoopEdit(count_in="1 bar").count_in_beats(1.0) == 1
    assert LoopEdit(count_in="2 bars").count_in_beats(4) == 8
    assert LoopEdit(count_in="2 bars").count_in_beats(3) == 6
    assert LoopEdit(count_in="2 bars").count_in_beats(2.5) == 6
    assert LoopEdit(count_in="2 bars").count_in_beats(3.5) == 8

    # -- Stage 9.0 fixed musical time maths ------------------------------
    assert harm_time_beats("free") is None
    assert harm_time_beats("1/4") == 1.0
    assert harm_time_beats("1/4d") == 1.5
    assert abs(harm_time_beats("1/4t") - 2.0 / 3.0) < 1e-9
    assert harm_time_beats("1/8") == 0.5
    assert harm_time_beats("1/32") == 0.125
    assert harm_time_beats("1 bar", 4.0) == 4.0
    assert harm_time_beats("1 bar", 2.5) == 2.5
    assert abs(harm_time_seconds("free", 0.5) - 0.5) < 1e-9
    assert abs(harm_time_seconds("1/4", 0.5, 120) - 0.5) < 1e-9
    assert abs(harm_time_seconds("1/4d", 0.5, 120) - 0.75) < 1e-9
    assert abs(harm_time_seconds("1/4t", 0.5, 120) - (1.0 / 3.0)) < 1e-9
    assert abs(harm_time_seconds("1/4", 0.5, 60) - 1.0) < 1e-9
    assert abs(harm_time_seconds("1/2", 0.5, 120) - 1.0) < 1e-9
    assert abs(harm_time_seconds("1 bar", 0.5, 120, 4.0) - 2.0) < 1e-9
    # A 1 bar delay at 60 BPM would be 4 s; the 2 s FX buffer clamps it.
    assert harm_time_seconds("1 bar", 0.5, 60, 4.0) == LOOP_HARM_TIME_MAX
    # A very short division clamps up to the 0.05 s floor.
    assert harm_time_seconds("1/32t", 0.5, 120) == LOOP_HARM_TIME_MIN
    # The instance helper mirrors the module function.
    assert abs(
        LoopEdit(harm_time=0.4, harm_time_sync="1/8").harm_time_seconds(120)
        - 0.25
    ) < 1e-9
    harm_status = LoopEdit(
        harm_mode="cascade", harm_steps=4, harm_mix=0.35, harm_time=0.5
    ).status_line()
    assert "harm cascade" in harm_status
    assert "4×" in harm_status
    assert "0.50 s" in harm_status
    assert "mix 35 %" in harm_status
    harm_fixed = LoopEdit(
        harm_mode="delay", harm_time_sync="1/4", harm_time=0.9
    ).status_line(120, 4.0)
    assert "harm delay" in harm_fixed and "1/4" in harm_fixed
    assert "0.50 s" in harm_fixed, harm_fixed
    status = LoopEdit(
        index=2,
        reverse=1,
        rate=1.5,
        mode=1,
        sync=1,
        grain_pos=0.5,
        grain_motion="forward",
        effective_pitch=1.5,
        count_in="1 bar",
        auto_play=1,
    ).status_line()
    assert status.startswith("Loop 2")
    assert "×3/2" in status and "rev" in status
    assert "grain/beat" in status
    assert "pos 50 %" in status
    assert "motion forward" in status
    assert "pitch ×3/2" in status
    assert "snap" in status
    assert "count-in 1 bar" in status and "auto" in status
    assert "gain 0.70" in status
    assert "tune" not in status.lower()
    assert "bypass" not in status.lower()
    assert "freeze" not in status
    # snapped status shows the effective ratio, not the raw float
    snapped = LoopEdit(
        index=2, mode=1, grain_pitch=1.49, effective_pitch=1.5
    ).status_line()
    assert "pitch ×3/2" in snapped, snapped
    assert "1.49" not in snapped, snapped
    frozen = LoopEdit(mode=1, grain_freeze=1).status_line()
    assert "freeze" in frozen
    morphed = LoopEdit(mode=1, grain_morph=0.4, grain_filter=0.25).status_line()
    assert "morph 40 %" in morphed, morphed
    assert "melt" not in morphed, morphed
    melting = LoopEdit(mode=1, grain_morph=0.4, grain_melt=1).status_line()
    assert "melt" in melting, melting
    assert LoopEdit(mode=1, grain_melt=1).is_melting is True

    # -- Stage 8.6.1 JI snap / ratio labels ------------------------------
    set_ratios = [1.0, 9.0 / 8.0, 5.0 / 4.0, 4.0 / 3.0, 3.0 / 2.0, 5.0 / 3.0, 15.0 / 8.0]
    assert abs(loop_snap_pitch(1.5, set_ratios) - 1.5) < 1e-9
    assert abs(loop_snap_pitch(0.66, set_ratios) - 2.0 / 3.0) < 1e-6
    assert abs(loop_snap_pitch(2.0, set_ratios) - 2.0) < 1e-9
    assert abs(loop_snap_pitch(1.98, set_ratios) - 2.0) < 1e-9
    assert abs(loop_snap_pitch(0.5, set_ratios) - 0.5) < 1e-9
    assert abs(loop_snap_pitch(1.18, set_ratios) - 9.0 / 8.0) < 1e-9
    assert abs(loop_snap_pitch(1.26, set_ratios) - 5.0 / 4.0) < 1e-9
    # the cents midpoint between 9/8 and 5/4 is 1.1858, so 1.19 is 5/4
    assert abs(loop_snap_pitch(1.19, set_ratios) - 5.0 / 4.0) < 1e-9
    # out-of-range requests clamp into 0.25..4.0 first
    assert loop_snap_pitch(9.0, set_ratios) == 4.0
    assert loop_snap_pitch(0.01, set_ratios) == 0.25
    # a one-class set snaps to that class's octaves
    assert abs(loop_snap_pitch(1.4, [1.0]) - 1.0) < 1e-9
    assert abs(loop_snap_pitch(1.4, [1.5]) - 1.5) < 1e-9
    assert ji_ratio_label(1.5) == "3/2"
    assert ji_ratio_label(0.6666667) == "2/3"
    assert ji_ratio_label(2.0) == "2/1"
    assert ji_ratio_label(0.5) == "1/2"
    assert ji_ratio_label(1.125) == "9/8"
    assert ji_ratio_label(1.0) == "1/1"

    # -- strict /ji/loop/edit parser (39 values) -------------------------
    edit = parse_loop_edit(
        [
            2, 1, 1.5, 0.7, 1, 0, "1/8", 7.0, 120.0, 0.25, 0.1, 2.0,
            0.3, 0.1, 1, 0.4, 0, "pingpong", 0.05, 0.5, 0.7, 0.3,
            "1 bar", 1, 1.125, 0.5, 1, 0.3, 0.08, 0.4, 0.09,
            "cascade", 0.4, 0.6, "1/4d", 6, 0.12, 0.7, 0,
        ]
    )
    assert isinstance(edit, LoopEdit) and edit.index == 2
    assert edit.reverse == 1 and edit.rate == 1.5 and edit.gain == 0.7
    assert edit.is_grain is True and edit.is_beat is False
    assert edit.division == "1/8" and edit.grain_size_ms == 120.0
    assert edit.count_in == "1 bar" and edit.auto_play == 1
    assert edit.grain_size_jitter == 0.3 and edit.grain_pitch_jitter == 0.1
    assert edit.grain_pitch_snap == 1 and edit.grain_smear == 0.4
    assert edit.grain_freeze == 0 and edit.grain_motion == "pingpong"
    assert edit.grain_speed == 0.05 and edit.grain_depth == 0.5
    assert edit.grain_burst == 0.7 and edit.grain_reverse == 0.3
    assert edit.grain_pitch == 2.0 and edit.effective_pitch == 1.125
    assert edit.grain_morph == 0.5 and edit.grain_melt == 1
    assert edit.grain_filter == 0.3 and edit.grain_filter_rate == 0.08
    assert edit.grain_breath == 0.4 and edit.grain_breath_rate == 0.09
    assert edit.is_melting is True and edit.is_morphed is True
    assert edit.harm_mode == "cascade" and edit.harm_mix == 0.4
    assert edit.harm_time == 0.6 and edit.harm_time_sync == "1/4d"
    assert edit.harm_steps == 6 and edit.harm_spacing == 0.12
    assert edit.harm_falloff == 0.7 and edit.harm_auto == 0
    assert "pitch ×9/8" in edit.status_line()
    assert "morph 50 %" in edit.status_line()
    assert "breath 40 %" in edit.status_line()
    assert "melt" in edit.status_line()
    assert "harm cascade" in edit.status_line()
    # clamping
    clamped = parse_loop_edit(
        [
            1, 2, 9.0, 5.0, 3, 7, "1", 999.0, 9999.0, 4.0, -1.0, 0.1,
            9.0, -2.0, 3, 9.0, 5, "drift", 9.0, 9.0, -1.0, 2.0,
            "2 bars", 9, 9.0, 9.0, 3, 9.0, 0.0, 9.0, 0.0,
            "delay", 9.0, 9.0, "1/4d", 99, 9.0, 9.0, 9,
        ]
    )
    assert clamped.reverse == 1 and clamped.mode == 1 and clamped.sync == 1
    assert clamped.rate == 2.0 and clamped.gain == 1.0
    assert clamped.grain_rate == LOOP_GRAIN_RATE_MAX
    assert clamped.grain_size_ms == 500.0
    assert clamped.grain_pos == 1.0 and clamped.grain_scatter == 0.0
    assert clamped.grain_pitch == 0.25 and clamped.grain_reverse == 1.0
    assert clamped.grain_size_jitter == 1.0
    assert clamped.grain_pitch_jitter == 0.0
    assert clamped.grain_pitch_snap == 1 and clamped.grain_smear == 1.0
    assert clamped.grain_freeze == 1
    assert clamped.grain_speed == LOOP_GRAIN_SPEED_MAX
    assert clamped.grain_depth == 1.0 and clamped.grain_burst == 0.0
    assert clamped.auto_play == 1 and clamped.effective_pitch == 4.0
    assert clamped.grain_morph == 1.0 and clamped.grain_melt == 1
    assert clamped.grain_filter == 1.0
    assert clamped.grain_filter_rate == LOOP_GRAIN_FILTER_RATE_MIN
    assert clamped.grain_breath == 1.0
    assert clamped.grain_breath_rate == LOOP_GRAIN_BREATH_RATE_MIN
    assert clamped.harm_mode == "delay" and clamped.harm_mix == 1.0
    assert clamped.harm_time == LOOP_HARM_TIME_MAX
    assert clamped.harm_time_sync == "1/4d"
    assert clamped.harm_steps == LOOP_HARM_STEPS_MAX
    assert clamped.harm_spacing == LOOP_HARM_SPACING_MAX
    assert clamped.harm_falloff == LOOP_HARM_FALLOFF_MAX
    assert clamped.harm_auto == 1
    assert isinstance(clamped.harm_steps, int)
    base = [1, 0, 1.0, 0.7, 0, 0, "1/16", 8, 80, 0, 0, 1, 0, 0, 1, 0, 0,
            "forward", 0.03, 0.6, 0.6, 0, "off", 0, 1.0, 0.0, 0, 0.0, 0.12,
            0.0, 0.08, "off", 0.35, 0.5, "free", 4, 0.08, 0.5, 1]
    for bad_payload in (
        [],
        base[:-1],
        base + [1.0],
        base[:31],
        # unknown division
        base[:6] + ["1/32"] + base[7:],
        # unknown count-in
        base[:22] + ["3 bars"] + base[23:],
        # unknown motion
        base[:17] + ["bounce"] + base[18:],
        # unknown harm mode
        base[:31] + ["on"] + base[32:],
        # unknown harm time sync
        base[:34] + ["1/64"] + base[35:],
        # out-of-range index
        [9] + base[1:],
    ):
        try:
            parse_loop_edit(bad_payload)
        except ValueError:
            pass
        else:
            raise AssertionError(f"bad loop edit accepted: {bad_payload}")

    # -- strict /ji/loop/mem parser --------------------------------------
    mem = parse_loop_mem([2, 1, 384000, 8.0])
    assert isinstance(mem, LoopMemory)
    assert (mem.slot, mem.filled, mem.frames, mem.seconds) == (2, 1, 384000, 8.0)
    assert mem.name == "C" and mem.is_filled is True
    assert "8.0 s" in mem.status_line()
    empty = parse_loop_mem([0, 0, 0, 0.0])
    assert empty.is_filled is False and "empty" in empty.status_line()
    assert parse_loop_mem([9, 3, -5, -2.0]).slot == 3
    assert parse_loop_mem([9, 3, -5, -2.0]).frames == 0
    for bad_payload in ([], [0, 1, 2], [0, 1, 2, 3, 4]):
        try:
            parse_loop_mem(bad_payload)
        except ValueError:
            pass
        else:
            raise AssertionError(f"bad loop mem accepted: {bad_payload}")

    # -- /ji/loop/count parser -------------------------------------------
    assert parse_loop_count([1, 3]) == (1, 3)
    assert parse_loop_count([4, 0]) == (4, 0)
    assert parse_loop_count([2, 999]) == (2, 64)
    assert parse_loop_count([3, -4]) == (3, 0)
    for bad_payload in ([], [1], [1, 2, 3], [0, 2], [5, 2]):
        try:
            parse_loop_count(bad_payload)
        except ValueError:
            pass
        else:
            raise AssertionError(f"bad loop count accepted: {bad_payload}")

    # -- store application ------------------------------------------------
    store = StatusStore()
    assert store.loop_edits[1] == LoopEdit(index=1)
    assert all(not memory.is_filled for memory in store.loop_mem.values())
    assert store.loop_counts == {1: 0, 2: 0, 3: 0, 4: 0}
    assert store.apply(
        "/ji/loop/edit",
        [
            3, 1, 0.75, 0.5, 1, 1, "1/4", 12.0, 60.0, 0.5, 0.2, 0.8,
            0.3, 0.1, 1, 0.25, 0, "backward", 0.02, 0.4, 0.5, 0.4,
            "1 beat", 1, 0.8, 0.6, 0, 0.2, 0.25,
            0.5, 0.05,
            # Stage 9.0 harm fields
            "delay", 0.45, 0.75, "1/8t", 7, 0.2, 0.8, 0,
        ],
    )
    assert store.loop_edits[3].rate == 0.75
    assert store.loop_edits[3].division == "1/4"
    assert store.loop_edits[3].count_in == "1 beat"
    assert store.loop_edits[3].grain_motion == "backward"
    assert store.loop_edits[3].grain_speed == 0.02
    assert store.loop_edits[3].grain_size_jitter == 0.3
    assert store.loop_edits[3].grain_smear == 0.25
    assert store.loop_edits[3].grain_freeze == 0
    assert store.loop_edits[3].effective_pitch == 0.8
    assert store.loop_edits[3].grain_morph == 0.6
    assert store.loop_edits[3].grain_melt == 0
    assert store.loop_edits[3].grain_filter == 0.2
    assert store.loop_edits[3].grain_filter_rate == 0.25
    assert store.loop_edits[3].grain_breath == 0.5
    assert store.loop_edits[3].grain_breath_rate == 0.05
    assert store.loop_edits[3].harm_mode == "delay"
    assert store.loop_edits[3].harm_mix == 0.45
    assert store.loop_edits[3].harm_time == 0.75
    assert store.loop_edits[3].harm_time_sync == "1/8t"
    assert store.loop_edits[3].harm_steps == 7
    assert store.loop_edits[3].harm_spacing == 0.2
    assert store.loop_edits[3].harm_falloff == 0.8
    assert store.loop_edits[3].harm_auto == 0
    assert "morph 60 %" in store.loop_edits[3].status_line()
    assert "breath 50 %" in store.loop_edits[3].status_line()
    assert "motion backward" in store.loop_edits[3].status_line()
    assert "harm delay" in store.loop_edits[3].status_line()
    assert store.loop_edits[1] == LoopEdit(index=1), "other loops unchanged"
    assert store.apply("/ji/loop/mem", [1, 1, 96000, 2.0])
    assert store.loop_mem[1].is_filled and store.loop_mem[1].seconds == 2.0
    assert store.apply("/ji/loop/count", [3, 2])
    assert store.loop_counts[3] == 2
    assert store.apply("/ji/loop/state", [2, "dubbing"])
    assert store.loop_states[2] == "dubbing"
    assert store.apply("/ji/loop/state", [2, "counting"])
    assert store.loop_states[2] == "counting"
    assert "dubbing" in LOOP_STATES and "counting" in LOOP_STATES
    assert store.sclang_seen is True

    print("  rates/divisions/motions/builders, LoopEdit/LoopMemory, strict 39-value")
    print("  parsers, JI snap maths, ceil count-in, harm time and store application")


def unique_type(value) -> type:
    return int if isinstance(value, int) and not isinstance(value, bool) else float


def test_rhythm_drum_builders() -> None:
    print("\n=== Test 3b: rhythm / drum / loop-config message builders ===")
    assert build_set_tempo(120) == ("/ji/rhythm/set_tempo", [120.0])
    assert build_set_tempo(5)[1] == [20.0]
    assert build_set_tempo(1000)[1] == [300.0]
    assert build_set_meter(4, 4) == ("/ji/rhythm/set_meter", [4, 4])
    assert build_set_meter(99, 8) == ("/ji/rhythm/set_meter", [16, 8])
    try:
        build_set_meter(4, 3)
    except ValueError:
        pass
    else:
        raise AssertionError("unsupported meter denominator accepted")
    assert build_transport(True) == ("/ji/rhythm/transport", [1])
    assert build_transport(False) == ("/ji/rhythm/transport", [0])
    assert build_rhythm_request() == ("/ji/rhythm/request", [])

    assert build_set_drums_enabled(True) == ("/ji/drums/set_enabled", [1])
    assert build_set_drum_lane(36, False) == ("/ji/drums/set_lane", [36, 0])
    assert build_set_drum_lane(999, True) == ("/ji/drums/set_lane", [127, 1])
    assert build_set_drum_rate(0.5) == ("/ji/drums/set_rate", [0.5])
    assert build_set_drum_rate(2) == ("/ji/drums/set_rate", [2.0])
    try:
        build_set_drum_rate(1.5)
    except ValueError:
        pass
    else:
        raise AssertionError("unsupported drum rate accepted")

    assert build_set_drums_preset("Break") == ("/ji/drums/set_preset", ["break"])
    assert build_set_groove_modifier("density", 0.5) == (
        "/ji/drums/set_density",
        [0.5],
    )
    assert build_set_drum_density(2) == ("/ji/drums/set_density", [1.0])
    assert build_set_drum_syncopation(-1) == (
        "/ji/drums/set_syncopation",
        [0.0],
    )
    assert build_set_drum_swing(0.25) == ("/ji/drums/set_swing", [0.25])
    assert build_set_drum_probability(0.75) == (
        "/ji/drums/set_probability",
        [0.75],
    )
    assert build_set_drum_step(2, 5, True) == ("/ji/drums/set_step", [2, 5, 1])
    assert build_set_drum_step(15, 31, False) == (
        "/ji/drums/set_step",
        [15, 31, 0],
    )
    assert build_set_drum_seed(7) == ("/ji/drums/set_seed", [7])
    assert build_reset_drum_grid() == ("/ji/drums/reset_grid", [])
    assert grid_steps_for_meter(4, 4) == 16
    assert grid_steps_for_meter(7, 8) == 14
    assert grid_steps_for_meter(16, 2) == 32
    for bad in (
        lambda: build_set_drums_preset("nope"),
        lambda: build_set_groove_modifier("seed", 1),
        lambda: build_set_drum_step(16, 0, True),
        lambda: build_set_drum_step(0, 32, True),
        lambda: build_set_drum_seed(float("nan")),
    ):
        try:
            bad()
        except (TypeError, ValueError):
            pass
        else:
            raise AssertionError("invalid groove builder value accepted")

    assert build_loop_set_mode(1, "Beat") == ("/ji/loop/set_mode", [1, "beat"])
    assert build_loop_set_beats(2, 99) == ("/ji/loop/set_beats", [2, 64.0])
    assert build_loop_set_beats(2, 0) == ("/ji/loop/set_beats", [2, 1.0])
    assert build_loop_set_length(3, 0) == ("/ji/loop/set_length", [3, 1.0])
    assert build_loop_set_length(3, 99) == ("/ji/loop/set_length", [3, 30.0])
    for bad in (
        lambda: build_set_tempo(float("nan")),
        lambda: build_set_meter(4, 0),
        lambda: build_set_drum_rate(3),
        lambda: build_loop_set_mode(0, "beat"),
        lambda: build_loop_set_mode(1, "nope"),
        lambda: build_loop_set_beats(5, 4),
    ):
        try:
            bad()
        except (TypeError, ValueError):
            pass
        else:
            raise AssertionError("invalid rhythm/drum builder value accepted")

    assert drum_lane_states(0x0045)[36] is True
    assert drum_lane_states(0x0045)[38] is True
    assert drum_lane_states(0x0045)[42] is True
    assert drum_lane_states(0x0045)[37] is False
    assert drum_lane_mask({36: True, 38: True, 42: True}) == 0x0045
    print("  tempo/meter/transport/drums/loop-mode builders and lane masks")


# ---------------------------------------------------------------------------
# Status parsers
# ---------------------------------------------------------------------------


def test_bass_builders() -> None:
    print("\n=== Test 5b: Stage 8.3a bass builders/parsers ===")
    assert build_set_bass_enabled(True) == (BASS_SET_ENABLED_ADDRESS, [1])
    assert build_set_bass_enabled(0) == (BASS_SET_ENABLED_ADDRESS, [0])
    assert build_set_bass_preset("techno") == (BASS_SET_PRESET_ADDRESS, ["techno"])
    assert build_set_bass_grid("1/8") == (osc_control.BASS_SET_GRID, ["1/8"])
    assert build_set_bass_length(12) == (osc_control.BASS_SET_LENGTH, [12])
    assert build_set_bass_length(0) == (osc_control.BASS_SET_LENGTH, [1])
    assert build_set_bass_length(99) == (osc_control.BASS_SET_LENGTH, [32])
    assert build_set_bass_param("density", 0.4) == (BASS_SET_DENSITY_ADDRESS, [0.4])
    assert build_set_bass_param("gate_beats", 99) == (
        osc_control.BASS_SET_GATE,
        [8.0],
    )
    assert build_set_bass_pitch_mode("walk") == (
        BASS_SET_PITCH_MODE_ADDRESS,
        ["walk"],
    )
    assert build_set_bass_octave_range(2) == (
        osc_control.BASS_SET_OCTAVE_RANGE,
        [2],
    )
    assert build_set_bass_base_octave(-2) == (
        osc_control.BASS_SET_BASE_OCTAVE,
        [-2],
    )
    assert build_set_bass_seed(7) == (osc_control.BASS_SET_SEED, [7])
    assert build_set_bass_sub_enabled(True) == (
        osc_control.BASS_SET_SUB_ENABLED,
        [1],
    )
    assert build_set_bass_sub_octaves(2) == (BASS_SET_SUB_OCTAVES_ADDRESS, [2])
    for bad in (
        lambda: build_set_bass_preset("nope"),
        lambda: build_set_bass_grid("1/3"),
        lambda: build_set_bass_pitch_mode("arp"),
        lambda: build_set_bass_sub_octaves(3),
        lambda: build_set_bass_param("unknown", 1),
    ):
        try:
            bad()
        except (TypeError, ValueError):
            pass
        else:
            raise AssertionError(f"invalid bass builder accepted: {bad}")

    state = parse_bass_state(
        [1, 1, "techno", "1/16", 16, 0.55, 0.15, 0.1, 0.12, 0.25,
         "walk", 0.45, 1, 0.05, 4, -2, 0, 1, 2, 3]
    )
    assert isinstance(state, BassStatus)
    assert state.enabled and state.port_ok
    assert state.preset == "techno" and state.grid == "1/16"
    assert state.steps_per_beat == 4
    custom = parse_bass_state(
        [0, 1, "custom", "1/8", 8, 0.2, 0.0, 0.0, 0.5, 0.0,
         "root", 0.0, 0, 0.0, 1, -2, 1, 2, 2, 3]
    )
    assert custom.preset == "custom"
    assert custom.sub_enabled and custom.sub_octaves == 2
    assert parse_bass_state.__name__ == "parse_bass_state"

    note = parse_bass_note([55.0, 33, 98])
    assert isinstance(note, BassNoteStatus)
    assert note.note_name == "A1"
    assert note.hz == 55.0 and note.velocity == 98
    for bad_state in ([0] * 19, [0] * 21):
        try:
            parse_bass_state(bad_state)
        except ValueError:
            pass
        else:
            raise AssertionError("bad bass state length accepted")
    print("  bass setters/validators, 20-field state, note/name")


def test_breath_and_bass_hits() -> None:
    print("\n=== Test 5d: Stage 8.7 breath row + bass hit row ===")

    # -- constants and the status line -----------------------------------
    assert BREATH_SHAPES == ("sine", "triangle", "inhale", "exhale")
    assert BREATH_DEFAULT_SHAPE == "inhale"
    assert BREATH_DEFAULT_PERIOD_BARS == 4
    assert BREATH_PERIOD_BARS_MIN == 1 and BREATH_PERIOD_BARS_MAX == 32
    assert abs(BREATH_DEFAULT_DEPTH - 0.35) < 1e-9
    assert abs(BREATH_DEFAULT_INTENSITY - 0.5) < 1e-9
    assert BREATH_DEFAULT_PHASE == 0.0 and BREATH_DEFAULT_ARP_LOCAL == 0.0
    assert BREATH_DEFAULT_ARP_SYNC is False
    assert BASS_HITS_MAX == 32
    assert BreathStatus().status_line() == "Breath: off"
    breath = BreathStatus(
        enabled=True,
        depth=0.35,
        period_bars=4,
        shape="inhale",
        intensity=0.5,
        phase=0.0,
        arp_local=0.0,
    )
    assert breath.status_line() == (
        "Breath: on · depth 35 % · 4 bars · inhale · int 50 % · arp 0 %"
    )
    assert BreathStatus(
        enabled=True, depth=0.35, period_bars=4, shape="inhale",
        intensity=0.5, phase=0.0, arp_local=0.0, arp_sync=True,
    ).status_line().endswith(" · arp sync")

    # -- builders clamp/validate -----------------------------------------
    assert build_breath_param("enabled", True) == (
        osc_control.BREATH_SET_ENABLED,
        [1],
    )
    assert build_breath_param("enabled", False) == (
        osc_control.BREATH_SET_ENABLED,
        [0],
    )
    assert build_breath_param("depth", 2.0) == (
        osc_control.BREATH_SET_DEPTH,
        [1.0],
    )
    assert build_breath_param("depth", -1.0) == (
        osc_control.BREATH_SET_DEPTH,
        [0.0],
    )
    assert build_breath_param("period_bars", 0) == (
        osc_control.BREATH_SET_PERIOD,
        [1],
    )
    assert build_breath_param("period_bars", 99) == (
        osc_control.BREATH_SET_PERIOD,
        [32],
    )
    assert build_breath_param("period_bars", 6.4) == (
        osc_control.BREATH_SET_PERIOD,
        [6],
    )
    assert build_breath_param("shape", "triangle") == (
        osc_control.BREATH_SET_SHAPE,
        ["triangle"],
    )
    assert build_breath_param("intensity", 9) == (
        osc_control.BREATH_SET_INTENSITY,
        [1.0],
    )
    assert build_breath_param("phase", 0.25) == (
        osc_control.BREATH_SET_PHASE,
        [0.25],
    )
    assert build_breath_param("arp_local", 0.6) == (
        osc_control.BREATH_SET_ARP_LOCAL,
        [0.6],
    )
    assert build_breath_param("arp_sync", True) == (
        osc_control.BREATH_SET_ARP_SYNC,
        [1],
    )
    assert build_breath_param("arp_sync", 0) == (
        osc_control.BREATH_SET_ARP_SYNC,
        [0],
    )
    for bad in (
        ("nope", 1),
        ("shape", "square"),
        ("period_bars", "x"),
        ("depth", object()),
    ):
        try:
            build_breath_param(*bad)
        except (TypeError, ValueError):
            pass
        else:
            raise AssertionError(f"invalid breath builder accepted: {bad}")

    # -- 8-value parser, with 7-value compatibility ----------------------
    parsed = parse_breath_state(
        [1, 0.35, 4, "inhale", 0.5, 0.1, 0.2, 1]
    )
    assert isinstance(parsed, BreathStatus)
    assert parsed.enabled and parsed.depth == 0.35
    assert parsed.period_bars == 4 and parsed.shape == "inhale"
    assert parsed.intensity == 0.5 and parsed.phase == 0.1
    assert parsed.arp_local == 0.2 and parsed.arp_sync is True
    legacy = parse_breath_state([1, 0.35, 4, "inhale", 0.5, 0.1, 0.2])
    assert legacy.arp_sync is False
    clamped = parse_breath_state(
        [1, 9.0, 99, "sine", -1.0, 9.0, -2.0, 1]
    )
    assert clamped.depth == 1.0 and clamped.period_bars == 32
    assert clamped.intensity == 0.0 and clamped.phase == 1.0
    assert clamped.arp_local == 0.0 and clamped.arp_sync is True
    for bad_payload in (
        [],
        [1, 0.3, 4, "inhale", 0.5, 0.0],
        [1, 0.3, 4, "inhale", 0.5, 0.0, 0.0, 1, 0],
        [1, 0.3, 4, "inhale", 0.5, 0.0, 0.0, 2],
        [1, 0.3, 4, "square", 0.5, 0.0, 0.0],
        [1, 0.3, "four", "sine", 0.5, 0.0, 0.0],
    ):
        try:
            parse_breath_state(bad_payload)
        except (TypeError, ValueError):
            pass
        else:
            raise AssertionError(f"bad breath state accepted: {bad_payload}")

    # -- hits builders / strict parser -----------------------------------
    assert build_bass_hits_enabled(True) == (
        BASS_SET_HITS_ENABLED_ADDRESS,
        [1],
    )
    assert build_bass_hits_enabled(0) == (
        BASS_SET_HITS_ENABLED_ADDRESS,
        [0],
    )
    assert build_bass_hit(3, True) == (BASS_SET_HIT_ADDRESS, [3, 1])
    assert build_bass_hit(99, 0) == (BASS_SET_HIT_ADDRESS, [BASS_HITS_MAX - 1, 0])
    assert build_bass_hit(-4, 1) == (BASS_SET_HIT_ADDRESS, [0, 1])
    assert build_bass_clear_hits() == (BASS_CLEAR_HITS_ADDRESS, [])
    assert build_bass_copy_hits() == (BASS_COPY_HITS_ADDRESS, [])
    assert parse_bass_hits([1, 3, 1, 0, 1]) == (True, (True, False, True))
    assert parse_bass_hits([0, 0]) == (False, ())
    assert parse_bass_hits([1, BASS_HITS_MAX] + [1] * BASS_HITS_MAX)[0] is True
    for bad_payload in (
        [],
        [1],
        [1, 3, 1, 0],
        [1, 3, 1, 0, 1, 1],
        [1, 33] + [0] * 33,
        [1, 2, 1],
    ):
        try:
            parse_bass_hits(bad_payload)
        except (TypeError, ValueError):
            pass
        else:
            raise AssertionError(f"bad bass hits accepted: {bad_payload}")

    # -- status store applies both messages -------------------------------
    store = StatusStore()
    assert store.breath is None
    assert store.apply(osc_control.BREATH_STATE, [1, 0.4, 8, "exhale", 0.7, 0.2, 0.3, 1])
    assert store.breath is not None and store.breath.enabled
    assert store.breath.period_bars == 8 and store.breath.shape == "exhale"
    assert store.breath.arp_sync is True
    assert store.apply(osc_control.BASS_HITS, [1, 4, 1, 0, 0, 1])
    assert store.bass_hits_enabled is True
    assert store.bass_hits == (True, False, False, True)
    assert store.apply(osc_control.BASS_HITS, [0, 2, 0, 1])
    assert store.bass_hits_enabled is False
    assert store.bass_hits == (False, True)

    # -- controller sends (bridge only) ----------------------------------
    bridge_client = FakeClient("127.0.0.1", 9000)
    sc_client = FakeClient("127.0.0.1", 57120)
    listener = FakeListener("127.0.0.1", 9001, lambda address, args: None)
    controller = OSCController(
        bridge_client=bridge_client,
        sc_client=sc_client,
        listener=listener,
    )
    controller.start()
    bridge_client.sent.clear()
    sc_client.sent.clear()
    controller.set_breath_param("depth", 0.6)
    controller.set_breath_param("enabled", 1)
    controller.set_breath_param("arp_sync", 1)
    controller.bass_set_hits_enabled(True)
    controller.bass_set_hit(2, True)
    controller.bass_clear_hits()
    controller.bass_copy_hits()
    assert bridge_client.addresses() == [
        "/ji/breath/set_depth",
        "/ji/breath/set_enabled",
        "/ji/breath/set_arp_sync",
        "/ji/bass/set_hits_enabled",
        "/ji/bass/set_hit",
        "/ji/bass/clear_hits",
        "/ji/bass/copy_hits",
    ]
    assert bridge_client.sent[0][1] == [0.6]
    assert bridge_client.sent[1][1] == [1]
    assert bridge_client.sent[2][1] == [1]
    assert bridge_client.sent[4][1] == [2, 1]
    assert sc_client.addresses() == []
    controller.stop()

    # -- OSC server handlers (bridge side) -------------------------------
    events: list[tuple] = []
    server = MutationOSCServer(
        MutationEngine(INITIAL, seed=5),
        listen_port=19313,
        on_breath_change=lambda name, value: events.append(
            ("breath", name, value)
        ),
        on_bass_hits_enabled=lambda on: events.append(("hits_enabled", on)),
        on_bass_hit=lambda index, on: events.append(("hit", index, on)),
        on_bass_clear_hits=lambda: events.append(("clear",)),
        on_bass_copy_hits=lambda: events.append(("copy",)),
    )
    server._handle_breath_enabled(BREATH_SET_ENABLED_ADDRESS, 1)
    server._handle_breath_depth(BREATH_SET_DEPTH_ADDRESS, 9.0)
    server._handle_breath_period(BREATH_SET_PERIOD_ADDRESS, 99)
    server._handle_breath_shape(BREATH_SET_SHAPE_ADDRESS, "triangle")
    server._handle_breath_intensity(BREATH_SET_INTENSITY_ADDRESS, -1.0)
    server._handle_breath_phase(BREATH_SET_PHASE_ADDRESS, 0.5)
    server._handle_breath_arp_local(BREATH_SET_ARP_LOCAL_ADDRESS, 0.3)
    server._handle_breath_arp_sync(BREATH_SET_ARP_SYNC_ADDRESS, 1)
    server._handle_bass_hits_enabled(BASS_SET_HITS_ENABLED_ADDRESS, 1)
    server._handle_bass_hit(BASS_SET_HIT_ADDRESS, 3, 0)
    server._handle_bass_clear_hits(BASS_CLEAR_HITS_ADDRESS)
    server._handle_bass_copy_hits(BASS_COPY_HITS_ADDRESS)
    assert events == [
        ("breath", "enabled", True),
        ("breath", "depth", 1.0),
        ("breath", "period_bars", 32),
        ("breath", "shape", "triangle"),
        ("breath", "intensity", 0.0),
        ("breath", "phase", 0.5),
        ("breath", "arp_local", 0.3),
        ("breath", "arp_sync", True),
        ("hits_enabled", True),
        ("hit", 3, False),
        ("clear",),
        ("copy",),
    ], events
    events.clear()
    server._handle_breath_depth(BREATH_SET_DEPTH_ADDRESS)  # arity
    server._handle_breath_depth(BREATH_SET_DEPTH_ADDRESS, 1, 2)  # arity
    server._handle_breath_shape(BREATH_SET_SHAPE_ADDRESS, "square")  # value
    server._handle_bass_hit(BASS_SET_HIT_ADDRESS, 99, 1)  # index
    server._handle_bass_hit(BASS_SET_HIT_ADDRESS, 1)  # arity
    assert events == [], events
    print("  constants/status line, builders, strict parsers, store, controller")
    print("  controller sends and OSC server handlers")


def test_arp_builders() -> None:
    print("\n=== Test 5c: Stage 8.3b arp builders/parsers ===")
    assert build_set_arp_setting("step", 0.5) == (ARP_SET_STEP_ADDRESS, [0.5])
    assert build_set_arp_step(0.5) == (ARP_SET_STEP_ADDRESS, [0.5])
    assert build_set_arp_step(99) == (ARP_SET_STEP_ADDRESS, [2.0])
    assert build_set_arp_gate(0.1) == (ARP_SET_GATE_ADDRESS, [0.1])
    assert build_set_arp_gate(99) == (ARP_SET_GATE_ADDRESS, [2.0])
    assert build_set_arp_gate(1.0, 0.2) == (ARP_SET_GATE_ADDRESS, [0.2])
    assert build_set_arp_velocity(90) == (ARP_SET_VELOCITY_ADDRESS, [90])
    assert build_set_arp_velocity(0) == (ARP_SET_VELOCITY_ADDRESS, [1])
    assert build_set_arp_velocity(999) == (ARP_SET_VELOCITY_ADDRESS, [127])
    assert build_set_arp_octaves(0, 1, 2) == (
        ARP_SET_OCTAVES_ADDRESS,
        [0, 1, 2],
    )
    assert build_set_arp_octaves(-9, 0, 9) == (
        ARP_SET_OCTAVES_ADDRESS,
        [-8, 0, 8],
    )
    assert build_set_arp_shared_mode("random") == (
        ARP_SET_SHARED_MODE_ADDRESS,
        ["random"],
    )
    assert build_set_arp_order("pingpong") == (
        ARP_SET_ORDER_ADDRESS,
        ["pingpong"],
    )
    assert build_set_arp_seed(7) == (ARP_SET_SEED_ADDRESS, [7])
    assert build_set_arp_setting("seed", -3) == (ARP_SET_SEED_ADDRESS, [0])
    # Stage 8.3c: beat sync + evolution.
    assert ARP_SYNC_MODES == ("free", "beat")
    assert ARP_DIVISIONS == ("1/4", "1/8", "1/16", "1/8T")
    assert ARP_DEFAULT_DIVISION == "1/8" and ARP_DEFAULT_EVOLVE is False
    assert build_set_arp_sync("beat") == (osc_control.ARP_SET_SYNC, ["beat"])
    assert build_set_arp_sync(False) == (osc_control.ARP_SET_SYNC, ["free"])
    assert build_set_arp_division("1/8t") == (
        osc_control.ARP_SET_DIVISION,
        ["1/8T"],
    )
    assert build_set_arp_evolve(True) == (osc_control.ARP_SET_EVOLVE, [1])
    assert build_set_arp_evolve(0) == (osc_control.ARP_SET_EVOLVE, [0])
    assert build_set_arp_evolve_beats(0) == (
        osc_control.ARP_SET_EVOLVE_BEATS,
        [1],
    )
    assert build_set_arp_evolve_beats(999) == (
        osc_control.ARP_SET_EVOLVE_BEATS,
        [64],
    )
    assert build_set_arp_evolve_depth(0.4) == (
        osc_control.ARP_SET_EVOLVE_DEPTH,
        [0.4],
    )
    assert build_set_arp_evolve_depth(9) == (
        osc_control.ARP_SET_EVOLVE_DEPTH,
        [1.0],
    )
    for bad in (
        lambda: build_set_arp_sync("swing"),
        lambda: build_set_arp_division("1/5"),
        lambda: build_set_arp_evolve("maybe"),
        lambda: build_set_arp_evolve_beats("lots"),
        lambda: build_set_arp_evolve_depth("deep"),
    ):
        try:
            bad()
        except (TypeError, ValueError):
            pass
        else:
            raise AssertionError(f"invalid arp sync/evolve builder: {bad}")
    for bad in (
        lambda: build_set_arp_order("sideways"),
        lambda: build_set_arp_shared_mode("middle"),
        lambda: build_set_arp_setting("tempo_sync", 1),
        lambda: build_set_arp_octaves(0, 1),
    ):
        try:
            bad()
        except (TypeError, ValueError):
            pass
        else:
            raise AssertionError(f"invalid arp builder accepted: {bad}")

    assert [spec.key for spec in ARP_PARAMS] == [
        "step_seconds",
        "gate_seconds",
        "velocity",
    ]
    assert ARP_ORDERS == ("forward", "reverse", "pingpong", "random")
    assert ARP_SHARED_MODES == ("anchor", "random")
    assert ARP_DEFAULT_OCTAVES == (0, 1, 2)
    assert ARP_DEFAULT_ORDER == "forward" and ARP_DEFAULT_SEED == 1

    free_state = [
        1, 1, 0.25, 0.25, 0.20, 90, 0, 1, 2, "anchor", "forward", 1,
        "free", "1/8", 0, 4, 0.0,
    ]
    state = parse_arp_state(free_state)
    assert isinstance(state, ArpStatus)
    assert state.enabled and state.port_ok and state.available
    assert state.octave_offsets == (0, 1, 2)
    assert state.synced is False
    assert state.status_line() == (
        "on | 0.25 s / 0.20 s | forward | anchor | seed 1"
    )
    off = parse_arp_state(
        [0, 0, 0.5, 0.5, 0.4, 30, -1, 0, 1, "random", "pingpong", 9,
         "free", "1/8", 0, 4, 0.0]
    )
    assert not off.available
    assert off.status_line() == (
        "off | 0.50 s / 0.40 s | pingpong | random | seed 9"
    )
    synced = parse_arp_state(
        [1, 1, 0.25, 0.25, 0.20, 90, 0, 1, 2, "random", "pingpong", 9,
         "beat", "1/8", 1, 8, 0.3]
    )
    assert synced.synced is True
    assert synced.evolve and synced.evolve_beats == 8
    assert abs(synced.evolve_depth - 0.3) < 1e-9
    assert synced.status_line() == (
        "on | beat 1/8 = 0.25 s / 0.20 s | pingpong | random | seed 9"
        " | evolve 8 b depth 0.30"
    )
    # Clamps keep a malformed bridge message harmless.
    clamped = parse_arp_state(
        [1, 1, 0.001, 9.0, 9.0, 999, -99, 0, 99, "anchor", "reverse", -1,
         "beat", "1/4", 1, 999, 9]
    )
    assert clamped.step_seconds == 0.05
    assert clamped.step_effective == 2.0
    assert clamped.gate_seconds == 2.0
    assert clamped.velocity == 127
    assert clamped.octave_offsets == (-8, 0, 8)
    assert clamped.seed == 0
    assert clamped.evolve_beats == 64 and clamped.evolve_depth == 1.0
    for bad_state in ([0] * 16, [0] * 18):
        try:
            parse_arp_state(bad_state)
        except ValueError:
            pass
        else:
            raise AssertionError("bad arp state length accepted")
    for index, bad_value in (
        (9, "nope"),
        (12, "swing"),
        (13, "1/5"),
    ):
        payload = list(free_state)
        payload[index] = bad_value
        try:
            parse_arp_state(payload)
        except ValueError:
            pass
        else:
            raise AssertionError(f"bad arp state value accepted: {bad_value}")

    store = StatusStore()
    assert store.arp is None
    assert store.apply("/ji/arp/state", free_state)
    assert store.arp is not None and store.arp.port_ok
    assert store.bridge_seen
    print("  arp setters/validators, 11-field state, label, store")


def test_status_parsers() -> None:
    print("\n=== Test 4: status parsers ===")
    freqset = parse_freqset([220.0, 1.0, 1.0, 1.125, 1.5])
    assert isinstance(freqset, FrequencySet)
    assert freqset.fundamental == 220.0
    assert freqset.anchor == 1.0
    assert freqset.ratios == [1.0, 1.125, 1.5]
    assert freqset.pairs == [(1.0, 220.0), (1.125, 247.5), (1.5, 330.0)]

    no_anchor = parse_freqset([220.0, -1.0, 1.0, 1.25])
    assert no_anchor.anchor is None

    for bad in (
        [220.0],
        [0.0, 1.0, 1.0],
        [220.0, 1.0, 0.0],
        [220.0, 1.0, float("nan")],
    ):
        try:
            parse_freqset(bad)
        except ValueError:
            pass
        else:
            raise AssertionError(f"invalid freqset accepted: {bad}")

    control = parse_ui_state(
        [0.55, -0.2, 0.25, 0.15, 2, 0.4, 0.3, 5]
    )
    assert isinstance(control, BridgeControlState)
    assert control.change_count == 2 and isinstance(control.change_count, int)
    assert control.max_changes == 5 and isinstance(control.max_changes, int)
    assert control.randomness == 0.55
    assert control.dissonance_bias == -0.2
    assert control.glide_time == 0.15
    assert control.temperature == 0.4

    clamped = parse_ui_state([2.0, -3.0, 3.0, 9.0, 99, 9.0, 9.0, 4])
    assert clamped.randomness == 1.0
    assert clamped.dissonance_bias == -1.0
    assert clamped.stepwise_bias == 1.0
    assert clamped.glide_time == 2.0
    assert clamped.change_count == 4
    assert clamped.temperature == 1.0
    assert clamped.anchor_mobility == 1.0

    midi = parse_midi_status([1, "IAC Driver Bus 1", "IAC Driver Bus 2"])
    assert isinstance(midi, MidiStatus)
    assert midi.enabled is True
    assert midi.drone_port == "IAC Driver Bus 1"
    assert midi.arp_port == "IAC Driver Bus 2"
    disabled = parse_midi_status([0, "", ""])
    assert disabled.enabled is False
    assert disabled.drone_port == "" and disabled.arp_port == ""

    toggle = parse_midi_state([1, 0])
    assert isinstance(toggle, MidiToggleState)
    assert toggle.drone_enabled is True and toggle.arp_enabled is False
    assert parse_midi_state([0, 1]).arp_enabled is True

    assert parse_cpu([12.5]) == 12.5
    assert parse_cpu([150.0]) == 100.0
    assert parse_loop_state([3, "recording"]) == (3, "recording")
    assert parse_loop_state([1, "empty"]) == (1, "empty")

    rhythm = parse_rhythm_state([140.0, 3, 4, 1, 1, 0.5])
    assert isinstance(rhythm, RhythmStatus)
    assert rhythm.bpm == 140.0
    assert rhythm.meter == "3/4"
    assert rhythm.running is True
    assert rhythm.drums_enabled is True
    assert rhythm.drum_rate == 0.5
    clamped = parse_rhythm_state([9999, 99, 8, 0, 0, 1.0])
    assert clamped.bpm == 300.0 and clamped.meter_num == 16
    for bad in (
        lambda: parse_rhythm_state([120, 4, 3, 1, 1, 1.0]),
        lambda: parse_rhythm_state([120, 4, 4, 2, 1, 1.0]),
        lambda: parse_rhythm_state([120, 4, 4, 1, 1, 1.5]),
        lambda: parse_rhythm_state([120, 4, 4, 1, 1]),
    ):
        try:
            bad()
        except (TypeError, ValueError):
            pass
        else:
            raise AssertionError("invalid rhythm state accepted")

    drums = parse_drums_state([1, 0, 1.0, 0x0045])
    assert isinstance(drums, DrumStatus)
    assert drums.enabled is True and drums.port_ok is False
    assert drums.lane_states[36] is True
    assert parse_drums_state([0, 1, 2.0, 70000]).lane_mask == 0xFFFF
    for bad in (
        lambda: parse_drums_state([2, 0, 1.0, 0]),
        lambda: parse_drums_state([1, 0, 1.5, 0]),
        lambda: parse_drums_state([1, 0, 1.0]),
    ):
        try:
            bad()
        except (TypeError, ValueError):
            pass
        else:
            raise AssertionError("invalid drum state accepted")

    groove_state = parse_groove(["break", 0.3, 0.2, 0.15, 0.7, 4, 16])
    assert isinstance(groove_state, GrooveStatus)
    assert groove_state.preset == "break"
    assert groove_state.density == 0.3
    assert groove_state.modifiers["probability"] == 0.7
    assert groove_state.seed == 4 and groove_state.grid_steps == 16
    clamped_groove = parse_groove(["four", 2, -1, 9, 1, 1, 99])
    assert clamped_groove.density == 1.0
    assert clamped_groove.syncopation == 0.0
    assert clamped_groove.swing == 1.0
    assert clamped_groove.grid_steps == 32
    for bad in (
        lambda: parse_groove(["nope", 0, 0, 0, 0, 1, 16]),
        lambda: parse_groove(["four", 0, 0, 0, 0, 1]),
    ):
        try:
            bad()
        except (TypeError, ValueError):
            pass
        else:
            raise AssertionError("invalid groove state accepted")

    masks = parse_grid([0x1111, 0] + [0] * 30)
    assert masks[0] == 0x1111
    assert len(masks) == 16
    assert parse_grid([0] * 30 + [0xFFFF, 0xFFFF])[15] == 0xFFFFFFFF
    for bad in (lambda: parse_grid([0] * 31), lambda: parse_grid([0] * 33)):
        try:
            bad()
        except (TypeError, ValueError):
            pass
        else:
            raise AssertionError("invalid grid payload accepted")

    quantize = parse_quantize([2, 0.32])
    assert isinstance(quantize, QuantizeStatus)
    assert quantize.pending == 2 and quantize.seconds == 0.32
    assert quantize.immediate is False
    stopped = parse_quantize([0, -1.0])
    assert stopped.immediate is True and stopped.seconds == -1.0
    assert parse_quantize([-3, 5.0]).pending == 0
    for bad in (
        lambda: parse_quantize([1]),
        lambda: parse_quantize([1, float("nan")]),
    ):
        try:
            bad()
        except (TypeError, ValueError):
            pass
        else:
            raise AssertionError("invalid quantize payload accepted")

    index, config = parse_loop_config([2, "beat", 7, 3.5])
    assert index == 2
    assert isinstance(config, LoopConfig)
    assert config.mode == "beat" and config.beats == 7.0
    assert config.seconds == 3.5
    assert parse_loop_config([1, "free", 0, 0])[1] == LoopConfig(
        mode="free", beats=1.0, seconds=0.0
    )
    for bad in (
        lambda: parse_loop_config([5, "free", 4, 8]),
        lambda: parse_loop_config([1, "loop", 4, 8]),
        lambda: parse_loop_config([1, "free", 4, -1]),
        lambda: parse_loop_config([1, "free", 4]),
    ):
        try:
            bad()
        except (TypeError, ValueError):
            pass
        else:
            raise AssertionError("invalid loop config accepted")

    for bad in (
        lambda: parse_midi_status([2, "a", "b"]),
        lambda: parse_midi_status([1, "a"]),
        lambda: parse_midi_state([1]),
        lambda: parse_midi_state([2, 0]),
        lambda: parse_loop_state([5, "empty"]),
        lambda: parse_loop_state([1, "nonsense"]),
        lambda: parse_cpu([float("nan")]),
    ):
        try:
            bad()
        except (TypeError, ValueError):
            pass
        else:
            raise AssertionError("invalid status payload was accepted")

    channel, values = parse_autotune_state(
        [1, 0.1, 0.8, 0.2, 7, 0.7, 0.5]
    )
    assert channel == 1
    assert values["mix"] == 0.8
    assert values["pitchRangeLimit"] == 7.0
    assert values["windowSize"] == 0.2
    assert parse_autotune_state([0, 9.0, 9.0, 9.0, 99, 9.0, 9.0])[1][
        "retuneTime"
    ] == 2.0
    print("  freqset/ui/midi/cpu/loop/autotune parsers and ranges are correct")


def test_status_store() -> None:
    print("\n=== Test 5: StatusStore waiting flags and updates ===")
    store = StatusStore()
    assert store.waiting_for_bridge and store.waiting_for_sclang
    assert store.max_changes == 3
    assert store.loop_states == {1: "waiting", 2: "waiting", 3: "waiting", 4: "waiting"}
    assert store.autotune[0]["mix"] == 1.0

    assert store.apply("/ji/ui/state", [0.55, 0.0, 0.25, 0.15, 1, 0.0, 0.25, 5])
    assert store.waiting_for_bridge is False
    assert store.waiting_for_sclang is True
    assert store.max_changes == 5
    assert store.control is not None and store.control.max_changes == 5

    assert store.apply("/ji/cpu", [14.0])
    assert store.waiting_for_sclang is False
    assert store.cpu == 14.0

    assert store.apply("/ji/loop/state", [2, "recorded"])
    assert store.loop_states[2] == "recorded"
    assert store.apply(
        "/ji/autotune/state", [0, 0.05, 1.0, 0.1, 12.0, 0.9, 1.0]
    )
    assert store.autotune[0]["retuneTime"] == 0.05

    assert store.apply("/ji/freqset/changed", [220.0, -1.0, 1.0, 1.5])
    assert store.freqset is not None and store.freqset.anchor is None
    assert store.apply("/ji/midi/status", [0, "", ""])
    assert store.midi is not None and store.midi.enabled is False
    assert store.apply("/ji/midi/state", [1, 0])
    assert store.midi_state is not None
    assert store.midi_state.drone_enabled is True
    assert store.midi_state.arp_enabled is False

    assert store.rhythm is None and store.drums is None
    assert store.loop_configs[1] == LoopConfig()
    assert store.apply("/ji/rhythm/state", [140.0, 3, 4, 1, 1, 0.5])
    assert store.rhythm is not None and store.rhythm.bpm == 140.0
    assert store.rhythm.meter == "3/4" and store.rhythm.running is True
    assert store.apply("/ji/drums/state", [1, 1, 2.0, 0x0045])
    assert store.drums is not None and store.drums.port_ok is True
    assert store.drums.lane_states[42] is True
    assert store.groove is None and store.grid is None and store.quantize is None
    assert store.apply("/ji/drums/groove", ["break", 0.4, 0.2, 0.1, 0.9, 7, 16])
    assert store.groove is not None
    assert store.groove.preset == "break" and store.groove.seed == 7
    assert store.groove.modifiers["density"] == 0.4
    assert store.apply("/ji/drums/grid", [1, 0] + [0] * 30)
    assert store.grid is not None and store.grid[0] == 1
    assert store.apply("/ji/quantize/state", [3, 0.25])
    assert store.quantize is not None
    assert store.quantize.pending == 3 and store.quantize.seconds == 0.25
    assert store.apply("/ji/loop/config", [3, "beat", 7, 3.5])
    assert store.loop_configs[3].mode == "beat"
    assert store.loop_configs[3].beats == 7.0
    assert store.loop_configs[3].seconds == 3.5

    assert store.apply("/unknown", []) is False
    try:
        store.apply("/ji/cpu", ["not-a-number"])
    except TypeError:
        pass
    else:
        raise AssertionError("malformed status did not raise")
    print("  waiting flags, snapshots, and malformed-message rejection are correct")


# ---------------------------------------------------------------------------
# Bridge / server additions
# ---------------------------------------------------------------------------


def test_osc_server_hello() -> None:
    print("\n=== Test 6: MutationOSCServer hello, MIDI, and rhythm callbacks ===")
    engine = MutationEngine(INITIAL, seed=3)
    called: list[int] = []
    midi_called: list[tuple[bool, bool]] = []
    tempo_called: list[float] = []
    meter_called: list[tuple[int, int]] = []
    transport_called: list[bool] = []
    drums_enabled_called: list[bool] = []
    drums_lane_called: list[tuple[int, bool]] = []
    drums_rate_called: list[float] = []
    rhythm_requested: list[int] = []
    preset_called: list[str] = []
    density_called: list[float] = []
    sync_called: list[float] = []
    swing_called: list[float] = []
    probability_called: list[float] = []
    step_called: list[tuple[int, int, bool]] = []
    seed_called: list[int] = []
    reset_called: list[int] = []
    server = MutationOSCServer(
        engine,
        listen_port=19311,
        on_ui_hello=lambda: called.append(1),
        on_midi_enabled=lambda drone, arp: midi_called.append((drone, arp)),
        on_rhythm_tempo=tempo_called.append,
        on_rhythm_meter=lambda num, den: meter_called.append((num, den)),
        on_rhythm_transport=transport_called.append,
        on_drums_enabled=drums_enabled_called.append,
        on_drums_lane=lambda note, on: drums_lane_called.append((note, on)),
        on_drums_rate=drums_rate_called.append,
        on_rhythm_request=lambda: rhythm_requested.append(1),
        on_drums_preset=preset_called.append,
        on_drums_density=density_called.append,
        on_drums_syncopation=sync_called.append,
        on_drums_swing=swing_called.append,
        on_drums_probability=probability_called.append,
        on_drums_step=lambda lane, step, on: step_called.append(
            (lane, step, on)
        ),
        on_drums_seed=seed_called.append,
        on_drums_reset_grid=lambda: reset_called.append(1),
    )
    assert UI_HELLO_ADDRESS == "/ji/ui/hello"
    assert server.UI_HELLO_ADDRESS == "/ji/ui/hello"
    assert MIDI_SET_ENABLED_ADDRESS == "/ji/midi/set_enabled"
    assert RHYTHM_SET_TEMPO_ADDRESS == "/ji/rhythm/set_tempo"
    assert RHYTHM_SET_METER_ADDRESS == "/ji/rhythm/set_meter"
    assert RHYTHM_TRANSPORT_ADDRESS == "/ji/rhythm/transport"
    assert RHYTHM_REQUEST_ADDRESS == "/ji/rhythm/request"
    assert DRUMS_SET_ENABLED_ADDRESS == "/ji/drums/set_enabled"
    assert DRUMS_SET_LANE_ADDRESS == "/ji/drums/set_lane"
    assert DRUMS_SET_RATE_ADDRESS == "/ji/drums/set_rate"
    assert DRUMS_SET_PRESET_ADDRESS == "/ji/drums/set_preset"
    assert DRUMS_SET_DENSITY_ADDRESS == "/ji/drums/set_density"
    assert DRUMS_SET_SYNCOPATION_ADDRESS == "/ji/drums/set_syncopation"
    assert DRUMS_SET_SWING_ADDRESS == "/ji/drums/set_swing"
    assert DRUMS_SET_PROBABILITY_ADDRESS == "/ji/drums/set_probability"
    assert DRUMS_SET_STEP_ADDRESS == "/ji/drums/set_step"
    assert DRUMS_SET_SEED_ADDRESS == "/ji/drums/set_seed"
    assert DRUMS_RESET_GRID_ADDRESS == "/ji/drums/reset_grid"
    server._handle_ui_hello("/ji/ui/hello")
    assert called == [1]
    server._handle_ui_hello("/ji/ui/hello", "extra")
    assert called == [1, 1]

    server._handle_midi_enabled("/ji/midi/set_enabled", 1, 0)
    assert midi_called == [(True, False)]
    server._handle_midi_enabled("/ji/midi/set_enabled", 0, 1)
    assert midi_called[-1] == (False, True)
    # Invalid arity/values are rejected without calling back.
    server._handle_midi_enabled("/ji/midi/set_enabled", 1)
    server._handle_midi_enabled("/ji/midi/set_enabled", 2, 0)
    assert len(midi_called) == 2

    server._handle_set_tempo("/ji/rhythm/set_tempo", 1000.0)
    assert tempo_called == [300.0]
    server._handle_set_tempo("/ji/rhythm/set_tempo", float("nan"))
    assert len(tempo_called) == 1
    server._handle_set_meter("/ji/rhythm/set_meter", 99, 8)
    assert meter_called == [(16, 8)]
    server._handle_set_meter("/ji/rhythm/set_meter", 4, 3)
    assert len(meter_called) == 1
    server._handle_transport("/ji/rhythm/transport", 1)
    server._handle_transport("/ji/rhythm/transport", 2)
    assert transport_called == [True]
    server._handle_drums_enabled("/ji/drums/set_enabled", 1)
    server._handle_drums_enabled("/ji/drums/set_enabled", 2)
    assert drums_enabled_called == [True]
    server._handle_drums_lane("/ji/drums/set_lane", 38, 0)
    assert drums_lane_called == [(38, False)]
    server._handle_drums_lane("/ji/drums/set_lane", 200, 1)
    assert len(drums_lane_called) == 1
    server._handle_drums_rate("/ji/drums/set_rate", 0.5)
    server._handle_drums_rate("/ji/drums/set_rate", 1.5)
    assert drums_rate_called == [0.5]
    server._handle_rhythm_request("/ji/rhythm/request")
    assert rhythm_requested == [1]

    server._handle_drums_preset("/ji/drums/set_preset", "Break")
    server._handle_drums_preset("/ji/drums/set_preset", "nope")
    assert preset_called == ["break"]
    server._handle_drums_density("/ji/drums/set_density", 0.5)
    server._handle_drums_density("/ji/drums/set_density", 2.0)
    server._handle_drums_density("/ji/drums/set_density", float("nan"))
    assert density_called == [0.5, 1.0]
    server._handle_drums_syncopation("/ji/drums/set_syncopation", -1.0)
    assert sync_called == [0.0]
    server._handle_drums_swing("/ji/drums/set_swing", 0.25)
    assert swing_called == [0.25]
    server._handle_drums_probability("/ji/drums/set_probability", 0.75)
    assert probability_called == [0.75]
    server._handle_drums_step("/ji/drums/set_step", 2, 5, 1)
    assert step_called == [(2, 5, True)]
    server._handle_drums_step("/ji/drums/set_step", 16, 5, 1)
    server._handle_drums_step("/ji/drums/set_step", 2, 32, 1)
    assert len(step_called) == 1
    server._handle_drums_seed("/ji/drums/set_seed", 9)
    assert seed_called == [9]
    server._handle_drums_reset_grid("/ji/drums/reset_grid")
    assert reset_called == [1]

    # Stage 8.2 request hooks: the handler asks the bridge to queue, while the
    # direct trigger()/octave() API stays immediate.
    mutation_requests: list[int] = []
    octave_requests: list[int] = []
    hooked = MutationOSCServer(
        engine,
        listen_port=19312,
        on_mutation_request=lambda: mutation_requests.append(1),
        on_octave_request=lambda: octave_requests.append(1),
    )
    before = list(engine.current_ratios)
    hooked._handle_trigger("/ji/mutation/trigger")
    hooked._handle_octave("/ji/mutation/octave")
    assert mutation_requests == [1]
    assert octave_requests == [1]
    assert engine.current_ratios == before  # handler did not apply directly
    hooked.trigger()  # direct API remains immediate
    assert engine.current_ratios != before
    print("  hello, MIDI, rhythm/drum/groove callbacks; quantize hook is soft")


def test_bridge_ui_status() -> None:
    print("\n=== Test 7: JIBridge UI status and hello ===")
    original = bridge_module.SimpleUDPClient
    FakeClient.instances = []
    bridge_module.SimpleUDPClient = FakeClient
    state_directory = tempfile.mkdtemp(prefix="ji-ui-status-")
    try:
        bridge = bridge_module.JIBridge(
            seed=41,
            max_changes=5,
            change_count=3,
            temperature=0.4,
            anchor_mobility=0.6,
            glide_time=0.3,
            # Keep the real states/ folder (user data) out of this test.
            state_dir=state_directory,
        )
        assert len(FakeClient.instances) == 2
        client, ui_client = FakeClient.instances
        assert bridge.client is client
        assert bridge.ui_client is ui_client
        assert bridge.midi is None

        assert bridge.send_set(0.2) is True
        assert client.addresses() == ["/ji/freqset/update"]
        assert ui_client.addresses() == [
            "/ji/freqset/changed",
            "/ji/ui/state",
            "/ji/midi/status",
            "/ji/midi/state",
        ]

        freq_address, freq_args = ui_client.sent[0]
        assert freq_address == "/ji/freqset/changed"
        assert freq_args[0] == 220.0
        assert freq_args[1] == 1.0
        assert len(freq_args) == 2 + len(bridge.engine.current_ratios)
        assert all(isinstance(value, float) for value in freq_args)
        assert freq_args[2:] == sorted(bridge.engine.current_ratios)

        _, state_args = ui_client.sent[1]
        assert len(state_args) == 8
        assert isinstance(state_args[0], float)
        assert isinstance(state_args[1], float)
        assert isinstance(state_args[2], float)
        assert isinstance(state_args[3], float)
        assert isinstance(state_args[4], int) and state_args[4] == 3
        assert isinstance(state_args[5], float)
        assert isinstance(state_args[6], float)
        assert isinstance(state_args[7], int) and state_args[7] == 5

        _, midi_args = ui_client.sent[2]
        assert midi_args == [0, "", ""]
        _, midi_state_args = ui_client.sent[3]
        assert midi_state_args == [0, 0]

        # The Stage 3 payload must remain exactly one update per set.
        assert len(client.sent) == 1
        assert client.sent[0][0] == "/ji/freqset/update"
        assert client.sent[0][1][1] == 0.2

        # A UI hello replies with a snapshot without sending another set.
        ui_client.sent.clear()
        bridge.server._handle_ui_hello("/ji/ui/hello")
        assert ui_client.addresses() == [
            "/ji/freqset/changed",
            "/ji/ui/state",
            "/ji/midi/status",
            "/ji/midi/state",
            "/ji/rhythm/state",
            "/ji/drums/state",
            "/ji/drums/groove",
            "/ji/drums/grid",
            "/ji/bass/state",
            "/ji/bass/hits",
            "/ji/breath/state",
            "/ji/arp/state",
            "/ji/lead/state",
            "/ji/lead/input/state",
            "/ji/lead/melody/list",
            "/ji/lead/melody/state",
            "/ji/drone/state",
            "/ji/home/state",
            "/ji/state/list",
            "/ji/state/state",
            "/ji/quantize/state",
        ]
        assert len(client.sent) == 1
        rhythm_args = ui_client.sent[4][1]
        assert rhythm_args == [120.0, 4, 4, 0, 0, 1.0]
        assert ui_client.sent[5][1] == [0, 0, 1.0, DRUM_LANE_DEFAULT_MASK]
        groove_args = ui_client.sent[6][1]
        assert groove_args[:2] == ["four", 0.0]
        assert groove_args[5] == 1 and groove_args[6] == 16
        assert len(ui_client.sent[7][1]) == 32
        bass_args = ui_client.sent[8][1]
        assert len(bass_args) == 20
        assert bass_args[0] == 0 and bass_args[1] == 0
        assert bass_args[2] == "pulse" and bass_args[3] == "1/4"
        assert bass_args[18] == 2 and bass_args[19] == 3
        hits_args = ui_client.sent[9][1]
        assert len(hits_args) == 2 and hits_args == [0, 0]
        breath_args = ui_client.sent[10][1]
        assert len(breath_args) == 8
        assert breath_args[0] == 0 and breath_args[3] == "inhale"
        assert breath_args[7] == 0
        arp_args = ui_client.sent[11][1]
        assert len(arp_args) == 17, arp_args
        assert arp_args[0] == 0 and arp_args[1] == 0
        assert arp_args[2] == 0.25 and arp_args[3] == 0.25
        assert arp_args[4] == 0.20
        assert arp_args[5] == 90
        assert arp_args[6:9] == [0, 1, 2]
        assert arp_args[9] == "anchor" and arp_args[10] == "forward"
        assert arp_args[11] == 1
        assert arp_args[12] == "free" and arp_args[13] == "1/8"
        assert arp_args[14] == 0 and arp_args[15] == 4 and arp_args[16] == 0.0
        lead_args = ui_client.sent[12][1]
        assert len(lead_args) == 17, lead_args
        assert lead_args[0] == 0 and lead_args[1] == 0
        assert lead_args[2] == "1/8" and lead_args[3] == 2
        assert lead_args[9] == "identity" and lead_args[10] == 0
        assert lead_args[14] == 1 and lead_args[15] == 0
        assert lead_args[16] == "beat"
        input_args = ui_client.sent[13][1]
        assert len(input_args) == 16, input_args
        assert input_args[0] == 0 and input_args[1] == 0
        assert input_args[3] == 1.0 and input_args[4] == "1 bar"
        assert input_args[5] == 2 and input_args[6] == 0
        assert input_args[9] == "generated"
        assert input_args[14] == 0.0 and input_args[15] == 0
        assert ui_client.sent[14][1] == [0]
        melody_state_args = ui_client.sent[15][1]
        assert melody_state_args == [0, "", "", ""]
        drone_args = ui_client.sent[16][1]
        assert len(drone_args) == 17, drone_args
        assert drone_args[0] == 0 and drone_args[1] == "hold"
        assert drone_args[2] == 90 and drone_args[3] == 4
        assert drone_args[10] == 2.5 and drone_args[11] == 4.0
        assert drone_args[13] == 0
        assert drone_args[14] == 5 and drone_args[15] == "1"
        assert drone_args[16] == 1.0
        # A 14-value (8.3d) payload still parses: a half-updated rig works.
        legacy = osc_control.parse_drone_state(
            [1, "pulse", 90, 4, "1/4", 0.5, 1, "down", 2, 0.4, 2.5, 4.0, 1, 0]
        )
        assert legacy.seq_notes == 5 and legacy.seq_bars == "1"
        assert legacy.seq_gate == 1.0 and legacy.mode == "pulse"
        assert legacy.gated is True
        assert osc_control.parse_drone_state(
            [1, "hold", 90, 4, "1/4", 0.5, 1, "down", 2, 0.4, 2.5, 4.0, 1, 0]
        ).gated is False
        assert ui_client.sent[17][1][0] == 0  # /ji/home/state (no home yet)
        assert len(ui_client.sent[17][1]) == 13
        assert ui_client.sent[18][1] == [0]  # /ji/state/list (none saved)
        assert ui_client.sent[19][1] == [0, "", "", ""]  # /ji/state/state
        assert ui_client.sent[20][1] == [0, -1.0]

        # Rhythm commands: state changes go to sclang and the UI.
        client.sent.clear()
        ui_client.sent.clear()
        bridge._on_rhythm_tempo(140.0)
        assert ("/ji/rhythm/tempo", [140.0, 4, 4, 0]) in [
            (address, list(args)) for address, args in client.sent
        ]
        assert ui_client.addresses() == [
            "/ji/rhythm/state",
            "/ji/arp/state",
            "/ji/drone/state",
            "/ji/quantize/state",
        ]
        assert ui_client.sent[0][1][0] == 140.0
        assert ui_client.sent[2][1][11] == 4.0

        # Stage 8.7.1: an arp_sync change is immediate and echoes 8 values.
        ui_client.sent.clear()
        bridge._on_breath_change("arp_sync", 1)
        assert bridge.breath.settings.arp_sync is True
        assert ui_client.addresses() == ["/ji/breath/state"]
        assert ui_client.sent[0][1][7] == 1
        bridge._on_breath_change("arp_sync", 0)
        assert bridge.breath.settings.arp_sync is False

        ui_client.sent.clear()
        bridge._on_rhythm_meter(3, 4)
        assert ui_client.sent[0][1][1:3] == [3, 4]
        ui_client.sent.clear()
        bridge._on_rhythm_transport(True)
        assert ui_client.sent[0][1][3] == 1
        assert bridge.rhythm.running is True
        # Transport changes also refresh the quantize state (countdown).
        assert ui_client.addresses()[-1] == "/ji/quantize/state"
        bridge._on_rhythm_transport(False)
        assert bridge.rhythm.running is False

        ui_client.sent.clear()
        bridge._on_rhythm_request()
        assert client.addresses()[-1] == "/ji/rhythm/tempo"

        # Drums are unavailable without --drums: state is reported, no crash.
        ui_client.sent.clear()
        bridge._on_drums_enabled(True)
        assert ui_client.addresses() == [
            "/ji/drums/state",
            "/ji/rhythm/state",
        ]
        assert ui_client.sent[0][1] == [0, 0, 1.0, DRUM_LANE_DEFAULT_MASK]
        ui_client.sent.clear()
        bridge._on_drums_lane(36, True)
        assert ui_client.addresses() == ["/ji/drums/state"]
        ui_client.sent.clear()
        bridge._on_drums_rate(2.0)
        assert ui_client.addresses() == ["/ji/drums/state"]
        assert ui_client.sent[0][1] == [0, 0, 1.0, DRUM_LANE_DEFAULT_MASK]

        # Explicit MIDI status with an injected rig, then independent toggles.
        fake_midi = FakeMidiRig(drums=FakeDrumRig())
        bridge.midi = fake_midi
        ui_client.sent.clear()
        bridge._send_ui_state()
        assert ui_client.addresses()[-2:] == [
            "/ji/midi/status",
            "/ji/midi/state",
        ]
        assert ui_client.sent[-2][1] == [
            1,
            "IAC Driver Bus 1",
            "IAC Driver Bus 2",
        ]
        assert ui_client.sent[-1][1] == [1, 1]

        ui_client.sent.clear()
        bridge._set_midi_enabled(False, True)
        assert fake_midi.drone_enabled is False
        assert fake_midi.arp_enabled is True
        assert fake_midi.calls == [("drone", False), ("arp", True)]
        assert ui_client.addresses() == [
            "/ji/midi/state",
            "/ji/arp/state",
            "/ji/quantize/state",
        ]
        assert ui_client.sent[0][1] == [0, 1]

        ui_client.sent.clear()
        bridge._set_midi_enabled(True, False)
        assert fake_midi.drone_enabled is True
        assert fake_midi.arp_enabled is False
        assert ui_client.sent[0][1] == [1, 0]

        # Drum state/mutations with the injected drum rig.
        ui_client.sent.clear()
        bridge._on_drums_enabled(True)
        assert fake_midi.drum_calls[-1] == ("enabled", True)
        assert ui_client.addresses() == [
            "/ji/drums/state",
            "/ji/rhythm/state",
            "/ji/quantize/state",
        ]
        assert ui_client.sent[0][1] == [1, 1, 1.0, DRUM_LANE_DEFAULT_MASK]

        ui_client.sent.clear()
        bridge._on_drums_lane(37, True)
        assert fake_midi.drums is not None
        assert fake_midi.drums.lane_mask & (1 << 1)
        assert ui_client.sent[0][0] == "/ji/drums/state"
        assert ui_client.sent[0][1][3] == fake_midi.drums.lane_mask

        ui_client.sent.clear()
        bridge._on_drums_rate(0.5)
        assert fake_midi.drums.rate == 0.5
        assert ui_client.sent[0][1][2] == 0.5
        assert ui_client.sent[1][1][5] == 0.5

        client.sent.clear()
        bridge._on_rhythm_tempo(90.0)
        assert fake_midi.drum_calls[-1][0] == "bpm"
        ui_client.sent.clear()
        bridge._on_rhythm_meter(5, 4)
        assert fake_midi.drum_calls[-1] == ("meter", 5, 4)
        bridge._on_rhythm_transport(False)
        assert fake_midi.drum_calls[-1] == ("transport", False)

        # --- Stage 8.2 groove state and quantized edits ----------------
        bridge._on_rhythm_transport(False)
        ui_client.sent.clear()
        bridge._on_drums_preset("break")
        assert bridge.groove.spec.preset == "break"
        assert "/ji/drums/groove" in ui_client.addresses()
        assert "/ji/drums/grid" in ui_client.addresses()
        assert bridge.quantizer.pending_count == 0

        # With the transport running, a groove edit is queued and only lands
        # when the queue flushes at the beat.
        bridge._on_rhythm_transport(True)
        bridge._on_drums_preset("techno")
        assert bridge.groove.spec.preset == "break"
        assert bridge.quantizer.pending_count == 1
        ui_client.sent.clear()
        bridge._on_quantize_change()
        assert ui_client.addresses() == ["/ji/quantize/state"]
        assert ui_client.sent[0][1][0] == 1
        bridge.quantizer.flush("test beat")
        assert bridge.groove.spec.preset == "techno"
        assert bridge.quantizer.pending_count == 0

        bridge._on_rhythm_transport(False)
        bridge._on_groove_modifier("density", 0.5)
        assert bridge.groove.spec.density == 0.5
        bridge._on_drums_seed(7)
        assert bridge.groove.spec.seed == 7
        bridge._on_drums_step(0, 1, True)
        assert bridge.groove.grid.is_on(0, 1)
        bridge._on_drums_reset_grid()
        assert not bridge.groove.grid.is_on(0, 1)

        # Mutation requests obey the same rule: immediate when stopped,
        # queued while running.
        bridge._on_rhythm_transport(False)
        before = list(bridge.engine.current_ratios)
        bridge.request_mutation()
        assert bridge.engine.current_ratios != before
        assert bridge.quantizer.pending_count == 0
        bridge._on_rhythm_transport(True)
        after_immediate = list(bridge.engine.current_ratios)
        bridge.request_octave()
        assert bridge.quantizer.pending_count == 1
        bridge.quantizer.flush("test beat")
        assert bridge.quantizer.pending_count == 0
        assert list(bridge.engine.current_ratios) == after_immediate
        bridge._on_rhythm_transport(False)

        # The CLI defaults remain compatible.
        parser = bridge_module.build_parser()
        defaults = parser.parse_args([])
        assert defaults.ui_host == "127.0.0.1"
        assert defaults.ui_port == 9001
        assert defaults.drums is False
        assert defaults.rhythm_config is None
        assert parser.parse_args(["--drums"]).drums is True
        assert parser.parse_args(["--midi"]).drums is False
    finally:
        bridge_module.SimpleUDPClient = original
        shutil.rmtree(state_directory, ignore_errors=True)
    print("  one /ji/freqset/update per set; UI/rhythm/drum messages are well formed")


# ---------------------------------------------------------------------------
# OSCController
# ---------------------------------------------------------------------------


def test_osc_controller() -> None:
    print("\n=== Test 8: OSCController lifecycle and sends ===")
    bridge_client = FakeClient("127.0.0.1", 9000)
    sc_client = FakeClient("127.0.0.1", 57120)
    listener = FakeListener("127.0.0.1", 9001, lambda address, args: None)
    statuses: list[tuple[str, list]] = []

    controller = OSCController(
        bridge_client=bridge_client,
        sc_client=sc_client,
        listener=listener,
        on_status=lambda address, args: statuses.append((address, args)),
    )
    assert controller.started is False
    controller.start()
    assert controller.started is True
    assert listener.started is True
    assert listener.order[0] == "listen"

    # hello goes to the bridge; query goes to sclang.
    assert bridge_client.addresses() == ["/ji/ui/hello"]
    assert sc_client.addresses() == ["/ji/status/query"]

    bridge_client.sent.clear()
    sc_client.sent.clear()
    controller.set_mutation("randomness", 0.7)
    controller.trigger_mutation()
    controller.trigger_octave()
    controller.set_midi_enabled(True, False)
    assert bridge_client.addresses() == [
        "/ji/mutation/set_randomness",
        "/ji/mutation/trigger",
        "/ji/mutation/octave",
        "/ji/midi/set_enabled",
    ]
    assert bridge_client.sent[0][1] == [0.7]
    assert bridge_client.sent[-1][1] == [1, 0]

    bridge_client.sent.clear()
    controller.set_lead_input_enabled(True)
    controller.set_lead_input_quantize(0.5)
    controller.set_lead_input_count_in("2 bars")
    controller.set_lead_input_bars(3)
    controller.lead_input_note_on(60, 100)
    controller.lead_input_note_off(60)
    controller.lead_input_record()
    controller.lead_input_stop()
    controller.lead_input_revert()
    controller.lead_input_clear()
    controller.refresh_lead_input()
    controller.melody_save("intro line")
    controller.melody_load("intro line")
    controller.melody_delete("intro line")
    controller.request_melody_list()
    assert bridge_client.addresses() == [
        "/ji/lead/input/set_enabled",
        "/ji/lead/input/set_quantize",
        "/ji/lead/input/set_count_in",
        "/ji/lead/input/set_bars",
        "/ji/lead/input/note_on",
        "/ji/lead/input/note_off",
        "/ji/lead/input/record",
        "/ji/lead/input/stop",
        "/ji/lead/input/revert",
        "/ji/lead/input/clear",
        "/ji/lead/input/refresh",
        "/ji/lead/melody/save",
        "/ji/lead/melody/load",
        "/ji/lead/melody/delete",
        "/ji/lead/melody/request",
    ], bridge_client.addresses()
    assert bridge_client.sent[0][1] == [1]
    assert bridge_client.sent[1][1] == [0.5]
    assert bridge_client.sent[3][1] == [3]
    assert bridge_client.sent[4][1] == [60, 100]
    assert bridge_client.sent[11][1] == ["intro line"]
    bridge_client.sent.clear()

    controller.set_autotune(1, "mix", 0.5)
    controller.loop_record(2, 12.0)
    controller.loop_stop_record(3)
    controller.loop_play(1)
    controller.loop_stop(2)
    controller.loop_clear(4)
    controller.loop_stop_all()
    controller.loop_set_default_length(10.0)
    controller.loop_set_mode(1, "beat")
    controller.loop_set_beats(1, 7)
    controller.loop_set_length(2, 12.0)
    controller.loop_dub(3)
    controller.set_loop_edit(2, "rate", 0.6666667)
    controller.set_loop_edit(2, "count_in", "1 bar")
    controller.set_loop_edit(2, "grain_motion", "pingpong")
    controller.set_loop_edit(2, "grain_speed", 0.04)
    controller.set_loop_edit(2, "grain_pitch_snap", 0)
    controller.set_loop_edit(2, "grain_smear", 0.25)
    controller.set_loop_edit(2, "grain_morph", 0.5)
    controller.loop_mem_save(0, 1)
    controller.loop_mem_recall(1, 4)
    controller.loop_mem_clear(2)
    controller.loop_fx_fire(4)
    controller.request_state()
    assert sc_client.addresses() == [
        "/ji/autotune/set",
        "/ji/loop/record_length",
        "/ji/loop/stop_record",
        "/ji/loop/play",
        "/ji/loop/stop",
        "/ji/loop/clear",
        "/ji/loop/stop_all",
        "/ji/loop/set_default_length",
        "/ji/loop/set_mode",
        "/ji/loop/set_beats",
        "/ji/loop/set_length",
        "/ji/loop/dub",
        "/ji/loop/edit_set",
        "/ji/loop/edit_set",
        "/ji/loop/edit_set",
        "/ji/loop/edit_set",
        "/ji/loop/edit_set",
        "/ji/loop/edit_set",
        "/ji/loop/edit_set",
        "/ji/loop/mem_save",
        "/ji/loop/mem_recall",
        "/ji/loop/mem_clear",
        "/ji/loop/fx_fire",
        "/ji/status/query",
    ]
    assert sc_client.sent[1][1] == [2, 12.0]
    assert sc_client.sent[10][1] == [2, 12.0]
    assert sc_client.sent[11][1] == [3]
    assert sc_client.sent[12][1] == [2, "rate", 0.6666667]
    assert sc_client.sent[13][1] == [2, "count_in", "1 bar"]
    assert sc_client.sent[14][1] == [2, "grain_motion", "pingpong"]
    assert sc_client.sent[15][1] == [2, "grain_speed", 0.04]
    assert sc_client.sent[16][1] == [2, "grain_pitch_snap", 0]
    assert sc_client.sent[17][1] == [2, "grain_smear", 0.25]
    assert sc_client.sent[18][1] == [2, "grain_morph", 0.5]
    assert sc_client.sent[19][1] == [0, 1]
    assert sc_client.sent[20][1] == [1, 4]
    assert sc_client.sent[21][1] == [2]
    assert sc_client.sent[22][1] == [4]

    bridge_client.sent.clear()
    controller.set_tempo(140)
    controller.set_meter(3, 4)
    controller.set_transport(True)
    controller.set_drums_enabled(True)
    controller.set_drum_lane(36, False)
    controller.set_drum_rate(2.0)
    controller.set_drums_preset("Break")
    controller.set_groove_modifier("density", 0.4)
    controller.set_drum_step(2, 5, True)
    controller.set_drum_seed(9)
    controller.reset_drum_grid()
    controller.request_rhythm_resync()
    assert bridge_client.addresses() == [
        "/ji/rhythm/set_tempo",
        "/ji/rhythm/set_meter",
        "/ji/rhythm/transport",
        "/ji/drums/set_enabled",
        "/ji/drums/set_lane",
        "/ji/drums/set_rate",
        "/ji/drums/set_preset",
        "/ji/drums/set_density",
        "/ji/drums/set_step",
        "/ji/drums/set_seed",
        "/ji/drums/reset_grid",
        "/ji/rhythm/request",
    ]
    assert bridge_client.sent[0][1] == [140.0]
    assert bridge_client.sent[1][1] == [3, 4]
    assert bridge_client.sent[2][1] == [1]
    assert bridge_client.sent[4][1] == [36, 0]
    assert bridge_client.sent[5][1] == [2.0]
    assert bridge_client.sent[6][1] == ["break"]
    assert bridge_client.sent[7][1] == [0.4]
    assert bridge_client.sent[8][1] == [2, 5, 1]
    assert bridge_client.sent[9][1] == [9]
    assert bridge_client.sent[10][1] == []
    assert bridge_client.sent[-1][1] == []

    # Status handling updates the store and invokes the callback.
    controller._handle_status("/ji/cpu", [22.5])
    assert controller.store.cpu == 22.5
    assert statuses[-1] == ("/ji/cpu", [22.5])
    before = len(statuses)
    controller._handle_status("/ji/cpu", ["bad"])
    assert len(statuses) == before

    controller.stop()
    assert controller.started is False
    assert listener.stopped is True
    assert bridge_client.closed is False  # injected clients are not owned

    # A second start reuses the same injected listener and re-announces.
    controller.start()
    assert listener.start_count == 2
    controller.stop()
    print("  listener-first start, hello/query, sends, callbacks, clean stop")


def test_controller_startup_send_failure_is_nonfatal() -> None:
    print("\n=== Test 9: missing bridge/sclang is non-fatal ===")

    class FailingClient(FakeClient):
        def send_message(self, address, args):
            raise OSError("no route")

    listener = FakeListener("127.0.0.1", 9001, lambda address, args: None)
    controller = OSCController(
        bridge_client=FailingClient("127.0.0.1", 9000),
        sc_client=FailingClient("127.0.0.1", 57120),
        listener=listener,
    )
    controller.start()
    assert controller.started is True
    controller.stop()
    print("  startup announce errors are logged, not fatal")


# ---------------------------------------------------------------------------
# Optional headless Qt smoke test
# ---------------------------------------------------------------------------


class FakeController:
    def __init__(self) -> None:
        self.calls: list[tuple] = []
        self.store = StatusStore()
        self.started = False
        self.stopped = False
        self.callback = None

    def start(self) -> None:
        self.started = True

    def stop(self) -> None:
        self.stopped = True

    def set_status_callback(self, callback) -> None:
        self.callback = callback

    def set_mutation(self, param, value) -> None:
        self.calls.append(("set_mutation", param, value))

    def trigger_mutation(self) -> None:
        self.calls.append(("trigger_mutation",))

    def trigger_octave(self) -> None:
        self.calls.append(("trigger_octave",))

    def set_autotune(self, channel, param, value) -> None:
        self.calls.append(("set_autotune", channel, param, value))

    def set_midi_enabled(self, drone, arp) -> None:
        self.calls.append(("set_midi_enabled", drone, arp))

    def loop_record(self, index, seconds=None) -> None:
        self.calls.append(("loop_record", index, seconds))

    def loop_stop_record(self, index) -> None:
        self.calls.append(("loop_stop_record", index))

    def loop_play(self, index) -> None:
        self.calls.append(("loop_play", index))

    def loop_stop(self, index) -> None:
        self.calls.append(("loop_stop", index))

    def loop_clear(self, index) -> None:
        self.calls.append(("loop_clear", index))

    def loop_stop_all(self) -> None:
        self.calls.append(("loop_stop_all",))

    def loop_set_default_length(self, seconds) -> None:
        self.calls.append(("loop_set_default_length", seconds))

    def loop_set_mode(self, index, mode) -> None:
        self.calls.append(("loop_set_mode", index, mode))

    def loop_set_beats(self, index, beats) -> None:
        self.calls.append(("loop_set_beats", index, beats))

    def loop_set_length(self, index, seconds) -> None:
        self.calls.append(("loop_set_length", index, seconds))

    def loop_dub(self, index) -> None:
        self.calls.append(("loop_dub", index))

    def loop_fx_fire(self, index) -> None:
        self.calls.append(("loop_fx_fire", index))

    def set_loop_edit(self, index, param, value) -> None:
        self.calls.append(("set_loop_edit", index, param, value))

    def loop_mem_save(self, slot, index) -> None:
        self.calls.append(("loop_mem_save", slot, index))

    def loop_mem_recall(self, slot, index) -> None:
        self.calls.append(("loop_mem_recall", slot, index))

    def loop_mem_clear(self, slot) -> None:
        self.calls.append(("loop_mem_clear", slot))

    def set_tempo(self, bpm) -> None:
        self.calls.append(("set_tempo", bpm))

    def set_meter(self, numerator, denominator) -> None:
        self.calls.append(("set_meter", numerator, denominator))

    def set_transport(self, running) -> None:
        self.calls.append(("set_transport", running))

    def request_rhythm_resync(self) -> None:
        self.calls.append(("request_rhythm_resync",))

    def set_drums_enabled(self, enabled) -> None:
        self.calls.append(("set_drums_enabled", enabled))

    def set_drum_lane(self, note, enabled) -> None:
        self.calls.append(("set_drum_lane", note, enabled))

    def set_drum_rate(self, rate) -> None:
        self.calls.append(("set_drum_rate", rate))

    def set_drums_preset(self, preset) -> None:
        self.calls.append(("set_drums_preset", preset))

    def set_groove_modifier(self, param, value) -> None:
        self.calls.append(("set_groove_modifier", param, value))

    def set_drum_step(self, lane, step, enabled) -> None:
        self.calls.append(("set_drum_step", lane, step, enabled))

    def set_drum_seed(self, seed) -> None:
        self.calls.append(("set_drum_seed", seed))

    def reset_drum_grid(self) -> None:
        self.calls.append(("reset_drum_grid",))

    # -- Stage 8.3a bass ------------------------------------------------

    def set_bass_setting(self, name, value) -> None:
        self.calls.append(("set_bass_setting", name, value))

    def set_bass_enabled(self, enabled) -> None:
        self.calls.append(("set_bass_enabled", enabled))

    def set_bass_preset(self, preset) -> None:
        self.calls.append(("set_bass_preset", preset))

    def set_bass_grid(self, grid) -> None:
        self.calls.append(("set_bass_grid", grid))

    def set_bass_length(self, steps) -> None:
        self.calls.append(("set_bass_length", steps))

    def set_bass_param(self, param, value) -> None:
        self.calls.append(("set_bass_param", param, value))

    def set_bass_pitch_mode(self, mode) -> None:
        self.calls.append(("set_bass_pitch_mode", mode))

    def set_bass_root_downbeat(self, enabled) -> None:
        self.calls.append(("set_bass_root_downbeat", enabled))

    def set_bass_octave_range(self, value) -> None:
        self.calls.append(("set_bass_octave_range", value))

    def set_bass_base_octave(self, value) -> None:
        self.calls.append(("set_bass_base_octave", value))

    def set_bass_seed(self, seed) -> None:
        self.calls.append(("set_bass_seed", seed))

    def set_bass_sub_enabled(self, enabled) -> None:
        self.calls.append(("set_bass_sub_enabled", enabled))

    def set_bass_sub_octaves(self, value) -> None:
        self.calls.append(("set_bass_sub_octaves", value))

    # -- Stage 8.7 breath + bass hit row -------------------------------

    def set_breath_param(self, name, value) -> None:
        self.calls.append(("set_breath_param", name, value))

    def bass_set_hits_enabled(self, enabled) -> None:
        self.calls.append(("bass_set_hits_enabled", enabled))

    def bass_set_hit(self, index, on) -> None:
        self.calls.append(("bass_set_hit", index, on))

    def bass_clear_hits(self) -> None:
        self.calls.append(("bass_clear_hits",))

    def bass_copy_hits(self) -> None:
        self.calls.append(("bass_copy_hits",))

    # -- Stage 8.3b arp ------------------------------------------------

    def set_arp_setting(self, name, value) -> None:
        self.calls.append(("set_arp_setting", name, value))

    def set_arp_param(self, name, value) -> None:
        self.calls.append(("set_arp_param", name, value))

    def set_arp_step(self, seconds) -> None:
        self.calls.append(("set_arp_step", seconds))

    def set_arp_gate(self, seconds, step=None) -> None:
        self.calls.append(("set_arp_gate", seconds, step))

    def set_arp_velocity(self, velocity) -> None:
        self.calls.append(("set_arp_velocity", velocity))

    def set_arp_octaves(self, shared, non_shared_1, non_shared_2) -> None:
        self.calls.append(
            ("set_arp_octaves", shared, non_shared_1, non_shared_2)
        )

    def set_arp_shared_mode(self, mode) -> None:
        self.calls.append(("set_arp_shared_mode", mode))

    def set_arp_order(self, order) -> None:
        self.calls.append(("set_arp_order", order))

    def set_arp_seed(self, seed) -> None:
        self.calls.append(("set_arp_seed", seed))

    def set_arp_sync(self, sync) -> None:
        self.calls.append(("set_arp_sync", sync))

    def set_arp_division(self, division) -> None:
        self.calls.append(("set_arp_division", division))

    def set_arp_evolve(self, enabled) -> None:
        self.calls.append(("set_arp_evolve", enabled))

    def set_arp_evolve_beats(self, beats) -> None:
        self.calls.append(("set_arp_evolve_beats", beats))

    def set_arp_evolve_depth(self, depth) -> None:
        self.calls.append(("set_arp_evolve_depth", depth))

    # -- Stage 8.3d ------------------------------------------------------

    def set_lead_setting(self, name, value) -> None:
        self.calls.append(("set_lead_setting", name, value))

    def set_lead_param(self, name, value) -> None:
        self.calls.append(("set_lead_param", name, value))

    def set_lead_enabled(self, enabled) -> None:
        self.calls.append(("set_lead_enabled", enabled))

    def vary_lead(self) -> None:
        self.calls.append(("vary_lead",))

    # -- Stage 9.1 lead input --------------------------------------------

    def set_lead_input_enabled(self, enabled) -> None:
        self.calls.append(("set_lead_input_enabled", enabled))

    def set_lead_input_quantize(self, value) -> None:
        self.calls.append(("set_lead_input_quantize", value))

    def set_lead_input_count_in(self, value) -> None:
        self.calls.append(("set_lead_input_count_in", value))

    def set_lead_input_bars(self, value) -> None:
        self.calls.append(("set_lead_input_bars", value))

    def lead_input_note_on(self, midi, velocity) -> None:
        self.calls.append(("lead_input_note_on", midi, velocity))

    def lead_input_note_off(self, midi) -> None:
        self.calls.append(("lead_input_note_off", midi))

    def lead_input_record(self) -> None:
        self.calls.append(("lead_input_record",))

    def lead_input_stop(self) -> None:
        self.calls.append(("lead_input_stop",))

    def lead_input_revert(self) -> None:
        self.calls.append(("lead_input_revert",))

    def lead_input_clear(self) -> None:
        self.calls.append(("lead_input_clear",))

    def refresh_lead_input(self) -> None:
        self.calls.append(("refresh_lead_input",))

    def melody_save(self, name) -> None:
        self.calls.append(("melody_save", name))

    def melody_load(self, name) -> None:
        self.calls.append(("melody_load", name))

    def melody_delete(self, name) -> None:
        self.calls.append(("melody_delete", name))

    def request_melody_list(self) -> None:
        self.calls.append(("request_melody_list",))

    def set_drone_setting(self, name, value) -> None:
        self.calls.append(("set_drone_setting", name, value))

    def set_drone_param(self, name, value) -> None:
        self.calls.append(("set_drone_param", name, value))

    # -- Stage 8.4 home sets ---------------------------------------------

    def home_capture(self, slot) -> None:
        self.calls.append(("home_capture", slot))

    def home_clear(self, slot) -> None:
        self.calls.append(("home_clear", slot))

    def home_go(self, slot) -> None:
        self.calls.append(("home_go", slot))

    def home_return(self) -> None:
        self.calls.append(("home_return",))

    def home_jump(self) -> None:
        self.calls.append(("home_jump",))

    def home_back(self) -> None:
        self.calls.append(("home_back",))

    def set_home_setting(self, name, value) -> None:
        self.calls.append(("set_home_setting", name, value))

    def state_save(self, name) -> None:
        self.calls.append(("state_save", name))

    def state_load(self, name) -> None:
        self.calls.append(("state_load", name))

    def state_delete(self, name) -> None:
        self.calls.append(("state_delete", name))

    def request_state_list(self) -> None:
        self.calls.append(("request_state_list",))


def run_qt_tests() -> int:
    print("\n=== Qt smoke test: one window, widgets, signals, close ===")
    os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")
    try:
        import PyQt6  # noqa: F401
        from PyQt6.QtCore import QEvent, Qt
        from PyQt6.QtGui import QKeyEvent
        from PyQt6.QtWidgets import QApplication, QGroupBox
    except ImportError as exc:
        print(
            "SKIP: PyQt6/Qt could not be imported "
            f"({exc}); --pure does not need it."
        )
        return 0

    from ui.ji_ui import MainWindow

    app = QApplication.instance() or QApplication([])
    fake = FakeController()
    window = MainWindow(fake, autostart=False)
    window.show()
    app.processEvents()

    titles = {group.title() for group in window.findChildren(QGroupBox)}
    for expected in (
        "Mutation",
        "Live Autotune",
        "Loops",
        "Rhythm",
        "Groove",
        "Bass",
        "Arp",
        "Drone modes",
        "Lead",
        "Lead input",
        "Fretboard",
        "MIDI",
        "Home",
        "State",
        "Status",
    ):
        assert expected in titles, f"missing {expected} group: {titles}"
    assert window.show_grid_checkbox.isChecked() is False
    assert window.grid_scroll.isVisible() is False
    assert len(window.step_cells) == 0, "grid must be built lazily"
    assert "waiting" in window.quantize_label.text()
    # Stage 9.0 adds the Harm row (expect ~1280x1198); the Qt bound is
    # 1230.  The scroll area remains the small-screen fallback.
    assert window.width() <= 1400 and window.height() <= 1290, (
        window.width(),
        window.height(),
    )
    # The three loop rows exist, with the 8.6.1 defaults shown.
    assert window.grain_motion_combo.count() == len(LOOP_GRAIN_MOTIONS)
    assert window.grain_motion_combo.currentText() == "forward"
    assert abs(window.grain_speed_spin.value() - 0.03) < 1e-9
    assert abs(window.grain_depth_spin.value() - 60.0) < 1e-9
    assert abs(window.grain_size_jitter_spin.value() - 20.0) < 1e-9
    assert window.grain_snap_checkbox.isChecked()
    assert not window.grain_freeze_checkbox.isChecked()
    assert abs(window.grain_rate_spin.value() - 20.0) < 1e-9
    assert abs(window.grain_size_spin.value() - 120.0) < 1e-9
    assert abs(window.grain_pos_spin.value() - 50.0) < 1e-9
    assert abs(window.grain_scatter_spin.value() - 10.0) < 1e-9
    assert abs(window.grain_morph_spin.value() - 0.0) < 1e-9
    assert not window.grain_melt_checkbox.isChecked()
    assert abs(window.grain_filter_spin.value() - 0.0) < 1e-9
    assert abs(window.grain_filter_rate_spin.value() - 0.12) < 1e-9
    assert abs(window.grain_breath_spin.value() - 0.0) < 1e-9
    assert abs(window.grain_breath_rate_spin.value() - 0.08) < 1e-9

    # --- Stage 9.0 Harm row: defaults, sends, Fire, incoming sync ------
    assert window.harm_mode_combo.currentText() == "off"
    assert window.harm_time_sync_combo.currentText() == "free"
    assert abs(window.harm_mix_spin.value() - 35.0) < 1e-9
    assert abs(window.harm_time_spin.value() - 0.50) < 1e-9
    assert window.harm_steps_spin.value() == 4
    assert abs(window.harm_spacing_spin.value() - 80.0) < 1e-9
    assert abs(window.harm_falloff_spin.value() - 50.0) < 1e-9
    assert window.harm_auto_checkbox.isChecked()
    assert not window.harm_fire_button.isEnabled()
    assert not window.harm_mix_spin.isEnabled()
    fake.calls.clear()
    window.harm_mode_combo.setCurrentText("Cascade")
    assert ("set_loop_edit", 1, "harm_mode", "cascade") in fake.calls, fake.calls
    assert window.harm_mix_spin.isEnabled()
    assert window.harm_time_spin.isEnabled()
    assert window.harm_spacing_spin.isEnabled()
    fake.calls.clear()
    window.harm_mix_spin.setValue(45)
    assert ("set_loop_edit", 1, "harm_mix", 0.45) in fake.calls, fake.calls
    window.harm_time_spin.setValue(0.75)
    assert ("set_loop_edit", 1, "harm_time", 0.75) in fake.calls, fake.calls
    window.harm_steps_spin.setValue(6)
    assert ("set_loop_edit", 1, "harm_steps", 6) in fake.calls, fake.calls
    window.harm_spacing_spin.setValue(120)
    assert ("set_loop_edit", 1, "harm_spacing", 0.12) in fake.calls, fake.calls
    window.harm_falloff_spin.setValue(40)
    assert ("set_loop_edit", 1, "harm_falloff", 0.4) in fake.calls, fake.calls
    window.harm_auto_checkbox.setChecked(False)
    assert ("set_loop_edit", 1, "harm_auto", 0) in fake.calls, fake.calls
    window.harm_time_sync_combo.setCurrentText("1/4")
    assert (
        "set_loop_edit",
        1,
        "harm_time_sync",
        "1/4",
    ) in fake.calls, fake.calls
    assert window.harm_time_spin.isEnabled(), "fixed time stays focusable"
    assert window.harm_time_spin.isReadOnly(), "fixed time is read-only"
    # Fire is enabled only in Cascade while the selected loop is playing.
    window._on_status_message("/ji/loop/state", [1, "playing"])
    assert window.harm_fire_button.isEnabled()
    fake.calls.clear()
    window.harm_fire_button.click()
    # Fire re-asserts the visible Cascade mode first, then sends the trigger.
    assert ("set_loop_edit", 1, "harm_mode", "cascade") in fake.calls, fake.calls
    assert ("loop_fx_fire", 1) in fake.calls, fake.calls
    assert all(c[0] in ("set_loop_edit", "loop_fx_fire") for c in fake.calls)
    # An incoming 39-value edit syncs every widget with no feedback.
    fake.calls.clear()
    window._on_status_message(
        "/ji/loop/edit",
        [
            1, 0, 1.0, 0.7, 0, 0, "1/16", 8, 80, 0, 0, 1, 0, 0, 1, 0, 0,
            "forward", 0.03, 0.6, 0.6, 0, "off", 0, 1.0, 0.0, 0, 0.0, 0.12,
            0.0, 0.08, "delay", 0.60, 1.25, "1/8d", 7, 0.25, 0.80, 0,
        ],
    )
    assert window.harm_mode_combo.currentText() == "Delay"
    assert window.harm_time_sync_combo.currentText() == "1/8d"
    assert abs(window.harm_mix_spin.value() - 60.0) < 1e-9
    #  1/8d at 120 BPM = 0.375 s; the fixed-time spin shows the derived value.
    assert abs(window.harm_time_spin.value() - 0.375) < 0.006
    assert window.harm_steps_spin.value() == 7
    assert abs(window.harm_spacing_spin.value() - 250.0) < 1e-9
    assert abs(window.harm_falloff_spin.value() - 80.0) < 1e-9
    assert not window.harm_auto_checkbox.isChecked()
    assert not window.harm_fire_button.isEnabled(), "Fire is inert in Delay"
    assert not fake.calls, fake.calls
    # Back to off for the remaining layout/height checks.
    window.harm_mode_combo.setCurrentText("off")

    # Stage 8.7: the Breath row defaults and the hidden bass hit row.
    assert not window.breath_enable_checkbox.isChecked()
    assert window.breath_depth_spin.value() == 35
    assert window.breath_period_spin.value() == 4
    assert window.breath_shape_combo.currentText() == "inhale"
    assert window.breath_intensity_spin.value() == 50
    assert window.breath_phase_spin.value() == 0
    assert window.breath_arp_spin.value() == 0
    assert not window.breath_arp_sync_checkbox.isChecked()
    assert window.breath_hint.text() == "breath off"
    assert window.bass_hits_checkbox.isChecked() is False
    assert window.bass_hits_scroll.isVisible() is False
    assert window.bass_hit_buttons == {}
    # Stage 8.3a bass defaults; controls wait disabled until the bridge says
    # the bass port is available.
    assert window.bass_preset_combo.currentText() == "pulse"
    assert window.bass_grid_combo.currentText() == "1/4"
    assert window.bass_pitch_combo.currentText() == "root"
    assert window.bass_length_spin.value() == 16
    assert window.bass_sub_checkbox.isChecked() is False
    assert window.bass_base_octave_spin.value() == -2
    assert window.bass_seed_spin.value() == 1
    assert not window.bass_enable_checkbox.isEnabled()

    # Incoming bass state syncs every widget and never echoes OSC.
    fake.calls.clear()
    window._on_status_message(
        "/ji/bass/state",
        [1, 1, "techno", "1/16", 16, 0.55, 0.15, 0.1, 0.12, 0.25,
         "walk", 0.45, 1, 0.05, 4, -2, 0, 1, 2, 3],
    )
    assert window.bass_preset_combo.currentText() == "techno"
    assert window.bass_grid_combo.currentText() == "1/16"
    assert window.bass_pitch_combo.currentText() == "walk"
    assert window.bass_enable_checkbox.isChecked() is True
    assert abs(window.bass_knobs["density"].value() - 0.55) < 1e-2
    assert window.bass_octave_range_spin.value() == 1
    assert window.bass_seed_spin.value() == 4
    assert window.bass_enable_checkbox.isEnabled()
    assert "techno" in window.bass_label.text()
    assert not [call for call in fake.calls if call[0].startswith("set_bass_")]

    window._on_status_message("/ji/bass/note", [55.0, 33, 98])
    assert "A1" in window.bass_note_label.text()
    assert "55.00" in window.bass_note_label.text()

    # --- Stage 8.7 breath row sends and echo sync -----------------------
    fake.calls.clear()
    window.breath_enable_checkbox.setChecked(True)
    assert ("set_breath_param", "enabled", 1) in fake.calls, fake.calls
    window.breath_depth_spin.setValue(60)
    assert ("set_breath_param", "depth", 0.6) in fake.calls, fake.calls
    window.breath_period_spin.setValue(8)
    assert ("set_breath_param", "period_bars", 8) in fake.calls, fake.calls
    window.breath_shape_combo.setCurrentText("sine")
    assert ("set_breath_param", "shape", "sine") in fake.calls, fake.calls
    window.breath_intensity_spin.setValue(70)
    assert ("set_breath_param", "intensity", 0.7) in fake.calls, fake.calls
    window.breath_phase_spin.setValue(25)
    assert ("set_breath_param", "phase", 0.25) in fake.calls, fake.calls
    window.breath_arp_spin.setValue(60)
    assert ("set_breath_param", "arp_local", 0.6) in fake.calls, fake.calls
    window.breath_arp_sync_checkbox.setChecked(True)
    assert ("set_breath_param", "arp_sync", 1) in fake.calls, fake.calls
    fake.calls.clear()
    window._on_status_message(
        "/ji/breath/state", [1, 0.4, 16, "exhale", 0.2, 0.5, 0.1, 1]
    )
    assert window.breath_enable_checkbox.isChecked()
    assert window.breath_depth_spin.value() == 40
    assert window.breath_period_spin.value() == 16
    assert window.breath_shape_combo.currentText() == "exhale"
    assert window.breath_intensity_spin.value() == 20
    assert window.breath_phase_spin.value() == 50
    assert window.breath_arp_spin.value() == 10
    assert window.breath_arp_sync_checkbox.isChecked()
    assert "breathing" in window.breath_hint.text()
    assert not [c for c in fake.calls if c[0] == "set_breath_param"], fake.calls

    # --- Stage 8.7 bass hit row (hidden until Hits is on) ----------------
    fake.calls.clear()
    window._on_status_message("/ji/bass/hits", [0, 4, 1, 0, 0, 1])
    assert window.bass_hits_checkbox.isChecked() is False
    assert len(window.bass_hit_buttons) == 4
    assert window.bass_hits_scroll.isVisible() is False
    assert window.bass_hit_buttons[0].isChecked()
    assert not fake.calls, fake.calls
    window._on_status_message("/ji/bass/hits", [1, 4, 1, 0, 1, 0])
    assert window.bass_hits_checkbox.isChecked() is True
    assert window.bass_hits_scroll.isVisible() is True
    assert window.bass_hit_buttons[2].isChecked()
    window._on_status_message("/ji/bass/hits", [1, 16] + [0] * 16)
    assert len(window.bass_hit_buttons) == 16
    fake.calls.clear()
    window.bass_hit_buttons[3].setChecked(True)
    assert ("bass_set_hit", 3, True) in fake.calls, fake.calls
    window.bass_hits_checkbox.setChecked(False)
    assert ("bass_set_hits_enabled", False) in fake.calls, fake.calls
    window.bass_hits_copy_button.click()
    assert ("bass_copy_hits",) in fake.calls, fake.calls
    window.bass_hits_clear_button.click()
    assert ("bass_clear_hits",) in fake.calls, fake.calls
    assert not [c for c in fake.calls if c[0] == "set_bass_"], fake.calls

    # Preset/knob/spin sends.
    fake.calls.clear()
    window.bass_preset_combo.setCurrentText("chaos")
    assert ("set_bass_preset", "chaos") in fake.calls
    window.bass_grid_combo.setCurrentText("1/8")
    assert ("set_bass_grid", "1/8") in fake.calls
    window.bass_pitch_combo.setCurrentText("cycle")
    assert ("set_bass_pitch_mode", "cycle") in fake.calls
    window.bass_length_spin.setValue(12)
    assert ("set_bass_length", 12) in fake.calls
    window.bass_sub_checkbox.setChecked(True)
    assert ("set_bass_sub_enabled", True) in fake.calls
    window.bass_sub_octaves_spin.setValue(2)
    assert ("set_bass_sub_octaves", 2) in fake.calls
    window.bass_octave_range_spin.setValue(2)
    assert ("set_bass_octave_range", 2) in fake.calls
    window.bass_base_octave_spin.setValue(-1)
    assert ("set_bass_base_octave", -1) in fake.calls
    window.bass_seed_spin.setValue(9)
    assert ("set_bass_seed", 9) in fake.calls
    window.bass_knobs["density"].set_value(0.4, emit=True)
    window._flush_pending()
    assert ("set_bass_param", "density", 0.4) in fake.calls

    window._on_status_message("/ji/bass/state", [0, 0, "pulse", "1/4", 4,
        0.0, 0.0, 0.0, 3.5, 0.0, "root", 0.0, 0, 0.0, 1, -2, 0, 1, 2, 3])
    assert not window.bass_enable_checkbox.isEnabled()
    assert "disabled" in window.bass_label.text()

    # --- Stage 8.8 fretboard aid (read-only; no OSC) --------------------
    fake.calls.clear()
    assert window.fretboard_tuning_combo.currentText() == "Standard E"
    assert window.fretboard_strings_spin.value() == 6
    assert window.fretboard_frets_spin.value() == 24
    assert [
        combo.currentText() for combo in window.fretboard_string_combos[:6]
    ] == ["E2", "A2", "D3", "G3", "B3", "E4"]
    assert window.fretboard_names_checkbox.isChecked()
    assert "waiting" in window.fretboard_set_label.text()
    assert window.fretboard_widget.degrees == ()
    assert len(window.fretboard_widget.cells) == 6 * 25

    window._on_status_message(
        "/ji/freqset/changed",
        [220.0, -1.0, 1.0, 9.0 / 8.0, 5.0 / 4.0, 4.0 / 3.0, 3.0 / 2.0,
         5.0 / 3.0, 15.0 / 8.0],
    )
    assert "A B C# D E F# G#" in window.fretboard_set_label.text()
    assert "5/4" in window.fretboard_legend_label.text()
    assert "-14" in window.fretboard_legend_label.text()
    assert [degree.name for degree in window.fretboard_widget.degrees] == [
        "A", "B", "C#", "D", "E", "F#", "G#"
    ]
    cells = window.fretboard_widget.cells

    def _fret_cell(string, fret):
        return next(
            cell for cell in cells
            if cell.string == string and cell.fret == fret
        )

    assert _fret_cell(3, 0).degree is None, "open G is not in A major"
    assert _fret_cell(3, 1).degree is not None
    assert _fret_cell(3, 1).degree.name == "G#"
    assert _fret_cell(1, 0).is_root is True
    assert _fret_cell(3, 0).is_root is False
    assert not fake.calls, fake.calls

    # A whole-neck preset replaces the tuning; no controller call.
    fake.calls.clear()
    window.fretboard_tuning_combo.setCurrentText("Drop D")
    assert [
        window.fretboard_string_combos[index].itemData(
            window.fretboard_string_combos[index].currentIndex()
        )
        for index in range(6)
    ] == [38, 45, 50, 55, 59, 64]
    assert window.fretboard_string_combos[0].currentText() == "D2"
    assert window.fretboard_widget.tuning.strings[0] == 38
    assert not fake.calls, fake.calls

    # Editing one string makes the preset Custom; still no OSC.
    fake.calls.clear()
    low = window.fretboard_string_combos[0]
    low.setCurrentIndex(low.findData(36))
    assert window.fretboard_tuning_combo.currentText() == "Custom"
    assert window.fretboard_string_combos[0].currentText() == "C2"
    assert window.fretboard_widget.tuning.strings[0] == 36
    assert not fake.calls, fake.calls

    # Seven strings: Standard E gains B1; seven combos visible.
    window.fretboard_tuning_combo.setCurrentText("Standard E")
    window.fretboard_strings_spin.setValue(7)
    assert window.fretboard_string_combos[0].currentText() == "B1"
    assert sum(
        1 for combo in window.fretboard_string_combos if combo.isVisible()
    ) == 7
    assert window.fretboard_widget.tuning.string_count == 7
    assert len(window.fretboard_widget.cells) == 7 * 25

    # Twelve frets: the cell count is strings x (frets + 1).
    window.fretboard_strings_spin.setValue(6)
    window.fretboard_frets_spin.setValue(12)
    assert window.fretboard_widget.frets == 12
    assert len(window.fretboard_widget.cells) == 6 * 13

    # Names off hides text only; no OSC.
    fake.calls.clear()
    window.fretboard_names_checkbox.setChecked(False)
    assert window.fretboard_widget.show_names is False
    assert window.fretboard_widget.degrees, "markers must survive Names off"
    assert not fake.calls, fake.calls
    window.fretboard_names_checkbox.setChecked(True)
    window.fretboard_frets_spin.setValue(24)
    assert not fake.calls, fake.calls
    # The temporary 7-string board is 18 px taller; restore the default size
    # before the remaining height checks (the layout may have grown with it).
    window.resize(1280, 1198)
    app.processEvents()

    # --- Stage 8.3b Arp group -------------------------------------------
    assert window.arp_order_combo.currentText() == ARP_DEFAULT_ORDER
    assert window.arp_shared_combo.currentText() == ARP_SHARED_MODES[0]
    assert window.arp_seed_spin.value() == ARP_DEFAULT_SEED
    assert [spin.value() for spin in window.arp_octave_spins] == list(
        ARP_DEFAULT_OCTAVES
    )
    assert abs(window.arp_knobs["step_seconds"].value() - 0.25) < 1e-2
    assert abs(window.arp_knobs["gate_seconds"].value() - 0.20) < 1e-2
    assert window.arp_knobs["velocity"].value() == 90
    # Nothing has arrived yet: waiting label, controls disabled.
    assert "waiting for bridge" in window.arp_label.text()
    assert not window.arp_order_combo.isEnabled()
    assert not window.arp_knobs["step_seconds"].isEnabled()
    # Stage 8.3c defaults: free sync, 1/8, evolution off.
    assert window.arp_sync_combo.currentText() == "free"
    assert window.arp_division_combo.currentText() == ARP_DEFAULT_DIVISION
    assert window.arp_evolve_checkbox.isChecked() is False
    assert window.arp_evolve_beats_spin.value() == 4
    assert abs(window.arp_depth_knob.value() - 0.0) < 1e-9

    # Incoming state syncs every widget with signals blocked (no feedback OSC).
    fake.calls.clear()
    window._on_status_message(
        "/ji/arp/state",
        [1, 1, 0.5, 0.5, 0.4, 40, -1, 0, 2, "random", "pingpong", 9,
         "free", "1/8", 0, 4, 0.0],
    )
    assert window.arp_order_combo.currentText() == "pingpong"
    assert window.arp_shared_combo.currentText() == "random"
    assert window.arp_seed_spin.value() == 9
    assert [spin.value() for spin in window.arp_octave_spins] == [-1, 0, 2]
    assert abs(window.arp_knobs["step_seconds"].value() - 0.5) < 1e-2
    assert window.arp_knobs["velocity"].value() == 40
    assert window.arp_order_combo.isEnabled()
    assert "on | 0.50 s / 0.40 s | pingpong | random | seed 9" in (
        window.arp_label.text()
    )
    assert not [c for c in fake.calls if str(c[0]).startswith("set_arp")], (
        fake.calls
    )

    # Combos and spins send immediately; knobs coalesce then flush.
    # Beat sync: the derived step is shown, the step knob is disabled and the
    # division combo becomes usable; the evolve row follows the checkbox.
    window._on_status_message(
        "/ji/arp/state",
        [1, 1, 0.5, 0.25, 0.20, 40, -1, 0, 2, "random", "pingpong", 9,
         "beat", "1/8", 1, 8, 0.35],
    )
    assert window.arp_sync_combo.currentText() == "beat"
    assert window.arp_division_combo.currentText() == "1/8"
    assert not window.arp_knobs["step_seconds"].isEnabled()
    assert window.arp_division_combo.isEnabled()
    assert window.arp_evolve_checkbox.isChecked()
    assert window.arp_evolve_beats_spin.value() == 8
    assert abs(window.arp_depth_knob.value() - 0.35) < 1e-2
    assert "beat 1/8 = 0.25 s / 0.20 s" in window.arp_label.text(), (
        window.arp_label.text()
    )
    assert "evolve 8 b depth 0.35" in window.arp_label.text(), (
        window.arp_label.text()
    )
    assert not [c for c in fake.calls if str(c[0]).startswith("set_arp")], (
        fake.calls
    )

    # Back to free: the step knob is usable again.
    window._on_status_message(
        "/ji/arp/state",
        [1, 1, 0.5, 0.5, 0.20, 40, -1, 0, 2, "random", "pingpong", 9,
         "free", "1/8", 0, 4, 0.0],
    )
    assert window.arp_knobs["step_seconds"].isEnabled()
    assert not window.arp_division_combo.isEnabled()
    assert not window.arp_evolve_beats_spin.isEnabled()
    assert not window.arp_depth_knob.isEnabled()
    assert "beat" not in window.arp_label.text()

    fake.calls.clear()
    window.arp_sync_combo.setCurrentText("beat")
    assert ("set_arp_sync", "beat") in fake.calls, fake.calls
    window.arp_division_combo.setCurrentText("1/4")
    assert ("set_arp_division", "1/4") in fake.calls, fake.calls
    window.arp_evolve_checkbox.setChecked(True)
    assert ("set_arp_evolve", True) in fake.calls, fake.calls
    window.arp_evolve_beats_spin.setValue(16)
    assert ("set_arp_evolve_beats", 16) in fake.calls, fake.calls
    window.arp_depth_knob.set_value(0.6, emit=True)
    assert not [c for c in fake.calls if c[0] == "set_arp_param"], fake.calls
    window._flush_pending()
    assert ("set_arp_param", "evolve_depth", 0.6) in fake.calls, fake.calls
    window.arp_sync_combo.setCurrentText("free")
    window.arp_evolve_checkbox.setChecked(False)

    fake.calls.clear()
    window.arp_order_combo.setCurrentText("reverse")
    assert ("set_arp_order", "reverse") in fake.calls, fake.calls
    window.arp_shared_combo.setCurrentText("anchor")
    assert ("set_arp_shared_mode", "anchor") in fake.calls, fake.calls
    window.arp_octave_spins[1].setValue(3)
    assert ("set_arp_octaves", -1, 3, 2) in fake.calls, fake.calls
    window.arp_seed_spin.setValue(4)
    assert ("set_arp_seed", 4) in fake.calls, fake.calls
    window.arp_knobs["velocity"].set_value(70, emit=True)
    assert not [c for c in fake.calls if c[0] == "set_arp_param"], fake.calls
    window._flush_pending()
    assert ("set_arp_param", "velocity", 70) in fake.calls, fake.calls

    # The message line reflects queued vs immediate application.
    window._on_status_message("/ji/rhythm/state", [120.0, 4, 4, 1, 1, 1.0])
    window.arp_order_combo.setCurrentText("forward")
    assert "queued for the next beat" in window.message_label.text(), (
        window.message_label.text()
    )
    window._on_status_message("/ji/rhythm/state", [120.0, 4, 4, 0, 1, 1.0])
    window.arp_order_combo.setCurrentText("pingpong")
    assert "applied immediately" in window.message_label.text(), (
        window.message_label.text()
    )

    # MIDI/Bus 2 unavailable: disabled controls, explicit label, no sends.
    fake.calls.clear()
    window._on_status_message(
        "/ji/arp/state",
        [0, 0, 0.25, 0.25, 0.2, 90, 0, 1, 2, "anchor", "forward", 1,
         "free", "1/8", 0, 4, 0.0],
    )
    assert not window.arp_order_combo.isEnabled()
    assert not window.arp_seed_spin.isEnabled()
    assert not window.arp_knobs["step_seconds"].isEnabled()
    assert "disabled" in window.arp_label.text(), window.arp_label.text()
    assert not [c for c in fake.calls if str(c[0]).startswith("set_arp")], (
        fake.calls
    )

    # --- Stage 8.3d Lead group ------------------------------------------
    assert window.lead_grid_combo.currentText() == LEAD_DEFAULT_GRID
    assert window.lead_variation_combo.currentText() == LEAD_DEFAULT_VARIATION
    assert window.lead_phrase_spin.value() == LEAD_DEFAULT_PHRASE_BARS
    assert abs(window.lead_knobs["density"].value() - 0.75) < 1e-2
    assert window.lead_octave_range_spin.value() == LEAD_DEFAULT_OCTAVE_RANGE
    assert window.lead_base_octave_spin.value() == LEAD_DEFAULT_BASE_OCTAVE
    assert window.lead_seed_spin.value() == LEAD_DEFAULT_SEED
    assert window.lead_variation_bars_spin.value() == LEAD_DEFAULT_VARIATION_BARS
    assert window.lead_groove_link_checkbox.isChecked() is True
    assert "waiting for bridge" in window.lead_label.text()
    assert not window.lead_enable_checkbox.isEnabled()
    assert not window.lead_grid_combo.isEnabled()
    assert not window.lead_vary_button.isEnabled()

    # Incoming state syncs every widget with signals blocked (no feedback OSC).
    fake.calls.clear()
    window._on_status_message(
        "/ji/lead/state",
        [1, 1, "1/16", 4, 0.4, 0.25, 80, 2, -1, "invert", 3, 2, 0.3, 0, 9, 1],
    )
    assert window.lead_enable_checkbox.isChecked() is True
    assert window.lead_grid_combo.currentText() == "1/16"
    assert window.lead_variation_combo.currentText() == "invert"
    assert window.lead_phrase_spin.value() == 4
    assert abs(window.lead_knobs["density"].value() - 0.4) < 1e-2
    assert abs(window.lead_knobs["gate_beats"].value() - 0.25) < 1e-2
    assert window.lead_knobs["velocity"].value() == 80
    assert window.lead_octave_range_spin.value() == 2
    assert window.lead_base_octave_spin.value() == -1
    assert window.lead_variation_bars_spin.value() == 2
    assert window.lead_seed_spin.value() == 9
    assert window.lead_groove_link_checkbox.isChecked() is False
    assert window.lead_enable_checkbox.isEnabled()
    assert window.lead_vary_button.isEnabled()
    assert "on | 1/16 · 4 bars · 0.40 | invert #3 | seed 9" in (
        window.lead_label.text()
    ), window.lead_label.text()
    assert not [c for c in fake.calls if str(c[0]).startswith("set_lead")], (
        fake.calls
    )

    window._on_status_message("/ji/lead/note", [440.0, 69, 80])
    assert "A4" in window.lead_note_label.text()
    assert "440.00" in window.lead_note_label.text()

    # Combos/buttons/spins send immediately; knobs coalesce then flush.
    fake.calls.clear()
    window.lead_grid_combo.setCurrentText("1/8")
    assert ("set_lead_setting", "grid", "1/8") in fake.calls, fake.calls
    window.lead_variation_combo.setCurrentText("identity")
    assert ("set_lead_setting", "variation", "identity") in fake.calls
    window.lead_phrase_spin.setValue(2)
    assert ("set_lead_setting", "phrase_bars", 2) in fake.calls
    window.lead_octave_range_spin.setValue(1)
    assert ("set_lead_setting", "octave_range", 1) in fake.calls
    window.lead_base_octave_spin.setValue(0)
    assert ("set_lead_setting", "base_octave", 0) in fake.calls
    window.lead_variation_bars_spin.setValue(4)
    assert ("set_lead_setting", "variation_bars", 4) in fake.calls
    window.lead_seed_spin.setValue(2)
    assert ("set_lead_setting", "seed", 2) in fake.calls
    window.lead_groove_link_checkbox.setChecked(True)
    assert ("set_lead_setting", "groove_link", True) in fake.calls
    window.lead_enable_checkbox.setChecked(False)
    assert ("set_lead_enabled", False) in fake.calls
    window.lead_vary_button.click()
    assert ("vary_lead",) in fake.calls
    window.lead_knobs["density"].set_value(0.6, emit=True)
    assert not [c for c in fake.calls if c[0] == "set_lead_param"], fake.calls
    window._flush_pending()
    density_calls = [
        call for call in fake.calls
        if call[0] == "set_lead_param" and call[1] == "density"
    ]
    assert density_calls and abs(density_calls[-1][2] - 0.6) < 1e-2, fake.calls

    # Queued vs immediate message text follows the transport.
    window._on_status_message("/ji/rhythm/state", [120.0, 4, 4, 1, 1, 1.0])
    window.lead_grid_combo.setCurrentText("1/4")
    assert "queued for the next beat" in window.message_label.text(), (
        window.message_label.text()
    )
    window._on_status_message("/ji/rhythm/state", [120.0, 4, 4, 0, 1, 1.0])
    window.lead_grid_combo.setCurrentText("1/8")
    assert "applied immediately" in window.message_label.text(), (
        window.message_label.text()
    )

    # Bus 5 unavailable: disabled controls, explicit label, no sends.
    fake.calls.clear()
    window._on_status_message(
        "/ji/lead/state",
        [0, 0, "1/8", 2, 0.75, 0.5, 90, 1, 0, "identity", 0, 0, 0.0, 1, 1, 0],
    )
    assert not window.lead_enable_checkbox.isEnabled()
    assert not window.lead_vary_button.isEnabled()
    assert not window.lead_grid_combo.isEnabled()
    assert "disabled" in window.lead_label.text(), window.lead_label.text()
    assert not [c for c in fake.calls if str(c[0]).startswith("set_lead")], (
        fake.calls
    )

    # --- Stage 9.1 Lead input group -------------------------------------
    assert window.lead_input_port_label.text() == "--"
    assert window.lead_input_keys_checkbox.isChecked() is False
    # Stage 9.2: the Live checkbox is gone (the input always overrides).
    assert not hasattr(window, "lead_input_live_checkbox")
    assert window.lead_input_quant_knob.value() == 1.0
    assert window.lead_input_count_in_combo.currentText() == "1 bar"
    assert window.lead_input_bars_spin.value() == 2
    assert window.lead_melody_combo.count() == 0
    assert window.lead_melody_name_edit.text() == ""
    assert not window.lead_melody_save_button.isEnabled()
    assert not window.lead_melody_load_button.isEnabled()
    assert not window.lead_melody_delete_button.isEnabled()
    assert not window.lead_input_record_button.isEnabled()
    assert not window.lead_input_stop_button.isEnabled()
    assert not window.lead_input_revert_button.isEnabled()
    assert not window.lead_input_clear_button.isEnabled()
    assert "waiting for bridge" in window.lead_input_status_label.text()

    # Running transport + an open port enables Rec; an incoming state syncs
    # everything with signals blocked (no feedback OSC).
    fake.calls.clear()
    window._on_status_message("/ji/rhythm/state", [120.0, 4, 4, 1, 1, 1.0])
    window._on_status_message(
        "/ji/lead/input/state",
        [1, 1, "Launchkey 49", 0.6, "2 bars", 3, 0, 0.0, 13, "played",
         "intro line", 61, 61, 13.7, 12.0, 64],
    )
    assert window.lead_input_port_label.text() == "Launchkey 49"
    assert window.lead_input_count_in_combo.currentText() == "2 bars"
    assert window.lead_input_bars_spin.value() == 3
    assert abs(window.lead_input_quant_knob.value() - 0.6) < 1e-2
    assert window.lead_input_record_button.isEnabled()
    assert not window.lead_input_stop_button.isEnabled()
    assert window.lead_input_revert_button.isEnabled()
    assert window.lead_input_clear_button.isEnabled()
    assert window.lead_input_status_label.text() == (
        "Input: Launchkey 49 - idle - 13 notes - bend +12 ¢ - mod 64"
    ), window.lead_input_status_label.text()
    assert window.melody_status_label.text() == (
        "Melody: intro line (13 notes) - played"
    )
    assert not [c for c in fake.calls if str(c[0]).startswith(("set_lead_input", "lead_input", "melody"))], fake.calls

    # A 14-value (9.1) payload still syncs: bend/mod default to zero.
    window._on_status_message(
        "/ji/lead/input/state",
        [1, 1, "Launchkey 49", 0.6, "2 bars", 3, 0, 0.0, 13, "played",
         "intro line", 61, 61, 13.7],
    )
    assert window.lead_input_status_label.text() == (
        "Input: Launchkey 49 - idle - 13 notes"
    ), window.lead_input_status_label.text()

    # A count-in/recording state enables Stop and shows the phase.
    window._on_status_message(
        "/ji/lead/input/state",
        [1, 1, "Launchkey 49", 0.6, "2 bars", 3, 1, 2.0, 0, "record",
         "", 61, 61, 13.7],
    )
    assert window.lead_input_stop_button.isEnabled()
    assert "count 2" in window.lead_input_status_label.text(), (
        window.lead_input_status_label.text()
    )
    window._on_status_message(
        "/ji/lead/input/state",
        [0, 1, "Launchkey 49", 0.6, "2 bars", 3, 0, 0.0, 13, "played",
         "intro line", 61, 61, 13.7],
    )

    # Buttons send immediately; the quant knob coalesces at ~30 Hz.
    fake.calls.clear()
    window.lead_input_refresh_button.click()
    assert ("refresh_lead_input",) in fake.calls
    window.lead_input_record_button.click()
    assert ("lead_input_record",) in fake.calls
    window.lead_input_stop_button.setEnabled(True)
    window.lead_input_stop_button.click()
    assert ("lead_input_stop",) in fake.calls
    window.lead_input_revert_button.click()
    assert ("lead_input_revert",) in fake.calls
    window.lead_input_clear_button.click()
    assert ("lead_input_clear",) in fake.calls
    window.lead_input_count_in_combo.setCurrentText("1 bar")
    assert ("set_lead_input_count_in", "1 bar") in fake.calls
    window.lead_input_bars_spin.setValue(4)
    assert ("set_lead_input_bars", 4) in fake.calls
    fake.calls.clear()
    window.lead_input_quant_knob.set_value(0.5, emit=True)
    assert not [c for c in fake.calls if c[0] == "set_lead_input_quantize"]
    window._flush_pending()
    quant_calls = [
        call for call in fake.calls if call[0] == "set_lead_input_quantize"
    ]
    assert quant_calls and abs(quant_calls[-1][1] - 0.5) < 1e-2, fake.calls

    # Melody list + name row: Save needs a name, Load/Del need a known name.
    fake.calls.clear()
    window._on_status_message(
        "/ji/lead/melody/list", [2, "Alpha", "intro line"]
    )
    assert window.lead_melody_combo.count() == 2
    assert window.lead_melody_combo.currentText() == "Alpha"
    assert window.lead_melody_name_edit.text() == "Alpha"
    assert window.lead_melody_load_button.isEnabled()
    assert window.lead_melody_delete_button.isEnabled()
    window.lead_melody_name_edit.clear()
    assert not window.lead_melody_save_button.isEnabled()
    window.lead_melody_name_edit.setText("new line")
    assert window.lead_melody_save_button.isEnabled()
    window.lead_melody_save_button.click()
    assert ("melody_save", "new line") in fake.calls
    window.lead_melody_combo.setCurrentText("intro line")
    window.lead_melody_load_button.click()
    assert ("melody_load", "intro line") in fake.calls
    window.lead_melody_delete_button.click()
    assert ("melody_delete", "intro line") in fake.calls

    # QWERTY: off by default; on only while a monitoring source is live.
    window.setFocus()
    app.processEvents()
    fake.calls.clear()
    press = QKeyEvent(
        QEvent.Type.KeyPress, Qt.Key.Key_A, Qt.KeyboardModifier.NoModifier, "a"
    )
    window.keyPressEvent(press)
    assert not [c for c in fake.calls if c[0] == "lead_input_note_on"]
    window.lead_input_keys_checkbox.setChecked(True)
    window.keyPressEvent(press)
    assert ("lead_input_note_on", 60, 100) in fake.calls, fake.calls
    release = QKeyEvent(
        QEvent.Type.KeyRelease, Qt.Key.Key_A,
        Qt.KeyboardModifier.NoModifier, "a"
    )
    window.keyReleaseEvent(release)
    assert ("lead_input_note_off", 60) in fake.calls, fake.calls
    fake.calls.clear()
    window.lead_input_keys_checkbox.setChecked(False)
    window.keyPressEvent(press)
    assert not [c for c in fake.calls if c[0] == "lead_input_note_on"]

    # Note len: gate_mode syncs silently and toggling sends the setting.
    window._on_status_message(
        "/ji/lead/state",
        [1, 1, "1/8", 2, 0.75, 0.5, 90, 1, 0, "identity", 0, 0, 0.0, 1, 1, 1,
         "note"],
    )
    assert window.lead_note_len_checkbox.isChecked() is True
    fake.calls.clear()
    window.lead_note_len_checkbox.setChecked(False)
    assert ("set_lead_setting", "gate_mode", "beat") in fake.calls, fake.calls
    window._on_status_message(
        "/ji/lead/state",
        [0, 0, "1/8", 2, 0.75, 0.5, 90, 1, 0, "identity", 0, 0, 0.0, 1, 1, 0],
    )

    # --- Stage 8.3d Drone modes group -----------------------------------
    assert window.drone_mode_combo.currentText() == DRONE_DEFAULT_MODE
    assert window.drone_velocity_knob.value() == 90
    assert window.drone_pulse_notes_spin.value() == DRONE_DEFAULT_PULSE_NOTES
    assert window.drone_pulse_grid_combo.currentText() == DRONE_DEFAULT_PULSE_GRID
    assert abs(window.drone_pulse_gate_knob.value() - 0.5) < 1e-2
    assert window.drone_pulse_span_spin.value() == DRONE_DEFAULT_PULSE_SPAN
    assert window.drone_direction_combo.currentText() == "down"
    assert window.drone_semitones_spin.value() == 2
    assert abs(window.drone_slide_knob.value() - 0.4) < 1e-2
    assert abs(window.drone_hold_spin.value() - 2.5) < 1e-6
    assert window.drone_seq_notes_spin.value() == DRONE_DEFAULT_SEQ_NOTES
    assert window.drone_seq_bars_combo.currentText() == DRONE_DEFAULT_SEQ_BARS
    assert abs(window.drone_seq_gate_knob.value() - 1.0) < 1e-2
    assert "waiting for bridge" in window.drone_label.text()
    assert not window.drone_mode_combo.isEnabled()

    # pulse: the pulse row is live, the bend row is inert.
    fake.calls.clear()
    window._on_status_message(
        "/ji/drone/state",
        [1, "pulse", 80, 6, "1/8", 0.25, 2, "up", 5, 0.5, 1.5, 3.0, 7, 1,
         5, "1", 1.0],
    )
    assert window.drone_mode_combo.currentText() == "pulse"
    assert window.drone_velocity_knob.value() == 80
    assert window.drone_pulse_notes_spin.value() == 6
    assert window.drone_pulse_grid_combo.currentText() == "1/8"
    assert abs(window.drone_pulse_gate_knob.value() - 0.25) < 1e-2
    assert window.drone_pulse_span_spin.value() == 2
    assert window.drone_direction_combo.currentText() == "up"
    assert window.drone_semitones_spin.value() == 5
    assert abs(window.drone_slide_knob.value() - 0.5) < 1e-2
    assert abs(window.drone_hold_spin.value() - 1.5) < 1e-6
    assert window.drone_pulse_row.isEnabled()
    assert not window.drone_bend_row.isEnabled()
    assert not window.drone_sequence_row.isEnabled()
    assert "pulse 6 · 1/8" in window.drone_label.text(), window.drone_label.text()
    assert not [c for c in fake.calls if str(c[0]).startswith("set_drone")], (
        fake.calls
    )

    # bend: the bend row is live, the pulse row is inert.
    window._on_status_message(
        "/ji/drone/state",
        [1, "bend", 90, 4, "1/4", 0.5, 1, "down", 2, 0.4, 2.5, 4.0, 1, 1,
         5, "1", 1.0],
    )
    assert window.drone_mode_combo.currentText() == "bend"
    assert not window.drone_pulse_row.isEnabled()
    assert window.drone_bend_row.isEnabled()
    assert not window.drone_sequence_row.isEnabled()
    assert "bend down 2 st / 0.40 s every 4 beats" in window.drone_label.text(), (
        window.drone_label.text()
    )

    # hold: both pad rows are inert (the Stage 6 drone has no pad controls).
    window._on_status_message(
        "/ji/drone/state",
        [1, "hold", 90, 4, "1/4", 0.5, 1, "down", 2, 0.4, 2.5, 4.0, 1, 0,
         5, "1", 1.0],
    )
    assert not window.drone_pulse_row.isEnabled()
    assert not window.drone_bend_row.isEnabled()
    assert not window.drone_sequence_row.isEnabled()
    assert "hold | 90" in window.drone_label.text(), window.drone_label.text()

    # sequence: the third row is live, the pulse/bend rows are inert.
    window._on_status_message(
        "/ji/drone/state",
        [1, "sequence", 85, 4, "1/4", 0.5, 1, "down", 2, 0.4, 2.5, 4.0, 1, 1,
         6, "2", 0.5],
    )
    assert window.drone_mode_combo.currentText() == "sequence"
    assert not window.drone_pulse_row.isEnabled()
    assert not window.drone_bend_row.isEnabled()
    assert window.drone_sequence_row.isEnabled()
    assert window.drone_seq_notes_spin.value() == 6
    assert window.drone_seq_bars_combo.currentText() == "2"
    assert abs(window.drone_seq_gate_knob.value() - 0.5) < 1e-2
    assert "sequence 6 notes · 2 bars | 85" in window.drone_label.text(), (
        window.drone_label.text()
    )
    assert "playing" in window.drone_label.text()

    # Gated mode + stopped transport: the line says why it is silent.
    window._on_status_message("/ji/rhythm/state", [120.0, 4, 4, 0, 1, 1.0])
    window._on_status_message(
        "/ji/drone/state",
        [1, "sequence", 85, 4, "1/4", 0.5, 1, "down", 2, 0.4, 2.5, 4.0, 1, 0,
         6, "2", 0.5],
    )
    assert "waiting for transport (press Play)" in window.drone_label.text(), (
        window.drone_label.text()
    )
    # hold is never gated, so it never shows the hint.
    window._on_status_message(
        "/ji/drone/state",
        [1, "hold", 90, 4, "1/4", 0.5, 1, "down", 2, 0.4, 2.5, 4.0, 1, 0,
         5, "1", 1.0],
    )
    assert "waiting for transport" not in window.drone_label.text()

    # Combo/spins send immediately, knobs coalesce then flush.
    fake.calls.clear()
    window.drone_mode_combo.setCurrentText("pulse")
    assert ("set_drone_setting", "mode", "pulse") in fake.calls, fake.calls
    window.drone_pulse_notes_spin.setValue(5)
    assert ("set_drone_setting", "pulse_notes", 5) in fake.calls
    window.drone_pulse_grid_combo.setCurrentText("1/16")
    assert ("set_drone_setting", "pulse_grid", "1/16") in fake.calls
    window.drone_pulse_span_spin.setValue(2)
    assert ("set_drone_setting", "pulse_octave_span", 2) in fake.calls
    window.drone_direction_combo.setCurrentText("up")
    assert ("set_drone_setting", "slide_direction", "up") in fake.calls
    window.drone_semitones_spin.setValue(7)
    assert ("set_drone_setting", "slide_semitones", 7) in fake.calls
    window.drone_hold_spin.setValue(4.0)
    assert ("set_drone_setting", "hold_beats", 4.0) in fake.calls
    window.drone_seq_notes_spin.setValue(4)
    assert ("set_drone_setting", "seq_notes", 4) in fake.calls
    window.drone_seq_bars_combo.setCurrentText("2")
    assert ("set_drone_setting", "seq_bars", "2") in fake.calls
    window.drone_seq_gate_knob.set_value(1.0, emit=True)
    window.drone_pulse_gate_knob.set_value(1.0, emit=True)
    window.drone_slide_knob.set_value(2.0, emit=True)
    window.drone_velocity_knob.set_value(60, emit=True)
    assert not [c for c in fake.calls if c[0] == "set_drone_param"], fake.calls
    window._flush_pending()

    def _drone_param(name):
        calls = [
            call for call in fake.calls
            if call[0] == "set_drone_param" and call[1] == name
        ]
        assert calls, fake.calls
        return calls[-1][2]

    assert abs(_drone_param("pulse_gate") - 1.0) < 1e-2, fake.calls
    assert abs(_drone_param("seq_gate") - 1.0) < 1e-2, fake.calls
    assert abs(_drone_param("slide_seconds") - 2.0) < 1e-2, fake.calls
    assert _drone_param("velocity") == 60, fake.calls

    # Bus 1 unavailable: everything disabled, explicit label, no sends.
    fake.calls.clear()
    window._on_status_message(
        "/ji/drone/state",
        [0, "hold", 90, 4, "1/4", 0.5, 1, "down", 2, 0.4, 2.5, 4.0, 1, 0,
         5, "1", 1.0],
    )
    assert not window.drone_mode_combo.isEnabled()
    assert "disabled" in window.drone_label.text()

    # The window stays compact with the two extra groups.
    # Stage 9.0 adds the Harm row (expect ~1280x1198); the Qt bound is
    # 1230.  The scroll area remains the small-screen fallback.
    assert window.width() <= 1400 and window.height() <= 1290, (
        window.width(),
        window.height(),
    )

    assert len(window.loop_state_labels) == 4
    assert len(window.autotune_knobs) == 2 * len(AUTOTUNE_PARAMS)
    assert len(window.lane_checkboxes) == 16
    assert len(window.loop_mode_combos) == 4
    assert len(window.loop_length_spins) == 4
    assert fake.callback is not None
    # Bridge status messages arrived above, so the bridge reads connected.
    # The Harm test above deliberately sent one loop-state message; reset the
    # sclang-seen flag for the original "waiting" assertion.
    window.store.sclang_seen = False
    window._refresh_status_labels()
    assert "connected" in window.bridge_status_label.text()
    assert "waiting for sclang" in window.sc_status_label.text()

    # Mutation knob is coalesced, then flushed by the 30 Hz timer path.
    window.mutation_knobs["randomness"].set_value(0.8, emit=True)
    window._flush_pending()
    assert fake.calls[-1][0] == "set_mutation"
    assert fake.calls[-1][1] == "randomness"
    assert abs(fake.calls[-1][2] - 0.8) < 1e-6

    fake.calls.clear()
    window.mutate_button.click()
    window.octave_button.click()
    assert fake.calls == [("trigger_mutation",), ("trigger_octave",)]

    # Link mirrors an autotune knob to both channels.
    fake.calls.clear()
    window.link_checkbox.setChecked(True)
    window.autotune_knobs[(0, "mix")].set_value(0.4, emit=True)
    window._flush_pending()
    linked = [call for call in fake.calls if call[0] == "set_autotune"]
    assert len(linked) == 2, linked
    assert {call[1] for call in linked} == {0, 1}
    assert all(call[2] == "mix" and abs(call[3] - 0.4) < 1e-6 for call in linked)

    # Record always sends the length displayed by the knob (0.1 s precision).
    fake.calls.clear()
    window.length_spin.setValue(12.0)
    displayed = round(window.length_spin.value(), 1)
    assert abs(displayed - 12.0) < 1e-9, (
        f"length spin shows {window.length_spin.value()!r}, expected 12.0"
    )
    window.loop_buttons[(2, "record")].click()
    record_calls = [call for call in fake.calls if call[0] == "loop_record"]
    assert len(record_calls) == 1, f"record calls: {fake.calls!r}"
    assert record_calls[0][1] == 2
    assert abs(record_calls[0][2] - displayed) < 1e-9, (
        f"record seconds {record_calls[0][2]!r}, displayed {displayed!r}"
    )
    window._flush_pending()
    length_calls = [
        call for call in fake.calls if call[0] == "loop_set_default_length"
    ]
    assert len(length_calls) == 1, f"length calls: {fake.calls!r}"
    assert abs(length_calls[0][1] - displayed) < 1e-9, (
        f"default length {length_calls[0][1]!r}, displayed {displayed!r}"
    )

    # Every loop action must carry its own 1-based index.  QPushButton.clicked
    # emits a bool, so a lambda like ``lambda i=index`` would receive False.
    fake.calls.clear()
    for index in range(1, 5):
        for name in ("stop_record", "play", "stop", "clear"):
            window.loop_buttons[(index, name)].click()
    for name in ("loop_stop_record", "loop_play", "loop_stop", "loop_clear"):
        indices = [call[1] for call in fake.calls if call[0] == name]
        assert indices == [1, 2, 3, 4], f"{name} indices: {indices!r}"
    window.stop_all_button.click()
    assert ("loop_stop_all",) in fake.calls

    # Status labels update from parsed messages.
    window._on_status_message(
        "/ji/ui/state", [0.55, 0.0, 0.25, 0.15, 2, 0.1, 0.3, 5]
    )
    assert window.mutation_knobs["change_count"].value() == 2.0
    assert "changes 2/5" in window.control_label.text()

    window._on_status_message(
        "/ji/freqset/changed", [220.0, 1.0, 1.0, 1.125, 1.5]
    )
    assert "1.125000" in window.freqset_label.text()
    assert "247.50" in window.freqset_label.text()

    window._on_status_message(
        "/ji/autotune/state", [1, 0.1, 0.8, 0.2, 7.0, 0.7, 0.5]
    )
    assert abs(window.autotune_knobs[(1, "mix")].value() - 0.8) < 1e-2

    window._on_status_message("/ji/cpu", [12.5])
    assert "12.5%" in window.cpu_label.text()

    window._on_status_message(
        "/ji/midi/status", [1, "IAC Driver Bus 1", "IAC Driver Bus 2"]
    )
    assert "IAC Driver Bus 1" in window.midi_label.text()
    assert window.midi_drone_checkbox.isEnabled()
    assert window.midi_arp_checkbox.isEnabled()

    # Per-bus toggles: state sync must not emit feedback OSC.
    window._on_status_message("/ji/midi/state", [1, 0])
    assert window.midi_drone_checkbox.isChecked()
    assert not window.midi_arp_checkbox.isChecked()
    assert "(on)" in window.midi_label.text()
    assert "(off)" in window.midi_label.text()

    fake.calls.clear()
    window.midi_arp_checkbox.setChecked(True)
    assert ("set_midi_enabled", True, True) in fake.calls, fake.calls

    fake.calls.clear()
    window._on_status_message("/ji/midi/state", [0, 1])
    assert not window.midi_drone_checkbox.isChecked()
    assert window.midi_arp_checkbox.isChecked()
    assert not [c for c in fake.calls if c[0] == "set_midi_enabled"], fake.calls

    window._on_status_message("/ji/midi/status", [0, "", ""])
    assert not window.midi_drone_checkbox.isEnabled()
    assert not window.midi_arp_checkbox.isEnabled()

    window._on_status_message("/ji/loop/state", [2, "recording"])
    assert "recording" in window.loop_state_labels[2].text()

    # --- Stage 8.1 Rhythm group: sends and status sync -------------------
    fake.calls.clear()
    window.bpm_spin.setValue(140)
    window._flush_pending()
    assert ("set_tempo", 140.0) in fake.calls, fake.calls

    fake.calls.clear()
    window.meter_num_spin.setValue(3)
    assert ("set_meter", 3, 4) in fake.calls, fake.calls
    window.meter_den_combo.setCurrentText("8")
    assert ("set_meter", 3, 8) in fake.calls, fake.calls

    fake.calls.clear()
    window.transport_button.click()
    assert ("set_transport", True) in fake.calls, fake.calls
    assert window.transport_button.text() == "Stop"
    window.transport_button.click()
    assert ("set_transport", False) in fake.calls, fake.calls
    assert window.transport_button.text() == "Play"

    fake.calls.clear()
    window.lane_checkboxes[36].setChecked(False)
    assert ("set_drum_lane", 36, False) in fake.calls, fake.calls
    window.lane_checkboxes[36].setChecked(True)
    window.drum_rate_combo.setCurrentIndex(2)
    assert ("set_drum_rate", 2.0) in fake.calls, fake.calls
    window.resync_button.click()
    assert ("request_rhythm_resync",) in fake.calls, fake.calls

    # Per-loop mode switch and length control (beats in beat mode).
    fake.calls.clear()
    window.loop_mode_combos[1].setCurrentIndex(1)
    assert ("loop_set_mode", 1, "beat") in fake.calls, fake.calls
    assert window.loop_length_spins[1].suffix() == " b"
    window.loop_length_spins[1].setValue(7)
    window._flush_pending()
    assert ("loop_set_beats", 1, 7.0) in fake.calls, fake.calls

    # Incoming rhythm/drums state syncs the widgets without feedback OSC.
    fake.calls.clear()
    window._on_status_message(
        "/ji/rhythm/state", [132.0, 5, 4, 0, 1, 0.5]
    )
    assert abs(window.bpm_spin.value() - 132.0) < 1e-6
    assert window.meter_num_spin.value() == 5
    assert window.meter_den_combo.currentText() == "4"
    assert not window.transport_button.isChecked()
    assert "132.0 BPM" in window.rhythm_label.text()
    assert "5/4" in window.rhythm_label.text()

    window._on_status_message("/ji/drums/state", [1, 1, 2.0, 0x0045])
    assert window.drum_enable_checkbox.isEnabled()
    assert window.drum_enable_checkbox.isChecked()
    assert window.drum_rate_combo.currentIndex() == 2
    assert window.lane_checkboxes[36].isChecked()
    assert not window.lane_checkboxes[37].isChecked()
    assert "Drums: on" in window.drum_label.text()
    assert not [call for call in fake.calls if call[0].startswith("set_")], fake.calls

    # Beat-mode Play is rejected locally while the transport is stopped.
    window._on_status_message("/ji/loop/config", [1, "beat", 4, 2.0])
    assert window.loop_mode_combos[1].currentIndex() == 1
    assert window.loop_length_spins[1].suffix() == " b"
    assert window.loop_length_spins[1].value() == 4.0
    fake.calls.clear()
    window.loop_buttons[(1, "play")].click()
    assert not [call for call in fake.calls if call[0] == "loop_play"], fake.calls
    assert "beat-locked" in window.message_label.text()

    # --- Stage 8.6: Dub buttons, Loop edit row, Grain row ---------------
    assert len(window.loop_buttons) == 4 * 6, window.loop_buttons.keys()
    fake.calls.clear()
    for index in range(1, 5):
        window.loop_buttons[(index, "dub")].click()
    assert [c[1] for c in fake.calls if c[0] == "loop_dub"] == [1, 2, 3, 4]
    assert "over" in window.loop_buttons[(1, "dub")].toolTip().lower()

    # The edit row follows the selected loop and sends only for it.
    window.loop_edit_selector.setCurrentIndex(1)  # loop 2
    assert window.loop_edit_label.text().startswith("Loop 2")
    fake.calls.clear()
    window.loop_reverse_checkbox.setChecked(True)
    assert ("set_loop_edit", 2, "reverse", 1) in fake.calls, fake.calls
    window.loop_rate_combo.setCurrentText("3/2")
    assert ("set_loop_edit", 2, "rate", 1.5) in fake.calls, fake.calls
    assert "×3/2" in window.loop_edit_label.text()
    window.loop_gain_spin.setValue(0.55)
    gain_calls = [
        c for c in fake.calls if c[0] == "set_loop_edit" and c[2] == "gain"
    ]
    assert gain_calls and abs(gain_calls[-1][3] - 0.55) < 1e-9, fake.calls
    window.loop_count_in_combo.setCurrentText("1 bar")
    assert ("set_loop_edit", 2, "count_in", "1 bar") in fake.calls, fake.calls
    window.loop_auto_checkbox.setChecked(True)
    assert ("set_loop_edit", 2, "auto_play", 1) in fake.calls, fake.calls
    status = window.loop_edit_label.text()
    assert "rev" in status and "auto" in status, status
    assert "count-in 1 bar" in status, status
    assert "tune" not in status.lower() and "bypass" not in status.lower()

    # Grain off disables the grain controls; on sends every parameter.
    assert not window.grain_rate_spin.isEnabled()
    assert not window.grain_div_combo.isEnabled()
    # Stage 8.6.1: the whole Grain motion row follows the same rule.
    for widget in (
        window.grain_motion_combo,
        window.grain_speed_spin,
        window.grain_depth_spin,
        window.grain_size_jitter_spin,
        window.grain_pitch_jitter_spin,
        window.grain_snap_checkbox,
        window.grain_smear_spin,
        window.grain_freeze_checkbox,
        window.grain_morph_spin,
        window.grain_melt_checkbox,
    ):
        assert not widget.isEnabled()
    # the moving filter is a post-autotune texture: usable without grains
    assert window.grain_filter_spin.isEnabled()
    assert window.grain_filter_rate_spin.isEnabled()
    assert window.grain_motion_combo.currentText() == "forward"
    assert abs(window.grain_speed_spin.value() - 0.03) < 1e-9
    assert abs(window.grain_depth_spin.value() - 60.0) < 1e-9
    assert abs(window.grain_size_jitter_spin.value() - 20.0) < 1e-9
    assert abs(window.grain_pitch_jitter_spin.value() - 0.0) < 1e-9
    assert window.grain_snap_checkbox.isChecked()
    assert abs(window.grain_smear_spin.value() - 0.0) < 1e-9
    assert not window.grain_freeze_checkbox.isChecked()
    fake.calls.clear()
    window.grain_checkbox.setChecked(True)
    assert ("set_loop_edit", 2, "mode", 1) in fake.calls, fake.calls
    assert window.grain_rate_spin.isEnabled()
    assert window.grain_div_combo.isEnabled()
    for widget in (
        window.grain_motion_combo,
        window.grain_speed_spin,
        window.grain_depth_spin,
        window.grain_size_jitter_spin,
        window.grain_pitch_jitter_spin,
        window.grain_snap_checkbox,
        window.grain_smear_spin,
        window.grain_freeze_checkbox,
        window.grain_morph_spin,
        window.grain_melt_checkbox,
    ):
        assert widget.isEnabled()
    window.grain_sync_combo.setCurrentIndex(1)
    assert ("set_loop_edit", 2, "sync", 1) in fake.calls, fake.calls
    assert "transport" in window.grain_hint.text(), window.grain_hint.text()
    window.grain_div_combo.setCurrentText("1/8")
    assert ("set_loop_edit", 2, "division", "1/8") in fake.calls, fake.calls
    window.grain_rate_spin.setValue(12.0)
    assert ("set_loop_edit", 2, "grain_rate", 12.0) in fake.calls, fake.calls
    # 120 ms / 50 % are the new defaults, so move off them to emit a change.
    window.grain_size_spin.setValue(130)
    assert ("set_loop_edit", 2, "grain_size", 130.0) in fake.calls, fake.calls
    window.grain_pos_spin.setValue(60)
    pos_calls = [
        c for c in fake.calls if c[0] == "set_loop_edit" and c[2] == "grain_pos"
    ]
    assert pos_calls and abs(pos_calls[-1][3] - 0.6) < 1e-9, fake.calls
    window.grain_scatter_spin.setValue(25)
    scatter_calls = [
        c
        for c in fake.calls
        if c[0] == "set_loop_edit" and c[2] == "grain_scatter"
    ]
    assert scatter_calls and abs(scatter_calls[-1][3] - 0.25) < 1e-9, fake.calls
    window.grain_pitch_spin.setValue(2.0)
    assert ("set_loop_edit", 2, "grain_pitch", 2.0) in fake.calls, fake.calls
    window.grain_reverse_spin.setValue(40)
    reverse_calls = [
        c
        for c in fake.calls
        if c[0] == "set_loop_edit" and c[2] == "grain_reverse"
    ]
    assert reverse_calls and abs(reverse_calls[-1][3] - 0.4) < 1e-9, fake.calls

    # --- Stage 8.6.1: the Grain motion row sends for the selected loop ---
    fake.calls.clear()
    window.grain_motion_combo.setCurrentText("pingpong")
    assert ("set_loop_edit", 2, "grain_motion", "pingpong") in fake.calls, fake.calls
    window.grain_speed_spin.setValue(0.05)
    speed_calls = [
        c for c in fake.calls if c[0] == "set_loop_edit" and c[2] == "grain_speed"
    ]
    assert speed_calls and abs(speed_calls[-1][3] - 0.05) < 1e-9, fake.calls
    window.grain_depth_spin.setValue(40)
    depth_calls = [
        c for c in fake.calls if c[0] == "set_loop_edit" and c[2] == "grain_depth"
    ]
    assert depth_calls and abs(depth_calls[-1][3] - 0.4) < 1e-9, fake.calls
    window.grain_size_jitter_spin.setValue(35)
    jitter_calls = [
        c
        for c in fake.calls
        if c[0] == "set_loop_edit" and c[2] == "grain_size_jitter"
    ]
    assert jitter_calls and abs(jitter_calls[-1][3] - 0.35) < 1e-9, fake.calls
    window.grain_pitch_jitter_spin.setValue(15)
    pitch_jitter_calls = [
        c
        for c in fake.calls
        if c[0] == "set_loop_edit" and c[2] == "grain_pitch_jitter"
    ]
    assert (
        pitch_jitter_calls and abs(pitch_jitter_calls[-1][3] - 0.15) < 1e-9
    ), fake.calls
    window.grain_smear_spin.setValue(30)
    smear_calls = [
        c for c in fake.calls if c[0] == "set_loop_edit" and c[2] == "grain_smear"
    ]
    assert smear_calls and abs(smear_calls[-1][3] - 0.3) < 1e-9, fake.calls
    window.grain_freeze_checkbox.setChecked(True)
    assert ("set_loop_edit", 2, "grain_freeze", 1) in fake.calls, fake.calls
    window.grain_sync_combo.setCurrentIndex(0)  # free: no transport hint
    assert "frozen" in window.grain_hint.text(), window.grain_hint.text()
    window.grain_sync_combo.setCurrentIndex(1)
    window.grain_freeze_checkbox.setChecked(False)
    assert ("set_loop_edit", 2, "grain_freeze", 0) in fake.calls, fake.calls
    window.grain_snap_checkbox.setChecked(False)
    assert ("set_loop_edit", 2, "grain_pitch_snap", 0) in fake.calls, fake.calls
    assert "pitch ×" in window.loop_edit_label.text()
    assert "snap" not in window.loop_edit_label.text()

    # --- Stage 8.6.2: the Morph row (dry <-> grains, melt, filter) --------
    fake.calls.clear()
    window.grain_morph_spin.setValue(35)
    morph_calls = [
        c for c in fake.calls if c[0] == "set_loop_edit" and c[2] == "grain_morph"
    ]
    assert morph_calls and abs(morph_calls[-1][3] - 0.35) < 1e-9, fake.calls
    window.grain_melt_checkbox.setChecked(True)
    assert ("set_loop_edit", 2, "grain_melt", 1) in fake.calls, fake.calls
    window.grain_melt_checkbox.setChecked(False)
    assert ("set_loop_edit", 2, "grain_melt", 0) in fake.calls, fake.calls
    window.grain_filter_spin.setValue(45)
    filter_calls = [
        c for c in fake.calls if c[0] == "set_loop_edit" and c[2] == "grain_filter"
    ]
    assert filter_calls and abs(filter_calls[-1][3] - 0.45) < 1e-9, fake.calls
    window.grain_filter_rate_spin.setValue(0.3)
    rate_calls = [
        c
        for c in fake.calls
        if c[0] == "set_loop_edit" and c[2] == "grain_filter_rate"
    ]
    assert rate_calls and abs(rate_calls[-1][3] - 0.3) < 1e-9, fake.calls
    # Stage 8.7: the loop's own grain breath sends two more edit params.
    window.grain_breath_spin.setValue(40)
    breath_calls = [
        c for c in fake.calls if c[0] == "set_loop_edit" and c[2] == "grain_breath"
    ]
    assert breath_calls and abs(breath_calls[-1][3] - 0.4) < 1e-9, fake.calls
    window.grain_breath_rate_spin.setValue(0.2)
    breath_rate_calls = [
        c
        for c in fake.calls
        if c[0] == "set_loop_edit" and c[2] == "grain_breath_rate"
    ]
    assert (
        breath_rate_calls and abs(breath_rate_calls[-1][3] - 0.2) < 1e-9
    ), fake.calls
    window.grain_morph_spin.setValue(0)

    # Incoming /ji/loop/edit mirrors the selected loop, no feedback OSC.
    fake.calls.clear()
    window._on_status_message(
        "/ji/loop/edit",
        [2, 1, 0.6666667, 0.4, 1, 1, "1/4", 6.5, 150.0, 0.5, 0.25, 1.25,
         0.4, 0.2, 1, 0.3, 0, "backward", 0.04, 0.7, 0.5, 0.5,
         "2 bars", 1, 1.125, 0.6, 0, 0.3, 0.2, 0.4, 0.09,
         "off", 0.35, 0.5, "free", 4, 0.08, 0.5, 1],
    )
    assert window.loop_reverse_checkbox.isChecked()
    assert window.loop_rate_combo.currentText() == "2/3"
    assert abs(window.loop_gain_spin.value() - 0.4) < 1e-9
    assert window.loop_count_in_combo.currentText() == "2 bars"
    assert window.loop_auto_checkbox.isChecked()
    assert window.grain_checkbox.isChecked()
    assert window.grain_sync_combo.currentIndex() == 1
    assert window.grain_div_combo.currentText() == "1/4"
    assert abs(window.grain_rate_spin.value() - 6.5) < 1e-9
    assert abs(window.grain_size_spin.value() - 150.0) < 1e-9
    assert abs(window.grain_pos_spin.value() - 50.0) < 1e-9
    assert abs(window.grain_scatter_spin.value() - 25.0) < 1e-9
    assert abs(window.grain_pitch_spin.value() - 1.25) < 1e-9
    assert abs(window.grain_reverse_spin.value() - 50.0) < 1e-9
    assert window.grain_motion_combo.currentText() == "backward"
    assert abs(window.grain_speed_spin.value() - 0.04) < 1e-9
    assert abs(window.grain_depth_spin.value() - 70.0) < 1e-9
    assert abs(window.grain_size_jitter_spin.value() - 40.0) < 1e-9
    assert abs(window.grain_pitch_jitter_spin.value() - 20.0) < 1e-9
    assert window.grain_snap_checkbox.isChecked()
    assert abs(window.grain_smear_spin.value() - 30.0) < 1e-9
    assert not window.grain_freeze_checkbox.isChecked()
    assert abs(window.grain_morph_spin.value() - 60.0) < 1e-9
    assert not window.grain_melt_checkbox.isChecked()
    assert abs(window.grain_filter_spin.value() - 30.0) < 1e-9
    assert abs(window.grain_filter_rate_spin.value() - 0.2) < 1e-9
    assert abs(window.grain_breath_spin.value() - 40.0) < 1e-9
    assert abs(window.grain_breath_rate_spin.value() - 0.09) < 1e-9
    status = window.loop_edit_label.text()
    assert "×2/3" in status and "motion backward" in status, status
    assert "pitch ×9/8" in status, status
    assert "snap" in status, status
    assert "morph 60 %" in status, status
    assert "breath 40 %" in status, status
    assert not [c for c in fake.calls if c[0] == "set_loop_edit"], fake.calls

    # --- Stage 8.6 §8 regression: the Div combo must not jump back ------
    # Choosing 1/4 sends one edit; an unrelated echo (same division) must
    # leave the combo exactly where the human put it.
    fake.calls.clear()
    window.grain_div_combo.setCurrentText("1/8")
    assert ("set_loop_edit", 2, "division", "1/8") in fake.calls, fake.calls
    window.grain_div_combo.setCurrentText("1/4")
    assert window.grain_div_combo.currentText() == "1/4"
    assert ("set_loop_edit", 2, "division", "1/4") in fake.calls, fake.calls
    fake.calls.clear()
    window.grain_size_spin.setValue(180)
    assert ("set_loop_edit", 2, "grain_size", 180.0) in fake.calls, fake.calls
    fake.calls.clear()
    window._on_status_message(
        "/ji/loop/edit",
        [2, 1, 0.6666667, 0.4, 1, 1, "1/4", 6.5, 180.0, 0.5, 0.25, 1.25,
         0.4, 0.2, 1, 0.3, 0, "backward", 0.04, 0.7, 0.5, 0.5,
         "2 bars", 1, 1.125, 0.6, 0, 0.3, 0.2, 0.4, 0.09,
         "off", 0.35, 0.5, "free", 4, 0.08, 0.5, 1],
    )
    assert window.grain_div_combo.currentText() == "1/4", (
        "unrelated echo reset the Div combo"
    )
    assert not [c for c in fake.calls if c[0] == "set_loop_edit"], fake.calls

    # Another loop's edit must not leak into the selected row.
    window._on_status_message(
        "/ji/loop/edit",
        [1, 0, 2.0, 1.0, 0, 0, "1", 1.0, 10.0, 0.0, 0.0, 4.0, 0.0, 0.0, 0,
         0.0, 0, "off", 0.5, 0.0, 0.0, 0.0, "off", 0, 4.0, 0.0, 0, 0.0, 0.12,
         0.0, 0.08, "off", 0.35, 0.5, "free", 4, 0.08, 0.5, 1],
    )
    assert window.loop_rate_combo.currentText() == "2/3", "other loop leaked"
    assert window.grain_div_combo.currentText() == "1/4", "other loop leaked"
    assert not [c for c in fake.calls if c[0] == "set_loop_edit"], fake.calls

    # Count-in countdown and the new state strings.
    window._on_status_message("/ji/loop/state", [2, "counting"])
    window._on_status_message("/ji/loop/count", [2, 3])
    assert "counting 3" in window.loop_state_labels[2].text(), (
        window.loop_state_labels[2].text()
    )
    window._on_status_message("/ji/loop/count", [2, 0])
    assert "counting" in window.loop_state_labels[2].text()
    window._on_status_message("/ji/loop/state", [2, "dubbing"])
    assert "dubbing" in window.loop_state_labels[2].text()

    # Memory slots: Recall/Clr enable only for a filled, selected slot.
    fake.calls.clear()
    window.loop_mem_combo.setCurrentIndex(2)  # slot C
    assert not window.loop_mem_recall_button.isEnabled()
    assert not window.loop_mem_clear_button.isEnabled()
    window.loop_mem_save_button.click()
    assert ("loop_mem_save", 2, 2) in fake.calls, fake.calls
    window._on_status_message("/ji/loop/mem", [2, 1, 384000, 8.0])
    assert window.loop_mem_recall_button.isEnabled()
    assert window.loop_mem_clear_button.isEnabled()
    window.loop_mem_recall_button.click()
    assert ("loop_mem_recall", 2, 2) in fake.calls, fake.calls
    window.loop_mem_clear_button.click()
    assert ("loop_mem_clear", 2) in fake.calls, fake.calls
    window._on_status_message("/ji/loop/mem", [2, 0, 0, 0.0])
    assert not window.loop_mem_clear_button.isEnabled()

    # --- Stage 8.2 Groove group and lazy step grid ----------------------
    fake.calls.clear()
    window.show_grid_checkbox.setChecked(True)
    app.processEvents()
    expected_steps = grid_steps_for_meter(5, 4)  # meter from the earlier sync
    assert window.grid_scroll.isVisible() is True
    assert window._grid_built_steps == expected_steps
    assert len(window.step_cells) == 16 * expected_steps
    assert all(cell.width() == 14 for cell in list(window.step_cells.values())[:8])

    window.step_cells[(0, 1)].click()
    assert ("set_drum_step", 0, 1, True) in fake.calls, fake.calls

    fake.calls.clear()
    window.groove_preset_combo.setCurrentIndex(1)
    assert ("set_drums_preset", "break") in fake.calls, fake.calls
    window.groove_knobs["density"].set_value(0.5, emit=True)
    window._flush_pending()
    assert ("set_groove_modifier", "density", 0.5) in fake.calls, fake.calls
    window.groove_seed_spin.setValue(7)
    assert ("set_drum_seed", 7) in fake.calls, fake.calls
    window.reset_grid_button.click()
    assert ("reset_drum_grid",) in fake.calls, fake.calls

    # Incoming groove/grid state syncs widgets without feedback OSC.
    fake.calls.clear()
    window._on_status_message(
        "/ji/drums/groove", ["break", 0.5, 0.2, 0.1, 0.8, 7, 16]
    )
    assert window.groove_preset_combo.currentIndex() == 1
    assert abs(window.groove_knobs["density"].value() - 0.5) < 1e-2
    assert abs(window.groove_knobs["probability"].value() - 0.8) < 1e-2
    assert window.groove_seed_spin.value() == 7
    assert not [c for c in fake.calls if c[0].startswith("set_")], fake.calls

    window._on_status_message("/ji/drums/grid", [1, 0] + [0] * 30)
    assert window.step_cells[(0, 0)].isChecked()
    assert not window.step_cells[(0, 1)].isChecked()
    assert not [c for c in fake.calls if c[0] == "set_drum_step"], fake.calls

    # Pending/quantize status line: running shows a countdown and count;
    # stopped shows the immediate mode.
    window._on_status_message("/ji/rhythm/state", [120, 4, 4, 1, 1, 1.0])
    window._on_status_message("/ji/quantize/state", [2, 0.32])
    assert "2 pending" in window.quantize_label.text()
    assert "quantized off" not in window.quantize_label.text()

    fake.calls.clear()
    window.mutate_button.click()
    assert ("trigger_mutation",) in fake.calls
    assert "queued" in window.message_label.text().lower()

    window._on_status_message("/ji/rhythm/state", [120, 4, 4, 0, 1, 1.0])
    window._on_status_message("/ji/quantize/state", [0, -1.0])
    assert "quantized off" in window.quantize_label.text()

    fake.calls.clear()
    window.mutate_button.click()
    assert ("trigger_mutation",) in fake.calls
    assert "immediately" in window.message_label.text().lower()

    # Bass feedback also follows the transport state.
    window._on_status_message("/ji/bass/state", [1, 1, "pulse", "1/4", 4,
        0.0, 0.0, 0.0, 3.5, 0.0, "root", 0.0, 0, 0.0, 1, -2, 0, 1, 2, 3])
    window._on_status_message("/ji/rhythm/state", [120, 4, 4, 1, 1, 1.0])
    fake.calls.clear()
    window.bass_preset_combo.setCurrentText("techno")
    assert ("set_bass_preset", "techno") in fake.calls
    assert "queued" in window.message_label.text().lower()
    window._on_status_message("/ji/rhythm/state", [120, 4, 4, 0, 1, 1.0])
    fake.calls.clear()
    window.bass_preset_combo.setCurrentText("pulse")
    assert ("set_bass_preset", "pulse") in fake.calls
    assert "immediately" in window.message_label.text().lower()

    # Missing drum port: show disabled state, disable the controls, no crash.
    window._on_status_message("/ji/drums/state", [0, 0, 1.0, 0x0045])
    assert not window.drum_enable_checkbox.isEnabled()
    assert not window.drum_rate_combo.isEnabled()
    assert not window.lane_checkboxes[36].isEnabled()
    assert "not available" in window.drum_label.text()

    # --- Stage 8.4 Home group ------------------------------------------
    assert window.home_slot_combo.currentText() == "A"
    assert window.home_every_spin.value() == 8
    assert window.home_drift_spin.value() == 0
    assert window.home_seed_spin.value() == 1
    assert not window.home_auto_checkbox.isChecked()
    assert not window.home_auto_checkbox.isEnabled(), "needs a captured slot"
    assert not window.home_go_button.isEnabled()
    assert not window.home_return_button.isEnabled()
    assert not window.home_back_button.isEnabled()
    assert not window.home_jump_button.isEnabled()
    assert "waiting" in window.home_label.text()

    fake.calls.clear()
    window.home_set_button.click()
    assert ("home_capture", 0) in fake.calls, fake.calls

    # Incoming state syncs every control with signals blocked (no feedback).
    fake.calls.clear()
    window._on_status_message(
        "/ji/home/state", [1, 0, 7, 2, 0, 5, 0, 8, 1, 0, 0, 3, 1]
    )
    assert not [
        call for call in fake.calls
        if call[0] in ("home_capture", "home_go", "home_return", "home_back",
                       "home_jump", "set_home_setting")
    ], fake.calls
    assert "Home: A · 7 cl" in window.home_label.text()
    assert "dist 2" in window.home_label.text()
    assert "back 5" in window.home_label.text()
    assert window.home_go_button.isEnabled()
    assert window.home_return_button.isEnabled()
    assert window.home_back_button.isEnabled()
    assert window.home_jump_button.isEnabled()
    assert window.home_auto_checkbox.isEnabled()

    # The selected slot drives Go/Clear availability.
    window.home_slot_combo.setCurrentIndex(1)  # B is empty in this state
    assert not window.home_go_button.isEnabled()
    assert not window.home_clear_button.isEnabled()
    window.home_slot_combo.setCurrentIndex(0)
    assert window.home_go_button.isEnabled()

    # Buttons send immediately; the Status line reports the queue rule.
    window._on_status_message("/ji/rhythm/state", [120, 4, 4, 1, 1, 1.0])
    fake.calls.clear()
    window.home_go_button.click()
    assert ("home_go", 0) in fake.calls, fake.calls
    assert "queued" in window.message_label.text().lower()
    window._on_status_message("/ji/rhythm/state", [120, 4, 4, 0, 1, 1.0])
    fake.calls.clear()
    window.home_return_button.click()
    assert ("home_return",) in fake.calls, fake.calls
    assert "immediately" in window.message_label.text().lower()
    window.home_back_button.click()
    window.home_jump_button.click()
    window.home_clear_button.click()
    assert ("home_back",) in fake.calls
    assert ("home_jump",) in fake.calls
    assert ("home_clear", 0) in fake.calls

    # Settings: Auto is immediate; the spins coalesce through the 30 Hz flush.
    fake.calls.clear()
    window.home_auto_checkbox.setChecked(True)
    assert ("set_home_setting", "auto", True) in fake.calls, fake.calls
    window.home_every_spin.setValue(4)
    window.home_drift_spin.setValue(3)
    window.home_seed_spin.setValue(9)
    window._flush_pending()
    assert ("set_home_setting", "every", 4) in fake.calls
    assert ("set_home_setting", "max_drift", 3) in fake.calls
    assert ("set_home_setting", "seed", 9) in fake.calls

    # Returning state: the line shows progress; Go/Return/Back disable.
    window._on_status_message(
        "/ji/home/state", [1, 0, 7, 1, 2, 6, 1, 4, 9, 3, 1, 5, 1]
    )
    assert "returning to A" in window.home_label.text()
    assert "1/3 beats" in window.home_label.text(), window.home_label.text()
    assert not window.home_go_button.isEnabled()
    assert not window.home_return_button.isEnabled()
    assert not window.home_back_button.isEnabled()
    assert window.home_jump_button.isEnabled()

    # Back to the idle state; Auto can be turned off again.
    window._on_status_message(
        "/ji/home/state", [1, 0, 7, 1, 0, 6, 1, 4, 9, 3, 0, 0, 1]
    )
    assert "Home: A ·" in window.home_label.text()
    fake.calls.clear()
    window.home_auto_checkbox.setChecked(False)
    assert ("set_home_setting", "auto", False) in fake.calls

    # No home at all: the documented empty line and a disabled Auto.
    window._on_status_message(
        "/ji/home/state", [0, -1, 0, 0, 0, 1, 0, 8, 1, 0, 0, 0, 0]
    )
    assert window.home_label.text() == "Home: none (Set home to capture)"
    assert not window.home_auto_checkbox.isEnabled()

    # --- Stage 8.5 State group ------------------------------------------
    assert window.state_label.text() == "State: waiting for bridge"
    assert not window.state_save_button.isEnabled()
    assert not window.state_load_button.isEnabled()
    assert not window.state_delete_button.isEnabled()

    # Incoming list + status sync the combo/label with signals blocked.
    fake.calls.clear()
    window._on_status_message("/ji/state/list", [2, "A-drone", "b mix"])
    window._on_status_message("/ji/state/state", [2, "A-drone", "", "saved"])
    assert fake.calls == [], fake.calls
    assert window.state_combo.currentText() == "A-drone"
    assert "State: 2 saved" in window.state_label.text()
    assert "saved A-drone" in window.state_label.text()
    assert window.state_save_button.isEnabled()
    assert window.state_load_button.isEnabled()
    assert window.state_delete_button.isEnabled()

    # An unknown name keeps Save enabled but disables Load/Delete.
    window.state_combo.setCurrentText("new-colour")
    assert window.state_save_button.isEnabled()
    assert not window.state_load_button.isEnabled()
    assert not window.state_delete_button.isEnabled()
    # Membership is case-insensitive, like the bridge's lookup.
    window.state_combo.setCurrentText("a-DRONE")
    assert window.state_load_button.isEnabled()

    fake.calls.clear()
    window.state_combo.setCurrentText("b mix")
    window.state_save_button.click()
    assert ("state_save", "b mix") in fake.calls, fake.calls
    window.state_load_button.click()
    assert ("state_load", "b mix") in fake.calls
    window.state_delete_button.click()
    assert ("state_delete", "b mix") in fake.calls
    window.state_refresh_button.click()
    assert ("request_state_list",) in fake.calls

    # Buttons send immediately; the Status line reports the queue rule.
    window._on_status_message("/ji/rhythm/state", [120, 4, 4, 1, 1, 1.0])
    window._on_state_load()
    assert "queued" in window.message_label.text().lower()
    window._on_status_message("/ji/rhythm/state", [120, 4, 4, 0, 1, 1.0])
    window._on_state_load()
    assert "immediately" in window.message_label.text().lower()
    window._on_state_save()
    assert "state save" in window.message_label.text().lower()
    window._on_state_delete()
    assert "state delete" in window.message_label.text().lower()
    window._on_state_refresh()
    assert "refreshed" in window.message_label.text().lower()

    # Errors show on the State line, not as a traceback.
    window._on_status_message(
        "/ji/state/state",
        [2, "A-drone", "", "invalid state: bad anchor"],
    )
    assert window.state_label.text() == (
        "State: error invalid state: bad anchor"
    )
    window._on_status_message("/ji/state/state", [0, "", "", ""])
    assert window.state_label.text() == "State: none saved"

    # A typed name survives a list refresh.
    window.state_combo.setCurrentText("typed name")
    window._on_status_message("/ji/state/list", [2, "A-drone", "b mix"])
    assert window.state_combo.currentText() == "typed name"
    assert window.state_save_button.isEnabled()
    assert not window.state_load_button.isEnabled()

    # Stage 9.0 adds the Harm row (expect ~1280x1198); the Qt bound is
    # 1230.  The scroll area remains the small-screen fallback.
    assert window.width() <= 1400 and window.height() <= 1290, (
        window.width(),
        window.height(),
    )

    window.close()
    app.processEvents()
    assert fake.stopped is True
    window.deleteLater()
    app.processEvents()
    print("  home/state groups, groove/grid sync, pending status, close")
    return 0


# ---------------------------------------------------------------------------
# Runner
# ---------------------------------------------------------------------------


def test_home_builders_and_state() -> None:
    print("\n=== Test 10: Stage 8.4 home builders, parser, store, controller ===")
    assert osc_control.HOME_CAPTURE == "/ji/home/capture"
    assert osc_control.HOME_STATE == "/ji/home/state"
    assert osc_control.HOME_SLOT_NAMES == ("A", "B", "C", "D")

    assert build_home_capture(0) == ("/ji/home/capture", [0])
    assert build_home_clear(3) == ("/ji/home/clear", [3])
    assert build_home_go(2) == ("/ji/home/go", [2])
    assert build_home_return() == ("/ji/home/return", [])
    assert build_home_jump() == ("/ji/home/jump", [])
    assert build_home_back() == ("/ji/home/back", [])
    # Slots clamp into 0..3 instead of raising (a knob can overshoot).
    assert build_home_capture(9) == ("/ji/home/capture", [3])
    assert build_home_capture(-2) == ("/ji/home/capture", [0])

    assert build_set_home_auto(True) == ("/ji/home/set_auto", [1])
    assert build_set_home_every(99) == ("/ji/home/set_every", [32])
    assert build_set_home_every(0) == ("/ji/home/set_every", [1])
    assert build_set_home_seed(2**40) == ("/ji/home/set_seed", [2**31 - 1])
    assert build_set_home_drift(99) == ("/ji/home/set_drift", [12])
    assert build_set_home_glide(0.5) == ("/ji/home/set_glide", [0.3])
    assert build_set_home_glide(0.0) == ("/ji/home/set_glide", [0.02])
    assert build_set_home_setting("drift", 3) == ("/ji/home/set_drift", [3])
    assert build_set_home_setting("glide", 0.1) == (
        "/ji/home/set_glide",
        [0.1],
    )
    try:
        build_set_home_setting("nope", 1)
    except ValueError:
        pass
    else:
        raise AssertionError("unknown home setting was accepted")

    empty = parse_home_state([0, -1, 0, 0, 0, 0, 0, 8, 1, 0, 0, 0, 0])
    assert empty.status_line() == "Home: none (Set home to capture)"
    assert not empty.has_home and empty.active_slot == -1

    state = parse_home_state([1, 0, 7, 2, 0, 12, 0, 8, 1, 3, 0, 4, 0b0101])
    assert state.has_home and state.active_name == "A"
    assert state.slot_captured(0) and state.slot_captured(2)
    assert not state.slot_captured(1)
    assert state.status_line() == (
        "Home: A · 7 cl | dist 2 | auto 8 | drift 3 | back 12"
    )
    orphan = parse_home_state([1, -1, 0, 0, 0, 0, 0, 8, 1, 0, 0, 0, 0b0010])
    assert orphan.status_line() == "Home: no active home (Go or Set home)"
    returning = parse_home_state([1, 1, 7, 2, 2, 12, 0, 8, 1, 3, 1, 4, 0b0011])
    assert returning.status_line(3) == "Home: returning to B · 1/3 beats"
    assert returning.status_line() == "Home: returning to B · 1/3 beats"
    # The 13-value contract is enforced.
    try:
        parse_home_state([1, 0, 7])
    except ValueError:
        pass
    else:
        raise AssertionError("short home state was accepted")

    store = StatusStore()
    assert store.home is None
    assert store.apply("/ji/home/state", [1, 2, 5, 1, 0, 3, 1, 4, 7, 2, 0, 1, 4])
    assert store.home is not None and store.bridge_seen
    assert store.home.active_name == "C"
    assert store.home.auto and store.home.every == 4

    bridge_client = FakeClient("127.0.0.1", 9000)
    sc_client = FakeClient("127.0.0.1", 57120)
    listener = FakeListener("127.0.0.1", 9001, lambda address, args: None)
    controller = OSCController(
        bridge_client=bridge_client,
        sc_client=sc_client,
        listener=listener,
    )
    controller.start()
    bridge_client.sent.clear()
    controller.home_capture(0)
    controller.home_go(1)
    controller.home_clear(2)
    controller.home_return()
    controller.home_jump()
    controller.home_back()
    controller.set_home_setting("auto", True)
    controller.set_home_setting("every", 4)
    controller.set_home_setting("seed", 9)
    controller.set_home_setting("drift", 2)
    controller.set_home_setting("glide", 0.1)
    assert bridge_client.addresses() == [
        "/ji/home/capture",
        "/ji/home/go",
        "/ji/home/clear",
        "/ji/home/return",
        "/ji/home/jump",
        "/ji/home/back",
        "/ji/home/set_auto",
        "/ji/home/set_every",
        "/ji/home/set_seed",
        "/ji/home/set_drift",
        "/ji/home/set_glide",
    ]
    assert bridge_client.sent[0][1] == [0]
    assert bridge_client.sent[1][1] == [1]
    assert bridge_client.sent[2][1] == [2]
    assert bridge_client.sent[3][1] == []
    assert bridge_client.sent[6][1] == [1]
    assert bridge_client.sent[10][1] == [0.1]
    controller.stop()

    # The bridge publishes one /ji/home/state on capture (never per note).
    original = bridge_module.SimpleUDPClient
    FakeClient.instances = []
    bridge_module.SimpleUDPClient = FakeClient
    state_directory = tempfile.mkdtemp(prefix="ji-ui-home-")
    try:
        bridge = bridge_module.JIBridge(seed=3, state_dir=state_directory)
        client, ui_client = FakeClient.instances
        bridge.request_home_capture(0)
        home_args = [
            args for address, args in ui_client.sent if address == "/ji/home/state"
        ]
        assert home_args, "capture must publish /ji/home/state"
        assert len(home_args[-1]) == 13
        parsed = osc_control.parse_home_state(home_args[-1])
        assert parsed.has_home and parsed.active_slot == 0
        assert parsed.slot_mask == 1
        # Capture is not a set change: still exactly the initial update.
        assert client.addresses() == []
        bridge.stop()
    finally:
        bridge_module.SimpleUDPClient = original
        shutil.rmtree(state_directory, ignore_errors=True)
    print("  builders/clamps, 13-value parser, store, controller, bridge state")


def test_state_builders_and_store() -> None:
    print("\n=== Test 11: Stage 8.5 state builders, parsers, store, controller ===")
    assert osc_control.STATE_SAVE == "/ji/state/save"
    assert osc_control.STATE_LOAD == "/ji/state/load"
    assert osc_control.STATE_DELETE == "/ji/state/delete"
    assert osc_control.STATE_REQUEST == "/ji/state/request"
    assert osc_control.STATE_LIST == "/ji/state/list"
    assert osc_control.STATE_STATE == "/ji/state/state"
    assert osc_control.STATE_LIST in osc_control.STATUS_ADDRESSES
    assert osc_control.STATE_STATE in osc_control.STATUS_ADDRESSES

    assert build_state_save("  A-drone ") == ("/ji/state/save", ["A-drone"])
    assert build_state_load("b mix") == ("/ji/state/load", ["b mix"])
    assert build_state_delete("A") == ("/ji/state/delete", ["A"])
    assert build_state_request() == ("/ji/state/request", [])
    for bad in ("", "   ", None):
        try:
            build_state_save(bad)
        except ValueError:
            pass
        else:
            raise AssertionError(f"empty state name accepted: {bad!r}")
    # Only emptiness is the UI's business; the bridge owns the name rules.
    assert build_state_save("..") == ("/ji/state/save", [".."])

    assert parse_state_list([0]) == ()
    assert parse_state_list([2, "A-drone", "b mix"]) == ("A-drone", "b mix")
    for bad in ([], [2, "only-one"], [-1]):
        try:
            parse_state_list(bad)
        except ValueError:
            pass
        else:
            raise AssertionError(f"bad state list accepted: {bad}")

    empty = parse_state_status([0, "", "", ""])
    assert empty.status_line() == "State: none saved"
    saved = parse_state_status([3, "A-drone", "b mix", "loaded"])
    assert saved.status_line() == (
        "State: 3 saved | saved A-drone | loaded b mix"
    )
    assert parse_state_status([1, "A", "", "saved"]).status_line() == (
        "State: 1 saved | saved A"
    )
    error = parse_state_status([2, "A", "", "invalid state: bad anchor"])
    assert error.is_error and error.status_line() == (
        "State: error invalid state: bad anchor"
    )
    assert parse_state_status([0, "", "", "no such state"]).status_line() == (
        "State: error no such state"
    )
    try:
        parse_state_status([1, "A"])
    except ValueError:
        pass
    else:
        raise AssertionError("short state status accepted")

    store = StatusStore()
    assert store.state is None and store.state_names == ()
    assert store.apply(osc_control.STATE_LIST, [2, "A-drone", "b mix"])
    assert store.state_names == ("A-drone", "b mix")
    assert store.apply(osc_control.STATE_STATE, [2, "A-drone", "", "saved"])
    assert store.state is not None and store.state.count == 2
    assert store.state.last_saved == "A-drone" and store.bridge_seen
    assert not store.apply("/ji/state/unknown", [])

    bridge_client = FakeClient("127.0.0.1", 9000)
    sc_client = FakeClient("127.0.0.1", 57120)
    listener = FakeListener("127.0.0.1", 9001, lambda address, args: None)
    controller = OSCController(
        bridge_client=bridge_client,
        sc_client=sc_client,
        listener=listener,
    )
    controller.start()
    bridge_client.sent.clear()
    controller.state_save("A-drone")
    controller.state_load("A-drone")
    controller.state_delete("b mix")
    controller.request_state_list()
    assert bridge_client.sent == [
        ("/ji/state/save", ["A-drone"]),
        ("/ji/state/load", ["A-drone"]),
        ("/ji/state/delete", ["b mix"]),
        ("/ji/state/request", []),
    ]
    controller.stop()

    # The bridge replies to save/load with both messages, sorted names.
    original = bridge_module.SimpleUDPClient
    FakeClient.instances = []
    bridge_module.SimpleUDPClient = FakeClient
    directory = tempfile.mkdtemp(prefix="ji-ui-state-")
    try:
        bridge = bridge_module.JIBridge(seed=3, state_dir=directory)
        _client, ui_client = FakeClient.instances
        bridge.request_state_save("B-two")
        bridge.request_state_save("A-one")
        assert [
            args for address, args in ui_client.sent
            if address == "/ji/state/list"
        ][-1] == [2, "A-one", "B-two"]
        status = [
            args for address, args in ui_client.sent
            if address == "/ji/state/state"
        ][-1]
        assert status == [2, "A-one", "", "saved"]
        bridge.request_state_load("A-one")
        status = [
            args for address, args in ui_client.sent
            if address == "/ji/state/state"
        ][-1]
        assert status == [2, "A-one", "A-one", "loaded"]
        bridge.stop()
    finally:
        bridge_module.SimpleUDPClient = original
        shutil.rmtree(directory, ignore_errors=True)
    print("  builders, strict parsers, store, controller, sorted bridge replies")


def test_lead_input_builders_and_parsers() -> None:
    print("\n=== Test 5e: Stage 9.1 lead input builders/parsers/status ===")
    assert osc_control.build_set_lead_setting("gate_mode", "note") == (
        "/ji/lead/set_gate_mode",
        ["note"],
    )
    assert osc_control.build_set_lead_setting("gate_mode", "beat")[1] == ["beat"]
    try:
        osc_control.build_set_lead_setting("gate_mode", "off")
    except ValueError:
        pass
    else:
        raise AssertionError("a bad gate_mode was accepted")
    assert osc_control.build_set_lead_input_enabled(1) == (
        "/ji/lead/input/set_enabled",
        [1],
    )
    assert osc_control.build_set_lead_input_quantize(0.4) == (
        "/ji/lead/input/set_quantize",
        [0.4],
    )
    assert osc_control.build_set_lead_input_quantize(9.0)[1] == [1.0]
    assert osc_control.build_set_lead_input_count_in("2 bars")[1] == ["2 bars"]
    assert osc_control.build_set_lead_input_bars(99)[1] == [8]
    assert osc_control.build_lead_input_note_on(60, 100) == (
        "/ji/lead/input/note_on",
        [60, 100],
    )
    assert osc_control.build_lead_input_note_on(200, 999)[1] == [127, 127]
    assert osc_control.build_lead_input_note_off(-4)[1] == [0]
    for builder, address in (
        (osc_control.build_lead_input_record, "/ji/lead/input/record"),
        (osc_control.build_lead_input_stop, "/ji/lead/input/stop"),
        (osc_control.build_lead_input_revert, "/ji/lead/input/revert"),
        (osc_control.build_lead_input_clear, "/ji/lead/input/clear"),
        (osc_control.build_lead_input_refresh, "/ji/lead/input/refresh"),
        (osc_control.build_lead_melody_request, "/ji/lead/melody/request"),
    ):
        assert builder() == (address, [])
    assert osc_control.build_lead_melody_save(" intro line ") == (
        "/ji/lead/melody/save",
        ["intro line"],
    )
    for builder in (
        osc_control.build_lead_melody_save,
        osc_control.build_lead_melody_load,
        osc_control.build_lead_melody_delete,
    ):
        try:
            builder("  ")
        except ValueError:
            pass
        else:
            raise AssertionError("an empty melody name was accepted")

    # /ji/lead/state accepts 16 or 17 values; gate_mode defaults to beat.
    legacy = osc_control.parse_lead_state(
        [0, 0, "1/8", 2, 0.75, 0.5, 90, 1, 0, "identity", 0, 0, 0.0, 1, 1, 0]
    )
    assert legacy.gate_mode == "beat" and legacy.note_gate is False
    current = osc_control.parse_lead_state(
        [1, 1, "1/16", 4, 0.4, 0.25, 80, 2, -1, "invert", 3, 2, 0.3, 0, 9, 1,
         "note"]
    )
    assert current.gate_mode == "note" and current.note_gate is True
    for bad in ([0] * 15, [0] * 18):
        try:
            osc_control.parse_lead_state(bad)
        except ValueError:
            pass
        else:
            raise AssertionError(f"accepted a {len(bad)}-value lead state")

    status = osc_control.parse_lead_input_state(
        [1, 1, "Launchkey 49", 0.8, "1 bar", 2, 1, 3.0, 13, "played",
         "intro line", 61, 61, 13.7]
    )
    assert isinstance(status, osc_control.LeadInputStatus)
    assert status.input_line() == "Launchkey 49 - count 3 - 13 notes"
    assert status.melody_line() == "intro line (13 notes) - played"
    assert osc_control.parse_lead_input_state(
        [0, 0, "", 1.0, "off", 1, 0, 0.0, 0, "generated", "", -1, -1, 0.0]
    ).melody_line() == "none (generated line)"
    for bad in (
        [0, 0, "", 1.0, "off", 1, 0, 0.0, 0, "generated", "", -1, -1],
        [0, 0, "", 1.0, "3 bars", 1, 0, 0.0, 0, "generated", "", -1, -1, 0.0],
        [0, 0, "", 1.0, "off", 1, 0, 0.0, 0, "sideways", "", -1, -1, 0.0],
    ):
        try:
            osc_control.parse_lead_input_state(bad)
        except ValueError:
            pass
        else:
            raise AssertionError(f"accepted a bad lead input state: {bad}")

    # Stage 9.2: 16 values append the bend cents and the mod wheel; 14 still
    # parse (bend 0 / mod 0), 15 and 17 are rejected.
    extended = osc_control.parse_lead_input_state(
        [1, 1, "Launchkey 49", 0.8, "1 bar", 2, 0, 0.0, 13, "played",
         "intro line", 61, 61, 13.7, 12.0, 64]
    )
    assert extended.last_bend_cents == 12.0 and extended.last_mod == 64
    assert extended.input_line() == (
        "Launchkey 49 - idle - 13 notes - bend +12 ¢ - mod 64"
    ), extended.input_line()
    assert status.input_line() == "Launchkey 49 - count 3 - 13 notes"
    for bad_length in (15, 17):
        try:
            osc_control.parse_lead_input_state([0] * bad_length)
        except ValueError:
            pass
        else:
            raise AssertionError(f"accepted a {bad_length}-value input state")
    # Bend only / mod only stay compact; both zero omit both.
    assert osc_control.parse_lead_input_state(
        [1, 1, "L", 1.0, "off", 1, 0, 0.0, 0, "generated", "", -1, -1, 0.0,
         -7.0, 0]
    ).input_line() == "L - idle - 0 notes - bend -7 ¢"
    assert osc_control.parse_lead_input_state(
        [1, 1, "L", 1.0, "off", 1, 0, 0.0, 0, "generated", "", -1, -1, 0.0,
         0.0, 5]
    ).input_line() == "L - idle - 0 notes - mod 5"
    # The compatibility toggle still exists as a builder (a stale GUI may send
    # it); it toggles the input monitor only.
    assert osc_control.build_set_lead_input_enabled(False) == (
        "/ji/lead/input/set_enabled",
        [0],
    )
    assert osc_control.parse_melody_list([2, "Alpha", "zeta"]) == (
        "Alpha",
        "zeta",
    )
    assert osc_control.parse_melody_list([0]) == ()
    try:
        osc_control.parse_melody_list([2, "one"])
    except ValueError:
        pass
    else:
        raise AssertionError("a melody count mismatch was accepted")
    melody_state = osc_control.parse_melody_state(
        [2, "Alpha", "zeta", "loaded"]
    )
    assert melody_state == osc_control.MelodyStatus(2, "Alpha", "zeta", "loaded")

    # The status store keeps the new snapshots.
    store = osc_control.StatusStore()
    assert store.apply(
        "/ji/lead/input/state",
        [1, 1, "Launchkey 49", 0.8, "1 bar", 2, 0, 0.0, 13, "played",
         "intro line", 61, 61, 13.7],
    )
    assert store.lead_input is not None
    assert store.lead_input.port_name == "Launchkey 49"
    assert store.apply("/ji/lead/melody/list", [1, "intro line"])
    assert store.melody_names == ("intro line",)
    assert store.apply("/ji/lead/melody/state", [1, "intro line", "", "saved"])
    assert store.melody is not None
    assert store.melody.last_saved == "intro line"
    # QWERTY uses the pure control helper; the Qt window must import it (the
    # window cannot be imported in this Qt-less environment, so check the
    # source for the exact wiring that a missing import would break).
    ui_source = (_ROOT / "ui" / "ji_ui.py").read_text(encoding="utf-8")
    assert "from control.lead_input import key_midi" in ui_source, ui_source[:200]
    assert "key_midi(text, self._keyboard_octave)" in ui_source
    # Stage 9.2: the Live checkbox and its handler are gone.
    assert "lead_input_live_checkbox" not in ui_source, "the Live widget is back"
    assert "_on_lead_input_live" not in ui_source
    print("  builders, validators, 16/17-value lead state, 14-value input state,")
    print("  melody list/state, status lines and store, QWERTY helper import")


def run_pure_tests() -> None:
    assert "PyQt6" not in sys.modules, "ui.osc_control imported PyQt6"
    test_mutation_builders()
    test_autotune_builders()
    test_loop_builders()
    test_loop_edit_builders_and_state()
    test_rhythm_drum_builders()
    test_bass_builders()
    test_breath_and_bass_hits()
    test_arp_builders()
    test_status_parsers()
    test_status_store()
    test_lead_input_builders_and_parsers()
    test_osc_server_hello()
    test_bridge_ui_status()
    test_osc_controller()
    test_controller_startup_send_failure_is_nonfatal()
    test_home_builders_and_state()
    test_state_builders_and_store()


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(description="Stage 7 UI harness")
    group = parser.add_mutually_exclusive_group(required=True)
    group.add_argument(
        "--pure",
        action="store_true",
        help="run pure OSC/message tests (no PyQt6, no sockets)",
    )
    group.add_argument(
        "--qt",
        action="store_true",
        help="run the optional offscreen Qt smoke test (requires PyQt6)",
    )
    return parser


def main(argv: list[str] | None = None) -> int:
    parser = build_parser()
    args = parser.parse_args(argv)
    try:
        if args.pure:
            print("JI Stage 7 GUI -- pure OSC/message harness")
            run_pure_tests()
            print("\nALL UI PURE TESTS PASSED")
            return 0
        return run_qt_tests()
    except Exception:
        traceback.print_exc()
        print("\nUI TESTS FAILED", file=sys.stderr)
        return 1


if __name__ == "__main__":
    raise SystemExit(main())
