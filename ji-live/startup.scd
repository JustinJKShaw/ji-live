// ===========================================================================
//  JI Station -- Stage 5 -- startup.scd
// ---------------------------------------------------------------------------
//  Boots scsynth with the Stage 5 device configuration (2 in / 16 out,
//  blockSize 64, 48 kHz):
//
//      input : MiniFuse 2     (2 channels)
//      output: BlackHole 16ch (16 channels = 8 stereo pairs)
//
//  allocates the cumulative ~ji_* control buses (slots 0-18), and loads every
//  SynthDef in synthdefs/*.scd:
//
//      0-4    Stage 0   debug Tartini + CPU
//      5-7    Stage 1   autotune tracker / target, channel 0
//      8-10   Stage 4   autotune tracker / target, channel 1
//      11-14  Stage 4   loop Tartini frequencies 1..4
//      15-18  Stage 4   loop Tartini confidences 1..4
//
//  Control buses stay 0-18.  Audio output buses 0-15 are now valid.  Stage 4
//  live/loop audio deliberately stays on pair 0/1 (Ableton track 1); the rest
//  of the 16-channel map is reserved (see config/ableton_routing.json).
//
//  Evaluate this whole file (Cmd+Return) before test_ableton_routing.scd,
//  test_tartini.scd, test_autotune.scd, test_bridge.scd, or
//  test_dual_loop.scd. No synths are started here -- instantiation belongs to
//  the test scripts.
//
//  If you re-evaluate this file while the server is running, the
//  ServerOptions below do NOT change; reboot first (s.reboot).
// ===========================================================================

