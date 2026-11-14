"""Deploy, Update and Redeploy (docs/orchestration/05-facility-console.md, Deploy and Update).

Separate steps instead of `make deploy`, so the plan is reviewed before anything changes:

  import     make import XML=<url or file>                 reads .state/last-import.json
  secrets    make secret-set WORKLOAD=<id> NAME=<name>      one at a time, then import again
  plan       make plan WORKLOAD=<id>                        reads .state/<id>.plan-summary.json
  review     the page shows the summary and the import's warnings
  provision  make provision WORKLOAD=<id>                   sudo asks in the terminal

Each command is typed into the console's one terminal. What a step found comes only from the file
the step writes, never from terminal output, and a file counts only if this step wrote it (its
mtime is after the step started, and it names this source or workload). The user's secrets and
sudo password are typed in the terminal; this module never sees them.
"""
import json
import re
import shlex
import time

from .status import WORKLOAD_ID
from .terminal import Busy

SECRET_NAME = re.compile(r"^[-._A-Za-z0-9]{1,253}$")  # tools/dmf_compile/compiler.py
ENDED = ("done", "refused", "cancelled")
AUTO_DELAY = 0.3  # seconds between one step's end and the next step's command: let bash show its prompt
MTIME_SLACK = 1.0


class FlowError(Exception):
    """Why an operation isn't possible now, in words for the page."""


def describe(status):
    return "interrupted, no exit status" if status is None else f"exit {status}"


