# Stage 8.5 — Performance state save / recall

**Status: built, agent-tested, and owner-approved (2026-11-02).** §10 stays as the regression checklist.
No sclang, SynthDef, audio-DSP or new MIDI voice is involved. Everything is a
Python/bridge change plus one compact GUI row.

The owner asked for:

> "i want various states to be saved and recalled (no need for loop recall)."
>
> — owner, 2026-11-01

and, again, "no need to worry about the loops". So a **state is a colour**, not a
session dump: it records everything that makes the rig sound the way it does,
and deliberately leaves loops, autotune and the transport alone.

---

## 1. What a state is

### 1.1 In scope (everything the bridge owns)

| Section | Contents |
|---|---|
| `freqset` | ratios (octave-reduced, sorted, unique), anchor, analysis octaves, fundamental |
| `mutation` | randomness, dissonance_bias, stepwise_bias, temperature, anchor_mobility, change_count, glide_time, max_changes |
| `home` | four slots (snapshot or `null`), active_slot, auto, every, seed, max_drift, return_glide, journey_seed, journey_mutations |
| `rhythm` | bpm, meter_num, meter_den — **not** `running` |
| `drums` | enabled, rate, lane_mask (the same 16-bit mask as `/ji/drums/state`) |
| `groove` | preset, density, syncopation, swing, probability, seed, and the 32-int grid payload (the `/ji/drums/grid` layout: per lane 0..15 `[mask_lo, mask_hi]`) |
| `bass` | every `BassSettings` field (including `preset: "custom"`) |
| `arp` | every `ArpSettings` field |
| `lead` | every `LeadSettings` field + `variation_index`; **Stage 9.1 optionally adds** inline `notes`/`base_notes`/`history`/`melody`/`source` for a played line |
| `drone` | every `DroneSettings` field |

The settings sections are validated by the existing `BassSettings.create`,
`ArpSettings.create`, `LeadSettings.create`, `DroneSettings.create`,
`HomeConfig.from_mapping`, `SetSnapshot.create`, `GrooveSpec` and `GrooveGrid`
models. There is no second validation layer.

### 1.2 Out of scope

* **Transport** (`running`) — loading a state never starts or stops the
  transport. A state is a colour, not a play command.
* **Loops are out of scope** — no loop mode, length, buffer, transport or
  default length. The owner excluded loop recall, and the file has no `loop`
  key at all.
* **Autotune** — sclang owns those parameters; a recall never sends
  `/ji/autotune/set`.
* **Pending quantizer actions / the Back history** — the 64-deep Back trace is
  session state, not a colour. A load **does** cancel a pending return and an
  active class glide (§6).
* **`max_changes`** is saved for information and clamping only: the engine's cap
  comes from `--max-changes`, and a recalled `change_count` above it is clamped
  with one line.

---

## 2. File format (version 1)

```json
{
  "format": "ji-performance-state",
  "version": 1,
  "saved_at": "2026-11-02T02:00:00-05:00",
  "name": "A-drone",
  "freqset": {
    "ratios": [1.0, 1.125, 1.25],
    "anchor": 1.0,
    "octaves": [[1.0, 0], [1.125, 0], [1.25, 0]],
    "fundamental": 220.0
  },
  "mutation": {
    "randomness": 0.55, "dissonance_bias": 0.0, "stepwise_bias": 0.25,
    "temperature": 0.0, "anchor_mobility": 0.25, "change_count": 1,
    "glide_time": 0.15, "max_changes": 3
  },
  "home": {
    "slots": [null, null, null, null],
    "active_slot": -1, "auto": false, "every": 8, "seed": 1,
    "max_drift": 0, "return_glide": 0.06,
    "journey_seed": 1, "journey_mutations": 0
  },
  "rhythm": {"bpm": 120.0, "meter_num": 4, "meter_den": 4},
  "drums": {"enabled": false, "rate": 1.0, "lane_mask": 69},
  "groove": {
    "preset": "four", "density": 0.0, "syncopation": 0.0,
    "swing": 0.0, "probability": 0.0, "seed": 1,
    "grid": [0, 0, 0, 0, "... 32 ints total ..."]
  },
  "bass": {"enabled": false, "preset": "pulse", "grid": "1/4", "...": "..."},
  "arp": {"step_seconds": 0.25, "gate_seconds": 0.2, "velocity": 90, "...": "..."},
  "lead": {"enabled": false, "grid": "1/8", "...": "...", "variation_index": 0,
           "notes": [], "base_notes": [], "history": [], "melody": "",
           "source": "generated"},
  "drone": {"mode": "hold", "velocity": 90, "...": "..."}
}
```

