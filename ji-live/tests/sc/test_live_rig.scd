// ===========================================================================
//  JI Station -- Live Rig -- test_live_rig.scd
// ---------------------------------------------------------------------------
//  One testable rig: two live autotuned inputs plus two loop voices, each on
//  its own SC output channel, with the Stage 3 Python mutation receiver.
//
//      input 0 -> Tartini -> nearest class -> PitchShift -> SC out 0
//      input 1 -> Tartini -> nearest class -> PitchShift -> SC out 1
//
//      input 0 (dry) -> RecordBuf -> loop 1 -> Tartini -> PitchShift -> SC out 2
//      input 1 (dry) -> RecordBuf -> loop 2 -> Tartini -> PitchShift -> SC out 3
//
//      Stage 9.0: each loop's post-PitchShift signal is also sent to a private
//      audio bus (18-21 on this boot); a \ji_harmonic_delay after the player returns wet
//      only to the same SC out 2/3.  No FX synth when Harm = off.
//
//  Four mono outputs on the first four BlackHole 16ch channels:
//      SC 0 -> Ableton track 1   autotuned input 0
//      SC 1 -> Ableton track 2   autotuned input 1
//      SC 2 -> Ableton track 3   loop 1
//      SC 3 -> Ableton track 4   loop 2
//
//  The Stage 4 extra loop voices 3 and 4 are still allocated; if used they sum
//  into SC 2 and SC 3 respectively.  The four-channel product uses loops 1/2.
//
//  The loops record PRE-autotune audio.  Playback runs its own Tartini +
//  PitchShift chain, so a Python mutation retunes every live and loop voice
//  without replaying or transposing a loop as a block.
//
//  PREREQUISITES
//    1. Stage 5 startup.scd evaluated: 2 in / 16 out, MiniFuse 2 input,
//       BlackHole 16ch output, buses 0-18, SynthDefs loaded.
//    2. Ableton: four mono tracks from BlackHole 16ch inputs 1, 2, 3, 4,
//       Monitor In, master to MiniFuse 2.  See docs/README_live_rig.md.
//    3. Do not run tests/sc/test_bridge.scd or tests/sc/test_dual_loop.scd at the same time.
//       This file stops the known routines and replaces ~ji_freqset_oscdef.
//
//  START ORDER
//    1. startup.scd
//    2. this file
//    3. cd ji-live
//       uv run --with python-osc python control/ji_bridge.py --interactive --glide-time 0.15
//
//  LOOP LENGTH
//    ~ji_loop_default_seconds = 8.0;   // initial default
//    ~ji_set_loop_default.(12.0);      // change the default at runtime
//    ~ji_record_loop.(1);              // uses the current default
//    ~ji_record_loop.(1, 20.0);        // one-take override
//
//  LOOP COMMANDS
//    ~ji_record_loop.(1);        // input 0 -> loop 1 -> SC 2
//    ~ji_stop_record.(1);        // stop early (or let the buffer fill)
//    ~ji_play_loop.(1);
//    ~ji_stop_loop.(1);
//    ~ji_record_loop.(2);        // input 1 -> loop 2 -> SC 3
//    ~ji_play_loop.(2);
//    ~ji_stop_all.();
//
//  STAGE 8.6 LOOP EDITING (see docs/README_stage8_6.md)
//    ~ji_dub_loop.(1);                    // overdub one pass onto loop 1
//    ~ji_loop_set_edit.(1, \reverse, 1);
//    ~ji_loop_set_edit.(1, \rate, 0.6666667);
//    ~ji_loop_set_edit.(1, \mode, "grain");
//    ~ji_loop_mem_save.(0, 1);            // slot A <- loop 1
//    ~ji_loop_mem_recall.(0, 2);          // slot A -> loop 2 (stopped)
//    ~ji_loop_mem_clear.(0);
//  Memory slots are session-only; re-evaluating this file frees them.
//
//  STAGE 8.6.1 GRANULAR REDESIGN (see docs/README_stage8_6.md §14)
//    ~ji_loop_set_edit.(1, \grain_motion, "forward");   // off/backward/pingpong/drift
//    ~ji_loop_set_edit.(1, \grain_speed, 0.03);         // buffer lengths per second
//    ~ji_loop_set_edit.(1, \grain_depth, 0.6);
//    ~ji_loop_set_edit.(1, \grain_size_jitter, 0.2);
//    ~ji_loop_set_edit.(1, \grain_pitch_jitter, 0.1);   // shimmer (0 = off)
//    ~ji_loop_set_edit.(1, \grain_pitch, 1.5);          // snaps to 3/2 with Snap on
//    ~ji_loop_set_edit.(1, \grain_pitch_snap, 0);       // raw ratio instead
//    ~ji_loop_set_edit.(1, \grain_smear, 0.3);          // post-autotune blur
//    ~ji_loop_set_edit.(1, \grain_freeze, 1);           // hold the motion
//    ~ji_loop_set_edit.(1, \grain_burst, 0.6);
//
//  STAGE 8.6.2 MORPH / MELT / MOVING FILTER (see docs/README_stage8_6.md §15)
//    ~ji_loop_set_edit.(1, \grain_morph, 0.5);      // 0 = original, 1 = grains
//    ~ji_loop_set_edit.(1, \grain_melt, 1);         // ramp 0 -> morph over ~4 s
//    ~ji_loop_set_edit.(1, \grain_melt, 0);         // ramp back to 0
//    ~ji_loop_set_edit.(1, \grain_filter, 0.3);     // moving melt filter
//    ~ji_loop_set_edit.(1, \grain_filter_rate, 0.12);// about 8 s per sweep
//    ~ji_loop_melt_seconds = 6.0;                   // ramp length in sclang
//  Morph needs Grain on; with Melt on, Play starts the loop at the original.
//  The effective (snapped) ratio is what the voice receives; the GUI status
//  line shows it as a fraction (pitch x3/2).
//
//  STOP (see the bottom of this file)
// ===========================================================================

// --- Preconditions ---------------------------------------------------------
if (s.serverRunning.not) {
    Error("test_live_rig: the server is not running (or is still booting). Evaluate startup.scd first.").throw;
};

~ji_stage4_buses = [
    ~ji_autotune_freq2, ~ji_autotune_hasfreq2, ~ji_autotune_target2,
    ~ji_loop_freq1, ~ji_loop_freq2, ~ji_loop_freq3, ~ji_loop_freq4,
    ~ji_loop_hasfreq1, ~ji_loop_hasfreq2, ~ji_loop_hasfreq3, ~ji_loop_hasfreq4
];
if (~ji_stage4_buses.any { |bus| bus.isNil }) {
    Error("test_live_rig: the Stage 4 buses (8-18) are missing. Re-evaluate the Stage 5 startup.scd first.").throw;
};

// Stage 9.0: the four private FX AUDIO buses.  startup.scd allocates them after the hardware I/O (18-21 on this boot);
// this file re-uses them and, when it is evaluated after an older startup that
// lacks them, allocates the missing ones (the audio allocator picks the next
// free private indices).  A missing bus after this is a hard error.
~ji_loop_fx_buses = [
    ~ji_loop_fx1, ~ji_loop_fx2, ~ji_loop_fx3, ~ji_loop_fx4
];
if (~ji_loop_fx_buses.any { |bus| bus.isNil }) {
    "*** WARNING: Stage 9 loop FX audio buses missing -- allocating them now.".postln;
    if (~ji_loop_fx1.isNil) { ~ji_loop_fx1 = Bus.audio(s, 1) };
    if (~ji_loop_fx2.isNil) { ~ji_loop_fx2 = Bus.audio(s, 1) };
    if (~ji_loop_fx3.isNil) { ~ji_loop_fx3 = Bus.audio(s, 1) };
    if (~ji_loop_fx4.isNil) { ~ji_loop_fx4 = Bus.audio(s, 1) };
    ~ji_loop_fx_buses = [
        ~ji_loop_fx1, ~ji_loop_fx2, ~ji_loop_fx3, ~ji_loop_fx4
    ];
};
if (~ji_loop_fx_buses.any { |bus| bus.isNil or: { bus.index.isNil } }) {
    Error("test_live_rig: a Stage 9 loop FX audio bus is missing (startup.scd failed?).").throw;
};
if (~ji_loop_fx_buses.collect { |bus| bus.index }.asSet.size != 4) {
    Error("test_live_rig: two Stage 9 loop FX buses share an index.").throw;
};

if (SynthDescLib.global[\ji_autotune_ch].isNil
    or: { SynthDescLib.global[\ji_looper_recorder].isNil
        or: { SynthDescLib.global[\ji_looper_player].isNil
            or: { SynthDescLib.global[\ji_loop_voice].isNil
                or: { SynthDescLib.global[\ji_buf_copy].isNil
                    or: { SynthDescLib.global[\ji_harmonic_delay].isNil } } } } }) {
    Error("test_live_rig: a required SynthDef is missing. Evaluate startup.scd (it loads synthdefs/*.scd) first.").throw;
};

// --- Stop the known old Stage 0/1/3/4 audio and routines --------------------
//  If you rebooted the server without restarting sclang, the old Synth objects
//  below still carry the previous server's node IDs.  Freeing them can post
//  "FAILURE IN SERVER /n_free Node ... not found".  That is harmless; the new
//  synths are created after this cleanup.  Restart the interpreter for a
//  completely clean slate.
~ji_tartini_routine !? { |routine| routine.stop };
~ji_cpu_routine !? { |routine| routine.stop };
~ji_tartini_debug_synth !? { |synth| synth.free };

~ji_lookup_routine !? { |routine| routine.stop };
~ji_cpu_routine_stage1 !? { |routine| routine.stop };
~ji_autotune_synth !? { |synth| synth.free };

~ji_bridge_lookup_routine !? { |routine| routine.stop };
~ji_cpu_routine_stage3 !? { |routine| routine.stop };
~ji_freqset_oscdef !? { |def| def.free };
~ji_buf_freqset !? { |buf| if (buf.bufnum.notNil) { buf.free } };

~ji_dual_lookup_routine !? { |routine| routine.stop };
~ji_dual_cpu_routine !? { |routine| routine.stop };
~ji_autotune_synth_ch0 !? { |synth| synth.free };
~ji_autotune_synth_ch1 !? { |synth| synth.free };
~ji_loop_players !? { |players| players.do { |synth| synth !? { synth.free } } };
~ji_loop_recorders !? { |recorders| recorders.do { |synth| synth !? { synth.free } } };
// Stage 9.0: stop old loop FX synths and free their scratch buffers.
~ji_loop_fx_synths !? { |synths| synths.do { |synth| synth !? { synth.free } } };
~ji_loop_fx_synths = nil;
~ji_loop_fx_buf !? { |bufs| bufs.do { |buf|
    if (buf.notNil and: { buf.bufnum.notNil }) { buf.free };
} };
~ji_loop_fx_buf = nil;

// Stage 8.6: stop the beat-grain and count-in schedulers and free the
// session-only memory slot buffers (session state ends with the session).
//  The grain/count-in schedulers are intentionally NOT stopped here: the
//  object returned by TempoClock.schedAbs may be the clock, so calling .stop
//  on it could kill the time base.  Their callbacks carry a generation token
//  and become no-ops once the new arrays are installed.
~ji_loop_grain_sched = nil;
~ji_loop_count_sched = nil;
~ji_loop_mem !? { |slots|
    slots.do { |slot|
        if (slot.notNil and: { slot[\buf].notNil }
            and: { slot[\buf].bufnum.notNil }) { slot[\buf].free };
    };
};

// Stop a Stage 5 routing-test tone if it is still running.
~ji_ableton_routing_routine !? { |routine| routine.stop };
~ji_ableton_routing_synth !? { |synth| synth.free };

// Free buffers from a previous Stage 4 run (safe no-op in a fresh session).
[~ji_buf_loop1, ~ji_buf_loop2, ~ji_buf_loop3, ~ji_buf_loop4].do { |buf|
    if (buf.notNil and: { buf.bufnum.notNil }) { buf.free };
};
~ji_buf_loop1 = nil;
~ji_buf_loop2 = nil;
~ji_buf_loop3 = nil;
~ji_buf_loop4 = nil;
~ji_buf_loop = Array.newClear(4);

// --- Language OSC port -----------------------------------------------------
("[BRIDGE] sclang language OSC port = " ++ NetAddr.langPort).postln;
if (NetAddr.langPort != 57120) {
    ("*** WARNING: sclang is listening on " ++ NetAddr.langPort ++ ", not 57120. Start Python with --sc-port " ++ NetAddr.langPort ++ ".").postln;
};

