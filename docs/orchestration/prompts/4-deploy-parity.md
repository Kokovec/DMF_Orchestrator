# Stage 4: deploy from XML, with parity

Run in Claude Code in distro `Ubuntu-24.04`, working directory `/home/dansi/switchframe-dmf`. Requires stage 3. Needs the facility running.

---

You're working on switchframe-dmf, which deploys SwitchFrame on an EBU DMF-aligned local k3s facility. Stage 3 built a compiler from published XML to workload files. This stage makes the generated files actually deploy and proves parity with the hand-written `news-studio` workload.

Read first:
1. `docs/orchestration/README.md`, `02-switchframe-options.md`, `04-orchestrator-spec.md`
2. `README.md` (day-to-day commands and troubleshooting), `Makefile`, `scripts/facility.sh`
3. `terraform/stacks/workload/main.tf`, `charts/switchframe/`, `charts/mxl-function/`, `ansible/playbooks/workload-*.yml`, `ansible/roles/mxl_domain/`

Build, for options marked stage 4 only:
- The Terraform changes in the spec: published flow IDs, more formats, flow size from the format, and no assumption that `sources[0]` exists. The chart volume and args changes marked for stage 4. The `postStart` hook stays until stage 5.
- `plan`'s port check, and `make deploy`.
- `mxl-testsrc`'s pattern and overlay from the generated file.
- `finalise` releasing the allocation and archiving source files.
- Generated files move to `workloads/generated/` (git-ignored), with one resolver, `scripts/workload-file.sh`, used everywhere a workload file is read, as in the spec's "Where generated files go". Delete stage 3's leftover `workloads/studio-a.*` once the compiler writes to the new place.
- Anchor `facility.sh`'s `kind: switchframe` match. Quote every arg in the chart. Keep the data flow in its `mxlSources` helper.

`news-studio.yaml` must keep working with no edits: `terraform plan` for it must show no changes against what the old code would create, other than what you explain.

Before changing anything live, run `make status` and `kubectl get ns`, using this repo's `KUBECONFIG=.state/kubeconfig`. Don't finalise, destroy, or restart a workload you didn't start in this session without asking me first. `make provision` and `make finalise` need sudo: ask me to run them if you can't, and give the exact command.

Live test, using `examples/studio-a.workload.xml`:
1. `make deploy` brings it up. All pods are ready.
2. The flows in Terraform output, the NMOS registry, and the MXL domain directory all match the IDs in the XML.
3. SwitchFrame's args contain what the options doc says. Its programme is on air, with the labels from the XML. `dmf_mxl_flow_active` is 1 for all 8 flows.
4. Compare with `news-studio`: functions, flows per function, args, and resources. List every difference and why it's right.
5. Check the MXL domain's real use with `df` against the sizing formula, and correct the formula if needed.
6. Pod restart: delete the SwitchFrame pod. Programme and labels come back with no action.
7. A different format, `1080p25` without `sync`, deploys with correct flow definitions.
8. Run two workloads together, if capacity allows: no port or UID conflicts. If it doesn't fit, show the capacity verdict.
9. `make finalise` archives the source XML, and a re-import after that gets a clean allocation.

I check login and picture by hand: finish with the URL, the credentials command, and what I should look for.

Acceptance also includes `make validate` passing.

Don't commit. Report anything that failed with its output, including failures you worked around.

Finish with: what changed, the results of each live test, the parity differences, and spec corrections you propose.
