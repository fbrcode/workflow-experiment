"""Shared plumbing for the local mock services.

Every mock exposes the same control API next to its fake upstream API:

    GET    /_mock/health   -> {"ok": true, "service": ...}
    GET    /_mock/calls    -> recorded upstream calls, oldest first
    DELETE /_mock/calls    -> forget recorded calls
    POST   /_mock/faults   -> queue a fault: {"when": {...}, "status": 500, "count": 1,
                              "description": "...", "delay_ms": 0}
    GET    /_mock/faults   -> queued faults
    DELETE /_mock/faults   -> clear queued faults
    POST   /_mock/reset    -> clear calls and faults

A fault's ``when`` dict is matched (string equality) against attributes of the incoming
upstream request, for example ``{"method": "sendMessage"}`` or ``{"model": "gemini-2.5-flash"}``.
An empty ``when`` matches every request. ``count: -1`` keeps the fault forever.
"""

from __future__ import annotations

import email.parser
import email.policy
import json
import socketserver
import sys
import threading
import time
from dataclasses import asdict, dataclass, field
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from typing import Any
from urllib.parse import parse_qsl, urlsplit

JSON = dict[str, Any]


@dataclass
class Fault:
    status: int
    when: dict[str, str] = field(default_factory=dict)
    count: int = 1
    description: str | None = None
    delay_ms: int = 0

    def matches(self, attrs: dict[str, str]) -> bool:
        return all(str(attrs.get(k)) == str(v) for k, v in self.when.items())


class MockState:
    """Thread-safe call log and fault queue."""

    def __init__(self) -> None:
        self._lock = threading.Lock()
        self._calls: list[JSON] = []
        self._faults: list[Fault] = []
        self._seq = 0

    def record(self, entry: JSON) -> JSON:
        with self._lock:
            self._seq += 1
            entry = {"id": self._seq, "ts": time.time(), **entry}
            self._calls.append(entry)
            return entry

    def calls(self) -> list[JSON]:
        with self._lock:
            return list(self._calls)

    def clear_calls(self) -> None:
        with self._lock:
            self._calls.clear()

    def add_fault(self, fault: Fault) -> None:
        with self._lock:
            self._faults.append(fault)

    def faults(self) -> list[JSON]:
        with self._lock:
            return [asdict(f) for f in self._faults]

    def clear_faults(self) -> None:
        with self._lock:
            self._faults.clear()

    def take_fault(self, attrs: dict[str, str]) -> Fault | None:
        """Return the first queued fault matching ``attrs`` and consume one use of it."""
        with self._lock:
            for fault in self._faults:
                if fault.count != 0 and fault.matches(attrs):
                    if fault.count > 0:
                        fault.count -= 1
                    self._faults = [f for f in self._faults if f.count != 0]
                    return fault
            return None


class Request:
    """Parsed incoming request: path, query, headers and a flat params dict from the body."""

    def __init__(self, handler: BaseHTTPRequestHandler) -> None:
        split = urlsplit(handler.path)
        self.method = handler.command
        self.path = split.path
        self.query: dict[str, str] = dict(parse_qsl(split.query, keep_blank_values=True))
        self.headers = handler.headers
        length = int(handler.headers.get("Content-Length") or 0)
        self.raw = handler.rfile.read(length) if length else b""
        self.content_type = (handler.headers.get("Content-Type") or "").split(";")[0].strip().lower()

    def json(self) -> Any:
        return json.loads(self.raw or b"null")

    def params(self) -> dict[str, Any]:
        """Telegram-style parameters: query string, then JSON, form or multipart body."""
        params: dict[str, Any] = dict(self.query)
        if not self.raw:
            return params
        if self.content_type == "application/json":
            body = self.json()
            if isinstance(body, dict):
                params.update(body)
        elif self.content_type == "application/x-www-form-urlencoded":
            params.update(parse_qsl(self.raw.decode("utf-8"), keep_blank_values=True))
        elif self.content_type == "multipart/form-data":
            header = f"Content-Type: {self.headers.get('Content-Type')}\r\n\r\n".encode()
            message = email.parser.BytesParser(policy=email.policy.HTTP).parsebytes(header + self.raw)
            for part in message.iter_parts():
                name = part.get_param("name", header="content-disposition")
                if name:
                    payload = part.get_payload(decode=True) or b""
                    params[str(name)] = payload.decode(part.get_content_charset() or "utf-8")
        return params


