# Orchestrator

Repo: `/home/dansi/switchframe-dmf`, distro `Ubuntu-24.04`.

## Layout

```
schemas/dmf-workload-v1.xsd
examples/studio-a.workload.xml
facility/local.yaml
tools/dmf_compile/
  __main__.py     CLI: compile, facility-export
  reader.py       DOCTYPE refusal, XSD validation, XML → model
  facility.py     profile loading, capability checks
  allocate.py     UID, ports, hostname; .state/allocations.json
  sizing.py       domain size, quota, volumes
  emit.py         workloads/<id>.yaml and <id>.config.json
  products/switchframe.py, products/dmf_mxl.py
  tests/          stdlib unittest; golden files
images/media-functions/sf-reconciler/reconciler.py
```

Stdlib plus `xmlschema` and PyYAML. Add `xmlschema` to the pip line in `scripts/prereqs.sh`. Run with
`.tools/venv/bin/python -m`. `scripts/validate.sh` runs the compiler's tests and `py_compile`.

## Make targets

| Target | Does |
|---|---|
| `make import XML=<path or http(s) url> [FACILITY=local]` | Compile only. Prints warnings and the allocation. Reads a URL only with `http://localhost` or an explicit `ALLOW_REMOTE=1` |
| `make deploy XML=…` | `import`, then `plan` and `provision` for the workload id in the file |
| `make facility-export [FACILITY=local]` | Writes `.state/facility-<name>.json` for the toolkit |
| `make secret-set WORKLOAD=<id> NAME=<name>` | Prompts without echo; writes `.state/secrets/<id>.yaml`, mode 600 |

## Facility profile

`facility/local.yaml`:

```yaml
apiVersion: dmf.local/v1alpha1
kind: Facility
metadata: {name: local}
spec:
  baseDomain: dmf.test        # must match the inventory's dmf_base_domain
  workloadUids: {first: 20001, last: 29999}
  ports:
    quic: {first: 8443, last: 8499}
    srt: {first: 6464, last: 6499}
  capabilities:
    mxl: true
    srt-in: true
    srt-caller-out: true
    rtmp-out: true
    recording: true
    srt-listener-out: false
    st2110: false
    moq: false
    gpu: false
    asr: false
    neural-key: false
    ai-segment: false
    scte104: false
  limits:
    maxRecordingGiB: 200
```

## Compile rules

Refuse with a message naming function and parameter. Report all problems, not just the first.

1. `schemaVersion`, `apiVersion`, module type and version are known.
2. Exactly one `switchframe.1` function (assumption 2). One domain.
3. Facility capability exists for each selected input, output, feature and enum value (see [02-switchframe-options.md](02-switchframe-options.md)).
4. `resolution` + `rate` is a real SwitchFrame format.
5. Every MXL source flow matches the engine: frame size, rate, `video/v210`, progressive, BT709, and `transfer` absent or SDR; audio 48 kHz float32. `dmf-mxl.2` requires 1080p29.97. Name the source, its value and the engine's value: the toolkit doesn't compare a source's format with the engine, so this is where a mismatch is caught. No wire reads `mxl-out.anc`: the toolkit can't produce one, so this only catches hand-edited XML.
6. Every required secret exists in `.state/secrets/<id>.yaml`. Stage 5 onward.
7. No hand-written `workloads/<id>.yaml` exists for this id. The generated header is information only.
8. Other things the generated YAML must satisfy: everything `workload-plan.yml` asserts.

Warn, and continue: `freerun` with MXL sources, `settings.extraArgs` in use, SCTE-104 on with no `mxl-src` `anc` port wired (the product only reads when a data flow exists), and the "Ignored for now" options.
Refuse `--demo` in any args.

## Allocation (`allocate.py`)

`.state/allocations.json` maps workload id → `{uid, quicPort, srtPort}`. Re-importing keeps the
allocation; new workloads take the lowest free values. `finalise` releases them.

- `uid = gid`, from `workloadUids`. `hostname = <id>.<baseDomain>`, `domain = <id>`.
- `plan` adds a port check to the existing UID and domain check: its task name says ports, but today it doesn't check them.
- A hand-written workload's UID, domain and ports (`news-studio`: 20001, 8443, 6464) always count as taken: the compiler can't see the cluster, and the workload could be deployed later.

## Sizing (`sizing.py`)

