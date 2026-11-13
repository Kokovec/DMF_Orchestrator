# DMF orchestration: definition

This folder defines how a workload designed in DMF Toolkit becomes running containers on this
facility. It is the source of truth for the build. If an implementation finding contradicts it,
report the contradiction and propose a change here; don't work around it silently.

| Document | Holds |
|---|---|
| [01-contract.md](01-contract.md) | The XML output: encoding, semantics, versioning |
| [02-switchframe-options.md](02-switchframe-options.md) | Every SwitchFrame option: who sets it, where it goes, when it is built |
| [03-toolkit-spec.md](03-toolkit-spec.md) | Changes to DMF Toolkit |
| [04-orchestrator-spec.md](04-orchestrator-spec.md) | Compiler, facility profile, reconciler, chart and lifecycle changes |
| [05-facility-console.md](05-facility-console.md) | The local web console: status, terminal and lifecycle buttons (stages 8 and 9) |
| [prompts/](prompts/) | One self-contained Claude Code prompt per build stage |
| [../../schemas/dmf-workload-v1.xsd](../../schemas/dmf-workload-v1.xsd) | The XML schema |
| [../../examples/studio-a.workload.xml](../../examples/studio-a.workload.xml) | The toolkit's real output for `studio-a`, and the compiler's first golden input |

## The system

```
DMF Toolkit (design)                     switchframe-dmf (orchestrator)
─────────────────────                    ──────────────────────────────────────────────
canvas + function dialog                 make import XML=<file|url>
  │                                        1. refuse DOCTYPE, validate against the XSD
  │ publishProblems                        2. check against facility/<name>.yaml
  ▼                                        3. allocate UID, ports, hostname, domain size
GET /api/workloads/:id/published.xml ──►   4. write workloads/<id>.yaml + <id>.config.json
                                                    │
                                         make plan → make provision → make status → make finalise
                                                    │
                                         k3s/containerd: testsrc/syncsrc, switchframe +
                                         reconciler, SSO gateway, mxl-flow-agent
```

## Decisions

| # | Decision | Why |
|---|---|---|
| D1 | XML is the handover format. The toolkit also keeps its JSON output, and both come from one model | Requested interface. No external schema exists, so we own the XSD |
| D2 | Parameters are encoded generically (`<value path type>`) | New module settings don't change the schema |
| D3 | The compiler generates the existing `workloads/<id>.yaml`, extended | `plan`, `provision`, `finalise` and the scripts keep working, and hand-written workloads stay valid |
| D4 | Compiler is Python in this repo, with one module per product type, and refuses unknown types | Same language as the repo's tools; adding a product is one file |
| D5 | A reconciler sidecar replaces the chart's `postStart` hook | SwitchFrame doesn't persist destinations or recording, and an SRT listener source has no API object until its encoder connects, so a one-shot hook can't apply its settings |
| D6 | The facility profile decides what can run. The compiler always enforces it; the toolkit uses an exported copy to grey out options | The configurator doesn't make infrastructure decisions (toolkit R1.2), but users shouldn't design what can't deploy |
| D7 | The toolkit owns flow IDs | Toolkit contract already says so; Terraform falls back to its own only for hand-written workloads |
| D8 | Secrets are references; values live in `.state/secrets/<id>.yaml` (mode 600, git-ignored), and reach Terraform's plan and state files, which are kept private too | Small and local. Replaceable by a real secret store later |
| D9 | `finalise` copies recordings to the archive before it destroys anything | The local-path volume is deleted with the workload, so without this recordings are lost for good |

## Environment facts

- **Two WSL distros.** Toolkit: distro `Ubuntu`, `/home/dansi/DMF_Toolkit`. Orchestrator: distro `Ubuntu-24.04`, `/home/dansi/switchframe-dmf`.
- **Cross-distro paths:** `/mnt/wsl/instances/Ubuntu/home/dansi/DMF_Toolkit` and `/mnt/wsl/instances/Ubuntu-24.04/home/dansi/switchframe-dmf`. They exist only while each distro has bind-mounted itself there, which isn't automatic: both were missing on 2026-11-09. To expose a distro, run this in it, with its own name: `sudo mkdir -p /mnt/wsl/instances/<distro> && sudo mount --bind / /mnt/wsl/instances/<distro>`.
- **Node:** v24 under nvm in `Ubuntu` only (`~/.nvm/versions/node/v24.21.0/bin`). Not on `PATH` in non-interactive shells, and absent from `Ubuntu-24.04`.
- **Facility:** Docker, k3s, Terraform, Helm and Ansible run in `Ubuntu-24.04`, with tools in `.tools/bin` and Python tools in `.tools/venv`. k3s is stopped until `make start`.
- **No browser in `Ubuntu`:** the toolkit UI has been tested through a jsdom harness kept outside its repo.
- **SwitchFrame source:** pinned commit `792361540f148b9ec8a93ec7727919672bd2330d`, https://github.com/Kokovec/switchframe. Check API shapes here, not from memory.
- **Toolkit database:** holds no saved workloads, so no workload migration is needed.

