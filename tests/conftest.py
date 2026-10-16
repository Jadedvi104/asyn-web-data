from __future__ import annotations

import threading
import time
from collections import Counter, defaultdict
from dataclasses import dataclass, field
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path
from urllib.parse import urlsplit

import pytest


@dataclass
class LocalSite:
    """A controlled HTTP origin; all integration traffic stays on this machine."""

    base_url: str = ""
    counts: Counter[str] = field(default_factory=Counter)
    starts: dict[str, list[float]] = field(default_factory=lambda: defaultdict(list))
    active: int = 0
    maximum_active: int = 0
    disconnected_streams: int = 0
    lock: threading.Lock = field(default_factory=threading.Lock)

    def url(self, path: str = "/") -> str:
        return self.base_url + path

    def count(self, path: str) -> int:
        with self.lock:
            return self.counts[path]

    def snapshot(self) -> dict:
        with self.lock:
            return {
                "counts": dict(self.counts),
                "starts": {path: list(values) for path, values in self.starts.items()},
                "active": self.active,
                "maximum_active": self.maximum_active,
                "disconnected_streams": self.disconnected_streams,
            }


@pytest.fixture
def local_site():
    site = LocalSite()
    page_template = (Path(__file__).parent / "fixtures" / "page.html").read_bytes()

    class Handler(BaseHTTPRequestHandler):
        protocol_version = "HTTP/1.1"

        def log_message(self, *_args):
            pass

        def do_GET(self):
            path = urlsplit(self.path).path
            with site.lock:
                site.counts[path] += 1
                site.starts[path].append(time.monotonic())
                site.active += 1
                site.maximum_active = max(site.maximum_active, site.active)
                attempt = site.counts[path]
            try:
                self.respond(path, attempt)
            except (BrokenPipeError, ConnectionResetError, ConnectionAbortedError):
                if path.startswith("/stream/"):
                    with site.lock:
                        site.disconnected_streams += 1
            finally:
                with site.lock:
                    site.active -= 1

        def send_body(self, body, status=200, content_type="text/html", headers=None):
            if isinstance(body, str):
                body = body.encode("utf-8")
            self.send_response(status)
            self.send_header("Content-Type", content_type)
            self.send_header("Content-Length", str(len(body)))
            self.send_header("Connection", "close")
            for name, value in (headers or {}).items():
                self.send_header(name, value)
            self.end_headers()
            self.wfile.write(body)
            self.wfile.flush()

        def respond(self, path, attempt):
            if path == "/robots.txt":
                self.send_body("User-agent: *\nDisallow: /blocked\n", content_type="text/plain")
            elif path in {"/", "/linked"}:
                external = site.base_url.replace("127.0.0.1", "localhost")
                self.send_body(
                    '<title>Fixture index</title>'
                    '<a href="/page/1">one</a><a href="/page/1#again">duplicate</a>'
                    '<a href="/page/2">two</a><a href="/blocked">denied</a>'
                    f'<a href="{external}/outside">external</a>'
                    '<a href="/redirect-external">redirect</a>'
                )
            elif path == "/depth" or path.startswith("/depth/"):
                graph = {
                    "/depth": ["/depth/a", "/depth/b"],
                    "/depth/a": ["/depth/x"],
                    "/depth/b": ["/depth/c"],
                    "/depth/c": ["/depth/x"],
                    "/depth/x": ["/depth/y"],
                }
                if path == "/depth/a":
                    time.sleep(0.2)
                self.send_body(
                    "<title>Depth graph</title>"
                    + "".join(f'<a href="{link}">next</a>' for link in graph.get(path, []))
                )
            elif path == "/fanout":
                self.send_body(
                    "<title>Fanout</title>"
                    + "".join(f'<a href="/slow/{n}">{n}</a>' for n in range(8))
                )
            elif path.startswith("/slow/"):
                time.sleep(0.12)
                self.send_body(f"<title>Slow {path.rsplit('/', 1)[-1]}</title>")
            elif path.startswith("/stream/"):
                chunks = 40
                chunk = b" " * 1024
                prefix = b"<html><title>Streaming page</title><body>"
                self.send_response(200)
                self.send_header("Content-Type", "text/html")
                self.send_header("Content-Length", str(len(prefix) + chunks * len(chunk)))
                self.send_header("Connection", "close")
                self.end_headers()
                self.wfile.write(prefix)
                self.wfile.flush()
                for _ in range(chunks):
                    time.sleep(0.025)
                    self.wfile.write(chunk)
                    self.wfile.flush()
            elif path == "/retry":
                self.send_body("try again" if attempt < 3 else "<title>Recovered</title>",
                               status=503 if attempt < 3 else 200)
            elif path == "/always-fails":
                self.send_body("unavailable", status=503)
            elif path == "/timeout":
                time.sleep(0.6)
                self.send_body("<title>Too late</title>")
            elif path == "/oversized":
                self.send_body(b"<html>" + b"x" * (64 * 1024) + b"</html>")
            elif path == "/redirect-blocked":
                self.send_body(b"", status=302, headers={"Location": "/blocked"})
            elif path == "/redirect-external":
                self.send_body(b"", status=302, headers={
                    "Location": site.base_url.replace("127.0.0.1", "localhost") + "/outside"
                })
            elif path == "/chain/0":
                self.send_body('<title>Depth zero</title><a href="/chain/1">next</a>')
            elif path == "/chain/1":
                self.send_body('<title>Depth one</title><a href="/chain/2">next</a>')
            elif path.startswith("/page/") or path in {"/fast", "/outside", "/blocked", "/chain/2"}:
                self.send_body(page_template)
            else:
                self.send_body("missing", status=404)

    server = ThreadingHTTPServer(("127.0.0.1", 0), Handler)
    server.daemon_threads = True
    site.base_url = f"http://127.0.0.1:{server.server_port}"
    thread = threading.Thread(target=server.serve_forever, daemon=True)
    thread.start()
    try:
        yield site
    finally:
        server.shutdown()
        server.server_close()
        thread.join(timeout=2)
