// ===========================================================================
//  JI Station -- Stage 8.1 -- tests/sc/test_rhythm.scd
// ---------------------------------------------------------------------------
//  HUMAN-RUN sclang check for the rhythm foundation and beat-locked loops.
//  This file does not generate audio by itself; it verifies the language-side
//  clock/config state and prints the manual checks.  Evaluate after:
//
//      1. startup.scd
//      2. tests/sc/test_live_rig.scd
//      3. ui/control_surface.scd
//
//  Then follow the printed instructions with the GUI (or by hand).
// ===========================================================================
(
var failed = false;

if (~ji_tempo_clock.isNil) {
    "[RHYTHM TEST] FAIL: ~ji_tempo_clock is nil; evaluate ui/control_surface.scd after tests/sc/test_live_rig.scd".postln;
    failed = true;
} {
    if (~ji_loop_modes.isNil or: { ~ji_loop_beats.isNil }
        or: { ~ji_loop_seconds.isNil } or: { ~ji_loop_sched.isNil }) {
        "[RHYTHM TEST] FAIL: loop mode/beats/seconds state is missing".postln;
        failed = true;
    };
};

if (failed.not) {
    ("[RHYTHM TEST] bpm=% meter=%/% running=% clockTempo=%")
        .format(
            ~ji_fmtNum.(~ji_rhythm_bpm, 2),
            ~ji_rhythm_meter_num,
            ~ji_rhythm_meter_den,
            if (~ji_rhythm_running) { 1 } { 0 },
            ~ji_fmtNum.(~ji_tempo_clock.tempo, 4)
        ).postln;

    (1..4).do { |index|
        ("[RHYTHM TEST] loop % mode=% beats=% seconds=%")
            .format(
                index,
                ~ji_loop_modes[index - 1].asString,
                ~ji_fmtNum.(~ji_loop_beats[index - 1], 2),
                ~ji_fmtNum.(~ji_loop_take_seconds.(index), 3)
            ).postln;
        ~ji_ui_send_loop_config.(index);
    };

    // A local clock sanity check for the next-beat math; does not touch the
    // live ~ji_tempo_clock or any loop.
    Routine {
        var clock = TempoClock.new(2.0);       // 120 BPM
        var start = clock.beats;
        var target = start.ceil + 1;
        var delay = clock.beats2secs(target) - clock.beats2secs(clock.beats);
        clock.schedAbs(target, { clock.stop; nil });
        if (delay <= 0 or: { delay > 0.75 }) {
            ("[RHYTHM TEST] FAIL: next-beat delay % s is outside (0, 0.75]").format(delay).postln;
        } {
            ("[RHYTHM TEST] local clock next beat in % s (ok)").format(
                ~ji_fmtNum.(delay, 3)
            ).postln;
        };
        0.8.wait;
    }.play(AppClock);

    "[RHYTHM TEST] language-side state is installed.".postln;
    "[RHYTHM TEST] Now check by ear/eye:".postln;
    "[RHYTHM TEST]  1. Rhythm group -> Play: the drum groove should start on the next step (120 BPM, 4/4).".postln;
    "[RHYTHM TEST]  2. Change BPM and meter while playing; drums should follow without an audio restart.".postln;
    "[RHYTHM TEST]  3. Loop 1 -> Beat, 4 beats; Loop 2 -> Beat, 3 beats; record both; they should phase.".postln;
    "[RHYTHM TEST]     If Beat Rec/Play does nothing, re-evaluate ui/control_surface.scd:".postln;
    "[RHYTHM TEST]     Stage 8.2.2 fixes action.value -> action.value(index) for scheduled beat actions.".postln;
    "[RHYTHM TEST]  4. Stop/Play and confirm no stuck drum notes and no stuck loops.".postln;
    "[RHYTHM TEST]  5. Free mode must behave exactly as Stage 7.".postln;
    "[RHYTHM TEST]  6. Missing IAC Bus 3: the bridge must keep drone/arp/audio running and report drums disabled.".postln;
    "[RHYTHM TEST] Audio/MIDI acceptance is human-only; record the result in docs/README_stage8.md.".postln;
} {
    "[RHYTHM TEST] FAILED -- fix the state above and re-evaluate.".postln;
};
)
