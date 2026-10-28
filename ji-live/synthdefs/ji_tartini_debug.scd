// ===========================================================================
//  JI Station -- Stage 0 -- synthdefs/ji_tartini_debug.scd
// ---------------------------------------------------------------------------
//  Publishes Tartini's pitch estimate for input channels 0 and 1 on the
//  reserved ~ji_* control buses, and passes both channels to the outputs
//  unchanged (no gain, no EQ, no other processing).
//
//  Tartini.kr signature (sc3-plugins, from doc.sccode.org/Classes/Tartini):
//      Tartini.kr(in: 0.0, threshold: 0.93, n: 2048, k: 0, overlap: 1024,
//                 smallCutoff: 0.5)
//  Two outputs: [0] freq, [1] hasFreq (confidence, 0..1).
//  This file passes `in` and the second positional argument (`threshold`)
//  only; every other argument keeps its default.
//
//  Requires startup.scd to have run: the ~ji_* bus indices are read when the
//  SynthDef is built and baked into the graph.
// ===========================================================================

if (~ji_tartini_freq1.isNil or: { ~ji_tartini_hasfreq2.isNil }) {
    Error("ji_tartini_debug: ji control buses are not allocated. Evaluate startup.scd first.").throw;
};

(
// Loading this file sets ~ji_tartini_def (nil if the build fails), so
// test_tartini.scd can tell whether the SynthDef was really built.
~ji_tartini_def = SynthDef(\ji_tartini_debug, { |inBusCh0 = 0, inBusCh1 = 1, outBusCh0 = 0, outBusCh1 = 1, tartiniThreshold = 0.9|

    var sig0 = LeakDC.ar(SoundIn.ar(inBusCh0));
    var sig1 = LeakDC.ar(SoundIn.ar(inBusCh1));

    // t0 / t1 are 2-element arrays: [freq, hasFreq]
    var t0 = Tartini.kr(sig0, tartiniThreshold);
    var t1 = Tartini.kr(sig1, tartiniThreshold);

    Out.kr(~ji_tartini_freq1.index,    t0[0]);
    Out.kr(~ji_tartini_freq2.index,    t1[0]);
    Out.kr(~ji_tartini_hasfreq1.index, t0[1]);
    Out.kr(~ji_tartini_hasfreq2.index, t1[1]);

    // Passthrough only.
    Out.ar(outBusCh0, sig0);
    Out.ar(outBusCh1, sig1);
}).add;
)
