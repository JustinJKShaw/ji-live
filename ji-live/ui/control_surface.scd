// ===========================================================================
//  JI Station -- Stage 7 -- ui/control_surface.scd
// ---------------------------------------------------------------------------
//  sclang side of the minimal performance GUI.  Evaluate this file AFTER
//  test_live_rig.scd (it uses that file's ~ji_* functions and loop state).
//
//  It adds only a control/status surface:
//
//    UI -> sclang   /ji/autotune/set [channel, param, value]
//                   /ji/loop/record | record_length | stop_record | play |
//                   stop | stop_all | clear | set_default_length
//                   /ji/status/query
//    sclang -> UI   /ji/cpu [percent]
//                   /ji/loop/state [index, state string]
//                   /ji/autotune/state [channel, retuneTime, mix, windowSize,
//                                       pitchRangeLimit, tartiniThreshold,
//                                       inputGain]
//
//  It does not change any SynthDef, audio bus, lookup, glide, or loop-length
//  semantics.  It is safe to re-evaluate: old OSCdefs and the old status
//  routine are freed/stopped first.
// ===========================================================================
(
// --- Remove the previous Stage 7 surface -----------------------------------
~ji_ui_autotune_oscdef !? { |def| def.free };
~ji_ui_loop_oscdefs !? { |defs| defs.do { |def| def.free } };
~ji_ui_status_oscdef !? { |def| def.free };
~ji_ui_status_routine !? { |routine| routine.stop };

// --- Require the accepted live rig ----------------------------------------
~ji_ui_rig_ready = ~ji_autotune_synth_ch0.notNil
    and: { ~ji_autotune_synth_ch1.notNil }
    and: { ~ji_record_loop.notNil }
    and: { ~ji_stop_record.notNil }
    and: { ~ji_play_loop.notNil }
    and: { ~ji_stop_loop.notNil }
    and: { ~ji_set_loop_default.notNil }
    and: { ~ji_set_loop_buffer.notNil }
    and: { ~ji_loop_states.notNil }
    and: { ~ji_buf_loop.notNil }
    and: { ~ji_dual_voices.notNil };

if (~ji_ui_rig_ready.not) {
    "[UI] evaluate test_live_rig.scd first -- control surface not installed".postln;
} {
    // --- Destination ------------------------------------------------------
    //  Manual changes: set ~ji_ui_host / ~ji_ui_port, then re-evaluate this
    //  file (or set ~ji_ui_addr = NetAddr(~ji_ui_host, ~ji_ui_port) yourself).
    ~ji_ui_host = ~ji_ui_host ? "127.0.0.1";
    ~ji_ui_port = ~ji_ui_port ? 9001;
    ~ji_ui_addr = NetAddr(~ji_ui_host, ~ji_ui_port);

    // --- Autotune whitelist / local mirror -------------------------------
    ~ji_ui_autotune_param_order = [
        \retuneTime, \mix, \windowSize,
        \pitchRangeLimit, \tartiniThreshold, \inputGain
    ];
    ~ji_ui_autotune_ranges = (
        retuneTime: [0.001, 2.0],
        mix: [0.0, 1.0],
        windowSize: [0.02, 0.5],
        pitchRangeLimit: [0.0, 24.0],
        tartiniThreshold: [0.0, 1.0],
        inputGain: [0.0, 2.0]
    );
    ~ji_ui_autotune_values = [
        [0.05, 1.0, 0.1, 12.0, 0.9, 1.0],
        [0.05, 1.0, 0.1, 12.0, 0.9, 1.0]
    ];

    ~ji_ui_send_autotune_state = { |channel|
        var values = ~ji_ui_autotune_values[channel];
        ~ji_ui_addr.sendMsg(
            '/ji/autotune/state', channel,
            values[0], values[1], values[2],
            values[3], values[4], values[5]
        );
    };

    //  Read the six live Synth arguments once so a re-evaluated control
    //  surface does not report stale defaults after the UI already moved them.
    ~ji_ui_refresh_autotune = { |channel|
        var synth, params, values, remaining;
        synth = if (channel == 0) {
            ~ji_autotune_synth_ch0
        } {
            ~ji_autotune_synth_ch1
        };
        params = ~ji_ui_autotune_param_order;
        values = ~ji_ui_autotune_values[channel].copy;
        remaining = params.size;
        // Send the local mirror immediately; the Synth read below then
        // corrects it if the UI or a terminal changed values earlier.
        ~ji_ui_send_autotune_state.(channel);
        if (synth.notNil) {
            params.do { |param, index|
                synth.get(param, { |value|
                    values[index] = value.asFloat;
                    remaining = remaining - 1;
                    if (remaining == 0) {
                        ~ji_ui_autotune_values[channel] = values;
                        ~ji_ui_send_autotune_state.(channel);
                    };
                });
            };
        };
    };

    // --- Loop status helpers ---------------------------------------------
    ~ji_ui_send_loop_state = { |index|
        ~ji_ui_addr.sendMsg(
            '/ji/loop/state', index, ~ji_loop_states[index - 1].asString
        );
    };

    //  Recording becomes \recording only after the buffer allocation +
    //  s.sync inside ~ji_record_loop, so report it just after that.
    ~ji_ui_send_loop_state_later = { |index, delay = 0.1|
        Routine {
            delay.wait;
            ~ji_ui_send_loop_state.(index);
        }.play(AppClock);
    };

    ~ji_ui_loop_index = { |msg|
        var index;
        index = if (msg.size >= 2) { msg[1].asInteger } { 0 };
        if (index >= 1 and: { index <= 4 }) { index } { nil };
    };

    // --- Autotune handler ------------------------------------------------
    ~ji_ui_autotune_oscdef = OSCdef(\ji_ui_autotune, { |msg|
        var channel, param, raw, range, paramIndex, synth, value;
        if (msg.size < 4) {
            "[UI] /ji/autotune/set expects channel, param, value".postln;
        } {
            channel = msg[1].asInteger;
            param = msg[2].asSymbol;
            raw = msg[3];
            range = ~ji_ui_autotune_ranges[param];
            paramIndex = ~ji_ui_autotune_param_order.indexOf(param);
            synth = nil;
            if (channel == 0) { synth = ~ji_autotune_synth_ch0 };
            if (channel == 1) { synth = ~ji_autotune_synth_ch1 };

            if (synth.isNil or: { range.isNil }
                or: { paramIndex.isNil }
                or: { raw.isNumber.not }) {
                ("[UI] ignore /ji/autotune/set: channel=% param=% value=%").format(
                    channel, param, raw
                ).postln;
            } {
                value = raw.asFloat.clip(range[0], range[1]);
                // Whitelisted symbol only; never pass an arbitrary string.
                synth.set(~ji_ui_autotune_param_order[paramIndex], value);
                ~ji_ui_autotune_values[channel][paramIndex] = value;
                ~ji_ui_send_autotune_state.(channel);
                ("[UI] autotune ch=% % = %").format(
                    channel, param, value
                ).postln;
            };
        };
    }, '/ji/autotune/set');

    // --- Loop transport handlers -----------------------------------------
    ~ji_ui_loop_oscdefs = [
        OSCdef(\ji_ui_loop_record, { |msg|
            var index;
            index = ~ji_ui_loop_index.(msg);
            if (index.isNil) {
                "[UI] ignore /ji/loop/record: index must be 1..4".postln;
            } {
                ~ji_record_loop.(index);
                ~ji_ui_send_loop_state_later.(index);
            };
        }, '/ji/loop/record'),

        OSCdef(\ji_ui_loop_record_length, { |msg|
            var index, seconds;
            index = ~ji_ui_loop_index.(msg);
            if (index.isNil) {
                "[UI] ignore /ji/loop/record_length: index must be 1..4".postln;
            } {
                seconds = if (msg.size >= 3 and: { msg[2].isNumber }) {
                    msg[2].asFloat.clip(0.1, 60.0)
                } {
                    ~ji_loop_default_seconds.asFloat.clip(0.1, 60.0)
                };
                ~ji_record_loop.(index, seconds);
                ~ji_ui_send_loop_state_later.(index);
                ("[UI] loop % record length = % s").format(
                    index, seconds
                ).postln;
            };
        }, '/ji/loop/record_length'),

        OSCdef(\ji_ui_loop_stop_record, { |msg|
            var index;
            index = ~ji_ui_loop_index.(msg);
            if (index.isNil) {
                "[UI] ignore /ji/loop/stop_record: index must be 1..4".postln;
            } {
                ~ji_stop_record.(index);
                ~ji_ui_send_loop_state.(index);
            };
        }, '/ji/loop/stop_record'),

        OSCdef(\ji_ui_loop_play, { |msg|
            var index;
            index = ~ji_ui_loop_index.(msg);
            if (index.isNil) {
                "[UI] ignore /ji/loop/play: index must be 1..4".postln;
            } {
                ~ji_play_loop.(index);
                ~ji_ui_send_loop_state.(index);
            };
        }, '/ji/loop/play'),

        OSCdef(\ji_ui_loop_stop, { |msg|
            var index;
            index = ~ji_ui_loop_index.(msg);
            if (index.isNil) {
                "[UI] ignore /ji/loop/stop: index must be 1..4".postln;
            } {
                ~ji_stop_loop.(index);
                ~ji_ui_send_loop_state.(index);
            };
        }, '/ji/loop/stop'),

        OSCdef(\ji_ui_loop_stop_all, { |msg|
            // NOTE: ~ji_stop_all also frees the live autotune synths and
            // stops the lookup/CPU routines (it is the whole-rig stop).  For
            // the UI's "Stop All Loops" button use the per-loop functions so
            // the live inputs and the rest of the rig keep running.
            (1..4).do { |index|
                ~ji_stop_record.(index);
                ~ji_stop_loop.(index);
            };
            (1..4).do { |index| ~ji_ui_send_loop_state.(index) };
        }, '/ji/loop/stop_all'),

        OSCdef(\ji_ui_loop_clear, { |msg|
            var index, buffer;
            index = ~ji_ui_loop_index.(msg);
            if (index.isNil) {
                "[UI] ignore /ji/loop/clear: index must be 1..4".postln;
            } {
                buffer = ~ji_buf_loop[index - 1];
                // Stop the recorder first: clearing a buffer a recorder is
                // still writing to would be unsafe.
                ~ji_stop_record.(index);
                ~ji_stop_loop.(index);
                ~ji_loop_states[index - 1] = \empty;
                ~ji_ui_send_loop_state.(index);
                if (buffer.notNil and: { buffer.bufnum.notNil }) {
                    Routine {
                        // Let the player's 30 ms gate fade finish first.
                        0.1.wait;
                        if (buffer.bufnum.notNil) { buffer.free };
                        if (~ji_buf_loop[index - 1] === buffer) {
                            ~ji_set_loop_buffer.(index, nil);
                        };
                    }.play(AppClock);
                } {
                    ~ji_set_loop_buffer.(index, nil);
                };
                ("[UI] loop % cleared").format(index).postln;
            };
        }, '/ji/loop/clear'),

        OSCdef(\ji_ui_loop_set_default_length, { |msg|
            var seconds;
            if (msg.size >= 2 and: { msg[1].isNumber }) {
                seconds = msg[1].asFloat.clip(0.1, 60.0);
                ~ji_set_loop_default.(seconds);
            } {
                "[UI] /ji/loop/set_default_length expects seconds > 0".postln;
            };
        }, '/ji/loop/set_default_length')
    ];

    // --- Status query ----------------------------------------------------
    ~ji_ui_status_oscdef = OSCdef(\ji_ui_status, { |msg|
        // Immediate local snapshot, then a fresh read of the live Synth args.
        ~ji_ui_addr.sendMsg('/ji/cpu', s.avgCPU ? 0.0);
        ~ji_ui_send_autotune_state.(0);
        ~ji_ui_send_autotune_state.(1);
        (1..4).do { |index| ~ji_ui_send_loop_state.(index) };
        ~ji_ui_refresh_autotune.(0);
        ~ji_ui_refresh_autotune.(1);
    }, '/ji/status/query');

    // --- Periodic status: CPU and loop states at 2 Hz --------------------
    //  Actions also send immediately; 2 Hz keeps auto-stop lag short without
    //  flooding the UI (five small UDP messages per tick).
    ~ji_ui_status_routine = Routine {
        loop {
            ~ji_ui_addr.sendMsg('/ji/cpu', s.avgCPU ? 0.0);
            4.do { |i| ~ji_ui_send_loop_state.(i + 1) };
            0.5.wait;
        };
    }.play(AppClock);

    // --- Initial announce ------------------------------------------------
    ~ji_ui_addr.sendMsg('/ji/cpu', s.avgCPU ? 0.0);
    (1..4).do { |index| ~ji_ui_send_loop_state.(index) };
    ~ji_ui_refresh_autotune.(0);
    ~ji_ui_refresh_autotune.(1);
    ("[UI] control surface installed -> %:%; autotune + loop + status OSC ready").format(
        ~ji_ui_host, ~ji_ui_port
    ).postln;
};
)
