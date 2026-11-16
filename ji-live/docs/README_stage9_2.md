# Stage 9.2 — Lead override, audible count-in, and pitch-bend / mod-wheel expression

Status: **built 2026-11-14 — awaiting the owner's acceptance.** Stages 0–8.5 are
owner-accepted; 9.1 is owner-confirmed functional (2026-11-14) and is the
baseline this increment extends without regressing. 9.2 is a **lead/performance**
increment, not a granular/glitch FX one; it is listed as such in the design
doc's §8.9/§8.10 and §9 tables.

**2026-11-14 owner report (open):** "playback is working and the override is
effective but im not hearing the pitch bend at all from my midi controller". The
override/count-in/recorded-expression parts pass; **checklist item 5 (live bend)
does not**. The agent cannot reproduce or hear it (no hardware), so a diagnostic
build was added: one-shot `[LEAD-IN] wheel:` log lines (first event, first move
with no held note, first bend sent per gesture, first CC 1), a coalesced ~10 Hz
status refresh so the GUI `bend`/`mod` fields are live, and the read-only
`run/midi_probe.py` controller probe. Triage steps are in
`docs/START_HERE.md` troubleshooting.

**2026-11-15 follow-up (the leading explanation):** the owner reports the bend is
"very subtle and hard to hear". A full wheel sends **±341/8192**, which is
**±2 st at a 48 st receiver but only ±8.3 ¢ at a 2 st receiver** (§2.2). A subtle
bend therefore indicates a **receiver range of ~2 st**, which also compresses
every JI note-on bend by 24× (the lead plays ~12-TET). `--check` now prints
`lead_range`, the input wheel span and both full-wheel intervals, and the wheel
log states the configured range. **9.2 stays unaccepted until the owner
confirms the live bend is a whole tone (or sets the ranges to match).**

The owner's request (2026-11-14, verbatim):

> "in a beat count-in to recording i want to still be able to play even if it is
> recorded. also i want the following: i dont want to have to toggle between
> live and playing, if i hit a midi note, it should override whatever is playing
> in the lead (not permanently, but the playing loop should not be played and the
> midi in should be, and then immediately return to the loop after the note is
> no longer being played. if a note is being played in the loop, the release
> should trigger the attack of the note). also consider if the following is
> feasible: capturing moving pitch bend and mod wheel and outputting that (the
> nearest pitch should be given by the note from the input, and then pitch bend
> would be added to that). ideally this movement can also somehow be recorded in
> the loop and played back. if that seems realistic to implement then also i
> want this in the plan."

There is **no sclang change**: no `MIDIIn`, no `MIDIClient.init`, no new
SynthDef, no new audio bus and no new address to port 57120. `MPEOutput` already
exposed 14-bit pitch bend and `control_change` on the lead member channel, so
the whole stage is Python-only on the existing Bus 5.

| # | Owner decision (2026-11-14) | Implementation |
|---|---|---|
| 1 | No Live/playing toggle: input overrides the loop | `LeadScheduler.override()` / `release_override()`; no `Live` widget |
| 2 | Count-in is audible and still playable | the count-in note gate is removed; a held key is seeded into the take |
| 3 | Bend is added on top of the note chosen at key-down | class `N` fixed at note-on; `bend_for_frequency` is relative to `N` |
| 4 | The movement is recorded and played back | `LeadNote.bend_points` / `mod_points`, one bounded expression player |
| 5 | Recording stays a clean take | `record` still stops the sequencer; the input sounds and records |

---

## 1. Override model (C1)

`source` is now `generated | played | record`. `live` remains accepted as a
**deprecated alias** (old states, a stale GUI): `validate_source` still accepts
it, `LeadRig.set_source("live")` logs
`[LEAD] source=live is deprecated; input always overrides`, and maps it to
`played` when a take exists, else `generated`. It never gates the input.

| `source` | Sequencer | Input |
|---|---|---|
| `generated` | runs | **overrides** |
| `played` | runs the played motif | **overrides** |
| `record` | stopped (unchanged) | sounds and records |