// --- Initial FrequencySet (same seven classes as Stage 1/3) ----------------
//   1/1, 9/8, 5/4, 4/3, 3/2, 5/3, 15/8 against A3 = 220 Hz.
~ji_ji_set_ratios = [1.0, 9/8, 5/4, 4/3, 3/2, 5/3, 15/8];
~ji_ji_fundamental = 220.0;
~ji_pending_glide = nil;
~ji_current_glide = 0.05;

// --- Fixed-point formatting helper (same approach as Stage 0/1/3) ----------
~ji_fmtNum = { |value, decimals = 2|
    var scale, digits, sign, whole, frac;
    scale  = (10 ** decimals).asInteger;
    digits = (value * scale).round.asInteger.abs;
    sign   = if (value < 0) { "-" } { "" };
    whole  = digits div: scale;
    frac   = (digits % scale).asString;
    while { frac.size < decimals } { frac = "0" ++ frac };
    sign ++ whole.asString ++ "." ++ frac;
};

// --- Nearest JI target (verified Stage 1 arithmetic) -----------------------
//  Fold the input into [fundamental, 2*fundamental), compare against the
//  current classes plus 2/1 (the octave-wrap candidate), then put the input's
//  own octave back.  This is used for live inputs AND loops.  A loop's "own
//  octave" is the octave of the recorded audio.
~ji_nearest_target = { |freq|
    var fundamental = ~ji_ji_fundamental;
    var oct = (freq / fundamental).log2.floor;
    var fOct = freq / (2.0 ** oct);
    var candidates = ~ji_ji_set_ratios ++ [2.0];
    var best = fundamental;
    var bestDist = 1e12;

    candidates.do { |r|
        var pitch = r * fundamental;
        var dist = (pitch - fOct).abs;
        if (dist < bestDist) {
            bestDist = dist;
            best = pitch;
        };
    };
    best * (2.0 ** oct);
};

// --- 12-slot Hz mirror (Stage 3 contract; not read by any SynthDef) --------
~ji_buf_freqset = Buffer.alloc(s, 12, 1);
~ji_write_freqset_buffer = {
    var freqs, padded;
    freqs = ~ji_ji_set_ratios.collect { |ratio| ratio * ~ji_ji_fundamental };
    padded = if (freqs.size < 12) {
        freqs ++ Array.fill(12 - freqs.size, { 0.0 })
    } {
        freqs
    };
    ~ji_buf_freqset !? { |buf| buf.setn(0, padded) };
};
~ji_write_freqset_buffer.value;
("[BRIDGE] Hz mirror allocated: bufnum=% slots=12".format(
    ~ji_buf_freqset.bufnum
)).postln;

// --- Start the two live autotune voices ------------------------------------
//  Two instances of the verified \ji_autotune_ch graph.  No new/divergent
//  autotune DSP is introduced for Stage 4.
~ji_autotune_synth_ch0 = Synth(\ji_autotune_ch, [
    \inBus, 0,
    \outBus, 0,
    \freqBus, ~ji_autotune_freq1.index,
    \hasFreqBus, ~ji_autotune_hasfreq1.index,
    \targetFreq, ~ji_ji_fundamental,
    \retuneTime, 0.05,
    \mix, 1.0,
]);
~ji_autotune_synth_ch1 = Synth(\ji_autotune_ch, [
    \inBus, 1,
    \outBus, 1,
    \freqBus, ~ji_autotune_freq2.index,
    \hasFreqBus, ~ji_autotune_hasfreq2.index,
    \targetFreq, ~ji_ji_fundamental,
    \retuneTime, 0.05,
    \mix, 1.0,
]);
("[AUTOTUNE] ch0 started (node %) inBus=0 outBus=0".format(
    ~ji_autotune_synth_ch0.nodeID
)).postln;
("[AUTOTUNE] ch1 started (node %) inBus=1 outBus=1".format(
    ~ji_autotune_synth_ch1.nodeID
)).postln;

// --- Loop voices and tracker buses -----------------------------------------
~ji_loop_freq_buses = [
    ~ji_loop_freq1, ~ji_loop_freq2, ~ji_loop_freq3, ~ji_loop_freq4
];
~ji_loop_hasfreq_buses = [
    ~ji_loop_hasfreq1, ~ji_loop_hasfreq2, ~ji_loop_hasfreq3, ~ji_loop_hasfreq4
];

//  Live channels use SC 0 and 1.  Loop 1 and loop 3 share SC 2; loop 2 and
//  loop 4 share SC 3.  This gives the four-channel product:
//      SC 0 = autotuned input 0, SC 1 = autotuned input 1,
//      SC 2 = loop 1,            SC 3 = loop 2.
//  The extra loops 3/4 are optional Stage 4 voices and sum into the same loop
//  channels; the product uses loops 1 and 2.
~ji_loop_outbus = [2, 3, 2, 3];
~ji_loop_states = Array.fill(4, { \empty });
~ji_loop_players = Array.newClear(4);
~ji_loop_recorders = Array.newClear(4);

// --- Stage 8.6 per-loop edit state -----------------------------------------
//  The edit event is the sclang source of truth for playback; the control
//  surface validates OSC input and ~ji_loop_edit_args.() turns it into Synth
//  arguments.  countIn/autoPlay are record preferences, not sound.
//  Stage 8.6.1: the grain half is the redesigned (motion/cloud/JI-snap) set.
//  Every grain default below only matters while mode = \grain; normal mode is
//  unaffected by all of them.
~ji_loop_edit = Array.fill(4, {
    (
        reverse: 0, rate: 1.0, gain: 0.7,
        mode: \normal, sync: \free, division: '1_16',
        grainRate: 20.0, grainSize: 0.12, grainPos: 0.5,
        grainScatter: 0.1, grainPitch: 1.0, grainReverse: 0.0,
        grainMotion: \forward, grainSpeed: 0.03, grainDepth: 0.6,
        grainSizeJitter: 0.2, grainPitchJitter: 0.0, grainPitchSnap: 1,
        grainSmear: 0.0, grainFreeze: 0, grainBurst: 0.6,
        grainMorph: 0.0, grainMelt: 0,
        grainFilter: 0.0, grainFilterRate: 0.12,
        grainBreath: 0.0, grainBreathRate: 0.08,
        countIn: \off, autoPlay: 0,
        //  Stage 9.0: post-PitchShift harmonic-series delay FX (session-only,
        //  per loop).  harmTime is FREE seconds; harmTimeSync can lock it to a
        //  fixed musical division (owner follow-up: quarter notes, dotted...).
        harmMode: \off, harmMix: 0.35, harmTime: 0.5,
        harmTimeSync: "free", harmSteps: 4, harmSpacing: 0.08,
        harmFalloff: 0.5, harmAuto: 1
    )
});
//  Stage 8.6.1: the last EFFECTIVE (snapped) grain ratio applied to each
//  loop, so the set-change refresh hook only logs/re-sets a changed ratio.
~ji_loop_grain_effective = Array.fill(4, { 1.0 });
//  Stage 8.6.2: the morph value the voice currently has (the Morph slider is
//  the TARGET; a melt ramp moves this from 0 to the target) and a per-loop
//  generation token that cancels a running ramp.
~ji_loop_morph_current = Array.fill(4, { 0.0 });
~ji_loop_melt_gen = Array.fill(4, { 0 });
//  Beat-grain schedulers, count-in schedulers, dub bookkeeping and the four
//  session-only memory slots (each: (buf, frames, seconds, edit)).
~ji_loop_grain_sched = Array.newClear(4);
~ji_loop_grain_count = Array.fill(4, { 0 });
//  Generation tokens: a pending tempo-clock callback is cancelled by bumping
//  its loop's generation, never by calling .stop on the object returned by
//  schedAbs (which may be the TempoClock itself).
~ji_loop_grain_gen = Array.fill(4, { Main.elapsedTime.asInteger });
~ji_loop_count_sched = Array.newClear(4);
~ji_loop_count_gen = Array.fill(4, { Main.elapsedTime.asInteger });
~ji_loop_count_prev = Array.fill(4, { \empty });
~ji_loop_dub_was_playing = Array.fill(4, { false });
~ji_loop_mem = Array.newClear(4);
//  Stage 9.0 loop FX: one live synth + one trigger counter per loop.  The FX
//  synth reads the loop voice's private send and writes wet to the loop's
//  output bus; ~ji_loop_fx_buf is the per-loop 2 s scratch buffer (allocated
//  with the loop buffers below).
~ji_loop_fx_synths = Array.newClear(4);
~ji_loop_fx_trig = Array.fill(4, { 0 });
~ji_loop_harm_max_seconds = 2.0;

// --- Stage 8.6 ratio / division / count-in tables ---------------------------
//  The only permitted `rate` values, identical to ui/osc_control.py
//  LOOP_RATES.  Parallel arrays avoid dictionary-literal edge cases.
~ji_loop_rates = [2.0, 1.5, 1.6666667, 1.25, 1.3333333, 1.0, 0.75, 0.8, 0.6666667, 0.6, 0.5];
~ji_loop_rate_labels = ["2/1", "3/2", "5/3", "5/4", "4/3", "1/1", "3/4", "4/5", "2/3", "3/5", "1/2"];
~ji_loop_divisions = ['1_16', '1_8', '1_4', '1_2', '1'];
~ji_loop_division_beats = [0.0625, 0.125, 0.25, 0.5, 1.0];
~ji_loop_division_strings = ["1/16", "1/8", "1/4", "1/2", "1"];
~ji_loop_count_in_kinds = [\off, '1_beat', '1_bar', '2_bars'];
~ji_loop_count_in_strings = ["off", "1 beat", "1 bar", "2 bars"];
//  Stage 8.6.1 grain motion names, index = the \ji_loop_voice grainMotion
//  argument (0 off, 1 forward, 2 backward, 3 pingpong, 4 drift).
~ji_loop_grain_motions = [\off, \forward, \backward, \pingpong, \drift];

~ji_loop_rate_index = { |rate|
    ~ji_loop_rates.detectIndex { |allowed| (allowed - rate.asFloat).abs < 0.0005 };
};
~ji_loop_rate_label = { |rate|
    var index = ~ji_loop_rate_index.(rate);
    if (index.isNil) { rate.asString } { ~ji_loop_rate_labels[index] };
};
~ji_loop_division_subdivision = { |symbol|
    var index = ~ji_loop_divisions.indexOf(symbol.asSymbol);
    if (index.isNil) { 0.0625 } { ~ji_loop_division_beats[index] };
};
~ji_loop_division_string = { |symbol|
    var index = ~ji_loop_divisions.indexOf(symbol.asSymbol);
    if (index.isNil) { "1/16" } { ~ji_loop_division_strings[index] };
};
//  NOTE: SuperCollider's Collection:indexOf compares by IDENTITY (===) and a
//  String received over OSC is a fresh object, so `strings.indexOf("1/4")`
//  is always nil.  8.6 used indexOf here, which silently rejected every
//  GUI division/count-in edit (the state stayed '1_16' and the next
//  /ji/loop/edit echo snapped the GUI combo back -- the reported bug).
//  indexOfEqual compares by value (==); symbols are interned, so the
//  symbol lookups above are safe either way (see startup.scd's device check).
~ji_loop_division_symbol = { |string|
    var index = ~ji_loop_division_strings.indexOfEqual(string.asString);
    if (index.isNil) { nil } { ~ji_loop_divisions[index] };
};
~ji_loop_count_in_string = { |symbol|
    var index = ~ji_loop_count_in_kinds.indexOf(symbol.asSymbol);
    if (index.isNil) { "off" } { ~ji_loop_count_in_strings[index] };
};
~ji_loop_count_in_symbol = { |string|
    var index = ~ji_loop_count_in_strings.indexOfEqual(string.asString);
    if (index.isNil) { nil } { ~ji_loop_count_in_kinds[index] };
};
//  Quarter beats of count-in: off 0, 1 beat 1, 1 bar / 2 bars from the meter.
//  Stage 9.0 rounding rule: CEIL to the next whole quarter beat, so odd 1/8
//  meters never start the take early.  4/4 -> 4, 3/4 -> 3, 6/8 -> 3,
//  5/8 (2.5) -> 3, 7/8 (3.5) -> 4.  Mirrored by LoopEdit.count_in_beats.
~ji_loop_count_in_quarter_beats = { |symbol, barBeats = 4.0|
    var kind = symbol.asSymbol;
    var beatsPerBar = barBeats.asFloat.max(1.0).ceil.asInteger;
    if (kind == '1_beat') { 1 } {
        if (kind == '1_bar') { beatsPerBar } {
            if (kind == '2_bars') { beatsPerBar * 2 } { 0 }
        }
    };
};

