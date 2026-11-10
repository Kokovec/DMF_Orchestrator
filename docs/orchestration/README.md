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

## Done means

From the toolkit, a user designs a workload using only options offered as available, publishes it,
and `make deploy XML=<url>` brings it up with every setting applied. A restart restores them, and
`make finalise` archives design, logs, state and recordings. An option the facility can't honour
is refused before anything deploys, with a message naming the option.

## Assumptions to confirm

1. Nothing outside these two repos reads the XML.
2. One SwitchFrame instance per workload, as Terraform requires today.
3. Workloads share one host, so the MXL domain and all pods stay together.
4. 2160p is allowed through the capacity check rather than blocked, even though it is unlikely to fit on this machine.
