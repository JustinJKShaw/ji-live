// ===========================================================================
//  JI Station -- Stage 3 -- test_bridge.scd
// ---------------------------------------------------------------------------
//  Receives the complete mutation-engine FrequencySet from Python:
//
//      /ji/freqset/update  fundamental  glide_time  ratio_1 ... ratio_N
//
//  updates ~ji_ji_set_ratios / ~ji_ji_fundamental, writes an Hz mirror into a
//  12-slot Buffer, and keeps the verified Stage 1 nearest-class lookup.  The
//  held input therefore retunes on the next lookup tick when a mutation
//  removes/replaces its current class.
//
//  Prerequisites
//    1. startup.scd has been evaluated: server booted, buses 0-7 allocated,
//       and synthdefs/ji_autotune_ch.scd loaded.
//    2. No other Stage 0/1 autotune test is left running.  This file stops the
//       known Stage 0/1/3 variables before starting, to avoid doubled audio.
//
//  Evaluate this whole file (Cmd+Return), then start Python:
//
//      cd ji-live
//      uv run --with python-osc python control/ji_bridge.py --interactive --glide-time 0.15
//
//  To stop:
//    ~ji_bridge_lookup_routine.stop;
//    ~ji_cpu_routine_stage3.stop;
//    ~ji_autotune_synth.free;
//    ~ji_freqset_oscdef.free;
//    ~ji_buf_freqset.free;
// ===========================================================================

// --- Preconditions ---------------------------------------------------------
if (s.serverRunning.not) {
    Error("test_bridge: the server is not running (or is still booting). Evaluate startup.scd first.").throw;
};
if (~ji_autotune_freq1.isNil or: { ~ji_autotune_hasfreq1.isNil or: { ~ji_autotune_target1.isNil } }) {
    Error("test_bridge: the Stage 1 buses (5-7) are missing. Evaluate startup.scd first.").throw;
};

// --- Stop old Stage 0/1/3 audio so nothing is doubled ----------------------
~ji_tartini_routine !? { |routine| routine.stop };
~ji_cpu_routine !? { |routine| routine.stop };
~ji_tartini_debug_synth !? { |synth| synth.free };

~ji_lookup_routine !? { |routine| routine.stop };
~ji_cpu_routine_stage1 !? { |routine| routine.stop };
~ji_autotune_synth !? { |synth| synth.free };

~ji_bridge_lookup_routine !? { |routine| routine.stop };
~ji_cpu_routine_stage3 !? { |routine| routine.stop };
~ji_freqset_oscdef !? { |def| def.free };

// --- Language OSC port -----------------------------------------------------
//  sclang listens for OSC on NetAddr.langPort (normally 57120).  The Python
//  bridge defaults to 57120; if this printout differs, pass --sc-port.
("[BRIDGE] sclang language OSC port = " ++ NetAddr.langPort).postln;
if (NetAddr.langPort != 57120) {
    ("*** WARNING: sclang is listening on " ++ NetAddr.langPort ++ ", not 57120. Start Python with --sc-port " ++ NetAddr.langPort ++ ".").postln;
};

// --- Initial FrequencySet (same seven classes as Stage 1) ------------------
//   1/1, 9/8, 5/4, 4/3, 3/2, 5/3, 15/8 against A3 = 220 Hz.
~ji_ji_set_ratios = [1.0, 9/8, 5/4, 4/3, 3/2, 5/3, 15/8];
~ji_ji_fundamental = 220.0;
~ji_pending_glide = nil;

// --- Fixed-point formatting helper (same approach as Stage 0/1) ------------
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

// --- 12-slot Hz mirror -----------------------------------------------------
//  Stage 3 writes it for future stages; no SynthDef reads it yet.  The mirror
//  is padded with zeros so a smaller set leaves the remaining slots silent.
~ji_buf_freqset !? { |buf| buf.free };
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

// --- OSC receiver ----------------------------------------------------------
~ji_freqset_oscdef = OSCdef(\ji_freqset_update, { |msg|
    var fundamental, glide, ratios, valid;

    if (msg.size < 3) {
        "[BRIDGE] ignore /ji/freqset/update: expected fundamental, glide, and at least one ratio".postln;
    } {
        fundamental = msg[1].asFloat;
        glide = msg[2].asFloat;
        ratios = if (msg.size > 3) { msg.copyRange(3, msg.size - 1) } { [] };

        // 1..12 classes, positive fundamental, non-negative finite glide,
        // every ratio strictly positive.  Comparisons reject NaN.
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
            ("[BRIDGE] set update: size=% fundamental=% Hz glide=%s buffer=% ratios=%").format(
                ~ji_ji_set_ratios.size,
                ~ji_fmtNum.(~ji_ji_fundamental, 2),
                ~ji_fmtNum.(~ji_pending_glide, 3),
                ~ji_buf_freqset.bufnum,
                ~ji_ji_set_ratios
            ).postln;
        };
    };
}, '/ji/freqset/update');

