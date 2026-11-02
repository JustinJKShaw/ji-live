#!/usr/bin/env python3
"""Stage 7/8.3b performance GUI: one PyQt6 window.

The supported one-command start (bridge + GUI) is ``run/ji_station.py``; see
``docs/START_HERE.md``.  Running this module directly still works::

    uv run --with python-osc --with PyQt6 python ui/ji_ui.py

It requires the sclang side (``ji_start.scd``) and the bridge to be running.

The window is a control surface only.  It never talks to scsynth/Ableton
directly, never starts or stops the audio/MIDI rig, and closing it stops only
its own OSC listener.  All ranges, message builders, and status parsing live in
:mod:`ui.osc_control` so the pure tests do not need PyQt6.
"""

from __future__ import annotations

import argparse
import math
import sys
import time
from fractions import Fraction
from typing import Any, Callable

try:
    from PyQt6.QtCore import QObject, QSignalBlocker, Qt, QTimer, pyqtSignal
    from PyQt6.QtGui import QFont
    from PyQt6.QtWidgets import (
        QApplication,
        QCheckBox,
        QComboBox,
        QDial,
        QDoubleSpinBox,
        QGridLayout,
        QGroupBox,
        QHBoxLayout,
        QLabel,
        QMainWindow,
        QMessageBox,
        QPushButton,
        QScrollArea,
        QSpinBox,
        QVBoxLayout,
        QWidget,
    )
except ImportError as exc:  # pragma: no cover - depends on the human's env
    raise SystemExit(
        "PyQt6 is required for the Stage 7/8.3a GUI.\n"
        "Install/run it with: uv run --with python-osc --with PyQt6 "
        "python ui/ji_ui.py\n"
        "See docs/README_stage8_3.md for details."
    ) from exc

if __package__:  # ``python -m ui.ji_ui`` and normal package imports
    from . import osc_control
    from .osc_control import (
        ARP_DEFAULT_DIVISION,
        ARP_DEFAULT_EVOLVE,
        ARP_DEFAULT_EVOLVE_BEATS,
        ARP_DEFAULT_OCTAVES,
        ARP_DEFAULT_ORDER,
        ARP_DEFAULT_SYNC,
        ARP_DIVISIONS,
        ARP_EVOLVE_BEATS_MAX,
        ARP_EVOLVE_BEATS_MIN,
        ARP_EVOLVE_DEPTH_PARAM,
        ARP_SYNC_MODES,
        ARP_DEFAULT_SEED,
        ARP_OCTAVE_MAX,
        ARP_OCTAVE_MIN,
        ARP_ORDERS,
        ARP_PARAMS,
        ARP_SEED_MAX,
        ARP_SHARED_MODES,
        DRONE_DEFAULT_HOLD_BEATS,
        DRONE_DEFAULT_SEQ_GATE,
        DRONE_DEFAULT_SEQ_BARS,
        DRONE_DEFAULT_SEQ_NOTES,
        DRONE_SEQ_GATE_PARAM,
        DRONE_SEQ_BARS,
        DRONE_SEQ_NOTES_MAX,
        DRONE_SEQ_NOTES_MIN,
        DRONE_DEFAULT_MODE,
        DRONE_DEFAULT_PULSE_GRID,
        DRONE_DEFAULT_PULSE_NOTES,
        DRONE_DEFAULT_PULSE_SPAN,
        DRONE_DEFAULT_SLIDE_DIRECTION,
        DRONE_DEFAULT_SLIDE_SEMITONES,
        DRONE_HOLD_BEATS_MAX,
        DRONE_HOLD_BEATS_MIN,
        DRONE_MODES,
        DRONE_PULSE_GATE_PARAM,
        DRONE_PULSE_GRIDS,
        DRONE_PULSE_NOTES_MAX,
        DRONE_PULSE_NOTES_MIN,
        DRONE_PULSE_SPAN_MAX,
        DRONE_PULSE_SPAN_MIN,
        DRONE_SLIDE_DIRECTIONS,
        DRONE_SLIDE_PARAM,
        DRONE_SLIDE_SEMITONES_MAX,
        DRONE_SLIDE_SEMITONES_MIN,
        DRONE_VELOCITY_PARAM,
        HOME_DRIFT_DEFAULT,
        HOME_DRIFT_MAX,
        HOME_DRIFT_MIN,
        HOME_EVERY_DEFAULT,
        HOME_EVERY_MAX,
        HOME_EVERY_MIN,
        HOME_GLIDE_DEFAULT,
        HOME_HISTORY_DEFAULT,
        HOME_SEED_DEFAULT,
        HOME_SEED_MAX,
        HOME_SLOT_NAMES,
        HOME_SLOTS,
        LEAD_BASE_OCTAVE_MAX,
        LEAD_BASE_OCTAVE_MIN,
        LEAD_DEFAULT_BASE_OCTAVE,
        LEAD_DEFAULT_GRID,
        LEAD_DEFAULT_OCTAVE_RANGE,
        LEAD_DEFAULT_PHRASE_BARS,
        LEAD_DEFAULT_SEED,
        LEAD_DEFAULT_VARIATION,
        LEAD_DEFAULT_VARIATION_BARS,
        LEAD_GRIDS,
        LEAD_OCTAVE_RANGE_MAX,
        LEAD_OCTAVE_RANGE_MIN,
        LEAD_PARAMS,
        LEAD_PHRASE_BARS_MAX,
        LEAD_PHRASE_BARS_MIN,
        LEAD_SEED_MAX,
        LEAD_VARIATIONS,
        LEAD_VARIATION_BARS_MAX,
        LEAD_VARIATION_BARS_MIN,
        AUTOTUNE_CHANNELS,
        AUTOTUNE_PARAMS,
        BPM_DEFAULT,
        BPM_MAX,
        BPM_MIN,
        BASS_BASE_OCTAVE_MAX,
        BASS_BASE_OCTAVE_MIN,
        BASS_DEFAULT_BASE_OCTAVE,
        BASS_DEFAULT_ENABLED,
        BASS_DEFAULT_GRID,
        BASS_DEFAULT_OCTAVE_RANGE,
        BASS_DEFAULT_PITCH_MODE,
        BASS_DEFAULT_PRESET,
        BASS_DEFAULT_SEED,
        BASS_GRIDS,
        BASS_LENGTH_MAX,
        BASS_LENGTH_MIN,
        BASS_OCTAVE_RANGE_MAX,
        BASS_OCTAVE_RANGE_MIN,
        BASS_PARAMS,
        BASS_PITCH_MODES,
        BASS_PRESETS,
        BASS_PRESET_CUSTOM,
        BASS_PRESET_DISPLAY,
        BASS_SEED_MAX,
        BASS_SUB_OCTAVES,
        DRUM_LANE_DEFAULT_MASK,
        DRUM_LANE_DEFS,
        DRUM_RATE_LABELS,
        DRUM_RATES,
        GROOVE_DEFAULT_PRESET,
        GROOVE_DEFAULT_SEED,
        GROOVE_PARAMS,
        GROOVE_PRESETS,
        GROOVE_SEED_MAX,
        LOOP_BEATS_DEFAULT,
        LOOP_BEATS_MAX,
        LOOP_BEATS_MIN,
        LOOP_LENGTH_DEFAULT,
        LOOP_LENGTH_MAX,
        LOOP_LENGTH_MIN,
        METER_DEN_DEFAULT,
        METER_DENOMINATORS,
        METER_NUM_DEFAULT,
        METER_NUM_MAX,
        METER_NUM_MIN,
        MAX_GRID_STEPS,
        MUTATION_PARAMS,
        OSCController,
        StatusStore,
        grid_steps_for_meter,
    )
else:  # pragma: no cover - direct ``python ui/ji_ui.py``
    import osc_control  # type: ignore[no-redef]
    from osc_control import (  # type: ignore[no-redef]
        ARP_DEFAULT_DIVISION,
        ARP_DEFAULT_EVOLVE,
        ARP_DEFAULT_EVOLVE_BEATS,
        ARP_DEFAULT_OCTAVES,
        ARP_DEFAULT_ORDER,
        ARP_DEFAULT_SYNC,
        ARP_DIVISIONS,
        ARP_EVOLVE_BEATS_MAX,
        ARP_EVOLVE_BEATS_MIN,
        ARP_EVOLVE_DEPTH_PARAM,
        ARP_SYNC_MODES,
        ARP_DEFAULT_SEED,
        ARP_OCTAVE_MAX,
        ARP_OCTAVE_MIN,
        ARP_ORDERS,
        ARP_PARAMS,
        ARP_SEED_MAX,
        ARP_SHARED_MODES,
        DRONE_DEFAULT_HOLD_BEATS,
        DRONE_DEFAULT_SEQ_GATE,
        DRONE_DEFAULT_SEQ_BARS,
        DRONE_DEFAULT_SEQ_NOTES,
        DRONE_SEQ_GATE_PARAM,
        DRONE_SEQ_BARS,
        DRONE_SEQ_NOTES_MAX,
        DRONE_SEQ_NOTES_MIN,
        DRONE_DEFAULT_MODE,
        DRONE_DEFAULT_PULSE_GRID,
        DRONE_DEFAULT_PULSE_NOTES,
        DRONE_DEFAULT_PULSE_SPAN,
        DRONE_DEFAULT_SLIDE_DIRECTION,
        DRONE_DEFAULT_SLIDE_SEMITONES,
        DRONE_HOLD_BEATS_MAX,
        DRONE_HOLD_BEATS_MIN,
        DRONE_MODES,
        DRONE_PULSE_GATE_PARAM,
        DRONE_PULSE_GRIDS,
        DRONE_PULSE_NOTES_MAX,
        DRONE_PULSE_NOTES_MIN,
        DRONE_PULSE_SPAN_MAX,
        DRONE_PULSE_SPAN_MIN,
        DRONE_SLIDE_DIRECTIONS,
        DRONE_SLIDE_PARAM,
        DRONE_SLIDE_SEMITONES_MAX,
        DRONE_SLIDE_SEMITONES_MIN,
        DRONE_VELOCITY_PARAM,
        HOME_DRIFT_DEFAULT,
        HOME_DRIFT_MAX,
        HOME_DRIFT_MIN,
        HOME_EVERY_DEFAULT,
        HOME_EVERY_MAX,
        HOME_EVERY_MIN,
        HOME_GLIDE_DEFAULT,
        HOME_HISTORY_DEFAULT,
        HOME_SEED_DEFAULT,
        HOME_SEED_MAX,
        HOME_SLOT_NAMES,
        HOME_SLOTS,
        LEAD_BASE_OCTAVE_MAX,
        LEAD_BASE_OCTAVE_MIN,
        LEAD_DEFAULT_BASE_OCTAVE,
        LEAD_DEFAULT_GRID,
        LEAD_DEFAULT_OCTAVE_RANGE,
        LEAD_DEFAULT_PHRASE_BARS,
        LEAD_DEFAULT_SEED,
        LEAD_DEFAULT_VARIATION,
        LEAD_DEFAULT_VARIATION_BARS,
        LEAD_GRIDS,
        LEAD_OCTAVE_RANGE_MAX,
        LEAD_OCTAVE_RANGE_MIN,
        LEAD_PARAMS,
        LEAD_PHRASE_BARS_MAX,
        LEAD_PHRASE_BARS_MIN,
        LEAD_SEED_MAX,
        LEAD_VARIATIONS,
        LEAD_VARIATION_BARS_MAX,
        LEAD_VARIATION_BARS_MIN,
        AUTOTUNE_CHANNELS,
        AUTOTUNE_PARAMS,
        BPM_DEFAULT,
        BPM_MAX,
        BPM_MIN,
        BASS_BASE_OCTAVE_MAX,
        BASS_BASE_OCTAVE_MIN,
        BASS_DEFAULT_BASE_OCTAVE,
        BASS_DEFAULT_ENABLED,
        BASS_DEFAULT_GRID,
        BASS_DEFAULT_OCTAVE_RANGE,
        BASS_DEFAULT_PITCH_MODE,
        BASS_DEFAULT_PRESET,
        BASS_DEFAULT_SEED,
        BASS_GRIDS,
        BASS_LENGTH_MAX,
        BASS_LENGTH_MIN,
        BASS_OCTAVE_RANGE_MAX,
        BASS_OCTAVE_RANGE_MIN,
        BASS_PARAMS,
        BASS_PITCH_MODES,
        BASS_PRESETS,
        BASS_PRESET_CUSTOM,
        BASS_PRESET_DISPLAY,
        BASS_SEED_MAX,
        BASS_SUB_OCTAVES,
        DRUM_LANE_DEFAULT_MASK,
        DRUM_LANE_DEFS,
        DRUM_RATE_LABELS,
        DRUM_RATES,
        GROOVE_DEFAULT_PRESET,
        GROOVE_DEFAULT_SEED,
        GROOVE_PARAMS,
        GROOVE_PRESETS,
        GROOVE_SEED_MAX,
        LOOP_BEATS_DEFAULT,
        LOOP_BEATS_MAX,
        LOOP_BEATS_MIN,
        LOOP_LENGTH_DEFAULT,
        LOOP_LENGTH_MAX,
        LOOP_LENGTH_MIN,
        METER_DEN_DEFAULT,
        METER_DENOMINATORS,
        METER_NUM_DEFAULT,
        METER_NUM_MAX,
        METER_NUM_MIN,
        MAX_GRID_STEPS,
        MUTATION_PARAMS,
        OSCController,
        StatusStore,
        grid_steps_for_meter,
    )


# ---------------------------------------------------------------------------
# Reusable knob
# ---------------------------------------------------------------------------


class Knob(QWidget):
    """A compact QDial with a caption and a live value/units label."""

    valueChanged = pyqtSignal(float)

    def __init__(
        self,
        caption: str,
        low: float,
        high: float,
        default: float,
        *,
        steps: int = 1000,
        integer: bool = False,
        fmt: str = "{:.2f}",
        suffix: str = "",
        parent: QWidget | None = None,
    ) -> None:
        super().__init__(parent)
        self._low = float(low)
        self._high = float(high)
        self._integer = bool(integer)
        self._steps = max(1, int(steps) if not integer else int(round(high - low)))
        self._fmt = fmt
        self._suffix = suffix

        self.caption_label = QLabel(caption)
        self.caption_label.setAlignment(Qt.AlignmentFlag.AlignHCenter)

        self.dial = QDial()
        self.dial.setRange(0, self._steps)
        self.dial.setNotchesVisible(False)
        self.dial.setFixedSize(32, 32)

        self.value_label = QLabel()
        self.value_label.setAlignment(Qt.AlignmentFlag.AlignHCenter)
        self.value_label.setMinimumWidth(46)

        layout = QVBoxLayout(self)
        layout.setContentsMargins(0, 0, 0, 0)
        layout.setSpacing(0)
        layout.addWidget(self.caption_label)
        layout.addWidget(self.dial, 0, Qt.AlignmentFlag.AlignHCenter)
        layout.addWidget(self.value_label)

        self.dial.valueChanged.connect(self._on_dial)
        self.set_value(default, emit=False)

    # -- conversion ------------------------------------------------------

    def _value_from_position(self, position: int) -> float:
        if self._integer:
            return float(int(round(self._low)) + int(position))
        span = self._high - self._low
        value = self._low + (span * float(position) / float(self._steps))
        return max(self._low, min(self._high, value))

    def _position_from_value(self, value: float) -> int:
        if self._integer:
            return int(round(value - self._low))
        span = self._high - self._low
        if span <= 0.0:
            return 0
        return int(round((value - self._low) / span * self._steps))

    # -- public ----------------------------------------------------------

    def value(self) -> float:
        return self._value_from_position(self.dial.value())

    def set_value(self, value: float, *, emit: bool = False) -> None:
        try:
            numeric = float(value)
        except (TypeError, ValueError):
            return
        if self._integer:
            numeric = float(int(round(numeric)))
        numeric = max(self._low, min(self._high, numeric))
        # Defensive: QDial's default maximum is 99, so re-assert our range
        # before applying a position that may be larger.
        if self.dial.maximum() != self._steps:
            self.dial.setRange(0, self._steps)
        position = self._position_from_value(numeric)
        blocker = QSignalBlocker(self.dial)
        self.dial.setValue(position)
        del blocker
        self._refresh_label(numeric)
        if emit:
            self.valueChanged.emit(self.value())

    def set_range(
        self, low: float, high: float, *, emit: bool = False
    ) -> None:
        current = self.value()
        self._low = float(low)
        self._high = float(high)
        if self._integer:
            self._steps = max(1, int(round(self._high - self._low)))
            blocker = QSignalBlocker(self.dial)
            self.dial.setRange(0, self._steps)
            del blocker
        self.set_value(current, emit=emit)

    def _refresh_label(self, value: float) -> None:
        if self._integer:
            text = self._fmt.format(int(round(value)))
        else:
            text = self._fmt.format(value)
        self.value_label.setText(text + self._suffix)

    def _on_dial(self, _position: int) -> None:
        value = self.value()
        self._refresh_label(value)
        self.valueChanged.emit(value)


