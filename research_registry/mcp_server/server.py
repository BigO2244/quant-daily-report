"""Minimal stdio JSON-RPC server for Caerus registry MCP Phase 6A."""

from __future__ import annotations

import argparse
import dataclasses
import json
import sys
from pathlib import Path
from typing import Any, TextIO

from research_registry.mcp_server.tools import ToolContext, build_caerus_registry, call_tool, list_tools


def json_default(value: Any) -> Any:
    if hasattr(value, "to_dict"):
        return value.to_dict()
    if dataclasses.is_dataclass(value):
        return dataclasses.asdict(value)
    if isinstance(value, Path):
        return str(value)
    return str(value)


def handle_jsonrpc(request: dict[str, Any], context: ToolContext) -> dict[str, Any]:
    request_id = request.get("id")
    method = request.get("method")
    params = request.get("params") or {}
    try:
        if method == "initialize":
            result = {
                "protocolVersion": "2024-11-05",
                "serverInfo": {"name": "caerus-research-registry", "version": "0.1.0"},
                "capabilities": {"tools": {}},
            }
        elif method == "tools/list":
            result = {"tools": list_tools()}
        elif method == "tools/call":
            tool_name = params.get("name")
            arguments = params.get("arguments") or {}
            result = {
                "content": [
                    {
                        "type": "text",
                        "text": json.dumps(call_tool(str(tool_name), arguments, context), indent=2, sort_keys=True, default=json_default),
                    }
                ],
                "isError": False,
            }
        else:
            return {
                "jsonrpc": "2.0",
                "id": request_id,
                "error": {"code": -32601, "message": f"method not found: {method}"},
            }
        return {"jsonrpc": "2.0", "id": request_id, "result": result}
    except Exception as exc:
        return {"jsonrpc": "2.0", "id": request_id, "error": {"code": -32000, "message": str(exc)}}


def serve_stdio(context: ToolContext, stdin: TextIO | None = None, stdout: TextIO | None = None) -> int:
    stdin = stdin or sys.stdin
    stdout = stdout or sys.stdout
    for line in stdin:
        if not line.strip():
            continue
        try:
            request = json.loads(line)
            response = handle_jsonrpc(request, context)
        except json.JSONDecodeError as exc:
            response = {"jsonrpc": "2.0", "id": None, "error": {"code": -32700, "message": str(exc)}}
        stdout.write(json.dumps(response, sort_keys=True, default=json_default) + "\n")
        stdout.flush()
    return 0


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(description="Local read-only Caerus research registry MCP-compatible server.")

    def add_common_options(target: argparse.ArgumentParser) -> None:
        target.add_argument("--db", default="/tmp/caerus-research-registry.db", help="Disposable SQLite registry path.")
        target.add_argument("--runs-root", default="outputs/runs")
        target.add_argument("--packets-root", default="outputs/research_packets")
        target.add_argument("--docs-root", default="docs/governance")
        target.add_argument("--limit", type=int, default=10)

    add_common_options(parser)
    subparsers = parser.add_subparsers(dest="command")
    add_common_options(subparsers.add_parser("stdio", help="Run stdio JSON-RPC server."))
    add_common_options(subparsers.add_parser("tools", help="Print tool definitions."))
    add_common_options(subparsers.add_parser("smoke", help="Build registry and query summary through tool functions."))
    return parser


def main(argv: list[str] | None = None) -> int:
    parser = build_parser()
    args = parser.parse_args(argv)
    context = ToolContext(
        db_path=Path(args.db),
        runs_root=Path(args.runs_root),
        packets_root=Path(args.packets_root),
        docs_root=Path(args.docs_root),
        limit=args.limit,
    )
    command = args.command or "stdio"
    if command == "stdio":
        return serve_stdio(context)
    if command == "tools":
        print(json.dumps({"tools": list_tools()}, indent=2, sort_keys=True, default=json_default))
        return 0
    if command == "smoke":
        payload = build_caerus_registry(
            context=context,
            db_path=str(context.db_path),
            runs_root=str(context.runs_root),
            packets_root=str(context.packets_root),
            docs_root=str(context.docs_root),
            limit=context.limit,
        )
        print(json.dumps(payload, indent=2, sort_keys=True, default=json_default))
        return 0 if payload.get("status") == "OK" else 1
    parser.error(f"unknown command: {command}")
    return 2


if __name__ == "__main__":
    raise SystemExit(main())