class Flow:
    def __init__(self, repo, run, emit, later, facility="local"):
        """run(action, command) types a command (raises Busy); emit(state) tells the page;
        later(seconds, fn) schedules fn on the event loop."""
        self.repo = repo
        self.run = run
        self.emit = emit
        self.later = later
        self.facility = facility
        self.state = None

    # ---- what the page sees ----

    def active(self):
        return self.state is not None and self.state["step"] not in ENDED

    def snapshot(self):
        return self.state

    def _emit(self):
        self.emit(self.state)

    # ---- operations from the page ----

    def start(self, kind, source, workload=None, note=None):
        if self.active():
            raise FlowError(f"a {self.state['kind']} of {self.state['id'] or self.state['source']} is still open: "
                            "finish or cancel it first")
        self.state = {"kind": kind, "source": source, "id": workload, "step": "import", "running": False,
                      "pending": False, "error": None, "message": None, "note": note, "command": None, "started": None,
                      "import": None, "secrets": [], "summary": None}
        self._run("import")

    def op(self, op):
        st = self.state
        if st is None:
            raise FlowError("no deploy is open")
        if op == "close":
            if self.active():
                raise FlowError("cancel it first")
            self.state = None
            self.emit(None)
            return
        if st["pending"] and op != "cancel":
            raise FlowError("the next step is about to start")
        if st["running"]:
            raise FlowError(f"{st['command']} is still running: wait for it, or stop it in the terminal (Ctrl-C)")
        if op == "cancel":
            if not self.active():
                raise FlowError("it has already ended")
            st.update(step="cancelled", pending=False, error=None, message="Cancelled.")
            self._emit()
        elif op == "run":
            if st["step"] not in ("import", "secrets", "plan", "provision"):
                raise FlowError(f"nothing to run at step {st['step']}")
            self._run(st["step"])
        elif op == "provision":
            if st["step"] != "review":
                raise FlowError("review the plan first")
            self._run("provision")
        elif op == "plan":
            if st["step"] not in ("review", "plan"):
                raise FlowError(f"can't plan at step {st['step']}")
            self._run("plan")
        else:
            raise FlowError(f"unknown operation {op!r}")

    def facility_changed(self, action, workload=None):
        """Start, Stop or Finalise was just typed while this deploy is open. The deploy stays open,
        but a plan it made no longer describes the facility, and finalising its own workload deletes
        the secrets and generated files it relies on."""
        st = self.state
        if not self.active():
            return
        if action == "finalise" and workload and workload == st["id"]:
            st.update(step="cancelled", pending=False, error=None,
                      message=f"Cancelled: finalise deletes {workload}'s secrets and generated files. "
                              "Deploy it again when finalise has finished.")
        elif st["step"] in ("review", "provision"):
            st.update(step="plan", summary=None, pending=False, error=None,
                      message=f"make {action} changes the facility this plan was made against. "
                              "When it has finished, run the plan again.")
        else:
            return
        self._emit()

    # ---- running steps ----

    def command(self, step):
        st = self.state
        if step == "import":
            fac = "" if self.facility == "local" else f" FACILITY={shlex.quote(self.facility)}"
            return f"make import XML={shlex.quote(st['source'])}{fac}"
        wid = st["id"]
        if not wid or not WORKLOAD_ID.match(wid):
            raise FlowError(f"workload id {wid!r} isn't valid")
        if step == "secrets":
            name = st["secrets"][0]
            if not SECRET_NAME.match(name):
                raise FlowError(f"secret name {name!r} isn't valid")
            return f"make secret-set WORKLOAD={wid} NAME={shlex.quote(name)}"
        return f"make {step} WORKLOAD={wid}"

    def _run(self, step):
        st = self.state
        st.update(step=step, pending=False)
        command = self.command(step)
        try:
            self.run(f"flow-{step}", command)
        except Busy as e:
            st.update(running=False, command=command, error=f"Can't run {command} now: {e}. Then press Run.")
            self._emit()
            return
        st.update(running=True, command=command, started=time.time(), error=None, message=None)
        self._emit()

    def _next(self, step):
        """Run the next step shortly, unless the flow changed meanwhile."""
        st = self.state

        def go():
            if self.state is st and st["step"] == step and st["pending"]:
                self._run(step)
        st.update(step=step, pending=True)
        self.later(AUTO_DELAY, go)

    def on_done(self, event):
        """The terminal's done event for any command."""
        st = self.state
        if not st or not st["running"] or event.get("action") != f"flow-{st['step']}":
            return
        st["running"] = False
        status = event.get("status")
        getattr(self, f"_after_{st['step']}")(status)
        self._emit()

    def _read(self, rel, belongs):
        """A step's JSON file, if this step wrote it."""
        path = self.repo / rel
        try:
            if path.stat().st_mtime < self.state["started"] - MTIME_SLACK:
                return None
            data = json.loads(path.read_text())
        except (OSError, ValueError):
            return None
        return data if isinstance(data, dict) and belongs(data) else None

    def _after_import(self, status):
        st = self.state
        rec = self._read(".state/last-import.json", lambda r: r.get("source") == st["source"])
        if rec is None:
            st["error"] = (f"make import ended ({describe(status)}) without writing .state/last-import.json "
                           "for this source: see the terminal")
            return
        st["import"] = {k: rec.get(k) for k in ("ok", "id", "revision", "allocation", "hostname", "warnings", "problems")}
        wid = rec.get("id")
        if rec.get("ok"):
            if not (isinstance(wid, str) and WORKLOAD_ID.match(wid)):
                st.update(step="refused", message=f"the import gave a workload id the console won't use: {wid!r}")
                return
            st.update(id=wid, secrets=[])
            self._next("plan")
            return
        problems = rec.get("problems") or []
        names = []
        for p in problems:
            name = p.get("secret")
            if isinstance(name, str) and SECRET_NAME.match(name) and name not in names:
                names.append(name)
        fixable = all(p.get("secret") in names for p in problems)
        if problems and fixable and isinstance(wid, str) and WORKLOAD_ID.match(wid):
            st.update(id=wid, secrets=names, step="secrets",
                      message=f"{len(names)} secret{'s' if len(names) != 1 else ''} to set, one at a time. "
                              "Type each value at the terminal's prompt, which doesn't echo it.")
        else:
            st.update(step="refused", message="The compiler refused this design." if problems else
                      f"make import failed ({describe(status)}) and reported no problem: see the terminal")

    def _after_secrets(self, status):
        st = self.state
        if status != 0:
            st["error"] = f"make secret-set ended ({describe(status)}): {st['secrets'][0]} wasn't set. Run it again."
            return
        st["secrets"].pop(0)
        if st["secrets"]:
            st["message"] = f"Set. Next: {st['secrets'][0]}."
            return
        st["message"] = "Secrets set: importing again."
        self._next("import")

    def _after_plan(self, status):
        st = self.state
        if status != 0:
            st["error"] = f"make plan failed ({describe(status)}): see the terminal. Fix the cause, then run it again."
            return
        summary = self._read(f".state/{st['id']}.plan-summary.json", lambda s: s.get("workload") == st["id"])
        if summary is None:
            st["error"] = f"make plan succeeded but didn't write .state/{st['id']}.plan-summary.json"
            return
        st.update(summary=summary, step="review", message="Review the plan, then provision.")

    def _after_provision(self, status):
        st = self.state
        if status == 0:
            st.update(step="done", message=f"Provisioned {st['id']}.")
            return
        st.update(step="plan", summary=None,
                  error=f"make provision failed ({describe(status)}): see the terminal. Plan again before provisioning.")
