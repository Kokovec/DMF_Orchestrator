# A/V sync measurement on MXL flows

Measures audio/video alignment of an input and of SwitchFrame's programme
output directly in the MXL Flow Domain (uncompressed, sample-accurate; no codec
in the measurement path).

1. **Sync source.** Add an `mxl-syncsrc` function to the workload and list it in
   the mixer's `sources` (news-studio has one, `sync`, labelled "Sync test").
   It runs `mxl-syncgen` (`images/media-functions/mxl-syncgen/`): a white frame
   on every grain whose MXL index is a multiple of 30 and a one-frame 1 kHz beep
   on every sample whose index modulo 48048 is below 1602. Both patterns are
   functions of the MXL index (TAI time), so flash and beep are aligned exactly
   at the source, from any start. Measured at its own flows: 0.0 ms.

   A file looper (`mxl-gst-looping-filesrc` with a flash/beep clip) is not a
   reliable reference: it sets the audio and video MXL time offsets separately
   when each decoder delivers its first buffer (measured: 6 to 20 ms, different
   on every start), video then snaps to the grain grid, and each loop re-times
   both streams again. An ffmpeg `enable=` beep gate is also evaluated per
   1024-sample frame, which drifts the beep 1.7 ms per second against the flash.
2. **Probe** input and programme flows at the same time, with `sync` on programme
   (build with `gcc -O2 -o mxl-av-probe mxl-av-probe.c -lmxl -lm` against the MXL SDK,
   run as the workload uid/gid with the Flow Domain mounted):
   ```bash
   mxl-av-probe <domain> <syncVideoId> <syncAudioId> 20 > in.txt &
   mxl-av-probe <domain> <pgmVideoId> <pgmAudioId> 20 > out.txt; wait
   python3 analyse.py in.txt out.txt
   ```
   Flow ids are `uuidv5(URL namespace, "dmf://<workload>/<function>/<video|audio>")`.

Result on news-studio (2026-10-31, file-looper clip standing in for Camera 1):
input→programme latency **video 66.7 ms, audio 71.0 ms**, so SwitchFrame adds
~4 ms of relative audio delay (EBU R37 tolerance is +40/−60 ms). The ±20 ms
absolute offsets seen in that run came from the clip and looper described above,
not from SwitchFrame; latency differences between input and output are unaffected.
