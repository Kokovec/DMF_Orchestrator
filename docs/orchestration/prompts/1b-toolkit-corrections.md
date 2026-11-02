# Stage 1b: toolkit corrections

Run in the same Claude Code session as stage 1 if it's still open, because its acceptance scripts are in that session's scratchpad. Otherwise use a fresh session in distro `Ubuntu`, working directory `/home/dansi/DMF_Toolkit`.

---

Stage 1 of the DMF Toolkit work is reviewed. Its changes are uncommitted in this working tree. These are the decisions on what it reported. The spec has been updated: re-read `/mnt/wsl/instances/Ubuntu-24.04/home/dansi/switchframe-dmf/docs/orchestration/03-toolkit-spec.md` (read-only), sections 2, 3, 4, 5 and 7. Follow `CLAUDE.md` and `docs/design-rules.md`, including updating rules and the decision log.

Node 24: `export PATH="$HOME/.nvm/versions/node/v24.21.0/bin:$PATH"`.

Make these changes:

1. **No data flows where none exist.** Add `ModuleBlock.noData`. A video-feed MXL block with it set has `video` and `audio` ports and no `anc`. Set it on `mxl` in both `dmf-mxl` modules and on `mxl-out` in `switchframe.1`. Keep `mxl-src.anc`. The reason for `mxl-out`: at the pinned SwitchFrame commit, `server/cmd/switchframe` opens no data writer for the programme output. Correct the `switcher.output.mxl` and `switcher.captions` descriptions, which promise MXL VANC data, and `docs/products/switchframe.md` to match.
2. **Discovery defaults.** Add `defaults.discovery` to `VendorModule` and set `register: true` on all three modules. Don't set `control.nmos`: these products don't register themselves; the facility's flow agent does.
3. **Parameter defaults.** Add `defaults.parameters` to `VendorModule`, applied over schema defaults for a new instance only. Set `mxl.format.video` to `1920x1080` and `29.97` on `dmf-mxl.1`. `dmf-mxl.2` already gets that from `allowed`, so set it there only if needed. Leave the shared `output.mxl` default alone.
4. **Captions.** Remove `off` from `captions.captions.mode`, since the toggle does this. The default stays `passthrough`.
5. **Keep the workload as a fixture.** Move the acceptance workload into `scripts/fixtures/studio-a.ts`, exporting a function that builds the `studio-a` workload through the model with id `studio-a`: `cam1`, `cam2` (`dmf-mxl.1`), `sync` (`dmf-mxl.2`), `vision-mixer` (`switchframe.1`) with 3 `mxl-src`, wires, and roles director `director1`, audio `audio1`, viewer `viewer1`. Pattern values and labels as in `examples/studio-a.workload.xml` in the orchestrator repo. Stage 2 builds on this. Build it the way a user would: start from new instances, set only what a user would set, and don't set format, discovery, or health where a default now covers them.

Regenerate catalogs, and bump versions as your rules require. No saved workloads use these unreleased module versions, so keep `switchframe.1` at `1.1.0` and `dmf-mxl.*` at `1.0.0` unless your rules say otherwise, and say which you did.

Acceptance, shown with real output:
- `npm run build` and `npm run lint` pass.
- The fixture's `publishProblems` is empty, having set no format on any source.
- Its published flows are exactly the 8 in the example: same IDs, writers, readers, and formats, with no data flow. All have `discovery.register: true`.
- A drag from a test source to `mxl-src`, and from `mxl-out` to another instance's `mxl-src`, creates only video and audio wires. Prove it with `planConnection` or the jsdom harness.
- `mxl-src.anc` still exists.
- A new `dmf-mxl.1` starts at 1080p29.97. A new `output.mxl` in any other module is unchanged.
- Earlier stage 1 acceptance items still pass. Re-run them from your scripts if you have them, and say which you couldn't.
- The features order in `examples/studio-a.workload.xml` now follows design order: compare yours and report any difference.
- Checksums of `data/` unchanged: no real server start.

Don't commit, and don't start stage 2. Finish with what changed, the results, and any further spec corrections.
