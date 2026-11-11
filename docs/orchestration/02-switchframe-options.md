# SwitchFrame options

Every option, who owns it, and where it lands. Paths are `parameters` paths; `<src>` is any input
copy (`srt-src`, `mxl-src_2`, …). Flags and routes exist at the pinned commit
`79236154`; check request bodies in that commit's `docs/reference/api/` before coding them.

That commit's docs are wrong in these places, found in stage 6: the gate range is -80 to -20 dB, SCTE-35 rule actions are `pass`, `delete` and `replace`, and the playout cache is in `<state dir>/playout-cache`, not `--cache-dir`.

**Stage** is the build stage that makes the option work. **G** means gated: offered only when the
facility has that capability, and refused by the compiler otherwise. All capabilities are listed
at the end.

## Set by the user

### Instance

| Option | Target | Stage |
|---|---|---|
| Copies per input and output block | Selects blocks below | 4 |
| Feature on/off (`features`) | See each feature | 4 onward |
| `access.roles` (workload level) | Keycloak group membership, `spec.access.members` | 4 |
| `resources.cpu`, `.memoryMiB` | Pod requests; limits = 3× CPU, 2× memory; `GOMEMLIMIT` = 75% of memory limit; these ratios reproduce today's chart defaults. CPU is reserved by limit (see the 04 spec's Sizing), so the default of 2 reserves 6 CPUs, which covers the 3.6 cores an all-features 1080p29.97 design averaged without throttling. Don't raise it on this host: 4 reserves 12 for SwitchFrame alone, and the workload can't fit in 16 | 4 |
| `resources.gpu` | **G `gpu`** | Not built |

### Engine (`engine.engine.*`)

| Option | Target | Stage |
|---|---|---|
| `resolution` + `rate` | `--format`, e.g. `1080p29.97`. Valid: 1080p at 23.976, 24, 25, 29.97, 30, 50, 59.94, 60; 720p and 2160p at 25, 29.97, 30, 50, 59.94, 60. 720p doesn't work with MXL at the pinned commit (V210 needs a width divisible by 6), so it's refused with an MXL source or `mxl-out` | 4, 7 |
| `colorProfile` | `--color-format` | 4 |
| `frameSync` | `off`: none; `freerun`: `--frame-sync`; `source-driven`: `--low-latency-sync`. Compiler warns on `freerun` with MXL sources: this repo measured 40% of single-frame flashes lost | 4 |
| `frameRateConversion` | `--frc-quality` | 4 |
| `onAirAtStart` (new): an input copy id, default the first | Cut to its key when nothing is on air: by the chart's hook in stage 4, the reconciler from stage 5. Needed because programme flows are only published with a source on air | 4 |

### MXL

| Option | Target | Stage |
|---|---|---|
| `mxl-src` copies (wires) | `--mxl-sources video:audio[:data]`, flow IDs from wires | 4 |
| `mxl-out` copies (0 or 1) | `--mxl-output`, `--mxl-output-video-def`, `--mxl-output-audio-def`, from published `flows` | 4 |
| Data on `mxl-out` | Not offered: `mxl-out` has no `anc` port. The product opens no data writer for its programme output at the pinned commit | Not built |

### SRT input (`<src>.srt.*`)

| Option | Target | Stage |
|---|---|---|
| `mode` = listener | `--srt-listen` with an allocated port; one shared listener | 5 |
| `streamId` (now required) | Key `srt:<streamId>` | 5 |
| `latencyMs` (listener) | Most common value to `--srt-latency`; others `PUT /api/sources/{key}/srt` | 5 |
| `mode` = caller; `address` (`host:port`), `streamId`, `latencyMs`, `inputBandwidthMbps`, `overheadPercent` | `POST /api/sources` once; the product persists it. `inputBW` = Mbit/s × 125000 | 5 |

### Every source (`<src>.source`, `.audio`, `.key`)

An SRT listener source has no API object until its encoder connects. The reconciler keeps checking and applies these when it appears. In stage 4 the chart's hook sets MXL labels; the reconciler takes over in stage 5.

