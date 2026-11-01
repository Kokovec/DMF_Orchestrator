#!/usr/bin/env python3
"""Stop SwitchFrame's MXL programme output from dropping and repeating frames.

usage: mxl-writer-phase.py <switchframe/server>

server/mxl/writer.go publishes each programme frame to a free-running
time.Ticker at the frame rate, which writes the newest frame at the grain
index current at tick time. The ticker's phase is arbitrary relative to both
frame arrivals and grain boundaries. When a tick lands near a frame arrival,
scheduling jitter decides which side of the tick the frame falls, so frames
are written twice or not at all (measured with a one-frame flash every
second: 19 to 22 of 30 flashes reached the programme flow), and when it lands
near a grain boundary, two ticks can share an index or skip one.

This patch:
  * records, for every published frame, its arrival phase within the grain
    period (circular average, so it is stable across the period wrap);
  * replaces the free-running ticker with one that writes grain n at
    start(n) + arrival phase + half a period, i.e. as far as possible from
    arrivals, so each frame is written exactly once as long as arrival jitter
    stays below half a frame;
  * writes every index exactly once, in order (repeating the newest frame if
    none arrived, as before; skipping ahead after a stall);
  * adds NowNs / IndexToTimestampNs (mxlGetTime / mxlIndexToTimestamp) next
    to CurrentIndex, with stub-build equivalents.
"""
import pathlib
import re
import sys

root = pathlib.Path(sys.argv[1]) / "mxl"


def patch(name, pairs):
    path = root / name
    src = path.read_text()
    if "DMF patch: phase-aligned" in src:
        print(f"mxl-writer-phase: {name} already patched; skipping")
        return
    for old, new in pairs:
        if callable(old):
            src = old(src)
            continue
        if src.count(old) != 1:
            sys.exit(f"mxl-writer-phase: {name}: expected one occurrence of {old.strip()[:70]!r}")
        src = src.replace(old, new)
    path.write_text(src)


patch("types.go", [(
    "var CurrentIndex func(rate Rational) uint64\n",
    "var CurrentIndex func(rate Rational) uint64\n"
    "\n"
    "// NowNs returns the MXL clock in nanoseconds (mxlGetTime in cgo builds).\n"
    "// DMF patch: phase-aligned MXL video writer.\n"
    "var NowNs func() uint64\n"
    "\n"
    "// IndexToTimestampNs returns the MXL-clock start time of a grain index.\n"
    "var IndexToTimestampNs func(rate Rational, index uint64) uint64\n",
)])

patch("flow.go", [(
    "\t\treturn uint64(C.mxlGetCurrentIndex(&cr))\n\t}\n",
    "\t\treturn uint64(C.mxlGetCurrentIndex(&cr))\n\t}\n"
    "\t// DMF patch: phase-aligned MXL video writer.\n"
    "\tNowNs = func() uint64 { return uint64(C.mxlGetTime()) }\n"
    "\tIndexToTimestampNs = func(rate Rational, index uint64) uint64 {\n"
    "\t\tcr := C.mxlRational{numerator: C.int64_t(rate.Numerator), denominator: C.int64_t(rate.Denominator)}\n"
    "\t\treturn uint64(C.mxlIndexToTimestamp(&cr, C.uint64_t(index)))\n"
    "\t}\n",
)])

patch("stub.go", [(
    "\t\treturn uint64(ns * float64(rate.Numerator) / (float64(rate.Denominator) * 1e9))\n\t}\n",
    "\t\treturn uint64(ns * float64(rate.Numerator) / (float64(rate.Denominator) * 1e9))\n\t}\n"
    "\t// DMF patch: phase-aligned MXL video writer.\n"
    "\tNowNs = func() uint64 { return uint64(time.Now().UnixNano()) }\n"
    "\tIndexToTimestampNs = func(rate Rational, index uint64) uint64 {\n"
    "\t\tif rate.Numerator == 0 || rate.Denominator == 0 {\n"
    "\t\t\treturn 0\n"
    "\t\t}\n"
    "\t\treturn uint64(float64(index) * float64(rate.Denominator) * 1e9 / float64(rate.Numerator))\n"
    "\t}\n",
)])

