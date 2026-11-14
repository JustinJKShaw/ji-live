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
//                   Stage 8.6: /ji/loop/dub | edit_set | mem_save |
//                   mem_recall | mem_clear
//                   Stage 8.6.2: the same addresses; /ji/loop/edit_set gains
//                   grain_morph / grain_melt / grain_filter /
//                   grain_filter_rate (sclang-side morph ramp).
//                   Stage 8.7: edit_set also accepts grain_breath /
//                   grain_breath_rate (29 params) and /ji/loop/edit carries
//                   31 values.
//                   Stage 9.0: edit_set also accepts the eight harm_* params,
//                   /ji/loop/edit carries 39 values, /ji/loop/fx_fire triggers
//                   a Cascade pass, and the count-in bugs A1-A4 are fixed.
//                   /ji/status/query
//    Bridge -> sclang /ji/rhythm/tempo [bpm, numerator, denominator, running]
//    sclang -> UI   /ji/cpu [percent]
//                   /ji/loop/state [index, state string]
//                   /ji/loop/config [index, mode, beats, seconds]
//                   Stage 8.6: /ji/loop/mem [slot, filled, frames, seconds]
//                   | /ji/loop/count [index, beats_left]
//                   Stage 8.6.1: /ji/loop/edit [25 values] (the 15 of 8.6 plus
//                   grain size/pitch jitter, snap, smear, freeze, motion,
//                   speed, depth, burst and the effective snapped pitch)
//                   Stage 8.6.2: /ji/loop/edit [29 values] (+ morph, melt
//                   flag, filter amount and filter rate)
//                   Stage 8.7: /ji/loop/edit [31 values] (+ loop grain breath
//                   depth and rate)
//                   Stage 9.0: /ji/loop/edit [39 values] (+ harm mode/mix/time/
//                   time-sync/steps/spacing/falloff/auto) and
//                   /ji/loop/fx_fire [index] for a Cascade trigger
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

