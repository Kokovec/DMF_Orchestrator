"""Reading: refusal before parsing, schema validation, and the value tree round trip."""
import builtins
import io
import json
import os
import socket
import unittest
import urllib.request
import uuid
from unittest import mock

from tools.dmf_compile import reader
from tools.dmf_compile.problems import CompileError

from .util import STUDIO_A, TOOLKIT_FIXTURES, Doc, strict_equal

NS = reader.NS
HEAD = '<?xml version="1.0" encoding="UTF-8"?>\n'


def refused(data):
    try:
        reader.read(data)
    except CompileError as e:
        return [str(p) for p in e.problems]
    raise AssertionError("not refused")


class NoAccess:
    """Fails the test on any attempt to open a file or a connection while active."""

    def __init__(self, test):
        self.test = test
        self.attempts = []

    def _deny(self, what):
        def f(*a, **k):
            self.attempts.append((what, a[:1]))
            raise PermissionError(f"test forbids {what}")
        return f

    def __enter__(self):
        reader.schema()   # loaded from this repo before access is cut off
        self.patches = [mock.patch.object(builtins, "open", self._deny("open")),
                        mock.patch.object(io, "open", self._deny("io.open")),
                        mock.patch.object(os, "open", self._deny("os.open")),
                        mock.patch.object(socket.socket, "connect", self._deny("connect")),
                        mock.patch.object(socket, "create_connection", self._deny("create_connection")),
                        mock.patch.object(socket, "getaddrinfo", self._deny("getaddrinfo")),
                        mock.patch.object(urllib.request, "urlopen", self._deny("urlopen"))]
        for p in self.patches:
            p.start()
        return self

    def __exit__(self, *exc):
        for p in self.patches:
            p.stop()
        self.test.assertEqual(self.attempts, [], "file or network access was attempted")


class RoundTrip(unittest.TestCase):
    def fixture(self, name):
        xml, js = TOOLKIT_FIXTURES / f"{name}.workload.xml", TOOLKIT_FIXTURES / f"{name}.published.json"
        if not xml.exists():
            raise unittest.SkipTest(f"toolkit fixtures not found at {TOOLKIT_FIXTURES}")
        return xml.read_bytes(), json.loads(js.read_text())

    def check(self, name):
        data, published = self.fixture(name)
        doc = reader.read(data)
        doc.pop("_schemaVersion")
        self.assertEqual(strict_equal(doc, published), [])

    def test_every_value_reads_back_to_its_published_json(self):
        self.check("every-value")

    def test_studio_a_reads_back_to_its_published_json(self):
        self.check("studio-a")

    def test_example_is_the_toolkit_fixture(self):
        data, _ = self.fixture("studio-a")
        self.assertEqual(STUDIO_A.read_bytes(), data, "examples/studio-a.workload.xml is not the toolkit's current output")

    def test_schema_is_the_toolkits_copy(self):
        toolkit = TOOLKIT_FIXTURES.parents[1] / "schemas" / "dmf-workload-v1.xsd"
        if not toolkit.exists():
            raise unittest.SkipTest("toolkit not found")
        self.assertEqual(reader.SCHEMA_PATH.read_bytes(), toolkit.read_bytes())

    def test_values_of_every_type(self):
        d = Doc()
        d.settings("vision-mixer", [("a[0][0]", "number", "-0.125"), ("a[0][1]", "integer", "-0"),
                                    ("b.c", "secret", "s1"), ("b.d", "null", ""), ("e", "emptyArray", ""),
                                    ("f", "string", ""), ("g-h_i", "boolean", "false")])
        settings = reader.read(d.bytes())["spec"]["functions"][3]["settings"]
        self.assertEqual(strict_equal(settings, {"a": [[-0.125, 0]], "b": {"c": {"$secret": "s1"}, "d": None},
                                                 "e": [], "f": "", "g-h_i": False}), [])

    def test_bad_value_trees(self):
        for values, msg in (([("x", "integer", "1.5")], "not an integer"),
                            ([("x", "boolean", "yes")], "not true or false"),
                            ([("x", "null", "x")], "must be empty"),
                            ([("x", "string", "a"), ("x", "string", "b")], "given twice"),
                            ([("x[1]", "string", "a")], "skips"),
                            ([("x[1]", "string", "a"), ("x[0]", "string", "b")], "skips"),
                            ([("x", "string", "a"), ("x.y", "string", "b")], "leaf"),
                            ([("x[0]", "string", "a"), ("x.y", "string", "b")], "object")):
            with self.subTest(values=values):
                d = Doc().settings("vision-mixer", values)
                self.assertTrue(any(msg in p for p in refused(d.bytes())), refused(d.bytes()))

    def test_flow_identity_vector(self):
        # 01-contract.md: workload t1, writer sw/mxl-out.video
        ns = uuid.UUID("9e6d3b0a-7c1f-5d4e-8a2b-1f0c3d5e7a9b")
        uid = uuid.uuid5(ns, "t1")
        self.assertEqual(str(uuid.uuid5(uid, "sw/mxl-out.video")), "11d120ff-ba57-5632-bc10-0a2c5e3ec600")

    def test_published_flow_ids_follow_the_contract(self):
        doc = reader.read(STUDIO_A.read_bytes())
        uid = uuid.UUID(doc["metadata"]["uid"])
        self.assertEqual(str(uuid.uuid5(uuid.UUID("9e6d3b0a-7c1f-5d4e-8a2b-1f0c3d5e7a9b"), "studio-a")), str(uid))
        for flow in doc["spec"]["flows"]:
            self.assertEqual(flow["id"], str(uuid.uuid5(uid, flow["writer"])))