| Option | Target | Stage |
|---|---|---|
| `source.label` | `POST /api/sources/{key}/label`. The product's limit is 128 bytes; the compiler refuses longer UTF-8 | 4 |
| `source.position` | `PUT /api/sources/{key}/position` | 6 |
| `source.delayMs` | `POST /api/sources/{key}/delay` | 6 |
| `audio.trimDb`, `.afv` | `POST /api/audio/trim`, `POST /api/audio/afv` | 6 |
| `audio.delayMs` | `PUT /api/audio/{source}/audio-delay` | 6 |
| `audio.highPass` | `PUT /api/audio/{source}/hpf` | 6 |
| `audio.gate` | `PUT /api/audio/{source}/gate` | 6 |
| `audio.eq.low`, `.mid`, `.high` | `PUT /api/audio/{source}/eq`, one call per band, `band` 0, 1, 2. Ranges are the mixer's (`server/audio/eq.go`), narrower than the API's check: low 80–1000 Hz, mid 200–8000 Hz, high 1000–16000 Hz, gain ±12 dB, Q 0.5–4 | 6 |
| `audio.compressor` | `PUT /api/audio/{source}/compressor` | 6 |
| `key.type` = chroma, luma | `PUT /api/sources/{source}/key` with `enabled: true`; `off`: `DELETE`, where a 404 means already off. An enabled key is composited over programme all the time, from when it's applied, so the design choice means "always keyed" | 6 |
| `key.type` = neural | **G `neural-key`** | Not built |

### Encoder and outputs

| Option | Target | Stage |
|---|---|---|
| `encoder.encoder.bitrateMbps` | `--output-bitrate`, bit/s | 5 |
| `.gopSeconds`, `.preset`, `.timecode` | `--output-gop-secs`, `--output-preset`, `--output-timecode` | 5 |
| `srt-out.srt.*`, mode = caller | `POST /api/output/destinations` type `srt-caller`, then `…/{id}/start`. Not persisted, so the reconciler recreates it; matched by `name` = block id. `maxBandwidth` in bit/s (the product divides by 8). `scte35Enabled` always written: absent means false. Encryption `off` = field absent | 5 |
| `srt-out.srt.passphrase` | Secret, in `passphrase`. `GET /api/output/destinations` returns it, so nothing this repo logs or archives may contain that response unredacted | 5 |
| `srt-out`, mode = listener | **G `srt-listener-out`**: the chart opens no output ports | Not built |
| `rtmp-out.rtmp.url` | Destination type `rtmp`, `url`. Refused with a `standard-hevc` or `professional` colour profile: the product's HEVC to H.264 transcoder is not wired up at the pinned commit (`createDefaultTranscoder` returns an error), so it reports the destination active and sends nothing | 5, 6 |
| `rtmp-out.rtmp.streamKey` | Secret, appended to `url`: the product ignores `streamID` for RTMP. The product masks the URL in its own logs. A build patch reduces `ControlRoomState`'s copy to scheme and host, so `GET /api/switch/state` and the state broadcast carry no key. `GET /api/output/destinations` and `GET /api/sync/state-snapshot` still return the full URL, and the product lets every GET through regardless of role, so a build patch refuses them to operators whose role can't command output. `GET /api/debug/snapshot` also carried them, and viewers need it for the stats panel, so its destinations are redacted for everyone. Same redaction rule as the passphrase | 5, 6 |
| `recording` on/off; `recording.recording.*` | `POST /api/recording/start` `{outputDir: "/recordings", rotateAfterMins, maxFileSizeMB, filenamePrefix}`. `rotateAfterMins: 0` means 60 in the product, so the minimum is 1; started once per SwitchFrame run, and restarted only if it failed: an operator's clean stop stands. Writes an empty file until a source is on air | 5 |
| `recording.recording.storageGiB` (new, required when on) | Size of a new recordings PVC | 5 |

### Other features

