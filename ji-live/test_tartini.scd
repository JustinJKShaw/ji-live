// ===========================================================================
//  JI Station -- Stage 0 -- test_tartini.scd
// ---------------------------------------------------------------------------
//  Starts \ji_tartini_debug, then posts:
//      [CH1] / [CH2] pitch estimate every 0.1 s
//      CPU every 1 s
//
//  Evaluate startup.scd first, then evaluate this whole file (Cmd+Return).
//
//  HOW THE SYNTHDEF IS LOADED
//  --------------------------
//  This file uses String:load (thisProcess.interpreter.executeFile), because
//  it runs the SynthDef file in the current interpreter environment, so the
//  ~ji_* bus indices set by startup.scd are visible while the SynthDef is
//  built. It is recompiled on every run, which means it always tracks the
//  current bus allocation. The path is derived from THIS file's location
//  (thisProcess.nowExecutingPath), with ~ji_root (set by startup.scd) as a
//  fallback -- so open the file from disk, don't paste it into a new document.
//
//  The pollers run on AppClock, where 1 beat == 1 second, so ".wait" is real
//  wall-clock time (TempoClock beats would be 0.5 s at the default 120 bpm).
// ===========================================================================

// --- Preconditions ---------------------------------------------------------
if (s.serverRunning.not) {
    Error("test_tartini: the server is not running (or is still booting). Evaluate startup.scd first and wait for the bus confirmation.").throw;
};
if (~ji_tartini_freq1.isNil or: { ~ji_tartini_hasfreq2.isNil }) {
    Error("test_tartini: the ~ji_* control buses are missing. Evaluate startup.scd first.").throw;
};

~ji_root = (thisProcess.nowExecutingPath !? { |p| PathName(p).parentPath }) ?? { ~ji_root };
if (~ji_root.isNil) {
    Error("test_tartini: cannot locate the project folder. Save this file to disk and evaluate it from the file.").throw;
};

// --- Formatting helpers ----------------------------------------------------
// Fixed-point formatting ("%.2f" style) built from integers, so the output
// does not depend on how SC happens to print floats.
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

~ji_post_pitch = { |f1, f2, h1, h2|
    if (h1 < 0.3) {
        "[CH1] no signal       hasFreq=%".format(~ji_fmtNum.(h1, 2)).postln;
    } {
        "[CH1] freq=% Hz  hasFreq=%".format(~ji_fmtNum.(f1, 2), ~ji_fmtNum.(h1, 2)).postln;
    };
    if (h2 < 0.3) {
        "[CH2] no signal       hasFreq=%".format(~ji_fmtNum.(h2, 2)).postln;
    } {
        "[CH2] freq=% Hz  hasFreq=%".format(~ji_fmtNum.(f2, 2), ~ji_fmtNum.(h2, 2)).postln;
    };
};

// --- Load / rebuild the SynthDef ------------------------------------------
~ji_defPath = ~ji_root +/+ "synthdefs" +/+ "ji_tartini_debug.scd";
~ji_tartini_def = nil;                       // the SynthDef file sets this when it builds
("Loading SynthDef: " ++ ~ji_defPath).postln;
~ji_defPath.load;

if (~ji_tartini_def.isNil) {
    "*** Could not build \\ji_tartini_debug -- see the error above.".postln;
    "*** Most likely sc3-plugins (Tartini) is not installed. See README_stage0.md.".postln;
};

// --- Clean slate (safe re-evaluation) --------------------------------------
~ji_tartini_routine !? { |routine| routine.stop };
~ji_cpu_routine     !? { |routine| routine.stop };
~ji_tartini_debug_synth !? { |synth| synth.free };

// --- Start the SynthDef ----------------------------------------------------
// The SynthDef is sent to the server asynchronously; s.sync (which must run
// inside a Routine) makes sure it has arrived before the Synth is created.
Routine {
    s.sync;
    ~ji_tartini_debug_synth = Synth(\ji_tartini_debug);
    ("[test] \\ji_tartini_debug started (node " ++ ~ji_tartini_debug_synth.nodeID ++ ")").postln;
}.play(AppClock);

// --- CH1 / CH2 pitch readout, every 0.1 s ----------------------------------
// Bus.get is asynchronous; the `busy` flag stops a slow reply from queueing up
// more request chains than the server can answer.
~ji_tartini_routine = Routine {
    var busy = false;
    loop {
        if (busy.not) {
            busy = true;
            ~ji_tartini_freq1.get { |f1|
                ~ji_tartini_freq2.get { |f2|
                    ~ji_tartini_hasfreq1.get { |h1|
                        ~ji_tartini_hasfreq2.get { |h2|
                            ~ji_post_pitch.(f1, f2, h1, h2);
                            busy = false;
                        };
                    };
                };
            };
        };
        0.1.wait;
    };
};
~ji_tartini_routine.play(AppClock);

// --- CPU readout, every 1 s ------------------------------------------------
~ji_cpu_routine = Routine {
    loop {
        var cpu = s.avgCPU ? 0.0;
        ("CPU: " ++ ~ji_fmtNum.(cpu, 1) ++ "%").postln;
        ~ji_cpu.set(cpu / 100);          // 0..1 on the reserved ~ji_cpu bus
        1.0.wait;
    };
};
~ji_cpu_routine.play(AppClock);

// ---------------------------------------------------------------------------
// To stop:
// ~ji_tartini_routine.stop;
// ~ji_cpu_routine.stop;
// ~ji_tartini_debug_synth.free;
// ---------------------------------------------------------------------------