TICK_LOOP = '''func (w *Writer) videoTickLoop(ctx context.Context) {
	// DMF patch: phase-aligned MXL video writer (see images/media-functions/
	// patches/mxl-writer-phase.py). Grain n is written once, at
	// start(n) + arrival phase + half a period.
	ref := w.videoRef.Load()
	if ref == nil || ref.rate.Numerator <= 0 || ref.rate.Denominator <= 0 {
		return
	}
	rate := ref.rate
	period := float64(rate.Denominator) * 1e9 / float64(rate.Numerator)

	next := CurrentIndex(rate) // next grain index to write
	var lastPhase float64      // unwrapped arrival phase used for the previous grain
	havePhase := false
	timer := time.NewTimer(time.Hour)
	defer timer.Stop()

	for {
		offset := period / 2 // until frames arrive: write mid-grain
		if p, ok := w.arrivalPhaseNs(period); ok {
			// Unwrap towards the previous phase so ticks move continuously
			// when the average crosses the period boundary.
			if havePhase {
				for p-lastPhase > period/2 {
					p -= period
				}
				for lastPhase-p > period/2 {
					p += period
				}
			}
			lastPhase, havePhase = p, true
			offset = p + period/2
		}

		wait := float64(IndexToTimestampNs(rate, next)) + offset - float64(NowNs())
		if wait < -2*period {
			// Stalled for more than two frames: resume at the grain due now.
			next = uint64(int64(CurrentIndex(rate)) - int64(offset/period))
			continue
		}
		if wait > 0 {
			if !timer.Stop() {
				select {
				case <-timer.C:
				default:
				}
			}
			timer.Reset(time.Duration(wait))
			select {
			case <-ctx.Done():
				return
			case <-timer.C:
			}
		}
		if w.closed.Load() {
			return
		}

		index := next
		next++

		frame := w.latestV210.Load()
		if frame == nil {
			continue
		}
		ref = w.videoRef.Load()
		if ref == nil || ref.writer == nil {
			return
		}

		// Claim-then-confirm, as upstream: publish the ring slot we read so
		// WriteVideo drops rather than overwrites it, then re-check latest.
		for frame.slot >= 0 {
			w.v210InFlight.Store(int64(frame.slot))
			latest := w.latestV210.Load()
			if latest == frame {
				break
			}
			frame = latest
		}

		err := ref.writer.WriteGrain(index, frame.data)
		if frame.slot >= 0 {
			w.v210InFlight.Store(-1)
		}
		if err != nil {
			w.log.Error("mxl writer: failed to write video grain",
				"error", err, "index", index)
		}
	}
}

// noteArrival records the phase, within the grain period, at which a frame
// was published (circular exponential average). DMF patch.
func (w *Writer) noteArrival() {
	ref := w.videoRef.Load()
	if ref == nil || ref.rate.Numerator <= 0 || ref.rate.Denominator <= 0 || NowNs == nil || IndexToTimestampNs == nil {
		return
	}
	now := NowNs()
	period := float64(ref.rate.Denominator) * 1e9 / float64(ref.rate.Numerator)
	start := IndexToTimestampNs(ref.rate, CurrentIndex(ref.rate))
	theta := 2 * math.Pi * float64(int64(now)-int64(start)) / period
	w.arrivalMu.Lock()
	alpha := 0.05
	if w.arrivalN < 20 {
		alpha = 1 / float64(w.arrivalN+1)
	}
	w.arrivalC = (1-alpha)*w.arrivalC + alpha*math.Cos(theta)
	w.arrivalS = (1-alpha)*w.arrivalS + alpha*math.Sin(theta)
	w.arrivalN++
	w.arrivalMu.Unlock()
}

// arrivalPhaseNs returns the average arrival phase in [0, period).
func (w *Writer) arrivalPhaseNs(period float64) (float64, bool) {
	w.arrivalMu.Lock()
	defer w.arrivalMu.Unlock()
	if w.arrivalN == 0 {
		return 0, false
	}
	phase := math.Atan2(w.arrivalS, w.arrivalC)
	if phase < 0 {
		phase += 2 * math.Pi
	}
	return phase / (2 * math.Pi) * period, true
}
'''


def replace_tick_loop(src):
    start = src.index("func (w *Writer) videoTickLoop(ctx context.Context) {")
    end = src.index("\n}\n", start) + 3
    return src[:start] + TICK_LOOP + src[end:]


def add_math_import(src):
    if '\t"math"\n' in src:
        return src
    if '\t"log/slog"\n' not in src:
        sys.exit("mxl-writer-phase: writer.go: imports changed upstream")
    return src.replace('\t"log/slog"\n', '\t"log/slog"\n\t"math"\n', 1)


def note_arrivals(src):
    old = "\tw.latestV210.Store(&v210Frame{data: buf, slot: nextIdx})\n"
    if src.count(old) != 2:
        sys.exit("mxl-writer-phase: writer.go: expected two frame publications")
    return src.replace(old, old + "\tw.noteArrival() // DMF patch\n")


patch("writer.go", [
    ("\tv210BufHeight int\n}\n",
     "\tv210BufHeight int\n"
     "\n"
     "\t// DMF patch: phase-aligned MXL video writer. Circular average of the\n"
     "\t// phase at which frames are published within the grain period.\n"
     "\tarrivalMu sync.Mutex\n"
     "\tarrivalC  float64\n"
     "\tarrivalS  float64\n"
     "\tarrivalN  int\n"
     "}\n"),
    (replace_tick_loop, None),
    (add_math_import, None),
    (note_arrivals, None),
])
print("mxl-writer-phase: MXL video grains written once each, phase-aligned to frame arrivals")
