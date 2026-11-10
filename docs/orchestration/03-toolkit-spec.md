# DMF Toolkit changes

Repo: `/home/dansi/DMF_Toolkit`, distro `Ubuntu`. Its `docs/design-rules.md` is the single source for
its decisions: every change below updates the rule and adds a decision-log line in the same change.

Target versions: library `4.0.0`, `apiVersion` `dmf-toolkit/v1alpha4`, `switchframe.1` version `1.1.0`.
Bump the schema version if stored data changes. The database has no saved workloads.

## 1. Feature toggles (changes R3.15, new R3.16)

Today `recording` isn't an input or output (`isOutputFeature` needs ports), so it can't be turned off,
and nothing else can either. An instance therefore always "uses" playout and speech-to-text.

- `ModuleBlock` gains `optional?: { default: boolean; addons?: Record<string, boolean> }`.
- `FunctionInstance` gains `features?: Record<string, boolean>`, keyed by block id or `<block>.<addon key>`.
- `instanceDesign` leaves out a disabled block and a disabled add-on's group, so no tab, parameters or publishing.
- `toPublished` always writes the full selection in force.
- The function dialog gets a Features section. Add-on toggles are shown only while their block is on.

Switchframe's optional blocks:

| Block | Default | | Block | Default |
|---|---|---|---|---|
| `recording` | off | | `scte35.scte104` | off |
| `captions` | off | | `replay` | on |
| `captions.asr` | off | | `playout` | off |
| `scte35` | off | | `ai-segment` | off |

These match the product's own defaults. Remove the `scte35.scte35.enabled` parameter.

## 2. Instance defaults (changes R3.12, R4.3)

A new instance now starts with every input and output, including ST 2110 and MoQ, and a health check
of `8080/healthz`, which is wrong for SwitchFrame.

`VendorModule` gains `defaults?`:

```ts
defaults?: {
  inputs?: InputSelection
  outputs?: OutputSelection
  control?: ControlSpec
  resources?: ResourceProfile
  discovery?: Discovery
  parameters?: { [block: string]: JsonValue } // over the schema's defaults, for a new instance only
}
```

`newFunctionData` uses them. Without them, behaviour stays as it is. A module's identity rule stays
(R4.2): these are starting values for an instance, not infrastructure decisions.

| Module | `inputs` | `outputs` | `control.health` | `resources` | `discovery` | `parameters` |
|---|---|---|---|---|---|---|
| `switchframe.1` | `mxl-src: 1` | `mxl-out: 1` | http 9090 `/health` | cpu 2, memoryMiB 4096 | `register: true` | none |
| `dmf-mxl.1` | none | `mxl: 1` | exec | cpu 0.25, memoryMiB 256 | `register: true` | `mxl.format.video`: 1920x1080, 29.97 |
| `dmf-mxl.2` | none | `mxl: 1` | exec | cpu 0.25, memoryMiB 256 | `register: true` | none: `allowed` already gives the format |

`discovery` is set directly, not through `control.nmos`, because these products don't register
themselves: this facility's `mxl-flow-agent` registers every flow in the domain.

The shared `output.mxl` default stays at its current value, so other modules don't change.

## 3. Flows of unread outputs (changes R6.6)

`flowsOf` skips outputs nobody reads ([workload.ts:560](/mnt/wsl/instances/Ubuntu/home/dansi/DMF_Toolkit/src/model/workload.ts)), but the writer still needs a flow
definition. New rule: the video and audio flows of every MXL output in use are always published;
a data flow only when read. `readers` may be empty.

### Blocks with no data flow (changes R6.1)

`ModuleBlock` gains `noData?: boolean`. A video-feed MXL block with it set has `video` and `audio`
ports and no `anc` port, so dragging a bundle can't create a data wire. Set it on `mxl` in both
`dmf-mxl` modules, which write no data, and on Switchframe's `mxl-out`. At the pinned commit
`server/cmd/switchframe` opens no data writer for the programme output, and there's no flag for a
data flow definition. `mxl-src` keeps its optional `anc` port: the product does read one.

Correct `switcher.output.mxl`'s description, which promises VANC data, and the `switcher.captions`
description's mention of MXL VANC.

## 4. Valid format combinations (closes an open question)

Add a schema keyword on a parameter group, enforced by `validateValue`:

```json
"x-allowed": { "fields": ["resolution", "rate"], "values": { "1920x1080": ["23.976","24","25","29.97","30","50","59.94","60"], "1280x720": ["25","29.97","30","50","59.94","60"], "3840x2160": ["25","29.97","30","50","59.94","60"] } }
```

The UI limits the rate choices accordingly. The message names both values, e.g. "2160p24 is not a Switchframe format".

A module using a shared feature can't change that feature's schema, so `ModuleBlock.allowed` narrows
a group for one block, using the same structure, keyed by group path. When only one combination is
left, it becomes the default. It covers the listed fields only: scan and colorspace on test sources
are left to compile rule 5.

## 5. Switchframe module (changes R7.7; update `docs/products/switchframe.md`)

