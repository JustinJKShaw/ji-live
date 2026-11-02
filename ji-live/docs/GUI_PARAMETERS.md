# JI Station — GUI parameter reference

One line per control, grouped exactly as the window is. Ranges are the GUI's
clamps; the shipped default is in parentheses. `⟳` = while the transport runs
the change lands on the **next quarter beat** (immediate when stopped). All
pitches come from the current JI set — no control invents a pitch.

## Mutation (top-left)

| Control | Range (default) | What it does |
|---|---|---|
| `Randomness` ⟳ | 0–1 (0.55) | How freely the engine picks among scored candidate classes. 0 = always the best score; 1 = flat random. |
| `Dissonance` ⟳ | −1…+1 (0) | Positive favours rougher/more complex intervals, negative favours simpler ones. |
| `Stepwise` ⟳ | 0–1 (0.25) | Chance the next edit is a small step (≤ ±1.5 semitones) from the last removed class. |
| `Temperature` ⟳ | 0–1 (0) | Opens higher-limit intervals (17, 19, 23, 29, 31) and flattens rank selection. 0 keeps the accepted Stage 2 palette. |
| `Anchor mobility` ⟳ | 0–1 (0.25) | How likely the drone/anchor class is to be removed. 0 = never; 1 = same weight as any class, random re-anchor. |
| `Changes` ⟳ | 1…`max_changes` (1) | Edits per `Mutate` press (batch width; the hard cap is 3 by default). |
| `Glide` ⟳ | 0–2 s (0.15) | Stepwise/replaced **class melt** time: when a mutation replaces one class with another, the class itself travels from the old ratio to the new one over this time, for every voice. Pure add/remove edits snap. |
| **Mutate** | button | One mutation now (next beat while running). |
| **Octave** | button | Moves one class up or down an octave in the analysis voicing; the class set is unchanged. |

**Glide in detail.** The knob is the **replacement melt**, not a global retune
time. When `Mutate` produces a *stepwise* or *replaced* event (one class
disappears and another appears), the bridge streams the **class itself** from the
old ratio to the new one over `Glide` seconds (30 Hz, cents-linear), and every
voice reads those intermediate sets: live input, playing loops (sclang's
`/ji/freqset/update` receiver + nearest-class lookup), the drone (it
retunes/bends along the moving class), the bass and lead (their next notes use
the moving class), and the arp (its pattern keeps its shape and follows the
moving class until the final rebuild). `0` is effectively instant.

A **pure addition or removal** has no old-to-new pair, so it **snaps
immediately** with a 0.005 s update glide: the Glide knob does not apply. This is
the owner's definition (2026-11-01) and a deliberate change from the older
"every mutation retunes over the Glide knob" behavior. The Live Autotune
`Retune` knob is still the persistent retune setting for the rest of the rig;
the final step of a glide sets `retuneTime` back to the Glide knob so the
resting value stays consistent. The knob is also used by **Back** and **Jump**
(one set change each); the **Home Return** uses `return_glide` (0.06 s) instead,
and **Octave** sends no set update, so it is unaffected. A new mutation, Return,
Go, Back or Jump during a melt cancels it and snaps to the previous final set
first; a transport stop does not cancel it.

**Known display mismatch (not changed here).** Every `/ji/freqset/update`
rewrites each active synth's `retuneTime`; during a melt that happens on every
step, and the final step writes the Glide knob. The Live Autotune `Retune` knob
still writes the same synth parameter directly, so its displayed value can
disagree with the synth until it is touched again. This already happened before
8.4.1 — the glide just makes it visible. The final step deliberately sets the
knob value so the resting state stays consistent with the Glide control.

## MIDI

| Control | Values | What it does |
|---|---|---|
| `Drone on` | 0/1 | Mutes/restarts the Bus 1 drone. The port stays open. |
| `Arp on` | 0/1 | Mutes/restarts the Bus 2 arp. The port stays open. |

## Home (Stage 8.4)

