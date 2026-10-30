// ===========================================================================
//  JI Station -- Stage 4 -- synthdefs/ji_looper_recorder.scd
// ---------------------------------------------------------------------------
//  Records one DRY input channel into one MONO loop buffer:
//
//      SoundIn(inputChannel) -> LeakDC -> RecordBuf -> buffer
//
//  The recorded signal is the raw, pre-autotune input.  Playback
//  (\ji_looper_player) runs its own Tartini + PitchShift chain, so a loop can
//  retune note-by-note when the frequency set changes.
//
//  ARGUMENTS
//    inputChannel   audio input index (loop 1 -> 0, loop 2 -> 1, ...)
//    bufnum         mono buffer, allocated by test_dual_loop.scd
//    inputGain      linear gain before recording (default 1.0)
//    recLevel       RecordBuf write level (default 1.0; preLevel is always 0)
//    run            1 = record, 0 = pause.  The test's ~ji_stop_record.()
//                   frees the synth, so the buffer is left intact.
//
//  RecordBuf is created with loop: 0 and doneAction: 2.  Recording starts at
//  frame 0, overwrites (does not overdub), and the Synth frees itself when the
//  allocated buffer is full.  An explicit early stop is simply
//  Synth.free / ~ji_stop_record.().
//
//  The test allocates the buffer to exactly the requested duration, so the
//  recorded region and the PlayBuf loop region are the same length.
// ===========================================================================
(
SynthDef(\ji_looper_recorder, { |inputChannel = 0, bufnum = 0, inputGain = 1.0, recLevel = 1.0, run = 1.0|

    var dry = LeakDC.ar(SoundIn.ar(inputChannel) * inputGain);

    // RecordBuf signature (SC 3.15): in, bufnum, offset, recLevel, preLevel,
    // run, loop, trigger, doneAction.
    // No Out is needed: RecordBuf is a side-effect UGen.  (The SC help example
    // uses it without an Out.)
    RecordBuf.ar(dry, bufnum,
        offset: 0.0,
        recLevel: recLevel,
        preLevel: 0.0,
        run: run,
        loop: 0.0,
        trigger: 1.0,
        doneAction: 2
    );
}).add;
)
