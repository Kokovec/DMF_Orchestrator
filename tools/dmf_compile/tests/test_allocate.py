import json
import unittest

from tools.dmf_compile import allocate

from .util import Doc, RepoTest


class Allocation(RepoTest):
    def alloc(self, result):
        a = result.doc["spec"]
        return a["mediaExchange"]["uid"], a["access"]["quicPort"], a["access"]["srtPort"]

    def test_first_workload_skips_the_hand_written_ones(self):
        r = self.repo.compile_and_write(Doc().bytes())
        self.assertEqual(self.alloc(r), (20002, 8444, 6465))   # news-studio has 20001, 8443, 6464
        self.assertTrue(r.new_allocation)

    def test_reimport_keeps_the_allocation(self):
        first = self.repo.compile_and_write(Doc().bytes())
        self.repo.compile_and_write(Doc().rename("studio-b").bytes())
        allocate.release(self.repo.path, "studio-b")
        again = self.repo.compile_and_write(Doc().bytes())
        self.assertEqual(self.alloc(again), self.alloc(first))
        self.assertFalse(again.new_allocation)
        saved = json.loads((self.repo.path / ".state" / "allocations.json").read_text())
        self.assertEqual(list(saved), ["studio-a"])
        self.assertEqual({f: saved["studio-a"][f] for f in allocate.FIELDS}, {"uid": 20002, "quicPort": 8444, "srtPort": 6465})
        self.assertEqual(saved["studio-a"]["workloadUid"], "9dd110d0-8c14-5c77-acf6-3eb708bf0c13")
        self.assertEqual(len(saved["studio-a"]["flowIds"]), 8)

    def test_second_workload_gets_different_values(self):
        a = self.repo.compile_and_write(Doc().bytes())
        b = self.repo.compile_and_write(Doc().rename("studio-b").bytes())
        self.assertEqual(self.alloc(b), (20003, 8445, 6466))
        self.assertTrue(set(self.alloc(a)).isdisjoint(self.alloc(b)))
        self.assertEqual(b.doc["spec"]["access"]["hostname"], "studio-b.dmf.test")
        self.assertEqual(b.doc["spec"]["mediaExchange"]["domain"], "studio-b")

    def test_generated_file_counts_without_its_allocation(self):
        self.repo.compile_and_write(Doc().bytes())
        (self.repo.path / ".state" / "allocations.json").unlink()
        b = self.repo.compile(Doc().rename("studio-b").bytes())
        self.assertEqual(self.alloc(b), (20003, 8445, 6466))

    def test_release_frees_the_lowest_values(self):
        self.repo.compile_and_write(Doc().bytes())
        self.repo.compile_and_write(Doc().rename("studio-b").bytes())
        allocate.release(self.repo.path, "studio-a")
        (self.repo.gen / "studio-a.yaml").unlink()
        c = self.repo.compile(Doc().rename("studio-c").bytes())
        self.assertEqual(self.alloc(c), (20002, 8444, 6465))

    def test_range_exhausted(self):
        fac = self.repo.facility("tiny", **{"ports.quic.first": 8443, "ports.quic.last": 8443})
        problems = self.repo.problems(Doc().bytes(), fac)
        self.assertIn("no free quicPort in 8443-8443 of facility tiny", [p.message for p in problems])

    def test_refused_compile_saves_no_allocation(self):
        d = Doc().set("vision-mixer", "engine.engine.rate", "24").set("vision-mixer", "engine.engine.resolution", "1280x720")
        self.repo.problems(d.bytes())
        self.assertFalse((self.repo.path / ".state" / "allocations.json").exists())


if __name__ == "__main__":
    unittest.main()
