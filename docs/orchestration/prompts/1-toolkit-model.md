# Stage 1: toolkit model and SwitchFrame options

Run in Claude Code in distro `Ubuntu`, working directory `~/DMF_Toolkit`.

---

You're working on DMF Toolkit, a configurator for EBU Dynamic Media Facility workloads. It will publish a desired state that an orchestrator in another repo compiles and deploys. This stage fixes the model so a SwitchFrame workload can be expressed correctly. No XML yet: that's stage 2.

Read first, in this order:
1. `CLAUDE.md` and `docs/design-rules.md` in this repo. Follow their conventions, including updating the rule and decision log in the same change.
2. The definition, in the orchestrator repo: `/mnt/wsl/instances/Ubuntu-24.04/home/$USER/switchframe-dmf/docs/orchestration/` — `README.md`, `02-switchframe-options.md`, and `03-toolkit-spec.md`. Treat these as read-only. If that path isn't visible from this distro, stop and tell me.

Node 24 isn't on `PATH` in non-interactive shells. Use `export PATH="$HOME/.nvm/versions/node/v24.21.0/bin:$PATH"`.

Implement sections 1 to 7 of `03-toolkit-spec.md`:
1. Feature toggles
2. Instance defaults
3. Flows of unread outputs
4. Valid format combinations
5. SwitchFrame module changes
6. Workload access
7. Test source modules

Sections 8 and 9 are later stages. Don't start them.

Before finalising the test pattern list in section 7, confirm the pattern names against GStreamer's `videotestsrc` documentation or `gst-inspect-1.0`. If you can't confirm one, drop it.

Regenerate catalogs with `npx tsx scripts/product-catalogs.ts` after library changes. Bump versions as the spec says.

There's no browser in this distro. Exercise the dialog with a jsdom harness kept outside the repo, as earlier work did, and say what that does and doesn't prove.

Acceptance, shown with real output:
- `npm run build` and `npm run lint` pass.
- A script builds this workload through the model and `publishProblems` returns nothing: `dmf-mxl.1` as `cam1` and `cam2`, `dmf-mxl.2` as `sync`, `switchframe.1` as `vision-mixer` with 3 `mxl-src`, wired to each source's video and audio, with a director set.
- In its published JSON, the `vision-mixer/mxl-out` video and audio flows exist with empty `readers` and the engine's format.
- A new SwitchFrame instance has only `mxl-src` and `mxl-out` selected, and health `http 9090 /health`.
- With `recording` off there's no `recording` group in published parameters. With it on, `storageGiB` is present.
- 2160p24 is refused, naming the values. 1080p24 is accepted.
- `dmf-mxl.2` is refused at any format except 1080p29.97.
- `onAirAtStart` naming an unused copy is refused.
- The flow ID test vector holds: workload id `t1`, writer `sw/mxl-out.video` → `11d120ff-ba57-5632-bc10-0a2c5e3ec600`.
- Output of `git status` shows no change to `data/`.

Don't commit. If something in the spec is wrong or impossible, don't work around it: finish what you can, and report it.

Finish with: what changed, the acceptance results, and any spec corrections you propose.
