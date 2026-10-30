// ===========================================================================
//  JI Station -- Stage 4 -- synthdefs/ji_looper_player.scd
// ---------------------------------------------------------------------------
//  One loop voice:
//
//      PlayBuf(mono, loop) -> LeakDC -> Tartini  -> loop tracker buses
//                                   \-> PitchShift(ratio = targetFreq / measured)
//                                        \-> gain * gate envelope -> outBus
//
//  This mirrors the verified \ji_autotune_ch graph.  The loop player does NOT
//  know about the frequency set: sclang's nearest-class lookup reads the loop
//  tracker buses and writes `targetFreq` / `retuneTime` with Synth.set,
//  exactly as it does for a live input.
//
//  Tartini analyses the UNSHIFTED PlayBuf output (before PitchShift), so the
//  autotune ratio is target / recorded pitch.  The whole loop is never
//  transposed as one block.
//
//  ARGUMENTS
//    bufnum           mono loop buffer (`~ji_buf_loop1..4`)
//    outBus           audio output channel (0 or 1 in Stage 4)
//    freqBus          control bus for measured Hz     ** REQUIRED **
//    hasFreqBus       control bus for confidence      ** REQUIRED **
//    targetFreq       Hz written by sclang's lookup
//    retuneTime       Lag time constant for the pitch ratio
//    windowSize       PitchShift grain window (0.1 s)
//    mix              0 = dry, 1 = fully shifted
//    pitchRangeLimit  semitones, clamps the shift (+/- 12 = one octave)
//    gain             output gain (0.7 leaves headroom for live + loop)
//    tartiniThreshold Tartini peak-picking threshold
//    gate             1 = play, 0 = 30 ms fade-out then free the synth
//
//  The buffer is fixed MONO in Stage 4 (`PlayBuf.ar(1, ...)`).  Stereo loop
//  files and per-loop Ableton routing are deferred to Stage 5.
// ===========================================================================
(
SynthDef(\ji_looper_player, { |bufnum = 0, outBus = 0, freqBus = 0, hasFreqBus = 0, targetFreq = 220, retuneTime = 0.05, windowSize = 0.1, mix = 1.0, pitchRangeLimit = 12, gain = 0.7, tartiniThreshold = 0.9, gate = 1|

    // rate 1.0 is correct because the recorder and player use the same server
    // sample rate.  PlayBuf argument order: numChannels, bufnum, rate, trigger,
    // startPos, loop, doneAction.
    var sig = LeakDC.ar(PlayBuf.ar(1, bufnum, 1.0, 1.0, 0.0, 1.0, 0));
    var t = Tartini.kr(sig, tartiniThreshold);
    // Env times are client-side constants: do not pass a Synth arg here.
    var env = EnvGen.kr(Env.asr(0.01, 1.0, 0.03), gate, doneAction: 2);
    var safeFreq, rawRatio, lim, clampedRatio, smoothRatio, shifted, out;

    // The analysis buses always describe the unshifted loop audio.
    Out.kr(freqBus, t[0]);
    Out.kr(hasFreqBus, t[1]);

    safeFreq = t[0].max(20);                 // no division by zero on silence
    rawRatio = targetFreq / safeFreq;

    // Same clamp expression as \ji_autotune_ch (UGen always on the left).
    lim = (pitchRangeLimit / 12 * 0.6931471805599453).exp;
    clampedRatio = rawRatio.clip(lim.reciprocal, lim);
    smoothRatio = Lag.kr(clampedRatio, retuneTime);

    shifted = PitchShift.ar(sig, windowSize, smoothRatio);
    out = XFade2.ar(sig, shifted, (mix * 2) - 1);

    Out.ar(outBus, out * gain * env);
}).add;
)
