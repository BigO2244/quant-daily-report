"""Localhost-only, read-only-by-default standard-library REST API."""

from __future__ import annotations

import json
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from typing import Type
from urllib.parse import urlparse

from .service import AegisService


def handler(service: AegisService, allow_writes: bool = False) -> Type[BaseHTTPRequestHandler]:
    class AegisHandler(BaseHTTPRequestHandler):
        def _send(self, code: int, value: object) -> None:
            body = json.dumps(value, sort_keys=True).encode("utf-8")
            self.send_response(code); self.send_header("Content-Type", "application/json"); self.send_header("Content-Length", str(len(body))); self.send_header("Cache-Control", "no-store"); self.end_headers(); self.wfile.write(body)

        def do_GET(self) -> None:  # noqa: N802
            path = urlparse(self.path).path
            routes = {
                "/health": lambda: {"status": "OK", "schema_version": service.store.schema_version(), "write_enabled": allow_writes},
                "/portfolio": service.mission_control_model, "/missions": service.store.missions,
                "/hierarchy": service.store.hierarchy, "/relationships": service.store.relationships,
                "/decisions": service.store.decisions_queue, "/reconciliation": service.store.reconciliation,
                "/priorities": service.store.priorities, "/briefs": service.store.briefs, "/sources": service.store.source_health,
            }
            if path in routes: self._send(200, routes[path]()); return
            if path.startswith("/missions/"):
                mission = service.store.mission(path.rsplit("/", 1)[-1]); self._send(200 if mission else 404, mission or {"error": "not found"}); return
            if path.startswith("/graph/"):
                entity_id = path.rsplit("/", 1)[-1]
                if not service.store.entity(entity_id): self._send(404, {"error": "not found"}); return
                self._send(200, {"entity": service.store.entity(entity_id), "relationships": service.store.relationships(entity_id), "related": service.store.traverse(entity_id)}); return
            self._send(404, {"error": "not found"})

        def do_POST(self) -> None:  # noqa: N802
            if not allow_writes: self._send(405, {"error": "REST writes disabled"}); return
            if urlparse(self.path).path != "/missions": self._send(404, {"error": "not found"}); return
            size = int(self.headers.get("Content-Length", "0")); payload = json.loads(self.rfile.read(size) or b"{}")
            try: self._send(201, service.create_mission(str(payload.get("objective", "")), payload.get("metadata")))
            except ValueError as exc: self._send(400, {"error": str(exc)})

        def log_message(self, format: str, *args: object) -> None: return
    return AegisHandler


def serve(service: AegisService, host: str, port: int) -> None:
    if host not in {"127.0.0.1", "localhost", "::1"}: raise ValueError("AEG-002 REST may bind only to localhost")
    ThreadingHTTPServer((host, port), handler(service, allow_writes=False)).serve_forever()
