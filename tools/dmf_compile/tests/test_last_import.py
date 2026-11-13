""".state/last-import.json (05-facility-console.md, Deploy and Update): written on every import,
refused or not, naming a missing secret, and never holding a secret value."""
import json
import stat

from .util import FULL_SETTINGS_SECRETS, STUDIO_A, RepoTest, full_settings


class LastImport(RepoTest):
    def record(self):
        return json.loads((self.repo.path / ".state" / "last-import.json").read_text())

    def xml(self, doc, name="design.workload.xml"):
        path = self.repo.path / name
        path.write_bytes(doc.bytes())
        return str(path)

    def test_compiled(self):
        rc, _, _ = self.repo.cli("compile", str(STUDIO_A))
        self.assertEqual(rc, 0)
        r = self.record()
        self.assertEqual((r["ok"], r["id"], r["source"], r["facility"], r["problems"]), (True, "studio-a", str(STUDIO_A), "local", []))
        self.assertIsInstance(r["revision"], int)
        self.assertEqual(set(r["allocation"]), {"uid", "gid", "quicPort", "srtPort", "new"})
        self.assertIs(r["allocation"]["new"], True)
        self.assertEqual(r["hostname"], "studio-a.dmf.test")
        self.assertIn("workloads/generated/studio-a.yaml", r["files"])
        self.assertFalse((self.repo.path / ".state" / "last-import.json.tmp").exists())
        # A second import keeps the allocation, and says so.
        self.repo.cli("compile", str(STUDIO_A))
        self.assertIs(self.record()["allocation"]["new"], False)

    def test_missing_secrets_are_named(self):
        rc, _, _ = self.repo.cli("compile", self.xml(full_settings()))
        self.assertEqual(rc, 1)
        r = self.record()
        self.assertEqual((r["ok"], r["id"], r["allocation"]), (False, "full-settings", None))
        self.assertEqual({p["rule"] for p in r["problems"]}, {6})
        self.assertEqual(sorted(p["secret"] for p in r["problems"]), ["venue-srt", "yt-key"])
        for p in r["problems"]:
            self.assertEqual(set(p), {"rule", "where", "message", "secret"})
            self.assertIn(f"make secret-set WORKLOAD=full-settings NAME={p['secret']}", p["message"])

    def test_no_secret_value_with_secrets_set(self):
        canaries = {"venue-srt": "CANARY-passphrase-0123", "yt-key": "CANARY-streamkey-4567"}
        self.assertEqual(set(canaries), set(FULL_SETTINGS_SECRETS))
        self.repo.secrets("full-settings", canaries)
        rc, _, _ = self.repo.cli("compile", self.xml(full_settings()))
        self.assertEqual(rc, 0, self.record()["problems"])
        text = (self.repo.path / ".state" / "last-import.json").read_text()
        self.assertTrue(self.record()["ok"])
        self.assertNotIn("CANARY", text)

        # Refused with secrets present: a value of the wrong length is named, not shown.
        self.repo.secrets("full-settings", {**canaries, "venue-srt": "CANARY"})
        rc, _, _ = self.repo.cli("compile", self.xml(full_settings()))
        self.assertEqual(rc, 1)
        text = (self.repo.path / ".state" / "last-import.json").read_text()
        self.assertNotIn("CANARY", text)
        self.assertEqual([p.get("secret") for p in self.record()["problems"]], ["venue-srt"])

    def test_problem_secret_set_cant_fix_has_no_name(self):
        self.repo.secrets("full-settings", FULL_SETTINGS_SECRETS, mode=0o644)
        self.repo.cli("compile", self.xml(full_settings()))
        problems = self.record()["problems"]
        self.assertTrue(problems)
        self.assertTrue(any("is mode 644" in p["message"] and "secret" not in p for p in problems), problems)

    def test_unreadable_source(self):
        self.repo.cli("compile", str(STUDIO_A))
        rc, _, _ = self.repo.cli("compile", str(self.repo.path / "missing.xml"))
        self.assertEqual(rc, 1)
        r = self.record()  # replaces the earlier successful import's record
        self.assertEqual((r["ok"], r["id"], r["revision"], r["files"]), (False, None, None, []))
        self.assertEqual([p["rule"] for p in r["problems"]], ["read"])

    def test_refused_remote_url_and_bad_xml(self):
        self.repo.cli("compile", "http://example.com/x.xml")
        self.assertIn("only http://localhost", self.record()["problems"][0]["message"])
        bad = self.repo.path / "bad.xml"
        bad.write_text("<nope/>")
        rc, _, _ = self.repo.cli("compile", str(bad))
        self.assertEqual(rc, 1)
        self.assertEqual(self.record()["problems"][0]["rule"], "read")

    def test_not_world_writable(self):
        self.repo.cli("compile", str(STUDIO_A))
        mode = stat.S_IMODE((self.repo.path / ".state" / "last-import.json").stat().st_mode)
        self.assertFalse(mode & 0o022)
