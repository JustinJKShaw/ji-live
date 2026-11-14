// ===========================================================================
//  JI Station -- Stage 9.0 diagnostic (HUMAN-RUN)
// ---------------------------------------------------------------------------
//  Evaluate AFTER `ji_start.scd` (or after startup + test_live_rig +
//  control_surface).  It prints [DEBUG] lines that separate:
//
//    1. sclang accepting the Harm mode at all;
//    2. the exact /ji/loop/edit echo the GUI receives (size + harmMode);
//    3. whether the core recorder starts (loop 4 is used as scratch);
//    4. whether the FX synth is created after the player.
//
//  LOOP 4 IS OVERWRITTEN.  Use a quiet moment and put audio into input 1
//  (loop 4 records input 1, i.e. the guitar channel) if you want the FX test
//  to pass real audio.
// ===========================================================================
(
~ji_stage9_debug !? { |routine| routine.stop };
if (~ji_loop_edit.isNil or: { ~ji_record_loop.isNil }) {
    "*** [DEBUG] the rig is not installed; evaluate ji_start.scd first".postln;
} {
    ~ji_stage9_debug = Routine {
        var oldAddr, echoDef, loop = 4, i = 3;
        var modeAfter, echoSeen = false;

        ("[DEBUG] control surface version = %").format(
            ~ji_ui_control_surface_version
        ).postln;
        ("[DEBUG] SynthDefs: loop_voice=% recorder=% harmonic=%").format(
            SynthDescLib.global[\ji_loop_voice].notNil,
            SynthDescLib.global[\ji_looper_recorder].notNil,
            SynthDescLib.global[\ji_harmonic_delay].notNil
        ).postln;
        ("[DEBUG] FX buses = %").format(
            ~ji_loop_fx_buses.collect { |bus| bus.index }
        ).postln;

        // --- 1. direct mode edit -------------------------------------------
        ~ji_loop_set_edit.(1, \harm_mode, "cascade");
        modeAfter = ~ji_loop_edit[0][\harmMode];
        ("[DEBUG] direct harm_mode after set = % (class %)").format(
            modeAfter, modeAfter.class.asString
        ).postln;

        // --- 2. capture the exact /ji/loop/edit echo ----------------------
        //  Point the UI sender at sclang itself, install a temporary receiver,
        //  edit the mode, then restore the real UI address.
        oldAddr = ~ji_ui_addr;
        ~ji_ui_addr = NetAddr("127.0.0.1", NetAddr.langPort);
        echoDef = OSCdef(\ji_stage9_debug_edit, { |msg|
            echoSeen = true;
            if (msg.size >= 36) {
                ("[DEBUG] echo size=% args=% harmMode=% timeSync=%").format(
                    msg.size, msg.size - 1, msg[32], msg[35]
                ).postln;
            } {
                ("[DEBUG] echo size=% args=% (old/incomplete payload)").format(
                    msg.size, msg.size - 1
                ).postln;
            };
        }, '/ji/loop/edit');
        try {
            ~ji_loop_set_edit.(1, \harm_mode, "delay");
        } { |err|
            ("*** [DEBUG] mode edit threw: %").format(err.errorString).postln;
        };
        1.0.wait;
        echoDef.free;
        ~ji_ui_addr = oldAddr;
        ("[DEBUG] echo received = %").format(echoSeen).postln;
        ("[DEBUG] mode after echo test = %").format(
            ~ji_loop_edit[0][\harmMode]
        ).postln;

        // --- 3. core recorder ----------------------------------------------
        //  Loop 4 = input 1.  Any signal there will be recorded; silence still
        //  proves the recorder/state path.
        ~ji_record_loop.(loop, 2.0);
        2.5.wait;
        ("[DEBUG] record state=% buffer=% frames=%").format(
            ~ji_loop_states[i],
            ~ji_buf_loop[i],
            ~ji_buf_loop[i] !? { |buf| buf.numFrames }
        ).postln;
        ~ji_stop_record.(loop);

        // --- 4. FX synth after the player ----------------------------------
        ~ji_play_loop.(loop);
        0.5.wait;
        ~ji_loop_set_edit.(loop, \harm_mode, "delay");
        1.0.wait;
        ("[DEBUG] player=% fx=%").format(
            ~ji_loop_players[i], ~ji_loop_fx_synths[i]
        ).postln;
        ~ji_loop_fx_synths[i] !? { |synth|
            ("[DEBUG] fx node=% (player node=%)").format(
                synth.nodeID, ~ji_loop_players[i] !? { |p| p.nodeID }
            ).postln;
        };
        ("[DEBUG] loop % state=%").format(loop, ~ji_loop_states[i]).postln;

        // --- cleanup / restore ---------------------------------------------
        ~ji_stop_loop.(loop);
        ~ji_stop_record.(loop);
        ~ji_loop_set_edit.(1, \harm_mode, "off");
        ~ji_loop_set_edit.(loop, \harm_mode, "off");
        "[DEBUG] done. Copy the [DEBUG] lines into the chat.".postln;
        "s.plotTree;  // optional: check the FX node while it is still playing".postln;
        ~ji_stage9_debug = nil;
    }.play(AppClock);
};
)
