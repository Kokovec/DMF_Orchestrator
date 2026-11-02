# Stage 6: remaining settings

Run in Claude Code in distro `Ubuntu-24.04`, working directory `/home/dansi/switchframe-dmf`. Requires stage 5. Needs the facility running.

---

You're working on switchframe-dmf, which deploys SwitchFrame on an EBU DMF-aligned local k3s facility from published XML, with a reconciler applying API-based settings. This stage wires through all remaining options.

Read first:
1. `docs/orchestration/02-switchframe-options.md`: every row marked stage 6 is in scope. `04-orchestrator-spec.md` for sizing and the reconciler.
2. `tools/dmf_compile/` and `images/media-functions/sf-reconciler/`
3. SwitchFrame at `792361540f148b9ec8a93ec7727919672bd2330d`, https://github.com/Kokovec/switchframe, cloned outside this repo: `docs/reference/api/{audio,keying,source,captions,scte35,replay}.md`, `docs/reference/config-and-flags.md`, and the handler structs in `server/control/`. Check each request body and unit against these, not the spec's summary.

Check units in particular: bytes versus MB or GB, bit/s versus kbit/s, and any field whose zero value means "off" or "default". For each row, a test asserts the exact flag or request body.

Measure, then set: replay tmpfs. Run with 300 s and 3 sources and record the tmpfs used. Set the sizing formula from that, with its evidence in a comment. Replay tmpfs counts against pod memory, so include it in the memory calculation.

Before changing anything live, check what's running. Don't finalise, destroy, or restart a workload you didn't start in this session without asking me.

Live test. Write `examples/full-settings.workload.xml`, giving every in-scope option a non-default value. Then:
1. Read each value back through the API or the running args. Output a table: option, value set, value read, match. Every in-scope row of the options doc appears. An option with no way to read it back is marked as such, with what you checked instead, such as a request log.
2. Delete the pod and repeat the table. Note which came back, which ones the product itself restored, and any that didn't.
3. Disable each optional feature in turn and confirm its flags and calls go away: captions, SCTE-35, replay, playout.
4. Confirm a chroma key and a luma key apply to a source. `off` removes it.
5. Confirm 10 GB of clip storage is honoured by a correctly sized state PVC, and that playout's cache has a sized volume.
6. Regression: stages 4 and 5 live tests for `studio-a` and SRT input.

Report any setting that the API accepts but has no observable effect. Don't count it as passing.

`make validate` passes. Don't commit.

Finish with: what changed, the full table, the replay measurement, what I should check by hand (audio and keying especially), and spec corrections you propose.
