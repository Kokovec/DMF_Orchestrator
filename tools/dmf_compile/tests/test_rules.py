"""Each compile rule refuses, naming function and parameter; problems are reported together."""
import unittest
import xml.etree.ElementTree as ET

from .util import FULL_SETTINGS_SECRETS, Doc, RepoTest, full_settings, q

VM = "vision-mixer"


class Rules(RepoTest):
    def assertRefused(self, doc, rule, where, contains, fac=None):
        problems = self.repo.problems(doc.bytes() if isinstance(doc, Doc) else doc, fac)
        hits = [p for p in problems if p.rule == rule and p.where == where and contains in p.message]
        self.assertTrue(hits, f"no rule {rule} problem at {where!r} containing {contains!r} in:\n"
                        + "\n".join(map(str, problems)))
        return problems

    # Rules 9 and 10 ------------------------------------------------------------------------
    def test_rule9_rtmp_with_an_hevc_profile(self):
        for profile in ("standard-hevc", "professional"):
            d = Doc().use(VM, "outputs", "rtmp-out", 1)
            d.set(VM, "rtmp-out.rtmp.url", "rtmp://live.example.com/app")
            d.set(VM, "engine.engine.colorProfile", profile)
            self.assertRefused(d, 9, f"{VM}: rtmp-out, engine.engine.colorProfile",
                               f"rtmp-out can't run with colour profile {profile!r}")

    def test_rule9_allows_hevc_without_rtmp_and_rtmp_with_standard(self):
        self.assertEqual(self.repo.compile(Doc().set(VM, "engine.engine.colorProfile", "professional").bytes()).workload_id, "studio-a")
        d = Doc().use(VM, "outputs", "rtmp-out", 1).set(VM, "rtmp-out.rtmp.url", "rtmp://live.example.com/app")
        self.assertEqual(self.repo.compile(d.bytes()).workload_id, "studio-a")

    def test_rule10_uid_and_flow_ids_of_another_workload(self):
        self.repo.compile_and_write(Doc().bytes())
        d = Doc()
        d.root.set("id", "studio-b")     # the same uid and flow IDs, under another id
        problems = self.assertRefused(d, 10, "workload: uid 9dd110d0-8c14-5c77-acf6-3eb708bf0c13",
                                      "already used by workload studio-a")
        self.assertRefused(d, 10, "workload: flow ID 817de3a0-f16a-5268-9d73-6b157c4b3611", "already used by workload studio-a")
        self.assertEqual(sum(p.rule == 10 for p in problems), 9)    # the uid and 8 flows

    def test_rule10_own_identity_and_new_identity_pass(self):
        self.repo.compile_and_write(Doc().bytes())
        self.repo.compile_and_write(Doc().bytes())                      # re-import keeps its own
        self.repo.compile_and_write(Doc().rename("studio-b").bytes())   # derived, so its own

    # Rule 1 --------------------------------------------------------------------------------
    def test_rule1_api_version(self):
        d = Doc()
        d.root.set("apiVersion", "dmf-toolkit/v1alpha9")
        self.assertRefused(d, 1, "workload: apiVersion", "'dmf-toolkit/v1alpha9' is not one the compiler knows")

    def test_rule1_module_version(self):
        d = Doc()
        d.fn(VM).set("typeVersion", "2.0.0")
        self.assertRefused(d, 1, f"{VM}: typeVersion", "switchframe.1 version 2.0.0")

    def test_rule1_module_type(self):
        d = Doc()
        d.fn("cam2").set("type", "acme-cam.1")
        self.assertRefused(d, 1, "cam2: type", "'acme-cam.1' is not a module type")

    def test_rule1_unknown_block(self):
        d = Doc().use(VM, "inputs", "ndi-src", 1)
        self.assertRefused(d, 1, f"{VM}: inputs.ndi-src", "no block 'ndi-src'")

    # Rule 2 --------------------------------------------------------------------------------
    LIMIT = "limits.functionsPerWorkload of facility local"

    def two_switchframes(self):
        d = Doc()
        d.fn("cam2").set("type", "switchframe.1")
        d.fn("cam2").set("typeVersion", "1.1.0")
        return d

    def test_rule2_no_switchframe(self):
        d = Doc().remove_function(VM)
        p = self.assertRefused(d, 2, "workload: functions", f"needs exactly one switchframe.1 function, has 0; {self.LIMIT}")
        self.assertNotIn("()", next(x.message for x in p if x.rule == 2))

    def test_rule2_one_switchframe_passes(self):
        self.assertEqual(self.repo.compile(Doc().bytes()).workload_id, "studio-a")

    def test_rule2_two_switchframes(self):
        self.assertRefused(self.two_switchframes(), 2, "workload: functions",
                           f"needs exactly one switchframe.1 function, has 2 (cam2, vision-mixer); {self.LIMIT}")

    def two_domains(self):
        d = Doc()
        ET.SubElement(d.root.find(q("domains")), q("domain"), {"id": "backup"})
        d.fn("cam2").set("domain", "backup")
        return d

    def test_rule2_two_domains(self):
        p = self.assertRefused(self.two_domains(), 2, "workload: domains",
                               "needs exactly one domain (all pods share one host), has 2: main, backup; "
                               "limits.domainsPerWorkload of facility local")
        self.assertEqual(len(p), 1)

    def test_rule2_function_outside_the_workloads_domain(self):
        d = Doc()
        d.fn("cam2").set("domain", "backup")
        p = self.assertRefused(d, 2, "cam2: domain", "'backup' is not the workload's domain 'main'")
        self.assertEqual([x.where for x in p if x.rule == 2], ["cam2: domain"])

    def test_rule2_profile_without_these_limits_keeps_one_switchframe_and_one_domain(self):
        fac = self.repo.facility("nolimits", limits={"maxRecordingGiB": 200})
        self.assertEqual(fac.limits["functionsPerWorkload"], {"switchframe.1": {"min": 1, "max": 1}})
        self.assertEqual(fac.limits["domainsPerWorkload"], 1)
        self.assertEqual(self.repo.compile(Doc().bytes(), fac).workload_id, "studio-a")
        self.assertRefused(self.two_switchframes(), 2, "workload: functions",
                           "needs exactly one switchframe.1 function, has 2 (cam2, vision-mixer); "
                           "limits.functionsPerWorkload of facility nolimits", fac)
        self.assertRefused(Doc().remove_function(VM), 2, "workload: functions", "exactly one switchframe.1 function, has 0", fac)
        self.assertRefused(self.two_domains(), 2, "workload: domains", "needs exactly one domain", fac)

    def test_rule2_limits_for_other_types_come_from_the_profile(self):
        fac = self.repo.facility("onecam", **{"limits.functionsPerWorkload": {
            "switchframe.1": {"min": 1, "max": 1}, "dmf-mxl.1": {"max": 1}}})
        problems = self.assertRefused(Doc(), 2, "workload: functions", "needs at most one dmf-mxl.1 function, has 2 (", fac)
        self.assertEqual(sum(p.rule == 2 for p in problems), 1)

    # Rule 3 --------------------------------------------------------------------------------
    def test_rule3_gated_inputs_outputs_features_and_values(self):
        d = Doc().use(VM, "inputs", "moq-src", 1).use(VM, "outputs", "st2110-out", 1)
        d.feature(VM, "captions", True).feature(VM, "captions.asr", True).feature(VM, "ai-segment", True)
        d.set(VM, "mxl-src_2.key.type", "neural")
        d.use(VM, "outputs", "srt-out", 1).set(VM, "srt-out.srt.mode", "listener")
        problems = self.repo.problems(d.bytes())
        got = {(p.rule, p.where) for p in problems}
        for where in (f"{VM}: inputs.moq-src", f"{VM}: outputs.st2110-out", f"{VM}: features.captions.asr",
                      f"{VM}: features.ai-segment", f"{VM}: mxl-src_2.key.type", f"{VM}: srt-out.srt.mode"):
            self.assertIn((3, where), got, "\n".join(map(str, problems)))
        self.assertTrue(all("capability" in p.message for p in problems if p.rule == 3))

    def test_rule3_capability_switched_off(self):
        fac = self.repo.facility("norec", **{"capabilities.recording": False})
        d = Doc().feature(VM, "recording", True).set(VM, "recording.recording.storageGiB", 10)
        self.assertRefused(d, 3, f"{VM}: features.recording", "needs the 'recording' capability, which facility norec", fac)

    def test_rule3_gpu(self):
        d = Doc()
        ET.SubElement(d.fn(VM).find(q("resources")), q("gpu"), {"class": "nvidia-l4", "count": "1"})
        self.assertRefused(d, 3, f"{VM}: resources.gpu", "needs the 'gpu' capability")

    def test_rule3_recording_limit(self):
        d = Doc().feature(VM, "recording", True).set(VM, "recording.recording.storageGiB", 500)
        self.assertRefused(d, 3, f"{VM}: recording.recording.storageGiB", "more than facility local's limit of 200 GiB")

    # Rule 4 --------------------------------------------------------------------------------
    def test_rule4_format(self):
        for res, rate, name in (("1280x720", "23.976", "720p23.976"), ("3840x2160", "24", "2160p24")):
            with self.subTest(name):
                d = Doc().set(VM, "engine.engine.resolution", res).set(VM, "engine.engine.rate", rate)
                self.assertRefused(d, 4, f"{VM}: engine.engine.resolution, engine.engine.rate",
                                   f"{name} is not a SwitchFrame format")

    # Rule 5 --------------------------------------------------------------------------------
    def test_rule5_source_flow_matches_engine(self):
        d = Doc()
        fmt = d.flow("cam2/mxl.video").find("{urn:dmf:workload:v1}format")
        fmt.set("grainRateNum", "25")
        fmt.set("grainRateDen", "1")
        fmt.set("transfer", "HLG")
        d.flow("cam1/mxl.audio").find("{urn:dmf:workload:v1}format").set("sampleRateNum", "44100")
        problems = self.repo.problems(d.bytes())
        msgs = {str(p) for p in problems}
        self.assertIn(f"{VM}: mxl-src_2: flow cam2/mxl.video rate is 25, the engine's is 29.97 [rule 5]", msgs)
        self.assertIn(f"{VM}: mxl-src_2: flow cam2/mxl.video transfer is HLG, the engine's is SDR [rule 5]", msgs)
        self.assertIn(f"{VM}: mxl-src: flow cam1/mxl.audio sample rate is 44100/1, the engine's is 48000 [rule 5]", msgs)

    def test_rule5_engine_resolution_against_sources(self):
        d = Doc().set(VM, "engine.engine.resolution", "1280x720")
        problems = self.repo.problems(d.bytes())
        sizes = [p for p in problems if p.rule == 5 and "frame size is 1920x1080, the engine's is 1280x720" in p.message]
        self.assertEqual(len(sizes), 4, "\n".join(map(str, problems)))   # three sources and the programme

    def test_rule5_sync_source_format(self):
        d = Doc().set("sync", "mxl.format.video.rate", "25")
        self.assertRefused(d, 5, "sync: mxl.format.video", "is 1080p25; the A/V sync test source only runs at 1080p29.97")

    def test_rule5_no_data_from_mxl_out(self):
        d = Doc()
        ET.SubElement(d.root.find(q("wires")), q("wire"), {"from": f"{VM}/mxl-out.anc", "to": "cam1/mxl.anc"})
        self.assertRefused(d, 5, f"{VM}: mxl-out.anc", "SwitchFrame writes no data flow for its programme")

    # Rule 6 --------------------------------------------------------------------------------
    def test_rule6_secret_missing(self):
        problems = self.assertRefused(full_settings(), 6, f"{VM}: srt-out.srt.passphrase",
                                      "secret 'venue-srt' is not in .state/secrets/full-settings.yaml")
        self.assertRefused(full_settings(), 6, f"{VM}: rtmp-out.rtmp.streamKey", "secret 'yt-key'")
        self.assertEqual({p.rule for p in problems}, {6})

    def test_rule6_secrets_file_mode_and_values(self):
        self.repo.secrets("full-settings", {"venue-srt": "short", "yt-key": "k"}, mode=0o644)
        problems = self.repo.problems(full_settings().bytes())
        msgs = [str(p) for p in problems]
        self.assertIn("workload: .state/secrets/full-settings.yaml: is mode 644; it must be readable by its owner only (600) [rule 6]", msgs)
        self.assertTrue(any("venue-srt' must be 10-79 bytes" in m for m in msgs), msgs)
        self.assertFalse(any("short" in m for m in msgs), "a secret value appeared in a message")

    def test_rule6_secret_written_as_a_value(self):
        d = full_settings().set(VM, "rtmp-out.rtmp.streamKey", "plain-key", "string")
        self.repo.secrets("full-settings", FULL_SETTINGS_SECRETS)
        self.assertRefused(d, 6, f"{VM}: rtmp-out.rtmp.streamKey", "must be a secret reference")

    def test_rule6_secret_name_is_a_secret_key(self):
        d = full_settings().set(VM, "rtmp-out.rtmp.streamKey", "yt key/1", "secret")
        self.repo.secrets("full-settings", {**FULL_SETTINGS_SECRETS, "yt key/1": "abcd"})
        self.assertRefused(d, 6, f"{VM}: rtmp-out.rtmp.streamKey", "secret name 'yt key/1' must match")

    def test_rule6_only_when_referenced(self):
        self.repo.compile(Doc().bytes())   # no secrets file, nothing referenced

    # Rule 7 --------------------------------------------------------------------------------
    def test_rule7_hand_written_id(self):
        target = self.repo.path / "workloads" / "news-studio.yaml"
        before = target.read_bytes()
        (self.repo.path / "news.xml").write_bytes(Doc().rename("news-studio").bytes())
        rc, out, err = self.repo.cli("compile", str(self.repo.path / "news.xml"))
        self.assertEqual(rc, 1)
        self.assertIn("workload: id news-studio: workloads/news-studio.yaml is a hand-written workload with this id; "
                      "it is never overwritten [rule 7]", err)
        self.assertEqual(target.read_bytes(), before)
        self.assertFalse((self.repo.gen / "news-studio.config.json").exists())
        self.assertNotIn("news-studio", (self.repo.path / ".state" / "allocations.json").read_text()
                         if (self.repo.path / ".state" / "allocations.json").exists() else "")

    def test_rule7_generated_id_is_replaced(self):
        self.repo.compile_and_write(Doc().bytes())
        self.repo.compile_and_write(Doc().bytes())

    # Rule 8 --------------------------------------------------------------------------------
    def test_rule8_plan_assertions(self):
        fac = self.repo.facility("lowuid", **{"workloadUids.first": 5000, "workloadUids.last": 5100})
        self.assertRefused(Doc(), 8, "workload: spec.mediaExchange.uid", "fails the check in ansible/playbooks/workload-plan.yml", fac)

    def test_rule8_demo_refused(self):
        d = Doc().settings(VM, [("extraArgs[0]", "string", "--demo")])
        self.assertRefused(d, 8, f"{VM}: settings.extraArgs", "--demo is refused")
        d = Doc().settings(VM, [("extraArgs[0]", "string", "--demo-video=/x")])
        self.assertRefused(d, 8, f"{VM}: settings.extraArgs", "--demo-video=/x is refused")

    def test_rule8_values_the_product_cant_take(self):
        d = Doc().set(VM, "mxl-src.source.label", "x" * 129).set(VM, "engine.engine.onAirAtStart", "srt-src")
        d.feature(VM, "recording", True).set(VM, "recording.recording.storageGiB", 5)
        d.set(VM, "recording.recording.rotateAfterMinutes", 0)
        d.feature(VM, "scte35", True).set(VM, "scte35.scte35.defaultRule", "rewrite")
        d.set(VM, "mxl-src.key.type", "luma").set(VM, "mxl-src.key.fillSource", "mxl-src_9")
        d.use(VM, "inputs", "srt-src", 1).set(VM, "srt-src.srt.mode", "caller").set(VM, "srt-src.srt.streamId", "a/b")
        d.set(VM, "srt-src.srt.address", "host-without-port")
        problems = self.repo.problems(d.bytes())
        got = {p.where for p in problems if p.rule == 8}
        for where in ("mxl-src.source.label", "recording.recording.rotateAfterMinutes", "scte35.scte35.defaultRule",
                      "mxl-src.key.fillSource", "srt-src.srt.streamId", "srt-src.srt.address"):
            self.assertIn(f"{VM}: {where}", got, "\n".join(map(str, problems)))

    def test_rule8_audio_ranges_are_the_mixers(self):
        # The API's own check is wider than the mixer's: between the two, the API accepts the
        # request and the mixer refuses it (server/audio/eq.go, server/audio/mixer.go).
        for path, value in (("mxl-src.audio.eq.mid.gainDb", 13), ("mxl-src.audio.eq.low.gainDb", -13),
                            ("mxl-src.audio.eq.high.q", 4.5), ("mxl-src.audio.eq.mid.q", 0.4),
                            ("mxl-src.audio.eq.low.frequencyHz", 79), ("mxl-src.audio.eq.mid.frequencyHz", 8001),
                            ("mxl-src.audio.eq.high.frequencyHz", 999),
                            ("audio.master.limiter.thresholdDb", -13), ("audio.master.limiter.ceilingDb", -4)):
            with self.subTest(path):
                d = Doc().set(VM, path, value)
                self.assertIn(f"{VM}: {path}", {p.where for p in self.repo.problems(d.bytes()) if p.rule == 8})
        d = Doc()
        for path, value in (("mxl-src.audio.eq.mid.gainDb", 12), ("mxl-src.audio.eq.mid.q", 4), ("mxl-src.audio.eq.low.q", 0.5),
                            ("mxl-src.audio.eq.low.frequencyHz", 80), ("mxl-src.audio.eq.high.frequencyHz", 16000),
                            ("audio.master.limiter.thresholdDb", -12), ("audio.master.limiter.ceilingDb", -3)):
            d.set(VM, path, value)
        self.repo.compile(d.bytes())

    def test_rule8_stream_id_is_unique_across_srt_inputs(self):
        # Listeners and callers share srt:<streamID> keys, so a mixed pair collides too.
        for modes in (("listener", "listener"), ("caller", "caller"), ("listener", "caller")):
            with self.subTest(modes=modes):
                d = Doc().use(VM, "inputs", "srt-src", 2)
                for copy, mode in zip(("srt-src", "srt-src_2"), modes):
                    d.set(VM, f"{copy}.srt.mode", mode).set(VM, f"{copy}.srt.streamId", "venue1")
                    d.set(VM, f"{copy}.srt.address", "enc.example.net:9000")
                self.assertRefused(d, 8, f"{VM}: srt-src_2.srt.streamId", "'venue1' is also used by srt-src")

    def test_rule8_distinct_stream_ids_are_accepted(self):
        d = Doc().use(VM, "inputs", "srt-src", 2)
        d.set(VM, "srt-src.srt.streamId", "venue1").set(VM, "srt-src_2.srt.streamId", "venue2")
        self.repo.compile(d.bytes())

    def test_rule8_mxl_input_without_wire(self):
        d = Doc().use(VM, "inputs", "mxl-src", 4)
        self.assertRefused(d, 8, f"{VM}: mxl-src_4.video", "has no wire")

    def test_rule8_unknown_role(self):
        d = Doc()
        role = ET.SubElement(d.root.find(q("access")), q("role"), {"name": "producer"})
        ET.SubElement(role, q("member")).text = "p1"
        self.assertRefused(d, 8, "workload: access.roles.producer", "is not a role of this facility")

    # Together ------------------------------------------------------------------------------
    def test_several_problems_reported_together(self):
        d = Doc()
        d.root.set("apiVersion", "dmf-toolkit/v0")
        d.set(VM, "engine.engine.rate", "24").set(VM, "engine.engine.resolution", "1280x720")
        d.use(VM, "inputs", "st2110-src", 2)
        d.settings(VM, [("extraArgs[0]", "string", "--demo")])
        path = self.repo.path / "bad.xml"
        path.write_bytes(d.bytes())
        rc, out, err = self.repo.cli("compile", str(path))
        self.assertEqual(rc, 1)
        rules = {line.rsplit("[rule ", 1)[1].rstrip("]") for line in err.splitlines() if "[rule " in line}
        self.assertTrue({"1", "3", "4", "8"} <= rules, err)
        self.assertFalse((self.repo.gen / "studio-a.yaml").exists())
        self.assertFalse((self.repo.path / ".state" / "allocations.json").exists())