// --- Stage 9.0 fixed musical delay time ------------------------------------
//  The owner asked for a "fixed (ex quarter notes, dotted, etc) time delay".
//  harmTimeSync = "free" uses harmTime seconds; every other value is a
//  quarter-note count at the current tempo.  Parallel tables keep the wire
//  vocabulary closed and value-compared (indexOfEqual, never indexOf).
//  "1 bar" is a sentinel (-1) resolved from the current meter.
~ji_loop_harm_syncs = [
    "free",
    "1/32", "1/32d", "1/32t",
    "1/16", "1/16d", "1/16t",
    "1/8", "1/8d", "1/8t",
    "1/4", "1/4d", "1/4t",
    "1/2", "1/2d", "1/2t",
    "1/1", "1/1d", "1/1t",
    "1 bar"
];
//  0.125 = 1/32 quarter beat; d = x1.5, t = x2/3.
~ji_loop_harm_sync_beats_table = [
    0.0,
    0.125, 0.1875, 0.0833333,
    0.25, 0.375, 0.1666667,
    0.5, 0.75, 0.3333333,
    1.0, 1.5, 0.6666667,
    2.0, 3.0, 1.3333333,
    4.0, 6.0, 2.6666667,
    -1.0
];
~ji_loop_harm_sync_label = { |sync|
    var text = sync.asString;
    if (~ji_loop_harm_syncs.indexOfEqual(text).isNil) { "free" } { text };
};
~ji_loop_harm_sync_beats = { |sync, barBeats = 4.0|
    var text = sync.asString;
    var index = ~ji_loop_harm_syncs.indexOfEqual(text);
    var beats;
    if (index.isNil) {
        nil
    } {
        beats = ~ji_loop_harm_sync_beats_table[index];
        if (text == "1 bar") { beats = barBeats.asFloat.max(1.0) };
        beats;
    };
};
//  Effective FX time: free seconds or beats * 60 / bpm, clamped to the 2 s
//  buffer.  The tempo/meter mirrors come from ui/control_surface.scd, so they
//  have safe ? defaults when this file is evaluated alone.
~ji_loop_fx_time = { |edit|
    var sync = ~ji_loop_harm_sync_label.(edit[\harmTimeSync]);
    var barBeats = (~ji_rhythm_meter_num ? 4) * 4.0 / (~ji_rhythm_meter_den ? 4);
    var bpm = (~ji_rhythm_bpm ? 120).asFloat.max(20);
    var seconds;
    if (sync == "free") {
        seconds = edit[\harmTime];
    } {
        seconds = ~ji_loop_harm_sync_beats.(sync, barBeats) * 60.0 / bpm;
    };
    seconds.asFloat.clip(0.05, ~ji_loop_harm_max_seconds);
};

// --- Stage 8.6.1 JI-snapped grain pitch ------------------------------------
//  Nearest member of the current set (x octaves, 0.25..4) to the requested
//  ratio.  The material is already tuned by the autotune, so snapping the
//  RATIO to a set ratio is a consonant interval (a fifth, a fourth, ...).
~ji_loop_snap_ratio = { |ratio, ratios|
    var target = ratio.asFloat.clip(0.25, 4.0);
    var best = target, bestDist = 1e9;
    (ratios ? [1.0]).do { |r|
        7.do { |k|          // octaves -3..+3 around 1/1
            var candidate = r * (2 ** (k - 3));
            if (candidate >= 0.25 and: { candidate <= 4.0 }) {
                var dist = (candidate / target).log2.abs;
                if (dist < bestDist) { bestDist = dist; best = candidate };
            };
        };
    };
    best;
};
//  What the voice actually receives as grainPitch: the snapped ratio when
//  Snap is on, otherwise the requested ratio clamped to 0.25..4.0.
~ji_loop_effective_pitch = { |edit|
    if (edit[\grainPitchSnap] == 1) {
        ~ji_loop_snap_ratio.(edit[\grainPitch], ~ji_ji_set_ratios);
    } {
        edit[\grainPitch].asFloat.clip(0.25, 4.0);
    };
};
//  Beat-mode burst decay: about half the current division in seconds.  The
//  SynthDef's Env.perc is scaled by timeScale: grainDecay, a control input.
~ji_loop_grain_decay = { |index|
    var i = (index ? 1).asInteger.clip(1, 4);
    var edit = ~ji_loop_edit[i - 1];
    var bpm = (~ji_rhythm_bpm ? 120).max(20);
    0.5 * ~ji_loop_division_subdivision.(edit[\division]) * 60 / bpm;
};
//  Re-snap every playing grain loop after a set change (called by the
//  /ji/freqset/update receiver, after ~ji_broadcast_glide).  One log line per
//  loop whose effective ratio actually changed; nothing else sends OSC.
~ji_loop_refresh_pitch = { |ratios|
    var set = ratios ? [1.0];
    (1..4).do { |i|
        var edit = ~ji_loop_edit[i - 1];
        var player = ~ji_loop_players[i - 1];
        var effective, last;
        if (edit[\grainPitchSnap] == 1 and: { edit[\mode] == \grain }) {
            effective = ~ji_loop_snap_ratio.(edit[\grainPitch], set);
            last = ~ji_loop_grain_effective[i - 1] ? 1.0;
            if ((effective - last).abs > 0.0005) {
                ~ji_loop_grain_effective[i - 1] = effective;
                if (player.notNil) { player.set(\grainPitch, effective) };
                ("[LOOP %] grain pitch % -> % (snapped to the current set)").format(
                    i, ~ji_fmtNum.(edit[\grainPitch], 3), ~ji_fmtNum.(effective, 4)
                ).postln;
                ~ji_ui_send_loop_edit !? { |send| send.(i) };
            };
        };
    };
};

// --- Stage 8.6.2 dry -> grain "melt" --------------------------------------
//  grainMorph is the TARGET (the Morph slider).  The value the voice actually
//  has is ~ji_loop_morph_current: a ramp moves it at ~30 Hz so a loop can start
//  as the original and progressively turn granular.  Every step is cancelled by
//  a per-loop generation token (never by stopping the clock), so a new melt, a
//  Stop, a Morph change or leaving grain mode can never leave a stale ramp
//  running.  ~ji_loop_melt_seconds is the ramp length (change it in sclang).
~ji_loop_melt_seconds = 4.0;

//  Where a fresh voice starts: 0 when Melt is on (start dry, then ramp),
//  otherwise the Morph slider value.
~ji_loop_start_morph = { |edit|
    if (edit[\grainMelt] == 1 and: { edit[\mode] == \grain }) {
        0.0
    } {
        (edit[\grainMorph] ? 0.0).asFloat.clip(0.0, 1.0)
    };
};

~ji_loop_melt_stop = { |index|
    var i = (index ? 1).asInteger.clip(1, 4);
    ~ji_loop_melt_gen[i - 1] = (~ji_loop_melt_gen[i - 1] ? 0) + 1;
};

~ji_loop_melt_to = { |index, target|
    var i = (index ? 1).asInteger.clip(1, 4);
    var goal = (target ? 0.0).asFloat.clip(0.0, 1.0);
    var from = (~ji_loop_morph_current[i - 1] ? 0.0).asFloat;
    var seconds = ~ji_loop_melt_seconds.max(0.05);
    var generation;

    //  A new ramp always supersedes the old one.
    ~ji_loop_melt_gen[i - 1] = (~ji_loop_melt_gen[i - 1] ? 0) + 1;
    generation = ~ji_loop_melt_gen[i - 1];

    if (~ji_loop_players[i - 1].isNil or: { (goal - from).abs < 0.0005 }) {
        //  Nothing sounding (or already there): remember the value and, if a
        //  voice exists, make sure it is exact.  Play starts from 0 again.
        ~ji_loop_morph_current[i - 1] = goal;
        ~ji_loop_players[i - 1] !? { |player| player.set(\grainMorph, goal) };
    } {
        Routine {
            var started = Main.elapsedTime;
            var running = true;
            while { running } {
                var progress = ((Main.elapsedTime - started) / seconds).clip(0.0, 1.0);
                if (~ji_loop_melt_gen[i - 1] != generation) {
                    running = false;
                } {
                    var value = from + ((goal - from) * progress);
                    ~ji_loop_morph_current[i - 1] = value;
                    ~ji_loop_players[i - 1] !? { |player| player.set(\grainMorph, value) };
                    if (progress >= 1.0) {
                        running = false;
                        ("[LOOP %] melt % -> % over % s").format(
                            i, ~ji_fmtNum.(from, 2),
                            ~ji_fmtNum.(goal, 2), ~ji_fmtNum.(seconds, 1)
                        ).postln;
                        ~ji_ui_send_loop_edit !? { |send| send.(i) };
                    } {
                        (1 / 30).wait;
                    };
                };
            };
        }.play(AppClock);
    };
};

// --- Stage 8.6 Synth argument builder --------------------------------------
//  One place that turns the edit state + buffer + tracker buses into the
//  \ji_loop_voice argument list.  mix stays 1.0: the loop always runs through
//  the accepted JI autotune (targetFreq -> clamp -> Lag -> PitchShift).
//  Stage 8.6.1 passes the EFFECTIVE (JI-snapped) grain pitch as \grainPitch;
//  the requested ratio stays in the edit state for the GUI/status payload.
~ji_loop_edit_args = { |index, targetFreqArg|
    var i = (index ? 1).asInteger.clip(1, 4);
    var edit = ~ji_loop_edit[i - 1];
    var buf = ~ji_buf_loop[i - 1];
    var voice = ~ji_dual_voices[1 + i];
    var sourceVoice = ~ji_dual_voices[(i - 1) % 2];
    var initialTarget = targetFreqArg ? (
        if (voice[\lastFreq] > 20) { voice[\lastTarget] } { sourceVoice[\lastTarget] }
    );
    var motionIndex = ~ji_loop_grain_motions.indexOf(edit[\grainMotion]);
    [
        \bufnum, if (buf.notNil) { buf.bufnum } { 0 },
        \outBus, ~ji_loop_outbus[i - 1],
        \fxBus, ~ji_loop_fx_buses[i - 1].index,
        \freqBus, ~ji_loop_freq_buses[i - 1].index,
        \hasFreqBus, ~ji_loop_hasfreq_buses[i - 1].index,
        \targetFreq, initialTarget,
        \retuneTime, ~ji_current_glide.max(0.005),
        \mix, 1.0,
        \gain, edit[\gain],
        \mode, if (edit[\mode] == \grain) { 1 } { 0 },
        \reverse, edit[\reverse],
        \rate, edit[\rate],
        \grainSync, if (edit[\sync] == \beat) { 1 } { 0 },
        \grainRate, edit[\grainRate],
        \grainSize, edit[\grainSize],
        \grainPos, edit[\grainPos],
        \grainScatter, edit[\grainScatter],
        \grainPitch, ~ji_loop_effective_pitch.(edit),
        \grainReverse, edit[\grainReverse],
        \grainMotion, if (motionIndex.isNil) { 1 } { motionIndex },
        \grainSpeed, edit[\grainSpeed],
        \grainDepth, edit[\grainDepth],
        \grainSizeJitter, edit[\grainSizeJitter],
        \grainPitchJitter, edit[\grainPitchJitter],
        \grainSmear, edit[\grainSmear],
        \grainMorph, ~ji_loop_start_morph.(edit),
        \grainFilter, edit[\grainFilter],
        \grainFilterRate, edit[\grainFilterRate],
        \grainBreath, edit[\grainBreath],
        \grainBreathRate, edit[\grainBreathRate],
        \grainFreeze, edit[\grainFreeze],
        \grainBurst, edit[\grainBurst],
        \grainDecay, ~ji_loop_grain_decay.(i),
        \grainTrig, 0
    ];
};

// --- Stage 8.6 status senders ----------------------------------------------
//  Nil-guarded: test_live_rig.scd is evaluated before ui/control_surface.scd,
//  where ~ji_ui_addr is created.  No status is sent from the install block.
//  Stage 8.6.1: 25 values (the 15 of 8.6 + the ten grain-redesign fields).
//  Stage 8.6.2: 29 values (adds morph, melt, filter and filter rate).
//  Stage 8.7: 31 values (adds the loop grain breath and its rate).
//  Stage 9.0: 39 values (adds harm mode/mix/time/time-sync/steps/spacing/
//  falloff/auto).  grain_pitch stays the REQUESTED ratio; effective_pitch is
//  what the voice actually receives.
~ji_ui_send_loop_edit = { |index|
    var i = index.asInteger.clip(1, 4);
    var edit = ~ji_loop_edit[i - 1];
    ~ji_ui_addr !? { |addr|
        addr.sendMsg('/ji/loop/edit', i,
            edit[\reverse], edit[\rate], edit[\gain],
            if (edit[\mode] == \grain) { 1 } { 0 },
            if (edit[\sync] == \beat) { 1 } { 0 },
            ~ji_loop_division_string.(edit[\division]),
            edit[\grainRate], edit[\grainSize] * 1000.0,
            edit[\grainPos], edit[\grainScatter], edit[\grainPitch],
            edit[\grainSizeJitter], edit[\grainPitchJitter],
            edit[\grainPitchSnap], edit[\grainSmear], edit[\grainFreeze],
            edit[\grainMotion].asString, edit[\grainSpeed],
            edit[\grainDepth], edit[\grainBurst],
            edit[\grainReverse],
            ~ji_loop_count_in_string.(edit[\countIn]),
            edit[\autoPlay],
            ~ji_loop_effective_pitch.(edit),
            edit[\grainMorph], edit[\grainMelt],
            edit[\grainFilter], edit[\grainFilterRate],
            edit[\grainBreath], edit[\grainBreathRate],
            //  Stage 9.0: eight harm_* values, in this exact order.
            edit[\harmMode].asString, edit[\harmMix], edit[\harmTime],
            edit[\harmTimeSync], edit[\harmSteps], edit[\harmSpacing],
            edit[\harmFalloff], edit[\harmAuto]
        );
    };
};
~ji_ui_send_all_loop_edits = { (1..4).do { |index| ~ji_ui_send_loop_edit.(index) } };