// --- Version marker ---------------------------------------------------
//  Printed on install so a stale editor buffer is obvious in the post
//  window.  8.2.2 fixed beat-mode Record/Play (action.value(index));
//  8.6.0 adds dub, loop edits, count-in, auto-play, the memory slots and the
//  beat-synced granular clock; 8.6.1 redesigns the granular half (motion,
//  cloud jitter, JI pitch snap, smear, freeze, beat burst) and fixes the
//  division/count-in edits being rejected by an identity-based string lookup;
//  8.6.2 adds the dry<->grain Morph blend, the Melt ramp and the moving melt
//  filter; 8.7.0 adds the loop grain breath (grain_breath / grain_breath_rate)
//  and the 31-value /ji/loop/edit echo; 9.0.0 adds the per-loop harmonic-series
//  delay FX, /ji/loop/fx_fire and the 39-value echo (+ fixed musical time).
~ji_ui_control_surface_version = "9.0.0";

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

    // --- Stage 8.6 count-in ------------------------------------------------
    //  A bar is the current meter's bar in quarter beats (4/4 = 4, 3/4 = 3,
    //  6/8 = 3).  The count runs on ~ji_tempo_clock, so it survives a stopped
    //  transport in free mode; beat mode cancels it on a transport stop.
    ~ji_count_in_beats = { |index|
        var i = index.asInteger.clip(1, 4);
        var barBeats = ~ji_rhythm_meter_num * 4.0 / ~ji_rhythm_meter_den;
        ~ji_loop_count_in_quarter_beats.(~ji_loop_edit[i - 1][\countIn], barBeats);
    };

    ~ji_loop_count_send = { |index, beatsLeft|
        ~ji_ui_addr.sendMsg('/ji/loop/count', index, beatsLeft.asInteger.max(0));
    };

    //  Cancel a pending count-in.  restore = true puts the last NON-COUNTING
    //  state back (empty/recorded/stopped) and never runs the auto-play step:
    //  there is no new take.  Stage 9.0 fix: a stored \counting can never be
    //  restored (that was the stuck-counting bug); when it is found, fall back
    //  to \recorded for a valid buffer and \empty otherwise.
    ~ji_cancel_count_in = { |index, restore, reason|
        var i = (index ? 1).asInteger.clip(1, 4);
        var shouldRestore = restore ? true;
        var sched = ~ji_loop_count_sched[i - 1];
        var wasCounting = ~ji_loop_states[i - 1] == \counting;
        var previous, validBuffer;
        if (sched.notNil or: { wasCounting }) {
            // Bump the generation: the pending tempo-clock callback becomes a
            // no-op.  Never .stop the object schedAbs returned (it may be the
            // clock itself).
            ~ji_loop_count_gen[i - 1] = ~ji_loop_count_gen[i - 1] + 1;
            ~ji_loop_count_sched[i - 1] = nil;
            if (shouldRestore) {
                previous = ~ji_loop_count_prev[i - 1];
                if (previous.isNil or: { previous == \counting }) {
                    validBuffer = ~ji_buf_loop[i - 1].notNil
                        and: { ~ji_buf_loop[i - 1].bufnum.notNil }
                        and: { ~ji_buf_loop[i - 1].numFrames > 0 };
                    previous = if (validBuffer) { \recorded } { \empty };
                    ("[LOOP %] count-in restore: invalid previous state -> %").format(
                        i, previous
                    ).postln;
                };
                ~ji_loop_states[i - 1] = previous;
            };
            ~ji_loop_count_send.(i, 0);
            ~ji_ui_send_loop_state.(i);
            ("[LOOP %] count-in cancelled%").format(
                i, if (reason.notNil) { " (" ++ reason ++ ")" } { "" }
            ).postln;
        };
    };

    //  The exact record path that used to sit inside ~ji_record_loop_action.
    ~ji_record_loop_now = { |index|
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

    //  Count in, then record.  The take length is recomputed at fire time.
    ~ji_count_in_start = { |index|
        var i = index.asInteger.clip(1, 4);
        var beats = ~ji_count_in_beats.(i);
        var previous;
        var now, target, generation;

        //  Stage 9.0 fix: resolve any pending count-in with restore = true
        //  BEFORE sampling the previous state.  Otherwise previous would be
        //  \counting and a later cancel would restore \counting (the stuck
        //  state).  A second count-in never starts from \counting.
        if (~ji_loop_states[i - 1] == \counting) {
            ~ji_cancel_count_in.(i, true, "restart");
        };
        ~ji_cancel_loop_schedule.(i);
        previous = ~ji_loop_states[i - 1];
        if (previous == \counting) {
            // Belt and braces: a state that is still counting is not a valid
            // previous state; it is not stored below.
            previous = if (~ji_buf_loop[i - 1].notNil
                and: { ~ji_buf_loop[i - 1].bufnum.notNil }
                and: { ~ji_buf_loop[i - 1].numFrames > 0 }) { \recorded } { \empty };
        };
        if (beats <= 0) {
            ~ji_record_loop_now.(i);
        } {
            ~ji_loop_count_prev[i - 1] = previous;
            now = ~ji_tempo_clock.beats;
            target = now.ceil + beats;
            generation = ~ji_loop_count_gen[i - 1] + 1;
            ~ji_loop_count_gen[i - 1] = generation;
            ~ji_loop_states[i - 1] = \counting;
            ~ji_ui_addr.sendMsg('/ji/loop/state', i, "counting");
            ~ji_loop_count_send.(i, beats);
            // One tick per counted quarter beat: beats-1 ... 1, then 0 when
            // the recorder starts.  Never per audio block.
            if (beats > 1) {
                (1..(beats - 1)).do { |left|
                    ~ji_tempo_clock.schedAbs(target - left, {
                        //  Stage 9.0 fix: every tick is generation-checked, so
                        //  a cancelled count-in's old ticks are silent even if
                        //  a new count-in has started for the same loop.
                        if (~ji_loop_count_gen[i - 1] == generation
                            and: { ~ji_loop_states[i - 1] == \counting }) {
                            ~ji_loop_count_send.(i, left);
                        };
                        nil;
                    });
                };
            };
            ~ji_loop_count_sched[i - 1] = ~ji_tempo_clock.schedAbs(target, {
                var seconds;
                if (~ji_loop_count_gen[i - 1] != generation) {
                    // Cancelled (Stop Rec / Clear / Stop All / Dub / second Rec
                    // / transport stop): there is no take.
                    nil;
                } {
                    ~ji_loop_count_sched[i - 1] = nil;
                    ~ji_loop_count_send.(i, 0);
                    seconds = ~ji_loop_take_seconds.(i);
                    ~ji_record_loop.(i, seconds);
                    ("[LOOP %] record start after count-in (% s)").format(
                        i, ~ji_fmtNum.(seconds, 2)
                    ).postln;
                    ~ji_ui_send_loop_state_later.(i);
                    ~ji_ui_send_loop_config.(i);
                    nil;
                };
            });
            ("[LOOP %] count-in % (% beats)").format(
                i, ~ji_loop_count_in_string.(~ji_loop_edit[i - 1][\countIn]), beats
            ).postln;
        };
    };

    // --- Stage 8.6 beat-synced granular clock -----------------------------
    //  Scheduled on ~ji_tempo_clock, never a sleep loop.  Each callback sends
    //  ONE incrementing grainTrig (a change fires exactly one grain); no log
    //  line is written per grain.  The next target is the previous target plus
    //  the division, so the interval can never become zero or negative.
    ~ji_loop_grain_schedule = { |index, target, generation|
        var i = index.asInteger.clip(1, 4);
        ~ji_loop_grain_sched[i - 1] = ~ji_tempo_clock.schedAbs(target, {
            var edit = ~ji_loop_edit[i - 1];
            var d = ~ji_loop_division_subdivision.(edit[\division]);
            var player = ~ji_loop_players[i - 1];

            if (~ji_loop_grain_gen[i - 1] == generation
                and: { ~ji_loop_states[i - 1] == \playing }
                and: { edit[\mode] == \grain }
                and: { edit[\sync] == \beat }
                and: { (~ji_rhythm_running ? false) }
                and: { player.notNil }) {
                ~ji_loop_grain_count[i - 1] = (~ji_loop_grain_count[i - 1] ? 0) + 1;
                player.set(\grainTrig, ~ji_loop_grain_count[i - 1]);
                ~ji_loop_grain_schedule.(i, target + d, generation);
            } {
                // Token expired or no longer eligible: the chain simply ends.
                // Do not clear the slot here -- a newer schedule may already
                // own it.
            };
            nil;
        });
    };

    ~ji_loop_grain_stop = { |index, log = true|
        var i = index.asInteger.clip(1, 4);
        var sched = ~ji_loop_grain_sched[i - 1];
        if (sched.notNil) {
            // Generation bump: the pending callback becomes a no-op.  Do not
            // .stop the object returned by schedAbs (it may be the clock).
            ~ji_loop_grain_gen[i - 1] = ~ji_loop_grain_gen[i - 1] + 1;
            ~ji_loop_grain_sched[i - 1] = nil;
            if (log) {
                ("[LOOP %] grain beat % stopped").format(
                    i, ~ji_loop_division_string.(~ji_loop_edit[i - 1][\division])
                ).postln;
            };
        };
    };

    ~ji_loop_grain_start = { |index|
        var i = index.asInteger.clip(1, 4);
        var edit = ~ji_loop_edit[i - 1];
        var d, position, target, generation;

        if (~ji_loop_grain_sched[i - 1].notNil) { ~ji_loop_grain_stop.(i, false) };
        if (edit[\mode] == \grain and: { edit[\sync] == \beat }
            and: { ~ji_loop_states[i - 1] == \playing }
            and: { ~ji_rhythm_running }
            and: { ~ji_loop_players[i - 1].notNil }) {
            d = ~ji_loop_division_subdivision.(edit[\division]);
            //  Stage 8.6.1: the beat-mode burst decays in about half the
            //  current division; recomputed here on every start/restart (so a
            //  tempo or division change reaches the voice).
            ~ji_loop_players[i - 1].set(\grainDecay, ~ji_loop_grain_decay.(i));
            position = ~ji_tempo_clock.beats - ~ji_beat_offset;
            target = ~ji_beat_offset + ((position / d).ceil * d);
            if (target <= ~ji_tempo_clock.beats) {
                target = ~ji_tempo_clock.beats + d;
            };
            // The voice was created with grainTrig = 0, so the first tick
            // sends 1 and always fires.
            ~ji_loop_grain_count[i - 1] = 0;
            generation = ~ji_loop_grain_gen[i - 1] + 1;
            ~ji_loop_grain_gen[i - 1] = generation;
            ~ji_loop_grain_schedule.(i, target, generation);
            ("[LOOP %] grain beat % started (burst decay % s)").format(
                i,
                ~ji_loop_division_string.(edit[\division]),
                ~ji_fmtNum.(~ji_loop_grain_decay.(i), 3)
            ).postln;
        };
    };

    ~ji_loop_grain_restart = { |index|
        var i = index.asInteger.clip(1, 4);
        var edit = ~ji_loop_edit[i - 1];
        if (~ji_loop_grain_sched[i - 1].notNil) { ~ji_loop_grain_stop.(i, false) };
        if (edit[\mode] == \grain and: { edit[\sync] == \beat }
            and: { ~ji_loop_states[i - 1] == \playing }) {
            ~ji_loop_grain_start.(i);
        };
    };

    ~ji_loop_grain_restart_all = {
        (1..4).do { |i| ~ji_loop_grain_restart.(i) };
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
                    // The record/play actions take the loop index.  Calling
                    // action.value with no argument made beat-mode record/play
                    // fire with index = nil and do nothing (Stage 8.1 bug fixed
                    // in 8.2.2).
                    action.value(index);
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
    //  Stage 8.6: a non-off count-in delays the take on the tempo clock; the
    //  buffer is untouched until the recorder actually starts.
    ~ji_record_loop_action = { |index|
        if (~ji_loop_edit[index - 1][\countIn] == \off) {
            ~ji_record_loop_now.(index);
        } {
            ~ji_count_in_start.(index);
        };
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
        var bpm, num, den, running, wasRunning, wasBpm;
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
                wasBpm = ~ji_rhythm_bpm;
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
                        // Stage 8.6: beat mode cancels a pending count-in; free
                        // mode keeps counting (free loops are not gated).
                        if (~ji_loop_states[index - 1] == \counting
                            and: { ~ji_loop_modes[index - 1] == \beat }) {
                            ~ji_cancel_count_in.(index, true, "transport stop");
                        };
                        if (~ji_loop_modes[index - 1] == \beat) {
                            if (~ji_loop_states[index - 1] == \recording) {
                                // No auto-play from a transport stop.
                                ~ji_stop_record.(index, false);
                            };
                            if (~ji_loop_states[index - 1] == \playing) {
                                ~ji_stop_loop.(index);
                            };
                            ~ji_ui_send_loop_state.(index);
                        };
                        // Stage 8.6: beat-synced grains are transport-gated even
                        // when the loop itself is in free mode.
                        if (~ji_loop_edit[index - 1][\sync] == \beat
                            and: { ~ji_loop_edit[index - 1][\mode] == \grain }) {
                            ~ji_loop_grain_stop.(index, false);
                            if (~ji_loop_states[index - 1] == \playing) {
                                ~ji_stop_loop.(index);
                            };
                            ("[LOOP %] grain beat % stopped (transport)").format(
                                index,
                                ~ji_loop_division_string.(~ji_loop_edit[index - 1][\division])
                            ).postln;
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
                // Stage 8.6: the next grain target is recomputed at the new
                // tempo (cancel and reschedule at the next boundary).
                if (~ji_rhythm_running) { ~ji_loop_grain_restart_all.value };
                // Stage 9.0: a FIXED-time FX follows the tempo.  Recreate only
                // when the BPM actually changed; free-time FX are untouched.
                if ((bpm - wasBpm).abs > 0.0001) {
                    (1..4).do { |index|
                        ~ji_loop_fx_retime !? { |retime| retime.(index) };
                    };
                };
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
                if (~ji_loop_states[index - 1] == \counting) {
                    // A second Rec skips the count-in and records now.
                    ("[LOOP %] count-in skipped (second Rec)").format(index).postln;
                    ~ji_cancel_count_in.(index, true);
                    ~ji_record_loop_now.(index);
                } {
                    if (mode == \beat) {
                        ~ji_schedule_loop_action.(index, ~ji_record_loop_action, "record");
                    } {
                        ~ji_record_loop_action.(index);
                    };
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
                if (~ji_loop_states[index - 1] == \counting) {
                    //  Stage 9.0 A1: the free-mode path lacked the beat path's
                    //  second-Rec branch.  Skip the count-in and record now in
                    //  BOTH modes (beat mode behaves exactly like
                    //  /ji/loop/record's second Rec).  The new free length is
                    //  remembered; beat mode keeps its beat length.
                    ("[LOOP %] count-in skipped (second Rec)").format(index).postln;
                    ~ji_cancel_count_in.(index, true);
                    //  Beat lengths come from ~ji_loop_beats, not seconds; only
                    //  the free path remembers the newly parsed length.
                    if (mode != \beat) {
                        ~ji_loop_seconds[index - 1] = seconds;
                        ~ji_loop_seconds_custom[index - 1] = true;
                    };
                    ~ji_record_loop_now.(index);
                } {
                    if (mode == \beat) {
                        ~ji_schedule_loop_action.(index, ~ji_record_loop_action, "record");
                    } {
                        // Free mode: remember this loop's length so
                        // /ji/loop/config reports what the UI is showing.
                        ~ji_loop_seconds[index - 1] = seconds;
                        ~ji_loop_seconds_custom[index - 1] = true;
                        ("[UI] loop % record length = % s").format(
                            index, seconds
                        ).postln;
                        ~ji_record_loop_action.(index);
                    };
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
                if (~ji_loop_states[index - 1] == \counting) {
                    ~ji_cancel_count_in.(index, true, "stop rec");
                } {
                    ~ji_stop_record.(index);
                    ~ji_ui_send_loop_state.(index);
                };
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
                // Stop is immediate, including a pending beat action or a
                // pending count-in (Stage 8.6: no take, so no auto-play).
                ~ji_cancel_loop_schedule.(index);
                if (~ji_loop_states[index - 1] == \counting) {
                    ~ji_cancel_count_in.(index, true, "stop");
                } {
                    ~ji_stop_loop.(index);
                    ~ji_ui_send_loop_state.(index);
                };
            };
        }, '/ji/loop/stop'),

        OSCdef(\ji_ui_loop_stop_all, { |msg|
            // NOTE: ~ji_stop_all also frees the live autotune synths and
            // stops the lookup/CPU routines (it is the whole-rig stop).  For
            // the UI's "Stop All Loops" button use the per-loop functions so
            // the live inputs and the rest of the rig keep running.
            ~ji_cancel_all_loop_schedules.value;
            (1..4).do { |index|
                if (~ji_loop_states[index - 1] == \counting) {
                    ~ji_cancel_count_in.(index, true, "stop all");
                } {
                    // applyAuto false: a stop-all never starts a loop.
                    ~ji_stop_record.(index, false);
                    ~ji_stop_loop.(index);
                };
            };
            (1..4).do { |index| ~ji_ui_send_loop_state.(index) };
        }, '/ji/loop/stop_all'),

        OSCdef(\ji_ui_loop_clear, { |msg|
            var index, buffer;
            index = ~ji_ui_loop_index.(msg);
            if (index.isNil) {
                "[UI] ignore /ji/loop/clear: index must be 1..4".postln;
            } {
                // Clear is immediate and cancels a pending beat action and a
                // pending count-in (Stage 8.6).
                ~ji_cancel_loop_schedule.(index);
                if (~ji_loop_states[index - 1] == \counting) {
                    ~ji_cancel_count_in.(index, true, "clear");
                };
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
        }, '/ji/loop/set_length'),

        // --- Stage 8.6 loop editing -------------------------------------
        OSCdef(\ji_ui_loop_dub, { |msg|
            var index;
            index = ~ji_ui_loop_index.(msg);
            if (index.isNil) {
                "[UI] ignore /ji/loop/dub: index must be 1..4".postln;
            } {
                ~ji_cancel_loop_schedule.(index);
                if (~ji_loop_states[index - 1] == \counting) {
                    ~ji_cancel_count_in.(index, true, "dub");
                };
                ~ji_dub_loop.(index);
                ~ji_ui_send_loop_state_later.(index);
            };
        }, '/ji/loop/dub'),

        OSCdef(\ji_ui_loop_edit_set, { |msg|
            var index, param, value;
            index = ~ji_ui_loop_index.(msg);
            if (index.isNil) {
                "[UI] ignore /ji/loop/edit_set: index must be 1..4".postln;
            } {
                if (msg.size < 4) {
                    "[UI] ignore /ji/loop/edit_set: expects index, param, value".postln;
                } {
                    param = msg[2].asString;
                    value = msg[3];
                    //  Stage 9.0 diagnostic: show the EXACT harm edit that
                    //  arrived (String vs Symbol vs number matters here).
                    if (param.beginsWith("harm_")) {
                        ("[UI] loop % harm edit % = % (%)").format(
                            index, param, value, value.class.asString
                        ).postln;
                    };
                    if (value.isNumber or: { value.isKindOf(String) }
                        or: { value.isKindOf(Symbol) }) {
                        ~ji_loop_set_edit.(index, param, value);
                    } {
                        ("[UI] ignore /ji/loop/edit_set: unknown param % value %").format(
                            param, value
                        ).postln;
                    };
                };
            };
        }, '/ji/loop/edit_set'),

        //  Stage 9.0: one-shot Cascade trigger.  UI -> sclang only; no reply
        //  (the existing /ji/loop/edit echo is the state, and a fire does not
        //  change it).  Delay mode and Harm off ignore it.
        OSCdef(\ji_ui_loop_fx_fire, { |msg|
            var index;
            index = ~ji_ui_loop_index.(msg);
            if (index.isNil) {
                "[UI] ignore /ji/loop/fx_fire: index must be 1..4".postln;
            } {
                if (~ji_loop_edit[index - 1][\harmMode] == \cascade
                    and: { ~ji_loop_fx_synths[index - 1].notNil }) {
                    ~ji_loop_fx_fire !? { |fire| fire.(index) };
                } {
                    ("[LOOP %] harm fire ignored (mode % / no FX)").format(
                        index, ~ji_loop_edit[index - 1][\harmMode].asString
                    ).postln;
                };
            };
        }, '/ji/loop/fx_fire'),

        OSCdef(\ji_ui_loop_mem_save, { |msg|
            var slot, index;
            slot = if (msg.size >= 2 and: { msg[1].isNumber }) {
                msg[1].asInteger
            } { -1 };
            index = if (msg.size >= 3 and: { msg[2].isNumber }) {
                msg[2].asInteger
            } { 0 };
            if (slot < 0 or: { slot > 3 }) {
                "[UI] ignore /ji/loop/mem_save: slot must be 0..3".postln;
            } {
                if (index < 1 or: { index > 4 }) {
                    "[UI] ignore /ji/loop/mem_save: index must be 1..4".postln;
                } {
                    ~ji_loop_mem_save.(slot, index);
                    ~ji_ui_send_loop_state.(index);
                };
            };
        }, '/ji/loop/mem_save'),

        OSCdef(\ji_ui_loop_mem_recall, { |msg|
            var slot, index;
            slot = if (msg.size >= 2 and: { msg[1].isNumber }) {
                msg[1].asInteger
            } { -1 };
            index = if (msg.size >= 3 and: { msg[2].isNumber }) {
                msg[2].asInteger
            } { 0 };
            if (slot < 0 or: { slot > 3 }) {
                "[UI] ignore /ji/loop/mem_recall: slot must be 0..3".postln;
            } {
                if (index < 1 or: { index > 4 }) {
                    "[UI] ignore /ji/loop/mem_recall: index must be 1..4".postln;
                } {
                    // Recall leaves the loop stopped; the human presses Play.
                    ~ji_cancel_loop_schedule.(index);
                    if (~ji_loop_states[index - 1] == \counting) {
                        ~ji_cancel_count_in.(index, true, "mem recall");
                    };
                    ~ji_loop_mem_recall.(slot, index);
                };
            };
        }, '/ji/loop/mem_recall'),

        OSCdef(\ji_ui_loop_mem_clear, { |msg|
            var slot;
            slot = if (msg.size >= 2 and: { msg[1].isNumber }) {
                msg[1].asInteger
            } { -1 };
            if (slot < 0 or: { slot > 3 }) {
                "[UI] ignore /ji/loop/mem_clear: slot must be 0..3".postln;
            } {
                ~ji_loop_mem_clear.(slot);
            };
        }, '/ji/loop/mem_clear')
    ];

    // --- Status query ----------------------------------------------------
    ~ji_ui_status_oscdef = OSCdef(\ji_ui_status, { |msg|
        // Immediate local snapshot, then a fresh read of the live Synth args.
        ~ji_ui_addr.sendMsg('/ji/cpu', s.avgCPU ? 0.0);
        ~ji_ui_send_autotune_state.(0);
        ~ji_ui_send_autotune_state.(1);
        (1..4).do { |index| ~ji_ui_send_loop_state.(index) };
        ~ji_ui_send_all_loop_configs.value;
        ~ji_ui_send_all_loop_edits !? { |send| send.value };
        ~ji_ui_send_all_loop_mem !? { |send| send.value };
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
    ~ji_ui_send_all_loop_edits !? { |send| send.value };
    ~ji_ui_send_all_loop_mem !? { |send| send.value };
    ~ji_ui_refresh_autotune.(0);
    ~ji_ui_refresh_autotune.(1);
    ("[UI] control surface % installed -> %:%; autotune + loop + rhythm + status OSC ready").format(
        ~ji_ui_control_surface_version, ~ji_ui_host, ~ji_ui_port
    ).postln;
};
)