class Warnings(RepoTest):
    def warnings(self, doc):
        return self.repo.compile(doc.bytes()).warnings

    def test_studio_a_has_no_warnings(self):
        # control.network.* holds only the toolkit's defaults, and historyMs never warns.
        self.assertEqual(self.warnings(Doc()), [])

    def test_ignored_option_at_the_toolkit_default_is_silent(self):
        d = Doc().use(VM, "inputs", "srt-src", 1).set(VM, "srt-src.srt.streamId", "v1")
        d.set(VM, "srt-src.network.exposure", "wan-facing").set(VM, "srt-src.payload.pathA.container", "mpeg-ts")
        d.set(VM, "srt-src.payload.pathA.videoCodec", "h264").set(VM, "srt-src.network.pathB", None, "emptyObject")
        self.assertEqual(self.warnings(d), [])

    def test_ignored_option_off_its_default_warns(self):
        d = Doc().set(VM, "control.network.exposure", "internal").set(VM, "control.network.pathA.vlan", "20")
        self.assertEqual(self.warnings(d), [
            "ignored for now: network.* is set (vision-mixer: control.network.exposure, control.network.pathA.vlan): "
            "it changes nothing on this single-host facility"])

    def test_freerun_with_mxl_sources(self):
        w = self.warnings(Doc().set(VM, "engine.engine.frameSync", "freerun"))
        self.assertTrue(any(x.startswith(f"{VM}: engine.engine.frameSync: freerun with MXL sources") for x in w), w)

    def test_extra_args(self):
        w = self.warnings(Doc().settings(VM, [("extraArgs[0]", "string", "--log-level=debug")]))
        self.assertIn(f"{VM}: settings.extraArgs: appends --log-level=debug to SwitchFrame's arguments, unchecked", w)

    def test_scte104_without_anc_when_the_facility_has_it(self):
        fac = self.repo.facility("s104", **{"capabilities.scte104": True})
        d = Doc().feature(VM, "scte35", True).feature(VM, "scte35.scte104", True)
        w = self.repo.compile(d.bytes(), fac).warnings
        self.assertTrue(any("features.scte35.scte104: is on, but no mxl-src anc port is wired" in x for x in w), w)

    def test_each_ignored_option_once(self):
        d = Doc()
        fn = d.fn(VM)
        disc = fn.find(q("discovery"))
        ET.SubElement(disc, q("label")).text = "news"
        deps = ET.Element(q("dependencies"))
        ET.SubElement(deps, q("dependency"), {"name": "archive", "kind": "storage", "required": "true"})
        fn.insert(list(fn).index(fn.find(q("resources"))), deps)
        fn.insert(list(fn).index(disc), ET.Element(q("expectations"), {"resilience": "1+1"}))
        fn.insert(list(fn).index(disc), ET.Element(q("placement"), {"site": "a"}))
        d.fn("cam1").find(q("discovery")).set("register", "false")
        d.use(VM, "inputs", "srt-src", 1).set(VM, "srt-src.srt.streamId", "v1").set(VM, "srt-src.payload.pathA.bitrateMbps", 10)
        d.set(VM, "control.network.exposure", "internal")
        w = [x for x in self.warnings(d) if x.startswith("ignored for now")]
        kinds = [x.split(": ", 1)[1].split(" is set")[0] for x in w]
        self.assertEqual(sorted(kinds), sorted(["network.*", "payload.*", "placement.*", "expectations.resilience",
                                                "discovery.labels", "discovery.register: false", "dependencies"]))


if __name__ == "__main__":
    unittest.main()
