// ===========================================================================
//  JI Station -- Stage 4 -- test_dual_loop.scd
// ---------------------------------------------------------------------------
//  Two independent live autotune channels plus up to four raw loop voices.
//
//      input 0 -> Tartini -> nearest class -> PitchShift -> out 0
//      input 1 -> Tartini -> nearest class -> PitchShift -> out 1
//
//      input 0 (dry) -> RecordBuf -> ~ji_buf_loop1
//      input 1 (dry) -> RecordBuf -> ~ji_buf_loop2
//      input 0 (dry) -> RecordBuf -> ~ji_buf_loop3
//      input 1 (dry) -> RecordBuf -> ~ji_buf_loop4
//
//      PlayBuf(loop N) -> Tartini -> nearest class -> PitchShift -> out
//
//  The loops record PRE-autotune audio.  Playback runs its own Tartini +
//  PitchShift chain, so a mutation retunes every playing loop note-by-note
//  without restarting playback and without transposing the loop as a block.
//
//  PREREQUISITES
//    1. startup.scd (Stage 4) has been evaluated: server booted, buses 0-18
//       allocated, and synthdefs/*.scd loaded.
//    2. Do not run test_bridge.scd at the same time.  This file stops the
//       known Stage 0/1/3/4 routines and synths, and replaces
//       ~ji_freqset_oscdef, so a second set receiver cannot remain active
//       unless it was installed under a different variable.
//
//  START ORDER
//    1. startup.scd
//    2. this file
//    3. Python:
//         cd ji-live
//         uv run --with python-osc python control/ji_bridge.py --interactive --glide-time 0.15
//
//  LOOP COMMANDS
//    ~ji_record_loop.(1, 4.0);   // input 0 -> loop 1
//    ~ji_stop_record.(1);        // stop early (or let the buffer fill)
//    ~ji_play_loop.(1);
//    ~ji_stop_loop.(1);
//    ~ji_record_loop.(2, 4.0);   // input 1 -> loop 2
//    ~ji_play_loop.(2);
//    ~ji_stop_all.();
//
//  STOP (see the bottom of this file):
//    ~ji_stop_all.();
//    ~ji_dual_lookup_routine.stop;
//    ~ji_dual_cpu_routine.stop;
//    ~ji_freqset_oscdef.free;
//    ~ji_buf_loop1.free;  // ... loops 2-4
// ===========================================================================

// --- Preconditions ---------------------------------------------------------
if (s.serverRunning.not) {
    Error("test_dual_loop: the server is not running (or is still booting). Evaluate startup.scd first.").throw;
};

