"""The console's one terminal: a PTY running `bash -l` in the repo (spec: Terminal, C5, C6).

Output is held in memory only, as the last REPLAY_LINES lines, and is never written to disk:
plan output, `make secrets` and API responses typed into this shell can hold passphrases.

A button's command is typed into the shell followed by DONE_SUFFIX, whose OSC 777 sequence tells
the console that the command ended and with what status. xterm.js ignores OSC sequences it has
no handler for, so it isn't displayed.
"""
import asyncio
import errno
import fcntl
import os
import pty
import re
import signal
import struct
import termios
import time

REPLAY_LINES = 2000
REPLAY_MAX_BYTES = 4 * 2**20  # also bounds one endless line
DONE_SUFFIX = "; printf '\\033]777;dmf-done;%s\\007' \"$?\""
DONE_RE = re.compile(rb"\x1b\]777;dmf-done;(\d+)\x07")
DONE_TAIL = 32  # longer than any marker, to find one split across reads
IDLE_GRACE = 1.5  # seconds the shell must be back at its prompt before a lost marker is assumed


class Busy(Exception):
    """Why a command can't be typed now, in words for the page."""


class Terminal:
    def __init__(self, cwd, argv=("bash", "-l"), env=None, on_output=None, on_event=None):
        self.cwd = str(cwd)
        self.argv = list(argv)
        self.env = dict(os.environ if env is None else env)
        # make console's own make would otherwise make every make typed here a sub-make, with its flags.
        for k in ("MAKELEVEL", "MAKEFLAGS", "MFLAGS", "MAKEOVERRIDES", "MAKE_TERMOUT", "MAKE_TERMERR"):
            self.env.pop(k, None)
        self.env.setdefault("TERM", "xterm-256color")
        self.env["COLORTERM"] = "truecolor"
        self.on_output = on_output or (lambda data: None)  # bytes
        self.on_event = on_event or (lambda event: None)   # dict
        self.buffer = bytearray()
        self.tail = b""
        self.pid = None
        self.fd = None
        self.size = (24, 80)
        self.action = None  # {"action", "command", "started"} while a button's command runs
        self.line = 0       # what the user has typed since their last Enter: 0 empty, -1 unknown
        self._idle_since = None
        self._watch = None

    # ---- process ----

    def start(self, notice=None):
        pid, fd = pty.fork()
        if pid == 0:  # child: exec at once
            try:
                os.chdir(self.cwd)
                os.execvpe(self.argv[0], self.argv, self.env)
            finally:
                os._exit(127)
        self.pid, self.fd = pid, fd
        self.line = 0
        os.set_blocking(fd, False)
        self.resize(*self.size)
        asyncio.get_running_loop().add_reader(fd, self._read)
        if notice:
            self._emit(f"\r\n\x1b[33m[console] {notice}\x1b[0m\r\n".encode())

    def stop(self):
        if self.fd is None:
            return
        asyncio.get_running_loop().remove_reader(self.fd)
        try:
            os.killpg(self.pid, signal.SIGHUP)
        except ProcessLookupError:
            pass
        os.close(self.fd)
        self.fd = None
        self._reap(block=True)
        if self._watch:
            self._watch.cancel()

    def _reap(self, block=False):
        try:
            _, status = os.waitpid(self.pid, 0 if block else os.WNOHANG)
            return os.waitstatus_to_exitcode(status) if status or block else 0
        except ChildProcessError:
            return None

    def _read(self):
        try:
            data = os.read(self.fd, 65536)
        except BlockingIOError:
            return
        except OSError as e:
            if e.errno != errno.EIO:
                raise
            data = b""
        if data:
            self._emit(data)
            return
        # bash exited: start another one and say so.
        asyncio.get_running_loop().remove_reader(self.fd)
        os.close(self.fd)
        self.fd = None
        code = self._reap(block=True)
        if self.action:
            self._finish(None)
        self.start(notice=f"bash exited ({code}); started a new one")

    def _emit(self, data):
        self.buffer += data
        self._trim()
        scan = self.tail + data
        last = None
        for m in DONE_RE.finditer(scan):
            last = m
        self.tail = scan[last.end():] if last else scan
        self.tail = self.tail[-DONE_TAIL:]
        self.on_output(data)
        if last and self.action:
            self._finish(int(last.group(1)))

    def _trim(self):
        if len(self.buffer) > REPLAY_MAX_BYTES:
            del self.buffer[:len(self.buffer) - REPLAY_MAX_BYTES]
        if self.buffer.count(b"\n") > REPLAY_LINES:
            cut = len(self.buffer)
            for _ in range(REPLAY_LINES):
                cut = self.buffer.rfind(b"\n", 0, cut)
            del self.buffer[:cut + 1]

    def replay(self):
        return bytes(self.buffer)

    # ---- input ----

    def write(self, data):
        """Keystrokes from the page."""
        if self.fd is None:
            return
        if isinstance(data, str):
            data = data.encode()
        self._track_line(data)
        os.write(self.fd, data)

    def _track_line(self, data):
        for b in data:
            if b in (0x0d, 0x03, 0x15):  # Enter, Ctrl-C, Ctrl-U: the line is empty again
                self.line = 0
            elif b in (0x7f, 0x08):      # Backspace
                if self.line > 0:
                    self.line -= 1
            elif b == 0x1b:              # arrows, history, Alt-...: can't tell what's on the line
                self.line = -1
            elif b >= 0x20 or b == 0x09:
                if self.line >= 0:
                    self.line += 1

    def resize(self, rows, cols):
        self.size = (max(int(rows), 2), max(int(cols), 10))
        if self.fd is not None:
            fcntl.ioctl(self.fd, termios.TIOCSWINSZ, struct.pack("HHHH", self.size[0], self.size[1], 0, 0))

    def shell_idle(self):
        """Is bash itself in the foreground, i.e. at its prompt rather than running something?"""
        try:
            return os.tcgetpgrp(self.fd) == self.pid
        except OSError:
            return False

    def run(self, action, command):
        """Type a button's command, with the done marker. Raises Busy instead of interrupting."""
        if self.action:
            raise Busy(f"{self.action['command']} is still running")
        if self.fd is None:
            raise Busy("the terminal isn't running")
        if not self.shell_idle():
            raise Busy("something is running in the terminal; wait for it to finish, or stop it, first")
        if self.line != 0:
            raise Busy("there's a partly typed line in the terminal; clear it (Ctrl-U) or run it first")
        self.action = {"action": action, "command": command, "started": time.time()}
        self.on_event({"type": "busy", **self.action})
        os.write(self.fd, (command + DONE_SUFFIX + "\r").encode())
        self._idle_since = None
        self._watch = asyncio.get_running_loop().create_task(self._watch_idle())

    async def _watch_idle(self):
        # If the command is interrupted (Ctrl-C ends the whole list, printf included), no marker
        # comes: once bash has been back at its prompt for IDLE_GRACE, end the action unknown.
        while self.action:
            await asyncio.sleep(0.25)
            if not self.action or time.time() - self.action["started"] < IDLE_GRACE:
                continue
            if self.fd is not None and self.shell_idle():
                self._idle_since = self._idle_since or time.time()
                if time.time() - self._idle_since >= IDLE_GRACE:
                    self._finish(None)
            else:
                self._idle_since = None

    def _finish(self, status):
        action, self.action = self.action, None
        self.on_event({"type": "done", "action": action["action"], "command": action["command"], "status": status})
