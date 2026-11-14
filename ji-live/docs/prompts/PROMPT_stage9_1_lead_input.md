# Stage 9.1 Agent Prompt — Lead MIDI input, recording, quantization and saved melodies

You are implementing **Stage 9.1** of the JI live-performance system. Stages
0–8.5 are owner-accepted. Stages 8.6, 8.7, 8.8 and 9.0 are built and awaiting
the owner's checks; their documented behaviour is the baseline. Treat every doc
as authoritative for what the code does today and do not regress it.

This stage makes the **lead playable**: a MIDI controller (and the QWERTY
keyboard) plays notes that sound **in the current JI pitch-class set**, the
played line can be **recorded and quantised to the beat/groove**, saved as a
named melody, and then **kept transforming through the existing lead Variation
system**.

The owner is the authority on the sound and on whether the gesture feels right.
The agent environment has **no MIDI hardware, no IAC, no Ableton and no audio**;
the evidence is the pure Python harness plus honest reporting. **Do not claim a
human listening pass.**

**Numbering note:** 9.1 is the chronological increment after 9.0. Stage 9's
original theme is granular/glitch FX, but this is a lead/performance increment;
list it as such in the design doc's §8.10/§9 tables rather than forcing it into
the FX section.

---

## 0. What the owner said (verbatim, 2026-11-14)

> "i want to add a new feature to my project: for the lead i want to be able to
> play midi with a midi controller or the keyboard, and have that output pitches
> in the pitch class. i want to be able to record that and have it quantized to
> the beat/groove, (i can input midi melodies and they are saved), then they can
> be continually modified using the existing lead variation."

The owner then answered the design questions. **These decisions are settled — do
not re-litigate them or add options "just in case".**

| # | Question | Owner's decision |
|---|---|---|
| 1 | Pitch mapping | **Nearest frequency class to the MIDI input.** No other mapping mode ships |
| 2 | Live play with the transport stopped | **Yes** — the controller monitors any time; *recording* still needs Play |
| 3 | Sequencer while playing live | **Paused while input is incoming** — the sequencer stops, and resumes on the next bar line when live is released |
| 4 | Variations on a played phrase | **Destructive, exactly like the generated line**, plus a **Revert** that restores the recorded base. Aleatoric composition is part of the project |
| 5 | Performance states | **Self-contained**: the state carries the lead notes inline ("minimum structure to start from; improvisation is still the main goal") |
| 6 | MIDI files / QWERTY | **No `.mid` import or export.** The **QWERTY keyboard is in scope and togglable** |

Related owner statements that shape the stage:

* "nearest frequency class to the midi input" — the mapping is *pitch-faithful*,
  never a fixed scale-degree keyboard.
* "alesatoric composition is part of the project, and a revert should cover all
  use cases" — variations compound; Revert is the safety net.
* "the purpose of saved states is mainly to be able to save some minimum
  structure to be able to start with" — a state must restore the played line and
  its variation history with no external file.

Where this prompt and `docs/PROPOSAL_stage9_1_lead_input.md` differ, **this
prompt wins** (it is newer); update the proposal if you find a stale sentence.

---

## 1. Read these files first

Read the code and docs, not just the summaries.

```text
docs/PROPOSAL_stage9_1_lead_input.md   the design brief this prompt implements
docs/README_stage8_3d.md              the lead voice contract (C5 depends on it)
docs/README_stage8_5.md               performance-state format + versioning policy
docs/README_stage8_7.md               breath: playback-only timing, never baked in
docs/README_stage8_2.md               groove/swing + the next-quarter-beat queue
docs/README_stage8.md                 rhythm/transport, BeatClock, drum scheduler
docs/START_HERE.md                    authoritative start/stop/flag sheet (update)
docs/GUI_PARAMETERS.md                every control, one line each (update)
docs/README_mpe.md                    MPE bus/channel/bend conventions
docs/README_live_rig.md               audio routing (the input adds no bus)
docs/design_doc_10.28.10.50           §5, §6.18, §7.1, §7.5, §8.9, §9, §13, §14
control/lead.py                       LeadNote/Motif/Engine/Scheduler/Rig
control/midi_rig.py                   MIDIRig: output buses only (do not add input here)
control/mpe_output.py                 frequency_to_pitch, MPEOutput, fake outputs
control/rhythm.py                     RhythmState/BeatClock/next_beat_time
control/quantize.py                   BeatQuantizer, PHASE_LEAD
control/bass.py                       swing_offset (the one swing definition)
control/state.py                      PerformanceState, StateLibrary, name rules
control/ji_bridge.py                  ownership, quantized requests, state recall
control/osc_server.py                 handlers + validators (pure)
control/groove.py                     GrooveGrid/Groove, for reference only
ui/osc_control.py                     pure builders/parsers/store
ui/ji_ui.py                           compact PyQt6 window, Lead group
run/ji_station.py                     one launcher (flags, --check, --dry-run)
config/lead_config.json               lead defaults
tests/test_lead.py                    the lead suite + fake output/clock idioms
tests/test_state.py                   state round-trip/validation idioms
tests/test_ui.py                      pure + optional Qt harness
tests/test_startup.py                 launcher/start-file/docs checks
docs/prompts/PROMPT_stage9_0_harmonic_delay.md   prompt-format precedent
```

Run Python from `ji-live/`. The uv-managed Python lives at `~/.ji-tools/uv`
even when `uv` is not on `PATH`:

```bash
~/.ji-tools/uv run --with python-osc python tests/test_lead.py
```

`mido`, `python-rtmidi`, PyQt6, sclang/scsynth, IAC and Ableton are **not**
required for the pure harness. Never import `mido` at module import time.

---

## 2. Current state you must preserve

- **Stage 8.3d lead**: monophonic MPE on `IAC Driver Bus 5`, member channel 2,
  48-semitone bend range, `config/lead_config.json` its only config, `--lead`
  ships **off**, a missing Bus 5 disables only the lead.
- **The lead is generated today.** `LeadNote(step, length, degree, octave,
  ratio_hint, velocity)`; `generate_motif()`; `apply_variation()`; eight
  variations (`identity, transpose, invert, retrograde, thin, ornament, rhythm,
  random`); `Vary`/`variation_bars`; the transport-gated scheduler; `PHASE_LEAD`.
- **Consistency contract**: `LeadEngine.on_set(ratios, anchor, fundamental)`
  stores the snapshot and **re-projects** — it never regenerates. A note keeps
  `ratio_hint` while that class survives, else falls back to
  `current_set[degree % len]`, octave unchanged.
- **The GUI/bridge split**: the bridge owns performance state; the UI sends OSC
  and mirrors status. The UI never talks to Ableton/sclang directly.
- **Beat quantisation**: while the transport runs, performance changes land on
  the next quarter beat (`PHASE_LEAD = 7`, order
  `home, mutation, octave, groove, drums, bass, arp, lead, drone, midi, state`);
  while it is stopped they apply immediately. Tempo/meter/transport are always
  immediate.
- **One swing definition**: `bass.swing_offset(step, swing, step_seconds,
  steps_per_beat)` — odd 8th/16th steps late by `swing*0.5*step`; a 1/4 grid is
  straight. Never fork it.
- **Breath is playback-only**: its offset is added to the scheduler's sleep
  target, never to the canonical grid and never to stored notes.
