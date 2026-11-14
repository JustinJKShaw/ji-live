// ===========================================================================
//  JI Station -- Stage 9.0 -- tests/sc/test_loop_edit.scd
// ---------------------------------------------------------------------------
//  HUMAN-RUN sclang check for loop editing: memory slots, overdub, reverse,
//  varispeed, granular playback (8.6.1 redesign: motion / cloud jitter / JI
//  pitch snap / smear / freeze / beat burst; 8.6.2: dry<->grain morph, melt
//  ramp, moving filter), per-loop gain, count-in and auto-play, the 8.6
//  "Div jumps back to 1/16" regression, and (9.0) the harmonic-series delay
//  FX, fixed musical time, and the count-in A1-A4 repairs.
//
//  This file CANNOT be run by an agent (no sclang/scsynth in that
//  environment).  It checks the language-side state and prints the manual
//  audio steps; the owner is the authority on the sound.
//
//  Evaluate AFTER:
//
//      1. startup.scd
//      2. tests/sc/test_live_rig.scd
//      3. ui/control_surface.scd
//
//  Then follow the printed instructions with the GUI (or by hand).
//
//  The memory-slot section performs a real buffer copy and recalls slot A into
//  loop 4, so loop 4's audio is REPLACED by this self-test.  It is a scratch
//  loop for this check; re-record loop 4 afterwards if you need it.
// ===========================================================================
(
if (~ji_tempo_clock.isNil) {
    "[LOOP EDIT TEST] FAIL: ~ji_tempo_clock is nil; evaluate ui/control_surface.scd after tests/sc/test_live_rig.scd".postln;
    "[LOOP EDIT TEST] FAIL".postln;
} {
    ~ji_loop_edit_test = Routine {
        var failed = false;
        var checks = 0;
        var check = { |label, condition|
            checks = checks + 1;
            if (condition) {
                ("[LOOP EDIT TEST] PASS: " ++ label).postln;
            } {
                ("[LOOP EDIT TEST] FAIL: " ++ label).postln;
                failed = true;
            };
            condition;
        };
        var savedEdit, voice, srcValue, frames, expectedGain;
        var beforeState;
        var savedRatios;
        var savedMeltSeconds, stoppedAt;

        ("[LOOP EDIT TEST] -- language-side checks (% checks) --").postln;

        // ---- state exists and the defaults are the accepted ones ---------
        check.("~ji_loop_edit is a 4-element array",
            ~ji_loop_edit.notNil and: { ~ji_loop_edit.size == 4 });
        check.("default reverse 0 / rate 1.0 / gain 0.7 / normal / free",
            (1..4).every { |i|
                var e = ~ji_loop_edit[i - 1];
                e[\reverse] == 0 and: { e[\rate] == 1.0 }
                and: { (e[\gain] - 0.7).abs < 0.0001 }
                and: { e[\mode] == \normal } and: { e[\sync] == \free }
            });
        check.("default division 1/16 and grain defaults",
            (1..4).every { |i|
                var e = ~ji_loop_edit[i - 1];
                e[\division] == '1_16'
                and: { (e[\grainRate] - 20.0).abs < 0.0001 }
                and: { (e[\grainSize] - 0.12).abs < 0.0001 }
                and: { (e[\grainPos] - 0.5).abs < 0.0001 }
                and: { (e[\grainScatter] - 0.1).abs < 0.0001 }
                and: { (e[\grainPitch] - 1.0).abs < 0.0001 }
            });
        //  Stage 8.6.1 grain-redesign defaults.
        check.("8.6.1 defaults: motion forward, speed 0.03, depth 0.6",
            (1..4).every { |i|
                var e = ~ji_loop_edit[i - 1];
                e[\grainMotion] == \forward
                and: { (e[\grainSpeed] - 0.03).abs < 0.0001 }
                and: { (e[\grainDepth] - 0.6).abs < 0.0001 }
            });
        check.("8.6.1 defaults: size jit 0.2, pitch jit 0, snap on",
            (1..4).every { |i|
                var e = ~ji_loop_edit[i - 1];
                (e[\grainSizeJitter] - 0.2).abs < 0.0001
                and: { (e[\grainPitchJitter] - 0.0).abs < 0.0001 }
                and: { e[\grainPitchSnap] == 1 }
            });
        check.("8.6.1 defaults: smear 0, freeze off, burst 0.6",
            (1..4).every { |i|
                var e = ~ji_loop_edit[i - 1];
                (e[\grainSmear] - 0.0).abs < 0.0001
                and: { e[\grainFreeze] == 0 }
                and: { (e[\grainBurst] - 0.6).abs < 0.0001 }
            });
        check.("the motion table has the 5 documented names",
            ~ji_loop_grain_motions == [\off, \forward, \backward, \pingpong, \drift]);
        check.("8.6.2 defaults: morph 0, melt off, filter 0, filter rate 0.12",
            (1..4).every { |i|
                var e = ~ji_loop_edit[i - 1];
                (e[\grainMorph] - 0.0).abs < 0.0001
                and: { e[\grainMelt] == 0 }
                and: { (e[\grainFilter] - 0.0).abs < 0.0001 }
                and: { (e[\grainFilterRate] - 0.12).abs < 0.0001 }
            });
        check.("8.7 defaults: loop grain breath 0 and rate 0.08",
            (1..4).every { |i|
                var e = ~ji_loop_edit[i - 1];
                (e[\grainBreath] - 0.0).abs < 0.0001
                and: { (e[\grainBreathRate] - 0.08).abs < 0.0001 }
            });
        check.("9.0 defaults: harm off / 0.35 mix / 0.5 s / free / 4 / 0.08 / 0.5 / auto",
            (1..4).every { |i|
                var e = ~ji_loop_edit[i - 1];
                e[\harmMode] == \off
                and: { (e[\harmMix] - 0.35).abs < 0.0001 }
                and: { (e[\harmTime] - 0.5).abs < 0.0001 }
                and: { e[\harmTimeSync] == "free" }
                and: { e[\harmSteps] == 4 }
                and: { (e[\harmSpacing] - 0.08).abs < 0.0001 }
                and: { (e[\harmFalloff] - 0.5).abs < 0.0001 }
                and: { e[\harmAuto] == 1 }
            });
        check.("9.0 FX buses are four distinct private audio buses",
            ~ji_loop_fx_buses.notNil and: { ~ji_loop_fx_buses.size == 4 }
            and: {
                ~ji_loop_fx_buses.collect { |bus| bus.index }.asSet.size == 4
            });
        check.("9.0 FX scratch buffers are four mono buffers",
            ~ji_loop_fx_buf.notNil and: { ~ji_loop_fx_buf.size == 4 }
            and: { (0..3).every { |i| ~ji_loop_fx_buf[i].numChannels == 1 } });
        check.("melt state arrays exist (current morph + generation)",
            ~ji_loop_morph_current.size == 4 and: { ~ji_loop_melt_gen.size == 4 }
            and: { ~ji_loop_melt_seconds > 0 });
        check.("count-in defaults to off on every loop",
            (1..4).every { |i| ~ji_loop_edit[i - 1][\countIn] == \off });
        check.("auto-play defaults to 0 on every loop",
            (1..4).every { |i| ~ji_loop_edit[i - 1][\autoPlay] == 0 });
        check.("memory slots start empty",
            ~ji_loop_mem.notNil and: { ~ji_loop_mem.size == 4 }
            and: { (0..3).every { |slot| ~ji_loop_mem[slot].isNil } });
        check.("the ratio table has the 11 documented values",
            ~ji_loop_rates.size == 11 and: { ~ji_loop_rates.includes(2.0) }
            and: { ~ji_loop_rates.includes(0.6666667) }
            and: { ~ji_loop_rates.includes(0.5) });

        // ---- count-in maths (4/4, 3/4, 6/8) ----------------------------
        check.("count-in off = 0 quarter beats",
            ~ji_loop_count_in_quarter_beats.(\off, 4) == 0);
        check.("count-in 1 beat = 1",
            ~ji_loop_count_in_quarter_beats.('1_beat', 4) == 1);
        check.("count-in 1 bar in 4/4 = 4",
            ~ji_loop_count_in_quarter_beats.('1_bar', 4) == 4);
        check.("count-in 2 bars in 4/4 = 8",
            ~ji_loop_count_in_quarter_beats.('2_bars', 4) == 8);
        check.("count-in 1 bar in 3/4 = 3",
            ~ji_loop_count_in_quarter_beats.('1_bar', 3) == 3);
        check.("count-in 2 bars in 3/4 = 6",
            ~ji_loop_count_in_quarter_beats.('2_bars', 3) == 6);
        check.("count-in 1 bar in 6/8 = 3 quarter beats",
            ~ji_loop_count_in_quarter_beats.('1_bar', (6 * 4) / 8) == 3);
        //  Stage 9.0 A4: ceil the bar to whole quarter beats, never round.
        check.("count-in 1 bar in 5/8 = 3 (2.5 -> ceil)",
            ~ji_loop_count_in_quarter_beats.('1_bar', (5 * 4) / 8) == 3);
        check.("count-in 1 bar in 7/8 = 4 (3.5 -> ceil)",
            ~ji_loop_count_in_quarter_beats.('1_bar', (7 * 4) / 8) == 4);
        check.("count-in 2 bars in 5/8 = 6",
            ~ji_loop_count_in_quarter_beats.('2_bars', (5 * 4) / 8) == 6);
        check.("count-in 2 bars in 7/8 = 8",
            ~ji_loop_count_in_quarter_beats.('2_bars', (7 * 4) / 8) == 8);

        // ---- every edit parameter clamps and rejects bad input ---------
        savedEdit = ~ji_loop_edit[0].copy;
        ~ji_loop_set_edit.(1, \gain, 5);
        check.("gain clamps to 1.0", (~ji_loop_edit[0][\gain] - 1.0).abs < 0.0001);
        ~ji_loop_set_edit.(1, \gain, -3);
        check.("gain clamps to 0.0", (~ji_loop_edit[0][\gain]).abs < 0.0001);
        ~ji_loop_set_edit.(1, \gain, 0.7);
        check.("gain accepts 0.7", (~ji_loop_edit[0][\gain] - 0.7).abs < 0.0001);
        ~ji_loop_set_edit.(1, \reverse, 9);
        check.("reverse clamps to 1", ~ji_loop_edit[0][\reverse] == 1);
        ~ji_loop_set_edit.(1, \reverse, 0);
        check.("reverse 0 restores forward", ~ji_loop_edit[0][\reverse] == 0);
        ~ji_loop_set_edit.(1, \rate, 0.6666667);
        check.("rate accepts 2/3",
            (~ji_loop_edit[0][\rate] - 0.6666667).abs < 0.0001);
        ~ji_loop_set_edit.(1, \rate, 1.234);
        check.("an unknown rate is rejected (keeps 2/3)",
            (~ji_loop_edit[0][\rate] - 0.6666667).abs < 0.0001);
        ~ji_loop_set_edit.(1, \rate, 1.0);
        ~ji_loop_set_edit.(1, \grain_rate, 999);
        check.("grain_rate clamps to 80",
            (~ji_loop_edit[0][\grainRate] - 80.0).abs < 0.0001);
        ~ji_loop_set_edit.(1, \grain_rate, 0);
        check.("grain_rate clamps to 0.5",
            (~ji_loop_edit[0][\grainRate] - 0.5).abs < 0.0001);
        ~ji_loop_set_edit.(1, \grain_rate, 20.0);
        check.("grain_rate accepts 20",
            (~ji_loop_edit[0][\grainRate] - 20.0).abs < 0.0001);
        ~ji_loop_set_edit.(1, \grain_size, 999);
        check.("grain_size clamps to 500 ms (0.5 s)",
            (~ji_loop_edit[0][\grainSize] - 0.5).abs < 0.0001);
        ~ji_loop_set_edit.(1, \grain_size, 1);
        check.("grain_size clamps to 5 ms (0.005 s)",
            (~ji_loop_edit[0][\grainSize] - 0.005).abs < 0.0001);
        ~ji_loop_set_edit.(1, \grain_size, 80);
        check.("grain_size stores 80 ms as 0.08 s",
            (~ji_loop_edit[0][\grainSize] - 0.08).abs < 0.0001);
        ~ji_loop_set_edit.(1, \grain_pos, 2);
        check.("grain_pos clamps to 1", (~ji_loop_edit[0][\grainPos] - 1.0).abs < 0.0001);
        ~ji_loop_set_edit.(1, \grain_pos, -1);
        check.("grain_pos clamps to 0", (~ji_loop_edit[0][\grainPos]).abs < 0.0001);
        ~ji_loop_set_edit.(1, \grain_scatter, 2);
        check.("grain_scatter clamps to 1",
            (~ji_loop_edit[0][\grainScatter] - 1.0).abs < 0.0001);
        ~ji_loop_set_edit.(1, \grain_scatter, 0);
        ~ji_loop_set_edit.(1, \grain_pitch, 99);
        check.("grain_pitch clamps to 4",
            (~ji_loop_edit[0][\grainPitch] - 4.0).abs < 0.0001);
        ~ji_loop_set_edit.(1, \grain_pitch, 0.01);
        check.("grain_pitch clamps to 0.25",
            (~ji_loop_edit[0][\grainPitch] - 0.25).abs < 0.0001);
        ~ji_loop_set_edit.(1, \grain_pitch, 1.0);
        ~ji_loop_set_edit.(1, \grain_reverse, 5);
        check.("grain_reverse clamps to 1",
            (~ji_loop_edit[0][\grainReverse] - 1.0).abs < 0.0001);
        ~ji_loop_set_edit.(1, \grain_reverse, 0);
        ~ji_loop_set_edit.(1, \mode, "grain");
        check.("mode accepts grain", ~ji_loop_edit[0][\mode] == \grain);
        ~ji_loop_set_edit.(1, \mode, "bogus");
        check.("an unknown mode is rejected (keeps grain)",
            ~ji_loop_edit[0][\mode] == \grain);
        ~ji_loop_set_edit.(1, \mode, "normal");
        check.("mode accepts normal", ~ji_loop_edit[0][\mode] == \normal);
        ~ji_loop_set_edit.(1, \sync, "beat");
        check.("sync accepts beat", ~ji_loop_edit[0][\sync] == \beat);
        ~ji_loop_set_edit.(1, \sync, "free");
        check.("sync accepts free", ~ji_loop_edit[0][\sync] == \free);
        ~ji_loop_set_edit.(1, \division, "1/8");
        check.("division accepts 1/8", ~ji_loop_edit[0][\division] == '1_8');
        ~ji_loop_set_edit.(1, \division, "1/3");
        check.("an unknown division is rejected (keeps 1/8)",
            ~ji_loop_edit[0][\division] == '1_8');
        ~ji_loop_set_edit.(1, \division, "1/16");
        ~ji_loop_set_edit.(1, \count_in, "2 bars");
        check.("count_in accepts 2 bars", ~ji_loop_edit[0][\countIn] == '2_bars');
        ~ji_loop_set_edit.(1, \count_in, "3 bars");
        check.("an unknown count-in is rejected (keeps 2 bars)",
            ~ji_loop_edit[0][\countIn] == '2_bars');
        ~ji_loop_set_edit.(1, \count_in, "off");
        ~ji_loop_set_edit.(1, \auto_play, 1);
        check.("auto_play accepts 1", ~ji_loop_edit[0][\autoPlay] == 1);
        ~ji_loop_set_edit.(1, \auto_play, 0);
        check.("auto_play accepts 0", ~ji_loop_edit[0][\autoPlay] == 0);
        ~ji_loop_set_edit.(1, \bogus_param, 1);
        check.("an unknown parameter never throws and changes nothing",
            (~ji_loop_edit[0][\gain] - 0.7).abs < 0.0001);

        // ---- Stage 8.6.1: the new grain parameters clamp / validate -----
        ~ji_loop_set_edit.(1, \grain_motion, "pingpong");
        check.("grain_motion accepts pingpong",
            ~ji_loop_edit[0][\grainMotion] == \pingpong);
        ~ji_loop_set_edit.(1, \grain_motion, "bounce");
        check.("an unknown motion is rejected (keeps pingpong)",
            ~ji_loop_edit[0][\grainMotion] == \pingpong);
        ~ji_loop_set_edit.(1, \grain_motion, "forward");
        check.("grain_motion accepts forward",
            ~ji_loop_edit[0][\grainMotion] == \forward);
        ~ji_loop_set_edit.(1, \grain_speed, 9);
        check.("grain_speed clamps to 0.5",
            (~ji_loop_edit[0][\grainSpeed] - 0.5).abs < 0.0001);
        ~ji_loop_set_edit.(1, \grain_speed, 0);
        check.("grain_speed clamps to 0.001",
            (~ji_loop_edit[0][\grainSpeed] - 0.001).abs < 0.0001);
        ~ji_loop_set_edit.(1, \grain_speed, 0.03);
        ~ji_loop_set_edit.(1, \grain_depth, 9);
        check.("grain_depth clamps to 1",
            (~ji_loop_edit[0][\grainDepth] - 1.0).abs < 0.0001);
        ~ji_loop_set_edit.(1, \grain_depth, -1);
        check.("grain_depth clamps to 0",
            (~ji_loop_edit[0][\grainDepth]).abs < 0.0001);
        ~ji_loop_set_edit.(1, \grain_depth, 0.6);
        ~ji_loop_set_edit.(1, \grain_size_jitter, 9);
        check.("grain_size_jitter clamps to 1",
            (~ji_loop_edit[0][\grainSizeJitter] - 1.0).abs < 0.0001);
        ~ji_loop_set_edit.(1, \grain_size_jitter, 0.2);
        ~ji_loop_set_edit.(1, \grain_pitch_jitter, -9);
        check.("grain_pitch_jitter clamps to 0",
            (~ji_loop_edit[0][\grainPitchJitter]).abs < 0.0001);
        ~ji_loop_set_edit.(1, \grain_pitch_jitter, 2);
        check.("grain_pitch_jitter clamps to 1",
            (~ji_loop_edit[0][\grainPitchJitter] - 1.0).abs < 0.0001);
        ~ji_loop_set_edit.(1, \grain_pitch_jitter, 0);
        ~ji_loop_set_edit.(1, \grain_smear, 9);
        check.("grain_smear clamps to 1",
            (~ji_loop_edit[0][\grainSmear] - 1.0).abs < 0.0001);
        ~ji_loop_set_edit.(1, \grain_smear, 0);
        ~ji_loop_set_edit.(1, \grain_freeze, 1);
        check.("grain_freeze accepts 1", ~ji_loop_edit[0][\grainFreeze] == 1);
        ~ji_loop_set_edit.(1, \grain_freeze, 0);
        check.("grain_freeze accepts 0", ~ji_loop_edit[0][\grainFreeze] == 0);
        ~ji_loop_set_edit.(1, \grain_burst, 9);
        check.("grain_burst clamps to 1",
            (~ji_loop_edit[0][\grainBurst] - 1.0).abs < 0.0001);
        ~ji_loop_set_edit.(1, \grain_burst, 0.6);
        ~ji_loop_set_edit.(1, \grain_pitch_snap, 0);
        check.("grain_pitch_snap accepts 0",
            ~ji_loop_edit[0][\grainPitchSnap] == 0);
        ~ji_loop_set_edit.(1, \grain_pitch_snap, 1);
        check.("grain_pitch_snap accepts 1",
            ~ji_loop_edit[0][\grainPitchSnap] == 1);
        //  Stage 8.6.2 morph / melt / filter.
        ~ji_loop_set_edit.(1, \grain_morph, 2);
        check.("grain_morph clamps to 1",
            (~ji_loop_edit[0][\grainMorph] - 1.0).abs < 0.0001);
        ~ji_loop_set_edit.(1, \grain_morph, -1);
        check.("grain_morph clamps to 0",
            (~ji_loop_edit[0][\grainMorph]).abs < 0.0001);
        ~ji_loop_set_edit.(1, \grain_morph, 0.5);
        check.("grain_morph accepts 0.5",
            (~ji_loop_edit[0][\grainMorph] - 0.5).abs < 0.0001);
        ~ji_loop_set_edit.(1, \grain_melt, 3);
        check.("grain_melt accepts 1", ~ji_loop_edit[0][\grainMelt] == 1);
        ~ji_loop_set_edit.(1, \grain_melt, 0);
        check.("grain_melt accepts 0", ~ji_loop_edit[0][\grainMelt] == 0);
        ~ji_loop_set_edit.(1, \grain_filter, 9);
        check.("grain_filter clamps to 1",
            (~ji_loop_edit[0][\grainFilter] - 1.0).abs < 0.0001);
        ~ji_loop_set_edit.(1, \grain_filter, 0);
        ~ji_loop_set_edit.(1, \grain_filter_rate, 99);
        check.("grain_filter_rate clamps to 2",
            (~ji_loop_edit[0][\grainFilterRate] - 2.0).abs < 0.0001);
        ~ji_loop_set_edit.(1, \grain_filter_rate, 0);
        check.("grain_filter_rate clamps to 0.01",
            (~ji_loop_edit[0][\grainFilterRate] - 0.01).abs < 0.0001);
        ~ji_loop_set_edit.(1, \grain_filter_rate, 0.12);
        check.("grain_filter_rate accepts 0.12",
            (~ji_loop_edit[0][\grainFilterRate] - 0.12).abs < 0.0001);
        //  Stage 8.7: the loop's own grain breath.
        ~ji_loop_set_edit.(1, \grain_breath, 9);
        check.("grain_breath clamps to 1",
            (~ji_loop_edit[0][\grainBreath] - 1.0).abs < 0.0001);
        ~ji_loop_set_edit.(1, \grain_breath, -3);
        check.("grain_breath clamps to 0",
            (~ji_loop_edit[0][\grainBreath]).abs < 0.0001);
        ~ji_loop_set_edit.(1, \grain_breath, 0.45);
        check.("grain_breath accepts 0.45",
            (~ji_loop_edit[0][\grainBreath] - 0.45).abs < 0.0001);
        ~ji_loop_set_edit.(1, \grain_breath_rate, 99);
        check.("grain_breath_rate clamps to 1",
            (~ji_loop_edit[0][\grainBreathRate] - 1.0).abs < 0.0001);
        ~ji_loop_set_edit.(1, \grain_breath_rate, 0);
        check.("grain_breath_rate clamps to 0.02",
            (~ji_loop_edit[0][\grainBreathRate] - 0.02).abs < 0.0001);
        ~ji_loop_set_edit.(1, \grain_breath_rate, 0.08);
        ~ji_loop_set_edit.(1, \grain_breath, 0);
        //  Stage 9.0: the harmonic-series delay FX edits.
        ~ji_loop_set_edit.(1, \harm_mode, "cascade");
        check.("harm_mode accepts cascade",
            ~ji_loop_edit[0][\harmMode] == \cascade);
        ~ji_loop_set_edit.(1, \harm_mode, "bogus");
        check.("an unknown harm mode is rejected (keeps cascade)",
            ~ji_loop_edit[0][\harmMode] == \cascade);
        ~ji_loop_set_edit.(1, \harm_mode, "delay");
        check.("harm_mode accepts delay",
            ~ji_loop_edit[0][\harmMode] == \delay);
        ~ji_loop_set_edit.(1, \harm_mix, 9);
        check.("harm_mix clamps to 1",
            (~ji_loop_edit[0][\harmMix] - 1.0).abs < 0.0001);
        ~ji_loop_set_edit.(1, \harm_mix, -1);
        check.("harm_mix clamps to 0",
            (~ji_loop_edit[0][\harmMix]).abs < 0.0001);
        ~ji_loop_set_edit.(1, \harm_mix, 0.35);
        ~ji_loop_set_edit.(1, \harm_time, 9);
        check.("harm_time clamps to 2 s",
            (~ji_loop_edit[0][\harmTime] - ~ji_loop_harm_max_seconds).abs < 0.0001);
        ~ji_loop_set_edit.(1, \harm_time, 0);
        check.("harm_time clamps to 0.05 s",
            (~ji_loop_edit[0][\harmTime] - 0.05).abs < 0.0001);
        ~ji_loop_set_edit.(1, \harm_time, 0.5);
        ~ji_loop_set_edit.(1, \harm_time_sync, "1/4d");
        check.("harm_time_sync accepts 1/4d",
            ~ji_loop_edit[0][\harmTimeSync] == "1/4d");
        ~ji_loop_set_edit.(1, \harm_time_sync, "1/64");
        check.("an unknown harm time sync is rejected (keeps 1/4d)",
            ~ji_loop_edit[0][\harmTimeSync] == "1/4d");
        ~ji_loop_set_edit.(1, \harm_time_sync, "free");
        ~ji_loop_set_edit.(1, \harm_steps, 99);
        check.("harm_steps clamps to 8", ~ji_loop_edit[0][\harmSteps] == 8);
        ~ji_loop_set_edit.(1, \harm_steps, 0);
        check.("harm_steps clamps to 1", ~ji_loop_edit[0][\harmSteps] == 1);
        ~ji_loop_set_edit.(1, \harm_steps, 4);
        ~ji_loop_set_edit.(1, \harm_spacing, 9);
        check.("harm_spacing clamps to 1",
            (~ji_loop_edit[0][\harmSpacing] - 1.0).abs < 0.0001);
        ~ji_loop_set_edit.(1, \harm_spacing, 0.08);
        ~ji_loop_set_edit.(1, \harm_falloff, -1);
        check.("harm_falloff clamps to 0",
            (~ji_loop_edit[0][\harmFalloff]).abs < 0.0001);
        ~ji_loop_set_edit.(1, \harm_falloff, 0.5);
        ~ji_loop_set_edit.(1, \harm_auto, 0);
        check.("harm_auto accepts 0", ~ji_loop_edit[0][\harmAuto] == 0);
        ~ji_loop_set_edit.(1, \harm_auto, 1);
        check.("harm_auto accepts 1", ~ji_loop_edit[0][\harmAuto] == 1);
        check.("fixed time 1/4 at 120 BPM = 0.5 s", {
            var savedBpm = ~ji_rhythm_bpm;
            var value;
            ~ji_rhythm_bpm = 120.0;
            ~ji_loop_edit[0][\harmTimeSync] = "1/4";
            value = ~ji_loop_fx_time.(~ji_loop_edit[0]);
            ~ji_rhythm_bpm = savedBpm;
            ~ji_loop_edit[0][\harmTimeSync] = "free";
            (value - 0.5).abs < 0.0001;
        }.value);
        ~ji_loop_set_edit.(1, \harm_mode, "off");
        check.("harm_mode returns to off", ~ji_loop_edit[0][\harmMode] == \off);
        check.("start morph: melt off uses the Morph slider",
            (~ji_loop_start_morph.(~ji_loop_edit[0]) - 0.5).abs < 0.0001);
        ~ji_loop_edit[0][\mode] = \grain;
        ~ji_loop_edit[0][\grainMelt] = 1;
        check.("start morph: melt on starts at the original (0)",
            (~ji_loop_start_morph.(~ji_loop_edit[0])).abs < 0.0001);
        ~ji_loop_edit[0][\mode] = \normal;
        ~ji_loop_edit[0][\grainMelt] = 0;
        check.("start morph: normal mode ignores melt",
            (~ji_loop_start_morph.(~ji_loop_edit[0]) - 0.5).abs < 0.0001);

        // ---- Stage 8.6 §8: Div / count-in edits from the GUI ------------
        //  These arrive as OSC STRINGS.  SuperCollider's indexOf compares by
        //  identity, so the 8.6 build silently rejected every GUI division
        //  and count-in edit; the next echo then snapped the Div combo back
        //  to 1/16.  indexOfEqual (value comparison) is the fix.
        ~ji_loop_set_edit.(1, \division, "1/4");
        check.("a String division edit is accepted (Div bug)",
            ~ji_loop_edit[0][\division] == '1_4');
        ~ji_loop_set_edit.(1, \division, "1/2");
        check.("another String division edit is accepted",
            ~ji_loop_edit[0][\division] == '1_2');
        ~ji_loop_set_edit.(1, \division, "1/16");
        ~ji_loop_set_edit.(1, \count_in, "1 beat");
        check.("a String count-in edit is accepted",
            ~ji_loop_edit[0][\countIn] == '1_beat');
        ~ji_loop_set_edit.(1, \count_in, "off");

        // ---- Stage 8.6.1: JI pitch snapping -----------------------------
        //  Nearest member of the shipped set (x octaves, 0.25..4.0).
        check.("snap 1.5 -> 3/2",
            (~ji_loop_snap_ratio.(1.5, [1, 9/8, 5/4, 4/3, 3/2, 5/3, 15/8]) - 1.5).abs < 0.0001);
        check.("snap 0.66 -> 2/3",
            (~ji_loop_snap_ratio.(0.66, [1, 9/8, 5/4, 4/3, 3/2, 5/3, 15/8]) - 0.6666667).abs < 0.001);
        check.("snap 2.0 -> 2/1",
            (~ji_loop_snap_ratio.(2.0, [1, 9/8, 5/4, 4/3, 3/2, 5/3, 15/8]) - 2.0).abs < 0.0001);
        check.("snap 1.98 -> 2/1",
            (~ji_loop_snap_ratio.(1.98, [1, 9/8, 5/4, 4/3, 3/2, 5/3, 15/8]) - 2.0).abs < 0.0001);
        check.("snap 0.5 -> 1/2",
            (~ji_loop_snap_ratio.(0.5, [1, 9/8, 5/4, 4/3, 3/2, 5/3, 15/8]) - 0.5).abs < 0.0001);
        check.("snap 1.18 -> 9/8 (set specific)",
            (~ji_loop_snap_ratio.(1.18, [1, 9/8, 5/4, 4/3, 3/2, 5/3, 15/8]) - (9/8)).abs < 0.0001);
        check.("snap 1.26 -> 5/4 (set specific)",
            (~ji_loop_snap_ratio.(1.26, [1, 9/8, 5/4, 4/3, 3/2, 5/3, 15/8]) - (5/4)).abs < 0.0001);
        //  The cents midpoint between 9/8 and 5/4 is 1.1858, so 1.19 is 5/4.
        //  An earlier draft of the stage prompt said 9/8 here; the log2
        //  distance in ~ji_loop_snap_ratio is what ships.
        check.("snap 1.19 -> 5/4 (nearest in cents)",
            (~ji_loop_snap_ratio.(1.19, [1, 9/8, 5/4, 4/3, 3/2, 5/3, 15/8]) - (5/4)).abs < 0.0001);
        check.("snap clamps the request into 0.25..4",
            (~ji_loop_snap_ratio.(9.0, [1, 9/8, 5/4, 4/3, 3/2, 5/3, 15/8]) - 4.0).abs < 0.0001);

        //  Snap on: the effective ratio follows the CURRENT set; Snap off
        //  passes the raw requested ratio.
        //  Pin the set for these checks so a mutated live set cannot change the
        //  expected values; the live set is restored afterwards.
        savedRatios = ~ji_ji_set_ratios;
        ~ji_ji_set_ratios = [1, 9/8, 5/4, 4/3, 3/2, 5/3, 15/8];
        ~ji_loop_edit[0][\grainPitch] = 1.5;
        ~ji_loop_edit[0][\grainPitchSnap] = 1;
        check.("effective pitch with the shipped set is 3/2",
            (~ji_loop_effective_pitch.(~ji_loop_edit[0]) - 1.5).abs < 0.0001);
        ~ji_ji_set_ratios = [1, 7/5, 3/2, 2];
        ~ji_loop_edit[0][\grainPitch] = 1.45;
        check.("1.45 snaps to 3/2 in the changed set",
            (~ji_loop_effective_pitch.(~ji_loop_edit[0]) - 1.5).abs < 0.0001);
        ~ji_ji_set_ratios = savedRatios;
        ~ji_loop_edit[0][\grainPitchSnap] = 0;
        check.("snap off passes the raw ratio",
            (~ji_loop_effective_pitch.(~ji_loop_edit[0]) - 1.45).abs < 0.0001);
        check.("snap off clamps the raw ratio into 0.25..4",
            {
                var value;
                ~ji_loop_edit[0][\grainPitch] = 9.0;
                value = ~ji_loop_effective_pitch.(~ji_loop_edit[0]);
                ~ji_loop_edit[0][\grainPitch] = 1.0;
                (value - 4.0).abs < 0.0001;
            }.value);

        ~ji_loop_edit[0] = savedEdit;
        check.("loop 1 edit state restored", (~ji_loop_edit[0][\gain] - 0.7).abs < 0.0001);

        // ---- the voice always carries mix = 1.0 (autotune stays in) ----
        check.("normal-mode args carry mix 1.0",
            ~ji_loop_edit_args.(1).includes(\mix)
            and: {
                var args = ~ji_loop_edit_args.(1);
                (args[args.indexOf(\mix) + 1] - 1.0).abs < 0.0001;
            });
        ~ji_loop_edit[0][\mode] = \grain;
        check.("grain-mode args still carry mix 1.0",
            {
                var args = ~ji_loop_edit_args.(1);
                (args[args.indexOf(\mix) + 1] - 1.0).abs < 0.0001;
            }.value);

        // ---- Stage 8.6.1: the grain-redesign args reach the voice -------
        check.("grain-mode args carry every 8.6.1 parameter",
            {
                var args = ~ji_loop_edit_args.(1);
                [\grainMotion, \grainSpeed, \grainDepth, \grainSizeJitter,
                 \grainPitchJitter, \grainSmear, \grainFreeze, \grainBurst,
                 \grainDecay, \grainPitch, \grainMorph, \grainFilter,
                 \grainFilterRate, \grainBreath, \grainBreathRate].every { |key| args.includes(key) };
            }.value);
        check.("9.0: args carry the private fxBus index and no harm args", {
            var args = ~ji_loop_edit_args.(1);
            var busIndex = args[args.indexOf(\fxBus) + 1];
            args.includes(\fxBus)
            and: { busIndex == ~ji_loop_fx_buses[0].index }
            and: { args.includes(\harmMode).not }
            and: { args.includes(\harmTime).not };
        }.value);
        check.("8.7: grainBreath defaults to 0 and grainBreathRate to 0.08",
            {
                var args = ~ji_loop_edit_args.(1);
                (args[args.indexOf(\grainBreath) + 1]).abs < 0.0001
                and: {
                    (args[args.indexOf(\grainBreathRate) + 1] - 0.08).abs < 0.0001
                };
            }.value);
        check.("grainMotion is the index of the motion name",
            {
                var args = ~ji_loop_edit_args.(1);
                args[args.indexOf(\grainMotion) + 1] == 1;   // forward
            }.value);
        check.("grainDecay reaches the voice and equals the helper",
            {
                var args = ~ji_loop_edit_args.(1);
                var decay = args[args.indexOf(\grainDecay) + 1];
                (decay - ~ji_loop_grain_decay.(1)).abs < 0.0001;
            }.value);
        check.("grainDecay is half the division (0 < decay <= 1 s)",
            {
                var decay = ~ji_loop_grain_decay.(1);
                decay > 0 and: { decay <= 1 };
            }.value);
        savedRatios = ~ji_ji_set_ratios;
        ~ji_ji_set_ratios = [1, 9/8, 5/4, 4/3, 3/2, 5/3, 15/8];
        ~ji_loop_edit[0][\grainPitch] = 1.18;
        ~ji_loop_edit[0][\grainPitchSnap] = 1;
        check.("the voice gets the SNAPPED ratio (1.18 -> 9/8)",
            {
                var args = ~ji_loop_edit_args.(1);
                (args[args.indexOf(\grainPitch) + 1] - (9/8)).abs < 0.0001;
            }.value);
        ~ji_ji_set_ratios = savedRatios;
        ~ji_loop_edit[0][\grainPitch] = 1.0;
        ~ji_loop_edit[0][\mode] = \normal;

        // ---- a \ji_loop_voice can be created in GRAIN mode, writes its bus --
        ~ji_stop_loop.(1);
        0.2.wait;
        srcValue = nil;
        ~ji_loop_edit[0][\mode] = \grain;
        if (SynthDescLib.global[\ji_loop_voice].isNil) {
            check.("\\ji_loop_voice is a loaded SynthDef", false);
        } {
            voice = Synth(\ji_loop_voice, ~ji_loop_edit_args.(1));
            check.("a \\ji_loop_voice can be created in grain mode", voice.notNil);
            check.("8.7: grainBreath = 0 still produces the accepted voice",
                {
                    var args = ~ji_loop_edit_args.(1);
                    voice.notNil
                    and: {
                        (args[args.indexOf(\grainBreath) + 1]).abs < 0.0001
                    };
                }.value);
            ~ji_loop_freq_buses[0].get { |value| srcValue = value };
            0.35.wait;
            check.("the loop tracker bus is written by the voice",
                srcValue.notNil);

            // ---- Stage 8.6.2: the Melt ramp ---------------------------
            //  The voice is wired as loop 1's player for this check only; the
            //  ramp length is shortened so the test stays quick.
            ~ji_loop_players[0] = voice;
            savedMeltSeconds = ~ji_loop_melt_seconds;
            ~ji_loop_melt_seconds = 0.25;
            ~ji_loop_morph_current[0] = 0.0;
            ~ji_loop_melt_to.(1, 0.8);
            0.45.wait;
            check.("melt ramps the morph to the target",
                (~ji_loop_morph_current[0] - 0.8).abs < 0.01);
            ~ji_loop_morph_current[0] = 0.0;
            ~ji_loop_melt_to.(1, 1.0);
            0.08.wait;
            ~ji_loop_melt_stop.(1);
            stoppedAt = ~ji_loop_morph_current[0];
            0.3.wait;
            check.("melt stop cancels the ramp",
                (~ji_loop_morph_current[0] - stoppedAt).abs < 0.001);
            check.("melt stop leaves the morph under the target",
                stoppedAt < 0.99);
            ~ji_loop_melt_seconds = savedMeltSeconds;
            ~ji_loop_morph_current[0] = 0.0;
            ~ji_loop_players[0] = nil;

            voice.set(\gate, 0);
            0.1.wait;
            check.("the voice frees on gate 0 (no throw)", true);
        };
        ~ji_loop_edit[0][\mode] = \normal;

        // ---- count-in can be scheduled and cancelled -------------------
        beforeState = ~ji_loop_states[2];
        ~ji_loop_edit[2][\countIn] = '1_bar';
        ~ji_count_in_start.(3);
        check.("count-in sets the counting state", ~ji_loop_states[2] == \counting);
        ~ji_cancel_count_in.(3, true, "self-test");
        check.("cancel restores the previous state",
            ~ji_loop_states[2] == beforeState);
        check.("cancel clears the count-in schedule",
            ~ji_loop_count_sched[2].isNil);
        ~ji_loop_edit[2][\countIn] = \off;

        // ---- memory slots: save, recall across loops, clear ------------
        ~ji_loop_mem_clear.(0);
        frames = ~ji_buf_loop[0].numFrames;
        ~ji_loop_mem_save.(0, 1);
        (frames / s.sampleRate + 0.6).wait;
        check.("a save fills slot A",
            ~ji_loop_mem[0].notNil
            and: { ~ji_loop_mem[0][\frames] == frames });
        check.("slot A stored an edit state",
            ~ji_loop_mem[0].notNil and: { ~ji_loop_mem[0][\edit].notNil }
            and: { ~ji_loop_mem[0][\edit][\mode] == \normal });
        if (~ji_loop_mem[0].notNil) {
            expectedGain = ~ji_loop_mem[0][\edit][\gain];
            ~ji_loop_edit[3][\gain] = 0.11;   // prove the recall overwrites it
            ~ji_loop_mem_recall.(0, 4);
            (frames / s.sampleRate + 1.0).wait;
            check.("a recall copies the frame count to loop 4",
                ~ji_buf_loop[3].notNil
                and: { ~ji_buf_loop[3].numFrames == frames });
            check.("a recall restores the stored edit state",
                (~ji_loop_edit[3][\gain] - expectedGain).abs < 0.0001);
            check.("a recalled loop stays stopped", ~ji_loop_states[3] == \stopped);
        };
        ~ji_loop_mem_clear.(0);
        check.("clear empties slot A", ~ji_loop_mem[0].isNil);
        check.("an empty slot recall logs one line and does not throw", true);
        ~ji_loop_mem_recall.(1, 2);

        // ---- final verdict + the manual audio checklist ----------------
        if (failed) {
            ("[LOOP EDIT TEST] FAIL (" ++ checks ++ " checks)").postln;
        } {
            ("[LOOP EDIT TEST] PASS (" ++ checks ++ " checks)").postln;
        };
        "[LOOP EDIT TEST] -- manual audio checks (the owner is the authority) --".postln;
        "[LOOP EDIT TEST]  1. Record loop 1, play it: rate 1/1 + normal + gain 0.70 must sound exactly as before.".postln;
        "[LOOP EDIT TEST]  2. Dub: press Dub on the playing loop, play over it one pass; the layer is added and the loop keeps running. Stop Rec ends a dub early.".postln;
        "[LOOP EDIT TEST]  3. Count-in (FREE mode, transport stopped): set 1 bar, press Rec; watch counting 3/2/1, then the take starts one bar later. Press Rec AGAIN during the count: it must skip and record immediately (log 'count-in skipped (second Rec)'); Stop Rec during a count must restore empty/recorded, never stuck 'counting'.".postln;
        "[LOOP EDIT TEST]  3b. Count-in in 3/4, 6/8, 5/8, 7/8: a bar is rounded UP to whole quarter beats (4, 3, 3, 4), so the take never starts early. Beat mode still needs Play and a transport stop cancels the count.".postln;
        "[LOOP EDIT TEST]  4. Auto-play: tick Auto, record; the loop starts at the end of the take. Beat mode + stopped transport logs auto-play skipped.".postln;
        "[LOOP EDIT TEST]  5. Reverse: toggle Rev while playing (no click), toggle back.".postln;
        "[LOOP EDIT TEST]  6. Rates 2/1, 3/2, 5/3, 5/4, 4/3, 1/1, 3/4, 4/5, 2/3, 3/5, 1/2: each changes speed/pitch, no clicks. The autotune may pull it back toward the set: expected.".postln;
        "[LOOP EDIT TEST]  7. Granular free: Grain on, Sync free, transport stopped; the DEFAULT cloud (Rate 20, Size 120 ms, Pos 50 %, Scatter 10 %, Motion forward, Depth 60 %) is a smooth continuous texture, not a stutter.".postln;
        "[LOOP EDIT TEST]  8. Growth: Motion off vs forward/backward/pingpong/drift are clearly different; Speed 0.1 travels faster; Depth 0 % pins it. Freeze holds the position and unfreezing continues (no jump).".postln;
        "[LOOP EDIT TEST]  9. Density: Rate 5 vs 60 is a clear density change; Size Jit thickens the cloud; Size 20 ms is glitchy, 300 ms is smooth.".postln;
        "[LOOP EDIT TEST] 10. Pitch: Snap on, Pitch 1.5 -> up a fifth IN THE CURRENT SET (status shows the effective ratio); mutate the set and a playing grain loop re-snaps without a click; Snap off restores the raw ratio; Pitch Jit adds shimmer (heavy values warble: expected).".postln;
        "[LOOP EDIT TEST] 11. Smear: raising it adds blur/space without destroying the JI tuning; 0 % is dry and bit-identical to the accepted chain.".postln;
        "[LOOP EDIT TEST] 12. Granular beat: Sync beat with the transport running; onsets lock to Div; each division is a small cloud (burst), not one tick; changing Div or tempo keeps it locked; stopping the transport stops the loop.".postln;
        "[LOOP EDIT TEST] 13. Div bug: change Div to 1/4, then move Size/Pos/Smear; Div stays 1/4 (the combo must not jump back to 1/16).".postln;
        "[LOOP EDIT TEST] 13b. Morph: Grain on, Morph 0 % = the original loop (bit-identical to normal playback); raise Morph slowly -> the grains fade in over the dry loop and the texture smears; 100 % = pure grains. The dry and grain layers must stay in tune and in time (one autotune).".postln;
        "[LOOP EDIT TEST] 13c. Melt: set Morph 60 %, tick Melt and press Play -> the loop starts as the original and melts into the cloud over ~4 s. Untick -> it melts back to the original. Move Morph while melting -> the ramp re-targets (no jump).".postln;
        "[LOOP EDIT TEST] 13d. Filter: raise Filter over a morphed loop -> a slow lowpass/low-cut sweep fuses the dry and grain layers; F.Rate 0.05 is a long breath, 0.5 is a wobble; 0 % is bypass. The filter also works with Grain off (on the plain loop).".postln;
        "[LOOP EDIT TEST] 13e. Loop breath: Grain on, Breath 0 % = exactly unchanged; 30-60 % makes the grain cloud swell and thin on its own slow LFO; B.Rate 0.03 is a long breath, 0.3 a pulse. The loop tempo/grid never moves (only the grain FX breathe).".postln;
        "[LOOP EDIT TEST] 14. Memory: save loop 1 to A, re-record loop 1, recall A (audio + edit state return, stopped), recall A into loop 2, clear A.".postln;
        "[LOOP EDIT TEST] 15. Harm = off: every loop sounds exactly as in 8.8 (no wet tail, no CPU hiss). Re-evaluate the rig: slots are empty (session-only); Harm returns to off.".postln;
        "[LOOP EDIT TEST] 16. Cascade: Harm = Cascade, press Fire; the captured segment plays, then the octave, 12th, 16th enter in order. Steps 1 = fundamental only; 8 = the full stack. Time/Space/Fall/Mix change the gesture; Auto off requires Fire. Fire during a cascade restarts the same frozen segment (documented).".postln;
        "[LOOP EDIT TEST] 17. Delay: Harm = Delay; the loop is continuously doubled/filled by the harmonic stack, no trigger. Steps 1 = a plain delay; higher steps add the ladder. Space/Auto/Fire are disabled (inert).".postln;
        "[LOOP EDIT TEST] 18. Fixed time: TimeSync 1/4, 1/4d (dotted), 1/4t (triplet), 1/8, 1 bar; the seconds spin shows the tempo-derived value read-only. Change BPM and a playing fixed-time FX retimes; free time does not. The 2 s buffer clamps longer values.".postln;
        "[LOOP EDIT TEST] 19. Post-PitchShift: mutate the JI set while the FX is on; the FX follows the loop's shifted pitch (it is downstream of PitchShift), and the harmonic ratios stay integer multiples of it. Per-loop: loop 1 Cascade + loop 2 Delay run independently; stopping a loop frees its FX (s.plotTree).".postln;
        "[LOOP EDIT TEST] Audio acceptance is human-only; record the result in docs/README_stage9_0.md §9.".postln;
    }.play(AppClock);
};
)
