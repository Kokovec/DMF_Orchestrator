# Facility console

A local web page that shows the state of this facility and runs its lifecycle: deploy a design
published by DMF Toolkit, start and stop the facility, finalise a workload, redeploy one from the
archive. Every action runs the existing `make` target in a terminal shown on the page, so the
console does nothing that can't be done by hand, and `make` stays the only implementation.

Agreed 2026-11-13:

| # | Decision | Why |
|---|---|---|
| C1 | Input is the published XML only, from the toolkit's API or the archive. No JSON | D1: XML is the contract the compiler validates |
| C2 | "Spin down" is `make stop`, which stops the whole facility | On this host one compiled workload runs at a time, so a per-workload stop adds nothing |
| C3 | Removing a workload is `make finalise`: archive, then delete. There's no delete without archive | D9 |
| C4 | The console shows whether this distro is bind-mounted for the toolkit, and can mount it. It doesn't start the toolkit server; it only shows what the server's API reports | Node is only in the toolkit's distro, and `wsl.exe` doesn't run from here. The mount is what the toolkit needs from this side (`DMF_FACILITY`) |
| C5 | Every action is typed into one real terminal (a PTY running bash), shown with xterm.js | Output stays visible, the user can type, and `sudo` prompts for the password there. The console never sees or stores the password |
| C6 | The console never writes terminal output to disk | `make secrets`, plan output and API responses can hold passphrases and RTMP URLs |

## Running it

- `make console [CONSOLE_PORT=8090]` starts `tools/console/` (Python, in `.tools/venv`). It listens on `127.0.0.1` only and prints `http://localhost:<port>/?token=<random>`. The token is set as an `HttpOnly`, `SameSite=Strict` cookie on first load and is required for every request and the WebSocket. The WebSocket also checks `Origin`.
- On WSL, Windows reaches it through WSL's localhost forwarding. Confirm this in stage 8; if it doesn't hold for a `127.0.0.1` listener, say so rather than binding wider.
- One dependency, added to `scripts/prereqs.sh` the way `xmlschema` was: `aiohttp`, pinned to a minor version, for HTTP and WebSocket in one. xterm.js is vendored under `tools/console/static/`, pinned, with its licence, so the page works offline.
- It stops on Ctrl-C. It isn't a service and doesn't start with WSL.

## Page

```
┌ Facility ─────────────────────────────────────────────────────────────────────────┐
│ k3s ● running   Kubernetes ● answers   Keycloak ● ready   NMOS registry ● up       │
│ CPU reserved 10 / 16   Memory 6.5 / 19.5 GiB   [Start] [Stop]                      │
│ Toolkit mount ● active (/mnt/wsl/instances/<distro>)   Profile ● exported [Export] │
├ Workloads ───────────────────────────┬ Toolkit designs ───────────────────────────┤
│ <id>  ON AIR  rev 2  10 CPU          │ <id>  rev 3  publishable   [Deploy]        │
│  [Open UI] [Status] [Update] [Fin…]  │ <id>  rev 1  2 problems    (reasons)       │
├ Archive ─────────────────────────────┤ toolkit API: not reachable on :3001        │
│ <id>  2026-11-12 00:45  [Redeploy]   │                                            │
├ Terminal ─────────────────────────────────────────────────────────────────────────┤
│ $ make plan WORKLOAD=<id>                                                         │
└───────────────────────────────────────────────────────────────────────────────────┘
```

Buttons are disabled while an action runs (see Terminal). Each button shows the exact command it
will run, on hover and in its confirmation.

## Status

Polled every 5 s by the server, each check with its own timeout of 3 s, run without sudo. A failing
check shows as unknown with its error, never blocks the others, and never fails the page.

| Item | Source |
|---|---|
| k3s | `systemctl is-active k3s` (or `k3s-agent`, as `facility.sh` chooses) |
| Kubernetes | `kubectl get ns -o name` with `.state/kubeconfig` |
| Keycloak | `https://keycloak.<base domain>/realms/dmf/.well-known/openid-configuration` answers 200, through the ingress address as `facility.sh` probes it |
| NMOS registry | Its pod in `dmf-system` is ready |
| Capacity | `ansible/scripts/capacity.py`, as `make plan` runs it, for reserved CPU and memory against allocatable. Show the same numbers the plan uses |
| Toolkit mount | `findmnt` shows `/mnt/wsl/instances/$WSL_DISTRO_NAME` bind-mounted from `/` |
| Profile | `.state/facility-<name>.json` exists and is newer than `facility/<name>.yaml` |
| Workloads | The union of namespaces labelled `dmf.ebu.ch/workload`, `workloads/*.yaml`, `workloads/generated/*.yaml` and `.state/allocations.json`. Per workload: hand-written or compiled; revision (from the generated header); deployed or not; pods ready; on air (reconciler metrics `dmf_reconcile_api_up`, `_pending`, `_failed`, read with `kubectl exec` as `facility.sh` reads the API); reserved CPU (its ResourceQuota) |
| Toolkit designs | `GET http://localhost:3001/api/facility` first: show the profile name it loaded, or a warning that it runs without one, so it offers options this facility can't run (stage 9). Then `GET http://localhost:3001/api/workloads`; for each, `published.xml` answering 200 (publishable) or 422 (show its reasons). Mark a design whose id is deployed, and whether its latest revision is newer than the deployed one. The deployed revision is the namespace's `dmf.ebu.ch/revision` label, which Terraform sets from the generated header (stage 9), not the compiled file's header: an import that wasn't provisioned changes the file but not what's running |
| Archive | Directories in `.state/archive/`, with the id, time and whether `source.xml` exists |