## Build stages

Run in order, each in a fresh Claude Code session in the named repo. Review before starting the next.

| # | Prompt | Repo | Result |
|---|---|---|---|
| 1 | [1-toolkit-model.md](prompts/1-toolkit-model.md) | DMF_Toolkit | Options fixed, feature toggles, test source modules |
| 1b | [1b-toolkit-corrections.md](prompts/1b-toolkit-corrections.md) | DMF_Toolkit | Stage 1 review decisions applied, reusable fixture kept |
| 2 | [2-toolkit-xml.md](prompts/2-toolkit-xml.md) | DMF_Toolkit | XML publish endpoint and export |
| 3 | [3-compiler.md](prompts/3-compiler.md) | switchframe-dmf | Offline compiler with golden tests |
| 4 | [4-deploy-parity.md](prompts/4-deploy-parity.md) | switchframe-dmf | `studio-a` runs from XML, matching `news-studio` |
| 5 | [5-reconciler-io.md](prompts/5-reconciler-io.md) | switchframe-dmf | Reconciler, secrets, SRT, RTMP, recording |
| 6 | [6-remaining-settings.md](prompts/6-remaining-settings.md) | switchframe-dmf | Remaining settings wired through |
| 7 | [7-gating-e2e.md](prompts/7-gating-e2e.md) | both | Greyed-out options, full end-to-end from the toolkit |
| 8 | [8-console-status.md](prompts/8-console-status.md) | switchframe-dmf | Facility console: status, terminal, toolkit mount |
| 9 | [9-console-actions.md](prompts/9-console-actions.md) | switchframe-dmf | Facility console: lifecycle buttons and deploy flow |

## Done means

From the toolkit, a user designs a workload using only options offered as available, publishes it,
and `make deploy XML=<url>` brings it up with every setting applied. A restart restores them, and
`make finalise` archives design, logs, state and recordings. An option the facility can't honour
is refused before anything deploys, with a message naming the option.

## Assumptions

Reviewed after the stage 7B end-to-end run, 2026-11-11.

1. **Holds.** Nothing outside these two repos reads the XML: no other consumer exists on this facility, and the compiler fetches `published.xml` only from `http://localhost` unless `ALLOW_REMOTE=1`.
2. **Confirmed, and enforced.** One SwitchFrame instance per workload: the facility profile's `limits.functionsPerWorkload` (`switchframe.1`: exactly 1) is applied by both the toolkit and the compiler.
3. **Confirmed, and narrowed.** Workloads share one host, so the MXL domain and all pods stay together. Because CPU is reserved by pod limits, this 16-CPU host runs one compiled workload at a time (`e2e-studio`: 9 CPUs; `studio-a`: 11), and `news-studio` only on its own.
4. **Unchanged, not tested live.** 2160p is allowed through the capacity check rather than blocked. No 2160p workload was deployed.

## Stage 7B: end-to-end result

Run on 2026-11-11 with workload `e2e-studio`, created and revised only through the toolkit's HTTP API (as a stand-in for its UI), with the toolkit serving the exported facility profile. Design: an MXL test source, an SRT listener source, SRT caller output with AES-128, RTMP output, recording, and non-default engine, encoder and audio settings.

| Step | Result |
|---|---|
| Publish and deploy | Passed at 1080p25: every setting read back from the live API; SRT and RTMP outputs received H.264 + AAC by a local receiver (the SRT one with the passphrase); recording written. At 720p50 it deployed, but its MXL source stayed offline: see the first item below |
| New revision, deploy again | Passed after a reconciler fix. A config-only change (limiter) restarted nothing: same containers, destination IDs and flow IDs; the reconciler applied just that call. Before the fix, a hot-loaded config was ignored until SwitchFrame restarted |
| `make stop`, `make start` | Passed: settings, destinations, recording and programme restored with no action; the SRT source was reconfigured when its encoder reconnected |
| Hand-edited ST 2110 | Refused at compile with the option named (rule 3); no file, allocation or cluster object changed |
| `make finalise` | Passed: design, config, source XML, logs, events, resources, state and 46 recordings archived; allocation released; namespace and Flow Domain gone; no secret value in the archive |
| `news-studio` | Provisioned from its hand-written file and came up with labels and programme |

