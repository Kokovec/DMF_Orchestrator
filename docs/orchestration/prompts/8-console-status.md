# Stage 8: facility console, status and terminal

Run in Claude Code in distro `Ubuntu-24.04`, working directory `/home/dansi/switchframe-dmf`. Requires stage 7. The facility can be running or stopped: this stage changes nothing live.

---

You're working on switchframe-dmf, which deploys SwitchFrame on an EBU DMF-aligned local k3s facility from workloads published as XML by DMF Toolkit. This stage builds the first half of a local web console: the status panels, the terminal and the toolkit mount. Stage 9 adds the lifecycle buttons and the deploy flow.

Read first: `CLAUDE.md`, `docs/orchestration/05-facility-console.md` (the spec: decisions C1 to C6, Running it, Page, Status, Terminal, Tests), `scripts/facility.sh` (how it probes Keycloak and the API), `ansible/scripts/capacity.py`, `README.md`'s runbook.

Build `tools/console/` and `make console`:
1. The server: `aiohttp`, added to `scripts/prereqs.sh` like `xmlschema`, pinned to a minor version. Bound to `127.0.0.1`, with the token cookie and the WebSocket `Origin` check.
2. Every Status row of the spec, polled every 5 s, each check with a 3 s timeout and no sudo. Reuse `capacity.py` and `scripts/workload-file.sh` rather than re-deriving what they compute. If `capacity.py` can't be run outside the plan playbook as it is, give it a small CLI mode and keep the playbook's behaviour identical.
3. The terminal: a PTY running `bash -l` in the repo, xterm.js vendored and pinned with its licence, reattach with the in-memory replay, and the done marker. Wire only these buttons in this stage: **Export profile**, **Mount for toolkit**, **Open UI** and **Status**. Buttons for the other actions are shown disabled, labelled "stage 9".
4. The page as sketched, at phone width too. No external network requests from the page.

Never write terminal output to disk, never call SwitchFrame routes that return secrets, and never read `.state/secrets/` or Terraform outputs. Check this with a test, not only by reading the code.

Tests as the spec lists for status, terminal and auth, added to `make validate`.

Live, read-only: start the console with the facility running and stopped, open it from Windows, and report what each panel shows. Confirm that Windows reaches a `127.0.0.1` listener through WSL's localhost forwarding; if it doesn't, stop and report instead of binding wider. Use **Mount for toolkit** once and show that the toolkit's distro can then read `.state/facility-local.json`; I'll type the sudo password.

Don't commit. Finish with what changed, what ran live, and anything in the spec that turned out to be wrong.
