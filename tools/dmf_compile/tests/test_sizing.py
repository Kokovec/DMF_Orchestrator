import unittest
from fractions import Fraction

from tools.dmf_compile import sizing

from .util import Doc, RepoTest

NTSC = Fraction(30000, 1001)


class Sizing(unittest.TestCase):
    def test_v210_frame_bytes(self):
        self.assertEqual(sizing.v210_frame_bytes(1920, 1080), 5_529_600)
        self.assertEqual(sizing.v210_frame_bytes(1280, 720), 2_465_280)    # ceil(1280/6) = 214 groups
        self.assertEqual(sizing.v210_frame_bytes(3840, 2160), 22_118_400)

    def test_video_grains(self):
        self.assertEqual(sizing.video_grains(NTSC, 500), 15)          # ceil(14.985)
        self.assertEqual(sizing.video_grains(NTSC, 100), 5)           # never under what the writers keep
        self.assertEqual(sizing.video_grains(Fraction(60000, 1001), 500), 30)

    def test_domain_studio_a(self):
        # 4 video flows x 15 grains x 5,529,600 B + 4 x 1 MiB = 320.4 MiB; ceil(x1.25) = 401; + 256.
        self.assertEqual(sizing.domain_size_mib([(1920, 1080, NTSC, 500)] * 4, 4), 657)

    def test_domain_studio_a_covers_the_measured_use(self):
        # Measured on studio-a: 111,399,034 B in the domain (5 grains per video flow).
        self.assertGreater(sizing.domain_size_mib([(1920, 1080, NTSC, 500)] * 4, 4) * 2**20, 4 * 111_399_034)

    def test_domain_2160p(self):
        # 4 x 30 grains x 22,118,400 B + 4 MiB = 2535 MiB; ceil(x1.25) = 3170; + 256.
        self.assertEqual(sizing.domain_size_mib([(3840, 2160, Fraction(60000, 1001), 500)] * 4, 4), 3426)
        # 3 x 15 x 22,118,400 B + 3 MiB = 952.2 MiB; ceil(x1.25) = 1191; + 256.
        self.assertEqual(sizing.domain_size_mib([(3840, 2160, NTSC, 500)] * 3, 3), 1447)
        self.assertEqual(sizing.domain_size_mib([(1280, 720, Fraction(25), 1)] * 2, 2), 512)

    def test_stmap_cache_matches_the_measurement(self):
        # du on /cache/stmap at 1080p: 5 effects of 3,359,232,000 B.
        self.assertEqual(sizing.stmap_cache_bytes(1920, 1080), 5 * 3_359_232_000)

    def test_switchframe_resources(self):
        self.assertEqual(sizing.switchframe_resources(2, 4096), (
            {"requests": {"cpu": "2", "memory": "4Gi"}, "limits": {"cpu": "6", "memory": "8Gi"}}, "6GiB", Fraction(4096)))
        self.assertEqual(sizing.switchframe_resources(2.5, 3000), (
            {"requests": {"cpu": "2500m", "memory": "3000Mi"}, "limits": {"cpu": "7500m", "memory": "6000Mi"}}, "4500MiB", Fraction(3000)))

    def test_replay_tmpfs_adds_to_request_and_limit_but_not_gomemlimit(self):
        res, gml, request = sizing.switchframe_resources(2, 4096, 664)
        self.assertEqual(res, {"requests": {"cpu": "2", "memory": "4760Mi"}, "limits": {"cpu": "6", "memory": "8856Mi"}})
        self.assertEqual(gml, "6GiB")
        self.assertEqual(request, Fraction(4760))

    def test_replay_tmpfs_mib(self):
        self.assertEqual(sizing.replay_tmpfs_mib(3), 3 * 200 + 64)
        self.assertEqual(sizing.replay_tmpfs_mib(8), 8 * 200 + 64)
        self.assertEqual(sizing.replay_tmpfs_mib(12), 8 * 200 + 64)     # replay buffers 8 sources at most
        self.assertEqual(sizing.replay_tmpfs_mib(3, replay_on=False), 0)
        self.assertEqual(sizing.replay_tmpfs_mib(0), 0)

    def test_test_source_resources(self):
        self.assertEqual(sizing.test_source_resources(0.25, 256),
                         {"requests": {"cpu": "250m", "memory": "256Mi"}, "limits": {"cpu": "1", "memory": "1Gi"}})
        self.assertEqual(sizing.test_source_resources(2, 2048),
                         {"requests": {"cpu": "2", "memory": "2Gi"}, "limits": {"cpu": "2", "memory": "2Gi"}})

    def test_quota_studio_a(self):
        # 2 + 3 x 0.25 + 0.05 + 0.03 + 0.02 = 2.85 CPU -> 3.42 -> 4
        # 4096 + 3 x 256 + 64 + 56 + 32 = 5016 MiB -> 6019.2 -> 6 GiB
        pods = sizing.workload_requests(2, 4096, [(Fraction(1, 4), 256)] * 3)
        self.assertEqual(sizing.quota(pods), {"cpu": "4", "memory": "6Gi", "gpu": 0})

    def test_volumes(self):
        # /cache: ST map prewarm (15.64 GiB at 1080p -> 16) + 2. State: clips + 1, plus the playout
        # cache when on: SwitchFrame keeps it in <state dir>/playout-cache, not under /cache.
        self.assertEqual(sizing.volumes(1920, 1080, 10), {"stateGi": 11, "cacheGi": 18, "replayTmpfsMi": 0})
        self.assertEqual(sizing.volumes(1920, 1080, 2), {"stateGi": 5, "cacheGi": 18, "replayTmpfsMi": 0})
        self.assertEqual(sizing.volumes(1920, 1080, None), {"stateGi": 11, "cacheGi": 18, "replayTmpfsMi": 0})
        self.assertEqual(sizing.volumes(1920, 1080, 10, True, 50, 100, 664),
                         {"stateGi": 61, "cacheGi": 18, "replayTmpfsMi": 664, "recordingsGi": 100})
        self.assertEqual(sizing.volumes(1920, 1080, 10, True), {"stateGi": 211, "cacheGi": 18, "replayTmpfsMi": 0})
        self.assertEqual(sizing.volumes(1280, 720)["cacheGi"], 9)       # 6.95 GiB
        self.assertEqual(sizing.volumes(3840, 2160)["cacheGi"], 65)     # 62.6 GiB