- **MXL domain** (measured in stage 4: 106 MiB used by `studio-a`): `sizeMiB = max(512, ceil(1.25 × (Σ video flows grains × V210 frame bytes + 1 MiB per audio flow) / 2²⁰) + 256)`, with `grains = max(5, ceil(historyMs × rate / 1000))`. V210 frame bytes = `ceil(width / 6) × 16 × height`. `studio-a`: 657 MiB; 2160p59.94 with 4 flows: 3426 MiB.
- **Pods:** `switchframe` from `resources` (see options doc); test sources 250m/256Mi requests, 1/1Gi limits; flow agent 50m/64Mi; gateway 30m/56Mi; reconciler 20m/32Mi.
- **Capacity check:** `capacity.py` counts every workload's Flow Domain (tmpfs is RAM) as reserved, not only this one's.
- **Quota:** `requests.cpu` and `requests.memory` = sum of pod requests + 20%, rounded up. Compare `news-studio`'s hand-set 7 CPU and 10 GiB when reviewing: the new value will be lower, and that is correct as long as pods schedule.
- **Volumes:** state PVC = `clips.storageMaxGB + 1` GiB, floor 5. Recordings PVC = `recording.storageGiB`. `/cache` is a PVC, not an emptyDir, sized ST-map prewarm + 2 GiB, plus `playout.cacheMaxGB` when playout is on. SwitchFrame prewarms the ST-map tables on every start into `--cache-dir` and skips tables already there (`stmap.DirComplete`), so a PVC generates them once per workload. An emptyDir regenerates about 16 GiB, 3 minutes of CPU, at each restart; at 5 GiB it caused evictions every 20–90 s in stage 4. Prewarm = 5 effects × 90 frames × 18 bytes per pixel: 9 GiB at 720p, 18 at 1080p, 65 at 2160p. Finalise doesn't archive it: it's regenerable.
- **Replay tmpfs:** the chart's 1 GiB stays for stage 4. Stage 6 measures use at 300 s for 3 sources, then sets a formula. It counts against pod memory.

## Where generated files go

Stage 4 onward: the compiler writes to `workloads/generated/<id>.{yaml,config.json,source.xml}`, which
is git-ignored, because generated files are build output, archived by `finalise`. Hand-written
workloads stay in `workloads/<id>.yaml`. One resolver, `scripts/workload-file.sh <id>`, is used by the
playbooks, Terraform's caller and the scripts. It returns the hand-written file, else the generated
one, and fails if both exist. That replaces rule 7's header check. Stage 3 wrote to `workloads/`;
stage 4 moves the output.

## Generated workload (`dmf.local/v1alpha2`)

`news-studio.yaml` stays valid as `v1alpha1`. Added fields are optional, so Terraform handles both.

```yaml
# GENERATED by tools/dmf_compile from studio-a.workload.xml revision 1. Do not edit.
apiVersion: dmf.local/v1alpha2
kind: MediaWorkload
metadata:
  name: studio-a
  description: ...
  uid: 9dd110d0-8c14-5c77-acf6-3eb708bf0c13
  revision: 1
spec:
  resourceProfile: {cpu: "4", memory: 6Gi, gpu: 0}
  mediaExchange: {domain: studio-a, uid: 20002, gid: 20002, sizeMiB: 2048}
  access:
    hostname: studio-a.dmf.test
    quicPort: 8444
    srtPort: 6465
    members: {director: [director1], audio: [audio1], viewer: [viewer1]}
  functions:
    - name: cam1
      kind: mxl-testsrc
      pattern: smpte
      label: "Camera 1"
      flows: {video: <id>, audio: <id>}
    - name: vision-mixer
      kind: switchframe
      format: 1080p29.97
      sources: [cam1, cam2, sync]
      programOutput: true
      flows: {video: <id>, audio: <id>}
      resources: {requests: {cpu: "2", memory: 4Gi}, limits: {cpu: "6", memory: 8Gi}}
      goMemLimit: 6GiB
      volumes: {stateGi: 11, cacheGi: 5}
      configFile: studio-a.config.json
      extraArgs: [--preview-proxy]
```

**Keep these literal formats:** `scripts/windows-browser.sh` and `scripts/facility.sh` parse this
file with `awk`. The workload's `hostname:` must be the first line matching `^ *hostname:`, and each
function must have `- name:` on the line immediately before `kind:`. The compiler checks its own
output the way both lines read it. Stage 4 anchors `facility.sh`'s match to `^ *kind: *switchframe *$`,
because today a function named `switchframe` also matches.

Write both files atomically. Also copy the source to `workloads/<id>.source.xml`. Add `workloads/*.config.json` and `*.source.xml` to what `finalise` archives.

## Terraform and chart changes

Terraform finds `<id>.config.json` beside the workload file. The generated YAML's `programSource` field is what Terraform and the provision playbook read for the on-air source.

`terraform/stacks/workload/main.tf`:
- `flow_id` uses `try(f.flows.video, <current uuidv5>)`, and likewise for audio.
- `formats` adds 720p and 2160p. Flow definitions take width and height from the format, not the hard-coded 1920×1080.
- `configure.programSource` no longer assumes `sources[0]` exists: an SRT-only workload has none, which breaks today.
- Reads `configFile` and passes its content to the chart; a changed config rolls the reconciler only.
- The chart quotes every arg, so a value containing `: ` can't break the rendered YAML. Its `mxlSources` helper keeps the data flow (`video:audio:data`).
- Stage 5: creates `Secret workload-refs` from `.state/secrets/<id>.yaml`. Values reach Terraform state, which is local, git-ignored and already holds this workload's tokens.

`ansible/playbooks/workload-provision.yml`:
- `expected_flows` counts `mxl-testsrc` and `mxl-syncsrc` as today, so stays correct for generated files.
- The programme-on-air check is skipped when the on-air source is an SRT listener whose encoder may not be connected, and reports that as a warning.