~ji_stage4_buses = [
    ~ji_autotune_freq2, ~ji_autotune_hasfreq2, ~ji_autotune_target2,
    ~ji_loop_freq1, ~ji_loop_freq2, ~ji_loop_freq3, ~ji_loop_freq4,
    ~ji_loop_hasfreq1, ~ji_loop_hasfreq2, ~ji_loop_hasfreq3, ~ji_loop_hasfreq4
];
if (~ji_stage4_buses.any { |bus| bus.isNil }) {
    Error("test_dual_loop: the Stage 4 buses (8-18) are missing. Re-evaluate the Stage 4 startup.scd first.").throw;
};
if (SynthDescLib.global[\ji_autotune_ch].isNil
    or: { SynthDescLib.global[\ji_looper_recorder].isNil
        or: { SynthDescLib.global[\ji_looper_player].isNil } }) {
    Error("test_dual_loop: a required SynthDef is missing. Evaluate startup.scd (it loads synthdefs/*.scd) first.").throw;
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

//  Loops 1/3 monitor on output 0, loops 2/4 on output 1.  Mono loops sum with
//  the corresponding live channel (the Stage 4 test deliberately keeps a
//  2-channel output).
~ji_loop_outbus = [0, 1, 0, 1];
~ji_loop_states = Array.fill(4, { \empty });
~ji_loop_players = Array.newClear(4);
~ji_loop_recorders = Array.newClear(4);

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
~ji_freqset_oscdef = OSCdef(\ji_freqset_update_dual, { |msg|
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
("[BRIDGE] /ji/freqset/update receiver installed (key=\\ji_freqset_update_dual), initial set size=% fund=%").format(
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
~ji_stop_loop = { |index = 1|
    var i = index.asInteger.clip(1, 4);
    var player = ~ji_loop_players[i - 1];
    var voice = ~ji_dual_voices[1 + i];

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

// --- Stop one loop's recorder (buffer kept) --------------------------------
~ji_stop_record = { |index = 1|
    var i = index.asInteger.clip(1, 4);
    var recorder = ~ji_loop_recorders[i - 1];
    var wasRecording = ~ji_loop_states[i - 1] == \recording;

    if (recorder.notNil) {
        // Always send; a node that has already auto-stopped ignores these.
        recorder.set(\run, 0);
        recorder.free;
        ~ji_loop_recorders[i - 1] = nil;
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
    };
};

// --- Record a raw loop -----------------------------------------------------
//  Reallocates the buffer to exactly `seconds`, starts a fresh recorder at
//  frame 0, and stops any old player after the recorder is running.  The old
//  player keeps its old buffer alive through the 30 ms fade; that buffer is
//  freed shortly afterwards.
~ji_record_loop = { |index = 1, seconds = 4.0|
    var i = index.asInteger.clip(1, 4);
    var secs = seconds.asFloat;
    var oldBuf;

    if (s.serverRunning.not) {
        "[LOOP] server not running; cannot record".postln;
    } {
        if (secs <= 0) {
            ("[LOOP %] invalid duration -- use seconds > 0").format(i).postln;
        } {
            ~ji_stop_record.(i);
            oldBuf = ~ji_buf_loop[i - 1];

            Routine {
                var frames, buf, recorder;

                frames = (secs * s.sampleRate).round.asInteger;
                buf = Buffer.alloc(s, frames, 1);
                s.sync;

                ~ji_set_loop_buffer.(i, buf);
                recorder = Synth(\ji_looper_recorder, [
                    \inputChannel, i - 1,
                    \bufnum, buf.bufnum,
                    \inputGain, 1.0,
                    \run, 1.0
                ]);
                ~ji_loop_recorders[i - 1] = recorder;

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
                    i, ~ji_fmtNum.(secs, 2), i - 1, buf.bufnum, frames
                ).postln;
                ("[LOOP %] ~ji_stop_record.(%) stops early; ~ji_play_loop.(%) plays").format(
                    i, i, i
                ).postln;

                // Mark the auto-stop when RecordBuf reaches the end of the
                // buffer (doneAction: 2 frees the synth server-side).  The
                // identity check makes this a no-op after an early stop or a
                // later re-record of the same loop.
                Routine {
                    (secs + 0.15).wait;
                    if (~ji_loop_recorders[i - 1] === recorder) {
                        ~ji_loop_recorders[i - 1] = nil;
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
                    };
                }.play(AppClock);
            }.play(AppClock);
        };
    };
};

// --- Play a loop through its own Tartini + PitchShift chain ----------------
~ji_play_loop = { |index = 1|
    var i = index.asInteger.clip(1, 4);
    var buf = ~ji_buf_loop[i - 1];
    var voice = ~ji_dual_voices[1 + i];
    var sourceVoice = ~ji_dual_voices[(i - 1) % 2];
    var initialTarget;
    var player;

    if (buf.isNil or: {
        buf.bufnum.isNil or: { buf.numFrames == 0 }
    }) {
        ("[LOOP %] no buffer -- record first with ~ji_record_loop.(%, 4.0)").format(i, i).postln;
    } {
        if (~ji_loop_states[i - 1] == \recording) {
            ("[LOOP %] still recording -- ~ji_stop_record.(%) first").format(i, i).postln;
        } {
            ~ji_stop_loop.(i);
            // On a loop's first play there is no remembered loop target yet;
            // use the source live channel's last target so the first Tartini
            // lock is more likely to be in the recorded octave.
            initialTarget = if (voice[\lastFreq] > 20) {
                voice[\lastTarget]
            } {
                sourceVoice[\lastTarget]
            };
            player = Synth(\ji_looper_player, [
                \bufnum, buf.bufnum,
                \outBus, ~ji_loop_outbus[i - 1],
                \freqBus, ~ji_loop_freq_buses[i - 1].index,
                \hasFreqBus, ~ji_loop_hasfreq_buses[i - 1].index,
                \targetFreq, initialTarget,
                \retuneTime, ~ji_current_glide.max(0.005),
                \mix, 1.0,
                \gain, 0.7
            ]);
            ~ji_loop_players[i - 1] = player;
            voice[\synth] = player;
            voice[\active] = true;
            voice[\busy] = false;
            voice[\lastFreq] = 0.0;
            voice[\lastHas] = 0.0;
            ~ji_loop_states[i - 1] = \playing;

            ("[LOOP %] playing bufnum=% -> out % (node %)").format(
                i, buf.bufnum, ~ji_loop_outbus[i - 1], player.nodeID
            ).postln;
        };
    };
};

// --- Stop all Stage 4 audio and routines -----------------------------------
//  Buffers, the 12-slot mirror, and ~ji_freqset_oscdef are intentionally left
//  for the explicit closing lines, so this function is safe to call before
//  freeing them.  ~ji_stop_all.() twice is harmless.
~ji_stop_all = {
    ~ji_dual_lookup_routine !? { |routine| routine.stop };
    ~ji_dual_cpu_routine !? { |routine| routine.stop };
    (1..4).do { |i| ~ji_stop_record.(i) };
    (1..4).do { |i| ~ji_stop_loop.(i) };
    ~ji_autotune_synth_ch0 !? { |synth| synth.free };
    ~ji_autotune_synth_ch1 !? { |synth| synth.free };
    "Stage 4 audio stopped (live channels, loops, recorders, lookup and CPU routines).".postln;
    "The set receiver and loop buffers are still allocated; run the closing lines at the bottom of test_dual_loop.scd to free them.".postln;
};

// --- Allocate the four loop buffers ---------------------------------------
//  Four seconds each by default.  ~ji_record_loop.(i, seconds) reallocates the
//  buffer at the exact requested length, so these are immediately usable
//  placeholders and satisfy the "four loop buffers reserved" contract.
~ji_set_loop_buffer.(1, Buffer.alloc(s, (4.0 * s.sampleRate).round.asInteger, 1));
~ji_set_loop_buffer.(2, Buffer.alloc(s, (4.0 * s.sampleRate).round.asInteger, 1));
~ji_set_loop_buffer.(3, Buffer.alloc(s, (4.0 * s.sampleRate).round.asInteger, 1));
~ji_set_loop_buffer.(4, Buffer.alloc(s, (4.0 * s.sampleRate).round.asInteger, 1));
("[LOOP] buffers allocated: 1=% 2=% 3=% 4=% (4.0 s mono each)".format(
    ~ji_buf_loop1.bufnum, ~ji_buf_loop2.bufnum,
    ~ji_buf_loop3.bufnum, ~ji_buf_loop4.bufnum
)).postln;
("[LOOP] tracker buses: loop_freq=% loop_hasfreq=%".format(
    ~ji_loop_freq_buses.collect { |bus| bus.index },
    ~ji_loop_hasfreq_buses.collect { |bus| bus.index }
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
                    /*("[AUTOTUNE ch=%] freq=% target=% hasFreq=% shift=%st setSize=% fund=%").format(
                        voice[\index],
                        ~ji_fmtNum.(voice[\lastFreq], 2),
                        ~ji_fmtNum.(voice[\lastTarget], 2),
                        ~ji_fmtNum.(voice[\lastHas], 2),
                        ~ji_fmtNum.(shift, 2),
                        ~ji_ji_set_ratios.size,
                        ~ji_fmtNum.(~ji_ji_fundamental, 2)
                    ).postln;*/
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
"[STAGE4] Ready. Live: input 0 -> out 0, input 1 -> out 1. Loops: 1/3 -> out 0, 2/4 -> out 1.".postln;
"[STAGE4] ~ji_record_loop.(1, 4.0);  ~ji_stop_record.(1);  ~ji_play_loop.(1);  ~ji_stop_loop.(1);".postln;
"[STAGE4] ~ji_record_loop.(2, 4.0);  (wait)  ~ji_stop_record.(2);  ~ji_play_loop.(2);  ~ji_stop_all.();".postln;
"[STAGE4] Loop 1 records input 0; loop 2 records input 1. Both play raw audio through their own autotune chain.".postln;

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
//  ~ji_buf_freqset.free;
//
//  The first five lines stop/free the audio and control routines; the buffer
//  lines release the loop memory and the 12-slot Hz mirror.  Re-evaluating this
//  file also stops the known old variables and frees old loop buffers.
// ---------------------------------------------------------------------------