| Control | Range (default) | What it does |
|---|---|---|
| `Slot` | A–D | Which home slot the next `Set home`/`Go`/`Clear` targets. |
| **Set home** ⟳ | button | Captures the current set (and anchor/voicing) into the selected slot; that slot becomes active and a journey restarts from `Seed`. |
| **Go** ⟳ | button | Smooth return to the selected slot: one edit batch per quarter beat. |
| **Clear** ⟳ | button | Forgets the selected slot. |
| **Return** ⟳ | button | Returns to the active home in `ceil(distance / max_changes)` beats. |
| **Back** ⟳ | button | Restores the immediately previous set; press again to walk further back. |
| **Jump** ⟳ | button | Instant exact set swap to the active home (not a smooth return). |
| `Auto` | 0/1 (off) | After `Every` mutations, return automatically on the next beat. Needs a captured slot. |
| `Every` | 1–32 (8) | Mutations before an automatic return. |
| `Drift` | 0–12 (0) | Maximum distance from home during a journey; proposals beyond it are rejected/retried. 0 = unlimited. |
| `Seed` | 0–2³¹−1 (1) | Journey seed. Same home + seed + mutation count reproduces the journey (a manual mutation becomes part of it). |
| Status | read-only | `Home: A · 7 cl \| dist 2 \| auto 8 \| drift 3 \| back 12`, or `returning to A · 2/3 beats`, or `none`. |

## Live Autotune

`Link` moves both input channels together. `Retune`, `Mix`, `Window`, `Range`,
`Threshold`, `Gain` exist per input (`Input 0`, `Input 1`). These go straight to
sclang and are **not** beat-quantized.

| Control | Range (default) | What it does |
|---|---|---|
| `Retune` | 0.001–2 s (0.05) | Glide time of the pitch shift; 0.005 = snap, 0.5 = portamento. |
| `Mix` | 0–1 (1) | Dry/wet of the autotuned voice. |
| `Window` | 0.02–0.5 s (0.1) | `PitchShift` analysis window; shorter = tighter but more artefact. |
| `Range` | 0–24 st (12) | Maximum shift `pitchRangeLimit` in semitones. |
| `Threshold` | 0–1 (0.9) | Tartini confidence below which the last target is held. |
| `Gain` | 0–2 (1) | Input gain before tracking/shifting. |

## Loops

| Control | Range | What it does |
|---|---|---|
| Loop `1`–`4` state | read-only | `empty` / `recording` / `recorded` / `playing` / `stopped`. |
| `Free` / `Beat` | combo | Free = seconds, immediate. Beat = length in beats, Record/Play quantized to the next beat. |
| Length spin | Free 1–30 s; Beat 1–64 b | Take length. Beat mode uses `beats × 60 / BPM` at record time; tempo changes do **not** time-stretch an existing loop. |
| **Rec** | button | Starts recording that loop (beat mode: next beat). |
| **Stop Rec** | button | Ends the take early; the buffer is kept. |
| **Play** | button | Plays the loop through the autotune chain (beat mode: next beat). |
| **Stop** | button | Stops playback; the buffer is kept. |
| **Clear** | button | Frees the loop's buffer. |
| **Stop All** | button | Stops all four loops. |
| `Default len` | 1–30 s (8) | Length used by Rec when the loop has no custom length. |

## Rhythm

| Control | Range (default) | What it does |
|---|---|---|
| **Play/Stop** | button | Transport. Beat-mode loops, drums, bass, lead and gated drone modes start/stop with it. |
| `BPM` | 20–300 (120) | Quarter-note tempo. Immediate; pending queue beats are recomputed. |
| `Meter` num / den | 1–16 / 1,2,4,8,16 (4/4) | Bar length and the 16th-step grid. Immediate. |
| `Drums on` | 0/1 ⟳ | Enables the Bus 3 drum output (needs the bus). |
| Rate combo | Half / Normal / Double (Normal) ⟳ | 0.5× / 1× / 2× the 16th-note step rate. |
| 16 lane toggles | on/off ⟳ | `Kick 1/2`, `Snare 1/2`, `Perc 1–12` = MIDI notes 36–51. |
| **Resync** | button | Re-broadcasts the canonical tempo/meter/transport to sclang. |

## Groove

