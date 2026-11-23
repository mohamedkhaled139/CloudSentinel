import importlib.util
import json
import threading
from http.server import BaseHTTPRequestHandler, HTTPServer
from pathlib import Path

from cloudsentinel.schemas import AgentHeartbeat

AGENT = Path(__file__).resolve().parents[2] / "agent" / "cloudsentinel_agent.py"


def load_agent():
    spec = importlib.util.spec_from_file_location("cloudsentinel_agent", AGENT)
    mod = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(mod)
    return mod


def test_collect_matches_server_schema():
    agent = load_agent()
    payload = agent.collect()
    hb = AgentHeartbeat.model_validate(payload)  # the server must accept what the agent sends
    assert hb.cpu_count and hb.cpu_count >= 1
    assert hb.boot_time is not None


def test_send_once_posts_with_token():
    agent = load_agent()
    received = {}

    class Handler(BaseHTTPRequestHandler):
        def do_POST(self):
            received["path"] = self.path
            received["auth"] = self.headers["Authorization"]
            received["body"] = json.loads(self.rfile.read(int(self.headers["Content-Length"])))
            self.send_response(202)
            self.end_headers()

        def log_message(self, *a):
            pass

    server = HTTPServer(("127.0.0.1", 0), Handler)
    threading.Thread(target=server.handle_request, daemon=True).start()
    rc = agent.main(["--url", f"http://127.0.0.1:{server.server_port}", "--token", "csa_test", "--once"])
    server.server_close()
    assert rc == 0
    assert received["path"] == "/api/v1/agent/heartbeat"
    assert received["auth"] == "Bearer csa_test"
    assert "filesystems" in received["body"]
