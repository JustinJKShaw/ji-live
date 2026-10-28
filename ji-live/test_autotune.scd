// ===========================================================================
//  JI Station -- Stage 1 -- test_autotune.scd
// ---------------------------------------------------------------------------
//  Prerequisites
//    1. The Stage 1 startup.scd has been evaluated: server booted, buses 0-7
//       allocated, and synthdefs/ loaded (including ji_autotune_ch).
//    2. The Stage 0 test is stopped, otherwise its dry passthrough is summed
//       with this stage's output:
//         ~ji_tartini_routine.stop;
//         ~ji_cpu_routine.stop;
//         ~ji_tartini_debug_synth.free;
//
//  Evaluate this whole file (Cmd+Return), then play into input channel 0.
//
//  To stop:
//    ~ji_lookup_routine.stop;
//    ~ji_cpu_routine_stage1.stop;
//    ~ji_autotune_synth.free;
// ===========================================================================

// --- Preconditions ---------------------------------------------------------
if (s.serverRunning.not) {
    Error("test_autotune: the server is not running (or is still booting). Evaluate startup.scd first.").throw;
};
if (~ji_autotune_freq1.isNil or: { ~ji_autotune_target1.isNil }) {
    Error("test_autotune: the Stage 1 buses (5-7) are missing. Evaluate the Stage 1 startup.scd first.").throw;
};

// --- Formatting helper (same fixed-point approach as Stage 0) ---------------
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

// --- The JI set ------------------------------------------------------------
// 7 ratios against A3 = 220 Hz:
//   ratio      x 220.0      note      (deviation from 12-TET)
//   1/1        220.00       A
//   9/8        247.50       B         (+3.9 cents)
//   5/4        275.00       C#        (-13.7 cents)
//   4/3        293.33       D         (-2.0 cents)
//   3/2        330.00       E         (+2.0 cents)
//   5/3        366.67       F#        (-15.6 cents)
//   15/8       412.50       G#        (-11.7 cents)
~ji_ji_set_ratios = [1.0, 9/8, 5/4, 4/3, 3/2, 5/3, 15/8];
~ji_ji_fundamental = 220.0;

// --- Nearest JI target -----------------------------------------------------
// Behaviour: fold the pitch into [fundamental, 2*fundamental) (this is what
// `oct` does), pick the closest JI ratio *in Hz*, then put the octave back.
//   220.0 -> 220.00  (1/1)        269.0 -> 275.00  (5/4)
//   219.0 -> 220.00  (1/1)        415.3 -> 412.50  (15/8)
//   233.0 -> 220.00  (1/1)        438.0 -> 440.00  (1/1, next octave)
//   110.0 -> 110.00  (1/1)
//
// The comparison list includes 2/1, the octave above 1/1. Without it the fold
// mis-tunes notes a little flat of the fundamental: 219 Hz folds to 438 Hz,
// whose nearest member is 15/8 (412.5 Hz) -- a semitone down -- instead of
// 220 Hz. With 2/1 in the list the folded comparison is circular, so 438 snaps
// up to 440 and lands back on 220. (Nothing else changes: 2/1 can only win
// near the top of the folded octave, where it is the correct answer.)
// Unvoiced input must be filtered out by the caller (hasFreq / freq > 20).
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

// --- Start the autotune synth on channel 0 ---------------------------------
~ji_lookup_routine !? { |routine| routine.stop };
~ji_cpu_routine_stage1 !? { |routine| routine.stop };
~ji_autotune_synth !? { |synth| synth.free };

~ji_autotune_synth = Synth(\ji_autotune_ch, [
    \inBus, 0,
    \outBus, 0,
    \freqBus, ~ji_autotune_freq1.index,
    \hasFreqBus, ~ji_autotune_hasfreq1.index,
    \targetFreq, ~ji_ji_fundamental,
    \retuneTime, 0.05,
    \mix, 1.0,
]);
("[test] \\ji_autotune_ch started (node " ++ ~ji_autotune_synth.nodeID ++ ")").postln;

// --- Pitch -> JI target lookup, 20 Hz --------------------------------------
// Reads the Synth's internal Tartini result from buses 5/6, picks the nearest
// JI member and pushes it into the Synth's `targetFreq`. If the input is not
// confident (hasFreq <= 0.5) the previous target is held, so the shifter does
// not jump around during note releases. One status line per second (20 ticks).
~ji_lookup_routine = Routine {
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
                    if (hasFreq > 0.5 and: { freq > 20 }) {
                        var tgt = ~ji_nearest_target.(freq);
                        ~ji_autotune_synth.set(\targetFreq, tgt);
                        lastTarget = tgt;
                    };
                    lastFreq = freq;
                    lastHas = hasFreq;
                    ticks = ticks + 1;

                    if (ticks % 20 == 0) {
                        var shift = if (lastHas > 0.5 and: { lastFreq > 20 }) {
                            12 * (lastTarget / lastFreq).log2
                        } { 0.0 };
                        "[AUTOTUNE] freq=%  target=%  hasFreq=%  shift=%st".format(
                            ~ji_fmtNum.(lastFreq, 2),
                            ~ji_fmtNum.(lastTarget, 2),
                            ~ji_fmtNum.(lastHas, 2),
                            ~ji_fmtNum.(shift, 2)
                        ).postln;
                    };
                    busy = false;
                };
            };
        };
        0.05.wait;
    };
};
~ji_lookup_routine.play(AppClock);

// --- CPU, every 1 s --------------------------------------------------------
~ji_cpu_routine_stage1 = Routine {
    loop {
        var cpu = s.avgCPU ? 0.0;
        ("CPU: " ++ ~ji_fmtNum.(cpu, 1) ++ "%").postln;
        ~ji_cpu !? { |bus| bus.set(cpu / 100) };    // 0..1 on the reserved bus
        1.0.wait;
    };
};
~ji_cpu_routine_stage1.play(AppClock);

// ---------------------------------------------------------------------------
// To stop:
// ~ji_lookup_routine.stop;
// ~ji_cpu_routine_stage1.stop;
// ~ji_autotune_synth.free;
// ---------------------------------------------------------------------------
