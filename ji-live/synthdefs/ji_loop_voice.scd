// ===========================================================================
//  JI Station -- Stage 8.6.1 -- synthdefs/ji_loop_voice.scd
// ---------------------------------------------------------------------------
//  The live-rig loop voice: normal / reverse / varispeed / granular playback
//  into the SAME accepted JI autotune graph as synthdefs/ji_looper_player.scd:
//
//      normal  : BufRd(Phasor, mono) -> LeakDC ---------\
//                                                        XFade2(mode) -> Tartini
//      grain   : TGrains(mono)       -> LeakDC ---------/                 |
//                                                                         v
//                       loop tracker buses Out.kr(freqBus, hasFreqBus)
//                       targetFreq / measured -> clamp -> Lag -> PitchShift
//                       -> XFade2(mix) -> smear(2 x AllpassC) -> * gain * env
//
//  The autotune is NEVER bypassed (owner, 2026-11-02): normal, reverse,
//  varispeed and granular all feed the same Tartini -> nearest class ->
//  PitchShift chain with mix = 1.0.  A varispeeded or granular loop is
//  re-analysed at its new pitch and may be pulled partly back toward the
//  current set.  That interaction is the intended "new material", not an
//  exact transposition.
//
//  STAGE 8.6.1 (owner: "annoying noises playing grains over and over"):
//  the grain read position now TRAVELS through the buffer (motion engine),
//  the cloud has per-grain size/pitch jitter and a beat-mode burst envelope,
//  the pitch is snapped to the current JI set in sclang (the voice gets the
//  already-snapped ratio as grainPitch), and a post-autotune smear is
//  available at 0 (bit-identical dry) by default.  Normal mode is unchanged.
//
//  ARGUMENTS (names are set by tests/sc/test_live_rig.scd)
//    bufnum           mono loop buffer
//    outBus           SC output channel (~ji_loop_outbus)
//    fxBus            Stage 9.0 private post-PitchShift FX send bus
//    freqBus          control bus for measured Hz     ** REQUIRED **
//    hasFreqBus       control bus for confidence      ** REQUIRED **
//    targetFreq       Hz written by sclang's lookup
//    retuneTime       Lag time constant for the pitch ratio
//    windowSize       PitchShift grain window (0.1 s)
//    mix              fixed at 1.0: the loop always runs through the retune
//    pitchRangeLimit  semitones, clamps the shift (+/- 12 = one octave)
//    gain             per-loop output gain (0..1, default 0.70)
//    tartiniThreshold Tartini peak-picking threshold
//    gate             1 = play, 0 = 30 ms fade-out then free the synth
//    mode             0 = normal, 1 = grain (crossfaded, no click)
//    reverse          0 = forward, 1 = reverse (phase continues, direction flips)
//    rate             varispeed ratio in frames per output sample (1.0 = natural)
//    grainSync        0 = free-running grains, 1 = beat-synced (grainTrig)
//    grainRate        free grains per second (0.1..80)
//    grainSize        grain duration in seconds (0.005..0.5)
//    grainPos         read position, 0..1 of the buffer
//    grainScatter     position scatter, 0..1 of the buffer
//    grainPitch       grain playback ratio (0.25..4.0), ALREADY JI-snapped
//    grainReverse     per-grain reverse probability 0..1
//    grainTrig        beat-synced trigger counter: sclang increments an integer
//                     once per subdivision, Changed.kr fires exactly one burst
//    grainMotion      0 off / 1 forward / 2 backward / 3 pingpong / 4 drift
//    grainSpeed       scan speed in buffer lengths per second (0.001..0.5)
//    grainDepth       0..1 scan depth around grainPos
//    grainSizeJitter  per-grain size variation 0..1
//    grainPitchJitter per-grain pitch scatter 0..1
//    grainPitchSnap   informational only: JI snapping happens in sclang
//    grainSmear       post-autotune diffusion 0..1 (0 = bit-identical dry)
//    grainFreeze      0/1 hold the motion phase
//    grainBurst       beat mode: extra density right after a trigger
//    grainDecay       beat mode: burst decay seconds (sclang computes it)
//    grainMorph       0..1 dry-loop <-> grain-cloud blend (0 = the original)
//    grainFilter      0..1 moving melt-filter amount (0 = bypass)
//    grainFilterRate  melt-filter LFO rate in Hz (0.01..2)
//    grainBreath      0..1 grain-breath depth (0 = exactly bypass)
//    grainBreathRate  grain-breath LFO rate in Hz (0.02..1)
//
//  REVERSE / VARISPEED
//    Phasor applies sc_wrap(level, start, end) on every sample (verified in
//    SuperCollider's TriggerUGens.cpp, Phasor_next_kk), so a negative increment
//    wraps from start back to end: a reverse loop runs end -> start and keeps
//    running.  The phase is not reset on a reverse change; only the increment's
//    sign changes, through a 0.02 s Lag, so the read head slows, stops and
//    reverses without a click.  rate 1/1 + forward is the accepted player.
//
//  GRAIN MOTION
//    A control-rate 0..1 phase (one step per control block, so the increment is
//    grainSpeed * ControlDur -- NOT SampleDur, which would be 64x too slow with
//    Phasor.kr) is mapped through grainPos/grainDepth to a read position; one
//    full pass takes 1 / grainSpeed seconds because grainSpeed is in buffer
//    lengths per second.  Motion = off gives phase 0.5, i.e. exactly grainPos,
//    so Depth has no effect.  Freeze scales the increment by (1 - frozen), so
//    the phase stops where it is and continues from there (no jump).
//    Pingpong runs Phasor 0..2 in one stepSize and folds it to a triangle.
//
//  GRANULAR CLAMPING
//    TGrains must never read outside the buffer: the centre is jittered, then
//    clamped to [halfSpan, seconds - halfSpan], where halfSpan includes the
//    per-grain pitch ratio; a buffer shorter than 2 * halfSpan plays from its
//    centre instead.
//
//  BEAT BURST
//    The beat clock multiplies the grain rate by (1 + grainBurst * burstEnv),
//    where burstEnv is Env.perc(0.001, 1.0) (client-side constants -- never a
//    Synth argument inside an Env) triggered by Changed.kr(grainTrig) and
//    scaled by timeScale: grainDecay.  One grainTrig OSC set == one burst.
//
//  SMEAR
//    Two AllpassC stages after the autotune, crossfaded with XFade2: at
//    grainSmear = 0 the crossfade pan is -1 and the output is the accepted dry
//    chain.  It is post-autotune: the loop still goes THROUGH the autotune.
//
//  MORPH (Stage 8.6.2)
//    blend = mode * grainMorph drives the normal/grain crossfade.  Morph 0 in
//    grain mode is the ORIGINAL loop (the dry path, through the autotune with
//    the exact accepted retuneTime); morph 1 is the full grain cloud; in
//    between the equal-power XFade2 smears the two.  The retune floor follows
//    the blend (0.15 * blend), so a mostly dry morph does not glide like a
//    cloud.  sclang's "Melt" toggle ramps the control from 0 to the slider
//    value, so a loop can start dry and progressively turn granular.
//
//  MELT FILTER (Stage 8.6.2)
//    Post-autotune (after the smear): a resonant lowpass and a low cut, each
//    swept by its own slow LFO (different rate/phase, LinExp so the movement
//    is even in log frequency), crossfaded in by grainFilter.  0 is exactly
//    the dry chain.  It works on the dry loop too, not only on grains.
//
//  CPU
//    One TGrains + two allpasses + two filters per loop, no LocalBuf and no
//    FFT: the cost stays linear.  Four simultaneous grain voices remain the
//    ceiling.
// ===========================================================================
(
SynthDef(\ji_loop_voice, { |bufnum = 0, outBus = 2, fxBus = 0, freqBus = 0, hasFreqBus = 0,
    targetFreq = 220, retuneTime = 0.05, windowSize = 0.1, mix = 1.0,
    pitchRangeLimit = 12, gain = 0.7, tartiniThreshold = 0.9, gate = 1,
    mode = 0, reverse = 0, rate = 1.0,
    grainSync = 0, grainRate = 20.0, grainSize = 0.12, grainPos = 0.5,
    grainScatter = 0.1, grainPitch = 1.0, grainReverse = 0.0, grainTrig = 0,
    grainMotion = 1, grainSpeed = 0.03, grainDepth = 0.6,
    grainSizeJitter = 0.2, grainPitchJitter = 0.0, grainPitchSnap = 1,
    grainSmear = 0.0, grainFreeze = 0, grainBurst = 0.6, grainDecay = 0.15,
    grainMorph = 0.0, grainFilter = 0.0, grainFilterRate = 0.12,
    grainBreath = 0.0, grainBreathRate = 0.08|

    var frames = BufFrames.kr(bufnum).max(2);
    var srate = SampleRate.ir;
    var bufSeconds = frames / srate;
    //  Stage 8.7: the loop's own grain breath.  SinOsc is bipolar (-1..1);
    //  grainBreath scales it and grainBreath = 0 makes every factor below
    //  exactly 1 (bit-identical to the accepted voice).
    var breathLfo = SinOsc.kr(grainBreathRate.clip(0.02, 1.0), 0);
    var breathe = grainBreath.clip(0, 1) * breathLfo;

    // --- normal / reverse / varispeed -------------------------------------
    //  frames per output sample; the sign is the playback direction.
    var direction = Select.kr(reverse.clip(0, 1), [1, -1]);
    var increment = Lag.kr(rate.clip(0.05, 8) * direction, 0.02);
    var phase = Phasor.ar(0, increment, 0, frames, 0);
    var normal = LeakDC.ar(BufRd.ar(1, bufnum, phase, 1, 4));

    // --- granular triggers, with the beat-mode burst envelope -------------
    //  Env.perc times are CLIENT-side constants: a Synth argument inside an
    //  Env is a SynthDef compile error.  timeScale IS a control input.
    var frozen = grainFreeze.clip(0, 1);
    var freeTrig = Impulse.kr((grainRate * (1 + (0.6 * breathe))).clip(0.1, 80));
    var beatTrig = Changed.kr(grainTrig);
    var burstEnv = EnvGen.kr(Env.perc(0.001, 1.0), beatTrig,
        timeScale: grainDecay.clip(0.01, 1));
    var burst = grainBurst.clip(0, 1) * burstEnv;
    var burstDensity = (grainRate * (1 + burst) * (1 + (0.6 * breathe))).clip(0.1, 80);
    var beatClock = Impulse.kr(burstDensity);
    var trig = Select.kr(grainSync.clip(0, 1), [freeTrig, beatClock]);

    // --- motion: a 0..1 phase that travels through the buffer -------------
    //  stepRate is the phase advance per CONTROL BLOCK (Phasor.kr), so the
    //  conversion uses ControlDur, never SampleDur.  grainSpeed is in BUFFER
    //  LENGTHS PER SECOND, so the phase rate is grainSpeed per second and one
    //  full pass takes 1 / grainSpeed seconds (0.03 -> ~33 s, whatever the
    //  buffer length).  (The stage prompt divided by bufSeconds as well; that
    //  would make the scan loop-length dependent and ~8x slower than the
    //  documented unit, so it is not used.)
    var stepRate = grainSpeed.clip(0.001, 0.5) * ControlDur.ir;
    var motionPhase = Select.kr(grainMotion.clip(0, 4), [
        0.5,                                                          // off
        Phasor.kr(0, stepRate * (1 - frozen), 0, 1),                    // forward
        Phasor.kr(1, stepRate.neg * (1 - frozen), 0, 1),                // backward
        Phasor.kr(0, stepRate * 2 * (1 - frozen), 0, 2).fold(0, 1),     // pingpong
        LFNoise1.kr(grainSpeed.clip(0.001, 0.5) * (1 - frozen)).range(0, 1) // drift
    ]);
    var posFraction = (grainPos.clip(0, 1)
        + (grainDepth.clip(0, 1) * (motionPhase - 0.5))).wrap(0, 1);
    var basePos = posFraction * bufSeconds;
    var scattered = basePos
        + (grainScatter.clip(0, 1) * bufSeconds * TRand.kr(-1, 1, trig));

    // --- per-grain size / pitch jitter ------------------------------------
    //  grainPitch is the EFFECTIVE ratio: sclang snapped it to the current JI
    //  set before it was sent.  pitchJitter adds a little per-grain scatter.
    var pitchJitter = 1 + (grainPitchJitter.clip(0, 1) * TRand.kr(-0.5, 0.5, trig));
    var grainRatio = grainPitch.abs.clip(0.25, 4.0) * pitchJitter;
    var jitteredSize = grainSize.clip(0.005, 0.5)
        * (1 + (grainSizeJitter.clip(0, 1) * TRand.kr(-0.5, 0.5, trig)));
    var grainDur = (jitteredSize * (1 + (0.35 * breathe))).clip(0.005, 0.5);

    // --- centre placement, clamped so the whole grain stays in the buffer --
    //  TGrains.ar is (numChannels, trigger, bufnum, rate, centerPos, dur,
    //  pan, amp, interp) -- pan BEFORE amp -- and centerPos is in SECONDS
    //  (it is multiplied by the buffer sample rate inside the UGen).
    //  pan/amp are passed by name so the order can never silently zero the
    //  grain amplitude again (amp in the pan slot + pan 0 in the amp slot is
    //  exactly "grain on, no sound").
    var halfSpan = grainDur * grainRatio * 0.5;
    var centre = scattered.clip(halfSpan, bufSeconds - halfSpan);
    var shortBuffer = bufSeconds < (2 * halfSpan);
    var centrePos = Select.kr(shortBuffer, [centre, bufSeconds * 0.5]);
    var growRate = grainRatio
        * Select.kr(TRand.kr(0, 1, trig) < grainReverse.clip(0, 1), [1, -1]);
    var grains = TGrains.ar(1, trig, bufnum, growRate, centrePos, grainDur,
        pan: 0.0, amp: 1.0, interp: 4);
    var grain = LeakDC.ar(grains);

    // --- morph: the dry loop <-> grain cloud blend (Stage 8.6.2) -----------
    //  blend = mode * grainMorph.  blend 0 is the normal path (bit-identical to
    //  the accepted loop), blend 1 is the grain cloud, and XFade2 is equal
    //  power, so the middle is a real smear of the two.  sclang "melt" ramps
    //  grainMorph from 0 to the Morph slider value; the Lag only smooths the
    //  ramp steps (and the mode switch).
    var blend = Lag.kr(mode.clip(0, 1) * grainMorph.clip(0, 1), 0.05);
    var sig = XFade2.ar(normal, grain, (blend * 2) - 1);

    // --- accepted JI autotune graph (never bypassed) ----------------------
    //  The more grain in the blend, the more the analysed signal is a jittery
    //  cloud, so the retune floor follows the blend: max(retuneTime,
    //  0.15 * blend).  blend 0 is exactly the accepted retuneTime (no
    //  regression) and blend 1 never retunes faster than 0.15 s.
    var retune = retuneTime.max(0.15 * blend);
    var t = Tartini.kr(sig, tartiniThreshold);
    var env = EnvGen.kr(Env.asr(0.01, 1.0, 0.03), gate, doneAction: 2);
    var safeFreq, rawRatio, lim, clampedRatio, smoothRatio, shifted, out;
    var smearWet;
    //  Every var must be declared here, BEFORE the first statement: sclang
    //  only accepts a var declaration at the top of a function block.
    var meltLfoA, meltLfoB, meltLp, meltHp, melted;

    Out.kr(freqBus, t[0]);
    Out.kr(hasFreqBus, t[1]);

    safeFreq = t[0].max(20);                 // no division by zero on silence
    rawRatio = targetFreq / safeFreq;

    // Same clamp expression as \ji_autotune_ch / \ji_looper_player.
    lim = (pitchRangeLimit / 12 * 0.6931471805599453).exp;
    clampedRatio = rawRatio.clip(lim.reciprocal, lim);
    smoothRatio = Lag.kr(clampedRatio, retune);

    shifted = PitchShift.ar(sig, windowSize, smoothRatio);
    out = XFade2.ar(sig, shifted, (mix * 2) - 1);

    // --- smear (post-autotune diffusion, dry and bit-identical at 0) ------
    smearWet = AllpassC.ar(shifted, 0.05, 0.007, 0.04);
    smearWet = AllpassC.ar(smearWet, 0.05, 0.011, 0.05);
    out = XFade2.ar(out, smearWet, (grainSmear.clip(0, 1) * 2) - 1);

    // --- moving "melt" filter (Stage 8.6.2, post-autotune) ----------------
    //  Two slowly moving filters over the dry+grain mix: a resonant lowpass
    //  and a low cut, each on its own LFO (different rate and phase), so the
    //  spectrum keeps breathing and the two layers fuse.  LinExp keeps the
    //  movement even in log frequency; the passband never closes completely.
    meltLfoA = SinOsc.kr(grainFilterRate.clip(0.01, 2), 0).range(0, 1);
    meltLfoB = SinOsc.kr(grainFilterRate.clip(0.01, 2) * 0.63, pi).range(0, 1);
    meltLp = LinExp.kr(meltLfoA, 0, 1, 700, 6000);
    meltHp = LinExp.kr(meltLfoB, 0, 1, 90, 420);
    melted = RHPF.ar(RLPF.ar(out, meltLp, 0.35), meltHp, 0.8);
    out = XFade2.ar(out, melted, (grainFilter.clip(0, 1) * 2) - 1);

    //  Stage 9.0: the accepted dry output is unchanged; the same POST-
    //  PitchShift / post-smear / post-melt / post-gain / post-envelope signal
    //  is also sent to the loop's private FX bus.  No FX synth reads it unless
    //  Harm is on, so the default output is exactly the 8.8 voice.
    Out.ar(outBus, out * gain * env);
    Out.ar(fxBus, out * gain * env);
}).add;
)
