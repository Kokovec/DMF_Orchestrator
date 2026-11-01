// Fix crackling browser audio: give SwitchFrame's audio player a real jitter buffer.
//
// Measured (Chrome, programme + PFL streams): AAC frames arrive at exactly
// 46.9/s with gaps up to ~80 ms, yet the player underran ~2x per second and
// played ~15% silence. Two causes in ui/src/lib/prism/audio-worklet.ts:
//   1. Playback starts at 50 ms buffered and then consumes at exactly 1x
//      anywhere between LOW_WATER (50) and HIGH_WATER (200), so the buffer
//      never reaches TARGET (100); it idles near 50 ms, less than the
//      network/decoder jitter.
//   2. After an underrun it resumes on the very next frame, toggling between
//      sound and silence every few milliseconds: the crackle.
// Fix: (re)start playback only once the buffer holds TARGET_BUFFER_MS, after
// startup and after every underrun, and widen the band above measured jitter.
//
// usage: node ui-audio-jitterbuffer.mjs <switchframe/ui/src/lib/prism>
import { readFileSync, writeFileSync } from 'node:fs';
import { join } from 'node:path';

const file = join(process.argv[2] ?? '', 'audio-worklet.ts');
let src = readFileSync(file, 'utf8');
if (src.includes('private rebuffering')) {
  console.log('ui-audio-jitterbuffer: already patched; skipping');
  process.exit(0);
}

function replace(from, to) {
  if (!src.includes(from)) {
    console.error(`ui-audio-jitterbuffer: expected code not found (upstream changed?):\n${from}`);
    process.exit(1);
  }
  src = src.replace(from, to);
}

// Band sized above the measured worst-case arrival gap (~80 ms).
replace('const TARGET_BUFFER_MS = 100;', 'const TARGET_BUFFER_MS = 150; // DMF patch: was 100');
replace('const LOW_WATER_MS = 50;', 'const LOW_WATER_MS = 75; // DMF patch: was 50');
replace('const HIGH_WATER_MS = 200;', 'const HIGH_WATER_MS = 300; // DMF patch: was 200');
replace('const HARD_FLUSH_MS = 400;', 'const HARD_FLUSH_MS = 600; // DMF patch: was 400');

replace('\tprivate fractionalAccum = 0;\n',
  '\tprivate fractionalAccum = 0;\n' +
  '\t// DMF patch: jitter buffer state. True at start and after any underrun;\n' +
  '\t// playback (re)starts only once TARGET_BUFFER_MS is buffered.\n' +
  '\tprivate rebuffering = true;\n');

replace('\t\tif (available === 0) {\n\t\t\tthis.outputSilence(outputs);',
  '\t\tif (available === 0) {\n\t\t\tthis.rebuffering = true; // DMF patch\n\t\t\tthis.outputSilence(outputs);');

replace('\t\tlet bufferMs = (available / this.localSampleRate) * 1000;\n',
  '\t\tlet bufferMs = (available / this.localSampleRate) * 1000;\n' +
  '\n' +
  '\t\t// DMF patch: hold silence until the buffer has refilled to the target,\n' +
  '\t\t// instead of resuming on the first frame (sound/silence toggling = crackle).\n' +
  '\t\tif (this.rebuffering) {\n' +
  '\t\t\tif (bufferMs < TARGET_BUFFER_MS) {\n' +
  '\t\t\t\tAtomics.store(this.sharedStates, SharedStates.BUFFER_DEPTH_MS, Math.round(bufferMs));\n' +
  '\t\t\t\tthis.outputSilence(outputs);\n' +
  '\t\t\t\tAtomics.add(this.sharedStates, SharedStates.INSERTED_SILENCE_MS,\n' +
  '\t\t\t\t\tMath.round((framesToFill / this.localSampleRate) * 1000));\n' +
  '\t\t\t\treturn true;\n' +
  '\t\t\t}\n' +
  '\t\t\tthis.rebuffering = false;\n' +
  '\t\t}\n');

writeFileSync(file, src);
console.log('ui-audio-jitterbuffer: player now re-buffers to ' + 150 + ' ms after start/underrun');