class Refusal(unittest.TestCase):
    VALID = STUDIO_A.read_bytes()

    def body(self):
        return self.VALID.split(b"\n", 1)[1]

    def test_doctype_is_refused_before_parsing(self):
        cases = {
            "external DTD": b'<!DOCTYPE workload SYSTEM "http://198.51.100.1/x.dtd">\n',
            "file DTD": b'<!DOCTYPE workload SYSTEM "file:///etc/passwd">\n',
            "internal subset": b"<!DOCTYPE workload [<!ELEMENT workload ANY>]>\n",
            "lower case": b'<!doctype workload SYSTEM "x">\n',
        }
        for name, decl in cases.items():
            with self.subTest(name), NoAccess(self), mock.patch.object(reader, "validate") as validate:
                problems = refused(HEAD.encode() + decl + self.body())
                validate.assert_not_called()
                self.assertIn("DOCTYPE declaration", problems[0])

    def test_entities_are_refused_before_parsing(self):
        cases = {
            "billion laughs": b'<!DOCTYPE w [<!ENTITY a "aaaa"><!ENTITY b "&a;&a;&a;&a;">]>\n',
            "external entity": b'<!ENTITY x SYSTEM "file:///etc/passwd">\n',
            "spaced": b'<! ENTITY x "y">\n',
        }
        for name, decl in cases.items():
            with self.subTest(name), NoAccess(self), mock.patch.object(reader, "validate") as validate:
                problems = refused(HEAD.encode() + decl + self.body())
                validate.assert_not_called()
                self.assertRegex(problems[0], "(DOCTYPE|ENTITY) declaration")

    def test_schema_invalid_documents_are_refused_without_access(self):
        valid = self.VALID.decode()
        cases = {
            "schemaLocation hint": valid.replace('xmlns="urn:dmf:workload:v1"', 'xmlns="urn:dmf:workload:v1" '
                                                 'xmlns:xsi="http://www.w3.org/2001/XMLSchema-instance" '
                                                 'xsi:schemaLocation="urn:dmf:workload:v1 http://198.51.100.1/evil.xsd" bogus="1"'),
            "xinclude": valid.replace("<domains>", '<xi:include xmlns:xi="http://www.w3.org/2001/XInclude" '
                                                   'href="file:///etc/passwd" parse="text"/><domains>'),
            "wrong namespace": valid.replace("urn:dmf:workload:v1", "urn:dmf:workload:v2"),
            "schemaVersion 2": valid.replace('schemaVersion="1"', 'schemaVersion="2"'),
            "bad id": valid.replace('id="cam2"', 'id="Cam_2"'),
            "duplicate function": valid.replace('id="cam2"', 'id="cam1"'),
            "historyMs 0": valid.replace('historyMs="500"', 'historyMs="0"', 1),
            "unknown value type": valid.replace('type="integer">2<', 'type="int">2<', 1),
        }
        for name, text in cases.items():
            with self.subTest(name), NoAccess(self):
                problems = refused(text.encode())
                self.assertTrue(problems and all("schema:" in p or "well-formed" in p for p in problems), problems)

    def test_valid_document_reads_without_access(self):
        with NoAccess(self):
            self.assertEqual(reader.read(self.VALID)["metadata"]["id"], "studio-a")

    def test_not_utf8(self):
        self.assertIn("not UTF-8", refused(self.VALID.decode().encode("utf-16"))[0])
        self.assertIn("not UTF-8", refused(self.VALID.replace(b"Studio A", b"Studio \xff"))[0])

    def test_not_well_formed(self):
        self.assertIn("well-formed", refused(self.VALID[:-20])[0])


if __name__ == "__main__":
    unittest.main()
