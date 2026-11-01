#!/usr/bin/env python3
"""Stop programme video timestamps falling behind after time on a built-in source.

usage: programme-pts-resync.py <switchframe/server>

Switcher.wallClockVideoPTS stamps programme video with a frame counter and
nudges it towards wall-clock time by half a frame per frame. While a built-in
source (BARS, BLACK) is on programme the counter does not advance, so after
cutting back it is behind by the whole time spent there, and catches up at
1.5x, taking twice that long. Throughout, video and audio timestamps (the
mixer's are wall-clock) disagree, the browser player gives up on its audio
clock and draws frames on arrival, and picture leads sound by the audio buffer.

Measured with the mxl-syncsrc source: 830 s on BARS left programme video
timestamps 770 s behind, 50 ms apart at 30 frames/s, and the beep 299 ms after
the flash, for what would have been ~28 minutes.

This patch jumps straight to wall-clock time when the counter is more than 5
frames behind (past the point where the player stops following audio). Smaller
drift keeps the half-frame nudge. Forward PTS jumps are already handled
downstream (pipeline_codecs.go allows them, and the mixer does the same).
"""
import pathlib
import sys

root = pathlib.Path(sys.argv[1]) / "switcher"
path = root / "switcher.go"
src = path.read_text()
if "DMF patch: programme PTS resync" in src:
    print("programme-pts-resync: already patched; skipping")
    sys.exit(0)

old = """	drift := wallPTS - s.videoPTS
	if drift > frameDur {
		s.videoPTS += frameDur / 2
"""
new = """	drift := wallPTS - s.videoPTS
	if drift > 5*frameDur {
		// DMF patch: programme PTS resync. The counter stands still while a
		// built-in source is on programme; jump instead of crawling at 1.5x.
		s.videoPTS = wallPTS
	} else if drift > frameDur {
		s.videoPTS += frameDur / 2
"""
if src.count(old) != 1:
    sys.exit("programme-pts-resync: switcher.go: wallClockVideoPTS changed upstream")
path.write_text(src.replace(old, new))

(root / "wallclock_pts_dmf_test.go").write_text('''package switcher

import (
	"testing"
	"time"
)

// DMF patch: after a long gap in programme frames the PTS jumps to wall-clock
// time instead of catching up at 1.5x; a small gap keeps the half-frame nudge.
func TestWallClockVideoPTS_ResyncsAfterLongGap(t *testing.T) {
	sw := newTestSwitcher(t, newTestRelay())
	sw.EnableWallClockVideoPTS()
	const frameDur = int64(3003)

	start := sw.wallClockVideoPTS(90000)
	sw.videoPTSMu.Lock()
	sw.videoPTSEpoch = time.Now().Add(-10 * time.Second) // 10 s without frames
	sw.videoPTSMu.Unlock()

	got := sw.wallClockVideoPTS(0)
	want := start + 10*90000
	if got < want-frameDur || got > want+9000 {
		t.Fatalf("after a 10 s gap PTS = %d, want about %d", got, want)
	}

	sw.videoPTSMu.Lock()
	sw.videoPTSEpoch = sw.videoPTSEpoch.Add(-2 * time.Duration(frameDur) * time.Second / 90000)
	before := sw.videoPTS
	sw.videoPTSMu.Unlock()
	step := sw.wallClockVideoPTS(0) - before
	if step > 2*frameDur {
		t.Fatalf("small drift jumped by %d ticks; want the half-frame nudge", step)
	}
}
''')
print("programme-pts-resync: programme PTS jumps to wall clock when more than 5 frames behind")