- **State format**: `states/<name>.json`, `format`/`version: 1`, atomic
  `.tmp`+fsync+`os.replace`, names 1–40 chars, never writes `config/`. New keys
  must be optional with defaults; absent keys fall back to shipped values.

### Do not change

- `/ji/freqset/update` and its payload; `synthdefs/*`; `startup.scd` audio
  behaviour; `tests/sc/test_live_rig.scd` DSP; the four-channel audio map; loops
  (still sclang-owned, session-only, excluded from states).
- `control/mutation.py`, `control/home.py`, `control/glide.py`,
  `control/groove.py` semantics, `control/breath.py` semantics,
  `control/rhythm.py` semantics.
- The first **16** values and their order in `/ji/lead/state`. This stage
  appends `gate_mode` as the 17th (see §7.6).
- The `PHASE_*` ordering.
- The generated lead's output when the input is off and no melody is loaded:
  same seed → same motif → same MIDI stream.
- `on_set()` must keep re-projecting only.
- **No sclang change at all.** No `MIDIIn`, no `MIDIClient.init`, no new
  SynthDef, no new bus, no new OSC address to port 57120.
- One window, no tab/dialog. Keep the compact rows and the scroll area as the
  small-screen net.
- **No `mido` import at module import time** (`tests/test_lead.py` asserts it).
- Existing harnesses pass unmodified except the intended additions to
  `tests/test_lead.py`, `tests/test_state.py`, `tests/test_ui.py` and
  `tests/test_startup.py`.

---

## 3. What this stage adds

```text
controller ──mido input──┐
                         ├─> LeadInputRig.note_on/off ─> nearest-class mapper
QWERTY keys ──OSC────────┘            │                         │
                                      │                         v
                                      │                  MPEOutput (Bus 5)
                                      └─> LeadRecorder (beat clock)
                                                    │
                                            load_motif() ─> LeadEngine
                                                              │
                                              LeadScheduler + apply_variation()
```

Five capabilities, in this order of dependency:

- **C1 play in** — keys sound on Bus 5 as the nearest class of the current set,
  in the register played.
- **C2 record** — played notes become `(step, length, degree, octave,
  ratio_hint, velocity)`, aligned to the bridge beat clock.
- **C3 quantize** — onsets/lengths snap to the lead `Grid`, with a strength, on
  the **swung** grid; the stored step is unswung.
- **C4 save** — named melodies in `melodies/<name>.json`, plus inline notes in
  performance states.
- **C5 keep varying** — a played phrase becomes the lead motif, so `Vary`,
  `Var every`, the combo and all eight variations keep working; `Revert`
  restores the recorded base.

**Out of scope:** `.mid` files; polyphonic/MPE multi-channel input; mapping modes
other than `nearest`; auto-duck; sub-step microtiming; pitch-bend/aftertouch
expression (ignore them, log once if you like); two controllers merged; any
sclang/audio change.

---

## 4. Part A — MIDI input and the nearest-class mapper

Create **`control/lead_input.py`**. Pure modules except one lazy mido adapter.

### 4.1 The mapper (pure, the heart of C1)

```python
@dataclass(frozen=True, slots=True)
class MappedNote:
    midi_in: int
    frequency: float      # what sounds (Hz)
    ratio: float          # the set's own float
    degree: int           # index in the sorted current ratios
    octave: int           # relative to the fundamental, BEFORE base_octave
    cents: float          # signed: played key vs the class (+ = key sharp)

def map_note(
    midi_note: int,
    ratios: Sequence[float],
    fundamental: float,
    base_octave: int = 0,
) -> MappedNote: ...
```

Exact maths (document it in the stage README):

```text
f_in  = 440 * 2 ** ((midi_note - 69) / 12)          # mpe_output.midi_note_frequency
oct   = floor(log2(f_in / fundamental))
f_oct = f_in / 2 ** oct                             # folded into [F, 2F)
d(r)  = 1200 * log2(f_oct / (fundamental * r))      # signed cents
ratio = the ratio minimising |d(r)|
f_out = fundamental * ratio * 2 ** oct
degree = sorted_ratios.index(ratio)
note.octave = oct - base_octave                     # so projection reproduces f_out
```

Rules:

- `ratios` are sorted ascending, positive, deduplicated (reuse `lead._clean_ratios`);
  an empty/invalid set raises `ValueError`.
- `midi_note` must be an int 0–127; `fundamental > 0`, finite.
- A key exactly on a class returns **that float** (cents 0), so playback through
  `LeadEngine._project_locked` is bit-exact.
- Ties between two classes are impossible for real just ratios; if they occur,
  prefer the lower ratio and note it.
- No other mapping mode. A comment may note that `cycle`/`free`/`anchor` were
  considered and deferred; do not implement them.

Worked values for the acceptance tests (F = 220, just major
`1, 9/8, 5/4, 4/3, 3/2, 5/3, 15/8`):

| MIDI | note | `oct` | class | cents | degree/octave |
|---|---|---|---|---|---|
| 61 | C#4 277.18 | 0 | 5/4 = 275.00 | +13.7 | 2 / 0 |
| 64 | E4 329.63 | 0 | 3/2 = 330.00 | −1.9 | 4 / 0 |
| 69 | A4 440.00 | 1 | 1/1 = 220.00 | 0.0 | 0 / 1 |
| 66 | F#4 369.99 | 0 | 5/3 = 366.67 | +15.6 | 5 / 0 |

### 4.2 The port adapter (one lazy mido import)

```python
class MidiInputPort:
    """Lazy mido input: open, callback, close, list names."""
    def __init__(self, port_name: str, *, match: str = "contains",
                 channel: int = 0, callback: Callable[[str, int, int], None]): ...
    def available(self) -> tuple[str, ...]: ...    # mido.get_input_names(), lazy
    def resolve(self) -> str: ...                  # contains/exact; 0 or >1 -> raise
    def open(self) -> None: ...                    # mido.open_input(name, callback=...)
    def close(self) -> None: ...
```

- `match: "contains"` picks the single name containing the configured string
  (case-insensitive); `"exact"` requires equality. **Zero matches → raise with
  the available list. More than one match → raise and list them**, asking for a
  more specific name; never silently pick one.
- `channel` 0 = omni, 1–16 filters `msg.channel + 1`.
- The callback translates `note_on` (velocity > 0), `note_on` with velocity 0
  (= note_off), `note_off`, and `control_change` 64 (sustain) into
  `("on"|"off"|"sustain", midi, value)`. Ignore everything else (pitch bend,
  aftertouch, clock) silently.
- `open_input(name, callback=self._on_message)`; the callback runs on mido's
  reader thread — **no blocking, no disk I/O, no OSC storm**.
- A device disappearing is caught and reported as one `[LEAD-IN] input lost: …`
  line; the rest of the rig keeps running. `refresh()` closes and re-opens.
- Accept an injected fake port for tests (`port=` argument), exactly like
  `MPEOutput(output=...)`.

### 4.3 The input rig