| Control | Range (default) | What it does |
|---|---|---|
| `Preset` | `four`, `break`, `half`, `techno` ⟳ | Base drum pattern. Manual grid edits persist until the preset is reloaded (`Reset grid`). |
| `Density` | 0–1 (0) ⟳ | Extra ghost hits per step. |
| `Syncopation` | 0–1 (0) ⟳ | Shifts/extends hits off the beat. |
| `Swing` | 0–1 (0) ⟳ | Delays odd 8th/16th steps; also pushed to the lead when `Groove link` is on. |
| `Probability` | 0–1 (0) ⟳ | Chance a generated ghost is dropped. |
| `Seed` | 0–2³¹−1 (1) ⟳ | Same preset + modifiers + seed + bar reproduce the same ghosts. |
| `Show grid` | on/off | Shows the 16-lane × N-step editor (built lazily; hidden by default). |
| `Reset grid` | button ⟳ | Reloads the current preset's grid. |
| Grid cells | on/off ⟳ | Per-lane per-16th-step on/off; edits replace generated cells. |

## Bass

| Control | Range (default) | What it does |
|---|---|---|
| `Bass on` | 0/1 ⟳ | Enables the Bus 4 bass voice (the port stays open). |
| `Preset` | `pulse`, `techno`, `chaos`, `custom` ⟳ | `pulse` = downbeat drone bass, `techno` = moving walk, `chaos` = wide seeded line. Manual edits show `custom`. |
| `Grid` | `1/4`, `1/8`, `1/16` ⟳ | Step resolution of the bass pattern. |
| `Pitch` | `root`, `anchor`, `walk`, `random`, `cycle` ⟳ | Which set member each step uses. |
| `Len` | 1–32 steps ⟳ | Pattern length in grid steps. The shipped default is auto = one bar (the spin starts at 16). |
| `Sub` + octaves | 0/1, 1–2 ⟳ | Optional sub note on the same bus, channel 3. |
| `Bass density` | 0–1 (0) ⟳ | Fraction of steps with a note. |
| `Bass sync` | 0–1 (0) ⟳ | Syncopated placement. |
| `Bass swing` | 0–1 (0) ⟳ | Odd-step delay. |
| `Gate` | 0.05–8 beats (3.5) ⟳ | Note length. |
| `Accent` | 0–1 (0) ⟳ | Velocity accent on strong steps. |
| `Movement` | 0–1 (0) ⟳ | Hold-vs-change and leap size for the pitch walk. |
| `Oct jump` | 0–1 (0) ⟳ | Probability of an octave jump. |
| `Oct` (range) | 0–3 (0) ⟳ | Allowed ± octave offsets around `Base`. |
| `Base` | −3…0 (−2) ⟳ | Base octave relative to the fundamental. |
| `Seed` | 0–2³¹−1 (1) ⟳ | Same set + controls + seed + bar/step reproduce the line. |
| Status / `Last:` | read-only | Preset/grid/pitch summary and the last triggered note. |

## Arp

| Control | Range (default) | What it does |
|---|---|---|
| `Step` | 0.05–2 s (0.25) ⟳ | Time between arp notes in free mode. |
| `Gate` | 0.01–2 s (0.20) ⟳ | Note length; clamped to ≤ `Step`. |
| `Vel` | 1–127 (90) ⟳ | Arp velocity. |
| `Sync` | `free`, `beat` ⟳ | `beat` derives the step from BPM/`Div` and snaps to the grid; `free` uses `Step`. |
| `Div` | `1/4`, `1/8`, `1/16`, `1/8T` ⟳ | Beat division when `Sync = beat`. |
| `Order` | `forward`, `reverse`, `pingpong`, `random` ⟳ | Playback order of the six-note pattern. |
| `Shared` | `anchor`, `random` ⟳ | How the shared note is chosen. |
| `Oct` (three spins) | −8…+8 each (0, 1, 2) ⟳ | Octave offsets of shared / non-shared 1 / non-shared 2. |
| `Evolve` | 0/1 ⟳ | Regenerates the arp pattern on its own, without a mutation. |
| `Every` | 1–64 beats (4) ⟳ | Evolution period. |
| `Depth` | 0–1 (0) ⟳ | 0 re-picks free notes only; higher also moves the shared note/octaves. |
| `Seed` | 0–2³¹−1 (1) ⟳ | Reproducible pattern, order and evolution. |
| Status | read-only | `on \| 0.25 s / 0.20 s \| forward \| anchor \| seed 1` (beat mode shows the division). |