| Option | Target | Stage |
|---|---|---|
| `audio.master.limiter` | `PUT /api/audio/limiter`. Ranges are the mixer's: threshold -12 to 0 dB, ceiling -3 to 0 dB | 6 |
| `replay` on/off; `bufferSeconds` | `--replay-buffer-secs` (0 when off). The product caps each source at 200 MiB and 8 sources, so 300 s holds only up to about 5.6 Mbit/s per source; above that the window is shorter than asked | 6 |
| `replay.replay.shutterAngle` | `PUT /api/replay/shutter-angle` `{angle}` | 6 |
| `clips.clips.storageMaxGB` | `--clip-storage-max`, bytes, where GB = 2³⁰. Clips live in the state directory, so this adds to the state PVC | 6 |
| `clips.clips.ephemeralTtlHours` | `--clip-ephemeral-ttl` as a duration, e.g. `48h` | 6 |
| `captions` on/off; `captions.captions.mode` (`passthrough`, `author`) | On: `--captions`, `POST /api/captions/mode`. Off: neither | 6 |
| `scte35` on/off; `pid`, `prerollMs`, `heartbeatMs`, `verify`, `webhookUrl` | `--scte35`, `--scte35-pid`, `--scte35-preroll`, `--scte35-heartbeat`, `--scte35-verify`, `--scte35-webhook` | 6 |
| `scte35.scte35.defaultRule` (`pass`, `drop`) | `PUT /api/scte35/rules/default` `{action}`: `pass` → `pass`, `drop` → `delete`. The product's `replace` needs replacement parameters, so it isn't a default. `drop` applies to every cue with no matching rule, including operators' cues: a design with `drop` and no rules can never send a cue | 6 |
| `playout` on/off; `cacheMaxGB`, `asRunRetentionDays` | `--playout`, `--playout-cache-max`, `--playout-asrun-retention`. The cache is in the state directory, so this adds to the state PVC | 6 |
| `control.control.preview*` | `--preview-proxy`, `--preview-resolution`, `--preview-bitrate` (bit/s) | 4 |
| `control.control.relay*` | `--relay-resolution`, `--relay-bitrate` (bit/s) | 6 |
| `settings` (free-form) | Only `settings.extraArgs` (string array) is used: appended to the args. Compiler warns on it and refuses `--demo` | 4 |

## Gated

| Capability | Options | Why unavailable here |
|---|---|---|
| `st2110` | `st2110-src`, `st2110-out` | Needs DPDK NICs |
| `moq` | `moq-src` | Source naming and admission are unresolved |
| `gpu` | `resources.gpu` | No GPU build in this repo |
| `asr` | `captions.asr` | Image has no speech-to-text sidecar |
| `neural-key` | `key.type = neural` | No CUDA build |
| `ai-segment` | `ai-segment` | No CUDA build |
| `srt-listener-out` | `srt-out` mode listener | Chart opens no output ports |
| `scte104` | `scte35.scte104` | Depends on MXL data flows, which aren't built |

## Removed from the user's view

| Option | Now |
|---|---|
| `control.control.access.*` (API token, invites) | Terraform generates them; SSO is the login path |
| `control.control.allowedOrigins` | Compiler sets it from the hostname |
| `scte35.scte35.enabled` | Replaced by the feature toggle |
| Control and health endpoints | Prefilled from the product; the compiler uses the chart's values and warns on a difference |

## Orchestrator decides

Never in the XML. See [04-orchestrator-spec.md](04-orchestrator-spec.md).

Workload UID/GID, MXL domain path and size, hostname, QUIC and SRT ports, tokens, image, volumes,
TLS, quota, SSO client, flow agent, namespace, and network policy.

## Live-operated: not configured

Cuts, transitions, fader moves, graphics on/off, replay marks, clip and playout transport, SCTE-35
cues, comms, DVE and preset recall, macros, colour grading and ST map. These stay with operators.

## Ignored for now

Accepted and carried in the XML, but they change nothing on this single-host facility. The toolkit
publishes defaults for some of them, so the compiler warns once for each only when its value differs
from the toolkit's default: `network.*`, `payload.*`, `placement.*`,
`expectations.resilience` other than `none`, `discovery.labels`, `discovery.register: false`,
and `dependencies`. `flows[].historyMs` gets no warning: the schema requires it, so every workload
has one.

`discovery.register: false` can't be honoured yet because `mxl-flow-agent` registers every flow in the domain.

`historyMs` isn't passed to MXL yet. `mxl-gst-testsrc` has `--video-options-file`, so this can come later.