class MockHandler(BaseHTTPRequestHandler):
    """Base handler. Subclasses set ``service`` and ``state`` and implement ``handle_upstream``."""

    service = "mock"
    state: MockState
    protocol_version = "HTTP/1.1"

    # --- plumbing -------------------------------------------------------------------------
    def log_message(self, format: str, *args: Any) -> None:  # noqa: A002 - stdlib signature
        pass  # replaced by structured logging in _log()

    def _log(self, status: int, request: Request, extra: JSON | None = None) -> None:
        line = {"service": self.service, "method": request.method, "path": request.path, "status": status}
        if extra:
            line.update(extra)
        print(json.dumps(line, ensure_ascii=False), file=sys.stdout, flush=True)

    def send_json(self, status: int, body: Any) -> None:
        data = json.dumps(body, ensure_ascii=False).encode("utf-8")
        self.send_response(status)
        self.send_header("Content-Type", "application/json; charset=utf-8")
        self.send_header("Content-Length", str(len(data)))
        self.end_headers()
        self.wfile.write(data)

    def send_raw(self, status: int, content_type: str, data: bytes) -> None:
        self.send_response(status)
        self.send_header("Content-Type", content_type)
        self.send_header("Content-Length", str(len(data)))
        self.end_headers()
        self.wfile.write(data)

    def _dispatch(self) -> None:
        request = Request(self)
        try:
            if request.path.startswith("/_mock/"):
                self._control(request)
            else:
                self.handle_upstream(request)
        except Exception as exc:  # a mock must never hang the client
            self._log(500, request, {"error": repr(exc)})
            self.send_json(500, {"mock_error": repr(exc)})

    do_GET = do_POST = do_DELETE = do_PUT = _dispatch  # noqa: N815 - stdlib naming

    # --- control API ----------------------------------------------------------------------
    def _control(self, request: Request) -> None:
        route = (request.method, request.path)
        if route == ("GET", "/_mock/health"):
            self.send_json(200, {"ok": True, "service": self.service})
            return
        if route == ("GET", "/_mock/calls"):
            self.send_json(200, self.state.calls())
        elif route == ("DELETE", "/_mock/calls"):
            self.state.clear_calls()
            self.send_json(200, {"ok": True})
        elif route == ("GET", "/_mock/faults"):
            self.send_json(200, self.state.faults())
        elif route == ("POST", "/_mock/faults"):
            body = request.json() or {}
            fault = Fault(
                status=int(body["status"]),
                when={str(k): str(v) for k, v in (body.get("when") or {}).items()},
                count=int(body.get("count", 1)),
                description=body.get("description"),
                delay_ms=int(body.get("delay_ms", 0)),
            )
            self.state.add_fault(fault)
            self.send_json(201, asdict(fault))
        elif route == ("DELETE", "/_mock/faults"):
            self.state.clear_faults()
            self.send_json(200, {"ok": True})
        elif route == ("POST", "/_mock/reset"):
            self.state.clear_calls()
            self.state.clear_faults()
            self.send_json(200, {"ok": True})
        else:
            self.send_json(404, {"mock_error": f"unknown control route {request.method} {request.path}"})
            return
        self._log(200, request)

    def handle_upstream(self, request: Request) -> None:  # pragma: no cover - abstract
        raise NotImplementedError


class MockServer(ThreadingHTTPServer):
    """ThreadingHTTPServer without the reverse-DNS lookup in server_bind (slow on macOS)."""

    daemon_threads = True

    def server_bind(self) -> None:
        socketserver.TCPServer.server_bind(self)
        host, port = self.server_address[:2]
        self.server_name, self.server_port = str(host), int(port)


def serve(handler: type[MockHandler], port: int) -> None:
    server = MockServer(("0.0.0.0", port), handler)  # noqa: S104 - container-internal mock
    print(json.dumps({"service": handler.service, "listening": port}), flush=True)
    server.serve_forever()
