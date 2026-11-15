# Stage 9: facility console, actions and deploy

Run in Claude Code in distro `Ubuntu-24.04`, working directory `~/switchframe-dmf`. Requires stage 8. Needs the facility running, and the toolkit server with a publishable design.

---

You're working on switchframe-dmf, which deploys SwitchFrame on an EBU DMF-aligned local k3s facility from workloads published as XML by DMF Toolkit. Stage 8 built the console's status panels and terminal in `tools/console/`. This stage adds the lifecycle buttons and the deploy flow.

Read first: `CLAUDE.md`, `docs/orchestration/05-facility-console.md` (Actions, Deploy and Update, Changes to existing code, Tests), `tools/console/`, `tools/dmf_compile/` and `ansible/playbooks/workload-plan.yml`.

Build:
1. Compiler: write `.state/last-import.json` on every import, refused or not, as the spec describes. A missing secret names the secret. No secret value ever appears in it: test that with a secret set.
2. `workload-plan.yml`: write `.state/<id>.plan-summary.json`, mode 600, after a successful plan. Counts, the resources replaced or restarted, the capacity verdict, and no values from the plan.
3. The buttons in the spec's Actions table, with their confirmations. Finalise needs the id typed.
4. Deploy and Update as separate steps: import, secrets one at a time, plan, review dialog, provision. The console reads the two JSON files, never terminal output.
5. Redeploy from the archive.
6. Terraform labels the workload namespace `dmf.ebu.ch/revision`, and the designs panel compares against that label, as the spec's Status table says. Plan every deployed workload: the label is an in-place change.
7. The designs panel shows which facility profile the toolkit server loaded (`GET /api/facility`), or warns that it has none.

Tests as the spec lists for the deploy flow, against fake `make` targets, added to `make validate`. The goldens shouldn't change; confirm with a run.

Before changing anything live, check what's running. Don't finalise, destroy, or restart a workload you didn't start in this session without asking me. Before asking me to run `make provision`, run `make plan` for every workload your change affects, generated ones included. Give any `ANSIBLE_LOG_PATH` as an absolute path: Ansible runs from `ansible/`.

Live, driving the console in a browser, one cycle as the README runbook does it by hand:
1. Finalise what's deployed, after asking me.
2. Deploy a toolkit design that needs one secret. Show the review dialog.
3. Save a settings-only revision in the toolkit, and Update. The review shows no restarts.
4. Stop, then Start. It comes back on air.
5. Finalise it, then Redeploy it from the archive.
6. Leave the facility as you found it, after asking me.

I'll type sudo passwords and secrets in the console's terminal. Report what each step showed, and anything that needed the terminal instead of a button.

`make validate` passes. Don't commit. Finish with what changed, what ran live, and what's still manual.
