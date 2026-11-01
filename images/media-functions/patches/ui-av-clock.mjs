// Fix the browser player's A/V clock: flash and beep land at a different offset
// on every page load (measured in Chrome: beep +347..+415 ms or -81..-172 ms).
//
// Video is paced by PrismAudioDecoder.getPlaybackPTS(). The worklet's PTS counts
// samples already output, so what the listener hears is PTS - output latency.
// Upstream also subtracted the ring buffer depth (audio not yet played, so it is
// ahead of that PTS, not behind it) and a fixed 60 ms nudge, so the clock read
// 150-360 ms too early, varying with wherever the buffer settled:
//   * shallow buffer: video was held back too far (sound before picture);
//   * deep buffer: the gap to arriving frames passed the renderer's 500 ms
//     "PTS discontinuity" rule, which shows frames on arrival with an empty
//     queue (measured: video 590 ms ahead of the clock), so the picture led the
//     sound by the whole audio buffer.
// The 60 ms nudge stood for a server-side gap that mixer-audio-latency.py removes.
//
// The worklet's PTS also has to follow the audio position. When the buffer runs
// high the worklet drains it by advancing its read pointer faster than it outputs
// samples, skipping audio, but upstream's PTS counts samples output. Every skipped
// sample left the clock permanently behind what is heard, so sync got worse over a
// session (measured live: ~550 ms unaccounted for after a few minutes, with the
// picture drawn on arrival). PTS now counts the samples consumed from the ring.
//
// usage: node ui-av-clock.mjs <switchframe/ui/src/lib/prism>
import { readFileSync, writeFileSync } from 'node:fs';
import { join } from 'node:path';

const dir = process.argv[2] ?? '';
function patch(name, pairs) {
  const file = join(dir, name);
  let src = readFileSync(file, 'utf8');
  for (const [from, to] of pairs) {
    if (!src.includes(from)) {
      console.error(`ui-av-clock: ${name}: expected code not found (upstream changed?):\n${from}`);
      process.exit(1);
    }
    src = src.replace(from, to);
  }
  writeFileSync(file, src);
}

patch('audio-decoder.ts', [
  ['\t\tconst bufferDepthMs = this.ringBuffer.readBufferDepthMs();\n', ''],
  ['\t\tconst serverContentNudgeMs = 60;\n', ''],
  ['\t\tconst compensationUs = (bufferDepthMs + outputLatencyMs + serverContentNudgeMs) * 1000;\n',
   '\t\t// DMF patch: the worklet PTS counts samples already output, so only the\n' +
   '\t\t// output latency separates it from what is heard. Buffered audio has not\n' +
   '\t\t// been played yet and must not be subtracted.\n' +
   '\t\tconst compensationUs = outputLatencyMs * 1000;\n'],
]);
patch('audio-decoder.test.ts', [
  ["should subtract ring buffer depth + AudioContext output latency from playback PTS",
   "should subtract only AudioContext output latency from playback PTS"],
  ['\t\t\t// readBufferDepthMs=100, outputLatency=32ms, serverNudge=60ms\n\t\t\t// compensation = (100 + 32 + 60) * 1000 = 192,000 µs\n\t\t\texpect(pts).toBe(rawPTS - 192_000);',
   '\t\t\t// DMF patch: outputLatency=32ms; buffered audio is not subtracted.\n\t\t\texpect(pts).toBe(rawPTS - 32_000);'],
  ['\t\t\t// No outputLatency → ring buffer depth + server nudge\n\t\t\t// readBufferDepthMs=100, outputLatency=0, serverNudge=60ms\n\t\t\t// compensation = (100 + 0 + 60) * 1000 = 160,000 µs\n',
   '\t\t\t// DMF patch: no outputLatency → raw PTS.\n'],
  ['expect(pts).toBe(rawPTS - 160_000);', 'expect(pts).toBe(rawPTS);'],
]);
patch('audio-worklet.ts', [
  ['\t\t// PTS tracks samples actually output to speakers (toRead), not the\n' +
   '\t\t// speed-adjusted ring pointer advance (toAdvance). When adaptive rate\n' +
   '\t\t// control drains excess buffer at >1.0x speed, the ring pointer jumps\n' +
   '\t\t// ahead (skipping samples), but PTS should reflect wall-clock playback\n' +
   '\t\t// position. Using samplesConsumed would cause PTS to overshoot by\n' +
   '\t\t// ~50ms/sec at 1.05x, accumulating seconds of AV sync drift over time.\n',
   '\t\t// DMF patch: PTS follows the ring read position, i.e. the audio being\n' +
   '\t\t// heard. Counting samples output instead leaves it behind by every\n' +
   '\t\t// sample skipped while draining a deep buffer.\n'],
  ['((this.sampleOffset + this.samplesOutput) / this.localSampleRate) * 1_000_000;',
   '((this.sampleOffset + this.samplesConsumed) / this.localSampleRate) * 1_000_000;'],
]);
console.log('ui-av-clock: audio clock = ring read position - output latency');
