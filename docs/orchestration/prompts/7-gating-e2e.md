# Stage 7: option gating and end to end

Run in two parts. Part A in distro `Ubuntu`, working directory `/home/dansi/DMF_Toolkit`. Part B in distro `Ubuntu-24.04`, working directory `/home/dansi/switchframe-dmf`. Requires stage 6. Paste only the part you're running.

---

## Part A: toolkit

You're working on DMF Toolkit, a configurator for EBU Dynamic Media Facility workloads. The orchestrator now refuses options the facility can't honour. This stage shows that in the design tool, so users don't design what can't deploy.

Read first: `CLAUDE.md`, `docs/design-rules.md`, and section 8 of `/mnt/wsl/instances/Ubuntu-24.04/home/dansi/switchframe-dmf/docs/orchestration/03-toolkit-spec.md`. That repo is read-only here. The exported file is at `/mnt/wsl/instances/Ubuntu-24.04/home/dansi/switchframe-dmf/.state/facility-local.json`. If it's missing, ask me to run `make facility-export` there.

Node 24: `export PATH="$HOME/.nvm/versions/node/v24.21.0/bin:$PATH"`.

Implement section 8. Keep the toolkit's rule that it makes no infrastructure decisions: it only shows what the facility declares, and with no facility set everything stays offered. Update design rules and the contract doc.

Acceptance, shown with real output:
- With `DMF_FACILITY` set: ST 2110, MoQ, speech-to-text, neural key, AI background removal, SCTE-104, and SRT listener output are disabled, with a reason. A workload saved earlier that uses one gets a publish problem naming it. Exercise this with the jsdom harness and state what it doesn't prove.
- With it unset: everything's offered, and existing behaviour is unchanged.
- The capability list here and the keys in the export match exactly, with a test.
- `npm run build` and `npm run lint` pass.

Don't commit. Finish with what changed and the results.

---

## Part B: end to end

You're working on switchframe-dmf, which deploys SwitchFrame from workloads published as XML by DMF Toolkit. Every stage is built. This stage proves the whole chain as a user would run it and finishes the docs.

Read first: `docs/orchestration/README.md` ("Done means"), `README.md`, `docs/dmf-compliance.md`, and `docs/architecture.md`.

The toolkit API runs in distro `Ubuntu`, with `npm run server` on port 3001. Ask me to start it if you can't reach `http://localhost:3001/api/library`.

Before changing anything live, check what's running. Don't finalise, destroy, or restart a workload you didn't start in this session without asking me. Before asking me to run `make provision`, run `make plan` for every workload your change affects, generated ones included, and fix what fails. Give any `ANSIBLE_LOG_PATH` as an absolute path: Ansible runs from `ansible/`.

Live test, using only the toolkit's API to create the workload, since this is an automated stand-in for the UI:
1. Create a workload with a test source, an SRT listener source, SRT and RTMP outputs, recording, and non-default engine and audio settings. Publish it.
2. `make deploy XML=http://localhost:3001/api/workloads/<id>/published.xml`. All running, settings applied.
3. Change one setting and save, so a new revision exists. Deploy again: only what should change does. Report what restarted. Flow IDs are unchanged.
4. Restart the facility with `make stop`, then `make start`. Everything recovers with no action. Ask me first.
5. Try to deploy an XML with ST 2110 selected, hand-edited to bypass the toolkit. It's refused before anything changes.
6. `make finalise`. Archive has the design, logs, state, recordings. Allocation released. Namespace gone.
7. Bring back `news-studio`, to prove hand-written workloads still work. Then return the facility to how you found it.

Then update:
- `README.md`: a section on deploying from the toolkit.
- `docs/architecture.md`: the compiler and reconciler.
- `docs/dmf-compliance.md`: only rows these changes actually affect, with honest status.
- `docs/orchestration/README.md`: mark assumptions as confirmed or changed, and record what's still not built.

`make validate` passes. Don't commit.

Finish with: the result of each step, a checklist of what I must confirm by hand (login, picture, audio, sync), and what remains not built.