```python
class LeadInputRig:
    def __init__(self, lead_rig, rhythm, settings, *, port=None,
                 library=None, now=time.monotonic): ...
    # note entry (the ONE entry point for controller and QWERTY)
    def note_on(self, midi: int, velocity: int) -> None: ...
    def note_off(self, midi: int) -> None: ...
    def sustain(self, down: bool) -> None: ...
    # lifecycle
    def start(self) -> bool: ...        # open the port; failure disables input only
    def stop(self) -> None: ...         # note-off + CC 123 + close
    def refresh(self) -> bool: ...
    # record (Part C)
    def start_record(self) -> bool: ...
    def stop_record(self, reason: str = "user") -> None: ...
    # status
    @property
    def live(self) -> bool: ...
    @property
    def recording(self) -> bool: ...
    def state_args(self) -> list: ...
```

Note ownership (one member channel, monophonic):

- Track `_held_midi: int | None`. On `note_on`: if a note is held, send
  `note_off` first, then `pitch_bend` + `note_on` (bend before note-on, like
  `LeadScheduler._play`).
- On `note_off`: only release the held note if it matches; a mismatched or
  already-released key is ignored.
- **Sustain (CC 64)**: while down, defer note-offs; on release, release the note
  if its key is up. A note-on while sustain is down retriggers normally.
- Only play when `lead_rig.source in ("live", "record")`. In `generated`/`played`
  the input is silent (the `Live`/`Rec` controls are the gate). This is the
  decided model — do not add a hidden pass-through.
- Velocity: `clamp(round(velocity * settings.velocity_scale), 1, 127)`.
- Every exit path sends note-off + `all_notes_off()`: `stop()`, `Live` off,
  source change, `Lead on` off, port lost, bridge stop. **A transport stop is
  not an exit path**: `live` keeps monitoring with the transport stopped (owner
  decision 2); only an in-flight recording finishes.
- The input rig **never opens or closes Bus 5**; it uses `lead_rig.output`.
- Send the existing `/ji/lead/note` status callback for played notes (deduped,
  same as the scheduler) so the GUI "Last:" line shows what you played.

### 4.4 Failure modes (one line each, never fatal)

```text
[LEAD-IN] disabled: no input port configured; available: 'Launchkey 49', ...
[LEAD-IN] disabled: no input matched 'Foo'; available: ...
[LEAD-IN] disabled: 2 inputs matched 'MIDI'; use a more specific name: ...
[LEAD-IN] input lost: ...; the rest of the rig continues
[LEAD-IN] unavailable: start with --lead for Bus 5
```

---

## 5. Part B — live monitoring, the source switch, QWERTY

### 5.1 The lead `source`

Add to `LeadEngine`/`LeadRig` — the **one** place that decides who drives Bus 5:

| `source` | Sequencer | Input monitor | Meaning |
|---|---|---|---|
| `generated` | runs (today) | silent | accepted default |
| `played` | runs the played motif | silent | hear the take, then `Vary` it |
| `live` | stopped | on | play freely |
| `record` | stopped | on | capture |

- `LeadRig.set_source(name, *, at_time: float | None = None)` validates the name,
  stores it, and starts/stops `LeadScheduler` exactly like
  `set_transport`/`set_enabled` do: start when `port_ok and enabled and
  transport_running and source in ("generated", "played")`; otherwise stop (which
  sends note-off + CC 123).
- Entering `live`/`record` stops the scheduler immediately (never mute it — no
  interleaved bends, no stale gates).
- **Guard the other start paths**: `set_transport(True)` and `set_enabled(True)`
  must not start the scheduler while `source` is `live` or `record` (today they
  start it unconditionally). One shared `_sync_scheduler()` used by
  `set_transport`, `set_enabled`, `set_source` and the state recall is the
  cleanest fix.
- Leaving `live`/`record` for `generated`/`played` while the transport runs must
  start on the **next bar line**, so the phrase re-enters at step 0 on a
  downbeat. Implement `LeadScheduler.start(at_time: float | None = None)`:
  `None` = now (all existing callers unchanged); otherwise the first deadline is
  `at_time`. The bridge computes `rhythm.clock.time_of_beat(next_bar_beat)` and
  passes it. With the transport stopped, pass `None` (step 0 fires immediately).
- `Live` off returns to `played` when a melody is loaded, else `generated`
  (derive it; do not keep a separate "previous source" that can go stale).
- The GUI's `Live` checkbox is **derived from the source**: checked when
  `source` is `live` or `record`. `Rec` with `Live` off implies `live`; a
  finished take returns to `played` and the checkbox clears. Never keep two
  independent notions of "live".
- Expose `source` and `live` for status; `LeadRig.playing` must remain false in
  `live`/`record`.

### 5.2 QWERTY

The GUI owns the keys; the bridge owns the sound.

- Key map in `lead_input.py` (pure, tested), not in the widget:

  ```python
  KEY_SEMITONES = {"a": 0, "w": 1, "s": 2, "e": 3, "d": 4, "f": 5,
                   "t": 6, "g": 7, "y": 8, "h": 9, "u": 10, "j": 11}
  ```

  Base note `60 + 12 * octave_shift`; `z`/`x` shift the octave down/up
  (clamped so every key stays in 0–127). Fixed velocity 100.
- `ji_ui.py`: override `keyPressEvent`/`keyReleaseEvent`.
  Ignore when `event.isAutoRepeat()`; ignore when a `QLineEdit`/`QComboBox` has
  focus (`QApplication.focusWidget()`), when any modifier is held, and when the
  `Keys` toggle is off.
- Send `/ji/lead/input/note_on [midi, velocity]` / `.../note_off [midi]` to the
  **bridge** client.
- On toggle off, focus loss (`focusOutEvent` or `QApplication.focusChanged`),
  window close or source change, send a note-off for every down key.
- The `Keys` toggle is GUI-local: it needs no bridge state, so it cannot
  desync. It does **not** imply `Live`: keys only sound while the source is
  `live` or `record`, so the player must be in a monitoring mode (the status
  line says `Melody: … · live` when they are).

### 5.3 The bridge note handlers

`/ji/lead/input/note_on` and `note_off` call `LeadInputRig.note_on/off` on the
OSC thread. Validate ranges; if the input is not open, ignore silently. Never
queue these on `BeatQuantizer` — they are real-time performance events.

---

## 6. Part C — recording and quantisation

### 6.1 State machine

One `LeadRecorder` and one supervisor thread inside `LeadInputRig`:

```text
idle ──Rec──> count_in ──target beat──> recording ──window end / Stop──> idle
                  └──Stop/cancel────────────┘
```

- **Record requires a running transport** (`[LEAD-IN] record needs the
  transport (press Play)`), because the beat clock is frozen when stopped.
- `count_in` vocabulary `off | 1 beat | 1 bar | 2 bars`; a bar is
  `ceil(bar_quarters)` whole quarter beats (4/4→4, 3/4→3, 6/8→3, 5/8→3, 7/8→4) —
  the same ceil rule as the loop count-in.
- `now = rhythm.clock.beat_at()`.
  `start_beat = ceil((now + count_beats) / bar_quarters) * bar_quarters`
  (always a bar line). `end_beat = start_beat + bars * bar_quarters`.
- No notes are captured or sounded before `start_beat`.
- A **second Rec during count-in skips it** and opens the window at the next
  bar line (mirroring the loop convention and its log line).
- `Stop` during count-in cancels silently; `Stop` during recording keeps the
  take: close open notes at `now`, quantise, load.
- A **transport stop during recording** finishes the take (keep it), logs
  `[LEAD-IN] recording aborted (transport stop); take kept`, and does **not**
  start the scheduler: set `source = "live"` if the `Live` toggle is on, else
  `"played"`.