~ji_loop_mem_send = { |slot|
    var slotIndex = slot.asInteger.clip(0, 3);
    var mem = ~ji_loop_mem[slotIndex];
    var filled = if (mem.notNil) { 1 } { 0 };
    var frames = if (mem.notNil) { mem[\frames] } { 0 };
    var seconds = if (mem.notNil) { mem[\seconds] } { 0.0 };
    ~ji_ui_addr !? { |addr|
        addr.sendMsg('/ji/loop/mem', slotIndex, filled, frames, seconds);
    };
};
~ji_ui_send_all_loop_mem = { (0..3).do { |slot| ~ji_loop_mem_send.(slot) } };

// --- Stage 9.0 loop FX lifecycle -------------------------------------------
//  One \ji_harmonic_delay instance per playing loop, created AFTER the loop
//  player (`Synth.new(..., player, \addAfter)`) so it reads the same block's
//  private FX send.  The
//  synth writes WET only to the loop's output bus; the dry loop is upstream.
~ji_loop_fx_stop = { |index|
    var i = (index ? 1).asInteger.clip(1, 4);
    var synth;
    //  Self-initialise: recording calls ~ji_stop_loop while the state arrays
    //  are being (re)built, and FX bookkeeping must never abort a take.
    if (~ji_loop_fx_synths.isNil) { ~ji_loop_fx_synths = Array.newClear(4) };
    if (~ji_loop_fx_trig.isNil) { ~ji_loop_fx_trig = Array.fill(4, { 0 }) };
    synth = ~ji_loop_fx_synths[i - 1];
    if (synth.notNil) {
        // Short ASR release, doneAction 2 in the SynthDef frees the node.
        synth.set(\gate, 0);
        ~ji_loop_fx_synths[i - 1] = nil;
    };
    ~ji_loop_fx_trig[i - 1] = 0;
};

~ji_loop_fx_start = { |index|
    var i = (index ? 1).asInteger.clip(1, 4);
    var edit, player, bus, buf, synth;
    if (~ji_loop_fx_synths.isNil) { ~ji_loop_fx_synths = Array.newClear(4) };
    if (~ji_loop_fx_trig.isNil) { ~ji_loop_fx_trig = Array.fill(4, { 0 }) };
    edit = ~ji_loop_edit[i - 1];
    player = ~ji_loop_players[i - 1];
    bus = ~ji_loop_fx_buses[i - 1];
    buf = ~ji_loop_fx_buf !? { |bufs| bufs[i - 1] };
    //  Stale/partial edit Events get the documented defaults, so a missing
    //  key can never turn the mode into a silent no-op.
    if (edit[\harmMode].isNil) { edit[\harmMode] = \off };
    if (edit[\harmMix].isNil) { edit[\harmMix] = 0.35 };
    if (edit[\harmTime].isNil) { edit[\harmTime] = 0.5 };
    if (edit[\harmTimeSync].isNil) { edit[\harmTimeSync] = "free" };
    if (edit[\harmSteps].isNil) { edit[\harmSteps] = 4 };
    if (edit[\harmSpacing].isNil) { edit[\harmSpacing] = 0.08 };
    if (edit[\harmFalloff].isNil) { edit[\harmFalloff] = 0.5 };
    if (edit[\harmAuto].isNil) { edit[\harmAuto] = 1 };
    if (~ji_loop_fx_synths[i - 1].notNil) { ~ji_loop_fx_stop.(i) };
    if (edit[\harmMode] != \off
        and: { player.notNil }
        and: { bus.notNil }
        and: { buf.notNil and: { buf.bufnum.notNil } }) {
        //  Synth.new(..., target, \addAfter) is the explicit form of "create
        //  after the loop player"; the FX must run in the same block as the
        //  player's Out.ar(fxBus).
        synth = Synth.new(\ji_harmonic_delay, [
            \inBus, bus.index,
            \outBus, ~ji_loop_outbus[i - 1],
            \bufnum, buf.bufnum,
            \gate, 1,
            \mode, if (edit[\harmMode] == \delay) { 1 } { 0 },
            \mix, edit[\harmMix],
            \time, ~ji_loop_fx_time.(edit),
            \steps, edit[\harmSteps],
            \spacing, edit[\harmSpacing],
            \falloff, edit[\harmFalloff],
            \auto, edit[\harmAuto],
            \trig, ~ji_loop_fx_trig[i - 1]
        ], player, \addAfter);
        ~ji_loop_fx_synths[i - 1] = synth;
        ("[LOOP %] harm % started (fx node %, buf %, in % -> out %, mix %, time %, steps %, auto %)").format(
            i, edit[\harmMode].asString, synth.nodeID, buf.bufnum,
            bus.index, ~ji_loop_outbus[i - 1],
            ~ji_fmtNum.(edit[\harmMix], 2),
            ~ji_fmtNum.(~ji_loop_fx_time.(edit), 3),
            edit[\harmSteps], edit[\harmAuto]
        ).postln;
    } {
        if (edit[\harmMode] != \off) {
            ("[LOOP %] harm % not started (no player/bus/buffer)").format(
                i, edit[\harmMode].asString
            ).postln;
        };
    };
    synth;
};

~ji_loop_fx_restart = { |index|
    var i = (index ? 1).asInteger.clip(1, 4);
    ~ji_loop_fx_stop !? { |stop| stop.(i) };
    if (~ji_loop_players[i - 1].notNil) { ~ji_loop_fx_start.(i) };
};

~ji_loop_fx_set = { |index, key, value|
    var i = (index ? 1).asInteger.clip(1, 4);
    var synth;
    if (~ji_loop_fx_synths.isNil) { ~ji_loop_fx_synths = Array.newClear(4) };
    synth = ~ji_loop_fx_synths[i - 1];
    synth !? { |node| node.set(key, value) };
};

~ji_loop_fx_fire = { |index|
    var i = (index ? 1).asInteger.clip(1, 4);
    var edit = ~ji_loop_edit[i - 1];
    var synth = ~ji_loop_fx_synths[i - 1];
    if (edit[\harmMode] == \cascade and: { synth.notNil }) {
        ~ji_loop_fx_trig[i - 1] = (~ji_loop_fx_trig[i - 1] ? 0) + 1;
        synth.set(\trig, ~ji_loop_fx_trig[i - 1]);
        ("[LOOP %] harm fire (%)").format(i, ~ji_loop_fx_trig[i - 1]).postln;
    } {
        ("[LOOP %] harm fire ignored (mode %, fx %)").format(
            i, edit[\harmMode].asString, if (synth.notNil) { "on" } { "off" }
        ).postln;
    };
};

//  A tempo change retimes every active fixed-time FX.  Free-time FX are left
//  alone.  Called from the control surface's /ji/rhythm/tempo handler.
~ji_loop_fx_retime = { |index|
    var i = (index ? 1).asInteger.clip(1, 4);
    var edit = ~ji_loop_edit[i - 1];
    if (edit[\harmMode] != \off
        and: { edit[\harmTimeSync].asString != "free" }
        and: { ~ji_loop_players[i - 1].notNil }) {
        ~ji_loop_fx_restart.(i);
        ~ji_ui_send_loop_edit.(i);
    };
};

// --- Loop length -----------------------------------------------------------
//  Default record length in seconds.  `~ji_record_loop.(i)` uses this;
//  `~ji_record_loop.(i, seconds)` overrides it for one take.  Change the
//  default at runtime with `~ji_set_loop_default.(12.0);`.
//  Recording always allocates a fresh buffer of exactly the requested length,
//  so any positive length is allowed; memory is seconds * 48000 * 4 bytes per
//  mono loop (8 s ≈ 1.5 MB, 30 s ≈ 5.8 MB).
~ji_loop_default_seconds = 8.0;

~ji_set_loop_default = { |seconds = 8.0|
    var secs = seconds.asFloat;
    if (secs > 0) {
        ~ji_loop_default_seconds = secs;
        ("[LOOP] default record length = % s. Use ~ji_record_loop.(i) or override: ~ji_record_loop.(i, seconds).").format(
            ~ji_fmtNum.(secs, 2)
        ).postln;
    } {
        "[LOOP] invalid default length -- use seconds > 0".postln;
    };
};

//  One voice table for live channels and loops.  The lookup routine services
//  every active entry with the same arithmetic and the same per-voice
//  lastTarget.  Each entry owns its tracker buses.
~ji_dual_voices = [
    (
        kind: \live, index: 0,
        synth: ~ji_autotune_synth_ch0,
        freqBus: ~ji_autotune_freq1,
        hasFreqBus: ~ji_autotune_hasfreq1,
        lastTarget: ~ji_ji_fundamental,
        lastFreq: 0.0, lastHas: 0.0,
        busy: false, active: true
    ),
    (
        kind: \live, index: 1,
        synth: ~ji_autotune_synth_ch1,
        freqBus: ~ji_autotune_freq2,
        hasFreqBus: ~ji_autotune_hasfreq2,
        lastTarget: ~ji_ji_fundamental,
        lastFreq: 0.0, lastHas: 0.0,
        busy: false, active: true
    )
];
4.do { |i|
    ~ji_dual_voices = ~ji_dual_voices.add((
        kind: \loop, index: i,
        synth: nil,
        freqBus: ~ji_loop_freq_buses[i],
        hasFreqBus: ~ji_loop_hasfreq_buses[i],
        lastTarget: ~ji_ji_fundamental,
        lastFreq: 0.0, lastHas: 0.0,
        busy: false, active: false
    ));
};

// --- Glide broadcast -------------------------------------------------------
//  A shared ~ji_pending_glide consumed by one voice would starve every other
//  voice.  Instead, a set update broadcasts retuneTime to every active voice
//  immediately.  The lookup then changes targetFreq later, and each voice's
//  own Lag.kr uses the event glide.
~ji_broadcast_glide = { |glide|
    var retune = glide.asFloat.max(0.005);
    ~ji_current_glide = retune;
    ~ji_dual_voices.do { |voice|
        if (voice[\active] and: { voice[\synth].notNil }) {
            voice[\synth].set(\retuneTime, retune);
        };
    };
};

// --- Set receiver ----------------------------------------------------------
//  Same Stage 3 payload and validation: fundamental, glide_time, ratio_1 ...
//  ratio_N.  Do not send Python directly to scsynth.
~ji_freqset_oscdef = OSCdef(\ji_freqset_update_live_rig, { |msg|
    var fundamental, glide, ratios, valid;

    if (msg.size < 3) {
        "[BRIDGE] ignore /ji/freqset/update: expected fundamental, glide, and at least one ratio".postln;
    } {
        fundamental = msg[1].asFloat;
        glide = msg[2].asFloat;
        ratios = if (msg.size > 3) { msg.copyRange(3, msg.size - 1) } { [] };

        // 1..12 classes, positive fundamental, non-negative glide, every ratio
        // strictly positive.  Comparisons reject NaN.
        valid = (fundamental > 0) and: {
            (glide >= 0) and: {
                (ratios.size >= 1) and: { ratios.size <= 12 }
            }
        };
        if (valid) {
            valid = ratios.detect { |ratio| (ratio.asFloat > 0).not }.isNil;
        };

        if (valid.not) {
            ("[BRIDGE] ignore /ji/freqset/update: invalid fundamental=% glide=% classes=%").format(
                fundamental, glide, ratios.size
            ).postln;
        } {
            ~ji_ji_fundamental = fundamental;
            ~ji_ji_set_ratios = ratios.collect { |ratio| ratio.asFloat };
            ~ji_pending_glide = glide;
            ~ji_write_freqset_buffer.value;
            ~ji_broadcast_glide.(glide);
            // Stage 8.6.1: the grain pitch is a set ratio, so a new set must
            // re-snap every playing grain loop (no click: the ratio is a
            // control input, the voice keeps running).
            ~ji_loop_refresh_pitch !? { |refresh| refresh.(~ji_ji_set_ratios) };
            ("[BRIDGE] set update: size=% fundamental=% Hz glide=%s buffer=% ratios=%").format(
                ~ji_ji_set_ratios.size,
                ~ji_fmtNum.(~ji_ji_fundamental, 2),
                ~ji_fmtNum.(glide, 3),
                ~ji_buf_freqset.bufnum,
                ~ji_ji_set_ratios
            ).postln;
        };
    };
}, '/ji/freqset/update');
("[BRIDGE] /ji/freqset/update receiver installed (key=\\ji_freqset_update_live_rig), initial set size=% fund=%").format(
    ~ji_ji_set_ratios.size,
    ~ji_fmtNum.(~ji_ji_fundamental, 2)
).postln;

