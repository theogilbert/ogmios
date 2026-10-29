"""Downloads against a local server that honours Range requests, like Hugging Face's."""

import threading
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer

import pytest

from ogmios import hub

CONTENT = bytes(range(256)) * 100


class Handler(BaseHTTPRequestHandler):
    def do_GET(self):
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
    base = f"http://127.0.0.1:{httpd.server_port}"
    monkeypatch.setattr(hub, "url", lambda repo, rev, name: f"{base}/{name}")
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


def test_missing_model_points_at_setup(tmp_path):
    with pytest.raises(ValueError, match="LLM x is not installed, run `ogmios setup`"):
        hub.installed(tmp_path / "model.gguf", "LLM x")