- On a normal finish, set `source = "played"` (hear the take). The `Live` toggle
  state is remembered, so the next `Live` off returns to `played`.
- The supervisor is a daemon thread that polls every ~5 ms using
  `clock.time_of_beat(...)` recomputed each iteration (a tempo change must not
  break the window), with a generation token so a cancelled take cannot start a
  stale recorder. Do **not** sleep for the whole duration.
- Maximum window: 8 bars (the lead phrase limit).

### 6.2 The recorder (pure)

```python
class LeadRecorder:
    def note_on(self, midi: int, velocity: int, beat: float) -> None: ...
    def note_off(self, midi: int, beat: float) -> None: ...
    def finish(self, *, start_beat: float, grid: str, strength: float,
               swing: float, step_seconds: float, steps_per_beat: int,
               phrase_steps: int) -> tuple[LeadNote, ...]: ...
    @property
    def dropped(self) -> int: ...       # reduced chord notes
```

Rules:

- a re-struck key closes the previous note at the new onset;
- **monophonic reduction = `last`** (fixed in v1): a chord keeps the last note
  and increments `dropped`, logged once at finish;
- a note held past the window closes at the window end;
- notes outside `[start_beat, end_beat)` are dropped;
- zero/negative length → 1 step;
- `finish()` is idempotent-ish: it returns notes and resets the capture.

### 6.3 The quantiser (pure, exact contract)

```python
def quantize_step(position_beats, start_beat, grid_quarters, strength,
                  swing, step_seconds, steps_per_beat) -> int:
    s = (position_beats - start_beat) / grid_quarters      # in grid steps
    k = round(s)
    swung = k + swing_offset(k, swing, step_seconds, steps_per_beat) / step_seconds
    s_q = s + strength * (swung - s)                       # 0..1
    return int(round(s_q))
```

- `strength` 0–1 (GUI 0–100 %): 1 = exact grid, 0 = as played (the stored step is
  still the rounded integer).
- **Quantise against the swung grid and store the unswung step.** Playback
  re-applies `bass.swing_offset`, so the take lands where it was played and
  still follows later groove changes. Never bake swing into `LeadNote`.
