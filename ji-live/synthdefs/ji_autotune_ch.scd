// ===========================================================================
//  JI Station -- Stage 1 -- synthdefs/ji_autotune_ch.scd
// ---------------------------------------------------------------------------
//  Single-channel JI autotune, one channel:
//
//    SoundIn -> LeakDC -> Tartini  ->  control buses freqBus / hasFreqBus
//                       \-> PitchShift(ratio = targetFreq / measured) -> out
//
//  sclang (test_autotune.scd) reads the buses, picks the nearest JI member and
//  writes it into `targetFreq` with Synth.set. Nothing here changes structure
//  at run time -- only the `targetFreq`, `retuneTime`, `mix` and `inputGain`
//  control values are meant to be set.
//
//  ARGUMENTS                       unit / range
//    inBus                 audio input channel index        (0 = input 1)
//    outBus                audio output channel index       (0 = out 1)
//    freqBus               control bus index for measured Hz    ** REQUIRED **
//    hasFreqBus            control bus index for confidence     ** REQUIRED **
//    targetFreq            Hz, the pitch to tune to        (220 = A3)
//    retuneTime            seconds, Lag time constant for the ratio
//                          (0.005 = snap, 0.05 = default, 0.5 = portamento)
//    windowSize            seconds, PitchShift grain window (0.1 = 100 ms)
//    mix                   0..1  (0 = dry, 1 = fully shifted)
//    pitchRangeLimit       semitones, clamps the shift to +/- this (12 = 1 oct)
//    tartiniThreshold      0..1, Tartini peak-picking threshold
//    inputGain             linear gain into analysis + shifter
//
//  ** REQUIRED ** means: pass a real control bus index at Synth creation, e.g.
//      \freqBus, ~ji_autotune_freq1.index, \hasFreqBus, ~ji_autotune_hasfreq1.index
//  The default (0) is not a safe fallback: it is the Stage 0 Tartini bus for
//  channel 1, so forgetting it would overwrite Stage 0's measurement.
//
//  PitchShift is a granular (time-domain) shifter: the shifted signal is
//  delayed by roughly windowSize. Rhythm/timing of the input is preserved;
//  formants are not (that is the aesthetic choice for this stage).
// ===========================================================================
(
SynthDef(\ji_autotune_ch, { |inBus = 0, outBus = 0, freqBus = 0, hasFreqBus = 0, targetFreq = 220, retuneTime = 0.05, windowSize = 0.1, mix = 1.0, pitchRangeLimit = 12, tartiniThreshold = 0.9, inputGain = 1.0|

    var sig = LeakDC.ar(SoundIn.ar(inBus) * inputGain);

    // t = [freq, hasFreq]
    var t = Tartini.kr(sig, tartiniThreshold);

    var safeFreq, rawRatio, lim, clampedRatio, smoothRatio, shifted, out;

    Out.kr(freqBus, t[0]);
    Out.kr(hasFreqBus, t[1]);

    safeFreq = t[0].max(20);                 // no division by zero on silence
    rawRatio = targetFreq / safeFreq;

    // lim = 2 ** (pitchRangeLimit / 12), written as exp(ln(2) * n/12) so that
    // the UGen always stays on the LEFT of every operator. (With a control-rate
    // input, "2 ** pitchRangeLimit" relies on number-on-the-left UGen dispatch;
    // the exp form is exactly equivalent and has no such dependency.)
    lim = (pitchRangeLimit / 12 * 0.6931471805599453).exp;

    // Symmetric clamp: 1/lim .. lim  (reciprocal instead of 1/lim for the same
    // reason -- UGen on the left).
    clampedRatio = rawRatio.clip(lim.reciprocal, lim);
    smoothRatio = Lag.kr(clampedRatio, retuneTime);

    shifted = PitchShift.ar(sig, windowSize, smoothRatio);

    // mix = 0 -> dry (pan -1), mix = 1 -> shifted (pan +1)
    out = XFade2.ar(sig, shifted, (mix * 2) - 1);
    Out.ar(outBus, out);
}).add;
)