// --- Nearest JI target (verified Stage 1 lookup) ---------------------------
//  Fold the input into [fundamental, 2*fundamental), compare against the
//  current classes plus 2/1 (the octave-wrap candidate), then put the input's
//  own octave back.  Live input octave is local: MutationEvent.new_octave and
//  OctaveEvent are not used here.
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

// --- Start the autotune synth on input 0 -> output 0 -----------------------
~ji_autotune_synth = Synth(\ji_autotune_ch, [
    \inBus, 0,
    \outBus, 0,
    \freqBus, ~ji_autotune_freq1.index,
    \hasFreqBus, ~ji_autotune_hasfreq1.index,
    \targetFreq, ~ji_ji_fundamental,
    \retuneTime, 0.05,
    \mix, 1.0,
]);
("[BRIDGE] \\ji_autotune_ch started (node " ++ ~ji_autotune_synth.nodeID ++ ")").postln;

// --- Pitch -> JI target lookup, 20 Hz --------------------------------------
//  Reads the Synth's internal Tartini result from buses 5/6.  When the target
//  changes, apply the pending mutation glide to `retuneTime` BEFORE setting
//  `targetFreq`, then clear the pending glide.  A mutation that leaves the
//  held class intact simply updates the set; the target and status keep
//  running without an audible jump.
~ji_bridge_lookup_routine = Routine {
    var busy = false;
    var ticks = 0;
    var lastFreq = 0.0;
    var lastTarget = ~ji_ji_fundamental;
    var lastHas = 0.0;

    loop {
        if (busy.not) {
            busy = true;
            ~ji_autotune_freq1.get { |freq|
                ~ji_autotune_hasfreq1.get { |hasFreq|
                    var target, shift;

                    if (hasFreq > 0.5 and: { freq > 20 }) {
                        target = ~ji_nearest_target.(freq);
                        // Small tolerance avoids redundant Synth.set traffic
                        // from numerically identical targets.
                        if ((target - lastTarget).abs > 0.0001) {
                            var glide = ~ji_pending_glide;
                            if (glide.notNil) {
                                ~ji_autotune_synth.set(
                                    \retuneTime, glide.max(0.005)
                                );
                                ~ji_pending_glide = nil;
                            };
                            ~ji_autotune_synth.set(\targetFreq, target);
                            lastTarget = target;
                        };
                    };

                    lastFreq = freq;
                    lastHas = hasFreq;
                    ticks = ticks + 1;

                    if (ticks % 20 == 0) {
                        shift = if (lastHas > 0.5 and: { lastFreq > 20 }) {
                            12 * (lastTarget / lastFreq).log2
                        } { 0.0 };
                        ("[AUTOTUNE] freq=% target=% hasFreq=% shift=%st setSize=% fund=%").format(
                            ~ji_fmtNum.(lastFreq, 2),
                            ~ji_fmtNum.(lastTarget, 2),
                            ~ji_fmtNum.(lastHas, 2),
                            ~ji_fmtNum.(shift, 2),
                            ~ji_ji_set_ratios.size,
                            ~ji_fmtNum.(~ji_ji_fundamental, 2)
                        ).postln;
                    };
                    busy = false;
                };
            };
        };
        0.05.wait;
    };
};
~ji_bridge_lookup_routine.play(AppClock);

// --- CPU, every 1 s --------------------------------------------------------
~ji_cpu_routine_stage3 = Routine {
    loop {
        var cpu = s.avgCPU ? 0.0;
        ("CPU: " ++ ~ji_fmtNum.(cpu, 1) ++ "%").postln;
        ~ji_cpu !? { |bus| bus.set(cpu / 100) };
        1.0.wait;
    };
};
~ji_cpu_routine_stage3.play(AppClock);

// ---------------------------------------------------------------------------
// To stop:
// ~ji_bridge_lookup_routine.stop;
// ~ji_cpu_routine_stage3.stop;
// ~ji_autotune_synth.free;
// ~ji_freqset_oscdef.free;
// ~ji_buf_freqset.free;
// ---------------------------------------------------------------------------
