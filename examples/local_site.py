"""Serve a deterministic local crawl target; no external services are needed."""

import argparse
import html
import time
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from urllib.parse import urlsplit


def make_handler(page_count: int, delay: float) -> type[BaseHTTPRequestHandler]:
    class Handler(BaseHTTPRequestHandler):
        def do_GET(self) -> None:
            path = urlsplit(self.path).path
            if path == "/robots.txt":
                self.send_body(
                    200, "text/plain; charset=utf-8", "User-agent: *\nDisallow: /private/\n"
                )
                return
            if path == "/redirect":
                self.send_response(302)
                self.send_header("Location", "/page/1")
                self.send_header("Content-Length", "0")
                self.end_headers()
                return
            if path.startswith("/private/"):
                self.send_body(403, "text/plain; charset=utf-8", "Robots prohibits this route.\n")
                return
            if path == "/":
                title = "Local collector fixture"
                links = [f"/page/{number}" for number in range(1, page_count + 1)]
                links.extend(["/page/1#duplicate", "/redirect"])
            elif path.startswith("/page/"):
                try:
                    number = int(path.removeprefix("/page/"))
                except ValueError:
                    self.send_body(404, "text/plain; charset=utf-8", "Not found\n")
                    return
                if not 1 <= number <= page_count:
                    self.send_body(404, "text/plain; charset=utf-8", "Not found\n")
                    return
                title = f"Fixture page {number}"
                links = ["/", f"/page/{(number % page_count) + 1}"]
            else:
                self.send_body(404, "text/plain; charset=utf-8", "Not found\n")
                return
            time.sleep(delay)
            anchors = "".join(
                f'<li><a href="{html.escape(link, quote=True)}">{html.escape(link)}</a></li>'
                for link in links
            )
            body = (
                '<!doctype html><html lang="en"><head><meta charset="utf-8">'
                f"<title>{html.escape(title)}</title>"
                '<meta name="description" content="Deterministic local metadata collection demo">'
                f'<link rel="canonical" href="{html.escape(path, quote=True)}">'
                f"</head><body><h1>{html.escape(title)}</h1><ul>{anchors}</ul></body></html>"
            )
            self.send_body(200, "text/html; charset=utf-8", body)

        def send_body(self, status: int, content_type: str, body: str) -> None:
            payload = body.encode("utf-8")
            self.send_response(status)
            self.send_header("Content-Type", content_type)
            self.send_header("Content-Length", str(len(payload)))
            self.end_headers()
            try:
                self.wfile.write(payload)
            except (BrokenPipeError, ConnectionResetError):
                pass

        def log_message(self, format: str, *args: object) -> None:
            return

    return Handler


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--host", default="127.0.0.1")
    parser.add_argument("--port", type=int, default=8080)
    parser.add_argument("--pages", type=int, default=12)
    parser.add_argument("--delay", type=float, default=0.15)
    args = parser.parse_args()
    if args.pages < 1 or args.delay < 0:
        parser.error("--pages must be positive and --delay must not be negative")
    handler = make_handler(args.pages, args.delay)
    with ThreadingHTTPServer((args.host, args.port), handler) as server:
        print(f"Fixture running at http://{args.host}:{server.server_port}/", flush=True)
        print("Press Ctrl+C to stop.", flush=True)
        try:
            server.serve_forever()
        except KeyboardInterrupt:
            pass


if __name__ == "__main__":
    main()
