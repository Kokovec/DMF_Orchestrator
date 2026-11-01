#!/usr/bin/env python3
"""mxl-flow-agent: Media Exchange discovery and monitoring for one MXL Flow Domain.

DMF RA v2.0, Media Exchange layer:
  * Provision: "Create the Flows within the Domains and make them discoverable"
      -> every flow in the domain is registered with an AMWA IS-04 v1.3
         Registration API as Node / Device / Source / Flow resources.
  * Monitoring: "real-time information about the status of Flows, such as
    presence/absence of payloads, throughput, latency"
      -> Prometheus metrics from `mxl-info` (active, head index, latency).

NMOS has no shared-memory transport yet (see the RA's "NMOS and shared media"
note), so no IS-04 Senders are registered. Flows carry tags identifying the
workload, the MXL domain and the provisional transport instead.

Standard library only. Configured through environment variables:
  MXL_DOMAIN          domain directory (required)
  WORKLOAD            workload name (required)
  NMOS_REGISTRY_URL   e.g. http://nmos-registry.dmf-system.svc:8010 (empty: no registration)
  NMOS_API_VERSION    default v1.3
  METRICS_ADDR        default 0.0.0.0:9102
  POLL_SECONDS        default 5
  MXL_INFO            default mxl-info
"""
import json
import logging
import os
import re
import signal
import socket
import subprocess
import sys
import threading
import time
import urllib.error
import urllib.request
import uuid
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path

FLOW_SUFFIX = ".mxl-flow"
FLOW_DEF = "flow_def.json"
TAI_OFFSET_S = 37  # TAI - UTC since 2017-01-01
NS = uuid.UUID("6f8a2b0e-6d0c-4d6e-9a59-0c8f0d6a1d10")  # namespace for derived ids

log = logging.getLogger("mxl-flow-agent")


def tai_version() -> str:
    now = time.time() + TAI_OFFSET_S
    sec = int(now)
    return f"{sec}:{int((now - sec) * 1e9)}"


def derived_id(*parts: str) -> str:
    return str(uuid.uuid5(NS, "/".join(parts)))


class FlowStatus:
    __slots__ = ("flow_id", "label", "fmt", "media_type", "active", "head_index", "latency_grains")

    def __init__(self, flow_id, label, fmt, media_type):
        self.flow_id = flow_id
        self.label = label
        self.fmt = fmt
        self.media_type = media_type
        self.active = None
        self.head_index = None
        self.latency_grains = None


_ANSI = re.compile(r"\x1b\[[0-9;]*m")
_FIELD = re.compile(r"^\s*([A-Za-z][A-Za-z /(),]*?)\s*:\s*(.+?)\s*$")


def probe_flow(mxl_info: str, domain: str, flow_id: str, status: FlowStatus) -> None:
    """Fill status from `mxl-info -d <domain> -f <flow>`; leaves fields None on failure."""
    try:
        out = subprocess.run(
            [mxl_info, "-d", domain, "-f", flow_id],
            capture_output=True, text=True, timeout=3, check=False,
        ).stdout
    except (OSError, subprocess.TimeoutExpired) as exc:
        log.debug("mxl-info failed for %s: %s", flow_id, exc)
        return
    for raw in out.splitlines():
        m = _FIELD.match(_ANSI.sub("", raw))
        if not m:
            continue
        key, val = m.group(1).strip().lower(), m.group(2)
        if key == "active":
            status.active = val.strip().lower() in ("true", "1", "yes")
        elif key == "head index":
            try:
                status.head_index = int(val.split()[0])
            except ValueError:
                pass
        elif key.startswith("latency"):
            num = re.match(r"[+-]?\d+", val)
            if num:
                status.latency_grains = int(num.group(0))


def scan_domain(domain: Path) -> dict:
    flows = {}
    for d in sorted(domain.glob(f"*{FLOW_SUFFIX}")):
        try:
            flows[d.name[: -len(FLOW_SUFFIX)]] = json.loads((d / FLOW_DEF).read_text())
        except (OSError, ValueError) as exc:
            log.warning("skipping %s: %s", d.name, exc)
    return flows