- `swing` = `engine.effective_swing()` (groove link or the lead's own swing) at
  record time.
- Length = `max(1, off_step - on_step)`, clamped to the next onset and the
  phrase end by `lead._normalize`.
- **Breath is not part of quantisation** (playback-only).
- Acceptance values: 120 BPM, 4/4, grid 1/8 (0.5 beats/step), record from beat
  4: onsets at 4.02/4.52/5.08/6.06 beats → steps 0, 1, 2, 4; offsets giving
  lengths 1, 1, 2, 1.

### 6.4 Finish → load → play

1. Quantise to `LeadNote`s; `dropped` logged once.
2. `LeadEngine.load_motif(...)` with the take (Part D), `grid` and `phrase_bars`
   = `bars`, `gate_mode = "note"`.
3. Set `source = "played"`. If the transport still runs, start the scheduler at
   **`end_beat`** (the take ends on a bar line, so the phrase loops seamlessly);
   if that instant has already passed by more than ~50 ms, start at the next bar
   line instead. Use the same `at_time` path as §5.1. If stopped, stay stopped.
4. Log `[LEAD-IN] recorded 13 notes / 2 bars (grid 1/8, quant 100 %, 2 chord
   notes reduced)`.
5. Send `/ji/lead/state` + `/ji/lead/input/state` once (no per-note spam).

---

## 7. Part D — the played phrase inside `LeadEngine`

This is the only change to the accepted lead engine. Keep every generated-line
behaviour identical.

### 7.1 `LeadMotif` gains four defaulted fields (appended at the end)

```python
source: str = "generated"                    # "generated" | "played"
base_notes: tuple[LeadNote, ...] = ()        # the take as recorded (Revert target)
history: tuple[tuple[str, int], ...] = ()    # (variation name, index used), in order
melody_name: str = ""                        # provenance for the status line
```

`LeadNote` and `LeadProjected` are unchanged. `LeadMotif` is frozen; existing
construction sites are keyword-only, so defaults are safe.

**Why the history carries the index**: `apply_variation` is seeded by
`(seed, index)`, and `Vary` applies at `index + 1` while the `Variation` combo
applies at the *current* index. Storing `(name, index)` makes a replay exact
whatever triggered it; storing only the name would shift the RNG and change the
line. JSON uses `[["invert", 1], ["rhythm", 2]]`.

### 7.2 `load_motif`

```python
LeadEngine.load_motif(notes, *, grid, phrase_bars, meter_num, meter_den,
                      name="", base_notes=None, history=(),
                      variation="identity", variation_index=0) -> LeadMotif
```

- Validates each note (integer `step >= 0`, `length >= 1`, `degree >= 0`,
  `octave` in −8..8, `ratio_hint > 0`, `velocity` 1..127), normalises, and sets
  `phrase_steps = phrase_bars * lead.steps_per_bar(meter_num, meter_den, grid)`.
- Updates the settings atomically in the same lock: `grid`,
  `phrase_bars`, and `gate_mode = "note"` (see §7.6). `base_notes` defaults to
  `notes` when not given.
- Stores `source = "played"`, `base_notes`, `history`, `melody_name`,
  `variation` and the given `_variation_index` on the motif; sets `_born = True`,
  bumps `_version`, clears the projection cache.
- **Never** calls `generate_motif`.

Also add `LeadEngine.clear_melody()` / `LeadRig.clear_melody()`: return to a
fresh generated motif (`source = "generated"`, regenerate from the seed,
`gate_mode` untouched), used by the GUI `Clear` button and by a state recall
whose `lead.notes` is empty.

### 7.3 Source-aware regeneration (the `_GENERATION_KEYS` table)

`_GENERATION_KEYS` is unchanged for generated motifs. When
`self._motif.source == "played"`, a shape-key change must **not** regenerate:

| Setting | Played-motif behaviour |
|---|---|
| `grid` | rescale `base_notes` from their stored steps (`new_step = round(old_step * old_gq / new_gq)`), rebuild the line by replaying `history` (or use the rescaled take when there is none), then `_normalize`; rebuild `phrase_steps` |
| `phrase_bars` | crop to the new length, or tile the notes to fill a longer phrase; `_normalize` clamps lengths |
| `density` | field updates only; one `[LEAD] density has no effect on a played line` line; no note change |
| `seed` | field updates; `_variation_index = 0` and `history` cleared (the chain is re-seeded); the notes are unchanged |
| `octave_range` | field updates; clamp recorded octaves into `±range` only when `range > 0` |
| meter change | rescale/crop to the new bar length, like `grid` + `phrase_bars` |
| `variation` | apply via `_apply_variation_locked` (same as today) |
| everything else | playback-only, unchanged |

`set_settings()` (state recall) must apply the same policy: never regenerate a
played motif implicitly. The state recall loads the motif explicitly (§8.5).

### 7.4 Variations, history and Revert

- `_vary_locked` / `set_setting("variation", …)` keep applying
  `apply_variation(name, self._motif, seed=seed, index=self._variation_index,
  ratios=self._ratios)` and then:
  - append `(name, self._variation_index)` to `history`;
  - **re-attach** `source`, `base_notes`, `melody_name` from the pre-variation
    motif — this matters most for `random`, which returns a freshly generated
    motif and would otherwise lose the Revert target.
- `apply_variation()` stays pure and does not touch `history`.
- `revert() -> LeadMotif`: restore `base_notes`, clear `history`, set
  `_variation_index = 0`, keep the settings, `source` stays `"played"`; log one
  `[LEAD] reverted to the recorded base (13 notes)`.
- `set_variation_index(index, force=False)`:
  - generated: today's behaviour (regenerate from the seed);
  - played: rebuild from `base_notes` by replaying `history[:index]` entry by
    entry, applying each `(name, used_index)` with
    `apply_variation(name, motif, seed=seed, index=used_index,
    ratios=ratios)` — the stored index reproduces the original RNG exactly.
    If `index > len(history)`, keep applying the selected variation with
    `index = len(history) + 1, +2, …` until the counter reaches `index`.
    `force=True` replays from the base the same way.
- Compounding is intended (owner: aleatoric composition); only `Revert` undoes.

### 7.5 Status and description

- `describe_phrase()` appends the source, e.g.
  `seed=1 variation=identity #0 steps=16 notes=12 source=played "intro line"`.
- `LeadSettings.state_args` gains `gate_mode` as the **17th** value (§7.6).
- The GUI status line reads
  `Lead: on | 1/8 · 2 bars · 0.75 | identity #0 | seed 1 | played "intro line"`.

### 7.6 `gate_mode` (new setting)

The scheduler caps every gate at `min(settings.gate_beats, note.length × step)`.
A recorded 2-beat note would be cut by the default 0.5-beat gate, so add:

```python
gate_mode: str = "beat"      # "beat" = today; "note" = the note length wins
```

- Validation `beat|note`; `with_change`/`create` support it.
- `LeadScheduler._play`: when `gate_mode == "note"`, use
  `note.length * step_quarters * beat_seconds` (no `gate_beats` cap).
- Recording and melody loading set `gate_mode = "note"`; the GUI exposes a
  `Note len` checkbox (checked = note mode) so the player can go back.
- Because `LeadSettings` is serialised field-by-field, this round-trips through
  states automatically. `parse_lead_state` in `ui/osc_control.py` must accept
  **16 or 17** values (a stale GUI must not wedge), and the Qt sync must set the
  checkbox silently.

---

## 8. Part E — melody library and performance states

### 8.1 `control/melody.py`

```python
DEFAULT_MELODY_DIR = PROJECT_ROOT / "melodies"
MELODY_FORMAT = "ji-lead-melody"
MELODY_VERSION = 1
MELODY_EXTENSION = ".json"
MELODY_TEMP_EXTENSION = ".json.tmp"

class MelodyError(ValueError): ...       # .kind = name|format|validation|missing|io

@dataclass(frozen=True, slots=True)
class Melody:
    name: str
    grid: str
    phrase_bars: int
    meter_num: int
    meter_den: int
    bpm: float
    quantize: float
    swing: float
    notes: tuple[lead.LeadNote, ...]
    base_notes: tuple[lead.LeadNote, ...]
    history: tuple[tuple[str, int], ...]
    variation: str
    variation_index: int
    ratios: tuple[float, ...]
    fundamental: float
    saved_at: str = ""

class MelodyLibrary:
    def __init__(self, directory=None): ...
    def ensure(self) -> Path: ...
    def names(self) -> tuple[str, ...]: ...
    def count(self) -> int: ...
    def path_for(self, name) -> Path: ...
    def save(self, melody: Melody) -> Path: ...
    def load(self, name) -> Melody: ...
    def delete(self, name) -> bool: ...
```

Reuse the **name rules** from `control/state.py` (`validate_state_name`) so the
two libraries cannot drift; wrap a `StateError` into `MelodyError` with the same
message and kind. Atomic save via `.tmp` + flush + fsync + `os.replace`; refuse a
case-only duplicate; never write into `config/`.

### 8.2 File format

```json
{
  "format": "ji-lead-melody",
  "version": 1,
  "name": "intro line",
  "saved_at": "2026-11-14T14:02:11",
  "grid": "1/8",
  "phrase_bars": 2,
  "meter_num": 4,
  "meter_den": 4,
  "bpm": 120.0,
  "quantize": 1.0,
  "swing": 0.0,
  "set": { "fundamental": 220.0, "anchor": 1.0,
           "ratios": [1.0, 1.125, 1.25, 1.3333333333333333, 1.5,
                      1.6666666666666667, 1.875] },
  "variation": { "name": "identity", "index": 1, "history": [["invert", 1]] },
  "base_notes": [ { "step": 0, "length": 1, "degree": 2, "octave": 0,
                    "ratio_hint": 1.25, "velocity": 104 } ],
  "notes":      [ { "step": 0, "length": 1, "degree": 2, "octave": 0,
                    "ratio_hint": 1.25, "velocity": 104 } ]
}
```

- `base_notes` = the take (Revert target); `notes` = the line at save time;
  `variation.history` is a list of `[name, index_used]` pairs (see §7.1).
- `set` is provenance only: the melody is set-relative and loads against the
  **current** set.
- Validation: `format` exact, `version <= 1` (newer → one clear error), notes
  parsed through the same `LeadNote` rules as §7.2, `grid` in `lead.GRIDS`,
  `phrase_bars` 1–8, meter valid, `ratios` 1–12 positive ascending, note `step`
  < `phrase_bars × steps_per_bar`.
- `load()` returns a `Melody`; the bridge turns it into `load_motif(...)`.
- Loading a melody also sets the lead `Grid`/`Phrase` to the file's values, so
  playback matches the take; if the current meter differs, log one line.

### 8.3 Performance states — inline notes (self-contained)

`state.py`'s `PerformanceState` gains a `LeadPlaySection` and the `lead` section
gains four optional keys:

```json
"lead": { "...every LeadSettings field...", "variation_index": 3,
          "notes": [ ... ], "base_notes": [ ... ],
          "history": [["invert", 1], ["rhythm", 2]],
          "melody": "intro line", "source": "played" }
```

- New dataclass `LeadPlaySection(notes=(), base_notes=(), history=(),
  melody="", source="generated")`; `_settings_dict`/`_settings_from` stay as they
  are (settings only), and the four keys are read/written beside them.
- Validation reuses the note rules; on any bad note the whole load aborts before
  applying anything (the 8.5 rule).
- Missing keys → defaults (old states load unchanged; `version` stays 1).
- `saved_at` remains informational.
- A state with `notes` present and `source == "played"` is a played line; with
  `notes` empty it is a generated line.

### 8.4 Recall order (bridge `_restore_lead`)

1. `set_lead_settings(state.lead)` (may change grid/phrase; the played motif is
   not regenerated by the policy).
2. If `state.lead_play.notes`: `lead_rig.load_motif(...)` with the inline notes,
   base, history, source, melody name and variation index.
   Else: `lead_rig.clear_melody()`.
3. `set_lead_variation_index(state.lead_variation_index, force=True)` **after**
   the motif is loaded (a played motif replays its history; a generated one
   regenerates, as today).
4. The existing single `send_set(glide_time)` follows; the lead re-projects.
5. Send `/ji/lead/state`, `/ji/lead/input/state`, `/ji/lead/melody/list`.
6. `[STATE]` logs one line; `Revert` works immediately after the recall.

Order matters: the state's own `grid`/`phrase_bars` are applied by
`set_lead_settings`, and `load_motif` re-applies the melody's structure — the
melody file (or inline notes) wins, which is the point of a self-contained
recall.

---

## 9. Controls, OSC, config and flags

### 9.1 New OSC addresses (all additive)

| Address | Args | Direction |
|---|---|---|
| `/ji/lead/input/set_enabled` | int 0/1 (Live) | UI → bridge |
| `/ji/lead/input/set_quantize` | float 0..1 | UI → bridge |
| `/ji/lead/input/set_count_in` | string `off\|1 beat\|1 bar\|2 bars` | UI → bridge |
| `/ji/lead/input/set_bars` | int 1..8 | UI → bridge |
| `/ji/lead/input/note_on` | int midi, int velocity | UI → bridge (QWERTY) |
| `/ji/lead/input/note_off` | int midi | UI → bridge (QWERTY) |
| `/ji/lead/input/record` | none | UI → bridge |
| `/ji/lead/input/stop` | none | UI → bridge |
| `/ji/lead/input/revert` | none | UI → bridge |
| `/ji/lead/input/clear` | none (back to generated) | UI → bridge |
| `/ji/lead/melody/save` | string name | UI → bridge |
| `/ji/lead/melody/load` | string name | UI → bridge |
| `/ji/lead/melody/delete` | string name | UI → bridge |
| `/ji/lead/melody/request` | none | UI → bridge |

Status (bridge → UI):

```text
/ji/lead/input/state   [live, port_ok, port_name, quantize, count_in, bars,
                        recording, count_beats, note_count, source,
                        melody_name, last_in_note, last_out_note, last_cents]
                        # 14 values, exact order
/ji/lead/melody/list   [count, name_1 .. name_N]
/ji/lead/melody/state  [count, last_saved, last_loaded, message]
```

Rules: validate in `osc_server` **and** in the engine; one small state message
per change, never per note; send `input/state` at start, on `/ji/ui/hello`, after
every applied change, after a record state change, after a source change, and
after a melody action.

**Quantisation**: `revert`, `clear` and a melody **load** are queued on
`PHASE_LEAD` while the transport runs and immediate when it is stopped (they
replace the sounding line). `set_enabled` (Live), `record` and `stop` are
**immediate** — they are start/stop gestures and already align themselves to the
bar (the count-in and the record window compute their own bar lines).
Save/delete/list/request are immediate file ops. `note_on/off` are **never**
queued.

### 9.2 `/ji/lead/state` — 16 → 17 values

Append `gate_mode` (string `beat|note`) as value 17, after `playing`. Do not
reorder the first 16. Update `LeadSettings.state_args`, `parse_lead_state`
(accept 16 or 17), the Qt sync, and every test/sclang echo that asserts 16.

### 9.3 Config — `config/lead_input_config.json` (new, read-only)

```json
{
  "port": "Launchkey",
  "match": "contains",
  "channel": 0,
  "quantize": 1.0,
  "count_in": "1 bar",
  "bars": 2,
  "velocity_scale": 1.0,
  "octave_shift": 0
}
```

- `port` empty + `--lead-in` → list the available inputs and disable the input
  (one line), never fail the rig.
- `match` `contains|exact`; `channel` 0 (omni) or 1–16; `quantize` 0–1;
  `count_in` one of the four strings; `bars` 1–8; `velocity_scale` 0.5–2.0;
  `octave_shift` −2..2.
- Unknown keys are ignored; a bad value disables the input only.

Add `config/lead_input_config.json` to the layout docs and `--check`.

### 9.4 Launcher and bridge flags

```text
--lead-in                  enable the MIDI input (off by default)
--lead-in-port <name>      controller name/substring (overrides the config)
--lead-in-config <path>    alternative config
--melody-dir <path>        melody library folder (default melodies/)
```

- `--lead-in` without `--lead` prints one line and is ignored; `--lead` without
  `--lead-in` is exactly today's rig.
- The launcher forwards them to the bridge and documents them in its `--help`.
- `--check` prints `mido.get_input_names()` beside the output names, verifies the
  configured input name when `--lead-in` is set, and validates
  `--melody-dir` (creates it, reports the count) exactly like `--state-dir`.
  `--dry-run` lists the new flags.
- **`tests/test_startup.py` asserts every launcher flag appears in
  `docs/START_HERE.md`** — update both or the suite fails.
- `JIBridge` constructs `LeadInputRig` only when `lead_in` and `lead` are both
  on, after `midi.start()`; it owns the melody library (`--melody-dir`).

---

## 10. GUI

Add **one** compact group, **Lead input**, directly under **Lead** (two rows +
status; do not grow the Lead group):

```text
Lead input
Port: Launchkey 49 [Refresh]  [x] Keys  [x] Live  [Rec] [Stop] [Revert] [Clear]
Quant [----100%]  Count-in [1 bar v]  Bars [2]  Melody [intro line v] Name [______] [Save] [Load] [Del]
Input: Launchkey 49 - idle - 13 notes | Melody: intro line (13 notes) - played
```

- **`Port:` is a read-only label** fed by `input/state.port_name`, plus a
  `Refresh` button (`refresh()` closes/re-opens the port). The bridge is the
  authority on ports; the GUI does not enumerate MIDI devices and no ports
  address is added. A missing controller is fixed by editing
  `config/lead_input_config.json` (or `--lead-in-port`) and pressing `Refresh`.
- `Keys` (QWERTY), `Live`, `Rec`, `Stop`, `Revert`, `Clear`; `Quant` knob;
  `Count-in` combo; `Bars` spin; `Melody` combo populated from
  `/ji/lead/melody/list`; `Name` QLineEdit pre-filled from the selection; three
  buttons.
- `Rec` is enabled only while the transport runs and the input is open; `Stop`
  only while counting/recording; `Revert` only when `source == "played"`;
  `Clear` only when a melody is loaded (it returns to the generated line and
  turns `Live` off); `Save` disabled on an empty name; `Load`/`Del` only for a
  name in the list (the State row's rule).
- The melody list arrives on `/ji/lead/melody/list` at bridge start, after
  save/delete, and whenever the UI sends `/ji/lead/melody/request` (send one on
  `/ji/ui/hello`).
- Every knob coalesces at ~30 Hz (existing `Knob`); buttons send immediately.
  Incoming state syncs with signals blocked (no feedback OSC), like the Lead
  group (`ui/ji_ui.py` `_lead_sync`).
- Status line: `Input: <port> · <idle|count 2|rec 1/2 bars> · <n> notes` and
  `Melody: <name> (<n> notes) · <source>`, or `Melody: none (generated line)`,
  where `source` is `generated`, `played`, `live` or `record`.
- `Note len` checkbox for `gate_mode` lives next to the Lead `Gate` knob (one
  small checkbox; no new row).
- Window grows one group (~60 px): `self.resize(1280, 1258)`; measure with Qt and
  bump the `tests/test_ui.py` height bounds (today 1230 → about **1290**).
  Status stays reachable; the scroll area remains the net.
- No new tab, dialog or second window. `Keys` must not fire while the melody
  name box has focus.

---

## 11. Tests

### 11.1 New `tests/test_lead_input.py` (pure; no mido, no Qt, no sockets)

Follow the existing harness style (`def test_*`, `main()`, prints
`ALL ... PASSED`, exits nonzero on failure, adds the project root to `sys.path`).

- **Mapping**: every class maps to itself with 0 cents; the four worked values
  in §4.1; keys above/below the fundamental; a note whose folded pitch is
  exactly `2F`; a 3-class set; sorted order of `degree`; invalid inputs raise;
  an empty `ratios` sequence normalises to `(1.0,)` like `lead._clean_ratios`;
  the `base_octave` subtraction.
- **Quantiser**: straight 1/8 grid; rounding at .5; strength 0/0.5/1; swing on
  odd steps; `1/8T` grid; 3/4 and 7/8 bars.
- **Recorder**: note on/off pairs; retrigger; chord reduction (`dropped`); note
  held past the end; zero-length; notes outside the window; empty take.
- **Key map**: 12 distinct semitones; `z`/`x` clamp.
- **Port adapter (fake)**: `contains`/`exact`; 0 and >1 matches raise with the
  list; note-on velocity 0 is a note-off; sustain deferred release.
- **Input rig (fake output + fake port)**: bend-before-note-on; note-off on
  retrigger; `stop()` sends note-off + CC 123; silent in `generated`; a
  mismatched note-off is ignored.
- **Record state machine** (fake clock/rhythm): count-in ceil for 5/8, 7/8;
  second Rec skips; Stop during count cancels; Stop during recording keeps the
  take; transport stop finishes without auto-play.
- **Melody library**: save/load round-trip; bad names; case-only duplicate;
  newer version refused; atomic temp cleanup; list sorting; delete.
- **Engine policy**: load a motif; `grid` rescale; `phrase_bars` crop/tile;
  `density`/`seed` no-op; mutations keep the contour; `vary` compounds and
  appends history; `random` keeps the Revert target; `revert`; `set_variation_index`
  replay; `gate_mode` note vs beat; generated output identical when
  `source="generated"`.
- **State**: inline notes round-trip; old state without the keys loads; bad note
  aborts the whole load; recall then Revert; `version` stays 1.
- **No mido import**: after importing `control.lead_input`, `control.melody`,
  `control.lead`, `control.ji_bridge` and `ui.osc_control`, `"mido"` is not in
  `sys.modules` (the `test_lead.py` idiom).

### 11.2 Additions to existing suites

- `tests/test_lead.py`: `gate_mode` default/validation/state_args (17), the
  source-aware regeneration table, `load_motif`/`revert`, and the generated-lead
  stream unchanged.
- `tests/test_state.py`: the `LeadPlaySection` round-trip, optional keys,
  note validation, and `lead.variation_index` unchanged.
- `tests/test_ui.py --pure`: constants, builders, parsers (14-value
  `input/state`, 16-or-17 `lead/state`, melody list/state), validators.
- `tests/test_ui.py --qt`: the Lead input group exists with defaults; `Keys`
  off by default; `Rec`/`Stop`/`Revert`/`Clear` enablement; an incoming
  14-value state syncs silently; Save/Load/Delete send the right name;
  height bound; no extra window.
- `tests/test_startup.py`: the four new flags (defaults, `--dry-run`,
  `--check`), and `START_HERE.md` documents them.

### 11.3 Regression (run from `ji-live/`; all except Qt are required)

```bash
~/.ji-tools/uv run --with python-osc python tests/test_mutation.py
~/.ji-tools/uv run --with python-osc python tests/test_mutation_controls.py
~/.ji-tools/uv run --with python-osc python tests/test_rhythm.py
~/.ji-tools/uv run --with python-osc python tests/test_breath.py
~/.ji-tools/uv run --with python-osc python tests/test_groove.py
~/.ji-tools/uv run --with python-osc python tests/test_quantize.py
~/.ji-tools/uv run --with python-osc python tests/test_bass.py
~/.ji-tools/uv run --with python-osc python tests/test_arp.py
~/.ji-tools/uv run --with python-osc python tests/test_lead.py
~/.ji-tools/uv run --with python-osc python tests/test_lead_input.py
~/.ji-tools/uv run --with python-osc python tests/test_drone.py
~/.ji-tools/uv run --with python-osc python tests/test_home.py
~/.ji-tools/uv run --with python-osc python tests/test_glide.py
~/.ji-tools/uv run --with python-osc python tests/test_state.py
~/.ji-tools/uv run --with python-osc python tests/test_startup.py
~/.ji-tools/uv run --with python-osc python tests/test_ui.py --pure
~/.ji-tools/uv run python tests/test_mpe.py --pure
~/.ji-tools/uv run python tests/test_fretboard.py
# if PyQt6 can run:
QT_QPA_PLATFORM=offscreen ~/.ji-tools/uv run --with python-osc --with PyQt6 \
    python tests/test_ui.py --qt
```

sclang tests are **not** required (no sclang change); if you run them, say so.

---

## 12. Human acceptance checklist (the owner is the authority)

1. **Routing**: the controller is visible to Python
   (`mido.get_input_names()`); Ableton's Track input for that controller is off,
   so no note is doubled. Nothing new is needed in Audio MIDI Setup.
2. **Play in**: with `Live` on, every key sounds on Bus 5 at the nearest class
   in the register played. A key on a class is exact; a key between two classes
   is pulled to the nearer one. `Last:` shows the played note.
3. **Live gating**: `Live` off → the keyboard does not sound over the generated
   line. `Live` on → the sequencer stops; `Live` off → it resumes on the next bar
   line (no mid-bar jump).
4. **QWERTY**: `Keys` on → `a w s e d f t g y h u j` play the same mapped line;
   `z`/`x` shift the octave; typing in the melody name box never plays notes;
   toggling off releases everything.
5. **Record**: Play, press `Rec` with `1 bar` count-in; the count is silent, the
   take starts on a bar line and stops on its own after `Bars`. The take plays
   back quantised, on the grid, with swing if the groove swings. `Stop` early
   keeps the partial take; a second `Rec` during the count skips it.
6. **Groove quantisation**: with `Groove link` on and a swung groove, a take
   played "straight" lands on the swung grid; changing the groove afterwards
   moves the phrase with it (the stored step is unswung).
7. **Variations**: `Vary` transforms the recorded line (transpose, invert,
   retrograde, thin, ornament, rhythm, random); variations compound; `Revert`
   restores the recorded line exactly.
8. **Mutation**: pressing `Mutate` keeps the contour — notes whose class
   survives keep their pitch; only removed classes move. The melody stays
   recognisable.
9. **Save/load**: type a name, `Save`; restart the bridge, `Refresh`/`Load`
   reproduces the line (grid, bars and notes). `Del` removes it. Nothing is
   written into `config/`.
10. **States**: save a state with a played line; recall it in a fresh bridge;
    the line and its variation state come back; `Revert` works immediately.
    An old state without notes loads as a generated line.
11. **No regressions**: generated lead, `Vary`/`Var every`, drone modes, arp,
    bass, drums, groove, breath, loops, home, states, autotune and the existing
    GUI behave exactly as before. Toggling `Live` never leaves a stuck note.
12. **Window**: the Lead input group fits at the default size; Status is
    reachable; the scroll area handles a small screen.
13. Record the result. **9.1 is not complete until the owner confirms the
    controller feels right, the quantised take is musical, and the variations
    keep working on a played line.**

---

## 13. Diagnostics

Use one prefix, `[LEAD-IN]`, and never log per-note during performance:

```text
[LEAD-IN] input=Launchkey 49 channel=0 match=contains quantize=1.00 count-in=1 bar bars=2
[LEAD-IN] notes sound as the nearest class of the current set (map=nearest)
[LEAD-IN] live on (sequencer paused at step 7)
[LEAD-IN] live off; phrase resumes at bar 5
[LEAD-IN] record requested (count-in 1 bar, 2 bars) at beat 8
[LEAD-IN] recording started at beat 12 (window 12..20)
[LEAD-IN] recorded 13 notes / 2 bars (grid 1/8, quant 100 %, 2 chord notes reduced)
[LEAD-IN] recording aborted (transport stop); take kept
[LEAD-IN] record needs the transport (press Play)
[LEAD-IN] count-in skipped (second Rec)
[LEAD-IN] melody saved "intro line" (13 notes, melodies/)
[LEAD-IN] melody loaded "intro line" (13 notes, grid 1/8, 2 bars)
[LEAD-IN] melody deleted "intro line"
[LEAD-IN] reverted to the recorded base (13 notes)
[LEAD-IN] cleared the melody; generated line restored
[LEAD-IN] disabled: no input port configured; available: ...
[LEAD-IN] disabled: no input matched "Foo"; available: ...
[LEAD-IN] disabled: 2 inputs matched "MIDI"; use a more specific name: ...
[LEAD-IN] input lost: ...; the rest of the rig continues
```

The existing `[LEAD]` lines stay; `describe_phrase()` gains `source=…` and the
melody name. No line per note.

---

## 14. Risks and decisions to document in the stage README

- **Double-trigger.** CoreMIDI lets several apps open one source. If Ableton
  listens to the controller, every note plays twice (snapped + raw). Document the
  Live preference setting in `START_HERE.md` troubleshooting.
- **Monophonic ownership.** One member channel. The input rig and the scheduler
  never run together: the `source` switch stops one before the other plays.
  Do not add a "mute" that leaves the sequencer running.
- **Bar-line resume.** Restarting the scheduler must not jump mid-bar; pass
  `time_of_beat(next_bar)` to `start(at_time=...)`.
- **Swing double-application.** Quantise against the swung grid, store the
  unswung step. If you store the swung position, playback applies swing twice.
- **Breath.** Playback-only; never in the recorded timing.
- **`ratio_hint` floats.** Use the set's own float; JSON round-trips `float`
  exactly. A rounded hint (e.g. 1.3333) would break `_match_ratio` and silently
  fall back to the degree.
- **Regeneration trap.** `_GENERATION_KEYS` regenerates; a played motif must go
  through the policy table or the take is destroyed by a Grid change.
- **`random` loses provenance.** Re-attach `source`/`base_notes` after
  `apply_variation` or `Revert` breaks.
- **State compatibility.** Keep `version` 1; optional keys only; the first 16
  `/ji/lead/state` values unchanged; `parse_lead_state` accepts 16 or 17.
- **mido import.** Lazy only. `tests/test_lead.py` and `test_startup.py` assert
  it.
- **Callback thread.** No blocking, no disk I/O, no OSC burst inside the mido
  callback; the record supervisor is a separate thread.
- **Stuck notes.** Every exit path sends note-off + CC 123; test with a fake
  output.
- **Window height / docs assertions.** `test_ui.py` bounds the height and
  `test_startup.py` checks every flag against `START_HERE.md`.
- **No hardware in the agent environment.** Do not claim MIDI/Ableton/audio
  acceptance.

---

## 15. Deliverables

```text
ji-live/
├── control/
│   ├── lead_input.py             NEW: mapper, key map, MidiInputPort,
│   │                                  LeadInputRig, LeadRecorder, quantiser
│   ├── melody.py                 NEW: Melody, MelodyLibrary, MelodyError
│   ├── lead.py                   MOD: LeadMotif source/base/history/melody,
│   │                                  load_motif/clear_melody/revert,
│   │                                  source-aware regeneration, gate_mode,
│   │                                  set_source, start(at_time), state_args 17
│   ├── state.py                  MOD: LeadPlaySection inline notes in `lead`
│   ├── ji_bridge.py              MOD: --lead-in construction, input handlers,
│   │                                  melody library, record state, source
│   │                                  switch, state recall order, status sends
│   ├── osc_server.py             MOD: /ji/lead/input/*, /ji/lead/melody/*
│   └── midi_rig.py               (unchanged expected: input is bridge-owned)
├── config/
│   └── lead_input_config.json    NEW
├── run/ji_station.py             MOD: --lead-in, --lead-in-port,
│                                       --lead-in-config, --melody-dir, --check
├── ui/
│   ├── osc_control.py            MOD: constants, builders, parsers, statuses
│   └── ji_ui.py                  MOD: Lead input group, Keys handler, Note len,
│                                       height
├── tests/
│   ├── test_lead_input.py        NEW: mapper, quantiser, recorder, library,
│   │                                  rig, state machine (pure)
│   ├── test_lead.py              MOD: gate_mode, load/revert, policy
│   ├── test_state.py             MOD: inline lead notes
│   ├── test_ui.py                MOD: pure + Qt
│   └── test_startup.py           MOD: new flags/docs
├── melodies/                     created on the first save (like states/)
└── docs/
    ├── README_stage9_1.md        NEW: design, data model, OSC, worked example,
    │                                  risks, human checklist
    ├── START_HERE.md             MOD: flags, Lead input controls, Ableton
    │                                  routing, troubleshooting rows
    ├── GUI_PARAMETERS.md         MOD: Lead input group + Note len
    ├── README_live_rig.md        MOD: input routing note (no new bus)
    ├── README_mpe.md             MOD: one line: input is Python-only
    ├── README_stage8_3d.md       MOD: link to 9.1
    ├── design_doc_10.28.10.50    MOD: §6.24 new, §7.1, §7.5, §8.9, §9, §13,
    │                                  §14
    └── PROPOSAL_stage9_1_lead_input.md   kept as the design record
README.md                         MOD: status, layout, tests, links, path notes
```

No changes to `synthdefs/`, `startup.scd`, `ui/control_surface.scd`,
`control/mutation.py`, `control/home.py`, `control/glide.py`,
`control/rhythm.py`, `control/breath.py` or `control/groove.py`. If you believe
one is unavoidable, stop and explain why in the stage README instead of adding
it quietly.

---

## 16. Self-review requirement (final response)

Provide:

- Files added/changed.
- For each requirement above, the code that satisfies it or a concrete concern.
- **The mapping maths**: restate the formula, the worked table, and the test that
  proves a class maps to itself with 0 cents and that `degree`/`octave` round-trip
  through `_project_locked`.
- **The quantiser**: restate the strength/swing rule and the test proving the
  stored step is unswung.
- **The variation integration**: show how a played motif survives `Mutate`,
  `Vary`, `random` and `Revert`, and how `set_variation_index` replays history.
- “If I had to bet on which line is most likely to fail on the owner's machine,
  which is it, and why? Give a one-line verification command.”
- Assumptions, max 5 bullets.
- Explicit statement that MIDI hardware, IAC, Ableton, audio and sclang were not
  tested by the agent; 9.1 awaits human acceptance.
- The exact one-sclang + one-Python start sequence, copied from
  `docs/START_HERE.md` after your updates.