class CompiledSizes(RepoTest):
    def test_studio_a(self):
        r = self.repo.compile(Doc().bytes())
        # Three sources: 664 MiB of replay tmpfs joins the mixer's request, so 5680 MiB -> x1.2 -> 7 GiB.
        self.assertEqual(r.doc["spec"]["resourceProfile"], {"cpu": "4", "memory": "7Gi", "gpu": 0})
        self.assertEqual(r.doc["spec"]["mediaExchange"]["sizeMiB"], 657)

    def test_2160p_workload(self):
        d = Doc().remove_function("sync").use("vision-mixer", "inputs", "mxl-src", 2)
        d.set("vision-mixer", "engine.engine.resolution", "3840x2160")
        for writer in ("cam1/mxl.video", "cam2/mxl.video", "vision-mixer/mxl-out.video"):
            fmt = d.flow(writer).find("{urn:dmf:workload:v1}format")
            fmt.set("frameWidth", "3840")
            fmt.set("frameHeight", "2160")
        for cam in ("cam1", "cam2"):
            d.set(cam, "mxl.format.video.frameSize", "3840x2160")
        r = self.repo.compile(d.bytes())
        mixer = r.doc["spec"]["functions"][-1]
        self.assertEqual(mixer["format"], "2160p29.97")
        self.assertEqual(r.doc["spec"]["mediaExchange"]["sizeMiB"], 1447)
        self.assertEqual(mixer["volumes"]["cacheGi"], 65)


if __name__ == "__main__":
    unittest.main()
