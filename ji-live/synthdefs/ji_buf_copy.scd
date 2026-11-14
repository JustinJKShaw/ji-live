// ===========================================================================
//  JI Station -- Stage 8.6 -- synthdefs/ji_buf_copy.scd
// ---------------------------------------------------------------------------
//  Server-side mono buffer copy for the session-only loop memory slots.
//
//      BufRd(src, Line 0 .. numFrames-1) -> BufWr(dst, same phase)
//
//  sclang allocates the destination buffer, starts this Synth and waits
//  numFrames / sampleRate + a small safety margin before it uses (or frees)
//  either buffer.  That keeps the copy on the audio thread and out of the
//  language thread, with no file I/O and no version-specific Buffer.copyData
//  dependency.  Line frees the Synth (doneAction: 2) when the copy is done.
//
//  ARGUMENTS
//    src         source mono buffer
//    dst         destination mono buffer (same frame count)
//    numFrames   frames to copy (clamped by the caller)
//    gate        1 = run; the Synth self-frees when the Line completes
// ===========================================================================
(
SynthDef(\ji_buf_copy, { |src = 0, dst = 0, numFrames = 1, gate = 1|

    var frames = numFrames.max(1);
    var dur = frames / SampleRate.ir;
    var phase = Line.ar(0, frames - 1, dur, doneAction: 2);
    var sig = BufRd.ar(1, src, phase, 0, 4);

    BufWr.ar(sig, dst, phase, 0);
}).add;
)
