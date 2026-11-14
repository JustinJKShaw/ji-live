"""Stage 7/8.8 minimal performance GUI package.

``ui.ji_ui`` is the PyQt6 window; ``ui.osc_control`` is the pure OSC
message/state layer and ``ui.fretboard`` the pure note/tuning maths; neither
pure module may import PyQt6.
"""

__all__ = ["fretboard", "osc_control"]