## Drone modes

| Control | Range (default) | What it does |
|---|---|---|
| `Mode` | `hold`, `pulse`, `sequence`, `bend` ⟳ | `hold` = Stage 6 sustained anchor; `pulse` = repeating anchor pattern; `sequence` = figure walking the set; `bend` = hold + slide. `pulse`/`sequence`/`bend` wait for the transport; `hold` never does. |
| `Vel` | 1–127 (90) ⟳ | Drone velocity. |
| Pulse `Notes` | 2–8 (4) ⟳ | Notes per repeating pattern. |
| Pulse `Grid` | `1/4`, `1/8`, `1/16` ⟳ | Pulse step length. |
| Pulse `Gate` | 0.05–2 beats (0.5) ⟳ | Pulse note length (clamped to the grid). |
| Pulse `Span` | 0–2 (1) ⟳ | Octave range of the seeded anchor pattern. |
| Bend `Dir` | `down`, `up` ⟳ | Slide direction. |
| Bend `Semis` | 1–12 st (2) ⟳ | Slide size (uses the 48 st MPE range). |
| Bend `Slide` | 0.05–4 s (0.4) ⟳ | Slide duration. |
| Bend `Hold` | 0.25–16 beats (2.5) ⟳ | Hold before the slide. Cycle length is derived and shown in the status line. |
| Sequence `Seq notes` | 2–8 (5) ⟳ | Notes per figure. |
| Sequence `Length` | `1/4`, `1/2`, `1`, `2`, `4` bars (1) ⟳ | How long each note is held. |
| Sequence `Gate` | 10–100 % (100 %) ⟳ | Note length as a fraction of the bar-note; 100 % = legato with a ~10 ms gap. |
| `Seed` | 0–2³¹−1 (1) ⟳ | Seeded pulse pattern and sequence resolution. |
| Status | read-only | Mode summary, plus `waiting for transport (press Play)` or `playing`. |

## Lead

| Control | Range (default) | What it does |
|---|---|---|
| `Lead on` | 0/1 ⟳ | Enables the Bus 5 melody (the port stays open). |
| `Grid` | `1/4`, `1/8`, `1/16`, `1/8T` ⟳ | Phrase resolution. |
| `Variation` | `identity`, `transpose`, `invert`, `retrograde`, `thin`, `ornament`, `rhythm`, `random` ⟳ | Transformation applied to the current motif. |
| `Phrase` | 1–8 bars (2) ⟳ | Motif length. |
| **Vary** | button ⟳ | Advances one variation. |
| `Density` | 0.1–1 (0.75) ⟳ | Fraction of grid steps with a note; bar downbeats always on. |
| `Gate` | 0.05–4 beats (0.5) ⟳ | Note length, clamped to the next note. |
| `Vel` | 1–127 (90) ⟳ | Base velocity; downbeats get +15. |
| `Swing` | 0–1 (0) ⟳ | Used only when `Groove link` is off. |
| `Groove link` | 0/1 ⟳ | Follow the drum groove's current swing. |
| `Oct` (range) | 0–2 (1) ⟳ | How far the line may travel by octave. |
| `Base` | −2…+2 (0) ⟳ | Base octave relative to the fundamental. |
| `Var every` | 0–16 bars (0) ⟳ | 0 = manual `Vary` only; N = next variation every N bars. |
| `Seed` | 0–2³¹−1 (1) ⟳ | Reproducible motif and variation sequence. |
| Status / `Last:` | read-only | `on \| 1/8 · 2 bars · 0.75 \| identity #0 \| seed 1`, plus the last note. |

## Status (read-only)

`BRIDGE`/`SCLANG` connection, the full ratio + Hz set, anchor, fundamental,
mutation-control summary, CPU, loop states, and the message line used for
"queued for the next beat" / "applied immediately" / errors.