// --- Loop buffer bookkeeping ----------------------------------------------
//  Keep the named globals (~ji_buf_loop1..4) and the index array in sync, so
//  the explicit stop lines at the bottom free the CURRENT buffers.
//  ~ji_buf_loop is 0-based: loop number i (1..4) lives at ~ji_buf_loop[i - 1].
~ji_set_loop_buffer = { |i, buf|
    if (i == 1) { ~ji_buf_loop1 = buf };
    if (i == 2) { ~ji_buf_loop2 = buf };
    if (i == 3) { ~ji_buf_loop3 = buf };
    if (i == 4) { ~ji_buf_loop4 = buf };
    ~ji_buf_loop = [~ji_buf_loop1, ~ji_buf_loop2, ~ji_buf_loop3, ~ji_buf_loop4];
};

// --- Stop one loop's playback (30 ms fade, buffer kept) --------------------
//  Also stops that loop's beat-grain scheduler and resets its trigger counter
//  (the first grain after a restart is always the counter value 1).
~ji_stop_loop = { |index = 1|
    var i = index.asInteger.clip(1, 4);
    var player = ~ji_loop_players[i - 1];
    var voice = ~ji_dual_voices[1 + i];

    if (~ji_loop_grain_sched[i - 1].notNil) {
        ~ji_loop_grain_gen[i - 1] = ~ji_loop_grain_gen[i - 1] + 1;
        ~ji_loop_grain_sched[i - 1] = nil;
    };
    ~ji_loop_grain_count[i - 1] = 0;
    //  Stage 8.6.2: no stale melt steps onto a freed voice.
    ~ji_loop_melt_stop !? { |stop| stop.(i) };
    //  Stage 9.0: the FX synth stops with the loop (no silent-bus reader).
    //  FX bookkeeping is guarded: it must never abort a stop or a new take.
    try {
        ~ji_loop_fx_stop !? { |stop| stop.(i) };
    } { |err|
        ("[LOOP %] FX stop skipped (%): ").format(i, err.errorString).postln;
    };

    if (player.notNil) {
        // EnvGen release, doneAction 2.  Do not test Synth.isPlaying here:
        // it is false unless the node is registered with a NodeWatcher.
        player.set(\gate, 0);
    };
    ~ji_loop_players[i - 1] = nil;
    if (voice.notNil) {
        voice[\active] = false;
        voice[\synth] = nil;
        voice[\busy] = false;
    };
    ~ji_loop_states[i - 1] = \stopped;
};

// --- Stage 8.6: after a take (auto-play) ----------------------------------
//  One shared helper called from the early ~ji_stop_record path AND from the
//  natural-completion timer, so a full take auto-plays like an early one.
//  autoPlay is ignored when the loop is already playing (a dub that keeps
//  playing does not restart).  Beat mode needs a running transport.
~ji_after_record = { |index = 1|
    var i = index.asInteger.clip(1, 4);
    var edit = ~ji_loop_edit[i - 1];
    var buf = ~ji_buf_loop[i - 1];
    var mode = (~ji_loop_modes !? { |modes| modes[i - 1] }) ? \free;
    var running = ~ji_rhythm_running ? false;

    if (edit[\autoPlay] == 1
        and: { ~ji_loop_states[i - 1] != \playing }
        and: { buf.notNil and: { buf.bufnum.notNil } and: { buf.numFrames > 0 } }) {
        if (mode == \beat and: { running.not }) {
            ("[LOOP %] auto-play skipped: transport stopped (beat mode)").format(i).postln;
        } {
            ~ji_play_loop.(i);
            ("[LOOP %] auto-play (% s)").format(i, ~ji_fmtNum.(buf.duration, 2)).postln;
        };
    };
};

// --- Stop one loop's recorder or overdub (buffer kept) --------------------
//  applyAuto = false is used by ~ji_record_loop / ~ji_stop_all so those paths
//  never trigger auto-play.  Stop Rec from the GUI keeps the default.
~ji_stop_record = { |index = 1, applyAuto = true|
    var i = index.asInteger.clip(1, 4);
    var recorder = ~ji_loop_recorders[i - 1];
    var state = ~ji_loop_states[i - 1];
    var wasDubbing = state == \dubbing;
    var wasPlaying = ~ji_loop_dub_was_playing[i - 1];
    var wasRecording = state == \recording;

    if (recorder.notNil) {
        // Always send; a node that has already auto-stopped ignores these.
        recorder.set(\run, 0);
        recorder.free;
        ~ji_loop_recorders[i - 1] = nil;
    };
    if (wasDubbing) {
        ~ji_loop_states[i - 1] = if (wasPlaying) { \playing } { \recorded };
        ~ji_loop_dub_was_playing[i - 1] = false;
        ("[LOOP %] dub stopped").format(i).postln;
        if (applyAuto and: { wasPlaying.not }) { ~ji_after_record.(i) };
    };
    if (wasRecording) {
        ~ji_loop_states[i - 1] = \recorded;
        if (~ji_buf_loop[i - 1].notNil and: { ~ji_buf_loop[i - 1].bufnum.notNil }) {
            ("[LOOP %] recording stopped; buffer kept (% s)").format(
                i, ~ji_fmtNum.(~ji_buf_loop[i - 1].duration, 2)
            ).postln;
        } {
            ("[LOOP %] recording stopped; buffer kept").format(i).postln;
        };
        if (applyAuto) { ~ji_after_record.(i) };
    };
};

// --- Record a raw loop -----------------------------------------------------
//  Reallocates the buffer to exactly `seconds`, starts a fresh recorder at
//  frame 0, and stops any old player after the recorder is running.  The old
//  player keeps its old buffer alive through the 30 ms fade; that buffer is
//  freed shortly afterwards.  Rec still REPLACES (the overdub is ~ji_dub_loop).
~ji_record_loop = { |index = 1, seconds|
    var i = index.asInteger.clip(1, 4);
    var secs = (seconds ? ~ji_loop_default_seconds).asFloat;
    var oldBuf;

    if (s.serverRunning.not) {
        "[LOOP] server not running; cannot record".postln;
    } {
        if (secs <= 0) {
            ("[LOOP %] invalid duration -- use seconds > 0").format(i).postln;
        } {
            // A direct ~ji_record_loop.(i) replaces any pending count-in; the
            // control surface owns its state/logging.
            if (~ji_loop_count_sched[i - 1].notNil) {
                ~ji_loop_count_gen[i - 1] = ~ji_loop_count_gen[i - 1] + 1;
                ~ji_loop_count_sched[i - 1] = nil;
            };
            ~ji_stop_record.(i, false);
            oldBuf = ~ji_buf_loop[i - 1];
            ("[LOOP %] record requested (% s)").format(
                i, ~ji_fmtNum.(secs, 2)
            ).postln;

            Routine {
                var frames, buf, recorder;

                frames = (secs * s.sampleRate).round.asInteger;
                buf = Buffer.alloc(s, frames, 1);
                s.sync;

                ~ji_set_loop_buffer.(i, buf);
                recorder = Synth(\ji_looper_recorder, [
                    \inputChannel, (i - 1) % 2,
                    \bufnum, buf.bufnum,
                    \inputGain, 1.0,
                    \run, 1.0
                ]);
                ~ji_loop_recorders[i - 1] = recorder;
                ("[LOOP %] recorder running (node %, bufnum %, frames %)").format(
                    i, recorder.nodeID, buf.bufnum, frames
                ).postln;

                // The new recorder is already running on the new buffer; stop
                // the previous player (it still reads oldBuf during its fade).
                ~ji_stop_loop.(i);
                ~ji_loop_states[i - 1] = \recording;
                // A fresh recording has no remembered loop pitch yet; the
                // first ~ji_play_loop.() will use the source live target.
                ~ji_dual_voices[1 + i][\lastFreq] = 0.0;
                ~ji_dual_voices[1 + i][\lastHas] = 0.0;

                if (oldBuf.notNil and: { oldBuf.bufnum.notNil }) {
                    Routine {
                        0.2.wait;
                        if (oldBuf.bufnum.notNil) { oldBuf.free };
                    }.play(AppClock);
                };

                ("[LOOP %] recording % s from input % (bufnum=% frames=%)").format(
                    i, ~ji_fmtNum.(secs, 2), (i - 1) % 2, buf.bufnum, frames
                ).postln;
                ("[LOOP %] ~ji_stop_record.(%) stops early; ~ji_play_loop.(%) plays").format(
                    i, i, i
                ).postln;

                // Completion at the EXACT take length.  Auto-play must start
                // here, not after the +0.15 s safety timer: in beat mode a
                // 20-150 ms late start would flam against the grid.
                Routine {
                    secs.wait;
                    if (~ji_loop_recorders[i - 1] === recorder) {
                        if (~ji_loop_states[i - 1] == \recording) {
                            ~ji_loop_states[i - 1] = \recorded;
                            if (~ji_buf_loop[i - 1].notNil
                                and: { ~ji_buf_loop[i - 1].bufnum.notNil }) {
                                ("[LOOP %] recording complete (% s, buffer filled)").format(
                                    i, ~ji_fmtNum.(secs, 2)
                                ).postln;
                            } {
                                ("*** [LOOP %] timer elapsed but the buffer is invalid; re-record").format(i).postln;
                            };
                        };
                        // The recorder Synth is the identity token: a replaced
                        // take can never fire this take's auto-play.
                        ~ji_after_record.(i);
                    };
                }.play(AppClock);

                // +0.15 s safety timer: state/logging only, never a second
                // auto-play (a no-op once auto-play has the loop playing).
                Routine {
                    (secs + 0.15).wait;
                    if (~ji_loop_recorders[i - 1] === recorder) {
                        ~ji_loop_recorders[i - 1] = nil;
                        if (~ji_loop_states[i - 1] == \recording) {
                            ~ji_loop_states[i - 1] = \recorded;
                            ("[LOOP %] recording complete (% s, buffer filled)").format(
                                i, ~ji_fmtNum.(secs, 2)
                            ).postln;
                        };
                    };
                }.play(AppClock);
            }.play(AppClock);
        };
    };
};

// --- Overdub onto an existing loop (Stage 8.6) ----------------------------
//  preLevel: 1.0 keeps the loop, recLevel: 1.0 mixes the input on top,
//  loop: 1.0 so the pass never auto-frees; sclang stops it after exactly one
//  buffer duration (+0.15 s).  The player keeps running.  An empty loop
//  becomes a fresh Rec.  Count-in does not apply to a dub.
~ji_dub_loop = { |index = 1|
    var i = index.asInteger.clip(1, 4);
    var buf = ~ji_buf_loop[i - 1];
    var recorder, wasPlaying;

    if (s.serverRunning.not) {
        "[LOOP] server not running; cannot dub".postln;
    } {
        if (buf.isNil or: { buf.bufnum.isNil } or: { buf.numFrames == 0 }) {
            ("[LOOP %] no buffer -- dub records a fresh take instead").format(i).postln;
            ~ji_record_loop.(i);
        } {
            wasPlaying = ~ji_loop_states[i - 1] == \playing;
            ~ji_stop_record.(i, false);
            ~ji_loop_dub_was_playing[i - 1] = wasPlaying;
            recorder = Synth(\ji_looper_recorder, [
                \inputChannel, (i - 1) % 2,
                \bufnum, buf.bufnum,
                \inputGain, 1.0,
                \recLevel, 1.0,
                \preLevel, 1.0,
                \loop, 1.0,
                \run, 1.0
            ]);
            ~ji_loop_recorders[i - 1] = recorder;
            ~ji_loop_states[i - 1] = \dubbing;
            ("[LOOP %] dub start (buffer % s)").format(
                i, ~ji_fmtNum.(buf.duration, 2)
            ).postln;

            Routine {
                (buf.duration + 0.15).wait;
                if (~ji_loop_recorders[i - 1] === recorder) {
                    if (recorder.notNil) {
                        recorder.set(\run, 0);
                        recorder.free;
                    };
                    ~ji_loop_recorders[i - 1] = nil;
                    ~ji_loop_states[i - 1] = if (~ji_loop_dub_was_playing[i - 1]) {
                        \playing
                    } {
                        \recorded
                    };
                    ~ji_loop_dub_was_playing[i - 1] = false;
                    ("[LOOP %] dub complete (1 pass)").format(i).postln;
                    ~ji_after_record.(i);
                };
            }.play(AppClock);
        };
    };
};

