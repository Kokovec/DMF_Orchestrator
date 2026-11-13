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
| `make start WORKLOAD=<id>` | Also waits for Keycloak and the workload's SSO gateway to answer before opening Chrome. Otherwise the first login after a cold start gets "no available server", then a 500 on the callback |
| `make start` (no `WORKLOAD`) | Starts the only deployed workload. With none deployed it starts the facility, waits for Keycloak and exits 0; with several it lists them and exits 0. A default of `news-studio` failed once that was finalised |
| `make plan` | Also: says the facility isn't running when Kubernetes doesn't answer; waits up to 5 minutes for Keycloak before Terraform, which logs in to it while planning; and names any access-role member that isn't a Keycloak user, instead of Terraform's error |

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
    functionsPerWorkload: {switchframe.1: {min: 1, max: 1}}   # rule 2; Terraform runs one SwitchFrame
    domainsPerWorkload: 1                                      # rule 2
```

## Compile rules

Refuse with a message naming function and parameter. Report all problems, not just the first.

1. `schemaVersion`, `apiVersion`, module type and version are known.
2. Function counts and the domain count are within the facility's `limits.functionsPerWorkload` and `limits.domainsPerWorkload`; for `local`, exactly one `switchframe.1` function (assumption 2) and one domain. Read from the profile, so the toolkit's exported copy and the compiler can't disagree.
3. Facility capability exists for each selected input, output, feature and enum value (see [02-switchframe-options.md](02-switchframe-options.md)).
4. `resolution` + `rate` is a real SwitchFrame format. 720p is refused when the design has an MXL source or `mxl-out`: at the pinned commit SwitchFrame's V210 converters (`server/mxl/v210.go`) need a width divisible by 6, and 1280 isn't, so every MXL frame fails (stage 7B). Name the format and the reason. Lift this only with a build patch that handles the remainder pixels.
5. Every MXL source flow matches the engine: frame size, rate, `video/v210`, progressive, BT709, and `transfer` absent or SDR; audio 48 kHz float32. `dmf-mxl.2` requires 1080p29.97. Name the source, its value and the engine's value: the toolkit checks this too from stage 7A, and this catches hand-edited XML. No wire reads `mxl-out.anc`: the toolkit can't produce one, so this only catches hand-edited XML.
6. Every required secret exists in `.state/secrets/<id>.yaml`. Stage 5 onward.
7. No hand-written `workloads/<id>.yaml` exists for this id. The generated header is information only.
8. Other things the generated YAML must satisfy: everything `workload-plan.yml` asserts.
9. No `rtmp-out` copies with a `standard-hevc` or `professional` colour profile. The product can't transcode them and reports success while sending nothing.
10. The workload's `uid` (from the XML, not its POSIX UID) and flow IDs aren't used by another workload in `.state/allocations.json`, which stores both. Two running workloads with the same flow IDs conflict in NMOS.

Refuse a `streamId` used by more than one SRT input of a function: listeners and callers share keys.

Warn, and continue: `freerun` with MXL sources, `settings.extraArgs` in use, SCTE-104 on with no `mxl-src` `anc` port wired (the product only reads when a data flow exists), and the "Ignored for now" options.
Refuse `--demo` in any args.

## Allocation (`allocate.py`)

`.state/allocations.json` maps workload id → `{uid, quicPort, srtPort}` plus the XML uid and flow IDs for rule 10. Re-importing keeps the
allocation; new workloads take the lowest free values. `finalise` releases them.

- `uid = gid`, from `workloadUids`. `hostname = <id>.<baseDomain>`, `domain = <id>`.
- `plan` adds a port check to the existing UID and domain check: its task name says ports, but today it doesn't check them.
- `plan` refuses a generated workload whose state, cache or recordings size differs from its live PVC, in either direction, naming the volume, both sizes and the two ways out: restore the value, or finalise and deploy again. `local-path` has no expansion, and a changed size otherwise fails partway through the apply and leaves the Deployment changed. PVC sizes are fixed after first provision.
- `plan` refuses a design that turns recording off while the workload has a live recordings PVC. The chart would delete the PVC, and `local-path` its files, on the next apply, which bypasses D9. The way out is to keep recording on, or finalise (which archives recordings) and deploy again.
- `plan` refuses a workload whose new PVCs total more than the free disk under the `local-path` root. `local-path` doesn't enforce a PVC's size, so nothing else catches this.
- A hand-written workload's UID, domain and ports (`news-studio`: 20001, 8443, 6464) always count as taken: the compiler can't see the cluster, and the workload could be deployed later.

## Sizing (`sizing.py`)

- **MXL domain** (measured in stage 4: 106 MiB used by `studio-a`): `sizeMiB = max(512, ceil(1.25 × (Σ video flows grains × V210 frame bytes + 1 MiB per audio flow) / 2²⁰) + 256)`, with `grains = max(5, ceil(historyMs × rate / 1000))`. V210 frame bytes = `ceil(width / 6) × 16 × height`. `studio-a`: 657 MiB; 2160p59.94 with 4 flows: 3426 MiB.
- **Pods:** `switchframe` from `resources` (see options doc), with the replay tmpfs added to its memory request and limit but not to `GOMEMLIMIT`, because the Go heap doesn't hold it; test sources 250m/256Mi requests, 1/1Gi limits; flow agent 50m/64Mi, CPU limit 500m; gateway 30m/56Mi, CPU limit 1 (its two containers set none, so each gets the LimitRange's 500m); reconciler 20m/32Mi, CPU limit 200m.
- **Capacity check:** `capacity.py` counts every workload's Flow Domain (tmpfs is RAM) as reserved, not only this one's.
- **CPU is reserved by limit, not request:** media functions are real time, so a workload reserves the sum of its pods' CPU limits, and `capacity.py` compares that with allocatable CPU. It counts another workload as the larger of its quota and its live pods' CPU limits, so one provisioned before this rule is still counted in full; platform services still count by request. On 2026-11-10, `studio-a` and `full-settings` together passed the request-based check (about 5 CPUs requested) but used 14.8 of 16 cores, with a load average of 42; test sources sat at their limit and the UI lost sync. Alone, `studio-a` used 2.9 cores in total. On this host that means one compiled workload at a time.
- **Quota:** `resourceProfile.cpu`, and the quota's `requests.cpu`, = sum of pod CPU limits, rounded up to whole CPUs: 11 for `studio-a` (6 + 3 × 1 + 0.5 + 1 + 0.2). It's held as `requests.cpu`, which it always covers, not as `limits.cpu`: a `limits.cpu` quota at the exact sum would leave no room for the gateway's rolling-update surge pod. `requests.memory` = sum of pod memory requests + 20%, rounded up. `news-studio` is hand-written and keeps its hand-set 7 CPU and 10 GiB.
- **Volumes:** state PVC = `max(5, clips.storageMaxGB + 1 + playout.cacheMaxGB when playout is on)` GiB; the playout cache is in `<state dir>/playout-cache`. Recordings PVC = `recording.storageGiB`. `/cache` is a PVC, not an emptyDir, sized ST-map prewarm + 2 GiB. SwitchFrame prewarms the ST-map tables on every start into `--cache-dir` and skips tables already there (`stmap.DirComplete`), so a PVC generates them once per workload. An emptyDir regenerates about 16 GiB, 3 minutes of CPU, at each restart; at 5 GiB it caused evictions every 20–90 s in stage 4. Prewarm = 5 effects × 90 frames × 18 bytes per pixel, so the `/cache` PVC is 9 GiB at 720p, 18 at 1080p (16 of tables + 2) and 65 at 2160p. Finalise doesn't archive it: it's regenerable.
- **Replay tmpfs:** `min(sources, 8) × 200 + 64` MiB, nothing when replay is off. The product gives each source one 200 MiB ring whatever the buffer length, and every ring ends up full, measured in stage 6 (evidence in `sizing.py`). Sized for designed sources: an operator-added source also takes a ring, and if the tmpfs fills, the product's replay writes fail rather than the pod.

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
  resourceProfile: {cpu: "11", memory: 6Gi, gpu: 0}
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
output the way both lines read it. Stage 4 anchors `facility.sh`'s match to `^ *kind: *switchframe *(#.*)?$`, allowing a trailing comment as `news-studio.yaml` has,
because today a function named `switchframe` also matches.

Write both files atomically. Also copy the source to `workloads/<id>.source.xml`. Add `workloads/*.config.json` and `*.source.xml` to what `finalise` archives.

## Terraform and chart changes

Terraform plan and state files hold secret values, so they stay private (600, 700). Hand-written workloads get a generated config that reproduces the old hook. Terraform finds `<id>.config.json` beside the workload file. The generated YAML's `programSource` field is what Terraform and the provision playbook read for the on-air source.

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
2. For each desired source: resolve its key (see [01-contract.md](01-contract.md)). Skip it if not present yet. Apply settings once per appearance, and again if it disappears and returns. For SRT sources an appearance is a connection: wait for `srt.connected` and reapply on each one, because SwitchFrame resets label and position every time and API-set values never reach its store.
3. Remove SRT caller pulls that the design used to have and no longer does: the product persists them across restarts in `srt_sources.json`. The reconciler records the stream IDs it manages in a file on the state volume and only ever removes those. A pull an operator added in the UI is never touched.
4. Once per SwitchFrame run, create and start destinations missing by `name`, so an operator's deletion stands until the next restart. Recreate only if the desired config changed.
5. Once per SwitchFrame run, start `recording` if desired. Restart it only if it failed, not after an operator's clean stop.
6. If nothing is on air and `program` is present, cut to it.

Requirements:
- Never fails the pod and never exits on API errors. Logs as JSON. Doesn't overwrite operator changes: a setting is applied once per appearance, not every loop.
- A call's identity is its content, so a new revision loaded without a restart applies only the calls that changed. The applied and failed sets are pruned to the current config's calls on each pass, so a value changed back to an earlier one is applied again (stage 7B review).
- A registration timeout means the API isn't ready: retry registration, don't fall back to the bare token, which SwitchFrame answers with 403 `operator not identified` once operators exist. A 403 `operator not identified` on any call drops the operator, registers again and retries the call, rather than counting it as failed. So does a 401 on the reconciler's own operator token, which SwitchFrame returns as `invalid token` once it no longer knows that operator (`server/control/auth.go`).
- Redacts passphrases and RTMP URLs from anything it logs, including API responses.
- Exposes `dmf_reconcile_pending`, `dmf_reconcile_failed`, `dmf_reconcile_api_up` and `dmf_reconcile_errors_total` at `:9103/metrics`, added to the ServiceMonitor.
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
- Before any teardown, stop the recording, then copy `/recordings` to `.state/archive/<run>/recordings`. If the copy fails, stop before `terraform destroy` and say so. `DISCARD_RECORDINGS=1` is the deliberate override.
- Archive `workloads/<id>.source.xml` and `<id>.config.json`.
- Release the allocation. Remove generated workload files only after a successful destroy.
- After a successful destroy, also remove `.state/secrets/<id>.yaml` and `.state/<id>.tfplan`, which hold secret values and are never archived. Deploying the id again then needs its secrets set again, and rule 6 says which (stage 7B review).
- After a successful destroy, also remove `.state/<id>.plan-summary.json`, the facility console's summary of the last plan (stage 9 review).

## Tests

| Level | Covers |
|---|---|
| Unit (`make validate`, no cluster) | Value tree round trip; DOCTYPE refusal; XSD rejection; each compile rule; flow ID test vector; goldens for `studio-a`; helm render of generated values |
| Reconciler | Fake API server in the test: first run, source appears late, restart with persisted caller, removed caller, operator change not overwritten, API down |
| Live (stages 4–7) | Written per prompt, needs `make start` |
