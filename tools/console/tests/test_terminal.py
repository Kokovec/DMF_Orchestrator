"""The terminal (spec 05, Tests: Terminal): the done marker and its status, reattach replay, the
injection guards, bash restarting, and that nothing is written to disk."""
import asyncio
import os
import tempfile
import time
import unittest

from tools.console import terminal as T
from tools.console.terminal import Busy, Terminal
from tools.console.tests.helpers import Audit

BASH = ("bash", "--norc", "--noprofile", "-i")


class Recorder:
    def __init__(self):
        self.out = bytearray()
        self.events = []

    def output(self, data):
        self.out += data

    def event(self, e):
        self.events.append(e)

    async def wait(self, pred, timeout=5):
        end = time.monotonic() + timeout
        while time.monotonic() < end:
            if pred():
                return True
            await asyncio.sleep(0.02)
        raise AssertionError(f"timed out; output so far: {bytes(self.out[-400:])!r}")

    def done(self):
        return [e for e in self.events if e["type"] == "done"]


class TerminalTest(unittest.IsolatedAsyncioTestCase):
    async def asyncSetUp(self):
        self.dir = tempfile.TemporaryDirectory(prefix="dmf-console-term-")
        self.rec = Recorder()
        env = {"PATH": "/usr/bin:/bin", "HOME": self.dir.name, "PS1": "$ ", "HISTFILE": "/dev/null",
               "MAKELEVEL": "1", "MAKEFLAGS": "s"}
        self.t = Terminal(self.dir.name, argv=BASH, env=env, on_output=self.rec.output, on_event=self.rec.event)
        self.t.start()
        await self.rec.wait(lambda: b"$ " in self.rec.out)

    async def asyncTearDown(self):
        self.t.stop()
        self.dir.cleanup()

    async def test_done_marker_and_status(self):
        for command, status in (("true", 0), ("false", 1), ("(exit 7)", 7), ("echo hi && false", 1)):
            with self.subTest(command):
                n = len(self.rec.done())
                self.t.run("test", command)
                self.assertEqual(self.rec.events[-1]["type"], "busy")
                with self.assertRaises(Busy):  # one action at a time
                    self.t.run("test", "true")
                await self.rec.wait(lambda: len(self.rec.done()) > n)
                self.assertEqual(self.rec.done()[-1]["status"], status)
                self.assertIsNone(self.t.action)
                await self.rec.wait(self.t.shell_idle)

    async def test_not_a_sub_make(self):
        self.t.run("test", 'echo "level=${MAKELEVEL-none} flags=${MAKEFLAGS-none}"')
        await self.rec.wait(lambda: self.rec.done())
        self.assertIn(b"level=none flags=none", self.rec.out)

    def test_marker_split_across_reads(self):
        self.t.action = {"action": "x", "command": "x", "started": time.time()}
        marker = b"\x1b]777;dmf-done;42\x07"
        for i in range(1, len(marker)):
            self.t.action = self.t.action or {"action": "x", "command": "x", "started": time.time()}
            self.t._emit(b"output " + marker[:i])
            self.t._emit(marker[i:] + b"$ ")
            self.assertEqual(self.rec.done()[-1]["status"], 42, i)
        # The literal text of the command line, as bash echoes it, is not a marker.
        self.t.action = {"action": "x", "command": "x", "started": time.time()}
        self.t._emit(T.DONE_SUFFIX.encode())
        self.assertIsNotNone(self.t.action)
        self.t.action = None

    async def test_interrupted_command_ends_without_status(self):
        self.t.run("test", "sleep 30")
        await self.rec.wait(lambda: not self.t.shell_idle())
        self.t.write("\x03")  # Ctrl-C ends the whole list, printf included
        await self.rec.wait(lambda: self.rec.done(), timeout=6)
        self.assertIsNone(self.rec.done()[-1]["status"])

    async def test_refuses_to_inject_over_typing_or_a_running_command(self):
        self.t.write("echo half")
        with self.assertRaisesRegex(Busy, "partly typed"):
            self.t.run("test", "true")
        self.t.write("\x15")  # Ctrl-U clears it
        self.t.run("test", "true")
        await self.rec.wait(lambda: self.rec.done())
        await self.rec.wait(self.t.shell_idle)
        self.t.write("x\x7f")  # typed and erased: empty again
        self.assertEqual(self.t.line, 0)
        self.t.write("\x1b[A")  # history recall: unknown contents
        with self.assertRaisesRegex(Busy, "partly typed"):
            self.t.run("test", "true")
        self.t.write("\x03")
        await self.rec.wait(self.t.shell_idle)
        await asyncio.sleep(0.3)  # let readline redraw its prompt, or it drops the next keys
        # Leading spaces: readline can still drop the first keys after Ctrl-C (seen as "leep").
        self.t.write("   sleep 30\r")  # the user's own command
        await self.rec.wait(lambda: not self.t.shell_idle())
        with self.assertRaisesRegex(Busy, "something is running"):
            self.t.run("test", "true")
        self.t.write("\x03")

    async def test_replay_keeps_last_lines(self):
        self.t.run("test", f"seq 1 {T.REPLAY_LINES + 500}")
        await self.rec.wait(lambda: self.rec.done(), timeout=10)
        replay = self.t.replay()
        self.assertLessEqual(replay.count(b"\n"), T.REPLAY_LINES)
        self.assertIn(f"\r\n{T.REPLAY_LINES + 500}\r\n".encode(), replay)
        self.assertNotIn(b"\r\n1\r\n", replay)
        self.assertEqual(bytes(self.rec.out).endswith(replay), True)

    async def test_bash_exit_starts_a_new_one(self):
        old = self.t.pid
        self.t.write("exit 3\r")
        await self.rec.wait(lambda: b"started a new one" in self.rec.out)
        self.assertIn(b"bash exited (3)", self.rec.out)
        self.assertNotEqual(self.t.pid, old)
        await self.rec.wait(self.t.shell_idle)
        self.t.run("test", "true")
        await self.rec.wait(lambda: self.rec.done())
        self.assertEqual(self.rec.done()[-1]["status"], 0)

    async def test_nothing_written_to_disk(self):
        before = snapshot(self.dir.name)
        Audit.start()
        try:
            self.t.run("test", "echo CANARY-OUTPUT; seq 1 50")
            await self.rec.wait(lambda: self.rec.done())
            self.t.write("echo typed-by-user\r")
            await self.rec.wait(lambda: b"typed-by-user\r\n" in self.rec.out)
            self.t.write("exit\r")
            await self.rec.wait(lambda: b"started a new one" in self.rec.out)
        finally:
            events = Audit.stop()
        self.assertEqual(Audit.writes(events), [])
        self.assertIn(b"CANARY-OUTPUT", self.t.replay())
        self.assertEqual(snapshot(self.dir.name), before)


def snapshot(root):
    return sorted((p, os.path.getsize(os.path.join(d, p))) for d, _, files in os.walk(root) for p in files)


if __name__ == "__main__":
    unittest.main()