# ---------------------------------------------------------------------------
# Qt status relay
# ---------------------------------------------------------------------------


def _ratio_label(ratio: float) -> str:
    """``1.5 -> 3/2`` for the drone status line (else ``1.500``)."""

    try:
        value = float(ratio)
    except (TypeError, ValueError):
        return "?"
    if not math.isfinite(value) or value <= 0.0:
        return "?"
    fraction = Fraction(value).limit_denominator(64)
    if abs(float(fraction) - value) <= max(1e-6, abs(value) * 1e-6):
        return f"{fraction.numerator}/{fraction.denominator}"
    return f"{value:.3f}"


class _StatusRelay(QObject):
    """Carries status messages from the OSC thread to the Qt main thread."""

    message = pyqtSignal(str, object)


# ---------------------------------------------------------------------------
# Main window
# ---------------------------------------------------------------------------


class MainWindow(QMainWindow):
    """One screen: Mutation, Autotune, Loops, Rhythm, Groove, Bass, Arp, Drone, Lead, Status."""

    def __init__(
        self,
        controller: Any,
        *,
        autostart: bool = False,
        parent: QWidget | None = None,
    ) -> None:
        super().__init__(parent)
        self.controller = controller
        self.store = StatusStore()
        self._pending: dict[tuple[Any, ...], float] = {}
        self._status_seen = False
        self._started = False
        self._midi_sync = False
        self._rhythm_sync = False
        self._drum_sync = False
        self._loop_sync = False
        self._loop_custom: set[int] = set()
        self._groove_sync = False
        self._bass_sync = False
        self._arp_sync = False
        self._lead_sync = False
        self._drone_sync = False
        self._home_sync = False
        self._home_return_total: int | None = None
        self._home_active_slot: int | None = None
        self._grid_sync = False
        self._grid_built_steps = 0
        self.step_cells: dict[tuple[int, int], QPushButton] = {}
        self._quantize_deadline: float | None = None
        self._quantize_pending = 0

        self._relay = _StatusRelay()
        self._relay.message.connect(
            self._on_status_message, Qt.ConnectionType.QueuedConnection
        )
        self._wire_controller_callback()

        self.setWindowTitle("JI Station - Stage 8.4 Performance")
        # One row taller than 8.3d so the extra Home row does not push Status
        # below the fold; the scroll area remains the small-screen safety net.
        self.resize(1280, 768)
        self.setMinimumSize(1080, 600)
        compact = QFont()
        compact.setPointSize(8)
        self.setFont(compact)

        central = QWidget()
        central.setMinimumWidth(1040)
        outer = QVBoxLayout(central)
        outer.setContentsMargins(4, 4, 4, 4)
        outer.setSpacing(3)

        top_row = QHBoxLayout()
        top_row.setSpacing(4)
        top_row.addWidget(self._build_mutation_group(), 3)
        top_row.addWidget(self._build_midi_group(), 2)
        outer.addLayout(top_row)
        outer.addWidget(self._build_home_group())
        outer.addWidget(self._build_autotune_group())
        outer.addWidget(self._build_loops_group())
        outer.addWidget(self._build_rhythm_group())
        outer.addWidget(self._build_groove_group())
        outer.addWidget(self._build_bass_group())
        arp_drone_row = QHBoxLayout()
        arp_drone_row.setSpacing(4)
        arp_drone_row.addWidget(self._build_arp_group(), 3)
        arp_drone_row.addWidget(self._build_drone_group(), 2)
        outer.addLayout(arp_drone_row)
        outer.addWidget(self._build_lead_group())
        outer.addWidget(self._build_status_group())
        outer.addStretch(1)

        # A scroll area is only a safety net for small screens; the compact
        # layout fits at the default size without scrolling.
        scroll = QScrollArea()
        scroll.setWidgetResizable(True)
        scroll.setWidget(central)
        self.setCentralWidget(scroll)

        self._send_timer = QTimer(self)
        self._send_timer.setInterval(33)  # ~30 Hz coalesced knob sends
        self._send_timer.timeout.connect(self._flush_pending)
        self._send_timer.start()

        # Local countdown for the pending/quantize status line.  The bridge
        # sends one /ji/quantize/state per change, not per animation frame.
        self._quantize_timer = QTimer(self)
        self._quantize_timer.setInterval(100)
        self._quantize_timer.timeout.connect(self._update_quantize_label)
        self._quantize_timer.start()

        self._refresh_status_labels()
        self._sync_home_controls()  # start disabled until a slot is captured

        if autostart:
            self.start_controller()

    # -- controller / relay ---------------------------------------------

    def _wire_controller_callback(self) -> None:
        setter = getattr(self.controller, "set_status_callback", None)
        if callable(setter):
            setter(self._relay.message.emit)

    def start_controller(self) -> None:
        """Start the OSC controller (listener first, then hello/query)."""

        self.controller.start()
        self._started = True

    def _on_status_message(self, address: str, args: Any) -> None:
        try:
            recognised = self.store.apply(address, list(args))
        except (TypeError, ValueError) as exc:
            self._set_message(f"Ignored malformed {address}: {exc}")
            return
        if not recognised:
            return
        self._status_seen = True

        if address == osc_control.UI_STATE and self.store.control is not None:
            self._sync_mutation_knobs(self.store.control)
        if address == osc_control.AUTOTUNE_STATE:
            channel = int(round(float(args[0])))
            self._sync_autotune_knobs(channel, self.store.autotune[channel])
        if address == osc_control.LOOP_STATE:
            index = int(round(float(args[0])))
            self._refresh_loop_label(index)
        if address == osc_control.LOOP_CONFIG:
            index = int(round(float(args[0])))
            self._sync_loop_config(index)
        if address in (osc_control.MIDI_STATUS, osc_control.MIDI_STATE):
            self._sync_midi_controls()
        if address == osc_control.RHYTHM_STATE:
            self._sync_rhythm_controls()
            self._update_quantize_label()
        if address == osc_control.DRUMS_STATE:
            self._sync_drum_controls()
        if address == osc_control.DRUMS_GROOVE:
            self._sync_groove_controls()
        if address == osc_control.DRUMS_GRID:
            self._sync_grid()
        if address == osc_control.QUANTIZE_STATE:
            self._sync_quantize()
        if address == osc_control.BASS_STATE:
            self._sync_bass_controls()
        if address == osc_control.BASS_NOTE:
            self._update_bass_label()
        if address == osc_control.ARP_STATE:
            self._sync_arp_controls()
        if address == osc_control.LEAD_STATE:
            self._sync_lead_controls()
        if address == osc_control.LEAD_NOTE:
            self._update_lead_label()
        if address == osc_control.DRONE_STATE:
            self._sync_drone_controls()
        if address == osc_control.HOME_STATE:
            self._sync_home_controls()

        self._refresh_status_labels()

    # -- widget construction --------------------------------------------

    def _build_mutation_group(self) -> QGroupBox:
        group = QGroupBox("Mutation")
        layout = QHBoxLayout(group)
        layout.setContentsMargins(4, 2, 4, 2)
        layout.setSpacing(1)

        self.mutation_knobs: dict[str, Knob] = {}
        for spec in MUTATION_PARAMS:
            steps = (
                int(round(spec.high - spec.low))
                if spec.integer
                else (2000 if spec.high - spec.low >= 2.0 else 1000)
            )
            knob = Knob(
                spec.label,
                spec.low,
                spec.high,
                spec.default,
                steps=steps,
                integer=spec.integer,
                fmt=spec.fmt,
                suffix=spec.suffix,
            )
            knob.valueChanged.connect(
                lambda value, key=spec.key: self._on_mutation_knob(key, value)
            )
            if spec.key == "glide_time":
                # Stage 8.4.1: the knob is the replaced-class melt time, not a
                # global autotune retune.
                knob.setToolTip(
                    "Stepwise/replaced class melt time: the replaced class "
                    "travels to its new ratio over this time, for every voice."
                )
            self.mutation_knobs[spec.key] = knob
            layout.addWidget(knob)

        layout.addSpacing(6)
        self.mutate_button = QPushButton("Mutate")
        self.octave_button = QPushButton("Octave")
        for button in (self.mutate_button, self.octave_button):
            button.setMinimumHeight(26)
            button.setMinimumWidth(78)
        self.mutate_button.clicked.connect(self._on_mutate_clicked)
        self.octave_button.clicked.connect(self._on_octave_clicked)
        layout.addWidget(self.mutate_button)
        layout.addWidget(self.octave_button)
        layout.addStretch(1)
        return group

    def _build_midi_group(self) -> QGroupBox:
        group = QGroupBox("MIDI")
        layout = QVBoxLayout(group)
        layout.setContentsMargins(4, 2, 4, 2)
        layout.setSpacing(1)

        row = QHBoxLayout()
        row.setSpacing(6)
        self.midi_drone_checkbox = QCheckBox("Drone on")
        self.midi_arp_checkbox = QCheckBox("Arp on")
        self.midi_drone_checkbox.setEnabled(False)
        self.midi_arp_checkbox.setEnabled(False)
        self.midi_drone_checkbox.toggled.connect(self._on_midi_toggle)
        self.midi_arp_checkbox.toggled.connect(self._on_midi_toggle)
        row.addWidget(self.midi_drone_checkbox)
        row.addWidget(self.midi_arp_checkbox)
        row.addStretch(1)
        layout.addLayout(row)

        self.midi_label = QLabel("MIDI: waiting for bridge")
        self.midi_label.setWordWrap(True)
        self.midi_label.setTextInteractionFlags(
            Qt.TextInteractionFlag.TextSelectableByMouse
        )
        layout.addWidget(self.midi_label)
        return group

    def _build_home_group(self) -> QGroupBox:
        """Stage 8.4 compact Home group (one control row + the status line)."""

        group = QGroupBox("Home")
        layout = QVBoxLayout(group)
        layout.setContentsMargins(4, 2, 4, 2)
        layout.setSpacing(1)

        row = QHBoxLayout()
        row.setSpacing(3)

        row.addWidget(QLabel("Slot"))
        self.home_slot_combo = QComboBox()
        self.home_slot_combo.addItems(list(HOME_SLOT_NAMES))
        self.home_slot_combo.setFixedWidth(44)
        self.home_slot_combo.currentIndexChanged.connect(
            self._on_home_slot_changed
        )
        row.addWidget(self.home_slot_combo)

        self.home_set_button = QPushButton("Set home")
        self.home_set_button.setMinimumHeight(22)
        self.home_set_button.setToolTip(
            "Capture the current frequency set into the selected slot and make "
            "it the active home."
        )
        self.home_set_button.clicked.connect(self._on_home_set)
        row.addWidget(self.home_set_button)

        self.home_go_button = QPushButton("Go")
        self.home_go_button.setMinimumHeight(22)
        self.home_go_button.setToolTip(
            "Smooth return to the selected slot: one batch per quarter beat."
        )
        self.home_go_button.clicked.connect(self._on_home_go)
        row.addWidget(self.home_go_button)

        self.home_clear_button = QPushButton("Clear")
        self.home_clear_button.setMinimumHeight(22)
        self.home_clear_button.setToolTip("Forget the selected slot.")
        self.home_clear_button.clicked.connect(self._on_home_clear)
        row.addWidget(self.home_clear_button)

        row.addSpacing(6)
        self.home_return_button = QPushButton("Return")
        self.home_return_button.setMinimumHeight(22)
        self.home_return_button.setToolTip(
            "Return to the active home in ceil(distance / max_changes) beats."
        )
        self.home_return_button.clicked.connect(self._on_home_return)
        row.addWidget(self.home_return_button)

        self.home_back_button = QPushButton("Back")
        self.home_back_button.setMinimumHeight(22)
        self.home_back_button.setToolTip(
            "Restore the immediately previous frequency set; press again to "
            "walk further back."
        )
        self.home_back_button.clicked.connect(self._on_home_back)
        row.addWidget(self.home_back_button)

        self.home_jump_button = QPushButton("Jump")
        self.home_jump_button.setMinimumHeight(22)
        self.home_jump_button.setToolTip(
            "instant set swap, not a smooth return"
        )
        self.home_jump_button.clicked.connect(self._on_home_jump)
        row.addWidget(self.home_jump_button)

        row.addSpacing(6)
        self.home_auto_checkbox = QCheckBox("Auto")
        self.home_auto_checkbox.setToolTip(
            "Return automatically after Every mutations (needs a captured slot)."
        )
        self.home_auto_checkbox.toggled.connect(self._on_home_auto)
        row.addWidget(self.home_auto_checkbox)

        row.addWidget(QLabel("Every"))
        self.home_every_spin = QSpinBox()
        self.home_every_spin.setRange(HOME_EVERY_MIN, HOME_EVERY_MAX)
        self.home_every_spin.setValue(HOME_EVERY_DEFAULT)
        self.home_every_spin.setFixedWidth(46)
        self.home_every_spin.setToolTip("Mutations before an automatic return.")
        self.home_every_spin.valueChanged.connect(self._on_home_every)
        row.addWidget(self.home_every_spin)

        row.addWidget(QLabel("Drift"))
        self.home_drift_spin = QSpinBox()
        self.home_drift_spin.setRange(HOME_DRIFT_MIN, HOME_DRIFT_MAX)
        self.home_drift_spin.setValue(HOME_DRIFT_DEFAULT)
        self.home_drift_spin.setFixedWidth(46)
        self.home_drift_spin.setToolTip(
            "Maximum distance from the active home during a journey; "
            "0 = unlimited."
        )
        self.home_drift_spin.valueChanged.connect(self._on_home_drift)
        row.addWidget(self.home_drift_spin)

        row.addWidget(QLabel("Seed"))
        self.home_seed_spin = QSpinBox()
        self.home_seed_spin.setRange(0, int(HOME_SEED_MAX))
        self.home_seed_spin.setValue(int(HOME_SEED_DEFAULT))
        self.home_seed_spin.setFixedWidth(90)
        self.home_seed_spin.setToolTip(
            "Journey seed. Same home + same seed + same number of mutations "
            "reproduces the same journey (a manual mutation is part of it)."
        )
        self.home_seed_spin.valueChanged.connect(self._on_home_seed)
        row.addWidget(self.home_seed_spin)

        row.addSpacing(6)
        self.home_label = QLabel("Home: none (Set home to capture)")
        self.home_label.setTextInteractionFlags(
            Qt.TextInteractionFlag.TextSelectableByMouse
        )
        row.addWidget(self.home_label, 1)
        layout.addLayout(row)

        self.home_controls: list[QWidget] = [
            self.home_slot_combo,
            self.home_set_button,
            self.home_go_button,
            self.home_clear_button,
            self.home_return_button,
            self.home_back_button,
            self.home_jump_button,
            self.home_auto_checkbox,
            self.home_every_spin,
            self.home_drift_spin,
            self.home_seed_spin,
        ]
        return group

    def _build_autotune_group(self) -> QGroupBox:
        group = QGroupBox("Live Autotune")
        layout = QHBoxLayout(group)
        layout.setContentsMargins(4, 2, 4, 2)
        layout.setSpacing(4)

        self.link_checkbox = QCheckBox("Link")
        self.link_checkbox.setToolTip("Move both input channels together")
        layout.addWidget(self.link_checkbox, 0, Qt.AlignmentFlag.AlignVCenter)

        self.autotune_knobs: dict[tuple[int, str], Knob] = {}
        for channel in AUTOTUNE_CHANNELS:
            channel_group = QGroupBox(f"Input {channel}")
            row = QHBoxLayout(channel_group)
            row.setContentsMargins(3, 1, 3, 1)
            row.setSpacing(1)
            for spec in AUTOTUNE_PARAMS:
                steps = (
                    int(round(spec.high - spec.low))
                    if spec.integer
                    else (
                        2000
                        if spec.high - spec.low >= 2.0
                        else 1000
                    )
                )
                knob = Knob(
                    spec.label,
                    spec.low,
                    spec.high,
                    spec.default,
                    steps=steps,
                    integer=spec.integer,
                    fmt=spec.fmt,
                    suffix=spec.suffix,
                )
                knob.valueChanged.connect(
                    lambda value, ch=channel, key=spec.key: self._on_autotune_knob(
                        ch, key, value
                    )
                )
                self.autotune_knobs[(channel, spec.key)] = knob
                row.addWidget(knob)
            layout.addWidget(channel_group)
        layout.addStretch(1)
        return group

    def _build_loops_group(self) -> QGroupBox:
        group = QGroupBox("Loops")
        layout = QVBoxLayout(group)
        layout.setContentsMargins(4, 2, 4, 2)
        layout.setSpacing(3)

        loop_row = QHBoxLayout()
        loop_row.setSpacing(3)
        self.loop_state_labels: dict[int, QLabel] = {}
        self.loop_buttons: dict[tuple[int, str], QPushButton] = {}
        self.loop_mode_combos: dict[int, QComboBox] = {}
        self.loop_length_spins: dict[int, QDoubleSpinBox] = {}

        for index in range(1, 5):
            loop_group = QGroupBox(f"Loop {index}")
            loop_layout = QVBoxLayout(loop_group)
            loop_layout.setContentsMargins(3, 1, 3, 1)
            loop_layout.setSpacing(1)

            state_label = QLabel("waiting")
            state_label.setAlignment(Qt.AlignmentFlag.AlignHCenter)
            self.loop_state_labels[index] = state_label
            loop_layout.addWidget(state_label)

            controls = QHBoxLayout()
            controls.setSpacing(2)
            mode_combo = QComboBox()
            mode_combo.addItems(["Free", "Beat"])
            mode_combo.setCurrentIndex(0)
            mode_combo.setFixedWidth(68)
            mode_combo.setToolTip(
                "Free: seconds, immediate. Beat: next-beat quantized. Tempo "
                "changes do not time-stretch an existing loop; stop and "
                "re-record, or use free mode."
            )
            mode_combo.currentIndexChanged.connect(
                lambda _position, i=index: self._on_loop_mode_changed(i)
            )
            self.loop_mode_combos[index] = mode_combo

            length_spin = QDoubleSpinBox()
            length_spin.setDecimals(1)
            length_spin.setRange(LOOP_LENGTH_MIN, LOOP_LENGTH_MAX)
            length_spin.setSingleStep(0.5)
            length_spin.setValue(LOOP_LENGTH_DEFAULT)
            length_spin.setSuffix(" s")
            length_spin.setFixedWidth(78)
            length_spin.setToolTip(
                "Free: 1-30 seconds. Beat: 1-64 beats, taken as "
                "beats * 60 / bpm at record time."
            )
            length_spin.valueChanged.connect(
                lambda value, i=index: self._on_loop_length_changed(i, value)
            )
            self.loop_length_spins[index] = length_spin

            controls.addWidget(mode_combo)
            controls.addWidget(length_spin)
            controls.addStretch(1)
            loop_layout.addLayout(controls)

            grid = QGridLayout()
            grid.setContentsMargins(0, 0, 0, 0)
            grid.setSpacing(1)
            # QPushButton.clicked emits a bool; bind it to ``_checked`` so it
            # cannot override the loop index default.
            actions = (
                (
                    "record",
                    "Rec",
                    lambda _checked=False, i=index: self._record_loop(i),
                ),
                (
                    "stop_record",
                    "Stop Rec",
                    lambda _checked=False, i=index: self._guard(
                        self.controller.loop_stop_record, i
                    ),
                ),
                (
                    "play",
                    "Play",
                    lambda _checked=False, i=index: self._play_loop(i),
                ),
                (
                    "stop",
                    "Stop",
                    lambda _checked=False, i=index: self._guard(
                        self.controller.loop_stop, i
                    ),
                ),
                (
                    "clear",
                    "Clear",
                    lambda _checked=False, i=index: self._guard(
                        self.controller.loop_clear, i
                    ),
                ),
            )
            placements = (
                (0, 0, 1, 1),
                (0, 1, 1, 1),
                (0, 2, 1, 1),
                (1, 0, 1, 1),
                (1, 1, 1, 2),
            )
            for (name, text, slot), (row, column, row_span, col_span) in zip(
                actions, placements
            ):
                button = QPushButton(text)
                button.setMinimumHeight(20)
                button.clicked.connect(slot)
                self.loop_buttons[(index, name)] = button
                grid.addWidget(button, row, column, row_span, col_span)
            loop_layout.addLayout(grid)
            loop_row.addWidget(loop_group)

        layout.addLayout(loop_row)

        bottom = QHBoxLayout()
        bottom.setSpacing(5)
        self.stop_all_button = QPushButton("Stop All")
        self.stop_all_button.setMinimumHeight(24)
        self.stop_all_button.setMinimumWidth(90)
        self.stop_all_button.clicked.connect(
            lambda: self._guard(self.controller.loop_stop_all)
        )
        self.length_spin = QDoubleSpinBox()
        self.length_spin.setDecimals(1)
        self.length_spin.setRange(LOOP_LENGTH_MIN, LOOP_LENGTH_MAX)
        self.length_spin.setSingleStep(0.5)
        self.length_spin.setValue(LOOP_LENGTH_DEFAULT)
        self.length_spin.setSuffix(" s")
        self.length_spin.setFixedWidth(80)
        self.length_spin.setToolTip(
            "Default free-loop length; loops you have not set individually "
            "follow this value."
        )
        self.length_spin.valueChanged.connect(self._on_length_changed)
        bottom.addWidget(self.stop_all_button)
        bottom.addWidget(QLabel("Default len"))
        bottom.addWidget(self.length_spin)
        bottom.addStretch(1)
        layout.addLayout(bottom)
        return group

    def _build_rhythm_group(self) -> QGroupBox:
        group = QGroupBox("Rhythm")
        layout = QVBoxLayout(group)
        layout.setContentsMargins(4, 2, 4, 2)
        layout.setSpacing(1)

        top = QHBoxLayout()
        top.setSpacing(4)

        self.transport_button = QPushButton("Play")
        self.transport_button.setCheckable(True)
        self.transport_button.setMinimumHeight(24)
        self.transport_button.setMinimumWidth(64)
        self.transport_button.setToolTip(
            "Start/stop rhythm scheduling. Beat-mode loops stop on transport "
            "stop; free loops keep running."
        )
        self.transport_button.toggled.connect(self._on_transport_toggled)
        top.addWidget(self.transport_button)

        top.addWidget(QLabel("BPM"))
        self.bpm_spin = QSpinBox()
        self.bpm_spin.setRange(int(BPM_MIN), int(BPM_MAX))
        self.bpm_spin.setValue(int(BPM_DEFAULT))
        self.bpm_spin.setFixedWidth(64)
        self.bpm_spin.setToolTip(
            "Quarter-note BPM (20-300). Changes while running take effect on "
            "the next drum step and on sclang's loop clock."
        )
        self.bpm_spin.valueChanged.connect(self._on_bpm_changed)
        top.addWidget(self.bpm_spin)

        top.addSpacing(2)
        top.addWidget(QLabel("Meter"))
        self.meter_num_spin = QSpinBox()
        self.meter_num_spin.setRange(METER_NUM_MIN, METER_NUM_MAX)
        self.meter_num_spin.setValue(METER_NUM_DEFAULT)
        self.meter_num_spin.setFixedWidth(46)
        self.meter_num_spin.valueChanged.connect(self._on_meter_changed)
        self.meter_den_combo = QComboBox()
        self.meter_den_combo.addItems(
            [str(value) for value in METER_DENOMINATORS]
        )
        self.meter_den_combo.setCurrentText(str(METER_DEN_DEFAULT))
        self.meter_den_combo.setFixedWidth(52)
        self.meter_den_combo.currentIndexChanged.connect(self._on_meter_changed)
        top.addWidget(self.meter_num_spin)
        top.addWidget(QLabel("/"))
        top.addWidget(self.meter_den_combo)

        top.addSpacing(4)
        self.drum_enable_checkbox = QCheckBox("Drums on")
        # Disabled until /ji/drums/state says the port is available; the state
        # message also sets the checked value.
        self.drum_enable_checkbox.setChecked(False)
        self.drum_enable_checkbox.setEnabled(False)
        self.drum_enable_checkbox.toggled.connect(self._on_drum_enabled_toggled)
        self.drum_rate_combo = QComboBox()
        self.drum_rate_combo.addItems(list(DRUM_RATE_LABELS))
        self.drum_rate_combo.setCurrentIndex(DRUM_RATES.index(1.0))
        self.drum_rate_combo.setFixedWidth(80)
        self.drum_rate_combo.setEnabled(False)
        self.drum_rate_combo.currentIndexChanged.connect(
            self._on_drum_rate_changed
        )
        top.addWidget(self.drum_enable_checkbox)
        top.addWidget(self.drum_rate_combo)

        self.resync_button = QPushButton("Resync")
        self.resync_button.setMinimumHeight(24)
        self.resync_button.setMinimumWidth(60)
        self.resync_button.clicked.connect(
            lambda: self._guard(self.controller.request_rhythm_resync)
        )
        top.addWidget(self.resync_button)
        top.addStretch(1)
        layout.addLayout(top)

        self.rhythm_label = QLabel("Rhythm: waiting for bridge")
        self.drum_label = QLabel("Drums: waiting for bridge")
        self.quantize_label = QLabel("Next beat: waiting for bridge")
        status_row = QHBoxLayout()
        status_row.setSpacing(12)
        for label in (self.rhythm_label, self.drum_label, self.quantize_label):
            label.setTextInteractionFlags(
                Qt.TextInteractionFlag.TextSelectableByMouse
            )
            status_row.addWidget(label)
        status_row.addStretch(1)
        layout.addLayout(status_row)

        lane_grid = QGridLayout()
        lane_grid.setHorizontalSpacing(4)
        lane_grid.setVerticalSpacing(0)
        self.lane_checkboxes: dict[int, QCheckBox] = {}
        for position, (note, _name, label) in enumerate(DRUM_LANE_DEFS):
            checkbox = QCheckBox(label)
            checkbox.setChecked(bool(DRUM_LANE_DEFAULT_MASK & (1 << position)))
            checkbox.setEnabled(False)  # enabled by /ji/drums/state
            checkbox.toggled.connect(
                lambda checked, n=note: self._on_lane_toggled(n, checked)
            )
            self.lane_checkboxes[note] = checkbox
            lane_grid.addWidget(checkbox, position // 8, position % 8)
        layout.addLayout(lane_grid)
        return group

    def _build_groove_group(self) -> QGroupBox:
        group = QGroupBox("Groove")
        layout = QVBoxLayout(group)
        layout.setContentsMargins(4, 2, 4, 2)
        layout.setSpacing(2)

        row = QHBoxLayout()
        row.setSpacing(3)
        row.addWidget(QLabel("Preset"))
        self.groove_preset_combo = QComboBox()
        self.groove_preset_combo.addItems(list(GROOVE_PRESETS))
        try:
            self.groove_preset_combo.setCurrentIndex(
                list(GROOVE_PRESETS).index(GROOVE_DEFAULT_PRESET)
            )
        except ValueError:  # pragma: no cover - constant lists stay aligned
            self.groove_preset_combo.setCurrentIndex(0)
        self.groove_preset_combo.setFixedWidth(82)
        self.groove_preset_combo.currentIndexChanged.connect(
            self._on_groove_preset_changed
        )
        row.addWidget(self.groove_preset_combo)

        self.groove_knobs: dict[str, Knob] = {}
        for spec in GROOVE_PARAMS:
            knob = Knob(
                spec.label,
                spec.low,
                spec.high,
                spec.default,
                steps=1000,
                fmt="{:.2f}",
            )
            knob.valueChanged.connect(
                lambda value, key=spec.key: self._on_groove_knob(key, value)
            )
            self.groove_knobs[spec.key] = knob
            row.addWidget(knob)

        row.addWidget(QLabel("Seed"))
        self.groove_seed_spin = QSpinBox()
        self.groove_seed_spin.setRange(0, int(GROOVE_SEED_MAX))
        self.groove_seed_spin.setValue(int(GROOVE_DEFAULT_SEED))
        self.groove_seed_spin.setFixedWidth(90)
        self.groove_seed_spin.setToolTip(
            "Groove seed. Same preset + modifiers + seed + bar reproduce the "
            "same generated ghosts."
        )
        self.groove_seed_spin.valueChanged.connect(self._on_groove_seed_changed)
        row.addWidget(self.groove_seed_spin)

        self.show_grid_checkbox = QCheckBox("Show grid")
        self.show_grid_checkbox.setChecked(False)
        self.show_grid_checkbox.setToolTip(
            "Hidden by default to keep the window compact. Builds the 16-lane "
            "step grid lazily on first show."
        )
        self.show_grid_checkbox.toggled.connect(self._on_show_grid)
        row.addWidget(self.show_grid_checkbox)
        self.reset_grid_button = QPushButton("Reset grid")
        self.reset_grid_button.setMinimumHeight(22)
        self.reset_grid_button.clicked.connect(
            lambda: self._guard(self.controller.reset_drum_grid)
        )
        row.addWidget(self.reset_grid_button)
        row.addStretch(1)
        layout.addLayout(row)

        self.grid_scroll = QScrollArea()
        self.grid_scroll.setWidgetResizable(True)
        self.grid_scroll.setVisible(False)
        self.grid_scroll.setMaximumHeight(190)
        self.grid_container = QWidget()
        self.grid_layout = QGridLayout(self.grid_container)
        self.grid_layout.setContentsMargins(0, 0, 0, 0)
        self.grid_layout.setSpacing(0)
        self.grid_scroll.setWidget(self.grid_container)
        layout.addWidget(self.grid_scroll)
        return group

    def _build_bass_group(self) -> QGroupBox:
        """Stage 8.3a compact bass group (two rows + status line)."""

        group = QGroupBox("Bass")
        layout = QVBoxLayout(group)
        layout.setContentsMargins(4, 2, 4, 2)
        layout.setSpacing(2)

        row = QHBoxLayout()
        row.setSpacing(3)
        self.bass_enable_checkbox = QCheckBox("Bass on")
        self.bass_enable_checkbox.setChecked(BASS_DEFAULT_ENABLED)
        self.bass_enable_checkbox.toggled.connect(self._on_bass_enabled)
        row.addWidget(self.bass_enable_checkbox)

        row.addWidget(QLabel("Preset"))
        self.bass_preset_combo = QComboBox()
        self.bass_preset_combo.addItems(list(BASS_PRESET_DISPLAY))
        self.bass_preset_combo.setCurrentIndex(
            list(BASS_PRESET_DISPLAY).index(BASS_DEFAULT_PRESET)
        )
        self.bass_preset_combo.setToolTip(
            "pulse = downbeat drone bass, techno = moving walk, chaos = wide "
            "seeded line. Manual edits show custom."
        )
        self.bass_preset_combo.currentIndexChanged.connect(
            self._on_bass_preset_changed
        )
        row.addWidget(self.bass_preset_combo)

        row.addWidget(QLabel("Grid"))
        self.bass_grid_combo = QComboBox()
        self.bass_grid_combo.addItems(list(BASS_GRIDS))
        self.bass_grid_combo.setCurrentText(BASS_DEFAULT_GRID)
        self.bass_grid_combo.currentIndexChanged.connect(
            self._on_bass_grid_changed
        )
        row.addWidget(self.bass_grid_combo)

        row.addWidget(QLabel("Pitch"))
        self.bass_pitch_combo = QComboBox()
        self.bass_pitch_combo.addItems(list(BASS_PITCH_MODES))
        self.bass_pitch_combo.setCurrentText(BASS_DEFAULT_PITCH_MODE)
        self.bass_pitch_combo.currentIndexChanged.connect(
            self._on_bass_pitch_changed
        )
        row.addWidget(self.bass_pitch_combo)

        row.addWidget(QLabel("Len"))
        self.bass_length_spin = QSpinBox()
        self.bass_length_spin.setRange(BASS_LENGTH_MIN, BASS_LENGTH_MAX)
        self.bass_length_spin.setValue(16)
        self.bass_length_spin.setFixedWidth(48)
        self.bass_length_spin.setToolTip(
            "Pattern length in grid steps (1..32). The default is one bar."
        )
        self.bass_length_spin.valueChanged.connect(self._on_bass_length_changed)
        row.addWidget(self.bass_length_spin)

        self.bass_sub_checkbox = QCheckBox("Sub")
        self.bass_sub_checkbox.toggled.connect(self._on_bass_sub_enabled)
        row.addWidget(self.bass_sub_checkbox)
        self.bass_sub_octaves_spin = QSpinBox()
        self.bass_sub_octaves_spin.setRange(
            int(BASS_SUB_OCTAVES[0]), int(BASS_SUB_OCTAVES[-1])
        )
        self.bass_sub_octaves_spin.setValue(1)
        self.bass_sub_octaves_spin.setFixedWidth(40)
        self.bass_sub_octaves_spin.valueChanged.connect(
            self._on_bass_sub_octaves
        )
        row.addWidget(self.bass_sub_octaves_spin)
        row.addStretch(1)
        layout.addLayout(row)

        row2 = QHBoxLayout()
        row2.setSpacing(2)
        self.bass_knobs: dict[str, Knob] = {}
        for spec in BASS_PARAMS:
            knob = Knob(
                spec.label,
                spec.low,
                spec.high,
                spec.default,
                steps=1000,
                fmt=spec.fmt,
                suffix=spec.suffix,
            )
            knob.valueChanged.connect(
                lambda value, key=spec.key: self._on_bass_knob(key, value)
            )
            self.bass_knobs[spec.key] = knob
            row2.addWidget(knob)

        row2.addWidget(QLabel("Oct"))
        self.bass_octave_range_spin = QSpinBox()
        self.bass_octave_range_spin.setRange(
            BASS_OCTAVE_RANGE_MIN, BASS_OCTAVE_RANGE_MAX
        )
        self.bass_octave_range_spin.setValue(BASS_DEFAULT_OCTAVE_RANGE)
        self.bass_octave_range_spin.setFixedWidth(42)
        self.bass_octave_range_spin.setToolTip(
            "Allowed +/- octave offsets around the base octave (0..3)."
        )
        self.bass_octave_range_spin.valueChanged.connect(
            self._on_bass_octave_range
        )
        row2.addWidget(self.bass_octave_range_spin)

        row2.addWidget(QLabel("Base"))
        self.bass_base_octave_spin = QSpinBox()
        self.bass_base_octave_spin.setRange(
            BASS_BASE_OCTAVE_MIN, BASS_BASE_OCTAVE_MAX
        )
        self.bass_base_octave_spin.setValue(BASS_DEFAULT_BASE_OCTAVE)
        self.bass_base_octave_spin.setFixedWidth(46)
        self.bass_base_octave_spin.setToolTip(
            "Whole octaves relative to the fundamental (default -2)."
        )
        self.bass_base_octave_spin.valueChanged.connect(
            self._on_bass_base_octave
        )
        row2.addWidget(self.bass_base_octave_spin)

        row2.addWidget(QLabel("Seed"))
        self.bass_seed_spin = QSpinBox()
        self.bass_seed_spin.setRange(0, int(BASS_SEED_MAX))
        self.bass_seed_spin.setValue(int(BASS_DEFAULT_SEED))
        self.bass_seed_spin.setFixedWidth(90)
        self.bass_seed_spin.setToolTip(
            "Same set + controls + seed + bar/step reproduce the same line."
        )
        self.bass_seed_spin.valueChanged.connect(self._on_bass_seed)
        row2.addWidget(self.bass_seed_spin)
        row2.addStretch(1)
        layout.addLayout(row2)

        self.bass_label = QLabel("Bass: waiting for bridge")
        self.bass_note_label = QLabel("Last: --")
        status_row = QHBoxLayout()
        status_row.setSpacing(8)
        status_row.addWidget(self.bass_label)
        status_row.addWidget(self.bass_note_label)
        status_row.addStretch(1)
        layout.addLayout(status_row)

        self.bass_controls: list[QWidget] = [
            self.bass_enable_checkbox,
            self.bass_preset_combo,
            self.bass_grid_combo,
            self.bass_pitch_combo,
            self.bass_length_spin,
            self.bass_sub_checkbox,
            self.bass_sub_octaves_spin,
            self.bass_octave_range_spin,
            self.bass_base_octave_spin,
            self.bass_seed_spin,
            *self.bass_knobs.values(),
        ]
        return group

    def _build_arp_group(self) -> QGroupBox:
        """Stage 8.3b/c compact arp group (three rows incl. the status line)."""

        group = QGroupBox("Arp")
        layout = QVBoxLayout(group)
        layout.setContentsMargins(4, 2, 4, 2)
        layout.setSpacing(2)

        row = QHBoxLayout()
        row.setSpacing(2)
        self.arp_knobs: dict[str, Knob] = {}
        for spec in ARP_PARAMS:
            steps = (
                int(round(spec.high - spec.low))
                if spec.integer
                else 1000
            )
            knob = Knob(
                spec.label,
                spec.low,
                spec.high,
                spec.default,
                steps=steps,
                integer=spec.integer,
                fmt=spec.fmt,
                suffix=spec.suffix,
            )
            knob.valueChanged.connect(
                lambda value, key=spec.key: self._on_arp_knob(key, value)
            )
            self.arp_knobs[spec.key] = knob
            row.addWidget(knob)

        row.addWidget(QLabel("Sync"))
        self.arp_sync_combo = QComboBox()
        self.arp_sync_combo.addItems(list(ARP_SYNC_MODES))
        self.arp_sync_combo.setCurrentText(ARP_DEFAULT_SYNC)
        self.arp_sync_combo.setToolTip(
            "free = the Step knob sets the step in seconds (Stage 6/8.3b). "
            "beat = the step is the chosen division of the bridge BPM and the "
            "next step snaps to the beat."
        )
        self.arp_sync_combo.currentIndexChanged.connect(
            self._on_arp_sync_changed
        )
        row.addWidget(self.arp_sync_combo)

        row.addWidget(QLabel("Div"))
        self.arp_division_combo = QComboBox()
        self.arp_division_combo.addItems(list(ARP_DIVISIONS))
        self.arp_division_combo.setCurrentText(ARP_DEFAULT_DIVISION)
        self.arp_division_combo.setToolTip(
            "Beat-mode step: 1/4, 1/8, 1/16 or 1/8T of the bridge tempo. "
            "1/8 at 120 BPM = 0.25 s, the free-mode default."
        )
        self.arp_division_combo.currentIndexChanged.connect(
            self._on_arp_division_changed
        )
        row.addWidget(self.arp_division_combo)

        row.addWidget(QLabel("Order"))
        self.arp_order_combo = QComboBox()
        self.arp_order_combo.addItems(list(ARP_ORDERS))
        self.arp_order_combo.setCurrentText(ARP_DEFAULT_ORDER)
        self.arp_order_combo.setToolTip(
            "forward = A1 A2 A3 B1 B2 B3; reverse = backwards; pingpong = "
            "forward then back without repeating the endpoints; random = a "
            "seeded permutation of the six steps."
        )
        self.arp_order_combo.currentIndexChanged.connect(
            self._on_arp_order_changed
        )
        row.addWidget(self.arp_order_combo)

        row.addWidget(QLabel("Shared"))
        self.arp_shared_combo = QComboBox()
        self.arp_shared_combo.addItems(list(ARP_SHARED_MODES))
        self.arp_shared_combo.setToolTip(
            "anchor = use the current anchor when it is in the set, else a "
            "seeded class; random = always a seeded class."
        )
        self.arp_shared_combo.currentIndexChanged.connect(
            self._on_arp_shared_changed
        )
        row.addWidget(self.arp_shared_combo)
        row.addStretch(1)
        layout.addLayout(row)

        row2 = QHBoxLayout()
        row2.setSpacing(3)
        self.arp_evolve_checkbox = QCheckBox("Evolve")
        self.arp_evolve_checkbox.setChecked(ARP_DEFAULT_EVOLVE)
        self.arp_evolve_checkbox.setToolTip(
            "Regenerate the pattern on its own from the current set — no "
            "mutation needed. Depth 0 only re-picks the free notes; higher "
            "depth also lets the shared note and the octaves drift."
        )
        self.arp_evolve_checkbox.toggled.connect(self._on_arp_evolve_toggled)
        row2.addWidget(self.arp_evolve_checkbox)

        row2.addWidget(QLabel("Every"))
        self.arp_evolve_beats_spin = QSpinBox()
        self.arp_evolve_beats_spin.setRange(
            ARP_EVOLVE_BEATS_MIN, ARP_EVOLVE_BEATS_MAX
        )
        self.arp_evolve_beats_spin.setValue(int(ARP_DEFAULT_EVOLVE_BEATS))
        self.arp_evolve_beats_spin.setFixedWidth(48)
        self.arp_evolve_beats_spin.setSuffix(" b")
        self.arp_evolve_beats_spin.setToolTip(
            "Evolve every N quarter beats (1..64). 4 beats = one 4/4 bar."
        )
        self.arp_evolve_beats_spin.valueChanged.connect(
            self._on_arp_evolve_beats
        )
        row2.addWidget(self.arp_evolve_beats_spin)

        self.arp_depth_knob = Knob(
            ARP_EVOLVE_DEPTH_PARAM.label,
            ARP_EVOLVE_DEPTH_PARAM.low,
            ARP_EVOLVE_DEPTH_PARAM.high,
            ARP_EVOLVE_DEPTH_PARAM.default,
            steps=1000,
            fmt=ARP_EVOLVE_DEPTH_PARAM.fmt,
        )
        self.arp_depth_knob.setToolTip(
            "How much one evolution changes: 0 keeps the shared note and the "
            "octaves; 1 lets the shared note move freely and nudges an octave."
        )
        self.arp_depth_knob.valueChanged.connect(self._on_arp_depth_knob)
        row2.addWidget(self.arp_depth_knob)

        row2.addWidget(QLabel("Oct"))
        self.arp_octave_spins: list[QSpinBox] = []
        for label, default in zip(
            ("sh", "1", "2"), ARP_DEFAULT_OCTAVES
        ):
            row2.addWidget(QLabel(label))
            spin = QSpinBox()
            spin.setRange(ARP_OCTAVE_MIN, ARP_OCTAVE_MAX)
            spin.setValue(int(default))
            spin.setFixedWidth(44)
            spin.setToolTip(
                "Positional octave offsets (shared, non-shared 1, non-shared "
                "2), -8..+8. The shared offset is identical in groups A and B."
            )
            spin.valueChanged.connect(self._on_arp_octaves)
            self.arp_octave_spins.append(spin)
            row2.addWidget(spin)

        row2.addWidget(QLabel("Seed"))
        self.arp_seed_spin = QSpinBox()
        self.arp_seed_spin.setRange(0, int(ARP_SEED_MAX))
        self.arp_seed_spin.setValue(int(ARP_DEFAULT_SEED))
        self.arp_seed_spin.setFixedWidth(90)
        self.arp_seed_spin.setToolTip(
            "Same seed + same set + same settings reproduce the same pattern, "
            "the same random order, and the same evolution sequence."
        )
        self.arp_seed_spin.valueChanged.connect(self._on_arp_seed)
        row2.addWidget(self.arp_seed_spin)
        row2.addStretch(1)
        layout.addLayout(row2)

        self.arp_label = QLabel("Arp: waiting for bridge")
        layout.addWidget(self.arp_label)

        self.arp_controls: list[QWidget] = [
            self.arp_sync_combo,
            self.arp_division_combo,
            self.arp_order_combo,
            self.arp_shared_combo,
            self.arp_evolve_checkbox,
            self.arp_evolve_beats_spin,
            self.arp_depth_knob,
            self.arp_seed_spin,
            *self.arp_octave_spins,
            *self.arp_knobs.values(),
        ]
        # Nothing has arrived yet: show the waiting state with controls off.
        self._update_arp_label()
        return group

    # -- Stage 8.2 groove grid ------------------------------------------

    def _grid_steps(self) -> int:
        groove = self.store.groove
        if groove is not None:
            return max(1, min(MAX_GRID_STEPS, int(groove.grid_steps)))
        rhythm = self.store.rhythm
        if rhythm is not None:
            return grid_steps_for_meter(rhythm.meter_num, rhythm.meter_den)
        return 16

    def _ensure_grid(self, steps: int | None = None) -> None:
        """Lazily build the 16 x N step grid; safe to call repeatedly."""

        wanted = self._grid_steps() if steps is None else int(steps)
        wanted = max(1, min(MAX_GRID_STEPS, wanted))
        if self.step_cells and self._grid_built_steps == wanted:
            self._apply_grid_from_store()
            return

        self._grid_sync = True
        try:
            while self.grid_layout.count():
                item = self.grid_layout.takeAt(0)
                widget = item.widget()
                if widget is not None:
                    widget.deleteLater()
            self.step_cells.clear()

            corner = QLabel("Lane")
            corner.setFixedWidth(52)
            self.grid_layout.addWidget(corner, 0, 0)
            for step in range(wanted):
                header = QLabel(str(step + 1))
                header.setAlignment(Qt.AlignmentFlag.AlignHCenter)
                header.setFixedSize(14, 14)
                self.grid_layout.addWidget(header, 0, step + 1)

            for lane, (_note, _name, label) in enumerate(DRUM_LANE_DEFS):
                lane_label = QLabel(label)
                lane_label.setFixedWidth(52)
                lane_label.setToolTip(label)
                self.grid_layout.addWidget(lane_label, lane + 1, 0)
                for step in range(wanted):
                    cell = QPushButton()
                    cell.setCheckable(True)
                    cell.setFixedSize(14, 14)
                    cell.setStyleSheet(
                        "QPushButton{padding:0; margin:0; border:1px solid "
                        "#888888;} QPushButton:checked{background:#4a90d9;}"
                    )
                    cell.setToolTip(f"{label} step {step + 1}")
                    cell.toggled.connect(
                        lambda checked, l=lane, s=step: self._on_grid_cell(
                            l, s, checked
                        )
                    )
                    self.step_cells[(lane, step)] = cell
                    self.grid_layout.addWidget(cell, lane + 1, step + 1)
            self._grid_built_steps = wanted
        finally:
            self._grid_sync = False
        self._apply_grid_from_store()

    def _apply_grid_from_store(self) -> None:
        masks = self.store.grid
        if masks is None or not self.step_cells:
            return
        self._grid_sync = True
        try:
            for (lane, step), cell in self.step_cells.items():
                on = bool(masks[lane] & (1 << step))
                blocker = QSignalBlocker(cell)
                cell.setChecked(on)
                del blocker
        finally:
            self._grid_sync = False

    def _on_show_grid(self, checked: bool) -> None:
        self.grid_scroll.setVisible(bool(checked))
        if checked:
            self._ensure_grid()

    def _on_grid_cell(self, lane: int, step: int, checked: bool) -> None:
        if self._grid_sync:
            return
        self._guard(self.controller.set_drum_step, int(lane), int(step), bool(checked))
        state = "on" if checked else "off"
        if self._transport_running():
            self._set_message(
                f"Grid cell {lane}:{step} {state} queued for the next beat"
            )
        else:
            self._set_message(
                f"Grid cell {lane}:{step} {state} applied immediately "
                "(transport stopped)"
            )

    def _on_groove_preset_changed(self, position: int) -> None:
        if self._groove_sync:
            return
        if not 0 <= position < len(GROOVE_PRESETS):
            return
        self._guard(
            self.controller.set_drums_preset, GROOVE_PRESETS[position]
        )
        self._set_message(
            f"Groove preset {GROOVE_PRESETS[position]} queued for the next beat"
            if self._transport_running()
            else f"Groove preset {GROOVE_PRESETS[position]} applied immediately"
        )

    def _on_groove_knob(self, key: str, value: float) -> None:
        self._pending[("groove", key)] = value

    def _on_groove_seed_changed(self, value: int) -> None:
        if self._groove_sync:
            return
        self._guard(self.controller.set_drum_seed, int(value))

    def _transport_running(self) -> bool:
        rhythm = self.store.rhythm
        return rhythm is not None and bool(rhythm.running)

    def _sync_groove_controls(self) -> None:
        groove = self.store.groove
        if groove is None:
            return
        self._groove_sync = True
        try:
            if groove.preset in GROOVE_PRESETS:
                self.groove_preset_combo.setCurrentIndex(
                    list(GROOVE_PRESETS).index(groove.preset)
                )
            for spec in GROOVE_PARAMS:
                knob = self.groove_knobs.get(spec.key)
                if knob is not None:
                    knob.set_value(groove.modifiers.get(spec.key, spec.default))
            self.groove_seed_spin.setValue(int(groove.seed))
        finally:
            self._groove_sync = False
        if self.grid_scroll.isVisible():
            if self._grid_built_steps != groove.grid_steps:
                self._ensure_grid(groove.grid_steps)
            else:
                self._apply_grid_from_store()

    # -- Stage 8.3a bass controls ---------------------------------------

    def _bass_message(self, label: str) -> None:
        self._set_message(
            f"{label} queued for the next beat"
            if self._transport_running()
            else f"{label} applied immediately (transport stopped)"
        )

    def _on_bass_enabled(self, checked: bool) -> None:
        if self._bass_sync:
            return
        self._guard(self.controller.set_bass_enabled, bool(checked))
        self._bass_message(f"Bass {'on' if checked else 'off'}")

    def _on_bass_preset_changed(self, position: int) -> None:
        if self._bass_sync:
            return
        if not 0 <= position < len(BASS_PRESET_DISPLAY):
            return
        preset = BASS_PRESET_DISPLAY[position]
        if preset == BASS_PRESET_CUSTOM:
            return
        self._guard(self.controller.set_bass_preset, preset)
        self._bass_message(f"Bass preset {preset}")

    def _on_bass_grid_changed(self, position: int) -> None:
        if self._bass_sync:
            return
        if not 0 <= position < len(BASS_GRIDS):
            return
        self._guard(self.controller.set_bass_grid, BASS_GRIDS[position])
        self._bass_message(f"Bass grid {BASS_GRIDS[position]}")

    def _on_bass_pitch_changed(self, position: int) -> None:
        if self._bass_sync:
            return
        if not 0 <= position < len(BASS_PITCH_MODES):
            return
        self._guard(
            self.controller.set_bass_pitch_mode, BASS_PITCH_MODES[position]
        )
        self._bass_message(f"Bass pitch {BASS_PITCH_MODES[position]}")

    def _on_bass_length_changed(self, value: int) -> None:
        if self._bass_sync:
            return
        self._guard(self.controller.set_bass_length, int(value))
        self._bass_message(f"Bass length {int(value)}")

    def _on_bass_sub_enabled(self, checked: bool) -> None:
        if self._bass_sync:
            return
        self._guard(self.controller.set_bass_sub_enabled, bool(checked))
        self._bass_message(f"Bass sub {'on' if checked else 'off'}")

    def _on_bass_sub_octaves(self, value: int) -> None:
        if self._bass_sync:
            return
        self._guard(self.controller.set_bass_sub_octaves, int(value))
        self._bass_message(f"Bass sub octaves {int(value)}")

    def _on_bass_octave_range(self, value: int) -> None:
        if self._bass_sync:
            return
        self._guard(self.controller.set_bass_octave_range, int(value))
        self._bass_message(f"Bass octave range {int(value)}")

    def _on_bass_base_octave(self, value: int) -> None:
        if self._bass_sync:
            return
        self._guard(self.controller.set_bass_base_octave, int(value))
        self._bass_message(f"Bass base octave {int(value)}")

    def _on_bass_seed(self, value: int) -> None:
        if self._bass_sync:
            return
        self._guard(self.controller.set_bass_seed, int(value))
        self._bass_message(f"Bass seed {int(value)}")

    def _on_bass_knob(self, key: str, value: float) -> None:
        self._pending[("bass", key)] = value

    def _sync_bass_controls(self) -> None:
        bass = self.store.bass
        if bass is None:
            return
        self._bass_sync = True
        try:
            self.bass_enable_checkbox.setChecked(bool(bass.enabled))
            preset = (
                bass.preset
                if bass.preset in BASS_PRESET_DISPLAY
                else BASS_PRESET_CUSTOM
            )
            self.bass_preset_combo.setCurrentIndex(
                list(BASS_PRESET_DISPLAY).index(preset)
            )
            self.bass_grid_combo.setCurrentText(bass.grid)
            self.bass_pitch_combo.setCurrentText(bass.pitch_mode)
            self.bass_length_spin.setValue(int(bass.length_steps))
            for spec in BASS_PARAMS:
                knob = self.bass_knobs.get(spec.key)
                if knob is not None:
                    knob.set_value(getattr(bass, spec.key))
            self.bass_octave_range_spin.setValue(int(bass.octave_range))
            self.bass_base_octave_spin.setValue(int(bass.base_octave))
            self.bass_seed_spin.setValue(int(bass.seed))
            self.bass_sub_checkbox.setChecked(bool(bass.sub_enabled))
            self.bass_sub_octaves_spin.setValue(int(bass.sub_octaves))
        finally:
            self._bass_sync = False
        self._update_bass_label()

    def _update_bass_label(self) -> None:
        bass = self.store.bass
        if bass is None:
            self.bass_label.setText("Bass: waiting for bridge")
            self.bass_note_label.setText("Last: --")
            for widget in self.bass_controls:
                widget.setEnabled(False)
            return
        for widget in self.bass_controls:
            widget.setEnabled(bool(bass.port_ok))
        if not bass.port_ok:
            self.bass_label.setText(
                "Bass: disabled (port not available; see bridge log)"
            )
        else:
            self.bass_label.setText(
                f"Bass: {'on' if bass.enabled else 'off'} | {bass.preset} | "
                f"{bass.grid} | sub {'on' if bass.sub_enabled else 'off'}"
            )
        note = self.store.bass_note
        if note is None:
            self.bass_note_label.setText("Last: --")
        else:
            self.bass_note_label.setText(
                f"Last: {note.note_name} {note.hz:.2f} Hz"
            )

    # -- Stage 8.3b arp controls ----------------------------------------

    def _arp_message(self, label: str) -> None:
        self._set_message(
            f"{label} queued for the next beat"
            if self._transport_running()
            else f"{label} applied immediately (transport stopped)"
        )

    def _on_arp_knob(self, key: str, value: float) -> None:
        # Knobs coalesce at ~30 Hz through the shared pending timer.
        self._pending[("arp", key)] = value

    def _on_arp_order_changed(self, position: int) -> None:
        if self._arp_sync:
            return
        if not 0 <= position < len(ARP_ORDERS):
            return
        order = ARP_ORDERS[position]
        self._guard(self.controller.set_arp_order, order)
        self._arp_message(f"Arp order {order}")

    def _on_arp_shared_changed(self, position: int) -> None:
        if self._arp_sync:
            return
        if not 0 <= position < len(ARP_SHARED_MODES):
            return
        mode = ARP_SHARED_MODES[position]
        self._guard(self.controller.set_arp_shared_mode, mode)
        self._arp_message(f"Arp shared mode {mode}")

    def _on_arp_octaves(self, _value: int = 0) -> None:
        if self._arp_sync:
            return
        offsets = [spin.value() for spin in self.arp_octave_spins]
        self._guard(self.controller.set_arp_octaves, *offsets)
        self._arp_message(
            "Arp octaves " + ",".join(str(item) for item in offsets)
        )

    def _on_arp_sync_changed(self, position: int) -> None:
        if self._arp_sync:
            return
        if not 0 <= position < len(ARP_SYNC_MODES):
            return
        mode = ARP_SYNC_MODES[position]
        self._guard(self.controller.set_arp_sync, mode)
        self._arp_message(f"Arp sync {mode}")

    def _on_arp_division_changed(self, position: int) -> None:
        if self._arp_sync:
            return
        if not 0 <= position < len(ARP_DIVISIONS):
            return
        division = ARP_DIVISIONS[position]
        self._guard(self.controller.set_arp_division, division)
        self._arp_message(f"Arp division {division}")

    def _on_arp_evolve_toggled(self, checked: bool) -> None:
        if self._arp_sync:
            return
        self._guard(self.controller.set_arp_evolve, bool(checked))
        self._arp_message(f"Arp evolve {'on' if checked else 'off'}")

    def _on_arp_evolve_beats(self, value: int) -> None:
        if self._arp_sync:
            return
        self._guard(self.controller.set_arp_evolve_beats, int(value))
        self._arp_message(f"Arp evolve every {int(value)} beats")

    def _on_arp_depth_knob(self, value: float) -> None:
        self._pending[("arp", "evolve_depth")] = value

    def _on_arp_seed(self, value: int) -> None:
        if self._arp_sync:
            return
        self._guard(self.controller.set_arp_seed, int(value))
        self._arp_message(f"Arp seed {int(value)}")

    def _sync_arp_controls(self) -> None:
        """Reflect ``/ji/arp/state`` with signals blocked (no feedback OSC)."""

        arp = self.store.arp
        if arp is None:
            return
        self._arp_sync = True
        try:
            for spec in ARP_PARAMS:
                knob = self.arp_knobs.get(spec.key)
                if knob is not None:
                    knob.set_value(getattr(arp, spec.key), emit=False)
            order = arp.order if arp.order in ARP_ORDERS else ARP_DEFAULT_ORDER
            self.arp_order_combo.setCurrentText(order)
            sync = (
                arp.sync_mode
                if arp.sync_mode in ARP_SYNC_MODES
                else ARP_DEFAULT_SYNC
            )
            self.arp_sync_combo.setCurrentText(sync)
            division = (
                arp.division
                if arp.division in ARP_DIVISIONS
                else ARP_DEFAULT_DIVISION
            )
            self.arp_division_combo.setCurrentText(division)
            self.arp_evolve_checkbox.setChecked(bool(arp.evolve))
            self.arp_evolve_beats_spin.setValue(int(arp.evolve_beats))
            self.arp_depth_knob.set_value(arp.evolve_depth, emit=False)
            shared = (
                arp.shared_mode
                if arp.shared_mode in ARP_SHARED_MODES
                else ARP_SHARED_MODES[0]
            )
            self.arp_shared_combo.setCurrentText(shared)
            for spin, value in zip(self.arp_octave_spins, arp.octave_offsets):
                spin.setValue(int(value))
            self.arp_seed_spin.setValue(int(arp.seed))
        finally:
            self._arp_sync = False
        self._update_arp_label()

    def _update_arp_label(self) -> None:
        arp = self.store.arp
        if arp is None:
            self.arp_label.setText("Arp: waiting for bridge")
            for widget in self.arp_controls:
                widget.setEnabled(False)
            return
        available = bool(arp.port_ok)
        for widget in self.arp_controls:
            widget.setEnabled(available)
        if not available:
            self.arp_label.setText(
                "Arp: disabled (MIDI/Bus 2 not available; see bridge log)"
            )
            return
        # The step knob only matters when free-running; the division only in
        # beat mode; the evolve rate/depth only while evolving.
        self.arp_knobs["step_seconds"].setEnabled(not arp.synced)
        self.arp_division_combo.setEnabled(arp.synced)
        self.arp_evolve_beats_spin.setEnabled(bool(arp.evolve))
        self.arp_depth_knob.setEnabled(bool(arp.evolve))
        self.arp_label.setText("Arp: " + arp.status_line())

    def _sync_grid(self) -> None:
        if not self.grid_scroll.isVisible():
            return
        self._ensure_grid()
        self._apply_grid_from_store()

    def _sync_quantize(self) -> None:
        quantize = self.store.quantize
        if quantize is None:
            self._quantize_deadline = None
            self._quantize_pending = 0
        elif quantize.seconds < 0.0:
            self._quantize_deadline = None
            self._quantize_pending = int(quantize.pending)
        else:
            self._quantize_deadline = time.monotonic() + float(quantize.seconds)
            self._quantize_pending = int(quantize.pending)
        self._update_quantize_label()

    def _update_quantize_label(self) -> None:
        rhythm = self.store.rhythm
        if rhythm is not None and not rhythm.running:
            self.quantize_label.setText("Next beat: - | quantized off (immediate)")
            return
        if self.store.quantize is None:
            self.quantize_label.setText("Next beat: waiting for bridge")
            return
        if self._quantize_deadline is None:
            self.quantize_label.setText(
                f"Next beat: - | {self._quantize_pending} pending"
            )
            return
        remaining = max(0.0, self._quantize_deadline - time.monotonic())
        self.quantize_label.setText(
            f"Next beat: {remaining:.2f} s | {self._quantize_pending} pending"
        )

    # -- Stage 8.3d lead controls ----------------------------------------

    def _lead_message(self, label: str) -> None:
        self._set_message(
            f"{label} queued for the next beat"
            if self._transport_running()
            else f"{label} applied immediately (transport stopped)"
        )

    def _on_lead_enabled(self, checked: bool) -> None:
        if self._lead_sync:
            return
        self._guard(self.controller.set_lead_enabled, bool(checked))
        self._lead_message(f"Lead {'on' if checked else 'off'}")

    def _on_lead_grid_changed(self, position: int) -> None:
        if self._lead_sync:
            return
        if not 0 <= position < len(LEAD_GRIDS):
            return
        self._guard(self.controller.set_lead_setting, "grid", LEAD_GRIDS[position])
        self._lead_message(f"Lead grid {LEAD_GRIDS[position]}")

    def _on_lead_variation_changed(self, position: int) -> None:
        if self._lead_sync:
            return
        if not 0 <= position < len(LEAD_VARIATIONS):
            return
        name = LEAD_VARIATIONS[position]
        self._guard(self.controller.set_lead_setting, "variation", name)
        self._lead_message(f"Lead variation {name}")

    def _on_lead_phrase_bars(self, value: int) -> None:
        if self._lead_sync:
            return
        self._guard(self.controller.set_lead_setting, "phrase_bars", int(value))
        self._lead_message(f"Lead phrase {int(value)} bars")

    def _on_lead_vary(self) -> None:
        self._guard(self.controller.vary_lead)
        self._lead_message("Lead vary")

    def _on_lead_knob(self, key: str, value: float) -> None:
        # Knobs coalesce at ~30 Hz through the shared pending timer.
        self._pending[("lead", key)] = value

    def _on_lead_octave_range(self, value: int) -> None:
        if self._lead_sync:
            return
        self._guard(
            self.controller.set_lead_setting, "octave_range", int(value)
        )
        self._lead_message(f"Lead octave range {int(value)}")

    def _on_lead_base_octave(self, value: int) -> None:
        if self._lead_sync:
            return
        self._guard(
            self.controller.set_lead_setting, "base_octave", int(value)
        )
        self._lead_message(f"Lead base octave {int(value):+d}")

    def _on_lead_seed(self, value: int) -> None:
        if self._lead_sync:
            return
        self._guard(self.controller.set_lead_setting, "seed", int(value))
        self._lead_message(f"Lead seed {int(value)}")

    def _on_lead_variation_bars(self, value: int) -> None:
        if self._lead_sync:
            return
        self._guard(
            self.controller.set_lead_setting, "variation_bars", int(value)
        )
        self._lead_message(f"Lead vary every {int(value)} bars")

    def _on_lead_groove_link(self, checked: bool) -> None:
        if self._lead_sync:
            return
        self._guard(
            self.controller.set_lead_setting, "groove_link", bool(checked)
        )
        self._lead_message(
            f"Lead groove link {'on' if checked else 'off'}"
        )

    def _sync_lead_controls(self) -> None:
        """Reflect ``/ji/lead/state`` with signals blocked (no feedback OSC)."""

        lead = self.store.lead
        if lead is None:
            return
        self._lead_sync = True
        try:
            self.lead_enable_checkbox.setChecked(bool(lead.enabled))
            if lead.grid in LEAD_GRIDS:
                self.lead_grid_combo.setCurrentText(lead.grid)
            self.lead_phrase_spin.setValue(int(lead.phrase_bars))
            variation = (
                lead.variation
                if lead.variation in LEAD_VARIATIONS
                else LEAD_VARIATIONS[0]
            )
            self.lead_variation_combo.setCurrentText(variation)
            for spec in LEAD_PARAMS:
                knob = self.lead_knobs.get(spec.key)
                if knob is not None:
                    knob.set_value(getattr(lead, spec.key), emit=False)
            self.lead_octave_range_spin.setValue(int(lead.octave_range))
            self.lead_base_octave_spin.setValue(int(lead.base_octave))
            self.lead_seed_spin.setValue(int(lead.seed))
            self.lead_variation_bars_spin.setValue(int(lead.variation_bars))
            self.lead_groove_link_checkbox.setChecked(bool(lead.groove_link))
        finally:
            self._lead_sync = False
        self._update_lead_label()

    def _update_lead_label(self) -> None:
        lead = self.store.lead
        if lead is None:
            self.lead_label.setText("Lead: waiting for bridge")
            self.lead_note_label.setText("Last: --")
            for widget in self.lead_controls:
                widget.setEnabled(False)
            return
        available = bool(lead.port_ok)
        for widget in self.lead_controls:
            widget.setEnabled(available)
        if not available:
            self.lead_label.setText(
                "Lead: disabled (Bus 5 not available; start with --lead)"
            )
        else:
            self.lead_label.setText("Lead: " + lead.status_line())
        note = self.store.lead_note
        if note is None:
            self.lead_note_label.setText("Last: --")
        else:
            self.lead_note_label.setText(
                f"Last: {note.note_name} {note.hz:.2f} Hz"
            )

    # -- Stage 8.3d drone mode controls ----------------------------------

    def _drone_message(self, label: str) -> None:
        self._set_message(
            f"{label} queued for the next beat"
            if self._transport_running()
            else f"{label} applied immediately (transport stopped)"
        )

    def _drone_set(self, name: str, value: Any, label: str) -> None:
        if self._drone_sync:
            return
        self._guard(self.controller.set_drone_setting, name, value)
        self._drone_message(label)

    def _on_drone_mode_changed(self, position: int) -> None:
        if self._drone_sync:
            return
        if not 0 <= position < len(DRONE_MODES):
            return
        self._drone_set("mode", DRONE_MODES[position], f"Drone {DRONE_MODES[position]}")

    def _on_drone_knob(self, key: str, value: float) -> None:
        self._pending[("drone", key)] = value

    def _on_drone_pulse_notes(self, value: int) -> None:
        self._drone_set("pulse_notes", int(value), f"Drone notes {int(value)}")

    def _on_drone_pulse_grid(self, position: int) -> None:
        if not 0 <= position < len(DRONE_PULSE_GRIDS):
            return
        grid = DRONE_PULSE_GRIDS[position]
        self._drone_set("pulse_grid", grid, f"Drone grid {grid}")

    def _on_drone_pulse_span(self, value: int) -> None:
        self._drone_set("pulse_octave_span", int(value), f"Drone span {int(value)}")

    def _on_drone_seq_notes(self, value: int) -> None:
        self._drone_set(
            "seq_notes", int(value), f"Drone sequence {int(value)} notes"
        )

    def _on_drone_seq_bars(self, position: int) -> None:
        if not 0 <= position < len(DRONE_SEQ_BARS):
            return
        bars = DRONE_SEQ_BARS[position]
        unit = "bar" if position <= 2 else "bars"
        self._drone_set(
            "seq_bars", bars, f"Drone sequence {bars} {unit} per note"
        )

    def _on_drone_slide_direction(self, position: int) -> None:
        if not 0 <= position < len(DRONE_SLIDE_DIRECTIONS):
            return
        direction = DRONE_SLIDE_DIRECTIONS[position]
        self._drone_set(
            "slide_direction", direction, f"Drone slide {direction}"
        )

    def _on_drone_slide_semitones(self, value: int) -> None:
        self._drone_set(
            "slide_semitones", int(value), f"Drone slide {int(value)} st"
        )

    def _on_drone_hold_beats(self, value: float) -> None:
        self._drone_set(
            "hold_beats", float(value), f"Drone hold {float(value):.2f} beats"
        )

    def _sync_drone_controls(self) -> None:
        """Reflect ``/ji/drone/state`` with signals blocked."""

        drone = self.store.drone
        if drone is None:
            return
        self._drone_sync = True
        try:
            if drone.mode in DRONE_MODES:
                self.drone_mode_combo.setCurrentText(drone.mode)
            self.drone_velocity_knob.set_value(drone.velocity, emit=False)
            self.drone_pulse_notes_spin.setValue(int(drone.pulse_notes))
            if drone.pulse_grid in DRONE_PULSE_GRIDS:
                self.drone_pulse_grid_combo.setCurrentText(drone.pulse_grid)
            self.drone_pulse_gate_knob.set_value(drone.pulse_gate, emit=False)
            self.drone_pulse_span_spin.setValue(int(drone.pulse_octave_span))
            if drone.slide_direction in DRONE_SLIDE_DIRECTIONS:
                self.drone_direction_combo.setCurrentText(drone.slide_direction)
            self.drone_semitones_spin.setValue(int(drone.slide_semitones))
            self.drone_slide_knob.set_value(drone.slide_seconds, emit=False)
            self.drone_hold_spin.setValue(float(drone.hold_beats))
            self.drone_seq_notes_spin.setValue(int(drone.seq_notes))
            if drone.seq_bars in DRONE_SEQ_BARS:
                self.drone_seq_bars_combo.setCurrentText(drone.seq_bars)
            self.drone_seq_gate_knob.set_value(drone.seq_gate, emit=False)
        finally:
            self._drone_sync = False
        self._update_drone_label()

    def _update_drone_label(self) -> None:
        drone = self.store.drone
        if drone is None:
            self.drone_label.setText("Drone: waiting for bridge")
            for widget in self.drone_controls:
                widget.setEnabled(False)
            return
        available = bool(drone.port_ok)
        for widget in self.drone_controls:
            widget.setEnabled(available)
        if not available:
            self.drone_label.setText(
                "Drone: disabled (Bus 1 not available; see bridge log)"
            )
            return
        anchor_label = None
        freqset = self.store.freqset
        if freqset is not None and freqset.anchor is not None:
            anchor_label = _ratio_label(freqset.anchor)
        text = "Drone: " + drone.status_line(anchor_label)
        # pulse/bend/sequence are transport-gated: say so instead of going
        # silent with no explanation.
        if drone.playing:
            text += " | playing"
        elif drone.gated:
            rhythm = self.store.rhythm
            if rhythm is not None and not rhythm.running:
                text += " | waiting for transport (press Play)"
            else:
                text += " | armed"
        self.drone_label.setText(text)
        # Only the controls that matter for the current mode stay live.
        self.drone_pulse_row.setEnabled(available and drone.mode == "pulse")
        self.drone_bend_row.setEnabled(available and drone.mode == "bend")
        self.drone_sequence_row.setEnabled(
            available and drone.mode == "sequence"
        )

    # -- Stage 8.4 home controls ------------------------------------------

    def _home_slot(self) -> int:
        return int(self.home_slot_combo.currentIndex())

    def _home_message(self, label: str, action: Callable[..., Any], *args: Any) -> None:
        """Send one home action; report the queue rule in the Status line."""

        running = self._transport_running()
        self._guard(action, *args)
        self._set_message(
            f"{label} queued for the next beat"
            if running
            else f"{label} applied immediately"
        )

    def _on_home_slot_changed(self, _index: int) -> None:
        if self._home_sync:
            return
        self._sync_home_controls()

    def _on_home_set(self) -> None:
        self._home_message(
            "Set home", self.controller.home_capture, self._home_slot()
        )

    def _on_home_go(self) -> None:
        self._home_message("Go", self.controller.home_go, self._home_slot())

    def _on_home_clear(self) -> None:
        self._home_message(
            "Clear", self.controller.home_clear, self._home_slot()
        )

    def _on_home_return(self) -> None:
        self._home_message("Return", self.controller.home_return)

    def _on_home_back(self) -> None:
        self._home_message("Back", self.controller.home_back)

    def _on_home_jump(self) -> None:
        self._home_message("Jump", self.controller.home_jump)

    def _on_home_auto(self, checked: bool) -> None:
        if self._home_sync:
            return
        self._guard(
            self.controller.set_home_setting, "auto", bool(checked)
        )
        self._set_message(
            "Auto return on" if checked else "Auto return off"
        )

    def _on_home_every(self, value: int) -> None:
        if self._home_sync:
            return
        self._pending[("home", "every")] = int(value)

    def _on_home_drift(self, value: int) -> None:
        if self._home_sync:
            return
        self._pending[("home", "max_drift")] = int(value)

    def _on_home_seed(self, value: int) -> None:
        if self._home_sync:
            return
        self._pending[("home", "seed")] = int(value)

    def _sync_home_controls(self) -> None:
        """Reflect ``/ji/home/state`` with signals blocked (no feedback OSC)."""

        home = self.store.home
        has_home = bool(home.has_home) if home is not None else False
        distance = home.distance if home is not None else 0
        returning = bool(home.returning) if home is not None else False

        if home is not None and home.returning:
            # The payload carries the pending count; the first state of a
            # return arrives after batch 0, so total = pending + 1.
            if (
                self._home_return_total is None
                or home.pending_batches + 1 > self._home_return_total
            ):
                self._home_return_total = home.pending_batches + 1
        else:
            self._home_return_total = None

        self._home_sync = True
        try:
            if home is not None:
                # Follow the bridge's active slot only when it actually moved,
                # so a manual Slot selection is not snapped back on every
                # incoming state.
                if home.active_slot != self._home_active_slot:
                    self._home_active_slot = home.active_slot
                    if 0 <= home.active_slot < HOME_SLOTS:
                        self.home_slot_combo.setCurrentIndex(home.active_slot)
                self.home_auto_checkbox.setChecked(bool(home.auto))
                self.home_every_spin.setValue(int(home.every))
                self.home_drift_spin.setValue(int(home.drift))
                self.home_seed_spin.setValue(int(home.seed))
            slot = self._home_slot()
            slot_captured = (
                home.slot_captured(slot) if home is not None else False
            )
            self.home_set_button.setEnabled(True)
            self.home_clear_button.setEnabled(slot_captured)
            self.home_go_button.setEnabled(
                bool(slot_captured and not returning)
            )
            self.home_return_button.setEnabled(
                bool(has_home and distance > 0 and not returning)
            )
            self.home_back_button.setEnabled(
                bool(
                    home is not None
                    and home.history_depth > 1
                    and not returning
                )
            )
            self.home_jump_button.setEnabled(
                bool(has_home and distance > 0)
            )
            self.home_auto_checkbox.setEnabled(has_home)
        finally:
            self._home_sync = False
        self._update_home_label()

    def _update_home_label(self) -> None:
        home = self.store.home
        if home is None:
            self.home_label.setText("Home: waiting for bridge")
            return
        self.home_label.setText(home.status_line(self._home_return_total))

    def _build_lead_group(self) -> QGroupBox:
        """Stage 8.3d compact lead group (two rows + status line)."""

        group = QGroupBox("Lead")
        layout = QVBoxLayout(group)
        layout.setContentsMargins(4, 2, 4, 2)
        layout.setSpacing(2)

        # QComboBox.addItems selects index 0 and emits currentIndexChanged;
        # build silently so construction never sends control OSC.
        self._lead_sync = True
        row = QHBoxLayout()
        row.setSpacing(3)
        self.lead_enable_checkbox = QCheckBox("Lead on")
        self.lead_enable_checkbox.toggled.connect(self._on_lead_enabled)
        row.addWidget(self.lead_enable_checkbox)

        row.addWidget(QLabel("Grid"))
        self.lead_grid_combo = QComboBox()
        self.lead_grid_combo.addItems(list(LEAD_GRIDS))
        self.lead_grid_combo.setCurrentText(LEAD_DEFAULT_GRID)
        self.lead_grid_combo.setToolTip(
            "Phrase resolution: 1/4, 1/8, 1/16 or 1/8T of the bridge tempo."
        )
        self.lead_grid_combo.currentIndexChanged.connect(
            self._on_lead_grid_changed
        )
        row.addWidget(self.lead_grid_combo)

        row.addWidget(QLabel("Variation"))
        self.lead_variation_combo = QComboBox()
        self.lead_variation_combo.addItems(list(LEAD_VARIATIONS))
        self.lead_variation_combo.setCurrentText(LEAD_DEFAULT_VARIATION)
        self.lead_variation_combo.setToolTip(
            "identity = as generated; transpose/invert/retrograde/thin/"
            "ornament/rhythm transform the current line; random = a fresh "
            "seeded line. Seeded by (seed, variation index)."
        )
        self.lead_variation_combo.currentIndexChanged.connect(
            self._on_lead_variation_changed
        )
        row.addWidget(self.lead_variation_combo)

        row.addWidget(QLabel("Phrase"))
        self.lead_phrase_spin = QSpinBox()
        self.lead_phrase_spin.setRange(LEAD_PHRASE_BARS_MIN, LEAD_PHRASE_BARS_MAX)
        self.lead_phrase_spin.setValue(LEAD_DEFAULT_PHRASE_BARS)
        self.lead_phrase_spin.setFixedWidth(46)
        self.lead_phrase_spin.setSuffix(" b")
        self.lead_phrase_spin.setToolTip("Motif length in bars (1..8).")
        self.lead_phrase_spin.valueChanged.connect(self._on_lead_phrase_bars)
        row.addWidget(self.lead_phrase_spin)

        self.lead_vary_button = QPushButton("Vary")
        self.lead_vary_button.setMinimumHeight(24)
        self.lead_vary_button.setMinimumWidth(56)
        self.lead_vary_button.setToolTip(
            "Advance one variation: next quarter beat while the transport "
            "runs, immediate while it is stopped."
        )
        self.lead_vary_button.clicked.connect(self._on_lead_vary)
        row.addWidget(self.lead_vary_button)
        row.addStretch(1)
        layout.addLayout(row)

        row2 = QHBoxLayout()
        row2.setSpacing(2)
        self.lead_knobs: dict[str, Knob] = {}
        for spec in LEAD_PARAMS:
            knob = Knob(
                spec.label,
                spec.low,
                spec.high,
                spec.default,
                steps=1000,
                integer=spec.integer,
                fmt=spec.fmt,
                suffix=spec.suffix,
            )
            knob.valueChanged.connect(
                lambda value, key=spec.key: self._on_lead_knob(key, value)
            )
            self.lead_knobs[spec.key] = knob
            row2.addWidget(knob)

        self.lead_groove_link_checkbox = QCheckBox("Groove link")
        self.lead_groove_link_checkbox.setChecked(True)
        self.lead_groove_link_checkbox.setToolTip(
            "On: the lead uses the drum groove's current swing and moves with "
            "it. Off: the lead's own Swing knob."
        )
        self.lead_groove_link_checkbox.toggled.connect(
            self._on_lead_groove_link
        )
        row2.addWidget(self.lead_groove_link_checkbox)

        row2.addWidget(QLabel("Oct"))
        self.lead_octave_range_spin = QSpinBox()
        self.lead_octave_range_spin.setRange(
            LEAD_OCTAVE_RANGE_MIN, LEAD_OCTAVE_RANGE_MAX
        )
        self.lead_octave_range_spin.setValue(LEAD_DEFAULT_OCTAVE_RANGE)
        self.lead_octave_range_spin.setFixedWidth(42)
        self.lead_octave_range_spin.setToolTip(
            "How far the line may travel in octaves (0..2)."
        )
        self.lead_octave_range_spin.valueChanged.connect(
            self._on_lead_octave_range
        )
        row2.addWidget(self.lead_octave_range_spin)

        row2.addWidget(QLabel("Base"))
        self.lead_base_octave_spin = QSpinBox()
        self.lead_base_octave_spin.setRange(
            LEAD_BASE_OCTAVE_MIN, LEAD_BASE_OCTAVE_MAX
        )
        self.lead_base_octave_spin.setValue(LEAD_DEFAULT_BASE_OCTAVE)
        self.lead_base_octave_spin.setFixedWidth(46)
        self.lead_base_octave_spin.setToolTip(
            "Whole octaves relative to the fundamental (-2..+2)."
        )
        self.lead_base_octave_spin.valueChanged.connect(
            self._on_lead_base_octave
        )
        row2.addWidget(self.lead_base_octave_spin)

        row2.addWidget(QLabel("Var every"))
        self.lead_variation_bars_spin = QSpinBox()
        self.lead_variation_bars_spin.setRange(
            LEAD_VARIATION_BARS_MIN, LEAD_VARIATION_BARS_MAX
        )
        self.lead_variation_bars_spin.setValue(LEAD_DEFAULT_VARIATION_BARS)
        self.lead_variation_bars_spin.setFixedWidth(48)
        self.lead_variation_bars_spin.setSuffix(" b")
        self.lead_variation_bars_spin.setToolTip(
            "Apply the next variation every N bars while the transport runs "
            "(0 = manual only)."
        )
        self.lead_variation_bars_spin.valueChanged.connect(
            self._on_lead_variation_bars
        )
        row2.addWidget(self.lead_variation_bars_spin)

        row2.addWidget(QLabel("Seed"))
        self.lead_seed_spin = QSpinBox()
        self.lead_seed_spin.setRange(0, int(LEAD_SEED_MAX))
        self.lead_seed_spin.setValue(int(LEAD_DEFAULT_SEED))
        self.lead_seed_spin.setFixedWidth(90)
        self.lead_seed_spin.setToolTip(
            "Same seed + same settings reproduce the same phrase and the same "
            "variation sequence."
        )
        self.lead_seed_spin.valueChanged.connect(self._on_lead_seed)
        row2.addWidget(self.lead_seed_spin)
        row2.addStretch(1)
        layout.addLayout(row2)

        self.lead_label = QLabel("Lead: waiting for bridge")
        self.lead_note_label = QLabel("Last: --")
        status_row = QHBoxLayout()
        status_row.setSpacing(8)
        status_row.addWidget(self.lead_label)
        status_row.addWidget(self.lead_note_label)
        status_row.addStretch(1)
        layout.addLayout(status_row)

        self.lead_controls: list[QWidget] = [
            self.lead_enable_checkbox,
            self.lead_grid_combo,
            self.lead_variation_combo,
            self.lead_phrase_spin,
            self.lead_vary_button,
            self.lead_groove_link_checkbox,
            self.lead_octave_range_spin,
            self.lead_base_octave_spin,
            self.lead_variation_bars_spin,
            self.lead_seed_spin,
            *self.lead_knobs.values(),
        ]
        self._lead_sync = False
        self._update_lead_label()
        return group

    def _build_drone_group(self) -> QGroupBox:
        """Stage 8.3d compact drone mode group (mode + pulse + bend rows)."""

        group = QGroupBox("Drone modes")
        layout = QVBoxLayout(group)
        layout.setContentsMargins(4, 2, 4, 2)
        layout.setSpacing(2)

        # As above: no control OSC while the widgets are being built.
        self._drone_sync = True
        top = QHBoxLayout()
        top.setSpacing(3)
        top.addWidget(QLabel("Mode"))
        self.drone_mode_combo = QComboBox()
        self.drone_mode_combo.addItems(list(DRONE_MODES))
        self.drone_mode_combo.setCurrentText(DRONE_DEFAULT_MODE)
        self.drone_mode_combo.setToolTip(
            "hold = the Stage 6 sustained anchor (default). pulse = a repeating "
            "N-note pattern on the anchor pitch class. sequence = a repeating "
            "figure that climbs the current set from the anchor and comes back "
            "(harmonic movement). bend = hold, then slide the pitch with one "
            "continuous bend."
        )
        self.drone_mode_combo.currentIndexChanged.connect(
            self._on_drone_mode_changed
        )
        top.addWidget(self.drone_mode_combo)

        velocity = Knob(
            DRONE_VELOCITY_PARAM.label,
            DRONE_VELOCITY_PARAM.low,
            DRONE_VELOCITY_PARAM.high,
            DRONE_VELOCITY_PARAM.default,
            steps=int(
                DRONE_VELOCITY_PARAM.high - DRONE_VELOCITY_PARAM.low
            ),
            integer=True,
            fmt=DRONE_VELOCITY_PARAM.fmt,
        )
        velocity.setToolTip("Drone note-on velocity (1..127).")
        velocity.valueChanged.connect(
            lambda value: self._on_drone_knob("velocity", value)
        )
        self.drone_velocity_knob = velocity
        top.addWidget(velocity)
        top.addStretch(1)
        layout.addLayout(top)

        pulse_row = QWidget()
        pulse = QHBoxLayout(pulse_row)
        pulse.setContentsMargins(0, 0, 0, 0)
        pulse.setSpacing(3)
        pulse.addWidget(QLabel("Notes"))
        self.drone_pulse_notes_spin = QSpinBox()
        self.drone_pulse_notes_spin.setRange(
            DRONE_PULSE_NOTES_MIN, DRONE_PULSE_NOTES_MAX
        )
        self.drone_pulse_notes_spin.setValue(DRONE_DEFAULT_PULSE_NOTES)
        self.drone_pulse_notes_spin.setFixedWidth(42)
        self.drone_pulse_notes_spin.setToolTip("Pulse pattern length (2..8).")
        self.drone_pulse_notes_spin.valueChanged.connect(
            self._on_drone_pulse_notes
        )
        pulse.addWidget(self.drone_pulse_notes_spin)

        pulse.addWidget(QLabel("Grid"))
        self.drone_pulse_grid_combo = QComboBox()
        self.drone_pulse_grid_combo.addItems(list(DRONE_PULSE_GRIDS))
        self.drone_pulse_grid_combo.setCurrentText(DRONE_DEFAULT_PULSE_GRID)
        self.drone_pulse_grid_combo.setToolTip(
            "Pulse rhythm: 1/4, 1/8 or 1/16 of the bridge tempo."
        )
        self.drone_pulse_grid_combo.currentIndexChanged.connect(
            self._on_drone_pulse_grid
        )
        pulse.addWidget(self.drone_pulse_grid_combo)

        gate = Knob(
            DRONE_PULSE_GATE_PARAM.label,
            DRONE_PULSE_GATE_PARAM.low,
            DRONE_PULSE_GATE_PARAM.high,
            DRONE_PULSE_GATE_PARAM.default,
            steps=1000,
            fmt=DRONE_PULSE_GATE_PARAM.fmt,
            suffix=DRONE_PULSE_GATE_PARAM.suffix,
        )
        gate.setToolTip("Pulse gate in beats (clamped to <= the grid step).")
        gate.valueChanged.connect(
            lambda value: self._on_drone_knob("pulse_gate", value)
        )
        self.drone_pulse_gate_knob = gate
        pulse.addWidget(gate)

        pulse.addWidget(QLabel("Span"))
        self.drone_pulse_span_spin = QSpinBox()
        self.drone_pulse_span_spin.setRange(
            DRONE_PULSE_SPAN_MIN, DRONE_PULSE_SPAN_MAX
        )
        self.drone_pulse_span_spin.setValue(DRONE_DEFAULT_PULSE_SPAN)
        self.drone_pulse_span_spin.setFixedWidth(42)
        self.drone_pulse_span_spin.setToolTip(
            "Pulse octave span: +/- octaves around the anchor (0..2)."
        )
        self.drone_pulse_span_spin.valueChanged.connect(
            self._on_drone_pulse_span
        )
        pulse.addWidget(self.drone_pulse_span_spin)
        pulse.addStretch(1)
        self.drone_pulse_row = pulse_row
        layout.addWidget(pulse_row)

        bend_row = QWidget()
        bend = QHBoxLayout(bend_row)
        bend.setContentsMargins(0, 0, 0, 0)
        bend.setSpacing(3)
        bend.addWidget(QLabel("Dir"))
        self.drone_direction_combo = QComboBox()
        self.drone_direction_combo.addItems(list(DRONE_SLIDE_DIRECTIONS))
        self.drone_direction_combo.setCurrentText(DRONE_DEFAULT_SLIDE_DIRECTION)
        self.drone_direction_combo.setToolTip(
            "Slide down or up at the end of the hold."
        )
        self.drone_direction_combo.currentIndexChanged.connect(
            self._on_drone_slide_direction
        )
        bend.addWidget(self.drone_direction_combo)

        bend.addWidget(QLabel("Semis"))
        self.drone_semitones_spin = QSpinBox()
        self.drone_semitones_spin.setRange(
            DRONE_SLIDE_SEMITONES_MIN, DRONE_SLIDE_SEMITONES_MAX
        )
        self.drone_semitones_spin.setValue(DRONE_DEFAULT_SLIDE_SEMITONES)
        self.drone_semitones_spin.setFixedWidth(46)
        self.drone_semitones_spin.setSuffix(" st")
        self.drone_semitones_spin.setToolTip(
            "Slide size in semitones (1..12; the MPE bend range is 48 st)."
        )
        self.drone_semitones_spin.valueChanged.connect(
            self._on_drone_slide_semitones
        )
        bend.addWidget(self.drone_semitones_spin)

        slide = Knob(
            DRONE_SLIDE_PARAM.label,
            DRONE_SLIDE_PARAM.low,
            DRONE_SLIDE_PARAM.high,
            DRONE_SLIDE_PARAM.default,
            steps=1000,
            fmt=DRONE_SLIDE_PARAM.fmt,
            suffix=DRONE_SLIDE_PARAM.suffix,
        )
        slide.setToolTip("Slide duration in seconds (0.05..4.0).")
        slide.valueChanged.connect(
            lambda value: self._on_drone_knob("slide_seconds", value)
        )
        self.drone_slide_knob = slide
        bend.addWidget(slide)

        bend.addWidget(QLabel("Hold"))
        self.drone_hold_spin = QDoubleSpinBox()
        self.drone_hold_spin.setRange(DRONE_HOLD_BEATS_MIN, DRONE_HOLD_BEATS_MAX)
        self.drone_hold_spin.setSingleStep(0.25)
        self.drone_hold_spin.setDecimals(2)
        self.drone_hold_spin.setValue(DRONE_DEFAULT_HOLD_BEATS)
        self.drone_hold_spin.setFixedWidth(64)
        self.drone_hold_spin.setSuffix(" b")
        self.drone_hold_spin.setToolTip(
            "Hold before the slide, in quarter beats. The cycle length is "
            "derived (hold + slide, rounded up to the next beat)."
        )
        self.drone_hold_spin.valueChanged.connect(self._on_drone_hold_beats)
        bend.addWidget(self.drone_hold_spin)
        bend.addStretch(1)
        self.drone_bend_row = bend_row
        layout.addWidget(bend_row)

        sequence_row = QWidget()
        sequence = QHBoxLayout(sequence_row)
        sequence.setContentsMargins(0, 0, 0, 0)
        sequence.setSpacing(3)
        sequence.addWidget(QLabel("Seq notes"))
        self.drone_seq_notes_spin = QSpinBox()
        self.drone_seq_notes_spin.setRange(
            DRONE_SEQ_NOTES_MIN, DRONE_SEQ_NOTES_MAX
        )
        self.drone_seq_notes_spin.setValue(DRONE_DEFAULT_SEQ_NOTES)
        self.drone_seq_notes_spin.setFixedWidth(42)
        self.drone_seq_notes_spin.setToolTip(
            "How many notes in the repeating figure (2..8). It climbs the "
            "current set from the anchor and comes back to it; each note is "
            "held for the Length below."
        )
        self.drone_seq_notes_spin.valueChanged.connect(
            self._on_drone_seq_notes
        )
        sequence.addWidget(self.drone_seq_notes_spin)

        sequence.addWidget(QLabel("Length"))
        self.drone_seq_bars_combo = QComboBox()
        self.drone_seq_bars_combo.addItems(list(DRONE_SEQ_BARS))
        self.drone_seq_bars_combo.setCurrentText(DRONE_DEFAULT_SEQ_BARS)
        self.drone_seq_bars_combo.setToolTip(
            "How long each note is held: 1/4, 1/2, 1, 2 or 4 bars. One bar or "
            "more makes the figure a slowly moving pad."
        )
        self.drone_seq_bars_combo.currentIndexChanged.connect(
            self._on_drone_seq_bars
        )
        sequence.addWidget(self.drone_seq_bars_combo)

        seq_gate = Knob(
            DRONE_SEQ_GATE_PARAM.label,
            DRONE_SEQ_GATE_PARAM.low,
            DRONE_SEQ_GATE_PARAM.high,
            DRONE_SEQ_GATE_PARAM.default,
            steps=1000,
            fmt=DRONE_SEQ_GATE_PARAM.fmt,
            suffix=DRONE_SEQ_GATE_PARAM.suffix,
        )
        seq_gate.setToolTip(
            "How much of each note sounds: 100% is legato (a tiny gap before "
            "the next note), 50% leaves a rest."
        )
        seq_gate.valueChanged.connect(
            lambda value: self._on_drone_knob("seq_gate", value)
        )
        self.drone_seq_gate_knob = seq_gate
        sequence.addWidget(seq_gate)
        sequence.addStretch(1)
        self.drone_sequence_row = sequence_row
        layout.addWidget(sequence_row)

        self.drone_label = QLabel("Drone: waiting for bridge")
        layout.addWidget(self.drone_label)

        self.drone_controls: list[QWidget] = [
            self.drone_mode_combo,
            self.drone_velocity_knob,
            self.drone_pulse_row,
            self.drone_bend_row,
            self.drone_sequence_row,
        ]
        self._drone_sync = False
        self._update_drone_label()
        return group

    def _build_status_group(self) -> QGroupBox:
        group = QGroupBox("Status")
        grid = QGridLayout(group)
        grid.setContentsMargins(4, 2, 4, 2)
        grid.setHorizontalSpacing(8)
        grid.setVerticalSpacing(1)

        fixed = QFont("Monospace")
        fixed.setStyleHint(QFont.StyleHint.TypeWriter)
        fixed.setPointSize(8)

        self.bridge_status_label = QLabel("BRIDGE: waiting for bridge")
        self.sc_status_label = QLabel("SCLANG: waiting for sclang")
        self.freqset_label = QLabel("Set: waiting for bridge")
        self.anchor_label = QLabel("Anchor: waiting for bridge")
        self.fundamental_label = QLabel("Fundamental: waiting for bridge")
        self.control_label = QLabel("Controls: waiting for bridge")
        self.cpu_label = QLabel("CPU: waiting for sclang")
        self.loop_status_label = QLabel("Loop states: waiting for sclang")
        self.message_label = QLabel("")
        self.message_label.setWordWrap(True)

        for label in (
            self.bridge_status_label,
            self.sc_status_label,
            self.freqset_label,
            self.anchor_label,
            self.fundamental_label,
            self.control_label,
            self.cpu_label,
            self.loop_status_label,
            self.message_label,
        ):
            label.setFont(fixed)
            label.setTextInteractionFlags(
                Qt.TextInteractionFlag.TextSelectableByMouse
            )

        self.freqset_label.setWordWrap(True)
        self.control_label.setWordWrap(True)

        grid.addWidget(self.bridge_status_label, 0, 0)
        grid.addWidget(self.sc_status_label, 0, 1)
        grid.addWidget(self.cpu_label, 0, 2)
        grid.addWidget(self.loop_status_label, 0, 3)
        grid.addWidget(self.freqset_label, 1, 0, 1, 4)
        grid.addWidget(self.anchor_label, 2, 0)
        grid.addWidget(self.fundamental_label, 2, 1)
        grid.addWidget(self.control_label, 2, 2, 1, 2)
        grid.addWidget(self.message_label, 3, 0, 1, 4)
        return group

    # -- knob queueing ---------------------------------------------------

    def _on_mutation_knob(self, key: str, value: float) -> None:
        self._pending[("mutation", key)] = value

    def _on_autotune_knob(self, channel: int, key: str, value: float) -> None:
        channels = AUTOTUNE_CHANNELS if self.link_checkbox.isChecked() else (channel,)
        for target in channels:
            self._pending[("autotune", target, key)] = value

    def _displayed_length(self) -> float:
        """The default free length shown by the spin box, in seconds."""

        return round(self.length_spin.value(), 1)

    def _on_length_changed(self, value: float) -> None:
        # The spin box shows one decimal; send exactly that value.  Free-mode
        # loops that the user has not customised follow this default, which
        # keeps the Stage 7 global-length behavior.
        seconds = round(value, 1)
        self._pending[("length",)] = seconds
        for index in range(1, 5):
            if index in self._loop_custom:
                continue
            config = self.store.loop_configs.get(index)
            if config is None:
                continue
            self.store.loop_configs[index] = osc_control.LoopConfig(
                mode=config.mode, beats=config.beats, seconds=seconds
            )
            if config.mode == "free":
                spin = self.loop_length_spins.get(index)
                if spin is not None:
                    was_sync = self._loop_sync
                    self._loop_sync = True
                    try:
                        spin.setValue(seconds)
                    finally:
                        self._loop_sync = was_sync

    # -- Stage 8.1 rhythm / drum / loop controls -----------------------

    def _on_bpm_changed(self, value: int) -> None:
        self._pending[("bpm",)] = float(round(value))

    def _on_transport_toggled(self, checked: bool) -> None:
        self.transport_button.setText("Stop" if checked else "Play")
        if self._rhythm_sync:
            return
        self._guard(self.controller.set_transport, bool(checked))

    def _on_meter_changed(self, _value: Any = None) -> None:
        if self._rhythm_sync:
            return
        self._guard(
            self.controller.set_meter,
            int(self.meter_num_spin.value()),
            int(self.meter_den_combo.currentText()),
        )

    def _on_drum_enabled_toggled(self, checked: bool) -> None:
        if self._drum_sync:
            return
        self._guard(self.controller.set_drums_enabled, bool(checked))
        if self._transport_running():
            self._set_message("Drums on/off queued for the next beat")

    def _on_drum_rate_changed(self, position: int) -> None:
        if self._drum_sync:
            return
        if 0 <= position < len(DRUM_RATES):
            self._guard(self.controller.set_drum_rate, DRUM_RATES[position])
            if self._transport_running():
                self._set_message("Drum rate queued for the next beat")

    def _on_lane_toggled(self, note: int, checked: bool) -> None:
        if self._drum_sync:
            return
        self._guard(self.controller.set_drum_lane, int(note), bool(checked))
        if self._transport_running():
            self._set_message("Drum lane queued for the next beat")

    def _loop_mode(self, index: int) -> str:
        combo = self.loop_mode_combos[index]
        return "beat" if combo.currentIndex() == 1 else "free"

    def _on_loop_mode_changed(self, index: int) -> None:
        if self._loop_sync:
            return
        mode = self._loop_mode(index)
        self._guard(self.controller.loop_set_mode, index, mode)
        self._configure_loop_length(index, mode)
        self._refresh_loop_label(index)

    def _configure_loop_length(self, index: int, mode: str) -> None:
        """Switch the per-loop length control between seconds and beats."""

        spin = self.loop_length_spins[index]
        config = self.store.loop_configs.get(
            index, osc_control.LoopConfig()
        )
        was_sync = self._loop_sync
        self._loop_sync = True
        try:
            if mode == "beat":
                spin.setDecimals(0)
                spin.setRange(LOOP_BEATS_MIN, LOOP_BEATS_MAX)
                spin.setSingleStep(1.0)
                spin.setSuffix(" b")
                spin.setValue(float(round(config.beats)))
            else:
                spin.setDecimals(1)
                spin.setRange(LOOP_LENGTH_MIN, LOOP_LENGTH_MAX)
                spin.setSingleStep(0.5)
                spin.setSuffix(" s")
                spin.setValue(float(config.seconds))
        finally:
            self._loop_sync = was_sync

    def _on_loop_length_changed(self, index: int, value: float) -> None:
        if self._loop_sync:
            return
        if self._loop_mode(index) == "beat":
            self._pending[("loop_beats", index)] = float(round(value))
        else:
            self._loop_custom.add(index)
            self._pending[("loop_length", index)] = round(float(value), 1)

    def _loop_transport_ready(self, index: int) -> bool:
        """Reject beat-mode record/play while the transport is stopped."""

        if self._loop_mode(index) != "beat":
            return True
        rhythm = self.store.rhythm
        if rhythm is not None and not rhythm.running:
            self._set_message(
                f"Loop {index} is beat-locked; press Play (transport) first"
            )
            return False
        return True

    def _record_loop(self, index: int) -> None:
        if not self._loop_transport_ready(index):
            return
        if self._loop_mode(index) == "beat":
            # sclang computes beats * 60 / bpm at record time.
            self._guard(self.controller.loop_record, index)
            self._set_message(
                f"Loop {index} record queued for the next beat"
            )
            return
        # Free mode keeps the Stage 7 behavior but uses this loop's seconds.
        config = self.store.loop_configs.get(index)
        seconds = (
            config.seconds if config is not None else self._displayed_length()
        )
        self._guard(self.controller.loop_record, index, seconds)

    def _play_loop(self, index: int) -> None:
        if not self._loop_transport_ready(index):
            return
        self._guard(self.controller.loop_play, index)
        if self._loop_mode(index) == "beat":
            self._set_message(f"Loop {index} play queued for the next beat")

    def _on_mutate_clicked(self) -> None:
        self._guard(self.controller.trigger_mutation)
        self._set_message(
            "Mutation queued for the next beat"
            if self._transport_running()
            else "Mutation applied immediately (transport stopped)"
        )

    def _on_octave_clicked(self) -> None:
        self._guard(self.controller.trigger_octave)
        self._set_message(
            "Octave queued for the next beat"
            if self._transport_running()
            else "Octave applied immediately (transport stopped)"
        )

    def _on_midi_toggle(self, _checked: bool) -> None:
        if self._midi_sync:
            return
        self._guard(
            self.controller.set_midi_enabled,
            self.midi_drone_checkbox.isChecked(),
            self.midi_arp_checkbox.isChecked(),
        )
        if self._transport_running():
            self._set_message("Drone/Arp change queued for the next beat")

    def _sync_midi_controls(self) -> None:
        """Reflect bridge MIDI enabled/ports and per-bus switch state."""

        midi = self.store.midi
        state = self.store.midi_state
        enabled = bool(midi.enabled) if midi is not None else False
        drone_on = state.drone_enabled if state is not None else True
        arp_on = state.arp_enabled if state is not None else True
        self._midi_sync = True
        try:
            self.midi_drone_checkbox.setEnabled(enabled)
            self.midi_arp_checkbox.setEnabled(enabled)
            self.midi_drone_checkbox.setChecked(bool(enabled and drone_on))
            self.midi_arp_checkbox.setChecked(bool(enabled and arp_on))
        finally:
            self._midi_sync = False

    def _flush_pending(self) -> None:
        if not self._pending:
            return
        pending = self._pending
        self._pending = {}
        for key, value in pending.items():
            if key[0] == "mutation":
                self._guard(self.controller.set_mutation, key[1], value)
                if key[1] == "glide_time":
                    self._set_message(f"stepwise glide {float(value):.3f} s")
            elif key[0] == "autotune":
                self._guard(
                    self.controller.set_autotune, key[1], key[2], value
                )
            elif key[0] == "length":
                self._guard(self.controller.loop_set_default_length, value)
            elif key[0] == "bpm":
                self._guard(self.controller.set_tempo, value)
            elif key[0] == "loop_beats":
                self._guard(self.controller.loop_set_beats, key[1], value)
            elif key[0] == "loop_length":
                self._guard(self.controller.loop_set_length, key[1], value)
            elif key[0] == "groove":
                self._guard(self.controller.set_groove_modifier, key[1], value)
            elif key[0] == "bass":
                self._guard(self.controller.set_bass_param, key[1], value)
            elif key[0] == "arp":
                self._guard(self.controller.set_arp_param, key[1], value)
            elif key[0] == "lead":
                self._guard(self.controller.set_lead_param, key[1], value)
            elif key[0] == "drone":
                self._guard(self.controller.set_drone_param, key[1], value)
            elif key[0] == "home":
                self._guard(self.controller.set_home_setting, key[1], value)

    def _guard(self, action: Callable[..., Any], *args: Any) -> None:
        try:
            action(*args)
        except Exception as exc:  # UI must stay alive if a send fails
            self._set_message(f"OSC send failed: {exc}")
            print(f"[UI] send failed: {exc}", file=sys.stderr)

    # -- syncing incoming state -----------------------------------------

    def _sync_mutation_knobs(self, control: Any) -> None:
        for key, knob in self.mutation_knobs.items():
            if key == "change_count":
                knob.set_range(1, control.max_changes)
            knob.set_value(getattr(control, key), emit=False)

    def _sync_autotune_knobs(self, channel: int, values: dict[str, float]) -> None:
        for spec in AUTOTUNE_PARAMS:
            knob = self.autotune_knobs.get((channel, spec.key))
            if knob is not None and spec.key in values:
                knob.set_value(values[spec.key], emit=False)

    def _sync_rhythm_controls(self) -> None:
        """Reflect the bridge tempo/meter/transport snapshot."""

        rhythm = self.store.rhythm
        if rhythm is None:
            return
        self._rhythm_sync = True
        try:
            self.bpm_spin.setValue(int(round(rhythm.bpm)))
            self.meter_num_spin.setValue(int(rhythm.meter_num))
            self.meter_den_combo.setCurrentText(str(rhythm.meter_den))
            self.transport_button.setChecked(bool(rhythm.running))
            self.transport_button.setText(
                "Stop" if rhythm.running else "Play"
            )
        finally:
            self._rhythm_sync = False

    def _sync_drum_controls(self) -> None:
        """Reflect drum enabled/port/rate/lane-mask state without echoing OSC."""

        drums = self.store.drums
        if drums is None:
            return
        available = bool(drums.port_ok)
        self._drum_sync = True
        try:
            self.drum_enable_checkbox.setEnabled(available)
            self.drum_enable_checkbox.setChecked(
                bool(available and drums.enabled)
            )
            self.drum_rate_combo.setEnabled(available)
            if drums.rate in DRUM_RATES:
                self.drum_rate_combo.setCurrentIndex(
                    DRUM_RATES.index(drums.rate)
                )
            states = drums.lane_states
            for note, checkbox in self.lane_checkboxes.items():
                checkbox.setEnabled(available)
                checkbox.setChecked(bool(available and states.get(note, False)))
        finally:
            self._drum_sync = False

    def _sync_loop_config(self, index: int) -> None:
        """Apply one ``/ji/loop/config`` snapshot to that loop's controls."""

        config = self.store.loop_configs.get(index)
        combo = self.loop_mode_combos.get(index)
        if config is None or combo is None:
            return
        self._loop_sync = True
        try:
            combo.setCurrentIndex(1 if config.mode == "beat" else 0)
            self._configure_loop_length(index, config.mode)
        finally:
            self._loop_sync = False
        self._refresh_loop_label(index)

    # -- status labels ---------------------------------------------------

    def _refresh_loop_label(self, index: int) -> None:
        label = self.loop_state_labels.get(index)
        if label is None:
            return
        state = self.store.loop_states.get(index, "waiting")
        config = self.store.loop_configs.get(
            index, osc_control.LoopConfig()
        )
        if config.mode == "beat":
            length = f"beat {config.beats:g}"
        else:
            length = f"free {config.seconds:g}s"
        label.setText(f"{state} | {length}")

    def _refresh_rhythm_labels(self) -> None:
        store = self.store
        if store.rhythm is None:
            self.rhythm_label.setText("Rhythm: waiting for bridge")
        else:
            rhythm = store.rhythm
            transport = "playing" if rhythm.running else "stopped"
            drums = "on" if rhythm.drums_enabled else "off"
            self.rhythm_label.setText(
                f"Rhythm: {rhythm.bpm:.1f} BPM | {rhythm.meter} | "
                f"transport {transport} | drums {drums} | "
                f"rate x{rhythm.drum_rate:g}"
            )

        if store.drums is None:
            self.drum_label.setText("Drums: waiting for bridge")
        elif not store.drums.port_ok:
            self.drum_label.setText(
                "Drums: disabled (drum port not available; see bridge log)"
            )
        elif not store.drums.enabled:
            self.drum_label.setText("Drums: off (port available)")
        else:
            self.drum_label.setText(
                f"Drums: on | rate x{store.drums.rate:g} | "
                f"lanes 0x{store.drums.lane_mask:04x}"
            )

    def _refresh_status_labels(self) -> None:
        store = self.store

        bridge_text = (
            "waiting for bridge" if store.waiting_for_bridge else "connected"
        )
        sc_text = "waiting for sclang" if store.waiting_for_sclang else "connected"
        self.bridge_status_label.setText(f"BRIDGE: {bridge_text}")
        self.sc_status_label.setText(f"SCLANG: {sc_text}")

        if store.freqset is None:
            self.freqset_label.setText("Set: waiting for bridge")
            self.anchor_label.setText("Anchor: waiting for bridge")
            self.fundamental_label.setText("Fundamental: waiting for bridge")
        else:
            freqset = store.freqset
            ratios = "  ".join(f"{ratio:.6f}" for ratio, _ in freqset.pairs)
            frequencies = "  ".join(f"{hz:8.2f}" for _, hz in freqset.pairs)
            self.freqset_label.setText(
                f"Set ({len(freqset.ratios)} classes)\n"
                f"ratios: {ratios}\n"
                f"Hz:     {frequencies}"
            )
            if freqset.anchor is None:
                self.anchor_label.setText("Anchor: none")
            else:
                anchor_hz = freqset.anchor * freqset.fundamental
                self.anchor_label.setText(
                    f"Anchor: {freqset.anchor:.6f} ({anchor_hz:.2f} Hz)"
                )
            self.fundamental_label.setText(
                f"Fundamental: {freqset.fundamental:.2f} Hz"
            )

        if store.control is None:
            self.control_label.setText("Controls: waiting for bridge")
        else:
            control = store.control
            self.control_label.setText(
                "Controls: "
                f"rand {control.randomness:.2f} | "
                f"diss {control.dissonance_bias:+.2f} | "
                f"step {control.stepwise_bias:.2f} | "
                f"temp {control.temperature:.2f} | "
                f"mobility {control.anchor_mobility:.2f} | "
                f"changes {control.change_count}/{control.max_changes} | "
                f"glide {control.glide_time:.3f} s"
            )

        if store.cpu is None:
            self.cpu_label.setText("CPU: waiting for sclang")
        else:
            self.cpu_label.setText(f"CPU: {store.cpu:.1f}%")

        if store.midi is None:
            self.midi_label.setText("MIDI: waiting for bridge")
        elif not store.midi.enabled:
            self.midi_label.setText("MIDI: disabled")
        else:
            state = store.midi_state
            drone_state = "on" if (state is None or state.drone_enabled) else "off"
            arp_state = "on" if (state is None or state.arp_enabled) else "off"
            self.midi_label.setText(
                f"MIDI: enabled | drone: {store.midi.drone_port or '?'} "
                f"({drone_state}) | arp: {store.midi.arp_port or '?'} "
                f"({arp_state})"
            )

        if store.waiting_for_sclang:
            self.loop_status_label.setText("Loop states: waiting for sclang")
        else:
            states = " | ".join(
                f"{index} {store.loop_states.get(index, '?')}"
                for index in range(1, 5)
            )
            self.loop_status_label.setText(f"Loop states: {states}")

        self._refresh_rhythm_labels()
        self._update_bass_label()
        self._update_lead_label()
        self._update_drone_label()
        self._update_home_label()

        for index in range(1, 5):
            self._refresh_loop_label(index)

    def _set_message(self, text: str) -> None:
        self.message_label.setText(text)

    # -- close -----------------------------------------------------------

    def closeEvent(self, event: Any) -> None:  # noqa: N802 (Qt API)
        try:
            self._flush_pending()
        finally:
            self._send_timer.stop()
            self._quantize_timer.stop()
            stop = getattr(self.controller, "stop", None)
            if callable(stop):
                try:
                    stop()
                except Exception as exc:
                    print(f"[UI] controller stop failed: {exc}", file=sys.stderr)
            self._started = False
        event.accept()


# ---------------------------------------------------------------------------
# Entry point
# ---------------------------------------------------------------------------


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(
        description="Stage 7/8.3b performance GUI (PyQt6, one window)"
    )
    parser.add_argument("--bridge-host", default="127.0.0.1")
    parser.add_argument("--bridge-port", type=int, default=9000)
    parser.add_argument("--sc-host", default="127.0.0.1")
    parser.add_argument("--sc-port", type=int, default=57120)
    parser.add_argument("--ui-host", default="127.0.0.1")
    parser.add_argument("--ui-port", type=int, default=9001)
    return parser


def main(argv: list[str] | None = None) -> int:
    parser = build_parser()
    args = parser.parse_args(argv)

    for label, port in (
        ("--bridge-port", args.bridge_port),
        ("--sc-port", args.sc_port),
        ("--ui-port", args.ui_port),
    ):
        if not 1 <= port <= 65535:
            parser.error(f"{label} must be between 1 and 65535")

    app = QApplication(sys.argv[:1])
    app.setApplicationName("JI Station Stage 8.4")

    controller = OSCController(
        bridge_host=args.bridge_host,
        bridge_port=args.bridge_port,
        sc_host=args.sc_host,
        sc_port=args.sc_port,
        ui_host=args.ui_host,
        ui_port=args.ui_port,
    )
    window = MainWindow(controller, autostart=False)

    try:
        window.start_controller()
    except Exception as exc:
        message = f"Could not start the UI OSC listener:\n{exc}"
        print(f"[UI] {message}", file=sys.stderr)
        QMessageBox.critical(None, "JI Stage 8.4 UI", message)
        window.close()
        return 1

    window.show()
    return app.exec()


if __name__ == "__main__":
    raise SystemExit(main())
