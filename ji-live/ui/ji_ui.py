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
from pathlib import Path
from fractions import Fraction
from typing import Any, Callable

try:
    from PyQt6.QtCore import (
        QObject,
        QPointF,
        QRectF,
        QSize,
        QSignalBlocker,
        Qt,
        QTimer,
        pyqtSignal,
    )
    from PyQt6.QtGui import (
        QBrush,
        QColor,
        QFont,
        QFontDatabase,
        QFontMetrics,
        QPainter,
        QPalette,
        QPen,
    )
    from PyQt6.QtWidgets import (
        QAbstractSpinBox,
        QApplication,
        QCheckBox,
        QComboBox,
        QDial,
        QDoubleSpinBox,
        QGridLayout,
        QGroupBox,
        QHBoxLayout,
        QLabel,
        QLineEdit,
        QMainWindow,
        QMessageBox,
        QPushButton,
        QScrollArea,
        QSizePolicy,
        QSpinBox,
        QToolTip,
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
        LEAD_INPUT_BARS_DEFAULT,
        LEAD_INPUT_BARS_MAX,
        LEAD_INPUT_BARS_MIN,
        LEAD_INPUT_COUNT_INS,
        LEAD_INPUT_COUNT_IN_DEFAULT,
        LEAD_INPUT_QUANTIZE_DEFAULT,
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
        BASS_HITS_DEFAULT_ENABLED,
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
        LOOP_GRAIN_BREATH_DEFAULT,
        LOOP_GRAIN_BREATH_RATE_DEFAULT,
        LOOP_GRAIN_BREATH_RATE_MAX,
        LOOP_GRAIN_BREATH_RATE_MIN,
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
        LOOP_COUNT_IN_OPTIONS,
        LOOP_DIVISION_LABELS,
        LOOP_GRAIN_DEPTH_DEFAULT,
        LOOP_GRAIN_FREEZE_DEFAULT,
        LOOP_GRAIN_MOTIONS,
        LOOP_GRAIN_MOTION_DEFAULT,
        LOOP_GRAIN_FILTER_DEFAULT,
        LOOP_GRAIN_FILTER_RATE_DEFAULT,
        LOOP_GRAIN_FILTER_RATE_MAX,
        LOOP_GRAIN_FILTER_RATE_MIN,
        LOOP_GRAIN_MELT_DEFAULT,
        LOOP_GRAIN_MORPH_DEFAULT,
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
        LOOP_MEM_SLOT_NAMES,
        LOOP_RATE_DEFAULT,
        LOOP_RATE_LABELS,
        LOOP_RATE_VALUES,
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
        LEAD_INPUT_BARS_DEFAULT,
        LEAD_INPUT_BARS_MAX,
        LEAD_INPUT_BARS_MIN,
        LEAD_INPUT_COUNT_INS,
        LEAD_INPUT_COUNT_IN_DEFAULT,
        LEAD_INPUT_QUANTIZE_DEFAULT,
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
        BASS_HITS_DEFAULT_ENABLED,
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
        LOOP_GRAIN_BREATH_DEFAULT,
        LOOP_GRAIN_BREATH_RATE_DEFAULT,
        LOOP_GRAIN_BREATH_RATE_MAX,
        LOOP_GRAIN_BREATH_RATE_MIN,
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
        LOOP_COUNT_IN_OPTIONS,
        LOOP_DIVISION_LABELS,
        LOOP_GRAIN_DEPTH_DEFAULT,
        LOOP_GRAIN_FREEZE_DEFAULT,
        LOOP_GRAIN_MOTIONS,
        LOOP_GRAIN_MOTION_DEFAULT,
        LOOP_GRAIN_FILTER_DEFAULT,
        LOOP_GRAIN_FILTER_RATE_DEFAULT,
        LOOP_GRAIN_FILTER_RATE_MAX,
        LOOP_GRAIN_FILTER_RATE_MIN,
        LOOP_GRAIN_MELT_DEFAULT,
        LOOP_GRAIN_MORPH_DEFAULT,
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
        LOOP_MEM_SLOT_NAMES,
        LOOP_RATE_DEFAULT,
        LOOP_RATE_LABELS,
        LOOP_RATE_VALUES,
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

# Stage 8.8: the pure note/tuning maths, imported independently of the big
# osc_control block above (it has no PyQt6 and no OSC dependency).
if __package__:
    from . import fretboard
else:  # pragma: no cover - direct ``python ui/ji_ui.py``
    import fretboard  # type: ignore[no-redef]

# Stage 9.1: the QWERTY map lives in the pure control module (one source of
# truth for the widget and the harness).  Resolve the project root so the
# direct ``python ui/ji_ui.py`` path also finds ``control``.
_PROJECT_ROOT = Path(__file__).resolve().parent.parent
if str(_PROJECT_ROOT) not in sys.path:
    sys.path.insert(0, str(_PROJECT_ROOT))
from control.lead_input import key_midi  # noqa: E402


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
# Stage 8.8: fretboard improvisation aid
# ---------------------------------------------------------------------------

FRETBOARD_ACCENT_ROOT = QColor("#d9822b")
FRETBOARD_ACCENT_SCALE = QColor("#3d7ea6")
FRETBOARD_ACCENT_ANCHOR = QColor("#8e44ad")
FRETBOARD_INLAYS: tuple[int, ...] = (3, 5, 7, 9, 12, 15, 17, 19, 21, 24)
FRETBOARD_MARGIN_LEFT = 64.0
FRETBOARD_MARGIN_RIGHT = 12.0
FRETBOARD_MARGIN_TOP = 20.0
FRETBOARD_MARGIN_BOTTOM = 8.0
FRETBOARD_OPEN_WIDTH = 28.0


def _cents_text(cents: float) -> str:
    """Signed whole cents with a half-up rounding, never ``-0``."""

    value = float(cents)
    rounded = (
        int(math.floor(value + 0.5))
        if value >= 0.0
        else int(math.ceil(value - 0.5))
    )
    return f"{rounded:+d}"


def _fretboard_ratio_label(ratio: float) -> str:
    """The JI fraction for a real set member.

    ``osc_control.ji_ratio_label`` snaps to the fixed grain vocabulary, so it
    is used when the member really is one of those ratios; otherwise the GUI's
    exact ``Fraction`` helper (its decimal/fraction fallback) keeps duplicate
    sources such as ``10/9`` distinct from ``9/8``.
    """

    try:
        value = float(ratio)
    except (TypeError, ValueError):
        return "?"
    label = osc_control.ji_ratio_label(value)
    for candidate, candidate_label in osc_control.JI_RATIO_LABELS:
        if candidate_label == label and abs(candidate - value) <= 1e-6:
            return label
    return _ratio_label(value)


class FretboardWidget(QWidget):
    """Read-only painted guitar neck marking the live 12-TET set.

    It never sends OSC and owns no timer: ``set_board`` rebuilds the cells and
    one repaint draws them.  String 0 (the lowest) is drawn at the bottom.
    """

    def __init__(self, parent: QWidget | None = None) -> None:
        super().__init__(parent)
        self._tuning = fretboard.DEFAULT_TUNING
        self._degrees: tuple[fretboard.ScaleDegree, ...] = ()
        self._cells: tuple[fretboard.FretboardCell, ...] = ()
        self._cell_lookup: dict[tuple[int, int], fretboard.FretboardCell] = {}
        self._frets = fretboard.DEFAULT_FRETS
        self._show_names = True
        self._root_pc: int | None = None
        self._anchor_pc: int | None = None
        self._fundamental: float | None = None
        self.setMouseTracking(True)
        self.setMinimumWidth(900)
        self.setSizePolicy(
            QSizePolicy.Policy.Expanding, QSizePolicy.Policy.Fixed
        )
        self._rebuild()

    # -- public read-only state (the Qt test uses these) -----------------

    @property
    def cells(self) -> tuple[fretboard.FretboardCell, ...]:
        return self._cells

    @property
    def degrees(self) -> tuple[fretboard.ScaleDegree, ...]:
        return self._degrees

    @property
    def frets(self) -> int:
        return self._frets

    @property
    def show_names(self) -> bool:
        return self._show_names

    @property
    def tuning(self) -> fretboard.GuitarTuning:
        return self._tuning

    # -- board updates ---------------------------------------------------

    def set_board(
        self,
        tuning: fretboard.GuitarTuning,
        degrees: Any,
        *,
        root_pc: object | None = None,
        anchor_pc: object | None = None,
        frets: object = fretboard.DEFAULT_FRETS,
        show_names: bool = True,
        fundamental: object | None = None,
    ) -> None:
        """Store a validated board and repaint (no signals, no OSC)."""

        if not isinstance(tuning, fretboard.GuitarTuning):
            raise TypeError("tuning must be a GuitarTuning")
        if isinstance(degrees, (str, bytes)):
            raise TypeError("degrees must be a sequence of ScaleDegree")
        items = tuple(degrees)
        cells = fretboard.build_fretboard(
            tuning,
            items,
            frets=frets,
            root_pc=root_pc,
            anchor_pc=anchor_pc,
        )
        fundamental_value: float | None = None
        if fundamental is not None:
            try:
                fundamental_value = float(fundamental)
            except (TypeError, ValueError) as exc:
                raise TypeError("fundamental must be a number") from exc
            if not math.isfinite(fundamental_value) or fundamental_value <= 0.0:
                raise ValueError("fundamental must be finite and > 0")

        self._tuning = tuning
        self._degrees = items
        self._cells = cells
        self._cell_lookup = {
            (cell.string, cell.fret): cell for cell in cells
        }
        self._frets = cells[-1].fret if cells else 0
        self._root_pc = None if root_pc is None else int(root_pc)
        self._anchor_pc = None if anchor_pc is None else int(anchor_pc)
        self._show_names = bool(show_names)
        self._fundamental = fundamental_value
        self.updateGeometry()
        self.update()

    def _rebuild(self) -> None:
        self.set_board(
            self._tuning,
            self._degrees,
            root_pc=self._root_pc,
            anchor_pc=self._anchor_pc,
            frets=self._frets,
            show_names=self._show_names,
            fundamental=self._fundamental,
        )

    # -- geometry --------------------------------------------------------

    def _layout(self) -> tuple[float, float, float, float, float, float, float]:
        width = max(1.0, float(self.width()))
        height = max(1.0, float(self.height()))
        count = max(1, self._tuning.string_count)
        board_left = FRETBOARD_MARGIN_LEFT
        board_right = max(
            board_left + 10.0, width - FRETBOARD_MARGIN_RIGHT
        )
        top = FRETBOARD_MARGIN_TOP
        bottom = max(top + 4.0, height - FRETBOARD_MARGIN_BOTTOM)
        row_height = (bottom - top) / count
        nut_x = board_left + FRETBOARD_OPEN_WIDTH
        available = max(1.0, board_right - nut_x)
        fret_width = available / self._frets if self._frets > 0 else available
        return (
            board_left,
            board_right,
            top,
            bottom,
            row_height,
            nut_x,
            fret_width,
        )

    def _string_y(self, index: int) -> float:
        _left, _right, top, bottom, row_height, _nut, _fw = self._layout()
        return bottom - (index + 0.5) * row_height

    def _fret_x(self, fret: int) -> float:
        left, _right, _top, _bottom, _row, nut_x, fret_width = self._layout()
        if fret <= 0:
            return left + FRETBOARD_OPEN_WIDTH / 2.0
        return nut_x + (fret - 0.5) * fret_width

    def _cell_for_position(
        self, x: float, y: float
    ) -> fretboard.FretboardCell | None:
        left, right, top, bottom, row_height, nut_x, fret_width = self._layout()
        if not (left <= x <= right and top <= y <= bottom):
            return None
        index = int((bottom - y) / row_height)
        if not 0 <= index < self._tuning.string_count:
            return None
        if x < nut_x:
            fret = 0
        else:
            if self._frets <= 0:
                return None
            fret = int((x - nut_x) / fret_width) + 1
            if fret > self._frets:
                return None
        return self._cell_lookup.get((index, fret))

    # -- painting --------------------------------------------------------

    def paintEvent(self, _event: Any) -> None:  # noqa: N802 (Qt API)
        painter = QPainter(self)
        painter.setRenderHint(QPainter.RenderHint.Antialiasing, True)
        palette = self.palette()
        window = palette.color(QPalette.ColorRole.Window)
        base = palette.color(QPalette.ColorRole.Base)
        ink = palette.color(QPalette.ColorRole.WindowText)
        muted = palette.color(QPalette.ColorRole.Mid)
        painter.fillRect(self.rect(), window)

        left, right, top, bottom, row_height, nut_x, fret_width = (
            self._layout()
        )
        board_rect = QRectF(left, top, right - left, bottom - top)
        painter.fillRect(board_rect, base)

        inlay = QColor(muted)
        inlay.setAlpha(110)
        painter.setPen(Qt.PenStyle.NoPen)
        painter.setBrush(QBrush(inlay))
        centre_y = (top + bottom) / 2.0
        for fret in FRETBOARD_INLAYS:
            if fret > self._frets:
                continue
            x = nut_x + (fret - 0.5) * fret_width
            if fret in (12, 24):
                offset = max(4.0, row_height * 1.4)
                ys = (centre_y - offset, centre_y + offset)
            else:
                ys = (centre_y,)
            for y in ys:
                painter.drawEllipse(QRectF(x - 3.0, y - 3.0, 6.0, 6.0))

        string_pen = QPen(QColor(ink.red(), ink.green(), ink.blue(), 170), 1.0)
        painter.setPen(string_pen)
        for index in range(self._tuning.string_count):
            y = self._string_y(index)
            painter.drawLine(QPointF(left, y), QPointF(right, y))

        painter.setPen(QPen(muted, 1.0))
        for fret in range(1, self._frets + 1):
            x = nut_x + fret * fret_width
            painter.drawLine(QPointF(x, top), QPointF(x, bottom))
        painter.setPen(QPen(ink, 3.0))
        painter.drawLine(QPointF(nut_x, top), QPointF(nut_x, bottom))

        label_font = QFont(self.font())
        label_font.setPointSize(7)
        painter.setFont(label_font)
        painter.setPen(ink)
        for index, label in enumerate(self._tuning.labels):
            y = self._string_y(index)
            painter.drawText(
                QRectF(0.0, y - row_height / 2.0, left - 8.0, row_height),
                Qt.AlignmentFlag.AlignRight | Qt.AlignmentFlag.AlignVCenter,
                label,
            )

        if self._frets and fret_width >= 12.0:
            number_font = QFont(self.font())
            number_font.setPointSize(7)
            number_metrics = QFontMetrics(number_font)
            painter.setFont(number_font)
            painter.setPen(muted)
            for fret in range(1, self._frets + 1):
                text = str(fret)
                width = number_metrics.horizontalAdvance(text) + 4.0
                if width > fret_width:
                    continue
                x = nut_x + (fret - 0.5) * fret_width
                painter.drawText(
                    QRectF(x - width / 2.0, 1.0, width, top - 2.0),
                    Qt.AlignmentFlag.AlignCenter,
                    text,
                )

        if self._degrees:
            self._paint_markers(painter, row_height, fret_width)
        painter.end()

    def _paint_markers(
        self,
        painter: QPainter,
        row_height: float,
        fret_width: float,
    ) -> None:
        marker_font = QFont(self.font())
        marker_font.setPointSizeF(6.5)
        marker_font.setBold(True)
        metrics = QFontMetrics(marker_font)
        outline = QColor(self.palette().color(QPalette.ColorRole.Light))
        outline.setAlpha(230)
        for cell in self._cells:
            degree = cell.degree
            if degree is None:
                continue
            x = self._fret_x(cell.fret)
            y = self._string_y(cell.string)
            column = FRETBOARD_OPEN_WIDTH if cell.fret <= 0 else fret_width
            radius = max(3.5, min(10.0, row_height * 0.42, column * 0.42))
            colour = (
                FRETBOARD_ACCENT_ROOT if cell.is_root else FRETBOARD_ACCENT_SCALE
            )
            painter.setPen(QPen(outline, 1.2))
            painter.setBrush(QBrush(colour))
            painter.drawEllipse(
                QRectF(x - radius, y - radius, radius * 2.0, radius * 2.0)
            )
            if cell.is_anchor:
                ring = radius + 2.5
                painter.setBrush(Qt.BrushStyle.NoBrush)
                painter.setPen(QPen(FRETBOARD_ACCENT_ANCHOR, 2.0))
                painter.drawEllipse(
                    QRectF(x - ring, y - ring, ring * 2.0, ring * 2.0)
                )
            if not self._show_names:
                continue
            text = degree.name
            if (
                metrics.horizontalAdvance(text) <= radius * 2.0 - 2.0
                and metrics.height() <= radius * 2.0
            ):
                painter.setFont(marker_font)
                painter.setPen(QPen(QColor("#ffffff")))
                painter.drawText(
                    QRectF(
                        x - radius, y - radius, radius * 2.0, radius * 2.0
                    ),
                    Qt.AlignmentFlag.AlignCenter,
                    text,
                )

    # -- tooltip ---------------------------------------------------------

    def _tooltip_for(self, cell: fretboard.FretboardCell) -> str:
        degree = cell.degree
        if degree is None:
            return ""
        source = degree.best_source
        parts = [
            f"{self._tuning.labels[cell.string]} fret {cell.fret}",
            degree.name,
            _fretboard_ratio_label(source.ratio),
            f"{_cents_text(source.cents)}¢",
        ]
        if self._fundamental is not None:
            parts.append(f"{self._fundamental * source.ratio:.2f} Hz")
        text = " · ".join(parts)
        if len(degree.sources) > 1:
            extras = " / ".join(
                f"{_fretboard_ratio_label(other.ratio)} "
                f"{_cents_text(other.cents)}¢"
                for other in degree.sources[1:]
            )
            text += f" (also {extras})"
        return text

    def mouseMoveEvent(self, event: Any) -> None:  # noqa: N802 (Qt API)
        position = event.position()
        cell = self._cell_for_position(position.x(), position.y())
        if cell is None or cell.degree is None:
            QToolTip.hideText()
        else:
            QToolTip.showText(
                self.mapToGlobal(position.toPoint()),
                self._tooltip_for(cell),
                self,
            )
        super().mouseMoveEvent(event)

    def leaveEvent(self, event: Any) -> None:  # noqa: N802 (Qt API)
        QToolTip.hideText()
        super().leaveEvent(event)

    # -- size ------------------------------------------------------------

    def _preferred_size(self) -> QSize:
        return QSize(940, 28 + 18 * self._tuning.string_count)

    def sizeHint(self) -> QSize:  # noqa: N802 (Qt API)
        return self._preferred_size()

    def minimumSizeHint(self) -> QSize:  # noqa: N802 (Qt API)
        return QSize(900, 28 + 18 * self._tuning.string_count)


# ---------------------------------------------------------------------------
# Main window
# ---------------------------------------------------------------------------


class MainWindow(QMainWindow):
    """One screen: Mutation, Home, State, Autotune, Loops, Rhythm, Groove, Bass, Arp, Drone, Lead, Fretboard, Status."""

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
        self._loop_edit_sync = False
        self._loop_custom: set[int] = set()
        self._loop_mem_sync = False
        self._groove_sync = False
        self._bass_sync = False
        self._bass_hits_sync = False
        self._breath_sync = False
        self._fretboard_sync = False
        self._fretboard_tuning = fretboard.DEFAULT_TUNING
        self._fretboard_degrees: tuple[fretboard.ScaleDegree, ...] = ()
        self._fretboard_root_pc: int | None = None
        self._fretboard_anchor_pc: int | None = None
        self._fretboard_fundamental: float | None = None
        self._arp_sync = False
        self._lead_sync = False
        self._lead_input_sync = False
        self._melody_sync = False
        self._keys_down: set[int] = set()
        self._keyboard_octave = 0
        self._drone_sync = False
        self._home_sync = False
        self._state_sync = False
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
        application = QApplication.instance()
        if application is not None:
            application.focusChanged.connect(self._on_focus_changed)

        self.setWindowTitle("JI Station - Stage 9.1 Lead Input")
        # Stage 8.6 added the Loop edit row and the Grain row (1280x880); 8.6.1
        # added the Grain motion row (1280x910); 8.6.2 added the Morph row
        # (1280x940); 8.7 added one Rhythm row (Breath) and a hidden Bass Hits
        # row (1280x966); 8.8 adds the full-width Fretboard group (~200 px),
        # 1280x1172; 9.0 adds one Harm row (~26 px) -> 1198; 9.1 adds the
        # Lead input group (~60 px), so the default grows to 1258.  The scroll
        # area remains the small-screen safety net.
        self.resize(1280, 1258)
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
        outer.addWidget(self._build_state_group())
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
        outer.addWidget(self._build_lead_input_group())
        outer.addWidget(self._build_fretboard_group())
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
        self._sync_state_controls()  # start disabled until a name is typed/known
        self._sync_loop_edit_row(1)  # Stage 8.6 edit/grain rows
        self._sync_loop_mem(0)
        self._sync_fretboard_controls()  # Stage 8.8: Standard E until changed
        self._sync_fretboard()  # waiting for the set until /ji/freqset/changed

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
            self._refresh_harm_enabled()
        if address == osc_control.LOOP_CONFIG:
            index = int(round(float(args[0])))
            self._sync_loop_config(index)
        if address == osc_control.LOOP_EDIT:
            edit = self.store.loop_edits.get(int(round(float(args[0]))))
            if edit is not None and edit.index == self._selected_loop():
                self._sync_loop_edit_row(edit.index)
        if address == osc_control.LOOP_MEM:
            self._sync_loop_mem(int(round(float(args[0]))))
        if address == osc_control.LOOP_COUNT:
            self._refresh_loop_label(int(round(float(args[0]))))
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
        if address == osc_control.BASS_HITS:
            self._sync_bass_hits()
        if address == osc_control.BASS_NOTE:
            self._update_bass_label()
        if address == osc_control.FREQSET_CHANGED:
            self._sync_fretboard()
        if address == osc_control.BREATH_STATE:
            self._sync_breath_controls()
        if address == osc_control.ARP_STATE:
            self._sync_arp_controls()
        if address == osc_control.LEAD_STATE:
            self._sync_lead_controls()
        if address == osc_control.LEAD_NOTE:
            self._update_lead_label()
        if address == osc_control.LEAD_INPUT_STATE:
            self._sync_lead_input_controls()
            self._update_lead_input_label()
        if address == osc_control.LEAD_MELODY_LIST:
            self._sync_melody_list()
        if address == osc_control.LEAD_MELODY_STATE:
            self._update_lead_input_label()
        if address == osc_control.DRONE_STATE:
            self._sync_drone_controls()
        if address == osc_control.HOME_STATE:
            self._sync_home_controls()
        if address in (osc_control.STATE_LIST, osc_control.STATE_STATE):
            self._sync_state_controls()

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

    def _build_state_group(self) -> QGroupBox:
        """Stage 8.5 compact State group (one control row + the status line)."""

        group = QGroupBox("State")
        layout = QVBoxLayout(group)
        layout.setContentsMargins(4, 2, 4, 2)
        layout.setSpacing(1)

        row = QHBoxLayout()
        row.setSpacing(3)

        row.addWidget(QLabel("Name"))
        self.state_combo = QComboBox()
        self.state_combo.setEditable(True)
        self.state_combo.setFixedWidth(170)
        self.state_combo.setToolTip(
            "Saved performance state. A state holds the frequency set, mutation "
            "controls, home slots, tempo/meter, groove, drums and the "
            "bass/arp/lead/drone settings. Loops and the sclang autotune "
            "parameters are NOT part of a state."
        )
        self.state_combo.currentTextChanged.connect(self._on_state_name_changed)
        row.addWidget(self.state_combo)

        self.state_save_button = QPushButton("Save")
        self.state_save_button.setMinimumHeight(22)
        self.state_save_button.setToolTip(
            "Save the whole performance configuration under this name."
        )
        self.state_save_button.clicked.connect(self._on_state_save)
        row.addWidget(self.state_save_button)

        self.state_load_button = QPushButton("Load")
        self.state_load_button.setMinimumHeight(22)
        self.state_load_button.setToolTip(
            "Recall the state: one set change and one update. While the "
            "transport runs it lands on the next beat."
        )
        self.state_load_button.clicked.connect(self._on_state_load)
        row.addWidget(self.state_load_button)

        self.state_delete_button = QPushButton("Delete")
        self.state_delete_button.setMinimumHeight(22)
        self.state_delete_button.setToolTip("Delete this state file.")
        self.state_delete_button.clicked.connect(self._on_state_delete)
        row.addWidget(self.state_delete_button)

        self.state_refresh_button = QPushButton("Refresh")
        self.state_refresh_button.setMinimumHeight(22)
        self.state_refresh_button.setToolTip(
            "Ask the bridge for a fresh state list and status."
        )
        self.state_refresh_button.clicked.connect(self._on_state_refresh)
        row.addWidget(self.state_refresh_button)

        row.addSpacing(6)
        self.state_label = QLabel("State: waiting for bridge")
        self.state_label.setTextInteractionFlags(
            Qt.TextInteractionFlag.TextSelectableByMouse
        )
        row.addWidget(self.state_label, 1)
        layout.addLayout(row)
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
                    "dub",
                    "Dub",
                    lambda _checked=False, i=index: self._dub_loop(i),
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
                (1, 1, 1, 1),
                (1, 2, 1, 1),
            )
            for (name, text, slot), (row, column, row_span, col_span) in zip(
                actions, placements
            ):
                button = QPushButton(text)
                button.setMinimumHeight(20)
                button.clicked.connect(slot)
                self.loop_buttons[(index, name)] = button
                grid.addWidget(button, row, column, row_span, col_span)
            self.loop_buttons[(index, "dub")].setToolTip(
                "Overdub: records OVER the loop for one pass, keeping what is "
                "already there (the loop keeps playing). On an empty loop it "
                "records a fresh take. Rec still replaces."
            )
            loop_layout.addLayout(grid)
            loop_row.addWidget(loop_group)

        layout.addLayout(loop_row)

        # --- Stage 8.6: ONE loop edit row (selected loop) -----------------
        edit_row = QHBoxLayout()
        edit_row.setSpacing(4)
        self.loop_edit_selector = QComboBox()
        self.loop_edit_selector.addItems([str(i) for i in range(1, 5)])
        self.loop_edit_selector.setFixedWidth(44)
        self.loop_edit_selector.setToolTip(
            "Which loop the edit and grain rows show and change."
        )
        self.loop_edit_selector.currentIndexChanged.connect(
            self._on_loop_edit_selected
        )
        edit_row.addWidget(QLabel("Loop"))
        edit_row.addWidget(self.loop_edit_selector)

        self.loop_reverse_checkbox = QCheckBox("Rev")
        self.loop_reverse_checkbox.setToolTip(
            "Play the loop backwards. The read head keeps its position and "
            "the direction flips (no restart, no click)."
        )
        self.loop_reverse_checkbox.toggled.connect(self._on_loop_reverse)
        edit_row.addWidget(self.loop_reverse_checkbox)

        edit_row.addWidget(QLabel("Rate"))
        self.loop_rate_combo = QComboBox()
        self.loop_rate_combo.addItems(list(LOOP_RATE_LABELS))
        self.loop_rate_combo.setCurrentText("1/1")
        self.loop_rate_combo.setFixedWidth(64)
        self.loop_rate_combo.setToolTip(
            "Varispeed: the loop plays at this ratio of its own speed and is "
            "re-pitched by it (2/1 = octave up, 2/3 = down a fifth). It still "
            "runs through the JI autotune, so the current set may pull it "
            "partly back toward a class — that is intended (new material, not "
            "an exact transposition)."
        )
        self.loop_rate_combo.currentIndexChanged.connect(self._on_loop_rate)
        edit_row.addWidget(self.loop_rate_combo)

        edit_row.addWidget(QLabel("Gain"))
        self.loop_gain_spin = QDoubleSpinBox()
        self.loop_gain_spin.setDecimals(2)
        self.loop_gain_spin.setRange(0.0, 1.0)
        self.loop_gain_spin.setSingleStep(0.05)
        self.loop_gain_spin.setValue(0.7)
        self.loop_gain_spin.setFixedWidth(58)
        self.loop_gain_spin.setToolTip(
            "Per-loop output gain. 0.70 is the accepted stage-4 loudness."
        )
        self.loop_gain_spin.valueChanged.connect(self._on_loop_gain)
        edit_row.addWidget(self.loop_gain_spin)

        edit_row.addWidget(QLabel("Count-in"))
        self.loop_count_in_combo = QComboBox()
        self.loop_count_in_combo.addItems(list(LOOP_COUNT_IN_OPTIONS))
        self.loop_count_in_combo.setFixedWidth(72)
        self.loop_count_in_combo.setToolTip(
            "Count-in delays a Rec by the chosen amount on the beat clock "
            "(1 bar follows the current meter). Free mode counts while the "
            "transport is stopped; beat mode is cancelled by a transport "
            "stop; a second Rec skips the count-in. It applies to Rec only, "
            "not to a dub."
        )
        self.loop_count_in_combo.currentTextChanged.connect(
            self._on_loop_count_in
        )
        edit_row.addWidget(self.loop_count_in_combo)

        self.loop_auto_checkbox = QCheckBox("Auto")
        self.loop_auto_checkbox.setToolTip(
            "Auto-play: start the loop as soon as a take (or an empty-loop "
            "dub) finishes. Beat mode needs a running transport, otherwise "
            "the take stays recorded and one line says why."
        )
        self.loop_auto_checkbox.toggled.connect(self._on_loop_auto)
        edit_row.addWidget(self.loop_auto_checkbox)

        edit_row.addWidget(QLabel("Mem"))
        self.loop_mem_combo = QComboBox()
        self.loop_mem_combo.addItems(list(LOOP_MEM_SLOT_NAMES))
        self.loop_mem_combo.setFixedWidth(48)
        self.loop_mem_combo.setToolTip(
            "Session-only memory slots A-D: save copies of a loop's audio and "
            "its edit state. They are lost when sclang re-evaluates the rig; "
            "nothing is written to disk."
        )
        self.loop_mem_combo.currentIndexChanged.connect(self._on_loop_mem_selected)
        edit_row.addWidget(self.loop_mem_combo)

        self.loop_mem_save_button = QPushButton("Save")
        self.loop_mem_recall_button = QPushButton("Recall")
        self.loop_mem_clear_button = QPushButton("Clr")
        for button, slot in (
            (self.loop_mem_save_button, self._on_loop_mem_save),
            (self.loop_mem_recall_button, self._on_loop_mem_recall),
            (self.loop_mem_clear_button, self._on_loop_mem_clear),
        ):
            button.setMinimumHeight(20)
            button.setMinimumWidth(52)
            button.clicked.connect(slot)
            edit_row.addWidget(button)
        self.loop_mem_save_button.setToolTip(
            "Copy the selected loop's audio + edit state into the selected "
            "memory slot (refused while recording or dubbing)."
        )
        self.loop_mem_recall_button.setToolTip(
            "Copy the selected slot back into the selected loop. The loop "
            "stays stopped — press Play."
        )
        self.loop_mem_clear_button.setToolTip("Free the selected memory slot.")

        #  The status line lives on the Morph row (Stage 8.6.2): the edit row is
        #  already full and the morph status is the headline of that row.
        edit_row.addStretch(1)
        layout.addLayout(edit_row)

        # --- Stage 8.6: ONE grain row (selected loop) ---------------------
        grain_row = QHBoxLayout()
        grain_row.setSpacing(4)
        self.grain_checkbox = QCheckBox("Grain")
        self.grain_checkbox.setToolTip(
            "Granular playback: the loop becomes a grain cloud that still "
            "goes through the JI autotune (tuned, detuned or both depending "
            "on the material and the set). switch off for normal playback."
        )
        self.grain_checkbox.toggled.connect(self._on_grain_toggled)
        grain_row.addWidget(self.grain_checkbox)

        grain_row.addWidget(QLabel("Sync"))
        self.grain_sync_combo = QComboBox()
        self.grain_sync_combo.addItems(["free", "beat"])
        self.grain_sync_combo.setFixedWidth(62)
        self.grain_sync_combo.setToolTip(
            "free = grains run on their own clock (transport stopped is "
            "fine). beat = one grain per division, locked to the transport; "
            "stopping the transport stops the loop."
        )
        self.grain_sync_combo.currentIndexChanged.connect(self._on_grain_sync)
        grain_row.addWidget(self.grain_sync_combo)

        grain_row.addWidget(QLabel("Div"))
        self.grain_div_combo = QComboBox()
        self.grain_div_combo.addItems(list(LOOP_DIVISION_LABELS))
        self.grain_div_combo.setFixedWidth(58)
        self.grain_div_combo.setToolTip("Beat-synced grain interval.")
        self.grain_div_combo.currentTextChanged.connect(self._on_grain_div)
        grain_row.addWidget(self.grain_div_combo)

        grain_row.addWidget(QLabel("Rate"))
        self.grain_rate_spin = QDoubleSpinBox()
        self.grain_rate_spin.setDecimals(1)
        self.grain_rate_spin.setRange(0.5, LOOP_GRAIN_RATE_MAX)
        self.grain_rate_spin.setSingleStep(0.5)
        self.grain_rate_spin.setValue(LOOP_GRAIN_RATE_DEFAULT)
        self.grain_rate_spin.setFixedWidth(58)
        self.grain_rate_spin.setToolTip(
            "Free-mode grains per second (Sync = free). 20 with a 120 ms "
            "grain is a continuous cloud; 5 vs 60 is a clear density change. "
            "Beat mode uses Div."
        )
        grain_row.addWidget(self.grain_rate_spin)

        grain_row.addWidget(QLabel("Size"))
        self.grain_size_spin = QDoubleSpinBox()
        self.grain_size_spin.setDecimals(0)
        self.grain_size_spin.setRange(5, 500)
        self.grain_size_spin.setSingleStep(5)
        self.grain_size_spin.setValue(LOOP_GRAIN_SIZE_DEFAULT_MS)
        self.grain_size_spin.setSuffix(" ms")
        self.grain_size_spin.setFixedWidth(68)
        self.grain_size_spin.setToolTip(
            "Grain duration. Longer grains = smoother/warmer, shorter = "
            "glitchier. Overlapping grains (Rate x Size) make a texture "
            "instead of a stutter. The grain is always kept inside the buffer."
        )
        grain_row.addWidget(self.grain_size_spin)

        grain_row.addWidget(QLabel("Pos"))
        self.grain_pos_spin = QDoubleSpinBox()
        self.grain_pos_spin.setDecimals(0)
        self.grain_pos_spin.setRange(0, 100)
        self.grain_pos_spin.setSingleStep(5)
        self.grain_pos_spin.setValue(LOOP_GRAIN_POS_DEFAULT * 100.0)
        self.grain_pos_spin.setSuffix(" %")
        self.grain_pos_spin.setFixedWidth(62)
        self.grain_pos_spin.setToolTip(
            "Centre of the read position in the loop (0 % = the very start). "
            "Motion scans around it, so Pos is the middle of the scan. If a "
            "take begins with silence, 0 % with Motion off is silent too -- "
            "move Pos or add Scatter."
        )
        grain_row.addWidget(self.grain_pos_spin)

        grain_row.addWidget(QLabel("Scatter"))
        self.grain_scatter_spin = QDoubleSpinBox()
        self.grain_scatter_spin.setDecimals(0)
        self.grain_scatter_spin.setRange(0, 100)
        self.grain_scatter_spin.setSingleStep(5)
        self.grain_scatter_spin.setValue(LOOP_GRAIN_SCATTER_DEFAULT * 100.0)
        self.grain_scatter_spin.setSuffix(" %")
        self.grain_scatter_spin.setFixedWidth(62)
        self.grain_scatter_spin.setToolTip(
            "Random spread around the travelling position, as a fraction of "
            "the loop. 0 % pins every grain to one slice; higher spreads the "
            "cloud."
        )
        grain_row.addWidget(self.grain_scatter_spin)

        grain_row.addWidget(QLabel("Pitch"))
        self.grain_pitch_spin = QDoubleSpinBox()
        self.grain_pitch_spin.setDecimals(2)
        self.grain_pitch_spin.setRange(0.25, 4.0)
        self.grain_pitch_spin.setSingleStep(0.05)
        self.grain_pitch_spin.setValue(LOOP_GRAIN_PITCH_DEFAULT)
        self.grain_pitch_spin.setFixedWidth(60)
        self.grain_pitch_spin.setToolTip(
            "Grain pitch ratio (0.25-4.0): the REQUESTED ratio. With Snap on "
            "it is snapped to the nearest ratio of the CURRENT JI set (x "
            "octaves) before the voice gets it, so 1.5 becomes a true 3/2 "
            "interval of the set; the status line shows the effective ratio. "
            "Snap off passes the raw ratio. The grains still go through the "
            "JI autotune."
        )
        grain_row.addWidget(self.grain_pitch_spin)

        grain_row.addWidget(QLabel("Rev%"))
        self.grain_reverse_spin = QDoubleSpinBox()
        self.grain_reverse_spin.setDecimals(0)
        self.grain_reverse_spin.setRange(0, 100)
        self.grain_reverse_spin.setSingleStep(5)
        self.grain_reverse_spin.setSuffix(" %")
        self.grain_reverse_spin.setFixedWidth(62)
        self.grain_reverse_spin.setToolTip(
            "Chance that a single grain plays backwards (TGrains negative "
            "rate), chosen per grain."
        )
        grain_row.addWidget(self.grain_reverse_spin)

        self.grain_hint = QLabel("")
        self.grain_hint.setMinimumWidth(180)
        grain_row.addWidget(self.grain_hint)
        grain_row.addStretch(1)
        layout.addLayout(grain_row)

        # --- Stage 8.6.1: ONE grain MOTION row (selected loop) -------------
        #  Motion moves the grain read position through the buffer, so the
        #  cloud evolves instead of repeating one slice.  Every control here
        #  is disabled while Grain is off.
        motion_row = QHBoxLayout()
        motion_row.setSpacing(4)
        motion_row.addWidget(QLabel("Motion"))
        self.grain_motion_combo = QComboBox()
        self.grain_motion_combo.addItems(list(LOOP_GRAIN_MOTIONS))
        self.grain_motion_combo.setCurrentText(LOOP_GRAIN_MOTION_DEFAULT)
        self.grain_motion_combo.setFixedWidth(76)
        self.grain_motion_combo.setToolTip(
            "How the grain read position travels through the loop: off = the "
            "position stays at Pos (the 8.6 behaviour), forward / backward "
            "scan the buffer, pingpong bounces, drift is a slow random walk. "
            "Motion is what makes the texture evolve; with off and a low "
            "Scatter the same slice repeats."
        )
        self.grain_motion_combo.currentTextChanged.connect(self._on_grain_motion)
        motion_row.addWidget(self.grain_motion_combo)

        motion_row.addWidget(QLabel("Speed"))
        self.grain_speed_spin = QDoubleSpinBox()
        self.grain_speed_spin.setDecimals(3)
        self.grain_speed_spin.setRange(LOOP_GRAIN_SPEED_MIN, LOOP_GRAIN_SPEED_MAX)
        self.grain_speed_spin.setSingleStep(0.005)
        self.grain_speed_spin.setValue(LOOP_GRAIN_SPEED_DEFAULT)
        self.grain_speed_spin.setFixedWidth(64)
        self.grain_speed_spin.setToolTip(
            "Scan speed in loop lengths per second: 0.03 crosses an 8 s loop "
            "in about 33 s. Higher = faster travel through the material."
        )
        motion_row.addWidget(self.grain_speed_spin)

        motion_row.addWidget(QLabel("Depth"))
        self.grain_depth_spin = QDoubleSpinBox()
        self.grain_depth_spin.setDecimals(0)
        self.grain_depth_spin.setRange(0, 100)
        self.grain_depth_spin.setSingleStep(5)
        self.grain_depth_spin.setValue(LOOP_GRAIN_DEPTH_DEFAULT * 100.0)
        self.grain_depth_spin.setSuffix(" %")
        self.grain_depth_spin.setFixedWidth(62)
        self.grain_depth_spin.setToolTip(
            "How far around Pos the scan travels (100 % = half the loop each "
            "way). 0 % pins the position even with Motion on."
        )
        motion_row.addWidget(self.grain_depth_spin)

        motion_row.addWidget(QLabel("Size Jit"))
        self.grain_size_jitter_spin = QDoubleSpinBox()
        self.grain_size_jitter_spin.setDecimals(0)
        self.grain_size_jitter_spin.setRange(0, 100)
        self.grain_size_jitter_spin.setSingleStep(5)
        self.grain_size_jitter_spin.setValue(
            LOOP_GRAIN_SIZE_JITTER_DEFAULT * 100.0
        )
        self.grain_size_jitter_spin.setSuffix(" %")
        self.grain_size_jitter_spin.setFixedWidth(62)
        self.grain_size_jitter_spin.setToolTip(
            "Per-grain size variation: thickens the cloud (grains of slightly "
            "different lengths) instead of a machine-gun of identical grains."
        )
        motion_row.addWidget(self.grain_size_jitter_spin)

        motion_row.addWidget(QLabel("Pitch Jit"))
        self.grain_pitch_jitter_spin = QDoubleSpinBox()
        self.grain_pitch_jitter_spin.setDecimals(0)
        self.grain_pitch_jitter_spin.setRange(0, 100)
        self.grain_pitch_jitter_spin.setSingleStep(5)
        self.grain_pitch_jitter_spin.setValue(
            LOOP_GRAIN_PITCH_JITTER_DEFAULT * 100.0
        )
        self.grain_pitch_jitter_spin.setSuffix(" %")
        self.grain_pitch_jitter_spin.setFixedWidth(62)
        self.grain_pitch_jitter_spin.setToolTip(
            "Per-grain pitch scatter: a shimmer/detune on top of the snapped "
            "ratio. 0 = every grain at exactly the effective ratio. Heavy "
            "values make Tartini warble (lower it if the cloud sounds unstable)."
        )
        motion_row.addWidget(self.grain_pitch_jitter_spin)

        self.grain_snap_checkbox = QCheckBox("Snap")
        self.grain_snap_checkbox.setChecked(bool(LOOP_GRAIN_PITCH_SNAP_DEFAULT))
        self.grain_snap_checkbox.setToolTip(
            "Snap the grain ratio to the nearest ratio of the CURRENT JI set "
            "(x octaves). The status line then shows the effective ratio "
            "(e.g. x3/2) instead of the raw float. A set change re-snaps a "
            "playing grain loop."
        )
        self.grain_snap_checkbox.toggled.connect(self._on_grain_snap)
        motion_row.addWidget(self.grain_snap_checkbox)

        motion_row.addWidget(QLabel("Smear"))
        self.grain_smear_spin = QDoubleSpinBox()
        self.grain_smear_spin.setDecimals(0)
        self.grain_smear_spin.setRange(0, 100)
        self.grain_smear_spin.setSingleStep(5)
        self.grain_smear_spin.setValue(LOOP_GRAIN_SMEAR_DEFAULT * 100.0)
        self.grain_smear_spin.setSuffix(" %")
        self.grain_smear_spin.setFixedWidth(62)
        self.grain_smear_spin.setToolTip(
            "Post-autotune diffusion (two short allpasses): blur/space on top "
            "of the tuned loop. 0 % is the dry accepted chain; low values are "
            "subtle."
        )
        motion_row.addWidget(self.grain_smear_spin)

        self.grain_freeze_checkbox = QCheckBox("Freeze")
        self.grain_freeze_checkbox.setToolTip(
            "Hold the motion where it is (the cloud keeps sounding). Unfreeze "
            "continues from the same position -- no jump."
        )
        self.grain_freeze_checkbox.toggled.connect(self._on_grain_freeze)
        motion_row.addWidget(self.grain_freeze_checkbox)

        self.grain_rate_spin.valueChanged.connect(
            lambda value: self._on_grain_value(
                "grain_rate", round(float(value), 1)
            )
        )
        self.grain_size_spin.valueChanged.connect(
            lambda value: self._on_grain_value(
                "grain_size", round(float(value), 0)
            )
        )
        self.grain_pos_spin.valueChanged.connect(
            lambda value: self._on_grain_value(
                "grain_pos", round(float(value) / 100.0, 3)
            )
        )
        self.grain_scatter_spin.valueChanged.connect(
            lambda value: self._on_grain_value(
                "grain_scatter", round(float(value) / 100.0, 3)
            )
        )
        self.grain_pitch_spin.valueChanged.connect(
            lambda value: self._on_grain_value(
                "grain_pitch", round(float(value), 2)
            )
        )
        self.grain_reverse_spin.valueChanged.connect(
            lambda value: self._on_grain_value(
                "grain_reverse", round(float(value) / 100.0, 3)
            )
        )
        self.grain_speed_spin.valueChanged.connect(
            lambda value: self._on_grain_value(
                "grain_speed", round(float(value), 3)
            )
        )
        self.grain_depth_spin.valueChanged.connect(
            lambda value: self._on_grain_value(
                "grain_depth", round(float(value) / 100.0, 3)
            )
        )
        self.grain_size_jitter_spin.valueChanged.connect(
            lambda value: self._on_grain_value(
                "grain_size_jitter", round(float(value) / 100.0, 3)
            )
        )
        self.grain_pitch_jitter_spin.valueChanged.connect(
            lambda value: self._on_grain_value(
                "grain_pitch_jitter", round(float(value) / 100.0, 3)
            )
        )
        self.grain_smear_spin.valueChanged.connect(
            lambda value: self._on_grain_value(
                "grain_smear", round(float(value) / 100.0, 3)
            )
        )
        motion_row.addStretch(1)
        layout.addLayout(motion_row);

        # --- Stage 8.6.2: ONE MORPH row (dry loop <-> grain, selected loop) -
        #  Morph is the main gesture: 0 is the ORIGINAL loop, 1 is the full
        #  grain cloud, and everything in between is the smear of the two.
        #  Melt makes the loop start at the original and ramp to the Morph
        #  value; Filter is a moving post-autotune filter that fuses the two
        #  layers (it also works on the plain loop).
        morph_row = QHBoxLayout()
        morph_row.setSpacing(4)
        morph_row.addWidget(QLabel("Morph"))
        self.grain_morph_spin = QDoubleSpinBox()
        self.grain_morph_spin.setDecimals(0)
        self.grain_morph_spin.setRange(0, 100)
        self.grain_morph_spin.setSingleStep(5)
        self.grain_morph_spin.setValue(LOOP_GRAIN_MORPH_DEFAULT * 100.0)
        self.grain_morph_spin.setSuffix(" %")
        self.grain_morph_spin.setFixedWidth(62)
        self.grain_morph_spin.setToolTip(
            "Dry loop <-> grain cloud. 0 % = the original loop (normal "
            "playback, through the JI autotune), 100 % = the full grain cloud, "
            "in between = the two smeared together. Needs Grain on."
        )
        morph_row.addWidget(self.grain_morph_spin)

        self.grain_melt_checkbox = QCheckBox("Melt")
        self.grain_melt_checkbox.setToolTip(
            "Start from the original and progressively melt into the grains: "
            "Play (or ticking Melt) ramps the Morph amount from 0 to the Morph "
            "slider over about 4 s. Untick to melt back to the original. "
            "Changing Morph while melting re-targets the ramp."
        )
        morph_row.addWidget(self.grain_melt_checkbox)

        morph_row.addWidget(QLabel("Filter"))
        self.grain_filter_spin = QDoubleSpinBox()
        self.grain_filter_spin.setDecimals(0)
        self.grain_filter_spin.setRange(0, 100)
        self.grain_filter_spin.setSingleStep(5)
        self.grain_filter_spin.setValue(LOOP_GRAIN_FILTER_DEFAULT * 100.0)
        self.grain_filter_spin.setSuffix(" %")
        self.grain_filter_spin.setFixedWidth(62)
        self.grain_filter_spin.setToolTip(
            "Moving melt filter (post-autotune): a resonant lowpass and a low "
            "cut, each on its own slow LFO, sweep the dry+grain mix so the two "
            "layers fuse into one texture. 0 % is bypass (the dry chain); it "
            "works on the plain loop too."
        )
        morph_row.addWidget(self.grain_filter_spin)

        morph_row.addWidget(QLabel("F.Rate"))
        self.grain_filter_rate_spin = QDoubleSpinBox()
        self.grain_filter_rate_spin.setDecimals(2)
        self.grain_filter_rate_spin.setRange(
            LOOP_GRAIN_FILTER_RATE_MIN, 1.0
        )
        self.grain_filter_rate_spin.setSingleStep(0.01)
        self.grain_filter_rate_spin.setValue(LOOP_GRAIN_FILTER_RATE_DEFAULT)
        self.grain_filter_rate_spin.setFixedWidth(58)
        self.grain_filter_rate_spin.setToolTip(
            "Filter movement speed in Hz: 0.12 is a slow sweep (about 8 s per "
            "cycle); higher is a more obvious wobble."
        )
        morph_row.addWidget(self.grain_filter_rate_spin)

        # Stage 8.7: the loop's own grain-breath LFO (independent of the
        # bridge's breathing rhythm engine; 0 % is an exact bypass).
        morph_row.addWidget(QLabel("Breath"))
        self.grain_breath_spin = QDoubleSpinBox()
        self.grain_breath_spin.setDecimals(0)
        self.grain_breath_spin.setRange(0, 100)
        self.grain_breath_spin.setSingleStep(5)
        self.grain_breath_spin.setValue(LOOP_GRAIN_BREATH_DEFAULT * 100.0)
        self.grain_breath_spin.setSuffix(" %")
        self.grain_breath_spin.setFixedWidth(60)
        self.grain_breath_spin.setToolTip(
            "The loop's own grain breath: density and grain size swell on a "
            "slow LFO inside the voice. 0 % is exactly unchanged. Needs Grain "
            "on."
        )
        morph_row.addWidget(self.grain_breath_spin)

        morph_row.addWidget(QLabel("B.Rate"))
        self.grain_breath_rate_spin = QDoubleSpinBox()
        self.grain_breath_rate_spin.setDecimals(2)
        self.grain_breath_rate_spin.setRange(
            LOOP_GRAIN_BREATH_RATE_MIN, LOOP_GRAIN_BREATH_RATE_MAX
        )
        self.grain_breath_rate_spin.setSingleStep(0.01)
        self.grain_breath_rate_spin.setValue(LOOP_GRAIN_BREATH_RATE_DEFAULT)
        self.grain_breath_rate_spin.setFixedWidth(58)
        self.grain_breath_rate_spin.setToolTip(
            "Grain-breath speed in Hz: 0.03 is a long breath, 0.3 a pulse."
        )
        morph_row.addWidget(self.grain_breath_rate_spin)

        self.grain_morph_spin.valueChanged.connect(
            lambda value: self._on_grain_value(
                "grain_morph", round(float(value) / 100.0, 3)
            )
        )
        self.grain_melt_checkbox.toggled.connect(self._on_grain_melt)
        self.grain_filter_spin.valueChanged.connect(
            lambda value: self._on_grain_value(
                "grain_filter", round(float(value) / 100.0, 3)
            )
        )
        self.grain_filter_rate_spin.valueChanged.connect(
            lambda value: self._on_grain_value(
                "grain_filter_rate", round(float(value), 2)
            )
        )
        self.grain_breath_spin.valueChanged.connect(
            lambda value: self._on_grain_value(
                "grain_breath", round(float(value) / 100.0, 3)
            )
        )
        self.grain_breath_rate_spin.valueChanged.connect(
            lambda value: self._on_grain_value(
                "grain_breath_rate", round(float(value), 2)
            )
        )

        morph_row.addStretch(1)
        layout.addLayout(morph_row)

        # --- Stage 9.0: ONE HARM row (post-PitchShift delay FX) -----------
        #  Harm = off / Cascade (one captured segment, harmonics entering in
        #  order) / Delay (continuous harmonized buffer delay).  Mix/Time/
        #  TimeSync/Steps/Fall work in both active modes; Space/Auto/Fire are
        #  Cascade-only.  Time can be FREE seconds or a fixed musical division
        #  (owner follow-up: quarter notes, dotted, triplets, a bar); the
        #  seconds spin becomes a read-only display of the tempo-derived time.
        harm_row = QHBoxLayout()
        harm_row.setSpacing(4)
        harm_row.addWidget(QLabel("Harm"))
        self.harm_mode_combo = QComboBox()
        self.harm_mode_combo.addItems(["off", "Cascade", "Delay"])
        self.harm_mode_combo.setFixedWidth(82)
        self.harm_mode_combo.setToolTip(
            "Per-loop harmonic-series delay, AFTER the JI PitchShift (it uses "
            "the tuned loop, never a second autotune). off = no FX synth. "
            "Cascade = capture a segment and play it at 1x, 2x, 3x ... in "
            "order on Fire/Auto. Delay = a continuous harmonized buffer delay "
            "(varispeed character, no trigger)."
        )
        self.harm_mode_combo.currentTextChanged.connect(self._on_harm_mode)
        harm_row.addWidget(self.harm_mode_combo)

        harm_row.addWidget(QLabel("Mix"))
        self.harm_mix_spin = QDoubleSpinBox()
        self.harm_mix_spin.setDecimals(0)
        self.harm_mix_spin.setRange(0, 100)
        self.harm_mix_spin.setSingleStep(5)
        self.harm_mix_spin.setValue(osc_control.LOOP_HARM_MIX_DEFAULT * 100.0)
        self.harm_mix_spin.setSuffix(" %")
        self.harm_mix_spin.setFixedWidth(62)
        self.harm_mix_spin.setToolTip(
            "Wet return level. The dry loop is always upstream, so 0 % is the "
            "accepted loop and 100 % is the full harmonic stack."
        )
        harm_row.addWidget(self.harm_mix_spin)

        harm_row.addWidget(QLabel("Time"))
        self.harm_time_spin = QDoubleSpinBox()
        self.harm_time_spin.setDecimals(2)
        self.harm_time_spin.setRange(
            osc_control.LOOP_HARM_TIME_MIN, osc_control.LOOP_HARM_TIME_MAX
        )
        self.harm_time_spin.setSingleStep(0.05)
        self.harm_time_spin.setValue(osc_control.LOOP_HARM_TIME_DEFAULT)
        self.harm_time_spin.setSuffix(" s")
        self.harm_time_spin.setFixedWidth(72)
        self.harm_time_spin.setToolTip(
            "Cascade segment length / Delay base window in seconds "
            "(0.05-2.00, the FX buffer size). Disabled when TimeSync is a "
            "musical division; the spin then shows the tempo-derived seconds."
        )
        harm_row.addWidget(self.harm_time_spin)

        self.harm_time_sync_combo = QComboBox()
        self.harm_time_sync_combo.addItems(
            list(osc_control.LOOP_HARM_TIME_SYNCS)
        )
        self.harm_time_sync_combo.setFixedWidth(64)
        self.harm_time_sync_combo.setToolTip(
            "Fixed (tempo-locked) delay time: free = use the seconds spin; "
            "1/4 = a quarter note, 1/4d = dotted, 1/4t = triplet, 1 bar = the "
            "current meter. Values are clamped to the 2 s FX buffer."
        )
        harm_row.addWidget(self.harm_time_sync_combo)

        harm_row.addWidget(QLabel("Steps"))
        self.harm_steps_spin = QSpinBox()
        self.harm_steps_spin.setRange(
            osc_control.LOOP_HARM_STEPS_MIN, osc_control.LOOP_HARM_STEPS_MAX
        )
        self.harm_steps_spin.setValue(osc_control.LOOP_HARM_STEPS_DEFAULT)
        self.harm_steps_spin.setFixedWidth(48)
        self.harm_steps_spin.setToolTip(
            "Harmonics 1..N: 1 = the fundamental only, 2 adds the octave, "
            "3 the 12th, 4 the 16th ... up to 8 (all exact integer multiples)."
        )
        harm_row.addWidget(self.harm_steps_spin)

        harm_row.addWidget(QLabel("Space"))
        self.harm_spacing_spin = QDoubleSpinBox()
        self.harm_spacing_spin.setDecimals(0)
        self.harm_spacing_spin.setRange(0, 1000)
        self.harm_spacing_spin.setSingleStep(10)
        self.harm_spacing_spin.setValue(
            osc_control.LOOP_HARM_SPACING_DEFAULT * 1000.0
        )
        self.harm_spacing_spin.setSuffix(" ms")
        self.harm_spacing_spin.setFixedWidth(74)
        self.harm_spacing_spin.setToolTip(
            "Cascade entry spacing (0-1000 ms): each harmonic enters this long "
            "after the previous one. 0 = all harmonics together. Inert in "
            "Delay mode."
        )
        harm_row.addWidget(self.harm_spacing_spin)

        harm_row.addWidget(QLabel("Fall"))
        self.harm_falloff_spin = QDoubleSpinBox()
        self.harm_falloff_spin.setDecimals(0)
        self.harm_falloff_spin.setRange(0, 100)
        self.harm_falloff_spin.setSingleStep(5)
        self.harm_falloff_spin.setValue(
            osc_control.LOOP_HARM_FALLOFF_DEFAULT * 100.0
        )
        self.harm_falloff_spin.setSuffix(" %")
        self.harm_falloff_spin.setFixedWidth(62)
        self.harm_falloff_spin.setToolTip(
            "High-harmonic roll-off: amplitude = 1 / k^(0.5 + 1.5 * Fall). "
            "0 % is gentle (1/sqrt(k)); 100 % is 1/k^2 (dark, fundamental-"
            "heavy)."
        )
        harm_row.addWidget(self.harm_falloff_spin)

        self.harm_auto_checkbox = QCheckBox("Auto")
        self.harm_auto_checkbox.setChecked(bool(osc_control.LOOP_HARM_AUTO_DEFAULT))
        self.harm_auto_checkbox.setToolTip(
            "Cascade auto-fire: the first capture fires after one segment "
            "length, then after each cascade. Inert in Delay mode."
        )
        harm_row.addWidget(self.harm_auto_checkbox)

        self.harm_fire_button = QPushButton("Fire")
        self.harm_fire_button.setMinimumHeight(20)
        self.harm_fire_button.setMinimumWidth(52)
        self.harm_fire_button.setToolTip(
            "Fire one Cascade pass now (enabled only in Cascade while the "
            "selected loop is playing). It sends /ji/loop/fx_fire, not an "
            "edit."
        )
        harm_row.addWidget(self.harm_fire_button)

        self.loop_edit_label = QLabel("Loop 1 · ×1/1 · count-in off · gain 0.70")
        self.loop_edit_label.setMinimumWidth(300)
        harm_row.addWidget(self.loop_edit_label)
        harm_row.addStretch(1)
        layout.addLayout(harm_row)

        self.harm_time_sync_combo.currentTextChanged.connect(
            self._on_harm_time_sync
        )
        self.harm_mix_spin.valueChanged.connect(
            lambda value: self._on_harm_value(
                "harm_mix", round(float(value) / 100.0, 3)
            )
        )
        self.harm_time_spin.valueChanged.connect(
            lambda value: self._on_harm_value(
                "harm_time", round(float(value), 3)
            )
        )
        self.harm_steps_spin.valueChanged.connect(
            lambda value: self._on_harm_value("harm_steps", int(value))
        )
        self.harm_spacing_spin.valueChanged.connect(
            lambda value: self._on_harm_value(
                "harm_spacing", round(float(value) / 1000.0, 3)
            )
        )
        self.harm_falloff_spin.valueChanged.connect(
            lambda value: self._on_harm_value(
                "harm_falloff", round(float(value) / 100.0, 3)
            )
        )
        self.harm_auto_checkbox.toggled.connect(
            lambda checked: self._on_harm_value("harm_auto", 1 if checked else 0)
        )
        self.harm_fire_button.clicked.connect(self._on_harm_fire)

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

        # --- Stage 8.7: ONE Breath row (the whole grid breathes) -----------
        breath_row = QHBoxLayout()
        breath_row.setSpacing(4)
        self.breath_enable_checkbox = QCheckBox("Breath")
        self.breath_enable_checkbox.setChecked(False)
        self.breath_enable_checkbox.setToolTip(
            "Make the played grid breathe: events arrive early/late and "
            "velocities swell in a slow, physical cycle. The canonical tempo "
            "and bar grid never move. Off is exactly the old timing."
        )
        self.breath_enable_checkbox.toggled.connect(self._on_breath_enabled)
        breath_row.addWidget(self.breath_enable_checkbox)

        breath_row.addWidget(QLabel("Depth"))
        self.breath_depth_spin = QSpinBox()
        self.breath_depth_spin.setRange(0, 100)
        self.breath_depth_spin.setValue(int(round(BREATH_DEFAULT_DEPTH * 100)))
        self.breath_depth_spin.setSuffix(" %")
        self.breath_depth_spin.setFixedWidth(58)
        self.breath_depth_spin.setToolTip(
            "How far events move early/late: up to +/- half a 16th at 100 %."
        )
        self.breath_depth_spin.valueChanged.connect(
            lambda value: self._on_breath_value(
                "depth", round(float(value) / 100.0, 3)
            )
        )
        breath_row.addWidget(self.breath_depth_spin)

        breath_row.addWidget(QLabel("Period"))
        self.breath_period_spin = QSpinBox()
        self.breath_period_spin.setRange(
            BREATH_PERIOD_BARS_MIN, BREATH_PERIOD_BARS_MAX
        )
        self.breath_period_spin.setValue(BREATH_DEFAULT_PERIOD_BARS)
        self.breath_period_spin.setSuffix(" b")
        self.breath_period_spin.setFixedWidth(54)
        self.breath_period_spin.setToolTip(
            "One full inhale + exhale, in bars (1 = a fast pulse, 16 = a long "
            "swell)."
        )
        self.breath_period_spin.valueChanged.connect(
            lambda value: self._on_breath_value("period_bars", int(value))
        )
        breath_row.addWidget(self.breath_period_spin)

        breath_row.addWidget(QLabel("Shape"))
        self.breath_shape_combo = QComboBox()
        self.breath_shape_combo.addItems(list(BREATH_SHAPES))
        self.breath_shape_combo.setCurrentText(BREATH_DEFAULT_SHAPE)
        self.breath_shape_combo.setFixedWidth(76)
        self.breath_shape_combo.setToolTip(
            "Where the breath accelerates: sine/triangle are even, inhale "
            "fills fast and releases slowly, exhale the opposite."
        )
        self.breath_shape_combo.currentTextChanged.connect(
            lambda text: self._on_breath_value("shape", str(text))
        )
        breath_row.addWidget(self.breath_shape_combo)

        breath_row.addWidget(QLabel("Intensity"))
        self.breath_intensity_spin = QSpinBox()
        self.breath_intensity_spin.setRange(0, 100)
        self.breath_intensity_spin.setValue(
            int(round(BREATH_DEFAULT_INTENSITY * 100))
        )
        self.breath_intensity_spin.setSuffix(" %")
        self.breath_intensity_spin.setFixedWidth(58)
        self.breath_intensity_spin.setToolTip(
            "Velocity/expression swell. 0 % is timing only; 100 % is a strong "
            "swell."
        )
        self.breath_intensity_spin.valueChanged.connect(
            lambda value: self._on_breath_value(
                "intensity", round(float(value) / 100.0, 3)
            )
        )
        breath_row.addWidget(self.breath_intensity_spin)

        breath_row.addWidget(QLabel("Phase"))
        self.breath_phase_spin = QSpinBox()
        self.breath_phase_spin.setRange(0, 100)
        self.breath_phase_spin.setValue(int(round(BREATH_DEFAULT_PHASE * 100)))
        self.breath_phase_spin.setSuffix(" %")
        self.breath_phase_spin.setFixedWidth(58)
        self.breath_phase_spin.setToolTip(
            "Rotate the breath against the bar (0 % starts at the bottom of "
            "the inhale)."
        )
        self.breath_phase_spin.valueChanged.connect(
            lambda value: self._on_breath_value(
                "phase", round(float(value) / 100.0, 3)
            )
        )
        breath_row.addWidget(self.breath_phase_spin)

        breath_row.addWidget(QLabel("Arp local"))
        self.breath_arp_spin = QSpinBox()
        self.breath_arp_spin.setRange(0, 100)
        self.breath_arp_spin.setValue(int(round(BREATH_DEFAULT_ARP_LOCAL * 100)))
        self.breath_arp_spin.setSuffix(" %")
        self.breath_arp_spin.setFixedWidth(58)
        self.breath_arp_spin.setToolTip(
            "The arp's extra breath depth. With Arp sync off it breathes half "
            "a cycle out of phase (against the groove); with Arp sync on it "
            "uses the master phase and is just deeper."
        )
        self.breath_arp_spin.valueChanged.connect(
            lambda value: self._on_breath_value(
                "arp_local", round(float(value) / 100.0, 3)
            )
        )
        breath_row.addWidget(self.breath_arp_spin)

        self.breath_arp_sync_checkbox = QCheckBox("Arp sync")
        self.breath_arp_sync_checkbox.setChecked(BREATH_DEFAULT_ARP_SYNC)
        self.breath_arp_sync_checkbox.setToolTip(
            "Use the master breath phase for the arp instead of its own "
            "half-cycle phase: the arp inhales/exhales with the rest of the "
            "groove. Arp local still adds depth. (Breath phase, not the Arp "
            "group's beat Sync.)"
        )
        self.breath_arp_sync_checkbox.toggled.connect(
            self._on_breath_arp_sync
        )
        breath_row.addWidget(self.breath_arp_sync_checkbox)

        self.breath_hint = QLabel("breath off")
        self.breath_hint.setMinimumWidth(100)
        breath_row.addWidget(self.breath_hint)
        breath_row.addStretch(1)
        layout.addLayout(breath_row)

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
        # Stage 8.7: the editable one-bar hit row lives on the status row.
        self.bass_hits_checkbox = QCheckBox("Hits")
        self.bass_hits_checkbox.setChecked(BASS_HITS_DEFAULT_ENABLED)
        self.bass_hits_checkbox.setToolTip(
            "One editable row of bass hits per bar (no pitch, no per-step "
            "velocity). While Hits is on, Len is not used: the row is one bar "
            "of the current Grid. Enabling seeds the row from the generated "
            "pattern if it is empty."
        )
        self.bass_hits_checkbox.toggled.connect(self._on_bass_hits_enabled)
        status_row.addWidget(self.bass_hits_checkbox)
        self.bass_hits_copy_button = QPushButton("Copy")
        self.bass_hits_copy_button.setMinimumHeight(22)
        self.bass_hits_copy_button.setToolTip(
            "Seed the hit row from the generated pattern of bar 1 (the pitch, "
            "accent and gate stay as they are)."
        )
        self.bass_hits_copy_button.clicked.connect(
            lambda: self._guard(self.controller.bass_copy_hits)
        )
        status_row.addWidget(self.bass_hits_copy_button)
        self.bass_hits_clear_button = QPushButton("Clear")
        self.bass_hits_clear_button.setMinimumHeight(22)
        self.bass_hits_clear_button.setToolTip(
            "Turn every hit off (deliberate silence); use Copy to get a row "
            "back."
        )
        self.bass_hits_clear_button.clicked.connect(
            lambda: self._guard(self.controller.bass_clear_hits)
        )
        status_row.addWidget(self.bass_hits_clear_button)
        status_row.addWidget(self.bass_label)
        status_row.addWidget(self.bass_note_label)
        status_row.addStretch(1)
        layout.addLayout(status_row)

        # Hidden by default; showing it does not change the default height.
        self.bass_hits_scroll = QScrollArea()
        self.bass_hits_scroll.setWidgetResizable(True)
        self.bass_hits_scroll.setVisible(False)
        self.bass_hits_scroll.setMaximumHeight(34)
        self.bass_hits_container = QWidget()
        self.bass_hits_layout = QHBoxLayout(self.bass_hits_container)
        self.bass_hits_layout.setContentsMargins(0, 0, 0, 0)
        self.bass_hits_layout.setSpacing(2)
        self.bass_hits_scroll.setWidget(self.bass_hits_container)
        layout.addWidget(self.bass_hits_scroll)
        self.bass_hit_buttons: dict[int, QPushButton] = {}
        self._bass_hits_built = 0

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
            self.bass_hits_checkbox,
            self.bass_hits_copy_button,
            self.bass_hits_clear_button,
            self.bass_hits_scroll,
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

    # -- Stage 8.7 bass hit row -----------------------------------------

    def _on_bass_hits_enabled(self, checked: bool) -> None:
        if self._bass_hits_sync:
            return
        self._guard(self.controller.bass_set_hits_enabled, bool(checked))
        self.bass_hits_scroll.setVisible(bool(checked))
        self._set_message(
            "Bass hits on (row seeded from the pattern)" if checked
            else "Bass hits off (generated pattern)"
        )

    def _on_bass_hit_toggled(self, index: int, checked: bool) -> None:
        if self._bass_hits_sync:
            return
        self._guard(self.controller.bass_set_hit, int(index), bool(checked))

    def _rebuild_bass_hits(self, count: int) -> None:
        """(Re)build the one-bar hit buttons for ``count`` cells."""

        while self.bass_hits_layout.count():
            item = self.bass_hits_layout.takeAt(0)
            widget = item.widget() if item is not None else None
            if widget is not None:
                widget.deleteLater()
        self.bass_hit_buttons = {}
        for index in range(count):
            button = QPushButton(str(index))
            button.setCheckable(True)
            button.setFixedSize(30, 26)
            button.setToolTip(f"Hit {index} of the bar")
            button.toggled.connect(
                lambda checked, i=index: self._on_bass_hit_toggled(i, checked)
            )
            self.bass_hits_layout.addWidget(button)
            self.bass_hit_buttons[index] = button
        self.bass_hits_layout.addStretch(1)
        self._bass_hits_built = count

    def _sync_bass_hits(self) -> None:
        """Mirror ``/ji/bass/hits`` into the Hits checkbox and its row."""

        enabled = bool(self.store.bass_hits_enabled)
        hits = self.store.bass_hits or ()
        was_sync = self._bass_hits_sync
        self._bass_hits_sync = True
        try:
            if self.bass_hits_checkbox.isChecked() != enabled:
                self.bass_hits_checkbox.setChecked(enabled)
        finally:
            self._bass_hits_sync = was_sync
        if len(hits) != self._bass_hits_built:
            self._rebuild_bass_hits(len(hits))
        for index, button in self.bass_hit_buttons.items():
            wanted = bool(hits[index]) if index < len(hits) else False
            button.blockSignals(True)
            try:
                button.setChecked(wanted)
            finally:
                button.blockSignals(False)
        self.bass_hits_scroll.setVisible(bool(enabled and hits))

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

    def _on_lead_note_len(self, checked: bool) -> None:
        if self._lead_sync:
            return
        self._guard(
            self.controller.set_lead_setting,
            "gate_mode",
            "note" if checked else "beat",
        )
        self._lead_message(
            f"Lead gate mode {'note length' if checked else 'beat'}"
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
            self.lead_note_len_checkbox.setChecked(bool(lead.note_gate))
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
            line = "Lead: " + lead.status_line()
            status = self.store.lead_input
            if status is not None and (
                status.melody_name or status.source != "generated"
            ):
                line += f" | {status.source}"
                if status.melody_name:
                    line += f' "{status.melody_name}"'
            self.lead_label.setText(line)
        note = self.store.lead_note
        if note is None:
            self.lead_note_label.setText("Last: --")
        else:
            self.lead_note_label.setText(
                f"Last: {note.note_name} {note.hz:.2f} Hz"
            )

    # -- Stage 8.3d drone mode controls ----------------------------------

    # -- Stage 9.1 lead input --------------------------------------------

    def _build_lead_input_group(self) -> QGroupBox:
        """One compact Lead input group (two rows + status)."""

        group = QGroupBox("Lead input")
        layout = QVBoxLayout(group)
        layout.setContentsMargins(4, 2, 4, 2)
        layout.setSpacing(2)
        self._lead_input_sync = True

        row = QHBoxLayout()
        row.setSpacing(3)
        row.addWidget(QLabel("Port:"))
        self.lead_input_port_label = QLabel("--")
        self.lead_input_port_label.setMinimumWidth(140)
        self.lead_input_port_label.setToolTip(
            "The controller the bridge opened. Edit config/lead_input_config.json "
            "(or --lead-in-port) and press Refresh to change it."
        )
        row.addWidget(self.lead_input_port_label)
        self.lead_input_refresh_button = QPushButton("Refresh")
        self.lead_input_refresh_button.setMinimumHeight(24)
        self.lead_input_refresh_button.setToolTip(
            "Close and re-open the configured MIDI input."
        )
        self.lead_input_refresh_button.clicked.connect(self._on_lead_input_refresh)
        row.addWidget(self.lead_input_refresh_button)

        self.lead_input_keys_checkbox = QCheckBox("Keys")
        self.lead_input_keys_checkbox.setToolTip(
            "QWERTY a w s e d f t g y h u j play the mapped line; z/x shift the "
            "octave. GUI-local: keys only sound while Live or Rec is on."
        )
        self.lead_input_keys_checkbox.toggled.connect(self._on_lead_input_keys)
        row.addWidget(self.lead_input_keys_checkbox)

        self.lead_input_live_checkbox = QCheckBox("Live")
        self.lead_input_live_checkbox.setToolTip(
            "Monitor the input: the sequencer stops while Live is on and "
            "resumes on the next bar line when it is turned off."
        )
        self.lead_input_live_checkbox.toggled.connect(self._on_lead_input_live)
        row.addWidget(self.lead_input_live_checkbox)

        self.lead_input_record_button = QPushButton("Rec")
        self.lead_input_record_button.setMinimumHeight(24)
        self.lead_input_record_button.setToolTip(
            "Record the played line from the next bar line (needs Play)."
        )
        self.lead_input_record_button.clicked.connect(self._on_lead_input_record)
        row.addWidget(self.lead_input_record_button)

        self.lead_input_stop_button = QPushButton("Stop")
        self.lead_input_stop_button.setMinimumHeight(24)
        self.lead_input_stop_button.setToolTip(
            "Stop the count-in, or keep the partial take."
        )
        self.lead_input_stop_button.clicked.connect(self._on_lead_input_stop)
        row.addWidget(self.lead_input_stop_button)

        self.lead_input_revert_button = QPushButton("Revert")
        self.lead_input_revert_button.setMinimumHeight(24)
        self.lead_input_revert_button.setToolTip(
            "Restore the recorded base (all variations undone)."
        )
        self.lead_input_revert_button.clicked.connect(self._on_lead_input_revert)
        row.addWidget(self.lead_input_revert_button)

        self.lead_input_clear_button = QPushButton("Clear")
        self.lead_input_clear_button.setMinimumHeight(24)
        self.lead_input_clear_button.setToolTip(
            "Clear the melody and return to the generated line."
        )
        self.lead_input_clear_button.clicked.connect(self._on_lead_input_clear)
        row.addWidget(self.lead_input_clear_button)
        row.addStretch(1)
        layout.addLayout(row)

        row2 = QHBoxLayout()
        row2.setSpacing(3)
        self.lead_input_quant_knob = Knob(
            "Quant",
            0.0,
            1.0,
            LEAD_INPUT_QUANTIZE_DEFAULT,
            steps=100,
            fmt="{:.0%}",
        )
        self.lead_input_quant_knob.valueChanged.connect(
            self._on_lead_input_quantize
        )
        row2.addWidget(self.lead_input_quant_knob)

        row2.addWidget(QLabel("Count-in"))
        self.lead_input_count_in_combo = QComboBox()
        self.lead_input_count_in_combo.addItems(list(LEAD_INPUT_COUNT_INS))
        self.lead_input_count_in_combo.setCurrentText(
            LEAD_INPUT_COUNT_IN_DEFAULT
        )
        self.lead_input_count_in_combo.setToolTip(
            "Silent count-in before the take starts on the next bar line."
        )
        self.lead_input_count_in_combo.currentTextChanged.connect(
            self._on_lead_input_count_in
        )
        row2.addWidget(self.lead_input_count_in_combo)

        row2.addWidget(QLabel("Bars"))
        self.lead_input_bars_spin = QSpinBox()
        self.lead_input_bars_spin.setRange(
            LEAD_INPUT_BARS_MIN, LEAD_INPUT_BARS_MAX
        )
        self.lead_input_bars_spin.setValue(LEAD_INPUT_BARS_DEFAULT)
        self.lead_input_bars_spin.setFixedWidth(46)
        self.lead_input_bars_spin.setSuffix(" b")
        self.lead_input_bars_spin.setToolTip("Take length in bars (1..8).")
        self.lead_input_bars_spin.valueChanged.connect(
            self._on_lead_input_bars
        )
        row2.addWidget(self.lead_input_bars_spin)

        row2.addWidget(QLabel("Melody"))
        self.lead_melody_combo = QComboBox()
        self.lead_melody_combo.setMinimumWidth(120)
        self.lead_melody_combo.setToolTip(
            "Saved melodies from the bridge (melodies/<name>.json)."
        )
        self.lead_melody_combo.currentTextChanged.connect(
            self._on_melody_combo_changed
        )
        row2.addWidget(self.lead_melody_combo)

        row2.addWidget(QLabel("Name"))
        self.lead_melody_name_edit = QLineEdit()
        self.lead_melody_name_edit.setMinimumWidth(110)
        self.lead_melody_name_edit.setPlaceholderText("melody name")
        self.lead_melody_name_edit.setToolTip(
            "Save name (1-40 characters, no path separators)."
        )
        self.lead_melody_name_edit.textChanged.connect(
            self._on_melody_name_changed
        )
        row2.addWidget(self.lead_melody_name_edit)

        self.lead_melody_save_button = QPushButton("Save")
        self.lead_melody_save_button.setMinimumHeight(24)
        self.lead_melody_save_button.clicked.connect(self._on_melody_save)
        row2.addWidget(self.lead_melody_save_button)

        self.lead_melody_load_button = QPushButton("Load")
        self.lead_melody_load_button.setMinimumHeight(24)
        self.lead_melody_load_button.clicked.connect(self._on_melody_load)
        row2.addWidget(self.lead_melody_load_button)

        self.lead_melody_delete_button = QPushButton("Del")
        self.lead_melody_delete_button.setMinimumHeight(24)
        self.lead_melody_delete_button.clicked.connect(self._on_melody_delete)
        row2.addWidget(self.lead_melody_delete_button)
        row2.addStretch(1)
        layout.addLayout(row2)

        status_row = QHBoxLayout()
        status_row.setSpacing(10)
        self.lead_input_status_label = QLabel("Input: waiting for bridge")
        self.melody_status_label = QLabel("Melody: --")
        status_row.addWidget(self.lead_input_status_label)
        status_row.addWidget(self.melody_status_label)
        status_row.addStretch(1)
        layout.addLayout(status_row)

        self.lead_input_controls: list[QWidget] = [
            self.lead_input_port_label,
            self.lead_input_refresh_button,
            self.lead_input_keys_checkbox,
            self.lead_input_live_checkbox,
            self.lead_input_record_button,
            self.lead_input_stop_button,
            self.lead_input_revert_button,
            self.lead_input_clear_button,
            self.lead_input_quant_knob,
            self.lead_input_count_in_combo,
            self.lead_input_bars_spin,
            self.lead_melody_combo,
            self.lead_melody_name_edit,
            self.lead_melody_save_button,
            self.lead_melody_load_button,
            self.lead_melody_delete_button,
        ]
        self._lead_input_sync = False
        self._sync_lead_input_controls()
        self._sync_melody_list()
        self._update_lead_input_label()
        return group

    def _on_lead_input_refresh(self) -> None:
        self._guard(self.controller.refresh_lead_input)
        self._set_message("Lead input refresh requested")

    def _on_lead_input_keys(self, checked: bool) -> None:
        if not checked:
            self._release_keys()
        self._set_message(f"Keys {'on' if checked else 'off'}")

    def _on_lead_input_live(self, checked: bool) -> None:
        if self._lead_input_sync:
            return
        self._guard(self.controller.set_lead_input_enabled, bool(checked))
        self._set_message(f"Live {'on' if checked else 'off'}")

    def _on_lead_input_record(self) -> None:
        self._guard(self.controller.lead_input_record)
        self._set_message("Recording requested")

    def _on_lead_input_stop(self) -> None:
        self._guard(self.controller.lead_input_stop)
        self._set_message("Recording stopped")

    def _on_lead_input_revert(self) -> None:
        self._guard(self.controller.lead_input_revert)
        self._set_message("Revert to the recorded base")

    def _on_lead_input_clear(self) -> None:
        self._guard(self.controller.lead_input_clear)
        self._set_message("Melody cleared; generated line restored")

    def _on_lead_input_quantize(self, value: float) -> None:
        if self._lead_input_sync:
            return
        self._pending[("lead_input", "quantize")] = float(value)

    def _on_lead_input_count_in(self, text: str) -> None:
        if self._lead_input_sync:
            return
        self._guard(self.controller.set_lead_input_count_in, text)

    def _on_lead_input_bars(self, value: int) -> None:
        if self._lead_input_sync:
            return
        self._guard(self.controller.set_lead_input_bars, int(value))

    def _sync_lead_input_controls(self) -> None:
        """Reflect ``/ji/lead/input/state`` with signals blocked."""

        status = self.store.lead_input
        self._lead_input_sync = True
        try:
            if status is not None:
                self.lead_input_port_label.setText(status.port_name or "(none)")
                self.lead_input_live_checkbox.setChecked(bool(status.live))
                if status.count_in in LEAD_INPUT_COUNT_INS:
                    self.lead_input_count_in_combo.setCurrentText(status.count_in)
                self.lead_input_bars_spin.setValue(int(status.bars))
                self.lead_input_quant_knob.set_value(status.quantize, emit=False)
            live = bool(status.live) if status is not None else False
            port_ok = bool(status.port_ok) if status is not None else False
            source = status.source if status is not None else "generated"
            rhythm = self.store.rhythm
            running = bool(rhythm.running) if rhythm is not None else False
            self.lead_input_record_button.setEnabled(running and port_ok)
            self.lead_input_stop_button.setEnabled(
                bool(status.recording) if status is not None else False
            )
            self.lead_input_revert_button.setEnabled(source == "played")
            self.lead_input_clear_button.setEnabled(source == "played")
            self.lead_input_live_checkbox.setEnabled(port_ok)
            self.lead_input_refresh_button.setEnabled(True)
            if not live:
                self._release_keys()
        finally:
            self._lead_input_sync = False
        self._update_melody_buttons()

    def _update_lead_input_label(self) -> None:
        status = self.store.lead_input
        if status is None:
            self.lead_input_status_label.setText("Input: waiting for bridge")
            self.melody_status_label.setText("Melody: --")
            return
        self.lead_input_status_label.setText("Input: " + status.input_line())
        self.melody_status_label.setText("Melody: " + status.melody_line())

    # -- melody list -----------------------------------------------------

    def _sync_melody_list(self) -> None:
        names = list(self.store.melody_names)
        self._melody_sync = True
        try:
            current = self.lead_melody_combo.currentText()
            self.lead_melody_combo.clear()
            self.lead_melody_combo.addItems(names)
            if current in names:
                self.lead_melody_combo.setCurrentText(current)
            elif names:
                self.lead_melody_combo.setCurrentIndex(0)
        finally:
            self._melody_sync = False
        self._on_melody_combo_changed(self.lead_melody_combo.currentText())

    def _on_melody_combo_changed(self, text: str) -> None:
        if self._melody_sync:
            return
        if text:
            self.lead_melody_name_edit.setText(text)
        self._update_melody_buttons()

    def _on_melody_name_changed(self, _text: str) -> None:
        self._update_melody_buttons()

    def _update_melody_buttons(self) -> None:
        name = self.lead_melody_combo.currentText()
        known = bool(name) and name in self.store.melody_names
        self.lead_melody_load_button.setEnabled(known)
        self.lead_melody_delete_button.setEnabled(known)
        self.lead_melody_save_button.setEnabled(
            bool(self.lead_melody_name_edit.text().strip())
        )

    def _selected_melody(self) -> str:
        return self.lead_melody_name_edit.text().strip() or (
            self.lead_melody_combo.currentText()
        )

    def _on_melody_save(self) -> None:
        name = self.lead_melody_name_edit.text().strip()
        if not name:
            self._set_message("Type a melody name first")
            return
        self._guard(self.controller.melody_save, name)
        self._set_message(f'Save melody "{name}"')

    def _on_melody_load(self) -> None:
        name = self.lead_melody_combo.currentText()
        if not name:
            self._set_message("Select a melody first")
            return
        self._guard(self.controller.melody_load, name)
        self._set_message(f'Load melody "{name}"')

    def _on_melody_delete(self) -> None:
        name = self.lead_melody_combo.currentText()
        if not name:
            self._set_message("Select a melody first")
            return
        self._guard(self.controller.melody_delete, name)
        self._set_message(f'Delete melody "{name}"')

    # -- QWERTY ----------------------------------------------------------

    def _typing_focus(self) -> bool:
        widget = QApplication.focusWidget()
        return isinstance(
            widget, (QLineEdit, QComboBox, QAbstractSpinBox)
        )

    def _keys_active(self) -> bool:
        return bool(self.lead_input_keys_checkbox.isChecked()) and not self._typing_focus()

    def _release_keys(self) -> None:
        if not getattr(self, "_keys_down", None):
            self._keys_down = set()
            self._key_notes = {}
            return
        for note in sorted(self._keys_down):
            self._guard(self.controller.lead_input_note_off, int(note))
        self._keys_down = set()
        self._key_notes = {}

    def _on_focus_changed(self, _old: Any, new: Any) -> None:
        if new is None or isinstance(
            new, (QLineEdit, QComboBox, QAbstractSpinBox)
        ):
            self._release_keys()
            return
        if isinstance(new, QWidget) and not self.isAncestorOf(new):
            self._release_keys()

    def keyPressEvent(self, event: Any) -> None:  # noqa: N802 (Qt API)
        if (
            event.isAutoRepeat()
            or not self._keys_active()
            or event.modifiers() != Qt.KeyboardModifier.NoModifier
        ):
            super().keyPressEvent(event)
            return
        text = str(event.text()).strip().lower()
        if text in ("z", "x"):
            shift = -1 if text == "z" else 1
            self._keyboard_octave = max(-2, min(2, self._keyboard_octave + shift))
            self._set_message(f"Keys octave {self._keyboard_octave:+d}")
            event.accept()
            return
        note = key_midi(text, self._keyboard_octave)
        if note is None:
            super().keyPressEvent(event)
            return
        if note not in self._keys_down:
            self._keys_down.add(note)
            if not hasattr(self, "_key_notes"):
                self._key_notes = {}
            self._key_notes[text] = note
            self._guard(self.controller.lead_input_note_on, int(note), 100)
        event.accept()

    def keyReleaseEvent(self, event: Any) -> None:  # noqa: N802 (Qt API)
        if event.isAutoRepeat():
            super().keyReleaseEvent(event)
            return
        text = str(event.text()).strip().lower()
        note = getattr(self, "_key_notes", {}).pop(text, None)
        if note is not None:
            self._keys_down.discard(note)
            self._guard(self.controller.lead_input_note_off, int(note))
            event.accept()
            return
        super().keyReleaseEvent(event)

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

    # -- Stage 8.5 state controls -----------------------------------------

    def _state_name(self) -> str:
        return str(self.state_combo.currentText()).strip()

    def _state_known(self) -> bool:
        name = self._state_name().casefold()
        if not name:
            return False
        return any(
            str(value).casefold() == name for value in self.store.state_names
        )

    def _update_state_buttons(self) -> None:
        name = self._state_name()
        known = self._state_known()
        self.state_save_button.setEnabled(bool(name))
        self.state_load_button.setEnabled(known)
        self.state_delete_button.setEnabled(known)

    def _on_state_name_changed(self, _text: str) -> None:
        if self._state_sync:
            return
        self._update_state_buttons()

    def _on_state_save(self) -> None:
        name = self._state_name()
        self._guard(self.controller.state_save, name)
        self._set_message(f"State save {name}")

    def _on_state_load(self) -> None:
        name = self._state_name()
        running = self._transport_running()
        self._guard(self.controller.state_load, name)
        self._set_message(
            f"State load {name} queued for the next beat"
            if running
            else f"State load {name} applied immediately"
        )

    def _on_state_delete(self) -> None:
        name = self._state_name()
        self._guard(self.controller.state_delete, name)
        self._set_message(f"State delete {name}")

    def _on_state_refresh(self) -> None:
        self._guard(self.controller.request_state_list)
        self._set_message("State list refreshed")

    def _sync_state_controls(self) -> None:
        """Reflect ``/ji/state/list`` + ``/ji/state/state`` with no feedback OSC."""

        self._state_sync = True
        try:
            names = list(self.store.state_names)
            current = self._state_name()
            self.state_combo.blockSignals(True)
            try:
                self.state_combo.clear()
                self.state_combo.addItems(names)
                if current:
                    self.state_combo.setCurrentText(current)
                elif names:
                    self.state_combo.setCurrentText(names[0])
                else:
                    self.state_combo.setCurrentText("")
            finally:
                self.state_combo.blockSignals(False)
        finally:
            self._state_sync = False
        self._update_state_buttons()
        self._update_state_label()

    def _update_state_label(self) -> None:
        status = self.store.state
        if status is None:
            self.state_label.setText("State: waiting for bridge")
            return
        self.state_label.setText(status.status_line())

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

        self.lead_note_len_checkbox = QCheckBox("Note len")
        self.lead_note_len_checkbox.setToolTip(
            "On: a note's own length sets the gate (recorded takes). "
            "Off: the Gate knob caps every note (the generated line)."
        )
        self.lead_note_len_checkbox.toggled.connect(self._on_lead_note_len)
        row2.addWidget(self.lead_note_len_checkbox)

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
            self.lead_note_len_checkbox,
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

    def _build_fretboard_group(self) -> QGroupBox:
        """Stage 8.8 improvisation aid: one full-width group, no OSC."""

        group = QGroupBox("Fretboard")
        layout = QVBoxLayout(group)
        layout.setContentsMargins(4, 2, 4, 2)
        layout.setSpacing(2)

        # Every combo/spin in this group is signal-blocked while it is built:
        # the aid is read-only and must never send a control message.
        self._fretboard_sync = True
        row = QHBoxLayout()
        row.setSpacing(3)

        row.addWidget(QLabel("Strings"))
        self.fretboard_strings_spin = QSpinBox()
        self.fretboard_strings_spin.setRange(
            fretboard.MIN_STRINGS, fretboard.MAX_STRINGS
        )
        self.fretboard_strings_spin.setValue(
            fretboard.DEFAULT_TUNING.string_count
        )
        self.fretboard_strings_spin.setFixedWidth(42)
        self.fretboard_strings_spin.setToolTip(
            "6, 7 or 8 strings. Adding one prepends a string a fourth below "
            "the current lowest; removing drops the lowest strings."
        )
        self.fretboard_strings_spin.valueChanged.connect(
            self._on_fretboard_strings
        )
        row.addWidget(self.fretboard_strings_spin)

        row.addWidget(QLabel("Frets"))
        self.fretboard_frets_spin = QSpinBox()
        self.fretboard_frets_spin.setRange(
            fretboard.MIN_FRETS, fretboard.MAX_FRETS
        )
        self.fretboard_frets_spin.setValue(fretboard.DEFAULT_FRETS)
        self.fretboard_frets_spin.setFixedWidth(42)
        self.fretboard_frets_spin.setToolTip(
            "0..24 frets. The open-string column is always drawn."
        )
        self.fretboard_frets_spin.valueChanged.connect(self._on_fretboard_frets)
        row.addWidget(self.fretboard_frets_spin)

        row.addWidget(QLabel("Tuning"))
        self.fretboard_tuning_combo = QComboBox()
        self.fretboard_tuning_combo.addItems(
            [label for label, _strings in fretboard.TUNING_PRESETS]
            + [fretboard.CUSTOM_PRESET]
        )
        self.fretboard_tuning_combo.setCurrentText("Standard E")
        self.fretboard_tuning_combo.setMinimumWidth(124)
        self.fretboard_tuning_combo.setToolTip(
            "Whole-neck presets (Standard E, Drop D, DADGAD, Open G/D, …). "
            "Editing one string below switches the label to Custom. This is "
            "a session-only aid: it is never saved and never sent."
        )
        self.fretboard_tuning_combo.currentIndexChanged.connect(
            self._on_fretboard_preset
        )
        row.addWidget(self.fretboard_tuning_combo)

        self.fretboard_string_combos: list[QComboBox] = []
        for index in range(fretboard.MAX_STRINGS):
            combo = QComboBox()
            for midi in range(128):
                combo.addItem(fretboard.midi_note_name(midi), midi)
            combo.setFixedWidth(58)
            combo.setToolTip(
                f"Open string {index + 1} (low to high), any MIDI note 0..127."
            )
            combo.currentIndexChanged.connect(
                lambda _position, string=index: (
                    self._on_fretboard_string_changed(string)
                )
            )
            combo.setVisible(index < fretboard.DEFAULT_TUNING.string_count)
            self.fretboard_string_combos.append(combo)
            row.addWidget(combo)

        self.fretboard_names_checkbox = QCheckBox("Names")
        self.fretboard_names_checkbox.setChecked(True)
        self.fretboard_names_checkbox.setToolTip(
            "Draw the note name inside each marker. Markers stay when off."
        )
        self.fretboard_names_checkbox.toggled.connect(self._on_fretboard_names)
        row.addWidget(self.fretboard_names_checkbox)
        row.addStretch(1)
        layout.addLayout(row)

        # The set summary and the degree legend share one compact row so the
        # default window fits without scrolling; the legend wraps on a narrow
        # screen and the scroll area remains the net.
        labels_row = QHBoxLayout()
        labels_row.setSpacing(8)
        self.fretboard_set_label = QLabel("Fretboard: waiting for the set")
        self.fretboard_set_label.setTextInteractionFlags(
            Qt.TextInteractionFlag.TextSelectableByMouse
        )
        labels_row.addWidget(self.fretboard_set_label)
        self.fretboard_legend_label = QLabel("")
        self.fretboard_legend_label.setWordWrap(True)
        self.fretboard_legend_label.setTextInteractionFlags(
            Qt.TextInteractionFlag.TextSelectableByMouse
        )
        labels_row.addWidget(self.fretboard_legend_label, 1)
        layout.addLayout(labels_row)

        self.fretboard_widget = FretboardWidget()
        layout.addWidget(self.fretboard_widget)
        self._fretboard_sync = False
        return group

    # -- Stage 8.8 fretboard handlers (never call the controller) -------

    def _on_fretboard_strings(self, value: int) -> None:
        if self._fretboard_sync:
            return
        try:
            self._fretboard_tuning = self._fretboard_tuning.with_count(
                int(value)
            )
        except (TypeError, ValueError):
            self._sync_fretboard_controls()
            return
        self._sync_fretboard_controls()
        self._redraw_fretboard()

    def _on_fretboard_frets(self, _value: int) -> None:
        if self._fretboard_sync:
            return
        self._redraw_fretboard()

    def _on_fretboard_preset(self, _position: int = 0) -> None:
        if self._fretboard_sync:
            return
        strings = fretboard.TUNING_PRESET_MAP.get(
            self.fretboard_tuning_combo.currentText()
        )
        if strings is None:
            # "Custom" is display-only: restore the real tuning's label.
            self._sync_fretboard_controls()
            return
        self._fretboard_tuning = fretboard.GuitarTuning.create(strings)
        self._sync_fretboard_controls()
        self._redraw_fretboard()

    def _on_fretboard_string_changed(self, index: int) -> None:
        if self._fretboard_sync:
            return
        if not 0 <= index < len(self.fretboard_string_combos):
            return
        midi = self.fretboard_string_combos[index].currentData()
        if midi is None:
            return
        try:
            self._fretboard_tuning = self._fretboard_tuning.with_string(
                index, int(midi)
            )
        except (TypeError, ValueError):
            self._sync_fretboard_controls()
            return
        self._sync_fretboard_controls()
        self._redraw_fretboard()

    def _on_fretboard_names(self, _checked: bool) -> None:
        if self._fretboard_sync:
            return
        self._redraw_fretboard()

    def _sync_fretboard_controls(self) -> None:
        """Reflect ``self._fretboard_tuning`` with signals blocked."""

        if not hasattr(self, "fretboard_strings_spin"):
            return
        was_sync = self._fretboard_sync
        self._fretboard_sync = True
        try:
            tuning = self._fretboard_tuning
            self.fretboard_strings_spin.setValue(tuning.string_count)
            self.fretboard_tuning_combo.setCurrentText(tuning.preset)
            for index, combo in enumerate(self.fretboard_string_combos):
                visible = index < tuning.string_count
                combo.setVisible(visible)
                if visible:
                    position = combo.findData(tuning.strings[index])
                    if position >= 0:
                        combo.setCurrentIndex(position)
        finally:
            self._fretboard_sync = was_sync

    def _redraw_fretboard(self) -> None:
        if not hasattr(self, "fretboard_widget"):
            return
        self.fretboard_widget.set_board(
            self._fretboard_tuning,
            self._fretboard_degrees,
            root_pc=self._fretboard_root_pc,
            anchor_pc=self._fretboard_anchor_pc,
            frets=int(self.fretboard_frets_spin.value()),
            show_names=bool(self.fretboard_names_checkbox.isChecked()),
            fundamental=self._fretboard_fundamental,
        )

    def _sync_fretboard(self) -> None:
        """Follow ``/ji/freqset/changed``; never sends anything."""

        if not hasattr(self, "fretboard_widget"):
            return
        freqset = self.store.freqset
        if freqset is None:
            self._fretboard_degrees = ()
            self._fretboard_root_pc = None
            self._fretboard_anchor_pc = None
            self._fretboard_fundamental = None
            self.fretboard_set_label.setText("Fretboard: waiting for the set")
            self.fretboard_legend_label.setText("")
            self._redraw_fretboard()
            return
        try:
            degrees = fretboard.scale_degrees(
                freqset.fundamental, freqset.ratios
            )
            root_pc = fretboard.root_pitch_class(freqset.fundamental)
            anchor_pc = (
                fretboard.ratio_pitch_class(
                    freqset.fundamental, freqset.anchor
                )
                if freqset.anchor is not None
                else None
            )
        except (TypeError, ValueError):
            # store.apply already rejects malformed sets; keep the last good
            # diagram if a bad object ever reaches here.
            self.fretboard_set_label.setText("Fretboard: invalid set (ignored)")
            return
        self._fretboard_degrees = degrees
        self._fretboard_root_pc = root_pc
        self._fretboard_anchor_pc = anchor_pc
        self._fretboard_fundamental = float(freqset.fundamental)
        summary = fretboard.scale_summary(degrees)
        self.fretboard_set_label.setText(
            f"12-TET set (A = {freqset.fundamental:.2f} Hz): {summary}"
        )
        self.fretboard_legend_label.setText(self._fretboard_legend(degrees))
        self._redraw_fretboard()

    def _fretboard_legend(self, degrees: Any) -> str:
        parts = []
        for degree in degrees:
            source = degree.best_source
            text = (
                f"{degree.name} {_fretboard_ratio_label(source.ratio)} "
                f"{_cents_text(source.cents)}¢"
            )
            if len(degree.sources) > 1:
                extras = ", ".join(
                    f"{_fretboard_ratio_label(other.ratio)} "
                    f"{_cents_text(other.cents)}¢"
                    for other in degree.sources[1:]
                )
                text += f" ({extras})"
            parts.append(text)
        return " · ".join(parts)

    def _build_status_group(self) -> QGroupBox:
        group = QGroupBox("Status")
        grid = QGridLayout(group)
        grid.setContentsMargins(4, 2, 4, 2)
        grid.setHorizontalSpacing(8)
        grid.setVerticalSpacing(1)

        # The platform's fixed-width font (Menlo on macOS, DejaVu Sans Mono
        # elsewhere).  "Monospace" is a Linux alias Qt cannot resolve on macOS.
        fixed = QFontDatabase.systemFont(QFontDatabase.SystemFont.FixedFont)
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

    # -- Stage 8.6 loop editing ----------------------------------------

    def _selected_loop(self) -> int:
        return int(self.loop_edit_selector.currentIndex()) + 1

    def _selected_mem_slot(self) -> int:
        return int(self.loop_mem_combo.currentIndex())

    def _loop_edit_for(self, index: int) -> osc_control.LoopEdit:
        edit = self.store.loop_edits.get(index)
        if edit is None:
            edit = osc_control.LoopEdit(index=index)
            self.store.loop_edits[index] = edit
        return edit

    def _bpm(self) -> float:
        """Bridge BPM for the fixed-time display (120 when unknown)."""

        rhythm = self.store.rhythm
        return float(rhythm.bpm) if rhythm is not None else 120.0

    def _bar_beats(self) -> float:
        """Current meter bar in quarter beats (4/4 when unknown)."""

        rhythm = self.store.rhythm
        if rhythm is None:
            return 4.0
        return max(1.0, rhythm.meter_num * 4.0 / rhythm.meter_den)

    def _dub_loop(self, index: int) -> None:
        self._guard(self.controller.loop_dub, index)
        self._set_message(
            f"Loop {index} overdub: one pass over what is already there"
        )

    def _on_loop_edit_selected(self, _position: int = 0) -> None:
        if self._loop_edit_sync:
            return
        self._sync_loop_edit_row(self._selected_loop())

    def _send_loop_edit(self, param: str, value: Any) -> None:
        if self._loop_edit_sync:
            return
        index = self._selected_loop()
        self._guard(self.controller.set_loop_edit, index, param, value)
        # Optimistic local echo so the row and its status line react at once;
        # the authoritative /ji/loop/edit reply overwrites this.
        self._apply_local_edit(self._loop_edit_for(index), param, value)
        self._refresh_loop_edit_label(index)

    def _effective_pitch(self, edit: osc_control.LoopEdit) -> float:
        """The ratio the voice gets (optimistic mirror of sclang's snap)."""

        if edit.is_snapped:
            freqset = self.store.freqset
            ratios = (
                list(freqset.ratios)
                if freqset is not None and freqset.ratios
                else [1.0]
            )
            return osc_control.loop_snap_pitch(edit.grain_pitch, ratios)
        return min(4.0, max(0.25, float(edit.grain_pitch)))

    def _apply_local_edit(
        self, edit: osc_control.LoopEdit, param: str, value: Any
    ) -> None:
        if param == "reverse":
            edit.reverse = 1 if value else 0
        elif param == "rate":
            edit.rate = float(value)
        elif param == "gain":
            edit.gain = float(value)
        elif param == "mode":
            edit.mode = 1 if value else 0
        elif param == "sync":
            edit.sync = 1 if value else 0
        elif param == "division":
            edit.division = str(value)
        elif param == "grain_rate":
            edit.grain_rate = float(value)
        elif param == "grain_size":
            edit.grain_size_ms = float(value)
        elif param == "grain_pos":
            edit.grain_pos = float(value)
        elif param == "grain_scatter":
            edit.grain_scatter = float(value)
        elif param == "grain_pitch":
            edit.grain_pitch = float(value)
            edit.effective_pitch = self._effective_pitch(edit)
        elif param == "grain_reverse":
            edit.grain_reverse = float(value)
        elif param == "grain_size_jitter":
            edit.grain_size_jitter = float(value)
        elif param == "grain_pitch_jitter":
            edit.grain_pitch_jitter = float(value)
        elif param == "grain_pitch_snap":
            edit.grain_pitch_snap = 1 if value else 0
            edit.effective_pitch = self._effective_pitch(edit)
        elif param == "grain_smear":
            edit.grain_smear = float(value)
        elif param == "grain_freeze":
            edit.grain_freeze = 1 if value else 0
        elif param == "grain_motion":
            edit.grain_motion = str(value)
        elif param == "grain_speed":
            edit.grain_speed = float(value)
        elif param == "grain_depth":
            edit.grain_depth = float(value)
        elif param == "grain_burst":
            edit.grain_burst = float(value)
        elif param == "grain_morph":
            edit.grain_morph = float(value)
        elif param == "grain_melt":
            edit.grain_melt = 1 if value else 0
        elif param == "grain_filter":
            edit.grain_filter = float(value)
        elif param == "grain_filter_rate":
            edit.grain_filter_rate = float(value)
        elif param == "grain_breath":
            edit.grain_breath = float(value)
        elif param == "grain_breath_rate":
            edit.grain_breath_rate = float(value)
        elif param == "count_in":
            edit.count_in = str(value)
        elif param == "auto_play":
            edit.auto_play = 1 if value else 0
        elif param == "harm_mode":
            edit.harm_mode = str(value)
        elif param == "harm_mix":
            edit.harm_mix = float(value)
        elif param == "harm_time":
            edit.harm_time = float(value)
        elif param == "harm_time_sync":
            edit.harm_time_sync = str(value)
        elif param == "harm_steps":
            edit.harm_steps = int(value)
        elif param == "harm_spacing":
            edit.harm_spacing = float(value)
        elif param == "harm_falloff":
            edit.harm_falloff = float(value)
        elif param == "harm_auto":
            edit.harm_auto = 1 if value else 0

    def _refresh_loop_edit_label(self, index: int) -> None:
        self.loop_edit_label.setText(
            self._loop_edit_for(index).status_line(
                self._bpm(), self._bar_beats()
            )
        )

    def _on_loop_reverse(self, checked: bool) -> None:
        self._send_loop_edit("reverse", 1 if checked else 0)

    def _on_loop_rate(self, position: int) -> None:
        if 0 <= position < len(LOOP_RATE_VALUES):
            self._send_loop_edit("rate", LOOP_RATE_VALUES[position])

    def _on_loop_gain(self, value: float) -> None:
        self._send_loop_edit("gain", round(float(value), 2))

    def _on_loop_count_in(self, text: str) -> None:
        if text:
            self._send_loop_edit("count_in", text)

    def _on_loop_auto(self, checked: bool) -> None:
        self._send_loop_edit("auto_play", 1 if checked else 0)

    # -- Stage 9.0 harmonic-series delay FX ------------------------------

    def _on_harm_mode(self, text: str) -> None:
        if self._loop_edit_sync:
            return
        mode = str(text).strip().lower()
        if mode not in osc_control.LOOP_HARM_MODES:
            return
        self._send_loop_edit("harm_mode", mode)
        self._refresh_harm_enabled()

    def _on_harm_time_sync(self, text: str) -> None:
        if self._loop_edit_sync or not text:
            return
        self._reassert_harm_mode()
        self._send_loop_edit("harm_time_sync", text)
        #  Show the derived seconds immediately; the sclang echo then makes it
        #  authoritative.  Set the spin with the guard so this display update
        #  never sends a harm_time edit.
        edit = self._loop_edit_for(self._selected_loop())
        was_sync = self._loop_edit_sync
        self._loop_edit_sync = True
        try:
            self.harm_time_spin.setValue(
                edit.harm_time
                if edit.harm_time_sync == "free"
                else edit.harm_time_seconds(self._bpm(), self._bar_beats())
            )
        finally:
            self._loop_edit_sync = was_sync
        self._refresh_harm_enabled()

    def _reassert_harm_mode(self) -> None:
        """Send the mode the combo shows before a parameter edit.

        The Harm mode is the one piece of FX state that gates the whole synth.
        Re-asserting it here makes a lost/ignored mode message self-healing:
        changing Mix/Time/Steps/Space/Fall/Auto on a loop that the UI shows as
        Cascade/Delay now always sends `harm_mode` first.  It is a no-op when
        the mode is off.
        """

        mode = str(self.harm_mode_combo.currentText()).strip().lower()
        if mode in ("cascade", "delay"):
            self._send_loop_edit("harm_mode", mode)

    def _on_harm_value(self, param: str, value: Any) -> None:
        self._reassert_harm_mode()
        self._send_loop_edit(param, value)

    def _on_harm_fire(self) -> None:
        index = self._selected_loop()
        #  Fire is enabled only for Cascade; re-assert the mode so a dropped
        #  earlier edit cannot leave the fire with no FX synth to trigger.
        self._reassert_harm_mode()
        self._guard(self.controller.loop_fx_fire, index)
        self._set_message(
            f"Loop {index} harmonic cascade fired" if self.harm_fire_button.isEnabled()
            else f"Loop {index} Fire ignored (not Cascade/playing)"
        )

    def _refresh_harm_enabled(self) -> None:
        """Enable only the FX controls that matter in the current mode.

        Mix/Time/TimeSync/Steps/Fall work in both active modes; Space/Auto are
        Cascade-only; Fire is Cascade + the selected loop playing.  With Harm
        off every FX control except the mode combo is disabled.  The Time spin
        is editable only in ``free``; a fixed division shows the tempo-derived
        seconds read-only.  It stays ENABLED in that case (read-only, no step
        buttons) so Qt does not move keyboard focus to the next control (the
        old behaviour looked like the edit had jumped to `Default len`).
        """

        if not hasattr(self, "harm_mix_spin"):
            return
        edit = self._loop_edit_for(self._selected_loop())
        active = edit.harm_mode in ("cascade", "delay")
        cascade = edit.harm_mode == "cascade"
        for widget in (
            self.harm_mix_spin,
            self.harm_time_spin,
            self.harm_time_sync_combo,
            self.harm_steps_spin,
            self.harm_falloff_spin,
        ):
            widget.setEnabled(active)
        for widget in (self.harm_spacing_spin, self.harm_auto_checkbox):
            widget.setEnabled(cascade)
        free_time = edit.harm_time_sync == "free"
        self.harm_time_spin.setReadOnly(not free_time)
        self.harm_time_spin.setButtonSymbols(
            QAbstractSpinBox.ButtonSymbols.UpDownArrows
            if free_time
            else QAbstractSpinBox.ButtonSymbols.NoButtons
        )
        playing = self.store.loop_states.get(self._selected_loop()) == "playing"
        self.harm_fire_button.setEnabled(cascade and playing)

    def _on_grain_toggled(self, checked: bool) -> None:
        self._send_loop_edit("mode", 1 if checked else 0)
        self._refresh_grain_enabled()

    def _on_grain_sync(self, position: int) -> None:
        self._send_loop_edit("sync", 1 if position == 1 else 0)
        self._refresh_grain_enabled()

    def _on_grain_div(self, text: str) -> None:
        if text:
            self._send_loop_edit("division", text)

    def _on_grain_motion(self, text: str) -> None:
        if text:
            self._send_loop_edit("grain_motion", text)
            self._refresh_grain_enabled()

    def _on_grain_snap(self, checked: bool) -> None:
        self._send_loop_edit("grain_pitch_snap", 1 if checked else 0)

    def _on_grain_freeze(self, checked: bool) -> None:
        self._send_loop_edit("grain_freeze", 1 if checked else 0)
        self._refresh_grain_enabled()

    def _on_grain_melt(self, checked: bool) -> None:
        self._send_loop_edit("grain_melt", 1 if checked else 0)
        self._refresh_grain_enabled()

    def _on_grain_value(self, param: str, value: float) -> None:
        self._send_loop_edit(param, value)

    def _on_loop_mem_selected(self, _position: int = 0) -> None:
        self._sync_loop_mem(self._selected_mem_slot())

    def _on_loop_mem_save(self) -> None:
        slot = self._selected_mem_slot()
        index = self._selected_loop()
        self._guard(self.controller.loop_mem_save, slot, index)
        self._set_message(
            f"Loop {index} -> memory {LOOP_MEM_SLOT_NAMES[slot]} (session only)"
        )

    def _on_loop_mem_recall(self) -> None:
        slot = self._selected_mem_slot()
        index = self._selected_loop()
        self._guard(self.controller.loop_mem_recall, slot, index)
        self._set_message(
            f"Memory {LOOP_MEM_SLOT_NAMES[slot]} -> loop {index} (stopped; "
            "press Play)"
        )

    def _on_loop_mem_clear(self) -> None:
        slot = self._selected_mem_slot()
        self._guard(self.controller.loop_mem_clear, slot)
        self._set_message(f"Memory {LOOP_MEM_SLOT_NAMES[slot]} cleared")

    def _refresh_grain_enabled(self) -> None:
        """Grain, motion, morph and loop-breath controls need Grain on.

        The hint explains the current clock/motion state (beat+stopped is the
        only combination that needs an action from the human); the moving
        filter stays usable on the plain loop.
        """

        grain_on = self.grain_checkbox.isChecked()
        for widget in (
            self.grain_sync_combo,
            self.grain_div_combo,
            self.grain_rate_spin,
            self.grain_size_spin,
            self.grain_pos_spin,
            self.grain_scatter_spin,
            self.grain_pitch_spin,
            self.grain_reverse_spin,
            self.grain_motion_combo,
            self.grain_speed_spin,
            self.grain_depth_spin,
            self.grain_size_jitter_spin,
            self.grain_pitch_jitter_spin,
            self.grain_snap_checkbox,
            self.grain_smear_spin,
            self.grain_freeze_checkbox,
            # Stage 8.7: the loop's own grain breath follows the grain switch.
            self.grain_breath_spin,
            self.grain_breath_rate_spin,
        ):
            widget.setEnabled(grain_on)
        #  Morph/Melt are grain-mode controls; the moving filter is a
        #  post-autotune texture and stays usable on the plain loop.
        self.grain_morph_spin.setEnabled(grain_on)
        self.grain_melt_checkbox.setEnabled(grain_on)
        beat = self.grain_sync_combo.currentIndex() == 1
        if not grain_on:
            self.grain_hint.setText("")
        elif beat and not self._transport_running():
            self.grain_hint.setText("beat grains need the transport (press Play)")
        elif self.grain_freeze_checkbox.isChecked():
            self.grain_hint.setText("motion frozen")
        elif beat:
            self.grain_hint.setText("locked to the transport")
        else:
            self.grain_hint.setText("free-running")

    # -- end Stage 8.6.2 ------------------------------------------------

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
            elif key[0] == "lead_input":
                self._guard(self.controller.set_lead_input_quantize, value)
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

        if hasattr(self, "grain_hint"):
            self._refresh_grain_enabled()

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
        #  A fixed-time FX display follows the tempo: refresh the Harm row
        #  (signal-blocked, no OSC) when the BPM/meter snapshot changes.
        if hasattr(self, "harm_time_spin") and not self._loop_edit_sync:
            self._sync_loop_edit_row(self._selected_loop())

    # -- Stage 8.7 breath row -------------------------------------------

    def _on_breath_enabled(self, checked: bool) -> None:
        if self._breath_sync:
            return
        self._guard(
            self.controller.set_breath_param,
            "enabled",
            1 if checked else 0,
        )
        self._set_message(
            "Breath on (the grid breathes)" if checked else "Breath off"
        )

    def _on_breath_value(self, name: str, value: Any) -> None:
        if self._breath_sync:
            return
        self._guard(self.controller.set_breath_param, name, value)

    def _on_breath_arp_sync(self, checked: bool) -> None:
        if self._breath_sync:
            return
        self._guard(
            self.controller.set_breath_param,
            "arp_sync",
            1 if checked else 0,
        )
        self._set_message(
            "Arp breath in sync with the groove" if checked
            else "Arp breath on its own half-cycle phase"
        )

    def _refresh_breath_hint(self, breath: Any | None = None) -> None:
        if breath is None:
            breath = self.store.breath
        if breath is None or not breath.enabled:
            self.breath_hint.setText("breath off")
            return
        if breath.depth <= 0.0 and breath.intensity <= 0.0:
            self.breath_hint.setText("breathing (no depth)")
            return
        self.breath_hint.setText(f"breathing · {breath.period_bars} bars")

    def _sync_breath_controls(self) -> None:
        """Mirror ``/ji/breath/state`` signal-blocked (never echo back)."""

        breath = self.store.breath
        if breath is None:
            return
        self._breath_sync = True
        try:
            self.breath_enable_checkbox.setChecked(bool(breath.enabled))
            self.breath_depth_spin.setValue(int(round(breath.depth * 100.0)))
            self.breath_period_spin.setValue(int(breath.period_bars))
            if self.breath_shape_combo.currentText() != breath.shape:
                self.breath_shape_combo.setCurrentText(breath.shape)
            self.breath_intensity_spin.setValue(
                int(round(breath.intensity * 100.0))
            )
            self.breath_phase_spin.setValue(int(round(breath.phase * 100.0)))
            self.breath_arp_spin.setValue(
                int(round(breath.arp_local * 100.0))
            )
            self.breath_arp_sync_checkbox.setChecked(bool(breath.arp_sync))
        finally:
            self._breath_sync = False
        self._refresh_breath_hint(breath)

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

    def _sync_loop_edit_row(self, index: int) -> None:
        """Mirror one loop's edit state into the edit/grain/motion widgets.

        Signal-blocked: setting a widget here never sends OSC back.  A widget
        is only touched when the incoming value differs, so an echo that
        carries the value the human just chose cannot move anything.
        """

        edit = self._loop_edit_for(index)
        was_sync = self._loop_edit_sync
        self._loop_edit_sync = True
        try:
            self.loop_reverse_checkbox.setChecked(bool(edit.reverse))
            if self.loop_rate_combo.currentText() != edit.rate_label:
                self.loop_rate_combo.setCurrentText(edit.rate_label)
            self.loop_gain_spin.setValue(edit.gain)
            if self.loop_count_in_combo.currentText() != edit.count_in:
                self.loop_count_in_combo.setCurrentText(edit.count_in)
            self.loop_auto_checkbox.setChecked(bool(edit.auto_play))
            self.grain_checkbox.setChecked(edit.is_grain)
            self.grain_sync_combo.setCurrentIndex(1 if edit.is_beat else 0)
            if self.grain_div_combo.currentText() != edit.division:
                self.grain_div_combo.setCurrentText(edit.division)
            self.grain_rate_spin.setValue(edit.grain_rate)
            self.grain_size_spin.setValue(edit.grain_size_ms)
            self.grain_pos_spin.setValue(edit.grain_pos * 100.0)
            self.grain_scatter_spin.setValue(edit.grain_scatter * 100.0)
            self.grain_pitch_spin.setValue(edit.grain_pitch)
            self.grain_reverse_spin.setValue(edit.grain_reverse * 100.0)
            if self.grain_motion_combo.currentText() != edit.grain_motion:
                self.grain_motion_combo.setCurrentText(edit.grain_motion)
            self.grain_speed_spin.setValue(edit.grain_speed)
            self.grain_depth_spin.setValue(edit.grain_depth * 100.0)
            self.grain_size_jitter_spin.setValue(edit.grain_size_jitter * 100.0)
            self.grain_pitch_jitter_spin.setValue(
                edit.grain_pitch_jitter * 100.0
            )
            self.grain_snap_checkbox.setChecked(edit.is_snapped)
            self.grain_smear_spin.setValue(edit.grain_smear * 100.0)
            self.grain_freeze_checkbox.setChecked(edit.is_frozen)
            self.grain_morph_spin.setValue(edit.grain_morph * 100.0)
            self.grain_melt_checkbox.setChecked(edit.is_melting)
            self.grain_filter_spin.setValue(edit.grain_filter * 100.0)
            self.grain_filter_rate_spin.setValue(edit.grain_filter_rate)
            self.grain_breath_spin.setValue(edit.grain_breath * 100.0)
            self.grain_breath_rate_spin.setValue(edit.grain_breath_rate)
            #  Stage 9.0 Harm row.
            labels = {"off": "off", "cascade": "Cascade", "delay": "Delay"}
            self.harm_mode_combo.setCurrentText(
                labels.get(edit.harm_mode, "off")
            )
            if self.harm_time_sync_combo.currentText() != edit.harm_time_sync:
                self.harm_time_sync_combo.setCurrentText(edit.harm_time_sync)
            self.harm_mix_spin.setValue(edit.harm_mix * 100.0)
            self.harm_time_spin.setValue(
                edit.harm_time
                if edit.harm_time_sync == "free"
                else edit.harm_time_seconds(self._bpm(), self._bar_beats())
            )
            self.harm_steps_spin.setValue(int(edit.harm_steps))
            self.harm_spacing_spin.setValue(edit.harm_spacing * 1000.0)
            self.harm_falloff_spin.setValue(edit.harm_falloff * 100.0)
            self.harm_auto_checkbox.setChecked(bool(edit.harm_auto))
        finally:
            self._loop_edit_sync = was_sync
        self.loop_edit_label.setText(
            edit.status_line(self._bpm(), self._bar_beats())
        )
        self._refresh_grain_enabled()
        self._refresh_harm_enabled()

    def _sync_loop_mem(self, slot: int) -> None:
        """Enable Recall/Clr only for a filled, selected slot."""

        mem = self.store.loop_mem.get(slot)
        filled = bool(mem is not None and mem.is_filled)
        name = LOOP_MEM_SLOT_NAMES[slot] if 0 <= slot < len(LOOP_MEM_SLOT_NAMES) else "?"
        if slot == self._selected_mem_slot():
            self.loop_mem_recall_button.setEnabled(filled)
            self.loop_mem_clear_button.setEnabled(filled)
            self.loop_mem_combo.setToolTip(
                f"Memory {name}: "
                + (mem.status_line() if mem is not None else "empty")
                + ". Session-only; lost when sclang re-evaluates the rig."
            )

    def _refresh_loop_label(self, index: int) -> None:
        label = self.loop_state_labels.get(index)
        if label is None:
            return
        state = self.store.loop_states.get(index, "waiting")
        if state == "counting":
            left = int(self.store.loop_counts.get(index, 0))
            state = f"counting {left}" if left > 0 else "counting"
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
        self._update_state_label()

        for index in range(1, 5):
            self._refresh_loop_label(index)

    def _set_message(self, text: str) -> None:
        self.message_label.setText(text)

    # -- close -----------------------------------------------------------

    def closeEvent(self, event: Any) -> None:  # noqa: N802 (Qt API)
        try:
            self._release_keys()
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
    app.setApplicationName("JI Station Stage 8.8")

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
        QMessageBox.critical(None, "JI Stage 8.5 UI", message)
        window.close()
        return 1

    window.show()
    return app.exec()


if __name__ == "__main__":
    raise SystemExit(main())
