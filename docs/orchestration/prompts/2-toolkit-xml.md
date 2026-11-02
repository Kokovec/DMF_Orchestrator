# Stage 2: toolkit XML output

Run in Claude Code in distro `Ubuntu`, working directory `/home/dansi/DMF_Toolkit`. Requires stage 1b.

---

You're working on DMF Toolkit, a configurator for EBU Dynamic Media Facility workloads. Stage 1 fixed its model. This stage adds the XML output that the orchestrator reads.

Read first:
1. `CLAUDE.md` and `docs/design-rules.md` in this repo. Follow their conventions, including updating the rule and decision log in the same change.
2. In `/mnt/wsl/instances/Ubuntu-24.04/home/dansi/switchframe-dmf/` (read-only; if it isn't visible, stop and tell me): `docs/orchestration/01-contract.md`, section 9 of `docs/orchestration/03-toolkit-spec.md`, `schemas/dmf-workload-v1.xsd`, and `examples/studio-a.workload.xml`.

Node 24: `export PATH="$HOME/.nvm/versions/node/v24.21.0/bin:$PATH"`.

Build:
- `src/model/xml.ts` with `toWorkloadXml`, following the contract exactly: deterministic bytes, generic `<value>` encoding, refusal of keys and characters XML can't carry. No new runtime dependency.
- `GET /api/workloads/:id/published.xml[?revision=N]`, with the same publish checks as the JSON route.
- A Publish XML action in the app that downloads `<workload id>.workload.xml`.
- `schemas/dmf-workload-v1.xsd`: a byte-identical copy, with a test comparing a recorded hash.
- Update `docs/orchestrator-contract.md` and `CLAUDE.md`'s code map.

Tests may use dev-only dependencies. Validate against the XSD with a real validator, for example `xmllint --schema` if available, or a dev dependency. Say which one you used.

Acceptance, shown with real output:
- Build the workload from `scripts/fixtures/studio-a.ts`, publish it, and validate the XML against the XSD.
- Compare your output with `examples/studio-a.workload.xml`. It's abbreviated, so yours has more `<value>` elements. Flow IDs, writers, readers, formats, wires, selections, and `uid` must match. Explain any other difference. If the example is what's wrong, report it instead of changing it.
- Same revision published twice gives identical bytes.
- Round trip: JSON → XML → JSON is deep-equal for that workload and a fixture covering every value type, including secrets, arrays, nulls, empty objects, empty arrays, empty strings and text with `& < > " '`.
- An XML-illegal control character and a settings key like `a.b` each give HTTP 422, naming the location.
- No secret value can appear: a secret parameter yields only its name.
- An unpublishable revision gives the same 422 as the JSON route.
- `npm run build` and `npm run lint` pass.

Don't commit. If the contract or schema needs changing, don't change your copy: report what and why. The source of truth is in the orchestrator repo.

Finish with: what changed, the acceptance results, the path of one generated XML file the next stage can use, and any contract corrections you propose.
