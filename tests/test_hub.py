"""Downloads against a local server that honours Range requests, like Hugging Face's."""

import json
import threading
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer

import pytest

from ogmios import hub

CONTENT = bytes(range(256)) * 100


SHA = "0123456789abcdef0123456789abcdef01234567"


class Handler(BaseHTTPRequestHandler):
    def do_GET(self):
        if self.path.startswith("/api/models/"):
            found = self.path == "/api/models/org/repo/revision/main"
            body = json.dumps({"sha": SHA}).encode()
            self.send_response(200 if found else 404)
            self.end_headers()
            if found:
                self.wfile.write(body)
            return
        start = int(self.headers.get("Range", "bytes=0-")[6:].rstrip("-") or 0)
        if start >= len(CONTENT):
            self.send_response(416)
            self.end_headers()
            return
        self.send_response(206 if start else 200)
        self.send_header("Content-Length", str(len(CONTENT) - start))
        self.end_headers()
        self.wfile.write(CONTENT[start:])
        self.server.starts.append(start)  # ty: ignore[unresolved-attribute]

    def do_HEAD(self):
        self.send_response(200)
        self.send_header("Content-Length", str(len(CONTENT)))
        self.end_headers()

    def log_message(self, format, *args):
        pass


@pytest.fixture
def server(monkeypatch):
    httpd = ThreadingHTTPServer(("127.0.0.1", 0), Handler)
    httpd.starts = []  # ty: ignore[unresolved-attribute]
    threading.Thread(target=httpd.serve_forever, daemon=True).start()
    monkeypatch.setattr(hub, "HUB", f"http://127.0.0.1:{httpd.server_port}")
    yield httpd
    httpd.shutdown()


def test_download_resumes_a_partial_file(server, tmp_path):
    (tmp_path / "model.bin.part").write_bytes(CONTENT[:1000])
    path = hub.download("org/repo", "rev", "model.bin", tmp_path)
    assert path.read_bytes() == CONTENT
    assert server.starts == [1000]
    assert not (tmp_path / "model.bin.part").exists()


def test_download_finishes_a_complete_partial_file(server, tmp_path):
    (tmp_path / "model.bin.part").write_bytes(CONTENT)
    assert (
        hub.download("org/repo", "rev", "model.bin", tmp_path).read_bytes() == CONTENT
    )


def test_remote_size(server):
    assert hub.remote_size("org/repo", "rev", "model.bin") == len(CONTENT)


def test_latest_revision(server):
    assert hub.latest_revision("org/repo") == SHA
    with pytest.raises(ValueError, match="no public repository org/missing"):
        hub.latest_revision("org/missing")


def test_missing_model_points_at_install(tmp_path):
    with pytest.raises(
        ValueError, match="LLM x is not installed, run `ogmios install`"
    ):
        hub.installed(tmp_path / "model.gguf", "LLM x")