// --- Play a loop through its own Tartini + PitchShift chain ----------------
//  Stage 8.6: the live-rig player is \ji_loop_voice with the full edit state;
//  mix stays 1.0, so the accepted autotune path is unchanged at the defaults.
~ji_play_loop = { |index = 1|
    var i = index.asInteger.clip(1, 4);
    var buf = ~ji_buf_loop[i - 1];
    var voice = ~ji_dual_voices[1 + i];
    var edit = ~ji_loop_edit[i - 1];
    var player;

    if (buf.isNil or: {
        buf.bufnum.isNil or: { buf.numFrames == 0 }
    }) {
        ("[LOOP %] no buffer -- record first with ~ji_record_loop.(%, 4.0)").format(i, i).postln;
    } {
        if (~ji_loop_states[i - 1] == \recording) {
            ("[LOOP %] still recording -- ~ji_stop_record.(%) first").format(i, i).postln;
        } {
            if (~ji_loop_states[i - 1] == \dubbing) {
                ("[LOOP %] dubbing -- ~ji_stop_record.(%) first").format(i, i).postln;
            } {
                ~ji_stop_loop.(i);
                // On a loop's first play there is no remembered loop target yet;
                // ~ji_loop_edit_args uses the source live channel's last target
                // so the first Tartini lock is more likely to be in the
                // recorded octave.  It also re-snaps the grain pitch to the
                // CURRENT set (the effective ratio is what the voice gets).
                player = Synth(\ji_loop_voice, ~ji_loop_edit_args.(i));
                ~ji_loop_players[i - 1] = player;
                voice[\synth] = player;
                voice[\active] = true;
                voice[\busy] = false;
                voice[\lastFreq] = 0.0;
                voice[\lastHas] = 0.0;
                ~ji_loop_grain_count[i - 1] = 0;
                ~ji_loop_grain_effective[i - 1] = ~ji_loop_effective_pitch.(edit);
                //  Stage 8.6.2: a melted loop starts as the original; the
                //  voice was created with this same value (edit_args).
                ~ji_loop_morph_current[i - 1] = ~ji_loop_start_morph.(edit);
                ~ji_loop_states[i - 1] = \playing;

                ("[LOOP %] playing bufnum=% -> out % (node %, % s) rate=% rev=% gain=% mode=% motion=% morph=%").format(
                    i, buf.bufnum, ~ji_loop_outbus[i - 1], player.nodeID,
                    ~ji_fmtNum.(buf.duration, 2),
                    ~ji_fmtNum.(edit[\rate], 3),
                    edit[\reverse],
                    ~ji_fmtNum.(edit[\gain], 2),
                    edit[\mode].asString,
                    edit[\grainMotion].asString,
                    ~ji_fmtNum.(~ji_loop_morph_current[i - 1], 2)
                ).postln;

                // Beat-synced grains are scheduled by ui/control_surface.scd
                // (it owns the tempo clock); nil-guarded no-op without it.
                ~ji_loop_grain_start !? { |start| start.(i) };
                //  Stage 8.6.2: Melt on = begin dry and ramp to the Morph
                //  value (one 30 Hz routine, cancelled by stop/mode/morph).
                if (edit[\mode] == \grain and: { edit[\grainMelt] == 1 }) {
                    ~ji_loop_melt_to.(i, edit[\grainMorph]);
                };
                //  Stage 9.0: create the post-PitchShift FX after the player
                //  (node order) when Harm is not off.  No FX synth when off.
                //  Guarded so an FX problem can never stop playback.
                try {
                    ~ji_loop_fx_start !? { |start| start.(i) };
                } { |err|
                    ("[LOOP %] FX start skipped (%): ").format(i, err.errorString).postln;
                };
            };
        };
    };
};

