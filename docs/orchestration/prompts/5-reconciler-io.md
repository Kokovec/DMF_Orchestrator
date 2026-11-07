# Stage 5: reconciler, secrets, real I/O

Run in Claude Code in distro `Ubuntu-24.04`, working directory `/home/dansi/switchframe-dmf`. Requires stage 4. Needs the facility running.

---

You're working on switchframe-dmf, which deploys SwitchFrame on an EBU DMF-aligned local k3s facility from published XML. SwitchFrame forgets its destinations and recording on restart, and SRT listener sources only exist once an encoder connects, so the current one-shot `postStart` hook can't apply their settings. This stage replaces it with a reconciler and adds real input and output.

Read first:
1. `docs/orchestration/README.md`, `01-contract.md` (source keys), `02-switchframe-options.md`, `04-orchestrator-spec.md` (Reconciler, Finalise)
2. `charts/switchframe/templates/deployment.yaml` (the current hook, whose auth and retry logic is your starting point), `networkpolicy.yaml`, `services.yaml`
3. `images/media-functions/Dockerfile`, `images/media-functions/mxl-flow-agent/` (the precedent for a small Python agent), and the guardrails policy in `charts/dmf-platform-config/`
4. SwitchFrame at `792361540f148b9ec8a93ec7727919672bd2330d`, https://github.com/Kokovec/switchframe, cloned outside this repo: `docs/reference/api/{source,output,operator}.md`, `server/control/`, `server/output/destination.go`, `docs/subsystems/srt-ingest.md`

Build, for options marked stage 5:
- `sf-reconciler` as specified, with tests against a fake API server. Replace the hook. Add it to the `mxl-tools` image.
- `make secret-set`, `.state/secrets/<id>.yaml`, the `workload-refs` Secret, and compile rule 6.
- SRT input in both modes, encoder settings, SRT caller output, RTMP output, and recording, including its PVC.
- Finalise copying recordings first, as specified. A failed copy must stop the teardown. Test that failure deliberately.

Verify before relying on these:
- The SRT caller's key format: read the code and check live. Match by `streamID`.
- Whether label and position persist for a listener source across a restart, per the docs.
- Whether a destination's `passphrase` appears in any API response or log. If it does, keep it out of everything this repo logs and archives, and report it.

Before changing anything live, check what's running. Don't finalise, destroy, or restart a workload you didn't start in this session without asking me. Before asking me to run `make provision`, run `make plan` for every workload your change affects, generated ones included, and fix what fails. Give any `ANSIBLE_LOG_PATH` as an absolute path: Ansible runs from `ansible/`. Rebuilding images takes a long time, so run it in the background. If provisioning needs sudo you can't get, give me the exact command.

Live test. Run test streams with `ffmpeg` inside a temporary container, not on the host. Write a workload XML under `examples/` covering these:
1. SRT listener: push an MPEG-TS with stream ID `venue1`. The source appears as `srt:venue1`, and its label and on-air selection get applied after it connects, not before. Start the workload with no encoder running first.
2. SRT caller: run a listener and have SwitchFrame pull. Delete the pod. After restart there's exactly one such source, not two.
3. Remove the caller from the XML and redeploy: the persisted pull is gone.
4. SRT caller output with encryption: receive it with `ffmpeg` using the passphrase from `make secret-set`, and confirm it fails with a wrong one.
5. RTMP output: receive with a temporary RTMP server container. Stream key comes from a secret, and isn't in any log, `config.json`, or archive.
6. Recording: files appear and rotate. Delete the pod: recording resumes. `make finalise` copies them to the archive.
7. Operator change: change a label through the API, and confirm the reconciler doesn't overwrite it on later loops.
8. Metrics: `dmf_reconcile_pending` reaches 0, and Prometheus scrapes it.
9. Regression: `studio-a` and `news-studio` still work. Stage 4's restart test still passes. The facility can't hold `news-studio` (12 GiB) beside another workload, so check capacity first and ask me before finalising anything to make room.

`make validate` passes. Don't commit. Report failures with their output.

Finish with: what changed, the results of each live test, the three verification results above, what I should check by hand, and spec corrections you propose.
