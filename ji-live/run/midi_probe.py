#!/usr/bin/env python3
"""Print the raw MIDI a controller sends — the wheel-triage tool (Stage 9.2).

The bridge only logs one line when the first pitch wheel / CC 1 arrives, so this
script is the ground truth for "the wheel does nothing":

* if nothing prints while you move the pitch wheel, the controller is not
  sending pitch bend (check its pitch-wheel assignment / MIDI channel);
* if ``pitchwheel`` prints with the right channel but the bridge ignores it, the
  ``channel`` in ``config/lead_input_config.json`` is filtering it;
* if it prints and the bridge logs ``sent pitch bend`` but you hear nothing, the
  Lead track/instrument is not applying bend (MPE range / instrument setting).

Run from ``ji-live/`` (mido is imported only inside :func:`main`)::

    uv run --with mido --with python-rtmidi python run/midi_probe.py
    uv run --with mido --with python-rtmidi python run/midi_probe.py \
        --port "Launchkey 49" --match exact --seconds 20

Nothing is sent, recorded or saved; it only reads one input port.
"""

from __future__ import annotations

import argparse
import sys
import time
from collections.abc import Sequence

#: Message types printed by default.  Notes are included because a doubled or
#: missing note is the other half of every "is my input reaching the bridge?"
#: question; clock/active-sensing traffic is skipped.
REPORTED_TYPES = ("pitchwheel", "control_change", "note_on", "note_off")


def choose_port(names: Sequence[str], needle: str, match: str = "contains") -> str:
    """Return the single input matching ``needle``; raise with the list."""

    listed = ", ".join(repr(name) for name in names) or "(none)"
    wanted = str(needle).strip()
    if not wanted:
        raise ValueError(f"no input port configured; available: {listed}")
    key = wanted.casefold()
    if match == "exact":
        hits = [name for name in names if name.casefold() == key]
    else:
        hits = [name for name in names if key in name.casefold()]
    if not hits:
        raise ValueError(f"no input matched {wanted!r}; available: {listed}")
    if len(hits) > 1:
        matched = ", ".join(repr(name) for name in hits)
        raise ValueError(
            f"{len(hits)} inputs matched {wanted!r}; use a more specific "
            f"name or --match exact: {matched}"
        )
    return hits[0]


def format_message(message: object) -> str:
    """One compact line for a mido message (returns ``""`` to skip it)."""

    kind = getattr(message, "type", "")
    if kind not in REPORTED_TYPES:
        return ""
    channel = int(getattr(message, "channel", 0)) + 1
    if kind == "pitchwheel":
        return f"pitchwheel  ch={channel:<2} pitch={int(getattr(message, 'pitch', 0)):+6d}"
    if kind == "control_change":
        control = int(getattr(message, "control", -1))
        value = int(getattr(message, "value", 0))
        label = {
            1: "mod wheel",
            64: "sustain",
            74: "MPE timbre",
        }.get(control, "")
        tail = f"  ({label})" if label else ""
        return f"CC          ch={channel:<2} control={control:<3} value={value:<3}{tail}"
    velocity = int(getattr(message, "velocity", 0))
    suffix = " (note-off)" if kind == "note_on" and velocity == 0 else ""
    return (
        f"{kind:<11} ch={channel:<2} note={int(getattr(message, 'note', 0)):<3} "
        f"velocity={velocity}{suffix}"
    )


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(
        description="Print the raw MIDI a controller sends (read-only probe)",
    )
    parser.add_argument(
        "--port",
        default="Launchkey",
        help="input name or substring (default: Launchkey)",
    )
    parser.add_argument(
        "--match",
        choices=("contains", "exact"),
        default="contains",
        help="how --port matches a name (default: contains)",
    )
    parser.add_argument(
        "--seconds",
        type=float,
        default=0.0,
        help="stop after N seconds (0 = run until Ctrl+C)",
    )
    parser.add_argument(
        "--list",
        action="store_true",
        help="list the available input names and exit",
    )
    return parser


def main(argv: list[str] | None = None) -> int:
    args = build_parser().parse_args(argv)
    try:
        import mido  # intentionally lazy
    except ImportError as exc:  # pragma: no cover - hardware path
        print(
            f"mido is not available ({exc}). Run with "
            "`uv run --with mido --with python-rtmidi python run/midi_probe.py`.",
            file=sys.stderr,
        )
        return 1
    try:
        names = tuple(str(name) for name in mido.get_input_names())
    except Exception as exc:  # pragma: no cover - hardware path
        print(
            f"could not enumerate MIDI inputs ({exc}); is python-rtmidi "
            "installed?",
            file=sys.stderr,
        )
        return 1
    print("Inputs: " + (", ".join(repr(name) for name in names) or "(none)"))
    if args.list:
        return 0
    try:
        chosen = choose_port(names, args.port, args.match)
    except ValueError as exc:
        print(str(exc), file=sys.stderr)
        return 1
    try:
        port = mido.open_input(chosen)
    except Exception as exc:  # pragma: no cover - hardware path
        print(f"could not open {chosen!r}: {exc}", file=sys.stderr)
        return 1
    print(
        f"Listening on {chosen!r} — move the pitch wheel and the mod wheel "
        "(Ctrl+C to stop)"
    )
    deadline = None if args.seconds <= 0 else time.monotonic() + args.seconds
    try:
        for message in port:
            line = format_message(message)
            if line:
                print(line, flush=True)
            if deadline is not None and time.monotonic() >= deadline:
                break
    except KeyboardInterrupt:
        pass
    except Exception as exc:  # pragma: no cover - hardware path
        print(f"input failed: {exc}", file=sys.stderr)
        return 1
    finally:
        close = getattr(port, "close", None)
        if callable(close):
            try:
                close()
            except Exception:
                pass
    print("Probe stopped.")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
