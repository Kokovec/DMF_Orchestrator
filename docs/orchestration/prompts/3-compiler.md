# Stage 3: offline compiler

Run in Claude Code in distro `Ubuntu-24.04`, working directory `/home/dansi/switchframe-dmf`. Requires stage 2's XML. No cluster needed.

---

You're working on switchframe-dmf, which deploys SwitchFrame on an EBU DMF-aligned local k3s facility. A separate configurator publishes workloads as XML. This stage builds the compiler from that XML to the workload files this repo's lifecycle reads. Nothing is deployed in this stage.

Read first:
1. `docs/orchestration/README.md`, `01-contract.md`, `02-switchframe-options.md`, `04-orchestrator-spec.md`
2. `schemas/dmf-workload-v1.xsd`, `examples/studio-a.workload.xml`
3. `workloads/news-studio.yaml`, `terraform/stacks/workload/main.tf`, `ansible/playbooks/workload-plan.yml`, `ansible/scripts/capacity.py`, `scripts/validate.sh`
4. `examples/studio-a.workload.xml` is real toolkit output. The toolkit's fixtures are in `/mnt/wsl/instances/Ubuntu/home/dansi/DMF_Toolkit/scripts/fixtures/` (read-only): `every-value.workload.xml` covers every value type, and each `.workload.xml` has a `.published.json` twin to test your XML reader against.

Build, as in `04-orchestrator-spec.md`:
- `tools/dmf_compile/` with `reader`, `facility`, `allocate`, `sizing`, `emit`, and `products/switchframe.py`, `products/dmf_mxl.py`
- `facility/local.yaml`
- `xmlschema` in `scripts/prereqs.sh`, and installed into `.tools/venv`
- `make import` and `make facility-export`
- Tests wired into `scripts/validate.sh`

Scope: compile everything the options doc lists for stages 4, 5, and 6, into `workloads/<id>.yaml` and `<id>.config.json`, so later stages only change what consumes them. Secrets are checked by rule 6 only when a secret reference is present.

Don't change Terraform, charts, or playbooks in this stage. Don't overwrite or edit `workloads/news-studio.yaml`.

Compile against the pinned SwitchFrame source for flags: clone https://github.com/Kokovec/switchframe at `792361540f148b9ec8a93ec7727919672bd2330d` into a temp directory outside this repo. Test that every flag your compiler can emit appears in `docker run --rm --entrypoint switchframe dmf.local/switchframe:0.1.0 --help`, if Docker is available, and say if it wasn't. Check each API body in `config.json` against that source's `docs/reference/api/` and handler structs. Correct the spec's example through your report where it differs.

Acceptance, shown with real output:
- `make validate` passes, with compiler tests running.
- `make import XML=examples/studio-a.workload.xml` gives only the warnings the spec calls for and writes both files. Output matches a golden file.
- `every-value.workload.xml` reads back deep-equal to its `.published.json`. It's not a deployable workload, so compiling it is expected to fail with clear messages: show them.
- Generated YAML passes `yamllint` and every assertion in `workload-plan.yml`, evaluated in a test. Both `awk` lines in `scripts/windows-browser.sh` and `scripts/facility.sh` return the right values on it.
- `helm template` renders with values derived from it.
- Each compile rule has a failing test, and refusal messages name function and parameter. Several problems are reported together.
- DOCTYPE, entity declarations, and schema-invalid files are refused before parsing, with no network or file access attempted.
- Re-importing keeps the allocation. A second workload gets different ports and UID.
- Importing over `workloads/news-studio.yaml` is refused, and that file is unchanged.
- Sizing unit tests include `studio-a`'s quota and 2160p's domain size.

Don't commit.

Finish with: what changed, acceptance results, spec corrections you propose, and anything that needs a running system to confirm.
