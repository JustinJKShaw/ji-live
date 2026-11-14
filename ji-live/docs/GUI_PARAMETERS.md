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

## State (Stage 8.5)

| Control | Range (default) | What it does |
|---|---|---|
| `Name` | 1–40 chars | Editable list of saved states (sorted, case-insensitive). Rules: first character alphanumeric, then `A-Z a-z 0-9 space . _ -`; no `/`, `\`, `..`, control characters or a leading dot. |
| **Save** | button | Saves the whole performance configuration under `Name` (immediate, even while running). Disabled while the name is empty. |
| **Load** ⟳ | button | Recalls the state: one set change and one `/ji/freqset/update`, immediate when stopped, next quarter beat while running. Disabled unless the name is in the list. |
| **Delete** | button | Deletes the state file (immediate). Disabled unless the name is in the list. |
| **Refresh** | button | Asks the bridge for a fresh list + status (immediate). |
| Status | read-only | `State: 3 saved \| saved A-drone \| loaded A-drone`, `State: none saved`, or `State: error …`. |

A state holds the frequency set, mutation controls, the four home slots + active
home + journey bookkeeping, tempo/meter, drums, groove + 32-int grid, and every
bass/arp/lead/drone setting + the lead variation index. It does **not** hold the
transport, loops, the autotune parameters, pending quantizer actions or the Back
history. A load cancels a pending return and an active class glide first. See
[docs/README_stage8_5.md](README_stage8_5.md).

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
| Loop `1`–`4` state | read-only | `empty` / `recording` / `recorded` / `playing` / `stopped` / `dubbing` / `counting N`. |
| `Free` / `Beat` | combo | Free = seconds, immediate. Beat = length in beats, Record/Play quantized to the next beat. |
| Length spin | Free 1–30 s; Beat 1–64 b | Take length. Beat mode uses `beats × 60 / BPM` at record time; tempo changes do **not** time-stretch an existing loop. |
| **Rec** | button | Starts recording that loop (beat mode: next beat). Honors `Count-in`. Replaces the loop's contents. |
| **Stop Rec** | button | Ends the take — or an overdub — early; the buffer is kept. |
| **Play** | button | Plays the loop through the autotune chain (beat mode: next beat). |
| **Stop** | button | Stops playback (and a pending count-in); the buffer is kept. |
| **Dub** | button (Stage 8.6) | Overdub: records **over** the loop for one pass (`preLevel 1`, keeps the contents) and the loop keeps playing. On an empty loop it records a fresh take. `Stop Rec` ends a dub early. |
| **Clear** | button | Frees the loop's buffer (the loop's edit state and the memory slots are untouched). |
| **Stop All** | button | Stops all four loops (recorders, players, grain schedules, pending count-ins). |
| `Default len` | 1–30 s (8) | Length used by Rec when the loop has no custom length. |

### Loop edit row (Stage 8.6, the loop chosen in the row's `Loop` combo)

| Control | Range (default) | What it does |
|---|---|---|
| `Loop` | 1–4 (1) | Which loop the edit and grain rows show and change. |
| `Rev` | 0/1 (off) | Reverse playback: the read head keeps its position and the direction flips (no restart, no click). |
| `Rate` | `2/1`, `3/2`, `5/3`, `5/4`, `4/3`, `1/1`, `3/4`, `4/5`, `2/3`, `3/5`, `1/2` (`1/1`) | Varispeed at a fixed length ratio; it also re-pitches (2/1 = +12 st, 3/2 = +7.02 st, 2/3 = −7.02 st, 3/5 = −8.84 st). The loop still runs through the JI autotune, so the current set may pull it partly back — intended "new material", not an exact transposition. |
| `Gain` | 0–1 (0.70) | Per-loop output gain. 0.70 is the accepted loudness. |
| `Count-in` | `off`, `1 beat`, `1 bar`, `2 bars` (`off`) | Delays a **Rec** by that many quarter beats on the tempo clock, starting at the next beat; a bar is the current meter's bar **rounded up** to whole quarter beats (4/4 = 4, 3/4 = 3, 6/8 = 3, 5/8 = 3, 7/8 = 4). Applies to Rec only, not to a dub. Free mode counts with the transport stopped; beat mode is cancelled by a transport stop; a second Rec skips it in both modes. |
| `Auto` | 0/1 (off) | Auto-play: starts the loop when a take ends (buffer full or `Stop Rec`). Beat mode needs a running transport, otherwise the take stays `recorded` and one line says so. |
| `Mem` | `A`–`D` | Selected memory slot for Save/Recall/Clr. |
| **Save** | button | Copies the selected loop's audio + edit state into the selected slot. Refused while recording/dubbing/counting or without a buffer. |
| **Recall** | button | Copies the selected slot into the selected loop; the loop stays **stopped** (press Play). Disabled for an empty slot. |
| **Clr** | button | Frees the selected slot. Disabled for an empty slot. |
| Status | read-only | Normal: `Loop 2 · ×3/2 · rev · count-in 1 bar · auto · gain 0.70`. Grain: `Loop 2 · ×3/2 · grain/beat · pos 50 % · motion forward · pitch ×3/2 · morph 60 % · breath 40 % · snap · gain 0.70` (`morph`, `melt` and `breath` appear only when non-default). Since 9.0 the label is on the Harm row. When a harm mode is on it also shows e.g. `harm cascade · 4× · 0.50 s · mix 35 %` (with the division, e.g. `1/4d`, when fixed). The pitch is the **effective** (snapped) ratio when Snap is on. No tune-bypass state: the loop is always in the autotune. |

Memory slots hold the sound edit state (`reverse`, `rate`, `gain`, `mode`,
`sync`, `division`, `grain_*`, `harm_*`) and the buffer duration, but **not**
`Count-in` / `Auto` (record preferences). They are **session-only**: no file is
written and re-evaluating the rig empties them.

All loop edit changes are **immediate** (real-time performance gestures); they
are not beat-quantized.

### Grain row (Stage 8.6.1, the selected loop)

| Control | Range (default) | What it does |
|---|---|---|
| `Grain` | 0/1 (off) | Switches the loop between normal and granular playback (crossfaded, no click). Off disables the Grain row **and** the Grain motion row. |
| `Sync` | `free`, `beat` (`free`) | `free` = own clock (runs with the transport stopped). `beat` = a small burst per `Div`, transport-gated. |
| `Div` | `1/16`, `1/8`, `1/4`, `1/2`, `1` (`1/16`) | Beat-synced grain interval in beats. |
| `Rate` | 0.5–80 grains/s (20) | Free-mode grain rate; in beat mode it is the burst density. 20 × 120 ms is a continuous cloud. |
| `Size` | 5–500 ms (120) | Grain duration. |
| `Pos` | 0–100 % (50) | Centre of the read position in the buffer (the scan moves around it). |
| `Scatter` | 0–100 % (10) | Random position scatter (the grain is clamped so it never reads outside the buffer). |
| `Pitch` | 0.25–4.0 (1.0) | **Requested** grain playback ratio. With `Snap` on it is snapped to the current JI set; the status line shows the effective ratio (`pitch ×3/2`). |
| `Rev%` | 0–100 % (0) | Per-grain reverse probability. |

### Grain motion row (Stage 8.6.1, the selected loop)

| Control | Range (default) | What it does |
|---|---|---|
| `Motion` | `off`, `forward`, `backward`, `pingpong`, `drift` (`forward`) | How the grain read position travels through the buffer. `off` = fixed at `Pos` (the 8.6 behaviour). |
| `Speed` | 0.001–0.5 loop-lengths/s (0.03) | Scan speed; 0.03 crosses an 8 s loop in about 33 s. |
| `Depth` | 0–100 % (60) | How far around `Pos` the scan travels; 0 % pins the position. |
| `Size Jit` | 0–100 % (20) | Per-grain size variation. |
| `Pitch Jit` | 0–100 % (0) | Per-grain pitch scatter (shimmer). |
| `Snap` | on/off (on) | Snap the grain ratio to the nearest ratio of the **current JI set** (× octaves); a set mutation re-snaps a playing grain loop. Off passes the raw ratio. |
| `Smear` | 0–100 % (0) | Post-autotune diffusion (two short allpasses). 0 % is the dry accepted chain. |
| `Freeze` | on/off (off) | Hold the motion where it is; unfreezing continues from the same position. |

`Sync = beat` with the transport stopped shows `beat grains need the transport
(press Play)`; while frozen the hint reads `motion frozen`.

### Morph row (Stage 8.6.2, the selected loop)

| Control | Range (default) | What it does |
|---|---|---|
| `Morph` | 0–100 % (0) | **Dry loop ↔ grain cloud**: 0 % is the original loop (normal playback through the autotune, bit-identical to Grain off), 100 % the full 8.6.1 cloud; in between the two are smeared together by an equal-power crossfade. Disabled while Grain is off. |
| `Melt` | on/off (off) | Start at the original and progressively melt into the grains: Play (or ticking Melt) ramps Morph from 0 to the slider value over ~4 s (`~ji_loop_melt_seconds` in sclang); unticking melts back to 0. Moving Morph while melting re-targets the ramp. Disabled while Grain is off. |
| `Filter` | 0–100 % (0) | Moving melt filter (post-autotune): a resonant lowpass (700 Hz–6 kHz) and a low cut (90–420 Hz) on two slow LFOs sweep the dry+grain mix so the layers fuse. 0 % is an exact bypass; it also works with Grain off. |
| `F.Rate` | 0.01–1 Hz (0.12) | Filter movement speed: 0.12 is a slow breath (about 8 s per cycle), 0.5 a wobble. |
| `Breath` | 0–100 % (0) | The loop's **own** grain breath (Stage 8.7): an LFO inside the voice modulates the free/burst grain density and the grain size, so the cloud swells and thins. 0 % is an exact bypass. Disabled while Grain is off. |
| `B.Rate` | 0.02–1 Hz (0.08) | Grain-breath speed: 0.03 is a long breath, 0.3 a pulse. |

### Harm row (Stage 9.0, the selected loop)

The FX reads the loop's **post-`PitchShift`** send and writes wet only to the
loop's output bus; the dry loop is upstream. `Harm = off` (default) creates no
FX synth.

| Control | Range (default) | What it does |
|---|---|---|
| `Harm` | `off`, `Cascade`, `Delay` (`off`) | `Cascade` captures one segment and plays it once with harmonics entering in order; `Delay` is a continuous harmonized buffer delay (varispeed character, no trigger). |
| `Mix` | 0–100 % (35) | Wet return. 0 % is the accepted loop; the dry loop is always upstream. |
| `Time` | 0.05–2.00 s (0.50) | Free time: Cascade segment length / Delay base window. Read-only while `TimeSync` is a division (shows the derived seconds). |
| `TimeSync` | `free`, `1/32`, `1/32d`, `1/32t`, `1/16`, `1/16d`, `1/16t`, `1/8`, `1/8d`, `1/8t`, `1/4`, `1/4d`, `1/4t`, `1/2`, `1/2d`, `1/2t`, `1/1`, `1/1d`, `1/1t`, `1 bar` (`free`) | Fixed tempo-locked time: `d` = dotted (×1.5), `t` = triplet (×2/3), `1 bar` = the current meter's bar. A BPM change retimes a playing fixed FX. |
| `Steps` | 1–8 (4) | Harmonics 1×…N×: 1 = fundamental only, 2 adds the octave, 3 the 12th, 4 the 16th … |
| `Space` | 0–1000 ms (80) | Cascade entry spacing (converted to seconds on the wire); 0 = all harmonics together. Inert in Delay. |
| `Fall` | 0–100 % (50) | High-harmonic roll-off in both modes: amplitude `1 / k^(0.5 + 1.5 × Fall)`. 0 % = `1/√k`, 100 % = `1/k²`. |
| `Auto` | on/off (on) | Cascade auto-fire; first capture after one segment length, then after each cascade. Inert in Delay. |
| **Fire** | button | One Cascade pass now; enabled only in Cascade while the selected loop is playing. Sends `/ji/loop/fx_fire`, not `edit_set`. |

`Space`/`Auto`/`Fire` are disabled in `Delay` and `off`; `Mix`/`Time`/`TimeSync`/
`Steps`/`Fall` remain enabled in both active modes. For a plain fixed-time
delay, use `Harm = Delay`, `Steps = 1` and a `TimeSync` note value. The status line (on the Harm
row) shows e.g. `harm cascade · 4× · 1/4d · 0.75 s · mix 35 %` when on, and
nothing extra when off. Memory slots copy the harm edit state with the rest of
the loop edit (session-only).

There is no dedicated **Burst** widget yet: `grain_burst` (0–1, default 0.6)
is reachable through `/ji/loop/edit_set` and sclang and sets how much extra
grain density a beat-mode subdivision gets on top of `Rate`. Stopping the
transport stops a beat-synced grain loop even when the loop's own mode is
`free`. Free-mode grains keep running. The Grain motion row is only live while
`Grain` is on.

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
| `Breath` | on/off (off) | Makes the played grid breathe: timing offsets + velocity swell. The BPM and bar grid never move; off is exactly the old timing. |
| `Depth` | 0–100 % (35) | How far events move early/late: up to ±½ of a 16th at 100 %. |
| `Period` | 1–32 bars (4) | One full inhale + exhale. 1 = a fast pulse, 16 = a long swell. |
| `Shape` | sine/triangle/inhale/exhale (inhale) | Where the breath accelerates. |
| `Intensity` | 0–100 % (50) | Velocity/expression swell; 0 % is timing only. |
| `Phase` | 0–100 % (0) | Rotates the breath against the bar. |
| `Arp local` | 0–100 % (0) | The arp's extra breath depth. With `Arp sync` off it also breathes half a cycle out of phase; with sync on it is just deeper. |
| `Arp sync` | on/off (off) | Off: the arp breathes half a cycle out of phase with the groove. On: it uses the master phase, so it inhales/exhales with the drums, bass and lead. This is **breath phase**, not the Arp group's beat `Sync`. |

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
| `Hits` | on/off (off) | The editable one-bar hit row overrides the generated pattern. Enabling seeds it from the generated pattern if it is empty. While Hits is on, `Len` is not used. |
| `Copy` / `Clear` | buttons | Seed the row from the generated pattern of bar 1 / turn every hit off (deliberate silence). |
| Hit buttons | on/off (hidden until Hits) | One row of `bar_steps` cells (4/8/16 in 4/4 for 1/4, 1/8, 1/16; 3/6/12 in 3/4). Clicking sends `/ji/bass/set_hit` immediately. The row re-shapes (never clears) on a meter/grid change. |
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
| `Note len` | 0/1 (off) ⟳ | On = each note's own length sets the gate (recorded takes); off = `Gate` caps every note (the generated line). Mirrors `gate_mode`. |
| `Vel` | 1–127 (90) ⟳ | Base velocity; downbeats get +15. |
| `Swing` | 0–1 (0) ⟳ | Used only when `Groove link` is off. |
| `Groove link` | 0/1 ⟳ | Follow the drum groove's current swing. |
| `Oct` (range) | 0–2 (1) ⟳ | How far the line may travel by octave. |
| `Base` | −2…+2 (0) ⟳ | Base octave relative to the fundamental. |
| `Var every` | 0–16 bars (0) ⟳ | 0 = manual `Vary` only; N = next variation every N bars. |
| `Seed` | 0–2³¹−1 (1) ⟳ | Reproducible motif and variation sequence. |
| Status / `Last:` | read-only | `on \| 1/8 · 2 bars · 0.75 \| identity #0 \| seed 1`, plus the last note. |

