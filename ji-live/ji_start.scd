// ===========================================================================
//  JI Station -- Stage 8.3b -- ji_start.scd
// ---------------------------------------------------------------------------
//  THE one sclang file to evaluate.  From a fresh interpreter, evaluate this
//  file (Cmd+Return) and nothing else on the sclang side is needed.
//
//  It does not contain audio code.  It loads, in order:
//
//      1. startup.scd                 ServerOptions, buses, SynthDefs, boot
//      2. tests/sc/test_live_rig.scd  live autotune + loop rig, receivers
//      3. ui/control_surface.scd      tempo/loop/autotune/status OSC surface
//
//  and then prints one [JI] ready line with the exact Python command.
//
//  BOOT OWNERSHIP
//      startup.scd boots scsynth itself with s.waitForBoot (the "Boot" button
//      is not needed).  This file waits for that boot, then flushes the server
//      command queue with s.sync so every SynthDef in synthdefs/*.scd is
//      compiled before the rig creates a Synth.  If the server does not come up
//      within 25 s, or a SynthDef is missing, it prints a specific error and
//      stops instead of installing a half-working rig.
//
//  RE-EVALUATING IS SAFE
//      This file stops its own previous watchdog routine first.  The loaded
//      files already clean up after themselves: test_live_rig.scd stops the
//      known routines and frees the old synths/buffers; control_surface.scd
//      frees its old OSCdefs and stops its old status routine; startup.scd
//      re-uses the existing control buses.  Re-evaluate this file after a
//      server reboot or a stale editor state; do not reboot scsynth without
//      restarting sclang if you can avoid it (bus numbering changes).
//
//  THE OLD FILES STILL WORK ON THEIR OWN
//      startup.scd, tests/sc/test_live_rig.scd and ui/control_surface.scd stay
//      valid and independently evaluable for debugging and for the human
//      checks in tests/sc/*.  Evaluating all three by hand is the archive
//      path; ji_start.scd is the supported one.
//
//  STOP
//      ~ji_stop_all.();            // stops audio, loops, recorders
//      ~ji_dual_lookup_routine.stop;
//      ~ji_dual_cpu_routine.stop;
//      ~ji_ui_status_routine.stop;
//      ~ji_tempo_clock.stop;
//      (see docs/START_HERE.md for the complete order)
// ===========================================================================
(
var jiRoot, startupPath, liveRigPath, surfacePath, launcherPath;
var launcherCmd, requiredDefs;

jiRoot = thisProcess.nowExecutingPath !? { |path| PathName(path).parentPath };

if (jiRoot.isNil) {
    "*** [JI] startup error: the project folder is unknown (thisProcess.nowExecutingPath is nil).".postln;
    "*** [JI] Save this file as <project>/ji-live/ji_start.scd and evaluate it from the editor buffer that is backed by that file.".postln;
    Error("ji_start: cannot resolve the project root; evaluate ji_start.scd from ji-live/").throw;
};

// --- 0. One watchdog at a time ---------------------------------------------
//  A previous evaluation may still be waiting for a boot; stop it so two
//  waiters can never install the rig twice.
~ji_start_routine !? { |routine| routine.stop };
~ji_start_routine = nil;
~ji_start_ready = false;
~ji_root = jiRoot;
("[JI] project root: " ++ jiRoot).postln;

// --- 1. Prerequisites (fail loudly, never silently) ------------------------
~ji_start_require = { |label, path|
    // pathMatch() is the same existence helper startup.scd already uses: it
    // returns an empty Array when nothing matches the (wildcard-free) path.
    if (path.pathMatch.isEmpty) {
        ("*** [JI] startup error: missing " ++ label ++ ": " ++ path).postln;
        ("*** [JI] Expected the file under the project root " ++ jiRoot ++ ".").postln;
        Error("ji_start: missing prerequisite " ++ path).throw;
    };
    path;
};

startupPath  = ~ji_start_require.("startup.scd",                jiRoot +/+ "startup.scd");
liveRigPath  = ~ji_start_require.("live rig",                   jiRoot +/+ "tests/sc/test_live_rig.scd");
surfacePath  = ~ji_start_require.("control surface",            jiRoot +/+ "ui/control_surface.scd");
launcherPath = ~ji_start_require.("Python launcher",            jiRoot +/+ "run/ji_station.py");

launcherCmd = "cd " ++ jiRoot ++ " && uv run --with python-osc --with mido --with python-rtmidi --with PyQt6 python run/ji_station.py --midi --drums --bass";

requiredDefs = [\ji_autotune_ch, \ji_looper_recorder, \ji_looper_player];

// The SynthDefs and the Stage 4 buses are both created by startup.scd's
// waitForBoot callback, so waiting for this predicate is enough.  The bus check
// catches a server reboot with a stale sclang (old defs, freed buses).
~ji_start_rig_ready = {
    requiredDefs.every { |name| SynthDescLib.global[name].notNil }
    and: { ~ji_autotune_freq1.notNil }
    and: { ~ji_autotune_freq2.notNil }
    and: { ~ji_loop_freq1.notNil }
    and: { ~ji_loop_freq4.notNil }
    and: { ~ji_loop_hasfreq1.notNil }
};

// --- 2. Evaluate startup.scd (ServerOptions, buses, boot, SynthDefs) ------
("[JI] loading " ++ startupPath).postln;
startupPath.load;

// --- 3..6. Wait for the server + SynthDefs, then install the rig -----------
~ji_start_routine = Routine {
    var waited = 0.0, missing;

    // startup.scd calls s.waitForBoot, so give it up to 25 s.
    while { s.serverRunning.not and: { waited < 25.0 } } {
        0.1.wait;
        waited = waited + 0.1;
    };

    if (s.serverRunning.not) {
        "*** [JI] startup error: scsynth did not boot within 25 s.".postln;
        "*** [JI] Check ServerOptions.inDevices / outDevices in startup.scd:".postln;
        ("*** [JI]   available inputs:  %").format(ServerOptions.inDevices).postln;
        ("*** [JI]   available outputs: %").format(ServerOptions.outDevices).postln;
        "*** [JI] Also check that MiniFuse 2 and BlackHole 16ch both run at 48000 Hz.".postln;
        "*** [JI] The rig was NOT installed.  Fix the device names, then re-evaluate ji_start.scd.".postln;
    } {
        // Flush the server command queue: every /d_recv sent by startup.scd is
        // compiled by the time this returns.
        s.sync;

        // The defs are registered client-side by SynthDef.add; a slow or busy
        // server may still be compiling, so poll briefly instead of failing.
        waited = 0.0;
        while { (~ji_start_rig_ready.value.not) and: { waited < 10.0 } } {
            0.1.wait;
            waited = waited + 0.1;
        };

        missing = requiredDefs.select { |name| SynthDescLib.global[name].isNil };

        if (~ji_start_rig_ready.value.not) {
            "*** [JI] startup error: the rig prerequisites are not ready.".postln;
            ("*** [JI] missing SynthDefs: %").format(missing).postln;
            "*** [JI] startup.scd loads every synthdefs/*.scd; check the post window above for a load error.".postln;
            ("*** [JI] Stage 4 buses: autotune_freq2=% loop_freq1=% loop_hasfreq1=%").format(
                ~ji_autotune_freq2, ~ji_loop_freq1, ~ji_loop_hasfreq1
            ).postln;
            "*** [JI] If you rebooted scsynth, also restart the interpreter (Language > Restart Interpreter), then re-evaluate ji_start.scd.".postln;
        } {
            // 4. Live rig: two autotuned inputs + two loop voices + receivers.
            ("[JI] loading " ++ liveRigPath).postln;
            liveRigPath.load;
            s.sync;

            // 5. Control surface: tempo/loop/autotune/status OSC.
            ("[JI] loading " ++ surfacePath).postln;
            surfacePath.load;
            0.1.wait;

            ~ji_start_ready = true;

            // 6. One clear ready line.
            "-------------------------------------------------------------------".postln;
            ("[JI] sclang rig + control surface ready; langPort=% sr=% Hz out=% in=%").format(
                NetAddr.langPort, s.sampleRate.asInteger,
                s.options.numOutputBusChannels, s.options.numInputBusChannels
            ).postln;
            ("[JI] next: " ++ launcherCmd).postln;
            "[JI] the bridge must be started AFTER this sclang rig; ji_station.py does that and starts the GUI too.".postln;
            "[JI] stop: ~ji_stop_all.(); then Ctrl+C the launcher.".postln;
            "-------------------------------------------------------------------".postln;
        };
    };

    ~ji_start_routine = nil;
};

("[JI] waiting for scsynth to boot ...").postln;
~ji_start_routine.play(AppClock);
)
