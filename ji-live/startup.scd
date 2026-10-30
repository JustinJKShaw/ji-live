// ===========================================================================
//  JI Station -- Stage 4 -- startup.scd
// ---------------------------------------------------------------------------
//  Boots scsynth with the Stage 0-4 configuration (2 in / 2 out, blockSize 64,
//  48 kHz), allocates the cumulative ~ji_* control buses (slots 0-18), and
//  loads every SynthDef in synthdefs/*.scd:
//
//      0-4    Stage 0   debug Tartini + CPU
//      5-7    Stage 1   autotune tracker / target, channel 0
//      8-10   Stage 4   autotune tracker / target, channel 1
//      11-14  Stage 4   loop Tartini frequencies 1..4
//      15-18  Stage 4   loop Tartini confidences 1..4
//
//  Evaluate this whole file (Cmd+Return) before test_tartini.scd,
//  test_autotune.scd, test_bridge.scd, or test_dual_loop.scd. No synths are
//  started here -- instantiation belongs to the test scripts.
//
//  If you re-evaluate this file while the server is running, the
//  ServerOptions below do NOT change; reboot first (s.reboot).
// ===========================================================================

(
// --- 1. Configuration ------------------------------------------------------
var blockSize  = 64;            // samples per control block: 64 / 48000 = 1.33 ms
var sampleRate = 48000;         // must match the interface (see Audio MIDI Setup)
// Human: set this to one CoreAudio device name; see ServerOptions.devices.
//
// scsynth opens ONE device for both input and output.  It cannot read from an
// interface and write to a different headphones/DAC directly.  For that, create
// an Aggregate Device in Audio MIDI Setup (README_stage4.md "Separate input and
// output devices"), order the output device first so its outputs become SC
// channels 0/1 (the interface's inputs are still SC 0/1 if it has no-input
// devices in front of it), and set deviceName to the aggregate's name.
var deviceName = "REPLACE_ME";

// --- 2. ServerOptions -----------------------------------------------------
s.options.numInputBusChannels  = 2;          // audio input  channels 0, 1
s.options.numOutputBusChannels = 2;          // audio output channels 0, 1
s.options.blockSize            = blockSize;
s.options.sampleRate           = sampleRate;

// Optional (macOS): ask CoreAudio for a hardware buffer matching blockSize.
// Not every interface accepts 64 frames -- if the server fails to boot with
// this uncommented, comment it out again.
// s.options.hardwareBufferSize = blockSize;

if (deviceName == "REPLACE_ME") {
    "*** WARNING: deviceName is still REPLACE_ME -- using the system default device.".postln;
    "***          Set it at the top of startup.scd (see ServerOptions.devices).".postln;
} {
    s.options.device_(deviceName);
    ("Audio device: " ++ deviceName).postln;
};

// --- 3. Project folder (test scripts reuse ~ji_root) -----------------------
~ji_root = thisProcess.nowExecutingPath !? { |p| PathName(p).parentPath };
if (~ji_root.isNil) {
    "*** WARNING: project folder unknown (thisProcess.nowExecutingPath is nil).".postln;
    "***          Save startup.scd to disk and evaluate it from the file.".postln;
};

if (s.serverRunning) {
    "NOTE: server already running -- changed ServerOptions need s.reboot.".postln;
};

// --- 4. Boot --------------------------------------------------------------
s.waitForBoot({
    var idx, stage4Buses, stage4Present;

    // 4a. Allocate the reserved control buses (Stage 0: 0-4, Stage 1: 5-7,
    //     Stage 4: 8-18).
    //     If a bus already exists we RE-USE it instead of freeing and
    //     reallocating: re-use keeps the numbering stable (SynthDefs bake the
    //     indices into their graphs) and keeps the client-side bus allocator
    //     consistent. Consequence: the numbers only change when this runs in a
    //     fresh interpreter, or if something else allocated control buses
    //     first -- the checks in 4b catch that.
    if (~ji_tartini_freq1.isNil) {
        ~ji_tartini_freq1    = Bus.control(s, 1);
        ~ji_tartini_freq2    = Bus.control(s, 1);
        ~ji_tartini_hasfreq1 = Bus.control(s, 1);
        ~ji_tartini_hasfreq2 = Bus.control(s, 1);
        ~ji_cpu              = Bus.control(s, 1);
    } {
        "Stage 0 buses already allocated -- re-using them.".postln;
    };

    if (~ji_autotune_freq1.isNil) {
        ~ji_autotune_freq1    = Bus.control(s, 1);
        ~ji_autotune_hasfreq1 = Bus.control(s, 1);
        ~ji_autotune_target1  = Bus.control(s, 1);
    } {
        "Stage 1 buses already allocated -- re-using them.".postln;
    };

    // Stage 4: buses 8-18.  Allocate the whole block when none of it exists; on
    // re-evaluation re-use the existing block.  A partial block is completed at
    // the end (with a warning) so every ~ji_loop_* variable always exists; the
    // sanity check below then reports any numbering that is not 0-18.
    stage4Buses = [
        ~ji_autotune_freq2, ~ji_autotune_hasfreq2, ~ji_autotune_target2,
        ~ji_loop_freq1, ~ji_loop_freq2, ~ji_loop_freq3, ~ji_loop_freq4,
        ~ji_loop_hasfreq1, ~ji_loop_hasfreq2, ~ji_loop_hasfreq3, ~ji_loop_hasfreq4
    ];
    stage4Present = stage4Buses.select { |bus| bus.notNil }.size;

    if (stage4Present == 0) {
        ~ji_autotune_freq2    = Bus.control(s, 1);
        ~ji_autotune_hasfreq2 = Bus.control(s, 1);
        ~ji_autotune_target2  = Bus.control(s, 1);
        ~ji_loop_freq1        = Bus.control(s, 1);
        ~ji_loop_freq2        = Bus.control(s, 1);
        ~ji_loop_freq3        = Bus.control(s, 1);
        ~ji_loop_freq4        = Bus.control(s, 1);
        ~ji_loop_hasfreq1     = Bus.control(s, 1);
        ~ji_loop_hasfreq2     = Bus.control(s, 1);
        ~ji_loop_hasfreq3     = Bus.control(s, 1);
        ~ji_loop_hasfreq4     = Bus.control(s, 1);
        "Stage 4 buses allocated (8-18).".postln;
    } {
        if (stage4Present != 11) {
            "*** WARNING: only % of the 11 Stage 4 buses exist -- allocating the missing ones at the end.".format(stage4Present).postln;
            "***          Bus numbers will differ from 8-18; the NOTE below reports them.".postln;
            if (~ji_autotune_freq2.isNil)    { ~ji_autotune_freq2    = Bus.control(s, 1) };
            if (~ji_autotune_hasfreq2.isNil) { ~ji_autotune_hasfreq2 = Bus.control(s, 1) };
            if (~ji_autotune_target2.isNil)  { ~ji_autotune_target2  = Bus.control(s, 1) };
            if (~ji_loop_freq1.isNil)        { ~ji_loop_freq1        = Bus.control(s, 1) };
            if (~ji_loop_freq2.isNil)        { ~ji_loop_freq2        = Bus.control(s, 1) };
            if (~ji_loop_freq3.isNil)        { ~ji_loop_freq3        = Bus.control(s, 1) };
            if (~ji_loop_freq4.isNil)        { ~ji_loop_freq4        = Bus.control(s, 1) };
            if (~ji_loop_hasfreq1.isNil)     { ~ji_loop_hasfreq1     = Bus.control(s, 1) };
            if (~ji_loop_hasfreq2.isNil)     { ~ji_loop_hasfreq2     = Bus.control(s, 1) };
            if (~ji_loop_hasfreq3.isNil)     { ~ji_loop_hasfreq3     = Bus.control(s, 1) };
            if (~ji_loop_hasfreq4.isNil)     { ~ji_loop_hasfreq4     = Bus.control(s, 1) };
        } {
            "Stage 4 buses already allocated -- re-using them.".postln;
        };
    };

    "JI system buses allocated: freq1=% freq2=% hasfreq1=% hasfreq2=% cpu=% autotune_freq1=% autotune_hasfreq1=% autotune_target1=% autotune_freq2=% autotune_hasfreq2=% autotune_target2=% loop_freq=% loop_hasfreq=%".format(
        ~ji_tartini_freq1.index, ~ji_tartini_freq2.index,
        ~ji_tartini_hasfreq1.index, ~ji_tartini_hasfreq2.index,
        ~ji_cpu.index,
        ~ji_autotune_freq1.index, ~ji_autotune_hasfreq1.index,
        ~ji_autotune_target1.index,
        ~ji_autotune_freq2.index, ~ji_autotune_hasfreq2.index,
        ~ji_autotune_target2.index,
        [~ji_loop_freq1.index, ~ji_loop_freq2.index,
         ~ji_loop_freq3.index, ~ji_loop_freq4.index],
        [~ji_loop_hasfreq1.index, ~ji_loop_hasfreq2.index,
         ~ji_loop_hasfreq3.index, ~ji_loop_hasfreq4.index]
    ).postln;

    // 4b. Sanity checks: the contract reserves 0-18, all distinct.
    idx = [~ji_tartini_freq1.index, ~ji_tartini_freq2.index,
           ~ji_tartini_hasfreq1.index, ~ji_tartini_hasfreq2.index,
           ~ji_cpu.index,
           ~ji_autotune_freq1.index, ~ji_autotune_hasfreq1.index,
           ~ji_autotune_target1.index,
           ~ji_autotune_freq2.index, ~ji_autotune_hasfreq2.index,
           ~ji_autotune_target2.index,
           ~ji_loop_freq1.index, ~ji_loop_freq2.index,
           ~ji_loop_freq3.index, ~ji_loop_freq4.index,
           ~ji_loop_hasfreq1.index, ~ji_loop_hasfreq2.index,
           ~ji_loop_hasfreq3.index, ~ji_loop_hasfreq4.index];

    if (idx.asSet.size != idx.size) {
        "*** ERROR: two ~ji buses share the same index -- SynthDefs would".postln;
        "***        write into each other's buses.".postln;
        "***        Usual cause: the server was rebooted without restarting".postln;
        "***        sclang. Fix: Language > Restart Interpreter, reboot the".postln;
        "***        server, then evaluate startup.scd again.".postln;
    };

    if (idx.sort != (0..18).asArray) {
        "*** NOTE: the ji buses are not in the reserved slots 0-18 (they are %).".format(idx).postln;
        "***       Everything still works, but the numbers differ from the".postln;
        "***       contract. For a clean allocation: restart the interpreter,".postln;
        "***       reboot the server, then evaluate this file again.".postln;
    };

    // 4c. Load every SynthDef in synthdefs/*.scd. This must run AFTER the
    //     buses are allocated: ji_tartini_debug reads ~ji_tartini_freq1.index
    //     etc. while it is being built, and bakes those indices in.
    //
    //     Path resolution: thisProcess.nowExecutingPath is only valid while
    //     the file is being evaluated, so ~ji_root was captured above (before
    //     waitForBoot) and is reused here. pathMatch() needs a glob: the
    //     pattern must end in "/*.scd", otherwise it matches the directory
    //     itself and .load would try to execute a folder.
    if (~ji_root.notNil) {
        var defDir = ~ji_root +/+ "synthdefs";
        var defFiles = (defDir +/+ "*.scd").pathMatch.sort;
        if (defFiles.isEmpty) {
            ("*** WARNING: no SynthDefs (*.scd) found in " ++ defDir).postln;
        };
        ("Loading % SynthDef file(s) from %".format(defFiles.size, defDir)).postln;
        defFiles.do { |path|
            ("  - " ++ path.basename).postln;
            path.load;
        };
    } {
        "*** WARNING: SynthDefs not loaded (project folder unknown).".postln;
    };

    ("Audio I/O: % in / % out, % Hz, blockSize %".format(
        s.options.numInputBusChannels,
        s.options.numOutputBusChannels,
        s.sampleRate.asInteger,
        s.options.blockSize
    )).postln;
    "Startup complete (Stage 4: 2 in / 2 out, buses 0-18).".postln;
    "Next: test_tartini.scd, test_autotune.scd, test_bridge.scd, or test_dual_loop.scd.".postln;

}, onFailure: {
    "*** scsynth FAILED TO BOOT.".postln;
    "*** Check the device name (ServerOptions.devices) and that the device runs".postln;
    "*** at 48000 Hz with 2 inputs and 2 outputs.".postln;
    false    // also print the standard boot-failure report
});
)