## Lead input (Stage 9.1)

| Control | Range (default) | What it does |
|---|---|---|
| `Port:` | read-only | The MIDI input the bridge opened. The GUI never enumerates devices; edit `config/lead_input_config.json`/`--lead-in-port` and press `Refresh`. |
| **Refresh** | button | Asks the bridge to close and re-open the port (one `[LEAD-IN]` line). |
| `Keys` | 0/1 (off) | QWERTY play: `a w s e d f t g y h u j`; `z`/`x` shift the octave. GUI-local; keys only sound while `Live` or `Rec` is on. Ignored while a text box/combo/spin has focus or a modifier is held. |
| `Live` | 0/1 (off) | Monitor the input. On stops the sequencer; off returns it on the next bar line (`played` when a take is loaded, else `generated`). |
| **Rec** | button | Record from the next bar line; enabled only while the transport runs and the input port is open. |
| **Stop** | button | Cancel a count-in, or keep the partial take; enabled only while counting/recording. |
| **Revert** | button ⟳ | Restore the recorded base (all variations undone); enabled only for a played line. |
| **Clear** | button ⟳ | Drop the played line and return to the generated one; also clears `Live`. |
| `Quant` | 0–100 % (100 %) | Quantise strength: 1 = exact grid, 0 = as played (the stored step is still the rounded integer). |
| `Count-in` | `off`, `1 beat`, `1 bar`, `2 bars` (`1 bar`) | Silent count before the window. A bar is `ceil(bar_quarters)` whole quarter beats (same rule as the loop count-in). |
| `Bars` | 1–8 (2) | Take length. |
| `Melody` | list | Saved melodies from `/ji/lead/melody/list` (one per `melodies/<name>.json`). |
| `Name` | 1–40 chars | Save name; pre-filled from the selected melody. |
| **Save** | button | Save the current played take; disabled on an empty name. |
| **Load** | button ⟳ | Load the selected melody (grid/phrase/notes); enabled only for a saved name. |
| **Del** | button | Delete the selected melody file; enabled only for a saved name. |
| Status line | read-only | `Input: Launchkey 49 - idle - 13 notes` and `Melody: intro line (13 notes) - played` (or `Melody: none (generated line)`). |

