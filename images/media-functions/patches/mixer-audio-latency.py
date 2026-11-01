#!/usr/bin/env python3
"""Give SwitchFrame's audio mixer a fixed, short input latency.

usage: mixer-audio-latency.py <switchframe/server>

Each mixer channel buffers source PCM in a ring and the output ticker pops
1024-sample frames with drift compensation (server/audio/ringbuf.go,
PopDriftCompensated): one extra or one fewer sample per channel per frame
(~1 ms/s) whenever the depth leaves target +/- 1 frame (target 2 frames).
Measured on news-studio with the mxl-syncsrc flash/beep source:

  * any depth inside the 1..3-frame band is never corrected, so programme
    audio settles anywhere between ~21 and ~64 ms of buffering depending on
    history (+37 ms vs +66 ms after two different reconnects);
  * rings fill to capacity (10 frames, 213 ms) at start-up, and on channels
    that are not on air (they are pushed but not popped), so after start-up
    and after every AFV cut audio runs ~200 ms late and takes ~3 minutes to
    drain at 1 ms/s.

This patch keeps the inaudible +/-1-sample correction but narrows its dead
band to +/-256 samples, and when the depth exceeds target + 3 frames it drops
the oldest samples straight back to the target (a one-off skip, at start-up,
after a reconnect or on a cut) instead of draining for minutes. It also stamps
MXL programme audio 3 frames (64 ms) earlier, the time its content entered the
mixer, matching how video grains are stamped (measured before: +67..+83 ms),
and does the same for the browser stream's audio timestamps.
"""
import pathlib
import sys

root = pathlib.Path(sys.argv[1]) / "audio"
path = root / "ringbuf.go"
src = path.read_text()
if "DMF patch: fixed mixer latency" in src:
    print("mixer-audio-latency: already patched; skipping")
    sys.exit(0)

old = """	// Determine how many samples to actually consume.
	threshold := 1024 * channels // 1 AAC frame hysteresis
"""
new = """	// DMF patch: fixed mixer latency. Far above target (start-up, reconnect,
	// a channel coming on air with a full ring): drop the oldest samples back
	// to the target at once instead of draining at one sample per frame.
	if resync := targetDepth + 3*1024*channels; rb.count > resync && targetDepth >= n {
		drop := rb.count - targetDepth
		drop -= drop % channels
		rb.rd = (rb.rd + drop) % rb.cap
		rb.count -= drop
	}

	// Determine how many samples to actually consume.
	threshold := 256 * channels // DMF patch: was 1024 * channels (1 AAC frame)
"""
if src.count(old) != 1:
    sys.exit("mixer-audio-latency: ringbuf.go: drift compensation code changed upstream")
path.write_text(src.replace(old, new))

# Upstream's scratch-reuse test pushes one frame against a two-frame target and
# expects no carry-over; that only held because one frame sat exactly on the
# old band edge. With the narrower band the controller (correctly) slows down
# there, so give that test a target equal to its fill: it tests buffer reuse,
# not drift behaviour.
test = root / "ringbuf_test.go"
tsrc = test.read_text()
fn = tsrc.index("func TestPCMRingBuffer_PopDriftCompensated_ReuseCorrectness(")
line = "\ttarget := 2 * 1024 * channels\n"
at = tsrc.index(line, fn)
if at > tsrc.index("\n}\n", fn):
    sys.exit("mixer-audio-latency: ringbuf_test.go: ReuseCorrectness changed upstream")
test.write_text(tsrc[:at] + "\ttarget := 1024 * channels // DMF patch: at fill depth (was 2 frames)\n" + tsrc[at + len(line):])

