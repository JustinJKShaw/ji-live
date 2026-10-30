// ===========================================================================
//  JI Station -- Stage 5 -- test_ableton_routing.scd
// ---------------------------------------------------------------------------
//  Verifies the 16-channel BlackHole 16ch output path, one stereo pair at a
//  time:
//
//      SC ch 0/1   -> Ableton track 1   (Stage 4 live/loop audio in normal use)
//      SC ch 2/3   -> Ableton track 2   (Loop 1, reserved)
//      SC ch 4/5   -> Ableton track 3   (Loop 2, reserved)
//      SC ch 6/7   -> Ableton track 4   (Loop 3, reserved)
//      SC ch 8/9   -> Ableton track 5   (Loop 4, reserved)
//      SC ch 10/11 -> Ableton track 6   (granular FX, Stage 8)
//      SC ch 12/13 -> Ableton track 7   (dry/diagnostic, reserved)
//      SC ch 14/15 -> Ableton track 8   (reserved)
//
//  A 1 kHz sine plays for about one second on each pair.  In Ableton (input
//  BlackHole 16ch, inputs 1-16 enabled) the matching track meters and the tone
//  is heard through Ableton's master output (MiniFuse 2 / headphones).
//
//  PREREQUISITES
//    1. startup.scd (Stage 5) has been evaluated and scsynth is running with
//       numOutputBusChannels = 16 and outDevice = BlackHole 16ch.
//    2. Ableton is configured with BlackHole 16ch input, inputs 1-16 enabled,
//       and its master output on the MiniFuse 2 / headphones.
//    3. Stop Stage 4 audio first (~ji_stop_all.()) if it is running, so the
//       pair-1 tone is not summed with live/loop audio.
//
//  This test does not use Python, the mutation engine, or any Stage 4 set
//  receiver.  It is safe to re-evaluate: it stops its own previous routine and
//  synth first.
//
//  STOP
//    ~ji_ableton_routing_routine.stop;
//    ~ji_ableton_routing_synth.free;
// ===========================================================================

(
var pairs       = 8;       // eight stereo pairs = 16 output channels
var toneFreq    = 1000;    // Hz
var toneAmp     = 0.08;    // quiet: about -22 dBFS per channel
var pairSeconds = 1.0;     // about one second per pair

// --- Preconditions ---------------------------------------------------------
if (s.serverRunning.not) {
    Error("test_ableton_routing: the server is not running (or is still booting). Evaluate startup.scd first.").throw;
};
if (s.options.numOutputBusChannels != 16) {
    Error("test_ableton_routing: numOutputBusChannels is % (expected 16). Re-evaluate the Stage 5 startup.scd and reboot the server.".format(
        s.options.numOutputBusChannels
    )).throw;
};

// --- Stop/clean a previous run --------------------------------------------
~ji_ableton_routing_routine !? { |routine| routine.stop };
~ji_ableton_routing_synth   !? { |synth| synth.free };
~ji_ableton_routing_routine = nil;
~ji_ableton_routing_synth   = nil;

// --- Test tone SynthDef ----------------------------------------------------
//  One second total: 10 ms attack, 940 ms sustain, 50 ms release.  The timed
//  envelope means that if the routine is stopped mid-pair, the tone still ends
//  instead of sustaining forever.  The synth is also freed explicitly before
//  the next pair starts.
SynthDef(\ji_ableton_routing_tone, { |bus = 0, freq = 1000, amp = 0.08|
    var env = EnvGen.kr(Env([0, 1, 1, 0], [0.01, 0.94, 0.05]), doneAction: 0);
    Out.ar(bus, (SinOsc.ar(freq, 0, amp) * env) ! 2)
}).add;

// --- Banner ----------------------------------------------------------------
"[ROUTING] 16-channel output test: 1 kHz sine, one stereo pair at a time".postln;
("[ROUTING] outDevice=% numOutputBusChannels=%").format(
    s.options.outDevice,
    s.options.numOutputBusChannels
).postln;
"[ROUTING] BlackHole is virtual: SC alone is silent. Ableton must monitor BlackHole".postln;
"[ROUTING] and send its master to the MiniFuse 2 / headphones.".postln;
("[ROUTING] % pairs, about % s each. Stop with ~ji_ableton_routing_routine.stop; ~ji_ableton_routing_synth.free;").format(
    pairs, pairSeconds
).postln;

// --- Sequential pair test --------------------------------------------------
~ji_ableton_routing_routine = Routine({
    var outBus, synth;

    s.sync;    // wait for \ji_ableton_routing_tone to reach the server

    pairs.do { |pair|
        outBus = pair * 2;
        synth = nil;

        ("[ROUTING] pair % (Ableton track %) -> SC ch %/%").format(
            pair + 1, pair + 1, outBus, outBus + 1
        ).postln;

        synth = Synth(\ji_ableton_routing_tone, [
            \bus, outBus,
            \freq, toneFreq,
            \amp, toneAmp
        ]);
        ~ji_ableton_routing_synth = synth;

        (pairSeconds + 0.05).wait;   // 1 s envelope plus a safety margin
        synth.free;
        ~ji_ableton_routing_synth = nil;

        0.04.wait;                // short gap before the next pair
    };

    "[ROUTING] done".postln;
    "If every pair was silent: check Ableton's input device (BlackHole 16ch),".postln;
    "Input Config channels 1-16, Monitor = In on each track, and the master output.".postln;
});

~ji_ableton_routing_routine.play;
"[ROUTING] running.".postln;

// --- Stop ------------------------------------------------------------------
// While it is running, evaluate these two lines:
//
//     ~ji_ableton_routing_routine.stop;
//     ~ji_ableton_routing_synth.free;
//
// Re-evaluating this file does the same cleanup automatically.
)