// --- Stage 8.6: one validated edit ----------------------------------------
//  param is one of reverse, rate, gain, mode, sync, division, grain_rate,
//  grain_size, grain_pos, grain_scatter, grain_pitch, grain_reverse, count_in,
//  auto_play (8.6) or grain_motion, grain_speed, grain_depth,
//  grain_size_jitter, grain_pitch_jitter, grain_pitch_snap, grain_smear,
//  grain_freeze, grain_burst (8.6.1) or grain_morph, grain_melt, grain_filter,
//  grain_filter_rate (8.6.2), grain_breath, grain_breath_rate (8.7) or the
//  eight harm_* Stage 9.0 FX parameters -- 37 in total.  One line for a
//  rejected edit and no change.
~ji_loop_set_edit = { |index, param, value|
    var i = (index ? 1).asInteger.clip(1, 4);
    var edit = ~ji_loop_edit[i - 1];
    var player = ~ji_loop_players[i - 1];
    var key = param.asSymbol;
    var handled = false;
    var rejected = false;
    var number = value.isNumber;
    //  Accept a String or a Symbol: some OSC paths/tools deliver the value as
    //  a symbol, and the harm/mode and division vocabularies are strings.
    var text = if (value.isKindOf(String) or: { value.isKindOf(Symbol) }) {
        value.asString
    } { "" };
    //  8.6.1: pitch changes (and a Snap toggle) re-snap to the current set.
    var pitchChanged = false;
    var effectivePitch;
    //  Stage 9.0: a repeated harm_mode (the GUI re-asserts it before every
    //  FX parameter edit) must NOT restart the FX; only a real mode change
    //  resets the capture/buffer.
    var harmModeChanged = false;

    //  Stage 9.0: fill the harm defaults into a stale/partial edit Event (for
    //  example a rig re-evaluated after an older save).  This keeps a mode or
    //  parameter edit from leaving nil values in the FX Synth args.
    if (edit[\harmMode].isNil) { edit[\harmMode] = \off };
    if (edit[\harmMix].isNil) { edit[\harmMix] = 0.35 };
    if (edit[\harmTime].isNil) { edit[\harmTime] = 0.5 };
    if (edit[\harmTimeSync].isNil) { edit[\harmTimeSync] = "free" };
    if (edit[\harmSteps].isNil) { edit[\harmSteps] = 4 };
    if (edit[\harmSpacing].isNil) { edit[\harmSpacing] = 0.08 };
    if (edit[\harmFalloff].isNil) { edit[\harmFalloff] = 0.5 };
    if (edit[\harmAuto].isNil) { edit[\harmAuto] = 1 };

    if (key == \reverse) {
        if (number) { edit[\reverse] = if (value.asFloat > 0.5) { 1 } { 0 }; handled = true };
    };
    if (key == \rate) {
        var allowed = if (number) { ~ji_loop_rate_index.(value.asFloat) } { nil };
        if (allowed.isNil) {
            ("[LOOP %] edit rate=% rejected (use one of %)").format(
                i, value, ~ji_loop_rate_labels
            ).postln;
            rejected = true;
        } {
            edit[\rate] = ~ji_loop_rates[allowed];
            handled = true;
        };
    };
    if (key == \gain) {
        if (number) { edit[\gain] = value.asFloat.clip(0.0, 1.0); handled = true };
    };
    if (key == \mode) {
        if (number) {
            edit[\mode] = if (value.asFloat > 0.5) { \grain } { \normal };
            handled = true;
        } {
            //  Value comparison: Collection:includes is identity-based in
            //  SuperCollider, so a String from OSC would never match.
            if (["normal", "grain"].indexOfEqual(text.toLower).notNil) {
                edit[\mode] = text.toLower.asSymbol;
                handled = true;
            } { rejected = true };
        };
    };
    if (key == \sync) {
        if (number) {
            edit[\sync] = if (value.asFloat > 0.5) { \beat } { \free };
            handled = true;
        } {
            if (["free", "beat"].indexOfEqual(text.toLower).notNil) {
                edit[\sync] = text.toLower.asSymbol;
                handled = true;
            } { rejected = true };
        };
    };
    if (key == \division) {
        var symbol = ~ji_loop_division_symbol.(text);
        if (symbol.notNil) {
            edit[\division] = symbol;
            handled = true;
        } { rejected = true };
    };
    if (key == \grain_rate) {
        if (number) { edit[\grainRate] = value.asFloat.clip(0.5, 80.0); handled = true };
    };
    if (key == \grain_size) {
        if (number) { edit[\grainSize] = value.asFloat.clip(5.0, 500.0) * 0.001; handled = true };
    };
    if (key == \grain_pos) {
        if (number) { edit[\grainPos] = value.asFloat.clip(0.0, 1.0); handled = true };
    };
    if (key == \grain_scatter) {
        if (number) { edit[\grainScatter] = value.asFloat.clip(0.0, 1.0); handled = true };
    };
    if (key == \grain_pitch) {
        if (number) {
            edit[\grainPitch] = value.asFloat.clip(0.25, 4.0);
            pitchChanged = true;
            handled = true;
        };
    };
    if (key == \grain_reverse) {
        if (number) { edit[\grainReverse] = value.asFloat.clip(0.0, 1.0); handled = true };
    };
    if (key == \count_in) {
        var symbol = ~ji_loop_count_in_symbol.(text);
        if (symbol.notNil) {
            edit[\countIn] = symbol;
            handled = true;
        } { rejected = true };
    };
    if (key == \auto_play) {
        if (number) { edit[\autoPlay] = if (value.asFloat > 0.5) { 1 } { 0 }; handled = true };
    };
    // --- Stage 8.6.1 grain redesign --------------------------------------
    if (key == \grain_motion) {
        var symbol = if (number) { nil } { text.toLower.asSymbol };
        if (symbol.notNil and: { ~ji_loop_grain_motions.includes(symbol) }) {
            edit[\grainMotion] = symbol;
            handled = true;
        } {
            ("[LOOP %] edit grain_motion=% rejected (use off/forward/backward/pingpong/drift)").format(
                i, value
            ).postln;
            rejected = true;
        };
    };
    if (key == \grain_speed) {
        if (number) { edit[\grainSpeed] = value.asFloat.clip(0.001, 0.5); handled = true };
    };
    if (key == \grain_depth) {
        if (number) { edit[\grainDepth] = value.asFloat.clip(0.0, 1.0); handled = true };
    };
    if (key == \grain_size_jitter) {
        if (number) { edit[\grainSizeJitter] = value.asFloat.clip(0.0, 1.0); handled = true };
    };
    if (key == \grain_pitch_jitter) {
        if (number) { edit[\grainPitchJitter] = value.asFloat.clip(0.0, 1.0); handled = true };
    };
    if (key == \grain_pitch_snap) {
        if (number) {
            edit[\grainPitchSnap] = if (value.asFloat > 0.5) { 1 } { 0 };
            pitchChanged = true;
            handled = true;
        };
    };
    if (key == \grain_smear) {
        if (number) { edit[\grainSmear] = value.asFloat.clip(0.0, 1.0); handled = true };
    };
    if (key == \grain_freeze) {
        if (number) { edit[\grainFreeze] = if (value.asFloat > 0.5) { 1 } { 0 }; handled = true };
    };
    if (key == \grain_burst) {
        if (number) { edit[\grainBurst] = value.asFloat.clip(0.0, 1.0); handled = true };
    };
    // --- Stage 8.6.2 morph / melt / moving filter ------------------------
    if (key == \grain_morph) {
        if (number) { edit[\grainMorph] = value.asFloat.clip(0.0, 1.0); handled = true };
    };
    if (key == \grain_melt) {
        if (number) { edit[\grainMelt] = if (value.asFloat > 0.5) { 1 } { 0 }; handled = true };
    };
    if (key == \grain_filter) {
        if (number) { edit[\grainFilter] = value.asFloat.clip(0.0, 1.0); handled = true };
    };
    if (key == \grain_filter_rate) {
        if (number) { edit[\grainFilterRate] = value.asFloat.clip(0.01, 2.0); handled = true };
    };
    // --- Stage 8.7: the loop's own grain breath --------------------------
    if (key == \grain_breath) {
        if (number) { edit[\grainBreath] = value.asFloat.clip(0.0, 1.0); handled = true };
    };
    if (key == \grain_breath_rate) {
        if (number) { edit[\grainBreathRate] = value.asFloat.clip(0.02, 1.0); handled = true };
    };
    // --- Stage 9.0: the harmonic-series delay FX -------------------------
    if (key == \harm_mode) {
        //  Accept the wire strings and, defensively, a 0/1/2 index.  Log the
        //  accepted mode: this is the state that gates the whole FX synth.
        var modeText = if (number) {
            ["off", "cascade", "delay"][value.asFloat.round.asInteger.clip(0, 2)]
        } {
            text.toLower
        };
        if (["off", "cascade", "delay"].indexOfEqual(modeText).notNil) {
            if (edit[\harmMode] != modeText.asSymbol) { harmModeChanged = true };
            edit[\harmMode] = modeText.asSymbol;
            handled = true;
            if (harmModeChanged) {
                ("[LOOP %] harm mode=%").format(i, modeText).postln;
            };
        } { rejected = true };
    };
    if (key == \harm_mix) {
        if (number) { edit[\harmMix] = value.asFloat.clip(0.0, 1.0); handled = true };
    };
    if (key == \harm_time) {
        if (number) {
            edit[\harmTime] = value.asFloat.clip(0.05, ~ji_loop_harm_max_seconds);
            handled = true;
        };
    };
    if (key == \harm_time_sync) {
        if (~ji_loop_harm_syncs.indexOfEqual(text).notNil) {
            edit[\harmTimeSync] = text;
            handled = true;
        } { rejected = true };
    };
    if (key == \harm_steps) {
        if (number) {
            edit[\harmSteps] = value.asFloat.round.asInteger.clip(1, 8);
            handled = true;
        };
    };
    if (key == \harm_spacing) {
        if (number) { edit[\harmSpacing] = value.asFloat.clip(0.0, 1.0); handled = true };
    };
    if (key == \harm_falloff) {
        if (number) { edit[\harmFalloff] = value.asFloat.clip(0.0, 1.0); handled = true };
    };
    if (key == \harm_auto) {
        if (number) { edit[\harmAuto] = if (value.asFloat > 0.5) { 1 } { 0 }; handled = true };
    };

    if (handled) {
        if (pitchChanged) {
            effectivePitch = ~ji_loop_effective_pitch.(edit);
            ~ji_loop_grain_effective[i - 1] = effectivePitch;
        };
        if (player.notNil) {
            if (key == \reverse) { player.set(\reverse, edit[\reverse]) };
            if (key == \rate) { player.set(\rate, edit[\rate]) };
            if (key == \gain) { player.set(\gain, edit[\gain]) };
            if (key == \mode) { player.set(\mode, if (edit[\mode] == \grain) { 1 } { 0 }) };
            if (key == \sync) { player.set(\grainSync, if (edit[\sync] == \beat) { 1 } { 0 }) };
            if (key == \grain_rate) { player.set(\grainRate, edit[\grainRate]) };
            if (key == \grain_size) { player.set(\grainSize, edit[\grainSize]) };
            if (key == \grain_pos) { player.set(\grainPos, edit[\grainPos]) };
            if (key == \grain_scatter) { player.set(\grainScatter, edit[\grainScatter]) };
            //  The voice gets the EFFECTIVE (snapped) ratio, never the raw one.
            if (pitchChanged) { player.set(\grainPitch, effectivePitch) };
            if (key == \grain_reverse) { player.set(\grainReverse, edit[\grainReverse]) };
            if (key == \grain_motion) {
                player.set(\grainMotion, ~ji_loop_grain_motions.indexOf(edit[\grainMotion]))
            };
            if (key == \grain_speed) { player.set(\grainSpeed, edit[\grainSpeed]) };
            if (key == \grain_depth) { player.set(\grainDepth, edit[\grainDepth]) };
            if (key == \grain_size_jitter) {
                player.set(\grainSizeJitter, edit[\grainSizeJitter])
            };
            if (key == \grain_pitch_jitter) {
                player.set(\grainPitchJitter, edit[\grainPitchJitter])
            };
            if (key == \grain_smear) { player.set(\grainSmear, edit[\grainSmear]) };
            if (key == \grain_freeze) { player.set(\grainFreeze, edit[\grainFreeze]) };
            if (key == \grain_burst) { player.set(\grainBurst, edit[\grainBurst]) };
            if (key == \grain_filter) { player.set(\grainFilter, edit[\grainFilter]) };
            if (key == \grain_filter_rate) {
                player.set(\grainFilterRate, edit[\grainFilterRate])
            };
            if (key == \grain_breath) {
                player.set(\grainBreath, edit[\grainBreath])
            };
            if (key == \grain_breath_rate) {
                player.set(\grainBreathRate, edit[\grainBreathRate])
            };
            if (key == \division) { player.set(\grainDecay, ~ji_loop_grain_decay.(i)) };
        };
        //  Stage 8.6.2: Morph is a target and Melt decides how it is reached.
        if (key == \grain_morph) {
            if (edit[\grainMelt] == 1) {
                ~ji_loop_melt_to.(i, edit[\grainMorph]);
            } {
                ~ji_loop_melt_stop.(i);
                ~ji_loop_morph_current[i - 1] = edit[\grainMorph];
                player !? { |p| p.set(\grainMorph, edit[\grainMorph]) };
            };
        };
        if (key == \grain_melt) {
            if (edit[\grainMelt] == 1 and: { edit[\mode] == \grain }) {
                ~ji_loop_melt_to.(i, edit[\grainMorph]);
            } {
                ~ji_loop_melt_to.(i, 0.0);
            };
        };
        //  Entering/leaving grain mode must not jump the blend.
        if (key == \mode) {
            if (edit[\mode] == \grain and: { edit[\grainMelt] == 1 }) {
                ~ji_loop_morph_current[i - 1] = 0.0;
                player !? { |p| p.set(\grainMorph, 0.0) };
                ~ji_loop_melt_to.(i, edit[\grainMorph]);
            } {
                if (edit[\mode] == \grain) {
                    ~ji_loop_melt_stop.(i);
                    ~ji_loop_morph_current[i - 1] = edit[\grainMorph];
                    player !? { |p| p.set(\grainMorph, edit[\grainMorph]) };
                } {
                    ~ji_loop_melt_stop.(i);
                    ~ji_loop_morph_current[i - 1] = 0.0;
                };
            };
        };
        // The beat-grain scheduler follows mode/sync/division changes.
        if (key == \mode or: { key == \sync } or: { key == \division }) {
            ~ji_loop_grain_restart !? { |restart| restart.(i) };
        };
        //  Stage 9.0: mode / time / fixed-time changes restart the FX (the
        //  capture/buffer state is reset); the other parameters set it live.
        if (harmModeChanged or: { key == \harm_time }
            or: { key == \harm_time_sync }) {
            if (edit[\harmMode] == \off) {
                ~ji_loop_fx_stop !? { |stop| stop.(i) };
            } {
                if (player.notNil) {
                    ~ji_loop_fx_restart !? { |restart| restart.(i) };
                };
            };
        } {
            if (key == \harm_mix) {
                ~ji_loop_fx_set !? { |set| set.(i, \mix, edit[\harmMix]) };
            };
            if (key == \harm_steps) {
                ~ji_loop_fx_set !? { |set| set.(i, \steps, edit[\harmSteps]) };
            };
            if (key == \harm_spacing) {
                ~ji_loop_fx_set !? { |set| set.(i, \spacing, edit[\harmSpacing]) };
            };
            if (key == \harm_falloff) {
                ~ji_loop_fx_set !? { |set| set.(i, \falloff, edit[\harmFalloff]) };
            };
            if (key == \harm_auto) {
                ~ji_loop_fx_set !? { |set| set.(i, \auto, edit[\harmAuto]) };
            };
        };
        //  If the FX synth is missing for an active mode (e.g. it failed to
        //  start earlier, or the loop was stopped when the mode was chosen),
        //  a parameter edit recreates it with the current values.
        if (key.asString.beginsWith("harm_") and: { player.notNil }
            and: { ~ji_loop_fx_synths[i - 1].isNil }) {
            ~ji_loop_fx_start !? { |start| start.(i) };
        };
        ~ji_ui_send_loop_edit.(i);
        if (key == \rate) {
            ("[LOOP %] edit rate=% (%)").format(
                i, ~ji_fmtNum.(edit[\rate], 6), ~ji_loop_rate_label.(edit[\rate])
            ).postln;
        } {
            if (key == \mode or: { key == \sync } or: { key == \division }) {
                ("[LOOP %] edit grain mode=% sync=% div=%").format(
                    i,
                    if (edit[\mode] == \grain) { "on" } { "off" },
                    edit[\sync].asString,
                    ~ji_loop_division_string.(edit[\division])
                ).postln;
            } {
                if (key == \count_in or: { key == \auto_play }) {
                    ("[LOOP %] edit count_in=% auto_play=%").format(
                        i, ~ji_loop_count_in_string.(edit[\countIn]), edit[\autoPlay]
                    ).postln;
                } {
                    if (pitchChanged) {
                        ("[LOOP %] edit grain pitch=% -> % (snap %)").format(
                            i,
                            ~ji_fmtNum.(edit[\grainPitch], 3),
                            ~ji_fmtNum.(effectivePitch, 4),
                            edit[\grainPitchSnap]
                        ).postln;
                    } {
                        ("[LOOP %] edit %=%").format(i, key, value).postln;
                    };
                };
            };
        };
    } {
        if (rejected.not) {
            ("[LOOP %] edit ignored: unknown param % value %").format(
                i, param, value
            ).postln;
        } {
            ("[LOOP %] edit %=% rejected (value not allowed)").format(
                i, param, value
            ).postln;
        };
    };
};

// --- Stage 8.6: session-only memory slots (A-D = 0..3) -------------------
//  Each slot holds a COPY of the loop buffer plus the sound edit state.  No
//  file is ever written; the slots vanish on re-evaluation.
~ji_loop_mem_save = { |slot, index|
    var slotIndex = (slot ? 0).asInteger.clip(0, 3);
    var i = (index ? 1).asInteger.clip(1, 4);
    var buf = ~ji_buf_loop[i - 1];
    var state = ~ji_loop_states[i - 1];
    var label = (65 + slotIndex).asAscii;

    if (state == \recording or: { state == \dubbing } or: { state == \counting }) {
        ("[LOOP %] mem % not saved: loop is %").format(i, label, state).postln;
    } {
        if (buf.isNil or: { buf.bufnum.isNil } or: { buf.numFrames == 0 }) {
            ("[LOOP %] mem % not saved: no buffer").format(i, label).postln;
        } {
            var frames = buf.numFrames;
            var seconds = buf.duration;
            var newBuf = Buffer.alloc(s, frames, 1);
            var edit = ~ji_loop_edit[i - 1].copy;

            Synth(\ji_buf_copy, [
                \src, buf.bufnum, \dst, newBuf.bufnum, \numFrames, frames
            ]);
            Routine {
                var old;
                // Wait for the server-side copy (doneAction frees the synth).
                (frames / s.sampleRate + 0.12).wait;
                old = ~ji_loop_mem[slotIndex];
                if (old.notNil and: { old[\buf].notNil }
                    and: { old[\buf].bufnum.notNil }) { old[\buf].free };
                ~ji_loop_mem[slotIndex] = (
                    buf: newBuf, frames: frames, seconds: seconds, edit: edit
                );
                ~ji_loop_mem_send.(slotIndex);
                ("[LOOP %] mem % saved (% s)").format(
                    i, label, ~ji_fmtNum.(seconds, 2)
                ).postln;
            }.play(AppClock);
        };
    };
};

~ji_loop_mem_recall = { |slot, index|
    var slotIndex = (slot ? 0).asInteger.clip(0, 3);
    var i = (index ? 1).asInteger.clip(1, 4);
    var mem = ~ji_loop_mem[slotIndex];
    var label = (65 + slotIndex).asAscii;

    if (mem.isNil) {
        ("[LOOP %] mem % is empty").format(i, label).postln;
    } {
        var frames = mem[\frames];
        var slotBuf = mem[\buf];
        var edit = mem[\edit];

        ~ji_stop_record.(i, false);
        ~ji_stop_loop.(i);
        Routine {
            var oldBuf, newBuf;

            // Let the old player's 30 ms gate fade before freeing its buffer.
            0.12.wait;
            oldBuf = ~ji_buf_loop[i - 1];
            newBuf = Buffer.alloc(s, frames, 1);
            Synth(\ji_buf_copy, [
                \src, slotBuf.bufnum, \dst, newBuf.bufnum, \numFrames, frames
            ]);
            (frames / s.sampleRate + 0.12).wait;

            ~ji_set_loop_buffer.(i, newBuf);
            if (oldBuf.notNil and: { oldBuf.bufnum.notNil }
                and: { oldBuf !== newBuf }) { oldBuf.free };
            ~ji_loop_edit[i - 1] = edit.copy;
            ~ji_loop_melt_stop !? { |stop| stop.(i) };
            ~ji_loop_morph_current[i - 1] = ~ji_loop_start_morph.(~ji_loop_edit[i - 1]);
            ~ji_loop_states[i - 1] = \stopped;
            ~ji_dual_voices[1 + i][\lastFreq] = 0.0;
            ~ji_dual_voices[1 + i][\lastHas] = 0.0;
            ~ji_loop_grain_count[i - 1] = 0;

            ~ji_ui_send_loop_edit.(i);
            ~ji_ui_send_loop_state !? { |send| send.(i) };
            ~ji_ui_send_loop_config !? { |send| send.(i) };
            ("[LOOP %] mem % recall -> loop % (stopped)").format(i, label, i).postln;
        }.play(AppClock);
    };
};