class Registrar:
    """Minimal IS-04 Registration API client (Node, Device, Source, Flow)."""

    def __init__(self, base_url: str, api_version: str, workload: str, domain: str):
        self.base = f"{base_url.rstrip('/')}/x-nmos/registration/{api_version}"
        self.workload = workload
        self.domain = domain
        # Stable across pod restarts: one IS-04 Node per workload Flow Domain.
        self.node_id = derived_id("node", workload, domain)
        self.device_id = derived_id("device", workload, domain)
        self.registered = {}  # flow id -> source id
        self.node_ok = False

    def _call(self, method: str, path: str, body=None) -> int:
        data = json.dumps(body).encode() if body is not None else None
        req = urllib.request.Request(self.base + path, data=data, method=method,
                                     headers={"Content-Type": "application/json"})
        try:
            with urllib.request.urlopen(req, timeout=5) as resp:
                return resp.status
        except urllib.error.HTTPError as exc:
            if exc.code >= 400 and exc.code != 404:
                log.warning("%s %s -> %s %s", method, path, exc.code, exc.read()[:300])
            return exc.code

    def _register(self, rtype: str, data: dict) -> bool:
        return self._call("POST", "/resource", {"type": rtype, "data": data}) in (200, 201)

    def _tags(self) -> dict:
        return {
            "urn:x-dmf:workload": [self.workload],
            "urn:x-dmf:mxl-domain": [self.domain],
            "urn:x-dmf:transport": ["mxl"],
        }

    def register_node(self) -> None:
        host = socket.gethostname()
        v = tai_version()
        node = {
            "id": self.node_id, "version": v, "label": f"{self.workload} MXL domain",
            "description": f"MXL Flow Domain {self.domain} of DMF workload {self.workload}",
            "tags": self._tags(), "href": f"http://{host}/", "hostname": host,
            "api": {"versions": ["v1.3"], "endpoints": []},
            "caps": {}, "services": [], "clocks": [{"name": "clk0", "ref_type": "internal"}],
            "interfaces": [],
        }
        device = {
            "id": self.device_id, "version": v, "label": f"{self.workload} media exchange",
            "description": "Shared-memory media exchange (MXL)", "tags": self._tags(),
            "type": "urn:x-nmos:device:generic", "node_id": self.node_id,
            "senders": [], "receivers": [], "controls": [],
        }
        self.node_ok = self._register("node", node) and self._register("device", device)
        if self.node_ok:
            self.registered.clear()  # registry may have lost everything; re-push flows
            log.info("registered node %s", self.node_id)

    def heartbeat(self) -> None:
        if not self.node_ok or self._call("POST", f"/health/nodes/{self.node_id}") == 404:
            self.register_node()

    def sync(self, flows: dict) -> None:
        if not self.node_ok:
            return
        for flow_id, fdef in flows.items():
            if flow_id in self.registered:
                continue
            source_id = derived_id("source", flow_id)
            fmt = fdef.get("format", "urn:x-nmos:format:data")
            v = tai_version()
            tags = {**fdef.get("tags", {}), **self._tags()}
            source = {
                "id": source_id, "version": v, "label": fdef.get("label", flow_id),
                "description": fdef.get("description", ""), "tags": tags, "caps": {},
                "device_id": self.device_id, "parents": [], "clock_name": "clk0", "format": fmt,
            }
            if fmt == "urn:x-nmos:format:audio":
                source["channels"] = [{"label": f"ch{i + 1}"} for i in range(int(fdef.get("channel_count", 2)))]
            flow = {**fdef, "id": flow_id, "version": v, "tags": tags, "caps": {},
                    "source_id": source_id, "device_id": self.device_id,
                    "parents": fdef.get("parents", []), "label": fdef.get("label", flow_id),
                    "description": fdef.get("description", "")}
            if self._register("source", source) and self._register("flow", flow):
                self.registered[flow_id] = source_id
                log.info("registered flow %s (%s)", flow_id, flow["label"])
        for flow_id in [f for f in self.registered if f not in flows]:
            self._call("DELETE", f"/resource/flows/{flow_id}")
            self._call("DELETE", f"/resource/sources/{self.registered.pop(flow_id)}")
            log.info("deregistered flow %s", flow_id)

    def shutdown(self) -> None:
        if self.node_ok:
            self._call("DELETE", f"/resource/nodes/{self.node_id}")


