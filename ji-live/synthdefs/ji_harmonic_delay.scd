// ===========================================================================
//  JI Station -- Stage 9.0 -- synthdefs/ji_harmonic_delay.scd
// ---------------------------------------------------------------------------
//  The loop FX bus: a harmonic-series buffer delay that reads the loop
//  voice's POST-PitchShift send (the private per-loop audio bus) and writes
//  wet only to the loop's output bus.  The dry loop is already upstream, so
//  this SynthDef never duplicates it and never re-runs the autotune.
//
//      loop voice Out.ar(fxBus) -> In.ar(inBus) -> 2 s scratch buffer
//                                                       |
//          mode 0 (Cascade): freeze a captured segment, play it once at
//                            1x, 2x, 3x ... 8x with staggered entry
//          mode 1 (Delay):   keep writing and read taps at 1x, 2x ... 8x
//                            continuously (a varispeed harmonizer)
//                                                       v
//                               Out.ar(outBus, wet * mix * gateEnv)
//
//  HARMONIC LAW (documented in docs/README_stage9_0.md):
//      rate_k = k                     (exact integer harmonics)
//      amp_k  = 1 / k ^ (0.5 + 1.5 * falloff)
//  At falloff = 0 the roll-off is 1/sqrt(k); at 1 it is 1/k^2.  The read rate
//  is NEVER lagged -- a Lag on k would glide the pitch and smear the integer
//  ratios.  Only mix and the gate are smoothed.
//
//  CASCADE (mode 0)
//    While idle the post-shift input is written at 1 frame/sample.  A Fire
//    (Changed.kr(trig)) or the Auto clock freezes the write, snapshots the
//    write head and starts up to eight players reading the same captured
//    segment at k frames/sample.  Player k enters (k - 1) * spacing seconds
//    after the freeze and plays for time / k seconds, so it traverses the
//    same time seconds of audio at k times the speed.  The buffer stays
//    frozen for max(time, (steps - 1) * spacing + time / steps) seconds;
//    writing then resumes and the next Fire captures a fresh segment.
//    A Fire during a cascade restarts it from the SAME frozen segment (the
//    write head is still frozen, so captureHead does not move).  A Fire
//    before `time` seconds of audio exists plays a partial (silent-padded)
//    segment.  Auto fires at `time`, then every
//    time + cascade_duration + 0.1 s, and never during a cascade.
//
//  DELAY (mode 1)
//    The write never stops.  Tap k starts `time` seconds behind the initial
//    write head and reads at k frames/sample, wrapping at the buffer end.  The
//    write head moves at 1x while a tap moves at kx, so the delay window
//    drifts: that is the intended varispeed harmonizer character, not a
//    fixed-time echo.  `spacing`, `auto` and Fire are inert in this mode.
//
//  BUFFER SAFETY / CPU
//    The caller passes a 2 s mono buffer.  `time` is clamped to the buffer
//    duration, `steps` to 1..8 and `spacing` to 0..1 s.  BufRd uses loop = 1
//    so every read wraps inside the buffer.  Four FX synths, each one BufWr
//    plus up to eight BufRd/envelopes, is the Stage 9 CPU ceiling; there is
//    no FFT and no per-harmonic PitchShift.
//
//  ARGUMENTS
//    inBus    private post-PitchShift loop audio bus
//    outBus   the loop's SC output bus (wet only; dry is upstream)
//    bufnum   per-loop 2 s mono FX scratch buffer
//    gate     1 = run, 0 = 30 ms release then free the synth
//    mode     0 = cascade, 1 = delay (the FX synth exists only when on)
//    mix      wet return gain 0..1 (default 0.35)
//    time     captured segment / base delay window in seconds (0.05..2.0)
//    steps    harmonics 1..8 (default 4)
//    spacing  cascade entry spacing in seconds (0..1, default 0.08)
//    falloff  amplitude exponent amount (0..1, default 0.5)
//    auto     0/1 cascade auto-fire (default 1)
//    trig     trigger counter; sclang increments it on Fire
// ===========================================================================
(
SynthDef(\ji_harmonic_delay, { |inBus = 0, outBus = 2, bufnum = 0,
    gate = 1, mode = 0, mix = 0.35, time = 0.5, steps = 4,
    spacing = 0.08, falloff = 0.5, auto = 1, trig = 0|

    var srate = SampleRate.ir;
    var frames = BufFrames.kr(bufnum).max(2);
    var maxTime = frames / srate;
    var timeSec = time.clip(0.05, maxTime.max(0.05));
    var timeFrames = (timeSec * srate).clip(1, frames);
    var spacingSec = spacing.clip(0, 1);
    var stepCount = steps.clip(1, 8);
    var falloffSec = falloff.clip(0, 1);
    var modeFlag = mode.clip(0, 1).round;
    var mixLag = Lag.kr(mix.clip(0, 1), 0.03);
    var gateEnv = EnvGen.kr(Env.asr(0.01, 1.0, 0.03), gate, doneAction: 2);
    var sig = In.ar(inBus, 1);
    var cascadeDur = timeSec.max(((stepCount - 1) * spacingSec) + (timeSec / stepCount));
    var autoPeriod = (timeSec + cascadeDur + 0.1).max(0.2);
    var autoClock = DelayN.kr(Impulse.kr(1 / autoPeriod), 2.2, timeSec);
    var manualFire, autoFire, fireGate, cascading, cascadingManual;
    var writeRate, writePhase, captureHead;
    var captureStart, cascade = 0, delayed = 0, wet;

    //  Manual Fire (a changed trigger counter) plus the Auto clock.  Both are
    //  cascade-only.  Auto is suppressed while a MANUAL cascade is running
    //  (no UGen feedback loop: cascadingManual depends on the manual trigger
    //  only).  An auto-triggered cascade is safe by construction because the
    //  auto period is longer than the cascade; a manual Fire during any
    //  cascade restarts it from the same frozen segment.
    manualFire = Changed.kr(trig) * (1 - modeFlag);
    cascadingManual = Trig1.kr(manualFire, cascadeDur);
    autoFire = autoClock * auto.clip(0, 1) * (1 - modeFlag)
        * (1 - cascadingManual);
    fireGate = manualFire + autoFire;
    cascading = Trig1.kr(fireGate, cascadeDur);

    //  The write phase stops while a cascade is frozen; in delay mode it
    //  never stops.  BufWr is fed sig * writeRate so a frozen write cannot
    //  corrupt the captured segment at the head (the players read up to
    //  head - 1, never head).
    writeRate = Select.kr(modeFlag, [1 - cascading, 1]);
    writePhase = Phasor.ar(0, writeRate, 0, frames, 0);
    captureHead = Latch.ar(writePhase, fireGate);
    captureStart = (captureHead - timeFrames).wrap(0, frames);
    BufWr.ar(sig * writeRate, bufnum, writePhase, 1);

    //  Cascade: one frozen segment, up to eight players reading the SAME
    //  frames at integer rates.  Player k enters after (k - 1) * spacing and
    //  lasts time / k, so it consumes exactly `time` seconds of source.
    (1..8).do { |k|
        var kFire = DelayN.kr(fireGate, 8.0, (k - 1) * spacingSec);
        var dur = timeSec / k;
        var gateK = (stepCount - k + 1).clip(0, 1);
        var ampK = gateK / (k ** (0.5 + (1.5 * falloffSec)));
        var envK = EnvGen.kr(
            Env([0, 1, 1, 0], [0.005, 0.99, 0.02]),
            kFire,
            timeScale: dur,
            levelScale: ampK
        );
        var phaseK = captureStart + Phasor.ar(kFire, k, 0, timeFrames, 0);
        cascade = cascade + (BufRd.ar(1, bufnum, phaseK, 1, 4) * envK);
    };

    //  Delay: continuous taps behind the initial write head, each reading at
    //  its own integer rate and wrapping in the buffer.  The negative reset
    //  position is the initial `time` delay; BufRd loop = 1 wraps it.
    (1..8).do { |k|
        var gateK = (stepCount - k + 1).clip(0, 1);
        var ampK = gateK / (k ** (0.5 + (1.5 * falloffSec)));
        //  Start `time` behind the initial head, expressed as a positive
        //  buffer position (frames - timeFrames); Phasor wraps it at the end.
        var tapStart = (frames - timeFrames).wrap(0, frames);
        var tapPhase = Phasor.ar(0, k, 0, frames, tapStart);
        delayed = delayed + (BufRd.ar(1, bufnum, tapPhase, 1, 4) * ampK);
    };

    //  Select.kr would make the wet return CONTROL rate and Out.ar rejects a
    //  control-rate input ("Out input ... is not audio rate").  The arithmetic
    //  blend stays AUDIO rate and is click-free because a mode change restarts
    //  the FX synth (new gate fade-in; the old node releases).
    wet = (cascade * (1 - modeFlag)) + (delayed * modeFlag);
    Out.ar(outBus, wet * mixLag * gateEnv);
}).add;
)
