#!/bin/sh
# Let SwitchFrame's MXL inputs survive a restart of the Media Function that
# writes them.
#
# When an input (camera) pod restarts, its writer recreates the flow and the
# reader's mapping of the old flow becomes invalid. The read loops in
# server/mxl/reader.go count every non-transient error and stop for good
# after 50 in a row (about 50 ms), so the source stays black until
# SwitchFrame itself is restarted.
#
# This wraps the video and audio flow handles opened in cmd/switchframe/app.go
# in a reader that, on any non-transient error, releases the handle and
# reopens the flow by id (at most every 500 ms), reporting ErrTimeout in the
# meantime so the read loops keep waiting instead of counting errors. MXL
# indexes grains by time, so the loops keep their position across the
# reopen (if it fell behind they re-sync to the head as usual).
#
# usage: mxl-reader-reopen.sh <switchframe/server>
set -eu
cd "${1:?usage: $0 <switchframe/server>}"
[ -f mxl/reopen.go ] && { echo "mxl-reader-reopen: already patched; skipping"; exit 0; }

cat > mxl/reopen.go <<'GO'
package mxl

import (
	"errors"
	"fmt"
	"log/slog"
	"sync"
	"time"
)

// reopenInterval rate-limits reopen attempts while a flow is unavailable.
const reopenInterval = 500 * time.Millisecond

// DiscreteOpener opens discrete (video/data) flows by id.
type DiscreteOpener interface {
	OpenReader(flowID string) (DiscreteReader, error)
}

// ContinuousOpener opens continuous (audio) flows by id.
type ContinuousOpener interface {
	OpenAudioReader(flowID string) (ContinuousReader, error)
}

// isTransient reports errors the read loops already handle without counting.
func isTransient(err error) bool {
	return errors.Is(err, ErrTimeout) || errors.Is(err, ErrTooEarly) || errors.Is(err, ErrTooLate)
}

// reopener holds the shared reopen state for one flow.
type reopener[R interface{ Close() error }] struct {
	mu       sync.Mutex
	flowID   string
	open     func() (R, error)
	cur      R
	ok       bool
	closed   bool
	lastOpen time.Time
	log      *slog.Logger
}

// get returns the current handle, reopening the flow if it was dropped.
func (o *reopener[R]) get() (R, bool) {
	o.mu.Lock()
	defer o.mu.Unlock()
	if o.ok || o.closed || time.Since(o.lastOpen) < reopenInterval {
		return o.cur, o.ok
	}
	o.lastOpen = time.Now()
	r, err := o.open()
	if err != nil {
		return o.cur, false
	}
	o.cur, o.ok = r, true
	o.log.Info("mxl: flow reopened", "flowID", o.flowID)
	return r, true
}

// drop releases the handle after a non-transient error.
func (o *reopener[R]) drop(r R, err error) {
	o.mu.Lock()
	defer o.mu.Unlock()
	if !o.ok || any(o.cur) != any(r) {
		return // already dropped (or replaced) by another caller
	}
	o.log.Warn("mxl: flow reader invalid, reopening", "flowID", o.flowID, "error", err)
	_ = o.cur.Close()
	o.ok = false
	o.lastOpen = time.Time{}
}

func (o *reopener[R]) close() error {
	o.mu.Lock()
	defer o.mu.Unlock()
	o.closed = true
	if !o.ok {
		return nil
	}
	o.ok = false
	return o.cur.Close()
}

func unavailable(flowID string) error {
	return fmt.Errorf("%w: flow %s unavailable, reopening", ErrTimeout, flowID)
}

// waitUnavailable keeps the caller's read loop from spinning while a flow is gone.
func waitUnavailable(timeoutNs uint64) {
	d := time.Duration(timeoutNs)
	if d <= 0 || d > 50*time.Millisecond {
		d = 50 * time.Millisecond
	}
	time.Sleep(d)
}

type reopeningDiscrete struct {
	o   *reopener[DiscreteReader]
	cfg FlowConfig
}

// NewReopeningReader wraps an open discrete flow reader so it reopens the flow
// (by id) after the flow's writer restarts.
func NewReopeningReader(opener DiscreteOpener, flowID string, r DiscreteReader, log *slog.Logger) DiscreteReader {
	if log == nil {
		log = slog.Default()
	}
	return &reopeningDiscrete{
		o: &reopener[DiscreteReader]{flowID: flowID, open: func() (DiscreteReader, error) { return opener.OpenReader(flowID) },
			cur: r, ok: true, lastOpen: time.Now(), log: log},
		cfg: r.ConfigInfo(),
	}
}

func (w *reopeningDiscrete) ReadGrain(index uint64, timeoutNs uint64) ([]byte, GrainInfo, error) {
	r, ok := w.o.get()
	if !ok {
		waitUnavailable(timeoutNs)
		return nil, GrainInfo{}, unavailable(w.o.flowID)
	}
	data, info, err := r.ReadGrain(index, timeoutNs)
	if err != nil && !isTransient(err) {
		w.o.drop(r, err)
		return nil, GrainInfo{}, unavailable(w.o.flowID)
	}
	return data, info, err
}

func (w *reopeningDiscrete) ConfigInfo() FlowConfig { return w.cfg }

func (w *reopeningDiscrete) HeadIndex() (uint64, error) {
	r, ok := w.o.get()
	if !ok {
		return 0, unavailable(w.o.flowID)
	}
	idx, err := r.HeadIndex()
	if err != nil && !isTransient(err) {
		w.o.drop(r, err)
		return 0, unavailable(w.o.flowID)
	}
	return idx, err
}

func (w *reopeningDiscrete) Close() error { return w.o.close() }

type reopeningContinuous struct {
	o   *reopener[ContinuousReader]
	cfg FlowConfig
}

