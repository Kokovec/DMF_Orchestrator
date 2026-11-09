# switchframe-dmf

Deploys SwitchFrame on a local EBU DMF-aligned k3s facility, from workloads published as XML by DMF Toolkit.

`docs/orchestration/` defines the build, and its `README.md` lists the decisions and stages. If what you find contradicts it, report the contradiction and propose a change; don't work around it silently.

## Environment

- This repo: WSL distro `Ubuntu-24.04`, `/home/dansi/switchframe-dmf`. Docker, k3s, Terraform, Helm and Ansible run here.
- DMF Toolkit: distro `Ubuntu`, `/home/dansi/DMF_Toolkit`, reachable here at `/mnt/wsl/instances/Ubuntu/home/dansi/DMF_Toolkit`. It has its own `CLAUDE.md`. Treat it as read-only from this repo.
- Node exists only in `Ubuntu`. The toolkit server listens on `localhost:3001` from both distros when it's running.
- `/tmp` doesn't survive a host restart. Keep nothing there you'll need later.

## Commands

| Command | Does |
|---|---|
| `make validate` | All static checks and tests; no cluster needed |
| `make import XML=<file\|http://localhost url>` | Compile only, into `workloads/generated/` (git-ignored) |
| `make deploy XML=...` | Import, plan and provision. Needs sudo |
| `make plan\|provision\|status\|finalise WORKLOAD=<id>` | Lifecycle of one workload. Provision and finalise need sudo |
| `make start\|stop [WORKLOAD=<id>]` | Start or stop the whole facility. Default workload is `news-studio` |
| `make images` | Rebuild images and load them into k3s. Slow, so run it in the background. Needs sudo |
| `make secret-set WORKLOAD=<id> NAME=<name>` | Set a workload secret |

Faster test loops:

```
PYTHONDONTWRITEBYTECODE=1 .tools/venv/bin/python -m unittest discover -s tools/dmf_compile/tests -t .
PYTHONDONTWRITEBYTECODE=1 python3 -I images/media-functions/sf-reconciler/test_reconciler.py
```

Golden files: `UPDATE_GOLDEN=1` on the first command rewrites them. Check that only the lines you expected changed. `SWITCHFRAME_SRC=<checkout>` also checks request bodies against the Go source. Use `KUBECONFIG=.state/kubeconfig` and `.tools/bin/kubectl`.

## Layout worth knowing

- `workloads/<id>.yaml` is hand-written; `workloads/generated/<id>.*` is compiled. Always resolve an id with `scripts/workload-file.sh`.
- `examples/studio-a.workload.xml` is a copy of the toolkit's `scripts/fixtures/studio-a.workload.xml`. When that changes, copy it again and regenerate the golden files.
- `schemas/dmf-workload-v1.xsd` is the source of truth. The toolkit keeps a byte-identical copy.
- `scripts/facility.sh` and `scripts/windows-browser.sh` parse workload YAML with `awk`, so keep the generated format they rely on.

## Working on the live facility

These rules come from things that went wrong.

- Before changing anything live, run `make status` and check `kubectl get ns`. Don't finalise, destroy or restart a workload you didn't start in this session without asking. Finalise deletes the workload's volumes.
- There's no passwordless sudo. When a step needs it, give the user the exact command.
- Before asking for a `make provision`, run `make plan` for every workload your change affects, generated ones included.
- Give `ANSIBLE_LOG_PATH` as an absolute path, because Ansible runs from `ansible/`.
- An Ansible task that ignores failures can hide a lost archive. Verify what a destructive step depends on before recommending it.
- Capacity is about 19.5 GiB of memory, including platform services. `news-studio` reserves 12 GiB and only fits on its own. Compiled workloads reserve about 6 GiB plus their MXL domain, so two fit at once. Check before planning a test that needs more.

## SwitchFrame

- The build is pinned to commit `792361540f148b9ec8a93ec7727919672bd2330d` of https://github.com/Kokovec/switchframe, plus `images/media-functions/patches/`. Check API shapes and units against the source at that commit: its API docs have been wrong.
- It forgets destinations and recording on restart. It resets an SRT source's label and position on each connection. The reconciler in `images/media-functions/sf-reconciler/` handles both.
- It writes about 16 GiB of ST-map tables to `/cache` at 1080p, which is why `/cache` is a sized PVC.

## Secrets

Passphrases and RTMP URLs appear in SwitchFrame API responses, `.state/secrets/`, and Terraform plan and state files. Never log, archive or commit them, and keep those files private.

## Git

Commit only when asked, and never push unless asked. Work is on branch `orchestration-definition`. This distro has no git identity, so pass the last commit's author with `git -c user.name=... -c user.email=... commit`.

Report results as they are: say what failed, and what was only tested against a fake or not run live.