# Timestamp MXL programme audio with the time its content entered the mixer:
# the mixer holds DriftTarget (2 frames) per channel plus the 1-frame output
# batch, so block n leaves ~3 frames after its source samples. Video grains are
# stamped with the grain in which the frame arrived (mxl-writer-phase.py), so
# without this MXL programme audio was ~75 ms late against its video.
writer = pathlib.Path(sys.argv[1]) / "mxl" / "writer.go"
wsrc = writer.read_text()
old_w = """	var index uint64
	if CurrentIndex != nil {
		index = CurrentIndex(rate)
	}
	expectedNext := w.lastAudioIndex + uint64(samplesPerCh)
"""
new_w = """	var index uint64
	if CurrentIndex != nil {
		index = CurrentIndex(rate)
		// DMF patch: fixed mixer latency. Stamp audio with the time its
		// content entered the mixer (2-frame drift target + 1-frame batch).
		if advance := uint64(3 * 1024); index > advance {
			index -= advance
		}
	}
	expectedNext := w.lastAudioIndex + uint64(samplesPerCh)
"""
if wsrc.count(old_w) != 1:
    sys.exit("mixer-audio-latency: mxl/writer.go: audio index code changed upstream")
writer.write_text(wsrc.replace(old_w, new_w))

# Same for the browser stream: the mixer stamps each output frame with the
# wall-clock time it is produced (advanceOutputPTS), while video carries its
# source read time, so stream audio was labelled ~3 frames late (measured in
# Chrome: beep PTS 52-58 ms after flash PTS). Only the outgoing timestamp moves;
# the mixer's own PTS counter is unchanged.
ticker = root / "mixer_ticker.go"
tsrc2 = ticker.read_text()
old_t = "\tpts := m.advanceOutputPTS(0)\n"
new_t = ("\tpts := m.advanceOutputPTS(0)\n"
         "\t// DMF patch: fixed mixer latency. Stamp the stream with the time the\n"
         "\t// content entered the mixer (2-frame drift target + 1-frame batch).\n"
         "\t// Applied to every frame so spacing stays regular (the epoch is seeded\n"
         "\t// from programme video PTS, which is well past 3 frames).\n"
         "\tpts -= 3 * m.frameDuration90k()\n")
if tsrc2.count(old_t) != 1:
    sys.exit("mixer-audio-latency: mixer_ticker.go: output PTS code changed upstream")
ticker.write_text(tsrc2.replace(old_t, new_t))

(root / "ringbuf_dmf_test.go").write_text('''package audio

import "testing"

// DMF patch: a ring far above target (here 8 frames) resyncs to the target in
// one pop, keeping the newest samples.
func TestPCMRingBuffer_PopDriftCompensated_CoarseResync(t *testing.T) {
	channels := 2
	frame := 1024 * channels
	target := 2 * frame
	rb := NewPCMRingBuffer(10 * frame)
	fill := make([]float32, 8*frame)
	for i := range fill {
		fill[i] = float32(i)
	}
	rb.Push(fill)

	out := rb.PopDriftCompensated(frame, target, channels)
	if len(out) != frame {
		t.Fatalf("got %d samples, want %d", len(out), frame)
	}
	if got := rb.Len(); got != target-frame {
		t.Fatalf("depth after resync = %d, want %d", got, target-frame)
	}
	// The samples returned are the oldest of the newest 'target' samples.
	if want := float32(8*frame - target); out[0] != want {
		t.Fatalf("first sample %v, want %v (newest audio kept)", out[0], want)
	}
}

// DMF patch: one frame above target is now corrected (it was inside the dead band).
func TestPCMRingBuffer_PopDriftCompensated_NarrowBand(t *testing.T) {
	channels := 2
	frame := 1024 * channels
	target := 2 * frame
	rb := NewPCMRingBuffer(10 * frame)
	rb.Push(make([]float32, target+frame/2))
	before := rb.Len()
	_ = rb.PopDriftCompensated(frame, target, channels)
	if consumed := before - rb.Len(); consumed != frame+channels {
		t.Fatalf("consumed %d, want %d (speed-up)", consumed, frame+channels)
	}
}
''')
print("mixer-audio-latency: drift band +/-256 samples, coarse resync above target + 3 frames, MXL and stream audio stamped -3 frames")