`LeadInputRig` sounds a key when **the port is open, the input monitor flag is
on and the lead is enabled** — never on `source`. `live` in
`/ji/lead/input/state` now means "input monitor active" (`port_ok and flag`); it
is normally 1 and the GUI has no control for it.
`/ji/lead/input/set_enabled` is kept for a stale GUI: it toggles the monitor,
logs exactly one line and never starts/stops the sequencer.

### 1.1 The exact call order (key-down → resume)

All of this runs on the input reader thread; the scheduler lock is the
scheduler's `RLock`.

1. `LeadInputRig.note_on(midi, velocity)` validates and checks the gate
   (port/monitor/Lead-on). It maps the key to the nearest class and reads the
   current wheel state under the input lock.
2. If **no input note is currently sounding** (this is the first key of the
   gesture, including after pedal-up), it calls
   `lead.override_from_input()` → `LeadScheduler.override()`.
   `override()` takes the scheduler lock and: sets `_overridden = True`,
   cancels the pending gate timer, cancels any active expression player,
   captures `_held` and clears it, remembers the step index. It releases the
   lock and sends the loop note-off (`note_off`). It never stops the thread and
   never resets the step.
3. The rig releases its own previous input note (a retrigger only — the first
   key has none), sends `pitch_bend(base + wheel)`, then CC 1 (current mod),
   then `note_on`. Because `override()` completed the loop note first, the input
   bend can never interleave with a loop bend. A retrigger while already
   overridden does **not** call `override()` again.
4. On the **last** key-up with the sustain pedal up (or on pedal-up when the key
   is already up), the rig sends its input `note_off` and then calls
   `lead.release_input_override()` → `LeadScheduler.release_override()`.