Workload states, in order: **designed** (in the toolkit only), **compiled** (generated file, not
deployed), **deployed, stopped** (namespace exists but pods aren't ready; or, while Kubernetes is down, its Flow Domain mount unit is installed in `/etc/systemd/system`, which stays until finalise and needs no sudo to read. The row says which), **running**
(pods ready), **on air** (`api_up` 1, nothing pending or failed). A reconciler failure shows as
**running, N settings failed**.

Status reads never call SwitchFrame routes that return secrets (`/api/output/destinations`,
`/api/sync/state-snapshot`), and never read `.state/secrets/` or Terraform outputs.

## Terminal

- One PTY running `bash -l` in the repo, started with the server, without `MAKELEVEL`, `MAKEFLAGS` and `MFLAGS` in its environment: started from `make console`, the shell would otherwise run every `make` typed in it as a sub-make (stage 8). Reloading the page reattaches to it and replays the last 2,000 lines, held in memory only (C6). If bash exits, the console starts a new one and says so.
- A button types its command into the PTY, followed by a marker the console uses to know when it ended and with what status: `; printf '\033]777;dmf-done;%s\007' "$?"`. xterm.js doesn't display that OSC sequence. While a button's command runs, the other buttons are disabled; typing stays possible.
- If the user is in the middle of typing, the console doesn't inject: it asks them to clear the line first.
- `make secrets` has no button. The user types it if they need it.

## Actions

| Button | Commands, in order | Confirmation |
|---|---|---|
| Start | `make start NO_BROWSER=1` | None |
| Stop | `make stop` | Lists the running workloads: all of them stop |
| Export profile | `make facility-export` | None |
| Mount for toolkit | `mountpoint -q /mnt/wsl/instances/$WSL_DISTRO_NAME \|\| { sudo mkdir -p /mnt/wsl/instances/$WSL_DISTRO_NAME && sudo mount --bind / /mnt/wsl/instances/$WSL_DISTRO_NAME; }`. Disabled while the mount is active; the status row shows a count if mounts were ever stacked | Says it lasts until WSL restarts |
| Open UI | `make windows-browser WORKLOAD=<id>` | None |
| Status | `make status WORKLOAD=<id>` | None |
| Deploy, Update | See below | The plan review |
| Finalise | `make finalise WORKLOAD=<id>` | The user types the id. The dialog says what is archived, that volumes and secrets are deleted, and that the facility must stay running |
| Redeploy | Deploy, with `XML=.state/archive/<run>/source.xml` | The plan review, and a note that its secrets must be set again |

### Deploy and Update

Separate steps instead of `make deploy`, so the user reviews the plan before anything changes.

1. `make import XML=<url or file>`. The compiler also writes `.state/last-import.json`: id, revision, allocation, warnings, and problems with their rule number, path, message and, for rule 6, the secret's name. Never a secret value. The console reads that file instead of parsing terminal output.
2. If problems other than missing secrets remain, show them and stop.
3. For each missing secret: `make secret-set WORKLOAD=<id> NAME=<name>`, one at a time. The user types the value in the terminal, at the prompt that doesn't echo it. Then import again.
4. `make plan WORKLOAD=<id>`. The plan playbook also writes `.state/<id>.plan-summary.json`: add, change and destroy counts, the resources it replaces or restarts, and the capacity verdict. No values from the plan.
5. A dialog shows that summary and the warnings from step 1. On **Provision**: `make provision WORKLOAD=<id>`, which asks for the sudo password in the terminal.

A design whose id is already deployed is offered as **Update**, the same flow.

## Changes to existing code

- Compiler: write `.state/last-import.json` on every `import`, refused or not.
- `workload-plan.yml`: write `.state/<id>.plan-summary.json` after a successful plan, mode 600.
- `scripts/prereqs.sh`: install `aiohttp`.
- Terraform: label the workload namespace `dmf.ebu.ch/revision` with the generated workload's revision (hand-written workloads: none). An in-place change on the next provision.
- `Makefile`: `console` target.
- `make validate`: the console's tests.

The three `make` fixes this needs are already done (2026-11-13, see the 04 spec's Make targets): plan
says when the facility is down, waits for Keycloak, and names an access member Keycloak doesn't
know; `make start` with no workload starts the only deployed one, or the facility alone.

## Not in scope

More than one terminal or user; remote access; managing the platform (`make platform`, `make
images`), which stays in the terminal; editing designs, which is the toolkit's job; starting the
toolkit server.

## Tests

- Status: each check against fakes for `systemctl`, `kubectl`, `findmnt`, the toolkit API and the Keycloak probe, including timeouts and a facility that's down.
- Terminal: the done marker is detected and its status read; a reattach replays the buffer; nothing is written to disk (assert on the files the server opens).
- Auth: requests and the WebSocket without the token, or with a foreign `Origin`, are refused.
- Deploy flow: driven against fake `make` targets writing the two JSON files, including a missing secret and a refused design.
- Live: in stage 9, one full cycle from the console on the real facility, as the README runbook does by hand.