Rules:

* `format` and `version` are **required** and checked. A newer/unknown version
  is refused with one clear line. Version 1 is the only version today; the
  loader is structured so a future version can migrate rather than break.
* `freqset` is required (a state without a set is meaningless). Every other
  section is optional: missing sections/keys fall back to the shipped values, so
  a hand-edited or older file still loads.
* Unknown keys (including `loop`/`autotune`, should an old hand-edited file have
  them) are **ignored**, never restored.
* The whole document is parsed and validated **before** anything is applied. One
  invalid value aborts the load with one `[STATE]` line and changes nothing.
* `saved_at` is informational only; it is never used for ordering. The list is
  sorted by name, case-insensitively and then case-sensitively.

A file whose `groove.grid` is missing gets the preset grid for the state's
meter, so it sounds coherent instead of empty.

---

## 3. Storage and names

```text
ji-live/states/<name>.json        default directory, created on demand
```

* `--state-dir <path>` on the launcher and the bridge overrides it. A relative
  path resolves from the project root, exactly like the other config paths.
* Name rules, after trimming: 1..40 characters, the first character
  alphanumeric, then `A-Z a-z 0-9 space . _ -`; no `/`, `\`, `..`, control
  characters or a leading dot.
* Two names that differ only in case are refused (`[STATE] save failed: a state
  named "A" already exists (names differ only in case)`), because the owner's
  macOS volume is case-insensitive and two files would silently merge.
* Saves are **atomic**: the text goes to `<name>.json.tmp` in the same
  directory, is flushed and fsync'd, then `os.replace`d onto the final name. A
  failed save leaves the previous file intact and logs one line. A read-only or
  uncreatable directory is reported and never fatal to the rig.
* State files are user data, never shipped config: nothing is written into
  `config/`.

---

## 4. Controls

### 4.1 GUI — the State row

One compact row under **Home** (`ji-live/ui/ji_ui.py`):

| Control | What it does |
|---|---|
| `Name` | editable combo listing the saved states (sorted) |
| `Save` | save the whole configuration under the typed name (immediate) |
| `Load` | recall the selected state (immediate when stopped, next beat while running) |
| `Delete` | delete the selected state file (immediate) |
| `Refresh` | ask the bridge for a fresh list + status |
| status line | `State: 3 saved \| saved A-drone \| loaded A-drone` (or `State: none saved`, or `State: error ...`) |

`Save` is disabled while the name is empty; `Load`/`Delete` are disabled unless
the name is in the list (case-insensitively). Incoming replies sync the combo and
the status line with Qt signals blocked, so a sync never sends OSC. The window
is 1280×800 (one row taller than 8.4); the scroll area remains the small-screen
safety net. A tooltip says that loops and autotune are not part of a state.

### 4.2 OSC

UI → bridge:

| Address | Argument | Meaning |
|---|---|---|
| `/ji/state/save` | string name | save the live configuration (immediate file op) |
| `/ji/state/load` | string name | recall; beat-quantized on `PHASE_STATE` |
| `/ji/state/delete` | string name | delete the file (immediate) |
| `/ji/state/request` | none | fresh list + status |

Bridge → UI:

| Address | Payload |
|---|---|
| `/ji/state/list` | `[int count, name_1 … name_N]` (sorted) |
| `/ji/state/state` | `[int count, last_saved, last_loaded, message]` |

Both replies are sent at bridge start, on `/ji/ui/hello`, and after every
save/load/delete/request. `message` is a short human line (`saved`, `loaded`,
`deleted`, `refreshed`, `no such state`, `invalid state: …`), never a traceback.
`last_saved` / `last_loaded` are **session-local** (they are not stored in the
file; `saved_at` is), so a restarted bridge shows empty values until the next
save/load.

Handlers validate arity/type only; the bridge owns the name rules, so the log
and the GUI reply always agree. A rejected message is logged and never raises
out of the OSC thread.

### 4.3 Quantization

`PHASE_STATE = 10` is appended **last**
(`home, mutation, octave, groove, drums, bass, arp, lead, drone, midi, state`),
so a recall at a beat wins over everything requested for that same beat. While
the transport is stopped it applies immediately. Save/delete/list are file
operations that change no sound, so they are **immediate even while running**;
only a load is beat-quantized.

---

## 5. Save / recall a state

1. Make the rig sound the way you want.
2. Type a name in the **State** row (`A-drone`, `club set 2`, …) and press
   **Save**. The log shows one
   `[STATE] saved A-drone (147 fields, states/)` line; the list/status refresh.
3. Change the set, controls, tempo/meter, groove, home slots and the
   bass/arp/lead/drone settings — the state is unaffected.
4. Select `A-drone` in the combo and press **Load**.
   * Transport **stopped**: everything returns at once — one set change, one
     `/ji/freqset/update`, one `/ji/home/state`, one `/ji/state/state`.
   * Transport **running**: the same load lands on the next quarter beat, with
     one update and no burst.
5. Quit the bridge, restart it (same `--state-dir`), press **Refresh**: the
   state is still in the list and loads identically.
6. **Delete** removes the file; loading it afterwards logs exactly one
   `[STATE] gap: no state named "…"` line and changes nothing.

Re-saving the same name replaces the file. Two names that differ only in case
are refused.

---

## 6. What a load does, exactly

1. Parse and validate the **whole** file (and re-check the class count/octaves
   against this bridge's engine). Anything wrong → one `[STATE]` line, no
   change, no update.
2. Cancel a pending return (one `[STATE] return cancelled by state load` line)
   and an active 8.4.1 class glide (`[GLIDE] cancelled by state load`).
3. Apply the frequency set (`apply_snapshot`), force the saved anchor and
   fundamental.
4. Apply the mutation controls (public `set_randomness` /
   `set_dissonance_bias` / `set_stepwise_bias` added in 8.5; the existing OSC
   handlers use the same code path).
5. Restore the four home slots, the active home and the home config, then the
   journey seed/counter and `engine.reseed(journey_seed)`.
6. Restore tempo/meter and broadcast `/ji/rhythm/tempo`.
7. Restore the groove (preset, modifiers, seed, then the exact 32-int grid) and
   the drum enable/rate/lane mask.
8. Restore bass/arp/lead/drone settings, the lead variation index, and (9.1) an inline played line through
   the rigs' settings paths; a missing bus skips only that voice, with one line.
9. Send **exactly one** `send_set(glide_time)` using the saved
   `mutation.glide_time` (a jump, not a smooth return), then one
   `/ji/home/state`, one `/ji/state/state` and the usual voice status messages.
10. Record the recalled snapshot in the Back history (a load is an applied set
    change; a save is not).

A load is **idempotent**: loading the same file twice leaves the live state
identical the second time (the history de-dupes it) and still logs the same
lines.

### 6.1 Caveats you should know

* **The transport is not recalled.** If it was running before the load it keeps
  running; if it was stopped it stays stopped.
* **The Back history is not saved.** The slots and the active home are; the
  64-deep Back trace is a session trace.
* **The journey RNG is reseeded, not resumed.** After a recall the next mutation
  stream is deterministic from the saved seed, but the exact continuation of a
  mid-journey random stream is not reproduced.
* **A recalled voice whose bus is missing** (e.g. `--bass` was dropped) logs one
  line and skips only that voice; everything else is recalled.
* Loops and autotune are untouched in both directions.

---

## 7. Diagnostics

One line per event, never per note:

```text
[STATE] dir=states/ states=3
[STATE] saved A-drone (147 fields, states/)
[STATE] loaded A-drone
[STATE] load A-drone: distance 4, 1 set update
[STATE] deleted A-drone
[STATE] gap: no state named "nope" (0 saved)
[STATE] invalid state "x": freqset: snapshot anchor is not in the class set
[STATE] save failed: state directory states/ is not writable
[STATE] return cancelled by state load
[STATE] drums skipped: Bus 3 not available; the rest of the state is applied
```

The existing `[QUANT]`, `[BRIDGE]`, `[MUTATION]`, `[HOME]` and `[GLIDE]` lines are
unchanged.

---

## 8. Code map

| File | Change |
|---|---|
| `control/state.py` | **new**: `StateError`, `StateName`, `PerformanceState`, `StateLibrary`, format constants |
| `control/quantize.py` | `PHASE_STATE = 10` (last) |
| `control/osc_server.py` | `/ji/state/*` handlers; public `set_randomness`/`set_dissonance_bias`/`set_stepwise_bias` (and quiet `log=` on the other setters) |
| `control/ji_bridge.py` | `StateLibrary`, `--state-dir`, capture, atomic load, replies |
| `control/home.py` | additive `clear_active()`, `restore_journey()` |
| `control/bass.py` / `lead.py` / `drone.py` / `midi_rig.py` | additive bulk `set_settings` restore paths and `set_variation_index` |
| `ui/osc_control.py` | addresses, builders, `StateStatus`, parsers, store, controller |
| `ui/ji_ui.py` | compact State group; window 1280×800 |
| `run/ji_station.py` | `--state-dir`, `--check` state-dir line |
| `tests/test_state.py` | **new** pure harness |
| `tests/test_quantize.py`, `test_ui.py`, `test_startup.py`, `test_home.py` | extended expectations |

---

## 9. Decisions and risks

* **Partial recall.** The whole file is validated first; a bad field never
  leaves half the rig changed.
* **Format drift.** `format`/`version` exist from day one; a future stage adds
  fields without breaking old files. Semantics are never silently reordered.
* **Loops and autotune are out**, by the owner. They are absent from the file,
  the GUI tooltip says so, and a load never sends `/ji/autotune/set`.
* **Case-insensitive filesystems.** Case-only duplicate names are refused rather
  than silently merged.
* **Missing buses.** A recalled voice whose bus is missing degrades alone.
* **File safety.** Names are path-safe, writes are atomic, and a failed save
  leaves the old file intact.
* **GUI density.** One more row; Status remains visible; 1280×800 default.
* **`mido` stays lazy.** Constructing a bridge or a state never imports `mido`.

---

## 10. Human acceptance checklist

1. Save a state `A` with a distinctive set, tempo, groove and voice settings.
2. Change the set, controls, tempo/meter, groove, and bass/arp/lead/drone
   settings (and capture a different home).
3. Load `A` while the transport is **stopped**: everything returns at once, the
   Status set matches, and the rig sounds like `A` again.
4. Load `A` while the transport **runs**: it lands on the next beat with one set
   update, no burst, no stuck note (drone retunes, lead re-projects, arp/bass
   follow).
5. Save `B` with a different colour; switch `A`/`B` repeatedly.
6. Quit the bridge, restart it, load `A` from the GUI: the state is still there.
7. Delete a state; the list shows it gone; loading it logs one clear error.
8. An invalid/hand-corrupted file is refused with one line and changes nothing.
9. Loop audio, loop settings and autotune parameters are **not** touched by a
   save or a load.
10. Drone/arp/bass/lead/drums, groove, groove-quantize, home sets/return and the
    accepted 8.3d/8.4 controls do not regress; the window stays compact.
11. Record the result. **8.5 is not complete until the human confirms
    save/recall across a restart with no audio/MIDI regressions.**

The agent did not test audio, MIDI, Ableton, IAC, sclang timing or the live GUI.

---

## 11. Tests

```bash
cd ji-live
uv run --with python-osc python tests/test_state.py
```

Covers names, a full round-trip of every in-scope field, defaults/unknown keys,
wrong format/version, bad-value aborts that leave the bridge untouched, the
library (atomic save, overwrite, sorted list, delete no-op, corrupt/read-only
files), a stopped load (one update, one home state, one state reply), a running
load (exactly one beat, one update), return cancellation, every restored value,
idempotency, a restart with the same state dir, missing-bus degradation, the
absence of loop/autotune keys, no `mido` import, and the `/ji/state/*` handlers
plus the two reply shapes.

The rest of the suite (`tests/test_ui.py --pure`, `tests/test_quantize.py`,
`tests/test_startup.py`, `tests/test_home.py`, …) must also pass; see
`README.md`.
