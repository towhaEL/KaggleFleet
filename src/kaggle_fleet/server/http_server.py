"""
Zero-dependency ThreadingHTTPServer with static routing and graceful shutdown.

Security model: the panel has no login, so the API must only be reachable by
the panel itself. Every /api request is checked for:
  * a Host header naming this server (blocks DNS-rebinding attacks), and
  * an Origin header, if present, equal to that host (blocks other websites).
State-changing requests with a body must also be `application/json`, which a
cross-site HTML form cannot send. No CORS headers are emitted.
"""

from __future__ import annotations

import json
import mimetypes
import signal
import sys
import threading
import webbrowser
from http import HTTPStatus
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path
from typing import Any
from urllib.parse import urlparse

from .api_handlers import ApiDispatcher
from ..account_manager import AccountManager
from ..notebook_manager import NotebookManager
from ..kaggle_runner import KaggleRunner
from ..storage import StorageEngine


STATIC_DIR = Path(__file__).parent / "static"


class KaggleFleetRequestHandler(BaseHTTPRequestHandler):
    """Handles HTTP requests for KaggleFleet REST API and Static UI."""

    # Injected by KaggleFleetServer
    dispatcher: ApiDispatcher
    allowed_hosts: frozenset[str] = frozenset({"localhost", "127.0.0.1", "::1"})
    any_host: bool = False          # True when bound to 0.0.0.0 / :: on purpose

    server_version = "KaggleFleet"
    sys_version = ""

    def log_message(self, format: str, *args: Any) -> None:
        """Override to provide quiet or formatted terminal logging."""
        # Suppress noise during test execution or normal static asset polling
        if "GET /static/" in (args[0] if args else ""):
            return
        # sys.stdout.write(f"[HTTP] {self.address_string()} - {format % args}\n")

    def _send_security_headers(self) -> None:
        self.send_header("X-Content-Type-Options", "nosniff")
        self.send_header("X-Frame-Options", "DENY")
        self.send_header("Referrer-Policy", "no-referrer")

    @staticmethod
    def _hostname(netloc: str) -> str:
        """'host:port' / '[::1]:port' -> 'host' / '::1' (lower-cased)."""
        return (urlparse(f"//{netloc}").hostname or "").lower()

    def _api_request_allowed(self, method: str) -> bool:
        """Reject cross-site and DNS-rebinding requests. Sends the error itself."""
        host_header = self.headers.get("Host", "")
        if not self.any_host and self._hostname(host_header) not in self.allowed_hosts:
            self._send_json(403, {"error": "Forbidden: unexpected Host header"})
            return False
        origin = self.headers.get("Origin")
        if origin and origin != "null" and urlparse(origin).netloc.lower() != host_header.lower():
            self._send_json(403, {"error": "Forbidden: cross-origin request"})
            return False
        if origin == "null":
            self._send_json(403, {"error": "Forbidden: opaque origin"})
            return False
        if method in ("POST", "DELETE") and int(self.headers.get("Content-Length") or 0) > 0:
            ctype = (self.headers.get("Content-Type") or "").split(";")[0].strip().lower()
            if ctype != "application/json":
                self._send_json(415, {"error": "Content-Type must be application/json"})
                return False
        return True

    def do_OPTIONS(self) -> None:
        """No cross-origin access is offered, so preflights are refused."""
        self._send_json(405, {"error": "Method Not Allowed"})

    def do_GET(self) -> None:
        parsed_url = urlparse(self.path)
        path = parsed_url.path
        query = parsed_url.query

        # Check API routes
        if path.startswith("/api/"):
            if not self._api_request_allowed("GET"):
                return
            status_code, response_data = self.dispatcher.dispatch("GET", path, query_string=query)
            self._send_json(status_code, response_data)
            return

        # Check Static assets
        self._serve_static(path)

    def do_POST(self) -> None:
        parsed_url = urlparse(self.path)
        path = parsed_url.path
        query = parsed_url.query

        if not path.startswith("/api/"):
            self._send_json(404, {"error": "Not Found"})
            return
        if not self._api_request_allowed("POST"):
            return

        content_length = int(self.headers.get("Content-Length") or 0)
        body_data: Any = {}
        if content_length > 0:
            try:
                body_data = json.loads(self.rfile.read(content_length).decode("utf-8"))
            except (UnicodeDecodeError, json.JSONDecodeError):
                self._send_json(400, {"error": "Request body is not valid JSON"})
                return
        if not isinstance(body_data, dict):
            self._send_json(400, {"error": "Request body must be a JSON object"})
            return

        if path.startswith("/api/"):
            status_code, response_data = self.dispatcher.dispatch("POST", path, query_string=query, body_data=body_data)
            self._send_json(status_code, response_data)
            return

        self._send_json(404, {"error": "Not Found"})

    def do_DELETE(self) -> None:
        parsed_url = urlparse(self.path)
        path = parsed_url.path
        query = parsed_url.query

        if path.startswith("/api/"):
            if not self._api_request_allowed("DELETE"):
                return
            status_code, response_data = self.dispatcher.dispatch("DELETE", path, query_string=query)
            self._send_json(status_code, response_data)
            return

        self._send_json(404, {"error": "Not Found"})

    def _send_json(self, status_code: int, data: Any) -> None:
        body = json.dumps(data, indent=2).encode("utf-8")
        self.send_response(status_code)
        self._send_security_headers()
        self.send_header("Cache-Control", "no-store")
        self.send_header("Content-Type", "application/json; charset=utf-8")
        self.send_header("Content-Length", str(len(body)))
        self.end_headers()
        self.wfile.write(body)

    def _serve_static(self, req_path: str) -> None:
        # Normalize request path
        if req_path in ["", "/", "/index.html"]:
            file_path = STATIC_DIR / "index.html"
        elif req_path.startswith("/static/"):
            rel = req_path[len("/static/"):].lstrip("/")
            file_path = (STATIC_DIR / rel).resolve()
        elif req_path in ["/app.js", "/styles.css"]:
            file_path = (STATIC_DIR / req_path.lstrip("/")).resolve()
        else:
            file_path = (STATIC_DIR / req_path.lstrip("/")).resolve()

        # Prevent directory traversal
        try:
            file_path.relative_to(STATIC_DIR.resolve())
        except ValueError:
            self._send_json(403, {"error": "Forbidden"})
            return

        if not file_path.is_file():
            self._send_json(404, {"error": f"File not found: {req_path}"})
            return

        content_type, _ = mimetypes.guess_type(str(file_path))
        if not content_type:
            if file_path.suffix == ".css":
                content_type = "text/css"
            elif file_path.suffix == ".js":
                content_type = "application/javascript"
            else:
                content_type = "text/plain"

        try:
            with open(file_path, "rb") as f:
                content = f.read()

            self.send_response(HTTPStatus.OK)
            self._send_security_headers()
            self.send_header("Content-Type", f"{content_type}; charset=utf-8")
            self.send_header("Content-Length", str(len(content)))
            self.send_header("Cache-Control", "no-cache")
            self.end_headers()
            self.wfile.write(content)
        except Exception as e:
            self._send_json(500, {"error": f"Failed to read file: {e}"})