- Add `engine.engine.onAirAtStart`: string, `x-options: "inputs"`, meaning the choices are the instance's input copies. Default: the first. Publishing fails if it names a copy not in use.
- Add `recording.recording.storageGiB`: integer ≥ 1, required, `x-audience: orchestrator`, default 50.
- `srt-src.srt.streamId`: required in both modes, pattern `^[A-Za-z0-9_-]{1,64}$`.
- `srt-src.srt.address` (caller): `host:port`, pattern `^[A-Za-z0-9.-]+:[0-9]{1,5}$`.
- `scte35.scte35.defaultRule`: `pass` or `drop` only.
- `recording.recording.rotateAfterMinutes`: minimum 1, because the product treats 0 as 60.
- `srt-out.srt.streamId`, `rtmp-out`: unchanged.
- Remove `control.control.access.*` and `control.control.allowedOrigins`.
- Remove `off` from `captions.captions.mode`: the feature toggle is how captions are turned off. Default stays `passthrough`.
- `switcher.scte104` has `key: scte104`, so its toggle is `scte35.scte104`. It requires `switcher.input.mxl` only, and its description says it reads SCTE-104 from MXL data flows: the product attempts writes, but the programme output has no data writer to carry them.
- `onAirAtStart`, when unset, is published as the first input in use.
- Add to `frameSync`'s description: "Leave off for MXL sources; they are already locked to the facility clock, and freerun drops single frames."
- Refresh the mapping doc from [02-switchframe-options.md](02-switchframe-options.md).

## 6. Workload access (new R8.5; update the contract doc)

`spec.access?: { roles?: Record<string, string[]> }`, usernames per role. Inspector → Workload panel.
Roles offered: `director`, `audio`, `graphics`, `captioner`, `viewer`. Validation: a director is
required to publish; usernames match `^[A-Za-z0-9._-]+$`. Whether the user exists is checked at deployment.

## 7. Test source modules (R3.1)

New company and catalog: vendor `DMF MXL`, id `dmf-mxl`, file `catalogs/dmf-mxl.catalog.json`, built by
`scripts/product-catalogs.ts`. Palette shows it under its company heading. Category tag: `acquisition`.

| Module | Models | Blocks |
|---|---|---|
| `dmf-mxl.1` "MXL test source" | `mxl-gst-testsrc` | `pattern` (feature `test.pattern`), `mxl` (feature `output.mxl`) |
| `dmf-mxl.2` "A/V sync test source" | `mxl-syncgen`: white frame and 1 kHz beep every 30 frames | `mxl` (feature `output.mxl`) |

Feature `test.pattern`, group `testing` in the Common section, key `pattern`, audience `function`:
- `pattern`: enum `smpte`, `ball`, `snow`, `black`, `white`, `red`, `green`, `blue`, `checkers-8`, `circular`, `smpte75`, default `smpte`. These are GStreamer `videotestsrc` nicknames; confirm against the tool before finalising.
- `overlayText`: string, optional, default is the function's name, applied by the compiler.

Extend `output.mxl`'s `rate` enum with `23.976`, `24` and `30`.

Both have `output.mxl` as an output block with `scaleOut` 1 and no inputs.

`dmf-mxl.2` is fixed at 1080p29.97, and the module enforces that with `ModuleBlock.allowed`.

Also part of the model since stage 1: publishing drops parameter groups for anything an instance
doesn't run; `requires` rules are checked per instance; access role names live in the library
vocabulary; and schema v10 removes stored module versions that no workload uses and that no longer
match the library, which catalog loading then replaces.

## 8. Facility capabilities (stage 7 only; new R8.6)

- `DMF_FACILITY` env var: path to a JSON file exported by `make facility-export` in the orchestrator.
- `GET /api/facility` returns its `capabilities` (id → boolean) and `name`, or `{}` if unset.
- Library features gain `needs?: string[]`. Enum values can state needs too: `"x-enumNeeds": {"neural": ["neural-key"]}`.
- The dialog disables what's unavailable and says why. `publishProblems` reports a selected option that's unavailable. With no facility set, everything stays offered.

| Feature or value | `needs` |
|---|---|
| `switcher.input.st2110`, `switcher.output.st2110` | `st2110` |
| `switcher.input.moq` | `moq` |
| `switcher.asr` | `asr` |
| `key.type = neural` | `neural-key` |
| `switcher.ai-segment` | `ai-segment` |
| `switcher.scte104` | `scte104` |
| `srt-out.srt.mode = listener` | `srt-listener-out` |

These are product limits, so they apply with or without a facility (found in stage 6):

- `publishProblems` refuses an `rtmp-out` copy with a `standard-hevc` or `professional` colour profile. The dialog says why.
- EQ and limiter ranges match the mixer's, as listed in [02-switchframe-options.md](02-switchframe-options.md). Narrowing needs no module version bump while no saved workload uses the version.
- Help text: an enabled key is over programme all the time. A replay buffer of 300 s holds only up to about 5.6 Mbit/s per source. The SCTE-35 default rule `drop` applies to every cue with no matching rule, including operators' cues. An all-features 1080p design should request 4 CPUs.

## 9. XML publish (stage 2; extends R8.2)

- `GET /api/workloads/:id/published.xml[?revision=N]`, `content-type: application/xml; charset=utf-8`. Same 422 and publish checks as the JSON route; serialisation failures are also 422.
- `src/model/xml.ts`: `toWorkloadXml(published)` and `fromWorkloadXml`. Hand-written with no new runtime dependency. `fromWorkloadXml` is for tests and may use a small dev-only parser.
- A **Publish XML** action in the app downloads the file.
- `schemas/dmf-workload-v1.xsd`: byte-identical copy with a hash test.
- Encoding: [01-contract.md](01-contract.md).