class Metrics:
    def __init__(self):
        self.lock = threading.Lock()
        self.text = ""

    def render(self, workload, domain, statuses, registry_up):
        lab = f'workload="{workload}",domain="{domain}"'
        out = [
            "# HELP dmf_mxl_domain_flows Number of flows present in the MXL domain.",
            "# TYPE dmf_mxl_domain_flows gauge",
            f"dmf_mxl_domain_flows{{{lab}}} {len(statuses)}",
            "# HELP dmf_nmos_registration_up 1 if the IS-04 node registration is healthy.",
            "# TYPE dmf_nmos_registration_up gauge",
            f"dmf_nmos_registration_up{{{lab}}} {registry_up}",
            "# HELP dmf_mxl_flow_info Static flow metadata.",
            "# TYPE dmf_mxl_flow_info gauge",
            "# HELP dmf_mxl_flow_active 1 if the flow has a live writer.",
            "# TYPE dmf_mxl_flow_active gauge",
            "# HELP dmf_mxl_flow_head_index Current head grain/sample index (rate() gives throughput).",
            "# TYPE dmf_mxl_flow_head_index counter",
            "# HELP dmf_mxl_flow_latency_grains Reader latency behind head, in grains.",
            "# TYPE dmf_mxl_flow_latency_grains gauge",
        ]
        for s in statuses:
            fl = f'{lab},flow_id="{s.flow_id}"'
            label = s.label.replace("\\", "\\\\").replace('"', '\\"')
            out.append(f'dmf_mxl_flow_info{{{fl},label="{label}",format="{s.fmt}",media_type="{s.media_type}"}} 1')
            if s.active is not None:
                out.append(f"dmf_mxl_flow_active{{{fl}}} {int(s.active)}")
            if s.head_index is not None:
                out.append(f"dmf_mxl_flow_head_index{{{fl}}} {s.head_index}")
            if s.latency_grains is not None:
                out.append(f"dmf_mxl_flow_latency_grains{{{fl}}} {s.latency_grains}")
        with self.lock:
            self.text = "\n".join(out) + "\n"

    def handler(self):
        metrics = self

        class H(BaseHTTPRequestHandler):
            def do_GET(self):
                if self.path not in ("/metrics", "/healthz"):
                    self.send_error(404)
                    return
                with metrics.lock:
                    body = (metrics.text if self.path == "/metrics" else "ok\n").encode()
                self.send_response(200)
                self.send_header("Content-Type", "text/plain; version=0.0.4")
                self.send_header("Content-Length", str(len(body)))
                self.end_headers()
                self.wfile.write(body)

            def log_message(self, *args):
                pass

        return H


def main() -> int:
    logging.basicConfig(level=os.environ.get("LOG_LEVEL", "INFO"),
                        format="%(asctime)s %(levelname)s %(name)s %(message)s")
    domain = os.environ.get("MXL_DOMAIN", "")
    workload = os.environ.get("WORKLOAD", "")
    if not domain or not workload:
        log.error("MXL_DOMAIN and WORKLOAD are required")
        return 2
    poll = float(os.environ.get("POLL_SECONDS", "5"))
    mxl_info = os.environ.get("MXL_INFO", "mxl-info")
    host, _, port = os.environ.get("METRICS_ADDR", "0.0.0.0:9102").rpartition(":")
    domain_name = Path(domain).name

    registry_url = os.environ.get("NMOS_REGISTRY_URL", "")
    registrar = (Registrar(registry_url, os.environ.get("NMOS_API_VERSION", "v1.3"), workload, domain_name)
                 if registry_url else None)

    metrics = Metrics()
    server = ThreadingHTTPServer((host, int(port)), metrics.handler())
    threading.Thread(target=server.serve_forever, daemon=True).start()
    log.info("watching %s, metrics on %s:%s, registry=%s", domain, host, port, registry_url or "disabled")

    def _term(*_):
        raise KeyboardInterrupt  # deregister from NMOS on pod termination

    signal.signal(signal.SIGTERM, _term)

    try:
        while True:
            flows = scan_domain(Path(domain))
            statuses = []
            for flow_id, fdef in flows.items():
                st = FlowStatus(flow_id, fdef.get("label", ""), fdef.get("format", ""), fdef.get("media_type", ""))
                probe_flow(mxl_info, domain, flow_id, st)
                statuses.append(st)
            if registrar:
                registrar.heartbeat()
                registrar.sync(flows)
            metrics.render(workload, domain_name, statuses, int(bool(registrar and registrar.node_ok)))
            time.sleep(poll)
    except KeyboardInterrupt:
        pass
    finally:
        if registrar:
            registrar.shutdown()
        server.shutdown()
    return 0


if __name__ == "__main__":
    sys.exit(main())