class KaggleFleetServer:
    """Zero-dependency ThreadingHTTPServer wrapper with lifecycle control."""

    def __init__(
        self,
        host: str = "127.0.0.1",
        port: int = 8080,
        account_manager: AccountManager | None = None,
        notebook_manager: NotebookManager | None = None,
        runner: KaggleRunner | None = None,
        storage: StorageEngine | None = None,
        notebooks_dir: str | Path | None = None,
        outputs_dir: str | Path | None = None,
        allowed_hosts: list[str] | None = None,
    ) -> None:
        self.host = host
        self.port = port
        self.storage = storage or StorageEngine()
        self.account_manager = account_manager or AccountManager(storage=self.storage)
        self.notebook_manager = notebook_manager or NotebookManager(
            notebooks_dir=Path(notebooks_dir) if notebooks_dir else None)
        self.runner = runner or KaggleRunner(
            account_manager=self.account_manager,
            notebook_manager=self.notebook_manager,
            storage=self.storage,
            outputs_dir=outputs_dir,
        )

        self.dispatcher = ApiDispatcher(
            account_manager=self.account_manager,
            notebook_manager=self.notebook_manager,
            runner=self.runner,
            storage=self.storage,
        )

        # Create handler class with injected dispatcher
        class Handler(KaggleFleetRequestHandler):
            pass

        Handler.dispatcher = self.dispatcher
        hosts = {"localhost", "127.0.0.1", "::1", host.lower()}
        hosts.update(h.strip().lower() for h in (allowed_hosts or []) if h.strip())
        Handler.allowed_hosts = frozenset(hosts)
        Handler.any_host = host in ("0.0.0.0", "::", "")
        self.is_exposed = host not in ("127.0.0.1", "localhost", "::1")

        self.httpd = ThreadingHTTPServer((self.host, self.port), Handler)
        # In case port was 0, retrieve bound port
        self.server_port = self.httpd.server_port
        self._server_thread: threading.Thread | None = None
        self._is_running = False

    def start_background(self) -> None:
        """Starts server in a background daemon thread (useful for testing & embeddings)."""
        if self._is_running:
            return
        self._is_running = True
        self._server_thread = threading.Thread(target=self.httpd.serve_forever, daemon=True)
        self._server_thread.start()

    def serve_forever(self) -> None:
        """Starts server blocking current thread."""
        self._is_running = True
        try:
            self.httpd.serve_forever()
        except KeyboardInterrupt:
            pass
        finally:
            self.shutdown()

    def shutdown(self) -> None:
        """Gracefully stop server."""
        if self._is_running:
            self._is_running = False
            self.httpd.shutdown()
            self.httpd.server_close()
            if self._server_thread and self._server_thread.is_alive():
                self._server_thread.join(timeout=2)


def run_server(
    host: str = "127.0.0.1",
    port: int = 8080,
    open_browser: bool = False,
    notebooks_dir: str | Path | None = None,
    outputs_dir: str | Path | None = None,
    allowed_hosts: list[str] | None = None,
) -> int:
    """CLI runner helper for serving the web dashboard."""
    server = KaggleFleetServer(host=host, port=port, notebooks_dir=notebooks_dir,
                               outputs_dir=outputs_dir, allowed_hosts=allowed_hosts)
    shown_host = "127.0.0.1" if host in ("0.0.0.0", "::", "") else host
    url = f"http://{shown_host}:{server.server_port}"
    print(f"\n KaggleFleet web panel is running at: {url}")
    print(f" Notebooks: {server.notebook_manager.notebooks_dir}")
    print(f" Outputs:   {server.runner.outputs_dir}")
    if server.is_exposed:
        print("\n WARNING: the panel has no authentication and is listening on a non-loopback")
        print(" address. Anyone who can reach this port can use every configured Kaggle")
        print(" account. Prefer an SSH tunnel: ssh -L 8080:127.0.0.1:8080 <host>")
    print(" Press Ctrl+C to stop the server.\n")

    if open_browser:
        threading.Timer(0.5, lambda: webbrowser.open(url)).start()

    def sig_handler(sig: Any, frame: Any) -> None:
        print("\nShutting down KaggleFleet server...")
        server.shutdown()
        sys.exit(0)

    signal.signal(signal.SIGINT, sig_handler)
    signal.signal(signal.SIGTERM, sig_handler)

    server.serve_forever()
    return 0