~ji_loop_mem_clear = { |slot|
    var slotIndex = slot.asInteger.clip(0, 3);
    var mem = ~ji_loop_mem[slotIndex];
    var label = (65 + slotIndex).asAscii;

    if (mem.isNil) {
        ("[LOOP] mem % is already empty").format(label).postln;
    } {
        if (mem[\buf].notNil and: { mem[\buf].bufnum.notNil }) { mem[\buf].free };
        ~ji_loop_mem[slotIndex] = nil;
        ~ji_loop_mem_send.(slotIndex);
        ("[LOOP] mem % cleared").format(label).postln;
    };
};

// --- Stop all Stage 4 audio and routines -----------------------------------
//  Buffers, the 12-slot mirror, and ~ji_freqset_oscdef are intentionally left
//  for the explicit closing lines, so this function is safe to call before
//  freeing them.  ~ji_stop_all.() twice is harmless.
~ji_stop_all = {
    ~ji_dual_lookup_routine !? { |routine| routine.stop };
    ~ji_dual_cpu_routine !? { |routine| routine.stop };
    // Stage 8.6: cancel count-ins first, and never auto-play from a stop-all
    // (applyAuto false).  Grain schedulers stop with ~ji_stop_loop.
    (1..4).do { |i|
        if (~ji_loop_count_sched[i - 1].notNil) {
            ~ji_loop_count_gen[i - 1] = ~ji_loop_count_gen[i - 1] + 1;
            ~ji_loop_count_sched[i - 1] = nil;
        };
    };
    (1..4).do { |i| ~ji_stop_record.(i, false) };
    (1..4).do { |i| ~ji_stop_loop.(i) };
    ~ji_autotune_synth_ch0 !? { |synth| synth.free };
    ~ji_autotune_synth_ch1 !? { |synth| synth.free };
    "Live rig audio stopped (live channels, loops, recorders, loop FX, grain and count-in schedulers, lookup and CPU routines).".postln;
    "Buffers, the FX scratch buffers and the memory slots are still allocated; run the closing lines at the bottom of test_live_rig.scd to free them.".postln;
};

// --- Allocate the four loop buffers ---------------------------------------
//  These placeholders use ~ji_loop_default_seconds (8.0 s).  Every recording
//  reallocates its buffer at the exact requested length, so these only need to
//  be valid and immediately usable.
~ji_set_loop_buffer.(1, Buffer.alloc(s, (~ji_loop_default_seconds * s.sampleRate).round.asInteger, 1));
~ji_set_loop_buffer.(2, Buffer.alloc(s, (~ji_loop_default_seconds * s.sampleRate).round.asInteger, 1));
~ji_set_loop_buffer.(3, Buffer.alloc(s, (~ji_loop_default_seconds * s.sampleRate).round.asInteger, 1));
~ji_set_loop_buffer.(4, Buffer.alloc(s, (~ji_loop_default_seconds * s.sampleRate).round.asInteger, 1));
("[LOOP] buffers allocated: 1=% 2=% 3=% 4=% (% s mono each)".format(
    ~ji_buf_loop1.bufnum, ~ji_buf_loop2.bufnum,
    ~ji_buf_loop3.bufnum, ~ji_buf_loop4.bufnum,
    ~ji_fmtNum.(~ji_loop_default_seconds, 2)
)).postln;
("[LOOP] default record length: % s. ~ji_set_loop_default.(12.0) changes it; ~ji_record_loop.(i, seconds) overrides one take.").format(
    ~ji_fmtNum.(~ji_loop_default_seconds, 2)
).postln;
("[LOOP] tracker buses: loop_freq=% loop_hasfreq=% loop_fx=%".format(
    ~ji_loop_freq_buses.collect { |bus| bus.index },
    ~ji_loop_hasfreq_buses.collect { |bus| bus.index },
    ~ji_loop_fx_buses.collect { |bus| bus.index }
)).postln;

// --- Stage 9.0: four 2 s mono FX scratch buffers ---------------------------
//  One per loop, allocated for the session beside the loop buffers.  They are
//  FX scratch, not loop audio and not memory slots; a re-evaluation frees the
//  old ones at the top of this file.
~ji_loop_fx_buf = Array.fill(4, {
    Buffer.alloc(
        s, (~ji_loop_harm_max_seconds * s.sampleRate).round.asInteger, 1
    )
});
("[LOOP] harm FX scratch buffers: 1=% 2=% 3=% 4=% (% s mono each), FX buses %".format(
    ~ji_loop_fx_buf[0].bufnum, ~ji_loop_fx_buf[1].bufnum,
    ~ji_loop_fx_buf[2].bufnum, ~ji_loop_fx_buf[3].bufnum,
    ~ji_fmtNum.(~ji_loop_harm_max_seconds, 1),
    ~ji_loop_fx_buses.collect { |bus| bus.index }
)).postln;

// --- Per-voice read + target update ---------------------------------------
//  Reads one voice's tracker buses, gates on hasFreq/freq, computes the
//  nearest current class in that voice's own octave, and writes targetFreq.
//  The glide was already broadcast by the set receiver.
~ji_read_voice = { |voice|
    voice[\freqBus].get { |freq|
        voice[\hasFreqBus].get { |hasFreq|
            var target;
            if (hasFreq > 0.5 and: { freq > 20 }) {
                target = ~ji_nearest_target.(freq);
                // Small tolerance avoids redundant Synth.set traffic.
                if ((target - voice[\lastTarget]).abs > 0.0001) {
                    if (voice[\synth].notNil) {
                        voice[\synth].set(\targetFreq, target);
                    };
                    voice[\lastTarget] = target;
                };
            };
            voice[\lastFreq] = freq;
            voice[\lastHas] = hasFreq;
            voice[\busy] = false;
        };
    };
};

// --- Multi-voice lookup, 20 Hz ---------------------------------------------
~ji_dual_lookup_routine = Routine {
    loop {
        ~ji_dual_voices.do { |voice|
            if (voice[\active] and: { voice[\busy].not }) {
                voice[\busy] = true;
                ~ji_read_voice.(voice);
            };
        };
        0.05.wait;
    };
};
~ji_dual_lookup_routine.play(AppClock);
("[LOOKUP] multi-voice lookup running at 20 Hz for % voices".format(
    ~ji_dual_voices.size
)).postln;

// --- Status + CPU, once per second ----------------------------------------
~ji_dual_cpu_routine = Routine {
    loop {
        var cpu = s.avgCPU ? 0.0;

        ~ji_dual_voices.do { |voice|
            var shift;

            if (voice[\active]) {
                shift = if (voice[\lastHas] > 0.5 and: { voice[\lastFreq] > 20 }) {
                    12 * (voice[\lastTarget] / voice[\lastFreq]).log2
                } { 0.0 };

                if (voice[\kind] == \live) {
                    ("[AUTOTUNE ch=%] freq=% target=% hasFreq=% shift=%st setSize=% fund=%").format(
                        voice[\index],
                        ~ji_fmtNum.(voice[\lastFreq], 2),
                        ~ji_fmtNum.(voice[\lastTarget], 2),
                        ~ji_fmtNum.(voice[\lastHas], 2),
                        ~ji_fmtNum.(shift, 2),
                        ~ji_ji_set_ratios.size,
                        ~ji_fmtNum.(~ji_ji_fundamental, 2)
                    ).postln;
                } {
                    ("[LOOP %] state=% freq=% target=% hasFreq=% shift=%st").format(
                        voice[\index] + 1,
                        ~ji_loop_states[voice[\index]],
                        ~ji_fmtNum.(voice[\lastFreq], 2),
                        ~ji_fmtNum.(voice[\lastTarget], 2),
                        ~ji_fmtNum.(voice[\lastHas], 2),
                        ~ji_fmtNum.(shift, 2)
                    ).postln;
                };
            } {
                if (voice[\kind] == \loop
                    and: { ~ji_loop_states[voice[\index]] == \recording }) {
                    var loopIndex, buf, bufnum;
                    loopIndex = voice[\index];
                    buf = ~ji_buf_loop[loopIndex];
                    bufnum = if (buf.notNil and: { buf.bufnum.notNil }) { buf.bufnum } { -1 };
                    ("[LOOP %] state=recording bufnum=%").format(
                        loopIndex + 1, bufnum
                    ).postln;
                };
            };
        };

        ("CPU: " ++ ~ji_fmtNum.(cpu, 1) ++ "%").postln;
        ~ji_cpu !? { |bus| bus.set(cpu / 100) };
        1.0.wait;
    };
};
~ji_dual_cpu_routine.play(AppClock);

// --- Command cheat sheet ---------------------------------------------------
"[RIG] Ready. SC 0 = autotuned input 0, SC 1 = autotuned input 1, SC 2 = loop 1, SC 3 = loop 2.".postln;
"[RIG] ~ji_record_loop.(1);  ~ji_stop_record.(1);  ~ji_play_loop.(1);  ~ji_stop_loop.(1);".postln;
"[RIG] ~ji_record_loop.(2);  (wait)  ~ji_stop_record.(2);  ~ji_play_loop.(2);  ~ji_stop_all.();".postln;
"[RIG] Loop 1 records input 0; loop 2 records input 1. Both retune through their own Tartini chain.".postln;
"[RIG] Loop length: default 8.0 s. ~ji_set_loop_default.(12.0); or ~ji_record_loop.(1, 20.0);".postln;
"[RIG] Python: press m to mutate, q to quit. Watch [BRIDGE] set update, [AUTOTUNE] and [LOOP].".postln;
"[RIG] Optional loops 3/4 sum into SC 2/3; the four-channel product uses loops 1 and 2.".postln;
"[RIG] Stage 8.6: ~ji_dub_loop.(1);  ~ji_loop_set_edit.(1, \\reverse, 1);  ~ji_loop_set_edit.(1, \\rate, 0.6666667);".postln;
"[RIG] Stage 8.6: ~ji_loop_set_edit.(1, \\mode, \"grain\");  ~ji_loop_mem_save.(0, 1);  ~ji_loop_mem_recall.(0, 2);".postln;
"[RIG] Stage 8.6.1: ~ji_loop_set_edit.(1, \\grain_motion, \"forward\");  ~ji_loop_set_edit.(1, \\grain_pitch, 1.5);  ~ji_loop_set_edit.(1, \\grain_smear, 0.3);".postln;
"[RIG] Stage 8.6.2: ~ji_loop_set_edit.(1, \\grain_morph, 0.5);  ~ji_loop_set_edit.(1, \\grain_melt, 1);  ~ji_loop_set_edit.(1, \\grain_filter, 0.3);".postln;
"[RIG] Stage 9.0: ~ji_loop_set_edit.(1, \\harm_mode, \"cascade\"); ~ji_loop_fx_fire.(1);  or \"delay\" for the continuous harmonizer.".postln;
"[RIG] Stage 9.0 fixed time: ~ji_loop_set_edit.(1, \\harm_time_sync, \"1/4\");  dotted = \"1/4d\", triplet = \"1/4t\", bar = \"1 bar\".".postln;

// ---------------------------------------------------------------------------
//  TO STOP
//
//  ~ji_stop_all.();
//  ~ji_dual_lookup_routine.stop;
//  ~ji_dual_cpu_routine.stop;
//  ~ji_freqset_oscdef.free;
//  ~ji_buf_loop1.free;
//  ~ji_buf_loop2.free;
//  ~ji_buf_loop3.free;
//  ~ji_buf_loop4.free;
//  ~ji_loop_fx_buf.do { |buf| buf.free };
//  ~ji_buf_freqset.free;
//
//  The first five lines stop/free the audio and control routines; the buffer
//  lines release the loop memory and the 12-slot Hz mirror.  Re-evaluating this
//  file also stops the known old variables and frees old loop buffers.
//
//  Ableton for this rig: four MONO tracks from BlackHole 16ch inputs 1, 2, 3, 4;
//  Monitor In; master to MiniFuse 2/headphones.  See docs/README_live_rig.md.
// ---------------------------------------------------------------------------
