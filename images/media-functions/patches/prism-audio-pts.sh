#!/bin/sh
# Fix scratchy browser audio: send full-precision audio timestamps over MoQ.
#
# Prism (the MoQ distribution library SwitchFrame uses) writes the audio
# capture timestamp as uint32(frame.PTS / 1000) * 1000. PTS is in 90 kHz
# ticks (video sends it unchanged and the SwitchFrame UI decodes audio
# timestamps as 90 kHz ticks too), so audio timestamps were rounded to
# 1000 ticks = 11.1 ms. A 1024-sample AAC frame lasts 21.33 ms, so frames
# landed up to 11 ms early or late; the browser player schedules audio by
# timestamp, leaving gaps/overlaps on nearly every frame: crackle on every
# source, regardless of CPU or network.
#
# This makes WriteAudioFrame take the 90 kHz PTS and send it unchanged,
# exactly like WriteVideoFrame. Captions/data objects are untouched.
#
# usage: prism-audio-pts.sh <writable copy of the prism module>
set -eu
cd "${1:?usage: $0 <prism module dir>}/distribution"
grep -q 'WriteAudioFrame(w io.Writer, data \[\]byte, timestampMS uint32)' protocol.go \
  || { echo "prism-audio-pts: already patched or upstream changed; skipping"; exit 0; }

# Interface
sed -i 's/WriteAudioFrame(w io.Writer, data \[\]byte, timestampMS uint32) (int64, error)/WriteAudioFrame(w io.Writer, data []byte, pts int64) (int64, error)/' protocol.go

# Implementation: only inside WriteAudioFrame (WriteDataObject keeps ms).
awk '
  /^func \(m \*moqWriter\) WriteAudioFrame\(/ {
    sub(/timestampMS uint32/, "pts int64"); inaudio = 1
  }
  inaudio && /uint64\(timestampMS\)\*1000/ {
    sub(/uint64\(timestampMS\)\*1000/, "uint64(max(pts, 0))"); $0 = $0 " // 90 kHz PTS, as for video"
  }
  inaudio && /^}/ { inaudio = 0 }
  { print }
' moq_writer.go > moq_writer.go.new && mv moq_writer.go.new moq_writer.go

# Upstream test asserted the old ms*1000 behaviour; the PTS now passes through.
if [ -f moq_writer_test.go ]; then
  sed -i 's|if val != 5_000_000 { // 5000ms \* 1000 = 5000000µs|if val != 5000 { // 90 kHz PTS passes through unchanged|; s|t.Errorf("capture timestamp: got %d, want 5000000", val)|t.Errorf("capture timestamp: got %d, want 5000", val)|' moq_writer_test.go
fi

# Call site
sed -i 's/n, err := sub.writer.WriteAudioFrame(stream, frame.Data, tsMS)/n, err := sub.writer.WriteAudioFrame(stream, frame.Data, frame.PTS)/' moq_session.go

grep -q 'WriteAudioFrame(stream, frame.Data, frame.PTS)' moq_session.go
grep -q 'uint64(max(pts, 0))' moq_writer.go
echo "prism-audio-pts: audio capture timestamps now carry the full 90 kHz PTS"