// NewReopeningAudioReader is NewReopeningReader for continuous (audio) flows.
func NewReopeningAudioReader(opener ContinuousOpener, flowID string, r ContinuousReader, log *slog.Logger) ContinuousReader {
	if log == nil {
		log = slog.Default()
	}
	return &reopeningContinuous{
		o: &reopener[ContinuousReader]{flowID: flowID, open: func() (ContinuousReader, error) { return opener.OpenAudioReader(flowID) },
			cur: r, ok: true, lastOpen: time.Now(), log: log},
		cfg: r.ConfigInfo(),
	}
}

func (w *reopeningContinuous) ReadSamples(index uint64, count int, timeoutNs uint64) ([][]float32, error) {
	r, ok := w.o.get()
	if !ok {
		waitUnavailable(timeoutNs)
		return nil, unavailable(w.o.flowID)
	}
	pcm, err := r.ReadSamples(index, count, timeoutNs)
	if err != nil && !isTransient(err) {
		w.o.drop(r, err)
		return nil, unavailable(w.o.flowID)
	}
	return pcm, err
}

func (w *reopeningContinuous) ConfigInfo() FlowConfig { return w.cfg }

func (w *reopeningContinuous) HeadIndex() (uint64, error) {
	r, ok := w.o.get()
	if !ok {
		return 0, unavailable(w.o.flowID)
	}
	idx, err := r.HeadIndex()
	if err != nil && !isTransient(err) {
		w.o.drop(r, err)
		return 0, unavailable(w.o.flowID)
	}
	return idx, err
}

func (w *reopeningContinuous) Close() error { return w.o.close() }
GO

cat > mxl/reopen_test.go <<'GO'
package mxl

import (
	"errors"
	"sync/atomic"
	"testing"
	"time"
)

type fakeDiscrete struct {
	gen    int
	broken *atomic.Bool
	closed bool
}

func (f *fakeDiscrete) ReadGrain(index uint64, _ uint64) ([]byte, GrainInfo, error) {
	if f.broken.Load() {
		return nil, GrainInfo{}, errors.New("mxl: read grain: flow invalid")
	}
	return []byte{byte(f.gen)}, GrainInfo{Index: index}, nil
}
func (f *fakeDiscrete) ConfigInfo() FlowConfig     { return FlowConfig{} }
func (f *fakeDiscrete) HeadIndex() (uint64, error) { return 100, nil }
func (f *fakeDiscrete) Close() error               { f.closed = true; return nil }

type fakeOpener struct {
	opens   int
	fail    atomic.Bool
	readers []*fakeDiscrete
}

func (o *fakeOpener) OpenReader(string) (DiscreteReader, error) {
	if o.fail.Load() {
		return nil, errors.New("mxl: open reader: flow not found")
	}
	o.opens++
	r := &fakeDiscrete{gen: o.opens, broken: new(atomic.Bool)}
	o.readers = append(o.readers, r)
	return r, nil
}

// A writer restart (invalid flow, then the flow reappears) must never surface
// a non-transient error to the read loop, and reads must resume on a new handle.
func TestReopeningReaderSurvivesWriterRestart(t *testing.T) {
	op := &fakeOpener{}
	first, _ := op.OpenReader("f")
	r := NewReopeningReader(op, "f", first, nil)

	if d, _, err := r.ReadGrain(1, 0); err != nil || d[0] != 1 {
		t.Fatalf("initial read: %v %v", d, err)
	}
	op.readers[0].broken.Store(true)
	op.fail.Store(true) // writer gone for a while
	deadline := time.Now().Add(2 * reopenInterval)
	for time.Now().Before(deadline) {
		if _, _, err := r.ReadGrain(2, 1_000_000); !errors.Is(err, ErrTimeout) {
			t.Fatalf("while unavailable: want ErrTimeout, got %v", err)
		}
	}
	if !op.readers[0].closed {
		t.Fatal("invalid handle was not released")
	}
	op.fail.Store(false) // writer back
	var got []byte
	for i := 0; i < 100 && got == nil; i++ {
		got, _, _ = r.ReadGrain(3, 1_000_000)
	}
	if got == nil || got[0] != 2 {
		t.Fatalf("did not resume on a reopened handle: %v (opens=%d)", got, op.opens)
	}
	if err := r.Close(); err != nil || !op.readers[1].closed {
		t.Fatalf("close: %v", err)
	}
}
GO

# Wrap the flow handles where the MXL sources are opened.
f=cmd/switchframe/app.go
grep -q 'videoFlow, err := inst.OpenReader(videoFlowID)' "$f" || { echo "mxl-reader-reopen: app.go changed upstream" >&2; exit 1; }
awk '
  /videoFlow, err := inst.OpenReader\(videoFlowID\)/ { print; invideo = 1; next }
  invideo && /^\t\t}$/ {
    print
    print "\t\tif err == nil {"
    print "\t\t\t// DMF patch: reopen the flow after its writer restarts."
    print "\t\t\tvideoFlow = mxl.NewReopeningReader(inst, videoFlowID, videoFlow, slog.Default())"
    print "\t\t}"
    invideo = 0; next
  }
  /^\t\t\t\taudioFlow = af$/ {
    print "\t\t\t\taudioFlow = mxl.NewReopeningAudioReader(inst, audioFlowID, af, slog.Default()) // DMF patch"
    next
  }
  { print }
' "$f" > "$f.new" && mv "$f.new" "$f"
[ "$(grep -c 'NewReopening' "$f")" = 2 ] || { echo "mxl-reader-reopen: app.go edit failed" >&2; exit 1; }
echo "mxl-reader-reopen: MXL inputs reopen their flows after a writer restart"