5. `release_override()` takes the scheduler lock, clears `_overridden`, cancels
   any expression, and evaluates the retrigger eligibility:

   ```text
   running                         (the scheduler thread is alive, no stop)
   and _last_note is not None      (the last played step had a note)
   and _step_started_at is not None
   and now - _step_started_at < _step_gate_seconds      (nominal gate still open)
   and _resumed_for != _played_total                    (this step not resumed yet)
   ```

   * **Eligible** → `_play(last_note, …, resumed=True)` with the lock still
     held: a fresh bend, a CC 1 0 (clears the input's mod state), a note-on, a
     fresh gate from now and the expression curve restarted at offset 0. This
     is the owner's "the release should trigger the attack of the note".
     `_play` increments `_played_total`; `_resumed_for` records the token, so a
     duplicate release can never double-attack.
   * **Not eligible** (the gate elapsed or the last step was a rest) → stay
     silent until the next scheduled step; the thread advances normally.
6. `_run` keeps advancing `_step_index`/deadline while overridden but `_play`
   returns immediately under the lock, so there is no sequencer note-on, gate,
   bend, mod or expression message. One token (`_played_total`) and the single
   lock are what make "release → attack" happen exactly once.

`stop()` clears `_overridden`, `_step_started_at`, `_gate_seconds`, the last
note and the expression player, then sends note-off + CC 123. A source change,
Lead-off, bridge stop, port loss, melody load, state recall or `record` start
uses `clear_override()` (clears without resuming, because Bus 5 is changing
what it plays). A transport stop while overridden stops the scheduler (notes
off), clears the override; the input may keep sounding, and a later Play starts
the phrase at step 0 as before.

`LeadRig.playing` is unchanged: `port_ok and transport and enabled and source in
(generated, played) and scheduler.is_running`.

### 1.2 Decisions recorded here

* `_step_started_at`/`_step_gate_seconds` are **kept across `override()`** (the
  gate timer is cancelled but the timestamp is the eligibility test itself).
  They are cleared on `stop()`. The prompt's "cleared on stop/override" cannot
  coexist with its own eligibility rule; this is the reading that implements
  the owner's release attack.
* The scheduler is never stopped or reset by an override, so the loop stays in
  time and resumes on its own grid.

---

## 2. Bend and mod wheel (C3)

### 2.1 Input translation

`MidiInputPort` (still lazy — no `mido` at import time):

```text
pitchwheel                   -> ("bend", value, 0)      value -8192..8191
control_change 1 (mod)       -> ("mod",  value, 0)      value 0..127
control_change 64 (sustain)  -> ("sustain", 64, value)  unchanged
note_on / note_off           -> unchanged
```

The channel filter applies to the wheel and CC 1 exactly as it does to notes;
aftertouch, pressure, other CCs and clock are ignored. `LeadInputRig._on_message`
dispatches `bend(value)` / `mod(value)`.

### 2.2 The maths (the important part)

The class is chosen **once at note-on** and the wheel moves the pitch from
there. Let `range = settings.bend_range_semitones` (the **controller** range,
default 2), `N` the note number sent at note-on and `R = lead_config.
bend_range_semitones` (the **synth's** MPE range, 48):

```text
cents  = (pitchwheel / 8192) * range * 100                 # signed
f_bent = mapped.frequency * 2 ** (cents / 1200)

semis  = 12 * log2(f_bent / midi_note_frequency(N))
bend14 = round(semis / R * 8192)                            # centered, -8192..8191
```

`control/mpe_output.py::bend_for_frequency(N, target_hz, R)` is that formula and
is the only place a wheel value is computed. `frequency_to_pitch` is used once
at note-on to choose `N` and the base bend. **Never** call
`frequency_to_pitch(f_bent)` and send only its bend: a large wheel move could
cross a semitone and jump the receiver to the next note (wrong by an octave
after the receiver's own transpose).

**What actually leaves Bus 5** (input range 2 st, lead range 48 st, the shipped
config):

| Wheel | Input cents | 14-bit bend sent | Sounds like at 48 st | at 2 st |
|---|---|---|---|---|
| +2048 (¼) | +50.0 ¢ | +85 | +49.8 ¢ | +2.1 ¢ |
| +4096 (½) | +100.0 ¢ | +171 | +100.2 ¢ | +4.2 ¢ |
| +8191 (full) | +199.98 ¢ | +341 | **+2.00 st** (a whole tone) | **+8.3 ¢** |

The 14-bit value is a *fraction of the receiver's* range. The rig assumes the
Lead instrument's MPE pitch-bend range is `config/lead_config.json`'s
`bend_range_semitones` (48). If the instrument/MPE zone is at the MIDI default
of 2 st, every bend — the wheel *and* the JI note-on bends — is scaled by 2/48
(24× too small): the wheel is 8 ¢ and the lead is effectively 12-TET. That is
the difference between "a whole tone" and "barely audible". `--check` now prints
both ranges and the full-wheel interval at each, and the one-shot wheel log
states the configured range too. Ableton's own documentation makes the same
point: an MPE instrument's pitch-bend range must match the controller's
([Live tuning systems manual](https://www.ableton.com/en/live-manual/12/using-tuning-systems/),
[MPE FAQ](https://help.ableton.com/hc/es/articles/360019144999-Preguntas-frecuentes-sobre-MPE-en-Live)).

* A key exactly on a class with the wheel centred gives the 9.1 output
  bit-exactly (`bend_for_frequency(N, class_float, 48) == frequency_to_pitch(
  class_float, 48).bend`, tested).
* With no held note the wheel is stored in the rig and reported, nothing is
  sent; the next note starts bent.
* With a held note a wheel move sends **only** `pitch_bend` — no note-on, no gate
  change, no recorder note event.
* `range = 0` disables bend: the wheel is still reported in the status, but no
  bend is sent.
* `range` clamps to `0..48`; a bad value disables only the input
  (`LeadInputSettings` raises, the bridge logs and starts without the input).

### 2.3 Mod wheel

CC 1 is the literal mod wheel (there is **no `mod_cc` option**; if an instrument
wants MPE timbre CC 74, remap it in Ableton). It is forwarded on the lead member
channel because CC 1 is channel state: it shapes whatever is sounding, the loop
or the input. Every change is forwarded, coalesced and throttled to one message
per ~5 ms with a single trailing timer (the *latest* value always lands), and
duplicates are dropped. The current value is sent once before every input
note-on so the note starts with it. Nothing is logged per event.

---

## 3. Recorded expression (C4)

### 3.1 Data model

```python
@dataclass(frozen=True, slots=True)
class LeadNote:
    step: int
    length: int
    degree: int
    octave: int
    ratio_hint: float
    velocity: int
    bend_points: tuple[tuple[float, float], ...] = ()   # (offset_beats, cents)
    mod_points: tuple[tuple[float, int], ...] = ()      # (offset_beats, 0..127)
```

`offset_beats` is measured from the **raw note-on** in quarter-note beats — not
quantised and not swung. Playback adds the offset to the note's quantised onset,
so the curve follows the note's actual (swung/breath-adjusted) start. Onsets
themselves stay quantised (sub-step *onsets* are out of scope).

Validation lives in the shared note validator (`LeadEngine._validate_notes`, used
by the engine, melody files and states): offsets are finite, `>= 0` and sorted
ascending; duplicate offsets are last-wins; identical consecutive values are
coalesced; cents are finite and **clamped to ±4800**; mod is `0..127` (a value
outside that range is rejected); at most **256 points per curve** (later points
are dropped). Existing six-argument `LeadNote(...)` construction is unchanged,
and empty tuples are the default.

JSON note mappings always write both keys (empty lists allowed) and default to
`()` when absent, so old files and old states load unchanged; both format
versions stay **1**:

```json
{"step": 0, "length": 2, "degree": 2, "octave": 0,
 "ratio_hint": 1.25, "velocity": 104,
 "bend_points": [[0.0, -12.0], [0.3125, 0.0]],
 "mod_points": [[0.0, 64]]}
```

### 3.2 Capture and playback

`LeadRecorder`:

```python
def note_on(self, midi, velocity, beat, *, bend_cents=0.0, mod=0) -> None
def expression(self, beat, *, bend_cents=None, mod=None) -> None
```

`note_on` seeds an offset-0 point when the wheel is off centre / mod is up.
`expression` attaches a point to the currently captured note (`offset = beat -
record["on"]`, clamped to `>= 0`, coalesced, capped); it is ignored when no note
is open or the note has already ended. Points after the note's off are dropped;
a note held past the window ends at the window end. A retrigger starts a fresh
curve; a chord reduction keeps only the last note *and its points*. Count-in
notes are not captured, but a key still held at `_begin_recording` is seeded at
`start_beat` with its played velocity and current bend/mod as offset 0.

`LeadScheduler._play` sends the base bend before the note-on as before. CC 1 is
sent before the note-on **only when it is needed**: the note has `mod_points`
(first value at offset 0, else 0), or this is an override-resume attack (0, to
clear the input's mod state). An ordinary generated note with no points sends no
CC 1, so the generated MIDI stream is unchanged (the 9.1 acceptance rule). After
the note-on a single `_ExpressionPlayer` daemon thread applies the merged curve
(bend before mod on a tie), each point expressed relative to `N` with
`bend_for_frequency`, skipping values equal to the last sent one. Real time =
`offset_beats * beat_seconds`; the curve is relative, so breath and swing carry
it along. It is cancelled by the gate note-off, the next `_play`, `override()`,
`release_override()` (before the resume attack), `stop()`, a source change and a
Lead-off; a fresh attack restarts it at offset 0. It never runs while the
scheduler is stopped/recording/overridden, never blocks the scheduler thread or
the MIDI reader thread, and there is at most one player.

### 3.3 Worked note (LeadNote → melody JSON → state JSON → playback)

Take one note: step 0, 2 steps at the 1/8 grid, degree 2 (5/4 on F = 220), one
bend move from −12 ¢ to 0 ¢ over 0.3125 beats, the mod wheel at 64 from the
start. At 120 BPM a beat is 0.5 s and one 1/8 step is 0.25 s.

1. **`LeadNote`** — `LeadNote(step=0, length=2, degree=2, octave=0,
   ratio_hint=1.25, velocity=104, bend_points=((0.0, -12.0), (0.3125, 0.0)),
   mod_points=((0.0, 64),))`.
2. **Melody `melodies/<name>.json`** — the same note under both `notes` and
   `base_notes`, with `"format": "ji-lead-melody", "version": 1`:
   `"bend_points": [[0.0, -12.0], [0.3125, 0.0]], "mod_points": [[0.0, 64]]`.
   `Melody.from_mapping` validates through `_validate_notes`, so load →
   `load_motif` → playback carries the curves.
3. **Performance state `states/<name>.json`** — the note is inline in the
   `lead` section (`notes` and `base_notes`), same two keys, `version: 1`; recall
   and `Revert` restore the take *and* its curves (`base_notes` is the Revert
   target).
4. **Playback** — the projected note carries the curves; `_play` sends the base
   bend (the class 5/4 float, bit-exact), CC 1 64 (first mod point), the
   note-on. At +0 beats the player computes `bend_for_frequency(N, f*2^(-12/1200),
   48)` and sends it (skipped if it equals the base bend, which it does not);
   at 0.3125 beats × 0.5 s = 156 ms it sends the class bend again (0 ¢ ⇒ the
   same 14-bit value as the base bend) and then the gate note-off at the note's
   own length. The loop's next note re-sends its own base bend and no CC 1
   (unless it has points), so the 9.1 generated stream is untouched.

### 3.4 Variations and provenance

`Vary`/`Vary every` still transform the take. Policy (documented, tested by the
existing variation suite):

* **transpose, invert, retrograde, thin** keep each surviving/moved note's own
  curves (the note keeps its identity; `replace` preserves the defaulted
  fields);
* **ornament** inserts a fresh passing note with empty curves;
* **rhythm** redraws the placement and generates fresh notes with empty curves;
* **random** returns a freshly generated motif with no curves.

`Revert` restores `base_notes`, which carry the recorded curves.

---

### 2.4 Receiver-range mismatch (the "subtle bend" report)

A full wheel sweep must sound like a **whole tone**. If it sounds like a few
cents, the Lead instrument's MPE pitch-bend range is ~2 st, not 48; the same
24× shrink then applies to the JI note-on bends. Fix **one** of:

* set the Lead instrument / MPE zone pitch-bend range to **48 st** (preferred:
  keeps the full 14-bit resolution), or
* set `bend_range_semitones` in `config/lead_config.json` to the instrument's
  actual range (e.g. `2`) and restart — the wheel then reaches the full 2 st
  and the JI lead is correct again.

The input wheel's span (how far the wheel moves musically) is the separate
`bend_range_semitones` in `config/lead_input_config.json` (default 2, 0–48); a
matching receiver turns it into exactly that many semitones.

## 4. The audible count-in (C2)

* The 9.1 `count_in` silence guard in `LeadInputRig.note_on` is gone: keys sound
  during the count-in exactly as during recording.
* The **take still starts on its bar line**. The recorder captures only in the
  `recording` state, so notes that begin *and end* during the count-in are not
  stored. A phrase that begins during the count-in survives if the key is still
  held at the bar line: `_begin_recording` seeds the recorder with the held key
  at `start_beat` (velocity kept, current bend/mod as offset 0), so it is stored
  from step 0.
* The 9.1 `_release_held()` at record start is removed: `LeadRig.
  set_source("record")` stops the sequencer by itself, so no input note-off is
  needed, and the held note must survive into the take. It is released only by
  the normal exit paths (Stop, lead off, source change on a state/melody
  recall, port loss, bridge stop).
* A transport stop during the count-in still cancels it silently (9.1 rule); a
  transport stop while recording keeps the partial take.
* **Decision:** the count-in is a monitoring pre-roll, not a pickup bar.
  Storing count-in-only notes would need negative steps or an extra bar; that is
  a separate decision if the owner ever wants a pickup take.
* The count-in remains silent on Bus 5's *sequencer* because `record` stops it;
  the audible sound is the input.

---

## 5. OSC, config, GUI

`/ji/lead/input/state` is now **16** values: the first 14 are the frozen 9.1
order (`live` = monitor active), 15 `last_bend_cents` (float, signed; 0 =
centre) and 16 `last_mod` (int 0..127). `parse_lead_input_state` accepts **14 or
16**; a 14-value payload gives 0/0. No other address changed; `set_enabled` is
the only compatibility note (monitor only). `revert`/`clear`/melody `load` stay
beat-queued; record/stop/refresh/note/expression events stay immediate.

`config/lead_input_config.json` gains `"bend_range_semitones": 2.0` (valid
`0.0..48.0`, 0 disables bend; a bad value disables only the input). `--check`
prints it: `… bars=2 bend=2 st`. No new flags.

GUI: the `Live` checkbox and its handler are removed. `Keys`, `Port`,
`Refresh`, `Rec`, `Stop`, `Revert`, `Clear`, `Quant`, `Count-in`, `Bars`, the
melody row and `Note len` remain; the window stays `1280x1258` (the Qt height
bound at 1290 is unchanged). `Keys` is still GUI-local; the input always
monitors. The status line is
`Input: Launchkey 49 - idle - 13 notes - bend +12 ¢ - mod 64` (bend/mod omitted
when both are zero) and `Melody: intro line (13 notes) - played`. No new
tab/dialog/window; QWERTY still does not fire while a text box/combo/spin has
focus.

---

## 6. Diagnostics

One prefix, `[LEAD-IN]`; never per event:

```text
[LEAD-IN] input=Launchkey 49 channel=0 match=contains quantize=1.00 count-in=1 bar bars=2
[LEAD-IN] notes sound as the nearest class of the current set (map=nearest)
[LEAD-IN] expression bend_range=2.00 st mod=CC1
[LEAD-IN] wheel: pitch bend -2048 -> -50.0 ¢ (range 2.00 st)      once, first wheel event
[LEAD-IN] wheel: no note held; the value is stored for the next note-on
[LEAD-IN] wheel: sent pitch bend -82 while note 64 sounds (-50.0 ¢)  once per gesture
[LEAD-IN] wheel: CC 1 (mod) 64 received                          once
[LEAD-IN] override on at step 7
[LEAD-IN] override off; loop note attacked at step 7
[LEAD-IN] override off; next loop note at step 8
[LEAD-IN] override cleared (scheduler stopped)
[LEAD-IN] override cleared
[LEAD-IN] input monitor off
[LEAD-IN] recording started at beat 12 (window 12..20)
[LEAD-IN] recorded 13 notes / 2 bars (grid 1/8, quant 100 %, 3 bend curves, 2 mod curves)
```

The `[LEAD] source=live is deprecated; input always overrides` line is one per
alias use. The existing `[LEAD]` lines stay. No line per bend/CC event: the four
`wheel:` lines above are one-shot (the "sent" one resets per key-down), which is
what makes "the wheel does nothing" triageable from the log. While a wheel is
moving the GUI receives a coalesced `input/state` at ~10 Hz (100 ms), so the
`bend`/`mod` fields on the status line are live; no other per-event UI traffic.

`run/midi_probe.py` is the read-only ground truth: it lists the inputs and
prints every `pitchwheel`/CC/note the controller actually sends
(`uv run --with mido --with python-rtmidi python run/midi_probe.py`), with no
ports opened by the bridge and nothing sent. Use it to separate "the controller
does not send bend" from "the rig/instrument does not apply it".

---

## 7. Tests

`tests/test_lead_expression.py` is the new pure harness (no mido, Qt, sockets):

* **bend maths** — 0, ±half, ±full, ranges 2/48, clamping, 9.1 bit-exactness, and
  a bend crossing a semitone that keeps `N` and sounds the target frequency;
* **port** — pitchwheel/CC 1 translation, sustain and velocity-0 note-off
  unchanged, the channel filter on both;
* **input rig** — bend, mod then note-on; bend-only while held; no note → state
  only; the wheel survives a release; the same values fed through the fake
  MIDI port reach the rig; throttle/coalescing;
* **recorder** — seeding, coalescing, the 256 cap, clamp to the note end,
  retrigger, chord reduction, empty take;
* **validation/serialisation** — `LeadNote`, melody JSON, state inline JSON,
  old files, bad offsets/cents/mod;
* **scheduler expression** — order, gate/next/stop cancellation, no points = no
  extra CC/bend;
* **override** — note-off, suppression, resume attack, no double attack, cleared
  on stop, an elapsed gate enters on the next step;
* **count-in** — audible, unstored when released early, a held key seeded at
  step 0 with its velocity, silent cancel.

Additive checks are in `tests/test_lead_input.py` (count-in, override call
order, 16 values, bend config), `tests/test_lead.py` (override API, expression
cleanup, generated stream unchanged), `tests/test_state.py` (inline curve
round-trip, old state, bad point aborts), `tests/test_ui.py` (14/16 parser, no
`Live` widget, bend/mod status, Qt group) and `tests/test_startup.py`
(`--check` bend range, docs).

Full regression: run every harness listed in the stage prompt §9.3 from
`ji-live/`. `test_ui.py --qt` needs Qt system libraries; it is written and
reviewed but could not run in this environment.

---

## 8. Risks and decisions

* **Race / double attack.** Override and resume run on different threads. One
  scheduler lock plus the `_played_total`/`_resumed_for` token make
  "release → attack" exactly once; `_play` refuses while overridden and the
  lock is held across the resume attack, so the input cannot slip a bend
  between the loop's note-off and its note-on. Tested with the real scheduler
  and a fake output.
* **Bend is relative to the note number.** The class is never re-chosen while a
  note sounds; `bend_for_frequency(N, …)` is the only formula. A fresh
  `frequency_to_pitch` on the bent frequency would jump an octave on a large
  bend.
* **Wheel range mismatch.** The controller's actual bend range must match
  `config/lead_input_config.json` (`bend_range_semitones`, default 2). Check it
  in the controller's own settings (Launchkey: Components/Setup → pitch-bend
  range); a mismatch scales every wheel move (2 vs 12 st = 6× too far).
* **MIDI event rate.** A wheel can arrive at ~100/s. CC 1 is coalesced to one
  message per ~5 ms and duplicates are dropped; the callback does no disk I/O,
  no logging and no blocking; bend sends are a single `pitch_bend`.
* **Timer load.** One expression player per sounding note (a daemon thread with
  an event), cancelled on every exit path; 256 points per curve; no per-point
  thread or timer explosion. The CC 1 throttle uses one trailing timer.
* **JSON size / version.** Optional keys, empty lists written, `version` stays
  1; a state/melody with curves also loads on the previous build (unknown keys
  are ignored there by design).
* **Count-in decision.** Monitoring only; held notes cross the bar line. A
  pickup bar is a separate change.
* **Record still stops the sequencer.** "No Live toggle" is about normal
  playing; recording remains a clean take.
* **Variation provenance.** Moved/repeated notes keep their curves; freshly
  generated notes start empty (§3.4).
* **Qt.** PyQt6 imports here but the Qt platform library (`libglib-2.0.so.0`) is
  missing, so `test_ui.py --qt` cannot run; the Qt test is written and reviewed.
* **Wheel triage (2026-11-14 follow-up).** The owner reported the override and
  playback working but no audible pitch bend. The agent cannot reproduce or hear
  this (no hardware), so the rig now logs the first wheel event, the first
  move with no note held, the first bend actually sent per gesture and the first
  CC 1, and `run/midi_probe.py` prints the controller's raw messages. The three
  failure domains are: the controller does not send pitch bend, the configured
  `channel` filters it, or the Lead track/instrument does not apply bend (MPE
  enabled, 48 st range, instrument bend range). See `docs/START_HERE.md`
  troubleshooting.
* **No hardware.** MIDI hardware, IAC, Ableton, audio and sclang are not
  available in this environment; no listening pass is claimed.

---

## 9. Human acceptance checklist (the owner is the authority)

> **2026-11-14:** items 1–4, 7–10 pass (owner: "playback is working and the
> override is effective"); **item 5 (live bend) is the open failure**. Use the
> wheel triage in `docs/START_HERE.md` before changing any code: the log
> distinguishes controller → port → rig → synth.

1. **Count-in**: arm `Rec` with a one-bar count-in; play freely during it and
   hear every note. The take still starts on the bar line; a note held across
   the line is in the take from step 0.
2. **No Live toggle**: the GUI has no `Live`; a key at any time takes the lead.
   The sequencer is not stopped.
3. **Override**: hold a key over a running generated/played line. The loop's
   sounding note stops immediately and only the input sounds. Release: the
   loop's note attacks immediately if it was still sounding; otherwise the next
   step enters normally. No double attack, no stuck note.
4. **Sustain**: the pedal keeps the override until pedal-up; a new key
   retriggers.
5. **Bend**: move the wheel while a key is held; the note glides from the
   nearest class, no re-attack, no class snap; centred returns to the class.
6. **Mod**: move the mod wheel; the lead responds as its CC 1 mapping dictates
   (map it in Ableton if needed).
7. **Recorded movement**: record a phrase with a bend and a mod move; the take
   plays back the movement on the quantised grid. Save the melody, restart the
   bridge, load it: the movement is there. Save/recall a state and `Revert`: the
   movement is restored.
8. **Variations**: `Vary`/`Vary every` work on a take with expression; `Revert`
   restores the take and its curves.
9. **No regressions**: generated lead with a silent input is unchanged; `Vary`,
   groove link, breath, states, melodies, QWERTY, drone/bass/arp/drums, loops
   and the window behave as before.
10. **Window**: the Lead input group fits, Status is reachable, the scroll area
    handles a small screen.
11. Record the result. **9.2 is not complete until the owner confirms the
    override feels seamless, the bend/mod is musical, and the recorded movement
    plays back.**

---

## 10. What was not verified here

MIDI hardware, the Launchkey, IAC, Ableton, audio and sclang were **not**
available to the agent. `tests/test_ui.py --qt` could not run (no Qt platform
libraries). Everything above is pure-Python evidence plus honest reporting; 9.2
awaits the owner's listening/playing acceptance.

## 11. Files

```text
control/lead.py              LeadNote points, shared validation, override,
                             expression playback, stop/exit cleanup
control/lead_input.py        audible count-in, bend/mod translation, override
                             calls, expression capture, bend config, 16 values
control/mpe_output.py         bend_for_frequency
control/state.py             inline note curves
control/melody.py            melody note curves
control/ji_bridge.py         monitor-only set_enabled, 16-value status
control/osc_server.py        set_enabled compatibility comment (no new address)
config/lead_input_config.json  bend_range_semitones
run/ji_station.py            --check prints the bend range
ui/osc_control.py            14/16 parser, bend/mod status, no Live
ui/ji_ui.py                  Live removed, bend/mod status
tests/test_lead_expression.py NEW
tests/test_lead_input.py     MOD (count-in, override order, 16 values)
tests/test_lead.py           MOD (override/expression invariants)
tests/test_state.py          MOD (inline curves)
tests/test_ui.py             MOD (pure + Qt)
tests/test_startup.py        MOD (--check bend range)
docs/README_stage9_2.md      this file
docs/START_HERE.md, docs/GUI_PARAMETERS.md, docs/README_stage9_1.md,
docs/README_mpe.md, docs/README_live_rig.md, docs/design_doc_10.28.10.50,
README.md                    MOD
```
