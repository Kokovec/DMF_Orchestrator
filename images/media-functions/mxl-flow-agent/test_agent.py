"""mxl-flow-agent's NMOS registration when the registry can't be reached (stdlib unittest; run by
scripts/validate.sh). Right after a facility start the registry accepts connections but doesn't
answer yet, which used to raise TimeoutError out of the main loop and crash-loop the pod."""
import importlib.util
import logging
import socket
import threading
import unittest
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path

_spec = importlib.util.spec_from_file_location("agent", Path(__file__).with_name("agent.py"))
agent = importlib.util.module_from_spec(_spec)
_spec.loader.exec_module(agent)
logging.disable(logging.CRITICAL)


def registrar(url):
    r = agent.Registrar(url, "v1.3", "w", "d")
    r.timeout = 0.2
    return r


class Silent:
    """Accepts connections and never answers: the timeout seen after a facility start."""

    def __init__(self):
        self.sock = socket.create_server(("127.0.0.1", 0))
        self.url = f"http://127.0.0.1:{self.sock.getsockname()[1]}"

    def close(self):
        self.sock.close()


class Registry:
    def __init__(self):
        class H(BaseHTTPRequestHandler):
            def log_message(self, *a):
                pass

            def do_POST(self):
                self.rfile.read(int(self.headers.get("Content-Length") or 0))
                self.send_response(201)
                self.send_header("Content-Length", "0")
                self.end_headers()

        self.server = ThreadingHTTPServer(("127.0.0.1", 0), H)
        threading.Thread(target=self.server.serve_forever, daemon=True).start()
        self.url = f"http://127.0.0.1:{self.server.server_address[1]}"

    def close(self):
        self.server.shutdown()
        self.server.server_close()


class UnreachableRegistry(unittest.TestCase):
    def test_timeout_is_not_raised(self):
        s = Silent()
        self.addCleanup(s.close)
        r = registrar(s.url)
        self.assertEqual(r._call("POST", "/resource", {}), 0)
        r.heartbeat()                        # register_node, which is where it crashed
        self.assertFalse(r.node_ok)

    def test_refused_connection_is_not_raised(self):
        s = Silent()
        url = s.url
        s.close()
        r = registrar(url)
        r.register_node()
        r.sync({"f": {"label": "x"}})
        self.assertFalse(r.node_ok)

    def test_timeout_after_registering_keeps_the_node(self):
        s = Silent()
        self.addCleanup(s.close)
        r = registrar(s.url)
        r.node_ok = True
        r.heartbeat()
        self.assertTrue(r.node_ok, "a missed heartbeat is not a lost registration")

    def test_registers_once_the_registry_answers(self):
        s = Silent()
        self.addCleanup(s.close)
        r = registrar(s.url)
        r.heartbeat()
        self.assertFalse(r.node_ok)
        reg = Registry()
        self.addCleanup(reg.close)
        r.base = reg.url + "/x-nmos/registration/v1.3"
        r.heartbeat()
        self.assertTrue(r.node_ok)



class Answering:
    """A registry that gives one status for flows and 201 for everything else."""

    def __init__(self, flow_status):
        outer = self
        self.flow_status = flow_status

        class H(BaseHTTPRequestHandler):
            def log_message(self, *a):
                pass

            def do_POST(self):
                body = self.rfile.read(int(self.headers.get("Content-Length") or 0))
                status = outer.flow_status if b'"type": "flow"' in body else 201
                self.send_response(status)
                self.send_header("Content-Length", "0")
                self.end_headers()

        self.server = ThreadingHTTPServer(("127.0.0.1", 0), H)
        threading.Thread(target=self.server.serve_forever, daemon=True).start()
        self.url = f"http://127.0.0.1:{self.server.server_address[1]}"

    def close(self):
        self.server.shutdown()
        self.server.server_close()


class RegistrationMetric(unittest.TestCase):
    def test_a_rejected_flow_means_registration_is_down_until_it_succeeds(self):
        reg = Answering(400)
        self.addCleanup(reg.close)
        r = registrar(reg.url)
        r.heartbeat()
        self.assertTrue(r.registration_up, "node and device registered, no flows yet")
        flows = {"f1": {"label": "x"}}
        r.sync(flows)
        self.assertTrue(r.node_ok)
        self.assertFalse(r.registration_up)
        reg.flow_status = 201
        r.sync(flows)
        self.assertTrue(r.registration_up)

    def test_a_flow_that_went_away_no_longer_counts(self):
        reg = Answering(400)
        self.addCleanup(reg.close)
        r = registrar(reg.url)
        r.heartbeat()
        r.sync({"f1": {"label": "x"}})
        self.assertFalse(r.registration_up)
        r.sync({})
        self.assertTrue(r.registration_up)

    def test_a_rejected_node_means_registration_is_down(self):
        s = Silent()
        self.addCleanup(s.close)
        r = registrar(s.url)
        r.heartbeat()
        self.assertFalse(r.registration_up)


if __name__ == "__main__":
    unittest.main()