(
// --- 1. Configuration ------------------------------------------------------
var blockSize        = 64;     // samples per control block: 64 / 48000 = 1.33 ms
var targetSampleRate = 48000;  // set BOTH devices to this in Audio MIDI Setup

// Stage 5 primary path: separate CoreAudio devices.
// Verify the exact names on this machine with:
//     ServerOptions.inDevices.postln;
//     ServerOptions.outDevices.postln;
var inDeviceName  = "MiniFuse 2";       // 2 inputs
var outDeviceName = "BlackHole 16ch";   // 16 outputs = 8 stereo pairs

// Fallback for a single shared device (e.g. an Aggregate Device, or a
// macOS/SC combination that cannot open separate devices).  Leave REPLACE_ME
// for the primary path; see README_stage5.md for the Aggregate fallback.
var singleDeviceName = "REPLACE_ME";

var useSeparate, knownIns, knownOuts;

// --- 2. ServerOptions -----------------------------------------------------
s.options.numInputBusChannels  = 2;          // MiniFuse 2 audio inputs 0, 1
s.options.numOutputBusChannels = 16;         // BlackHole 16ch: pairs 0/1 ... 14/15
s.options.blockSize            = blockSize;

// IMPORTANT: do NOT force s.options.sampleRate = 48000 here.
// With BlackHole as a separate output device, a forced rate can make scsynth
// exit immediately after "server ready" with exit code 0 on macOS.
// 0 is SC's default: use the Audio MIDI Setup rate of the devices.  Setting it
// explicitly also clears an old 48000 left by a previously evaluated startup.scd.
s.options.sampleRate = 0;

// Set MiniFuse 2 AND BlackHole 16ch to 48000 Hz in Audio MIDI Setup instead;
// the server then uses the devices' rate.  The post-boot check below verifies
// that the running server actually reached 48000 Hz.
// s.options.sampleRate = targetSampleRate;   // do not use with BlackHole

// Optional (macOS): ask CoreAudio for a hardware buffer matching blockSize.
// Not every interface accepts 64 frames -- if the server fails to boot with
// this uncommented, comment it out again.  BlackHole does not expose this
// control, so leave it commented for the BlackHole path.
// s.options.hardwareBufferSize = blockSize;

~ji_target_sample_rate = targetSampleRate;

// --- 3. Devices -----------------------------------------------------------
// scsynth CAN use separate input and output devices on macOS.  When inDevice
// and outDevice differ, scsynth is launched with "-H <in> <out>".
// device_("...") sets BOTH directions to one device and is only used by the
// single-device fallback below.
useSeparate = (inDeviceName != "REPLACE_ME") and: { outDeviceName != "REPLACE_ME" };

knownIns  = try { ServerOptions.inDevices }  { |err| nil };
knownOuts = try { ServerOptions.outDevices } { |err| nil };

("Available input devices:  %").format(knownIns).postln;
("Available output devices: %").format(knownOuts).postln;

// Warn, list the available names, and never substitute another physical device.
// NOTE: SC's Collection:includes compares by identity (===), so a String built
// in this file can fail to match an equal String returned by ServerOptions.
// Compare by value instead (asString + ==).
~ji_check_device_name = { |label, listLabel, name, list|
    var names;
    if (list.isNil or: { list.isEmpty }) {
        ("*** NOTE: cannot verify the % device '%' -- ServerOptions.% is empty.".format(label, name, listLabel)).postln;
    } {
        names = list.collect { |item| item.asString };
        if (names.any { |item| item == name.asString }) {
            ("[DEVICE] % found: %".format(label, name)).postln;
        } {
            ("*** WARNING: % device not found: '%'.".format(label, name)).postln;
            ("***          ServerOptions.% = %".format(listLabel, list)).postln;
            "***          Use an exact name from that list (or fix the device/driver).".postln;
            "***          Not falling back to a different physical device.".postln;
        };
    };
};

if (useSeparate) {
    s.options.inDevice_(inDeviceName);
    s.options.outDevice_(outDeviceName);
    ~ji_in_device   = inDeviceName;
    ~ji_out_device  = outDeviceName;
    ~ji_device_mode = \separate;
    ("Audio devices: in=% out=% (separate)".format(inDeviceName, outDeviceName)).postln;
    ~ji_check_device_name.("input",  "inDevices",  inDeviceName,  knownIns);
    ~ji_check_device_name.("output", "outDevices", outDeviceName, knownOuts);
} {
    if (singleDeviceName != "REPLACE_ME") {
        s.options.device_(singleDeviceName);
        ~ji_in_device   = singleDeviceName;
        ~ji_out_device  = singleDeviceName;
        ~ji_device_mode = \single;
        "*** WARNING: separate in/out names are incomplete (REPLACE_ME).".postln;
        ("***          Using the single-device fallback '%' for both directions.".format(singleDeviceName)).postln;
        "***          Ensure that device provides 2 inputs and 16 outputs (e.g. an Aggregate).".postln;
        ("Audio device: " ++ singleDeviceName ++ " (single-device fallback)").postln;
    } {
        ~ji_in_device   = "system default";
        ~ji_out_device  = "system default";
        ~ji_device_mode = \default;
        "*** WARNING: device names are still REPLACE_ME -- using the system default device(s).".postln;
        "***          Stage 5 requires 2 in / 16 out: set inDeviceName/outDeviceName".postln;
        "***          at the top of startup.scd from ServerOptions.inDevices/outDevices.".postln;
        "***          The system default device may not provide 16 outputs.".postln;
    };
};

// --- 4. Project folder (test scripts reuse ~ji_root) -----------------------
~ji_root = thisProcess.nowExecutingPath !? { |p| PathName(p).parentPath };
if (~ji_root.isNil) {
    "*** WARNING: project folder unknown (thisProcess.nowExecutingPath is nil).".postln;
    "***          Save startup.scd to disk and evaluate it from the file.".postln;
};

if (s.serverRunning) {
    "NOTE: server already running -- changed ServerOptions need s.reboot.".postln;
};

// --- 5. Boot --------------------------------------------------------------
s.waitForBoot({
    var idx, stage4Buses, stage4Present;

    // 5a. Allocate the reserved control buses (Stage 0: 0-4, Stage 1: 5-7,
    //     Stage 4: 8-18).
    //     If a bus already exists we RE-USE it instead of freeing and
    //     reallocating: re-use keeps the numbering stable (SynthDefs bake the
    //     indices into their graphs) and keeps the client-side bus allocator
    //     consistent. Consequence: the numbers only change when this runs in a
    //     fresh interpreter, or if something else allocated control buses
    //     first -- the checks in 5b catch that.
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

    // 5b. Sanity checks: the contract reserves 0-18, all distinct.
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

    // 5c. Load every SynthDef in synthdefs/*.scd. This must run AFTER the
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

    if (s.sampleRate.asInteger != ~ji_target_sample_rate) {
        ("*** WARNING: server sample rate is % Hz (expected %).".format(
            s.sampleRate.asInteger, ~ji_target_sample_rate
        )).postln;
        ("***          Set BOTH MiniFuse 2 and BlackHole 16ch to % Hz in Audio MIDI Setup,".format(
            ~ji_target_sample_rate
        )).postln;
        "***          then quit and re-evaluate startup.scd.".postln;
    };

    ("inDevice=% outDevice=%".format(
        ~ji_in_device, ~ji_out_device
    )).postln;
    ("Audio I/O: % in / % out, % Hz, blockSize %".format(
        s.options.numInputBusChannels,
        s.options.numOutputBusChannels,
        s.sampleRate.asInteger,
        s.options.blockSize
    )).postln;
    ("Server device setting: %".format(s.options.device)).postln;
    "Startup complete (Stage 5: 2 in / 16 out, buses 0-18).".postln;
    "Next: test_ableton_routing.scd (Stage 5), then test_dual_loop.scd (Stage 4).".postln;

}, onFailure: {
    "*** scsynth FAILED TO BOOT.".postln;
    "*** Check inDevice/outDevice names (ServerOptions.inDevices/outDevices),".postln;
    "*** that both devices run at 48000 Hz, that MiniFuse 2 has 2 inputs, and".postln;
    "*** that BlackHole 16ch has 16 outputs.".postln;
    false    // also print the standard boot-failure report
});
)