## Fretboard (Stage 8.8, read-only improvisation aid)

| Control | Range (default) | What it does |
|---|---|---|
| `Strings` | 6–8 (6) | Strings on the diagram. Growing prepends a string a low fourth below the current lowest (7-string B adds B1, 8-string F# adds F#1); shrinking drops the lowest strings. |
| `Frets` | 0–24 (24) | Frets drawn. 0 draws the open strings only; 12 is a short diagram, 24 a full neck. |
| `Tuning` | Standard E, Drop D, Drop C, DADGAD, Open G, Open D, Half-step down, 7-string B, 8-string F#, `Custom` | Whole-neck presets. Picking one replaces every open string. `Custom` is display-only and appears as soon as one string is edited. |
| String combos (up to 8) | any MIDI note 0–127 (`E2 A2 D3 G3 B3 E4`) | The open string, low → high, labelled with the exact note name. Editing one switches the preset label to `Custom`. |
| `Names` | on/off (on) | Draws the note name inside each marker. Off keeps the markers. |
| Set label | read-only | `12-TET set (A = 220.00 Hz): A B C# D E F# G#`, or `Fretboard: waiting for the set`. |
| Legend | read-only | `A 1/1 +0¢ · B 9/8 +4¢ · C# 5/4 -14¢ · …`. Cents are signed (negative = the JI frequency is flat of the 12-TET note); a pitch class reached by two ratios shows the closest source first and the other in parentheses. |
| Board | read-only | Every occurrence of every set pitch class over all frets. Root `#d9822b`, other scale notes `#3d7ea6`, anchor ring `#8e44ad`; hover a marked position for `E2 fret 5 · A · 1/1 · +0¢ · 220.00 Hz` (plus other sources). Low string at the bottom. |

No control here sends OSC. The tuning is **session-only** (reset to Standard E
on GUI restart) and is not part of a performance state or `config/`.

## Status (read-only)

`BRIDGE`/`SCLANG` connection, the full ratio + Hz set, anchor, fundamental,
mutation-control summary, CPU, loop states, and the message line used for
"queued for the next beat" / "applied immediately" / errors.
