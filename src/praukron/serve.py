"""`praukron dashboard --serve`: the dashboard, able to record the owner's answers.

A foreground, single-threaded standard-library server bound to 127.0.0.1
(ADR-055). It serves the same page as the static dashboard with the review
forms enabled, and it has one write route, which calls `praukron respond`.
There is no database and no second state store: an answer lands in
RESPONSES.md or nowhere. Every request must carry the per-run token, and a
write from another origin is refused.
"""

from __future__ import annotations

import json
import secrets
import sys
from http.server import BaseHTTPRequestHandler, HTTPServer
from pathlib import Path
from urllib.parse import parse_qs, urlparse

from . import analytics, compile as compiler, dashboard, respond

HOST = "127.0.0.1"
_MAX_BODY = 1_000_000


def page(root: Path) -> str:
    project = compiler.load(root)
    try:
        reviewer = respond.identity()
    except respond.RespondError:
        reviewer = ""
    return dashboard.render(project, analytics.report(project), compiler.as_json(project), interactive=True,
                            reviewer=reviewer)


def make_server(root: Path, port: int = 0) -> tuple[HTTPServer, str, str]:
    token = secrets.token_urlsafe(24)

    class Handler(BaseHTTPRequestHandler):
        server_version = "praukron"

        def log_message(self, format: str, *args) -> None:  # noqa: A002 - stdlib signature
            return

        def _send(self, status: int, body: str, kind: str = "application/json") -> None:
            data = body.encode()
            self.send_response(status)
            self.send_header("Content-Type", f"{kind}; charset=utf-8")
            self.send_header("Content-Length", str(len(data)))
            self.send_header("Cache-Control", "no-store")
            self.send_header("X-Content-Type-Options", "nosniff")
            self.end_headers()
            self.wfile.write(data)

        def _token_ok(self) -> bool:
            query = parse_qs(urlparse(self.path).query)
            given = self.headers.get("X-Praukron-Token") or (query.get("token") or [""])[0]
            return secrets.compare_digest(given, token)

        def do_GET(self) -> None:  # noqa: N802 - stdlib name
            if urlparse(self.path).path != "/" or not self._token_ok():
                # A person who typed the address without its token should be
                # told what to open, not handed a JSON error.
                self._send(403, (
                    "<!doctype html><meta charset=utf-8><title>Praukron: link needed</title>"
                    "<body style='font:16px system-ui;margin:3em'><h1>Open the full link</h1>"
                    "<p>This review page needs the link printed by <code>praukron dashboard --serve</code>, "
                    "including its <code>?token=…</code> part. The token keeps other pages on this "
                    "machine from writing your answers.</p></body>"), "text/html")
                return
            self._send(200, page(root), "text/html")

        def do_POST(self) -> None:  # noqa: N802 - stdlib name
            origin = self.headers.get("Origin")
            own = f"http://{HOST}:{self.server.server_address[1]}"
            length = int(self.headers.get("Content-Length") or 0)
            if (urlparse(self.path).path != "/respond" or not self._token_ok()
                    or (origin is not None and origin != own)
                    or not (self.headers.get("Content-Type") or "").startswith("application/json")):
                # Read the unread body first: closing a socket with unread data sends a
                # reset on Windows, and the client sees an aborted connection, not the 403.
                if 0 < length <= _MAX_BODY:
                    self.rfile.read(length)
                self._send(403, json.dumps({"ok": False, "error": "refused"}))
                return
            if length > _MAX_BODY:
                self._send(413, json.dumps({"ok": False, "error": "too large"}))
                return
            try:
                body = json.loads(self.rfile.read(length) or b"{}")
                items = [respond.Item(i["target"], i["action"], i["text"]) for i in body["items"]]
                ids = respond.append(root, items, via="dashboard", verify=True)
            except (ValueError, KeyError, TypeError) as error:
                self._send(400, json.dumps({"ok": False, "error": f"malformed request: {error}"}))
                return
            except respond.RespondError as error:
                self._send(400, json.dumps({"ok": False, "error": str(error)}))
                return
            self._send(200, json.dumps({"ok": True, "ids": ids}))

    server = HTTPServer((HOST, port), Handler)
    url = f"http://{HOST}:{server.server_address[1]}/?token={token}"
    return server, token, url


def run(root: Path, port: int = 0, open_browser: bool = False) -> int:
    server, _, url = make_server(root, port)
    print(f"Serving the dashboard at {url}")
    print("Answers are written to RESPONSES.md through `praukron respond`. Press Ctrl-C to stop.")
    sys.stdout.flush()
    if open_browser:
        import webbrowser

        webbrowser.open(url)
    try:
        server.serve_forever()
    except KeyboardInterrupt:
        print("\nStopped.")
    finally:
        server.server_close()
    return 0


__all__ = ["HOST", "make_server", "page", "run"]