`charts/switchframe`, in stage 4: sized cache and state volumes, plus `values` for the stage 4 args in the options doc. The `postStart` hook stays and still sets labels and the first cut.

`charts/switchframe`, in stage 5: remove the hook; add the reconciler container and recordings PVC. The reconciler must satisfy the `dmf-media-workload-guardrails` admission policy, and the network policy must let monitoring reach its metrics port.

## Reconciler

`images/media-functions/sf-reconciler/reconciler.py`, shipped in the `mxl-tools` image alongside
`mxl-flow-agent`. Python stdlib. A sidecar in the SwitchFrame pod, reaching the API at
`127.0.0.1:<httpPort>`.

Config: `/etc/switchframe/config.json` from a ConfigMap. Secrets by file from `workload-refs`, never in logs. Token: the existing `SWITCHFRAME_API_TOKEN`.

Auth: register a short-lived named `dmf-automation-<epoch>` director operator, as the current hook does. Commands need an identified operator once operators exist.

Loop: every 5 s for the first 2 minutes after start, then every 30 s:

1. Wait for `GET /api/switch/state`.
2. For each desired source: resolve its key (see [01-contract.md](01-contract.md)). Skip it if not present yet. Apply settings once per source appearance, and again if it disappears and returns.
3. Remove SRT caller pulls no longer desired: the product persists them across restarts in `srt_sources.json`.
4. Create and start destinations missing by `name`. Recreate only if the desired config changed.
5. If `recording` is desired and not running, start it.
6. If nothing is on air and `program` is present, cut to it.

Requirements:
- Never fails the pod and never exits on API errors. Logs as JSON. Doesn't overwrite operator changes: a setting is applied once per appearance, not every loop.
- Redacts passphrases and RTMP URLs from anything it logs, including API responses.
- Exposes `dmf_reconcile_pending` and `dmf_reconcile_errors_total` at `:9103/metrics`, added to the ServiceMonitor.
- Uses the current hook's auth and retry logic as its starting point.

`config.json`: explicit calls with `{key}` placeholders the reconciler resolves at runtime,
`program: {input, ref}`, and destinations as `{name, body, secretFields | secretUrlSuffix}`. The
reference is `tools/dmf_compile/tests/golden/full-settings.config.json`, which is checked
field by field against the Go request structs. The example below is the original sketch, kept for
the intent of each part; where it differs, the golden file wins.

```json
{
  "program": {"sourceRef": "mxl:<flow id>"},
  "sources": [
    {"ref": {"kind": "mxl", "key": "mxl:<flow id>"},
     "label": "Camera 1", "position": 1, "delayMs": 0,
     "audio": {"trimDb": 0, "afv": true}, "key": {"type": "off"}},
    {"ref": {"kind": "srt-listener", "key": "srt:venue1", "latencyMs": 200}},
    {"ref": {"kind": "srt-caller", "streamID": "venue2", "address": "host:9000",
             "latencyMs": 200, "inputBW": 0, "overheadBW": 25}}
  ],
  "master": {"limiter": {"threshold": -3, "ceiling": -1}},
  "captions": {"mode": "passthrough"},
  "replay": {"shutterAngle": 180},
  "scte35": {"defaultAction": "pass"},
  "destinations": [
    {"name": "srt-out", "type": "srt-caller", "address": "host", "port": 9000, "latency": 120,
     "streamID": "pgm", "encryption": "aes-256", "passphraseSecret": "venue-srt",
     "maxBandwidth": 0, "scte35Enabled": true},
    {"name": "rtmp-out", "type": "rtmp", "url": "rtmp://host/app", "streamIDSecret": "yt-key"}
  ],
  "recording": {"outputDir": "/recordings", "rotateAfterMins": 60, "maxFileSizeMB": 0, "filenamePrefix": "program"}
}
```

Field names come from the API docs at the pinned commit. If a request body differs, follow the source and correct this example.

## Finalise

The archive path is fixed once per run. Before any teardown, finalise checks that the archive has the design, the resources and, with pods running, a SwitchFrame state archive that passes `tar -tzf`. Only after a successful destroy and domain removal does it release the allocation and delete the generated files.


`workload-finalise.yml`:
- Before any teardown, copy `/recordings` to `.state/archive/<run>/recordings`. If the copy fails, stop before `terraform destroy` and say so. `DISCARD_RECORDINGS=1` is the deliberate override.
- Archive `workloads/<id>.source.xml` and `<id>.config.json`.
- Release the allocation. Remove generated workload files only after a successful destroy.

## Tests

| Level | Covers |
|---|---|
| Unit (`make validate`, no cluster) | Value tree round trip; DOCTYPE refusal; XSD rejection; each compile rule; flow ID test vector; goldens for `studio-a`; helm render of generated values |
| Reconciler | Fake API server in the test: first run, source appears late, restart with persisted caller, removed caller, operator change not overwritten, API down |
| Live (stages 4–7) | Written per prompt, needs `make start` |
