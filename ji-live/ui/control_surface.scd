// ===========================================================================
//  JI Station -- Stage 8.1 -- ui/control_surface.scd
// ---------------------------------------------------------------------------
//  sclang side of the performance GUI.  Evaluate this file AFTER
//  tests/sc/test_live_rig.scd (it uses that file's ~ji_* functions and loop
//  state).
//
//  It adds only a control/status surface:
//
//    UI -> sclang   /ji/autotune/set [channel, param, value]
//                   /ji/loop/record | record_length | stop_record | play |
//                   stop | stop_all | clear | set_default_length |
//                   set_mode | set_beats | set_length
//                   /ji/status/query
//    Bridge -> sclang /ji/rhythm/tempo [bpm, numerator, denominator, running]
//    sclang -> UI   /ji/cpu [percent]
//                   /ji/loop/state [index, state string]
//                   /ji/loop/config [index, mode, beats, seconds]
//                   /ji/autotune/state [channel, retuneTime, mix, windowSize,
//                                       pitchRangeLimit, tartiniThreshold,
//                                       inputGain]
//    sclang -> bridge /ji/rhythm/request   (ask for a tempo resync)
//
//  Stage 8.1 loop semantics:
//
//    free mode: exactly the Stage 7 behavior.  Length is seconds, Record and
//               Play happen immediately when the message arrives.
//    beat mode: the loop's length is a number of beats (1..64).  Record and
//               Play are delayed to the next beat boundary on ~ji_tempo_clock;
//               the take is beats * 60 / bpm seconds at record time.  Stop and
//               Clear remain immediate and cancel a pending action.
//
//  Tempo changes do NOT time-stretch an existing loop buffer.  Stop and
//  re-record, or use free mode.
//
//  It does not change any SynthDef, audio bus, lookup, glide, or loop-length
//  semantics.  It is safe to re-evaluate: old OSCdefs and the old status
//  routine are freed/stopped first.
// ===========================================================================
(
// --- Remove the previous UI surface ---------------------------------------
~ji_ui_autotune_oscdef !? { |def| def.free };
~ji_ui_rhythm_oscdef !? { |def| def.free };
~ji_ui_loop_oscdefs !? { |defs| defs.do { |def| def.free } };
~ji_ui_status_oscdef !? { |def| def.free };
~ji_ui_status_routine !? { |routine| routine.stop };
~ji_loop_sched !? { |scheds| scheds.do { |sched| sched !? { |item| if (item.respondsTo(\stop)) { item.stop } } } };
~ji_tempo_clock !? { |clock| clock.stop };

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
    "[UI] evaluate tests/sc/test_live_rig.scd first -- control surface not installed".postln;
} {
    // --- Destination ------------------------------------------------------
    //  Manual changes: set ~ji_ui_host / ~ji_ui_port, then re-evaluate this
    //  file (or set ~ji_ui_addr = NetAddr(~ji_ui_host, ~ji_ui_port) yourself).
    ~ji_ui_host = ~ji_ui_host ? "127.0.0.1";
    ~ji_ui_port = ~ji_ui_port ? 9001;
    ~ji_ui_addr = NetAddr(~ji_ui_host, ~ji_ui_port);

    //  Stage 8.1: the bridge owns the canonical rhythm state.  This address
    //  is only used for the install-time tempo resync request.
    ~ji_bridge_host = ~ji_bridge_host ? "127.0.0.1";
    ~ji_bridge_port = ~ji_bridge_port ? 9000;
    ~ji_bridge_addr = NetAddr(~ji_bridge_host, ~ji_bridge_port);

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

    // --- Stage 8.1 rhythm state and beat clock ---------------------------
    //  ~ji_tempo_clock.tempo is beats per second (BPM / 60), matching the
    //  bridge's quarter-note BPM.  The clock is free-running; a new transport
    //  start moves ~ji_beat_offset to the next clock beat, and beat-mode
    //  actions quantize relative to that offset.
    ~ji_rhythm_bpm = 120.0;
    ~ji_rhythm_meter_num = 4;
    ~ji_rhythm_meter_den = 4;
    ~ji_rhythm_running = false;
    ~ji_tempo_clock = TempoClock.new(~ji_rhythm_bpm / 60.0);
    ~ji_beat_offset = ~ji_tempo_clock.beats.ceil;

    // --- Per-loop mode / length state ------------------------------------
    ~ji_loop_modes = Array.fill(4, { \free });
    ~ji_loop_beats = Array.fill(4, { 4.0 });
    ~ji_loop_seconds = Array.fill(4, { ~ji_loop_default_seconds });
    ~ji_loop_seconds_custom = Array.fill(4, { false });
    ~ji_loop_sched = Array.newClear(4);

    // --- Loop status helpers ---------------------------------------------
    ~ji_ui_send_loop_state = { |index|
        ~ji_ui_addr.sendMsg(
            '/ji/loop/state', index, ~ji_loop_states[index - 1].asString
        );
    };

    //  Free mode follows ~ji_loop_default_seconds unless this loop's length
    //  was set individually (the Stage 7 behavior plus a per-loop override).
    ~ji_loop_free_seconds = { |index|
        if (~ji_loop_seconds_custom[index - 1]) {
            ~ji_loop_seconds[index - 1]
        } {
            ~ji_loop_default_seconds
        };
    };

    ~ji_loop_take_seconds = { |index|
        if (~ji_loop_modes[index - 1] == \beat) {
            ~ji_loop_beats[index - 1] * 60.0 / ~ji_rhythm_bpm
        } {
            ~ji_loop_free_seconds.(index)
        };
    };

    ~ji_ui_send_loop_config = { |index|
        var mode = ~ji_loop_modes[index - 1];
        var beats = ~ji_loop_beats[index - 1];
        var seconds;
        seconds = if (mode == \beat) {
            beats * 60.0 / ~ji_rhythm_bpm
        } {
            ~ji_loop_free_seconds.(index)
        };
        ~ji_ui_addr.sendMsg(
            '/ji/loop/config', index, mode.asString, beats, seconds
        );
    };

    ~ji_ui_send_all_loop_configs = {
        (1..4).do { |index| ~ji_ui_send_loop_config.(index) };
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

    // --- Beat quantization helpers ---------------------------------------
    //  The next integer beat after the transport-start origin.  If the
    //  transport has just started at beat 10.3, the next target is 11.
    ~ji_next_beat = {
        var position = ~ji_tempo_clock.beats - ~ji_beat_offset;
        ~ji_beat_offset + position.floor + 1;
    };

    ~ji_cancel_loop_schedule = { |index|
        var sched = ~ji_loop_sched[index - 1];
        if (sched.notNil) {
            if (sched.respondsTo(\stop)) { sched.stop };
            ~ji_loop_sched[index - 1] = nil;
        };
    };

    ~ji_cancel_all_loop_schedules = {
        (1..4).do { |index| ~ji_cancel_loop_schedule.(index) };
    };

    //  Schedule a beat-mode action at the next beat boundary.  The action is
    //  cancelled if the transport stops or the loop leaves beat mode before
    //  the boundary.
    ~ji_schedule_loop_action = { |index, action, label|
        var target, delay;
        if (~ji_rhythm_running.not) {
            ("[UI] loop % % rejected: transport is stopped (beat mode needs Play)").format(
                index, label
            ).postln;
            ~ji_ui_send_loop_config.(index);
        } {
            ~ji_cancel_loop_schedule.(index);
            target = ~ji_next_beat.value;
            delay = ~ji_tempo_clock.beats2secs(target)
                - ~ji_tempo_clock.beats2secs(~ji_tempo_clock.beats);
            ~ji_loop_sched[index - 1] = ~ji_tempo_clock.schedAbs(target, {
                ~ji_loop_sched[index - 1] = nil;
                if (~ji_rhythm_running
                    and: { ~ji_loop_modes[index - 1] == \beat }) {
                    action.value;
                    ("[RHYTHM] loop % % fired on beat %").format(
                        index, label, target
                    ).postln;
                } {
                    ("[UI] loop % % cancelled (transport stopped or mode changed)").format(
                        index, label
                    ).postln;
                };
                nil;
            });
            ("[RHYTHM] loop % % scheduled for beat % (in % s)").format(
                index, label, target, ~ji_fmtNum.(delay.max(0), 3)
            ).postln;
            ~ji_ui_send_loop_config.(index);
        };
    };

    //  Actions used by both free (immediate) and beat (scheduled) paths.
    ~ji_record_loop_action = { |index|
        var seconds = ~ji_loop_take_seconds.(index);
        ~ji_record_loop.(index, seconds);
        ~ji_ui_send_loop_state_later.(index);
        ~ji_ui_send_loop_config.(index);
        ("[RHYTHM] loop % record mode=% beats=% seconds=%").format(
            index,
            ~ji_loop_modes[index - 1].asString,
            ~ji_fmtNum.(~ji_loop_beats[index - 1], 3),
            ~ji_fmtNum.(seconds, 3)
        ).postln;
    };

    ~ji_play_loop_action = { |index|
        ~ji_play_loop.(index);
        ~ji_ui_send_loop_state.(index);
        ~ji_ui_send_loop_config.(index);
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

    // --- Stage 8.1 rhythm handler ----------------------------------------
    ~ji_ui_rhythm_oscdef = OSCdef(\ji_ui_rhythm_tempo, { |msg|
        var bpm, num, den, running, wasRunning;
        if (msg.size < 5) {
            "[RHYTHM] /ji/rhythm/tempo expects bpm, numerator, denominator, running".postln;
        } {
            bpm = msg[1].asFloat;
            num = msg[2].asInteger;
            den = msg[3].asInteger;
            running = msg[4].asInteger;
            if (bpm.isNaN or: { bpm < 20 } or: { bpm > 300 }
                or: { num < 1 } or: { num > 16 }
                or: { #[1, 2, 4, 8, 16].includes(den).not }
                or: { running != 0 and: { running != 1 } }) {
                ("[RHYTHM] ignore invalid tempo: bpm=% meter=%/% running=%").format(
                    bpm, num, den, running
                ).postln;
            } {
                wasRunning = ~ji_rhythm_running;
                ~ji_rhythm_bpm = bpm;
                ~ji_rhythm_meter_num = num;
                ~ji_rhythm_meter_den = den;
                ~ji_tempo_clock.tempo = bpm / 60.0;
                ~ji_rhythm_running = running == 1;
                if (~ji_rhythm_running and: { wasRunning.not }) {
                    // New transport start: quantize to the next clock beat.
                    ~ji_beat_offset = ~ji_tempo_clock.beats.ceil;
                };
                if (wasRunning and: { ~ji_rhythm_running.not }) {
                    // Beat-mode loops are transport-gated: cancel pending
                    // actions and stop a beat-mode recording/playback now.
                    // Free loops keep running (they are started/stopped
                    // individually).
                    ~ji_cancel_all_loop_schedules.value;
                    (1..4).do { |index|
                        if (~ji_loop_modes[index - 1] == \beat) {
                            if (~ji_loop_states[index - 1] == \recording) {
                                ~ji_stop_record.(index);
                            };
                            if (~ji_loop_states[index - 1] == \playing) {
                                ~ji_stop_loop.(index);
                            };
                            ~ji_ui_send_loop_state.(index);
                        };
                    };
                };
                ("[RHYTHM] bpm=% meter=%/% running=%").format(
                    ~ji_fmtNum.(~ji_rhythm_bpm, 1),
                    ~ji_rhythm_meter_num,
                    ~ji_rhythm_meter_den,
                    if (~ji_rhythm_running) { 1 } { 0 }
                ).postln;
                // Beat-mode config seconds depend on the tempo.
                ~ji_ui_send_all_loop_configs.value;
            };
        };
    }, '/ji/rhythm/tempo');

    // --- Loop transport handlers -----------------------------------------
    ~ji_ui_loop_oscdefs = [
        OSCdef(\ji_ui_loop_record, { |msg|
            var index, mode;
            index = ~ji_ui_loop_index.(msg);
            if (index.isNil) {
                "[UI] ignore /ji/loop/record: index must be 1..4".postln;
            } {
                mode = ~ji_loop_modes[index - 1];
                if (mode == \beat) {
                    ~ji_schedule_loop_action.(index, ~ji_record_loop_action, "record");
                } {
                    ~ji_record_loop.(index, ~ji_loop_free_seconds.(index));
                    ~ji_ui_send_loop_state_later.(index);
                    ~ji_ui_send_loop_config.(index);
                };
            };
        }, '/ji/loop/record'),

        OSCdef(\ji_ui_loop_record_length, { |msg|
            var index, seconds, mode;
            index = ~ji_ui_loop_index.(msg);
            if (index.isNil) {
                "[UI] ignore /ji/loop/record_length: index must be 1..4".postln;
            } {
                mode = ~ji_loop_modes[index - 1];
                seconds = if (msg.size >= 3 and: { msg[2].isNumber }) {
                    msg[2].asFloat.clip(0.1, 60.0)
                } {
                    ~ji_loop_default_seconds.asFloat.clip(0.1, 60.0)
                };
                if (mode == \beat) {
                    ~ji_schedule_loop_action.(index, ~ji_record_loop_action, "record");
                } {
                    // Free mode: remember this loop's length so /ji/loop/config
                    // reports what the UI is showing.
                    ~ji_loop_seconds[index - 1] = seconds;
                    ~ji_loop_seconds_custom[index - 1] = true;
                    ~ji_record_loop.(index, seconds);
                    ~ji_ui_send_loop_state_later.(index);
                    ~ji_ui_send_loop_config.(index);
                    ("[UI] loop % record length = % s").format(
                        index, seconds
                    ).postln;
                };
            };
        }, '/ji/loop/record_length'),

        OSCdef(\ji_ui_loop_stop_record, { |msg|
            var index;
            index = ~ji_ui_loop_index.(msg);
            if (index.isNil) {
                "[UI] ignore /ji/loop/stop_record: index must be 1..4".postln;
            } {
                ~ji_cancel_loop_schedule.(index);
                ~ji_stop_record.(index);
                ~ji_ui_send_loop_state.(index);
            };
        }, '/ji/loop/stop_record'),

        OSCdef(\ji_ui_loop_play, { |msg|
            var index, mode;
            index = ~ji_ui_loop_index.(msg);
            if (index.isNil) {
                "[UI] ignore /ji/loop/play: index must be 1..4".postln;
            } {
                mode = ~ji_loop_modes[index - 1];
                if (mode == \beat) {
                    ~ji_schedule_loop_action.(index, ~ji_play_loop_action, "play");
                } {
                    ~ji_play_loop.(index);
                    ~ji_ui_send_loop_state.(index);
                };
            };
        }, '/ji/loop/play'),

        OSCdef(\ji_ui_loop_stop, { |msg|
            var index;
            index = ~ji_ui_loop_index.(msg);
            if (index.isNil) {
                "[UI] ignore /ji/loop/stop: index must be 1..4".postln;
            } {
                // Stop is immediate in 8.1, including a pending beat action.
                ~ji_cancel_loop_schedule.(index);
                ~ji_stop_loop.(index);
                ~ji_ui_send_loop_state.(index);
            };
        }, '/ji/loop/stop'),

        OSCdef(\ji_ui_loop_stop_all, { |msg|
            // NOTE: ~ji_stop_all also frees the live autotune synths and
            // stops the lookup/CPU routines (it is the whole-rig stop).  For
            // the UI's "Stop All Loops" button use the per-loop functions so
            // the live inputs and the rest of the rig keep running.
            ~ji_cancel_all_loop_schedules.value;
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
                // Clear is immediate and cancels a pending beat action.
                ~ji_cancel_loop_schedule.(index);
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
                // Keep the Stage 7 acceptance range here; the UI and the
                // per-loop set_length use the stricter 1..30 s product range.
                seconds = msg[1].asFloat.clip(0.1, 60.0);
                ~ji_set_loop_default.(seconds);
                // Free-mode loops that were not customised follow the global
                // default, preserving the Stage 7 global-length behavior.
                4.do { |i|
                    if (~ji_loop_seconds_custom[i].not) {
                        ~ji_loop_seconds[i] = seconds;
                    };
                };
                ~ji_ui_send_all_loop_configs.value;
            } {
                "[UI] /ji/loop/set_default_length expects seconds > 0".postln;
            };
        }, '/ji/loop/set_default_length'),

        // --- Stage 8.1 per-loop mode / beat length / free length ---------
        OSCdef(\ji_ui_loop_set_mode, { |msg|
            var index, modeString, mode;
            index = ~ji_ui_loop_index.(msg);
            if (index.isNil) {
                "[UI] ignore /ji/loop/set_mode: index must be 1..4".postln;
            } {
                modeString = if (msg.size >= 3) { msg[2].asString.toLower } { "" };
                mode = if (modeString == "free") { \free } { nil };
                if (modeString == "beat") { mode = \beat };
                if (mode.isNil) {
                    ("[UI] ignore /ji/loop/set_mode: mode=% (use free or beat)").format(
                        modeString
                    ).postln;
                } {
                    // Leaving/entering beat mode cancels a pending action.
                    ~ji_cancel_loop_schedule.(index);
                    ~ji_loop_modes[index - 1] = mode;
                    ~ji_ui_send_loop_config.(index);
                    ("[RHYTHM] loop % mode=% beats=% seconds=%").format(
                        index,
                        mode.asString,
                        ~ji_fmtNum.(~ji_loop_beats[index - 1], 3),
                        ~ji_fmtNum.(~ji_loop_take_seconds.(index), 3)
                    ).postln;
                };
            };
        }, '/ji/loop/set_mode'),

        OSCdef(\ji_ui_loop_set_beats, { |msg|
            var index, beats;
            index = ~ji_ui_loop_index.(msg);
            if (index.isNil) {
                "[UI] ignore /ji/loop/set_beats: index must be 1..4".postln;
            } {
                if (msg.size >= 3 and: { msg[2].isNumber }) {
                    beats = msg[2].asFloat.clip(1.0, 64.0);
                    ~ji_loop_beats[index - 1] = beats;
                    ~ji_ui_send_loop_config.(index);
                    ("[RHYTHM] loop % mode=% beats=% seconds=%").format(
                        index,
                        ~ji_loop_modes[index - 1].asString,
                        ~ji_fmtNum.(beats, 3),
                        ~ji_fmtNum.(~ji_loop_take_seconds.(index), 3)
                    ).postln;
                } {
                    "[UI] /ji/loop/set_beats expects a number 1..64".postln;
                };
            };
        }, '/ji/loop/set_beats'),

        OSCdef(\ji_ui_loop_set_length, { |msg|
            var index, seconds;
            index = ~ji_ui_loop_index.(msg);
            if (index.isNil) {
                "[UI] ignore /ji/loop/set_length: index must be 1..4".postln;
            } {
                if (msg.size >= 3 and: { msg[2].isNumber }) {
                    seconds = msg[2].asFloat.clip(1.0, 30.0);
                    ~ji_loop_seconds[index - 1] = seconds;
                    ~ji_loop_seconds_custom[index - 1] = true;
                    ~ji_ui_send_loop_config.(index);
                    ("[RHYTHM] loop % free length = % s").format(
                        index, ~ji_fmtNum.(seconds, 3)
                    ).postln;
                } {
                    "[UI] /ji/loop/set_length expects seconds 1..30".postln;
                };
            };
        }, '/ji/loop/set_length')
    ];

    // --- Status query ----------------------------------------------------
    ~ji_ui_status_oscdef = OSCdef(\ji_ui_status, { |msg|
        // Immediate local snapshot, then a fresh read of the live Synth args.
        ~ji_ui_addr.sendMsg('/ji/cpu', s.avgCPU ? 0.0);
        ~ji_ui_send_autotune_state.(0);
        ~ji_ui_send_autotune_state.(1);
        (1..4).do { |index| ~ji_ui_send_loop_state.(index) };
        ~ji_ui_send_all_loop_configs.value;
        ~ji_ui_refresh_autotune.(0);
        ~ji_ui_refresh_autotune.(1);
    }, '/ji/status/query');

    // --- Periodic status: CPU and loop states at 2 Hz --------------------
    //  Actions also send immediately; 2 Hz keeps auto-stop lag short without
    //  flooding the UI (five small UDP messages per tick).  Loop config is
    //  sent on query/install/change only, not on this tick.
    ~ji_ui_status_routine = Routine {
        loop {
            ~ji_ui_addr.sendMsg('/ji/cpu', s.avgCPU ? 0.0);
            4.do { |i| ~ji_ui_send_loop_state.(i + 1) };
            0.5.wait;
        };
    }.play(AppClock);

    // --- Initial announce ------------------------------------------------
    //  Ask the bridge for the canonical tempo first, so a control surface
    //  installed after the bridge still gets the current BPM/meter/transport.
    ~ji_bridge_addr.sendMsg('/ji/rhythm/request');
    ~ji_ui_addr.sendMsg('/ji/cpu', s.avgCPU ? 0.0);
    (1..4).do { |index| ~ji_ui_send_loop_state.(index) };
    ~ji_ui_send_all_loop_configs.value;
    ~ji_ui_refresh_autotune.(0);
    ~ji_ui_refresh_autotune.(1);
    ("[UI] control surface installed -> %:%; autotune + loop + rhythm + status OSC ready").format(
        ~ji_ui_host, ~ji_ui_port
    ).postln;
};
)
