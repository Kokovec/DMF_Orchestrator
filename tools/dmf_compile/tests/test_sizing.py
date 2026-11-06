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

    def test_domain_studio_a(self):
        # 4 x 1080p29.97 = 632 MiB/s; x1.25 + 256 = 1047, under the 2048 floor.
        self.assertEqual(sizing.domain_size_mib([(1920, 1080, NTSC)] * 4), 2048)

    def test_domain_2160p(self):
        # 4 x 22,118,400 B x 29.97 = 2528.7 MiB/s; ceil(x1.25) = 3161; + 256.
        self.assertEqual(sizing.domain_size_mib([(3840, 2160, NTSC)] * 4), 3417)
        self.assertEqual(sizing.domain_size_mib([(3840, 2160, Fraction(60000, 1001))] * 4), 6578)
        self.assertEqual(sizing.domain_size_mib([(3840, 2160, Fraction(25))] * 2), 2048)

    def test_switchframe_resources(self):
        self.assertEqual(sizing.switchframe_resources(2, 4096), (
            {"requests": {"cpu": "2", "memory": "4Gi"}, "limits": {"cpu": "6", "memory": "8Gi"}}, "6GiB"))
        self.assertEqual(sizing.switchframe_resources(2.5, 3000), (
            {"requests": {"cpu": "2500m", "memory": "3000Mi"}, "limits": {"cpu": "7500m", "memory": "6000Mi"}}, "4500MiB"))

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
        self.assertEqual(sizing.volumes(10), {"stateGi": 11, "cacheGi": 5})
        self.assertEqual(sizing.volumes(2), {"stateGi": 5, "cacheGi": 5})
        self.assertEqual(sizing.volumes(None), {"stateGi": 11, "cacheGi": 5})
        self.assertEqual(sizing.volumes(10, True, 50, 100), {"stateGi": 11, "cacheGi": 52, "recordingsGi": 100})
        self.assertEqual(sizing.volumes(10, True), {"stateGi": 11, "cacheGi": 202})


class CompiledSizes(RepoTest):
    def test_studio_a(self):
        r = self.repo.compile(Doc().bytes())
        self.assertEqual(r.doc["spec"]["resourceProfile"], {"cpu": "4", "memory": "6Gi", "gpu": 0})
        self.assertEqual(r.doc["spec"]["mediaExchange"]["sizeMiB"], 2048)

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
        # 3 x 22,118,400 B x 29.97 = 1896.5 MiB/s; ceil(x1.25) = 2371; + 256.
        self.assertEqual(r.doc["spec"]["mediaExchange"]["sizeMiB"], 2627)


if __name__ == "__main__":
    unittest.main()