Fixed during the run: provision restarted every Deployment straight after a first provision, so each function started twice (`workload-provision.yml` now restarts only Deployments that existed before the apply); and the reconciler tracked applied calls by position, so a changed value was never applied (now by content, with a test).

What the run doesn't prove: the toolkit's UI in a browser; picture, sound and A/V sync as a viewer sees them; sign-in. See the checks in the stage report.

## Stage 9: console result

Run on 2026-11-13 from the console in a browser, as the README runbook does it by hand, with a
throwaway workload needing one SRT passphrase. The design and its revision were created through the
toolkit's HTTP API, not its UI. Every action ran from a button; only sudo passwords and the secret
value were typed in the terminal.

| Step | Result |
|---|---|
| Finalise the deployed workload | Archived, namespace gone, allocation released |
| Deploy | The import was refused on exactly the one missing secret. After it was set: re-import, plan, review (28 to add, capacity fits), provision. On air at revision 1 |
| Settings-only revision, Update | The designs panel showed the newer revision. Review: 2 in-place changes, no restarts. After provision: the same pods, with no restarts, and the label at 2 |
| Stop, Start | Stopped showed "deployed, stopped (Flow Domain unit)"; Start chose the only deployed workload. Back on air |
| Finalise, Redeploy from the archive | The secret was asked for again; the review showed revision 2. On air |
| Restore | The throwaway workload finalised and its design deleted. The original workload redeployed from its archive with the same uid and ports, on air. Its SwitchFrame runtime state was not carried over; it stays in the archive |

Fixed during the run: the restart check read Terraform's `[false]` unknown-mask as unknown and said
"may restart" for a settings-only change; a review dialog stayed open in another browser after the
deploy moved on; the Finalise dialog described the playbook's steps in the wrong order. Finalise now
also deletes the plan summary.

## Contradictions found, with proposed changes

1. **720p doesn't work with MXL.** At the pinned commit, SwitchFrame's 8-bit V210 converters (`server/mxl/v210.go`, `V210ToYUV420pInto` and `YUV420pToV210Into`) refuse a width not divisible by 6, and 1280 isn't, so every MXL source frame and the MXL programme output fail at 720p. [02-switchframe-options.md](02-switchframe-options.md) lists 720p as valid. **Ruled 2026-11-11:** compile rule 4 refuses 720p when the design has an MXL source or `mxl-out`, and the toolkit refuses the same as a product limit, until a build patch handles the remainder pixels.

## Not built

From [02-switchframe-options.md](02-switchframe-options.md), gated or ignored:

- ST 2110 input and output (needs DPDK NICs); MoQ input; SRT listener output (the chart opens no output ports).
- GPU resources, neural key, AI segmentation (no CUDA build); speech-to-text captions (no ASR sidecar); SCTE-104 (needs MXL data flows).
- Data on `mxl-out`; `historyMs` passed to MXL; `discovery.register: false`; `network.*`, `payload.*`, `placement.*` and `dependencies`, which are carried but change nothing on one host.

Found in stage 7B, and the review rulings on them (2026-11-11):

- **Built, unit-tested:** compile rule 4 refuses 720p with an MXL source or `mxl-out`. The toolkit's matching product limit ([03-toolkit-spec.md](03-toolkit-spec.md) section 8) is not built yet.
- **Built, unit-tested:** the reconciler prunes its applied and failed sets to the current config, so a value changed back is applied again; a registration timeout means "not ready" (no token fallback); a 403 `operator not identified`, or a 401 on its own operator's token, registers again and retries the call. The 401 case goes slightly beyond the ruling: it is how SwitchFrame answers an operator token it no longer knows (`server/control/auth.go`). Live only once the `mxl-tools` image is rebuilt and a workload provisioned with it.
- **Built:** `finalise` removes `.state/secrets/<id>.yaml` and `.state/<id>.tfplan` after a successful destroy. Exercised on a scratch directory by starting the playbook at that task; not run in a full live finalise.
- **Recording drops, not built and not changed.** The counter is SwitchFrame's `AsyncAdapter` (`server/output/async_adapter.go`): a TS packet is dropped when the recorder's 256-packet buffer is full, so the recording loses data while disk writes fall behind. The about 6,000 drops came in the first minutes after a first start, at the same time as SwitchFrame was writing its 16 GiB of ST-map tables to the same disk; that's a coincidence in time, not a measured cause. The run after `make start`, with the cache already written, logged no drop in its 2.5 minutes of recording. `studio-a` doesn't record, so this couldn't be rechecked there. Drops during a first start's prewarm are likely, and a longer recording with a warm cache hasn't been measured.
