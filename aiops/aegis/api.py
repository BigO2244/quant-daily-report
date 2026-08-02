"""Small standard-library REST surface; it is opt-in and never starts a daemon itself."""
from __future__ import annotations
import json
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from typing import Type
from .service import AegisService

def handler(service: AegisService) -> Type[BaseHTTPRequestHandler]:
    class AegisHandler(BaseHTTPRequestHandler):
        def _send(self, code: int, value: object) -> None:
            body = json.dumps(value, sort_keys=True).encode("utf-8")
            self.send_response(code); self.send_header("Content-Type", "application/json"); self.send_header("Content-Length", str(len(body))); self.end_headers(); self.wfile.write(body)
        def do_GET(self) -> None:  # noqa: N802
            if self.path == "/portfolio": self._send(200, service.mission_control_model()); return
            if self.path == "/missions": self._send(200, service.store.missions()); return
            if self.path.startswith("/missions/"):
                mission = service.store.mission(self.path.rsplit("/", 1)[-1]); self._send(200 if mission else 404, mission or {"error": "not found"}); return
            self._send(404, {"error": "not found"})
        def do_POST(self) -> None:  # noqa: N802
            if self.path != "/missions": self._send(404, {"error": "not found"}); return
            size = int(self.headers.get("Content-Length", "0")); payload = json.loads(self.rfile.read(size) or b"{}")
            try: self._send(201, service.create_mission(str(payload.get("objective", "")), payload.get("metadata")))
            except ValueError as exc: self._send(400, {"error": str(exc)})
        def log_message(self, format: str, *args: object) -> None: return
    return AegisHandler

def serve(service: AegisService, host: str, port: int) -> None:
    ThreadingHTTPServer((host, port), handler(service)).serve_forever()
