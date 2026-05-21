"""
Caerus Quant Research Agent — MCP Server
=========================================
A FastMCP server that exposes read-only tools for the Caerus pipeline.
Deploy on the GCP VM alongside the existing pipeline scripts.

Usage:
    pip install "mcp[cli]"
    python caerus_mcp_server.py

Connects via streamable-http on port 8765.
Claude Code config points to http://<VM_IP>:8765/mcp
"""

import json
import os
import glob
from datetime import datetime
from pathlib import Path

from mcp.server.fastmcp import FastMCP

# ---------------------------------------------------------------------------
# CONFIG — Update these paths to match your GCP VM layout
# ---------------------------------------------------------------------------
CAERUS_BASE = os.environ.get("CAERUS_BASE", os.path.expanduser("~/caerus"))
SCORES_DIR = os.path.join(CAERUS_BASE, "output", "scores")
DIGEST_DIR = os.path.join(CAERUS_BASE, "output", "digests")
LOG_DIR = os.path.join(CAERUS_BASE, "logs")

# ---------------------------------------------------------------------------
# MCP Server
# ---------------------------------------------------------------------------
mcp = FastMCP(
    name="Caerus Research Agent",
    host="0.0.0.0",  # Listen on all interfaces (firewall controls access)
    port=8765,
)


@mcp.tool()
def get_pipeline_status() -> dict:
    """Check whether the Caerus nightly pipeline ran successfully.

    Returns the timestamp of the last run, status, and any error snippets
    from the most recent log file.
    """
    log_files = sorted(glob.glob(os.path.join(LOG_DIR, "*.log")), reverse=True)

    if not log_files:
        return {
            "status": "unknown",
            "message": "No log files found. Check LOG_DIR path.",
            "log_dir": LOG_DIR,
        }

    latest_log = log_files[0]
    stat = os.stat(latest_log)
    modified = datetime.fromtimestamp(stat.st_mtime).isoformat()

    # Read last 50 lines for status/errors
    with open(latest_log, "r") as f:
        lines = f.readlines()
    tail = lines[-50:] if len(lines) > 50 else lines
    tail_text = "".join(tail)

    # Simple heuristic: look for ERROR or SUCCESS markers
    has_error = any("ERROR" in line.upper() for line in tail)
    has_success = any("SUCCESS" in line.upper() or "COMPLETE" in line.upper() for line in tail)

    if has_error:
        status = "error"
    elif has_success:
        status = "success"
    else:
        status = "unclear"

    return {
        "status": status,
        "last_log_file": os.path.basename(latest_log),
        "last_modified": modified,
        "tail": tail_text[-2000:],  # Cap output size
    }


@mcp.tool()
def get_latest_scores(top_n: int = 10) -> dict:
    """Retrieve the most recent Caerus scoring output.

    Args:
        top_n: Number of top-scored items to return (default 10).

    Returns the filename, timestamp, and the top N scored items
    from the most recent scores file (expects JSON).
    """
    score_files = sorted(glob.glob(os.path.join(SCORES_DIR, "*.json")), reverse=True)

    if not score_files:
        return {
            "status": "no_data",
            "message": f"No score files found in {SCORES_DIR}",
        }

    latest = score_files[0]
    stat = os.stat(latest)
    modified = datetime.fromtimestamp(stat.st_mtime).isoformat()

    try:
        with open(latest, "r") as f:
            data = json.load(f)
    except json.JSONDecodeError as e:
        return {
            "status": "parse_error",
            "file": os.path.basename(latest),
            "error": str(e),
        }

    # Adapt this to your actual schema —
    # assumes a list of dicts with a "score" key, or a top-level list
    items = data if isinstance(data, list) else data.get("scores", data.get("items", []))

    # Sort by score descending if possible
    try:
        items = sorted(items, key=lambda x: x.get("score", 0), reverse=True)
    except (TypeError, AttributeError):
        pass  # If items aren't dicts with score, just return as-is

    return {
        "status": "ok",
        "file": os.path.basename(latest),
        "generated_at": modified,
        "total_items": len(items),
        "top_n": items[:top_n],
    }


@mcp.tool()
def get_latest_digest() -> dict:
    """Return the most recent HTML digest summary.

    Returns metadata and the first 3000 characters of the digest HTML
    (enough for Claude to summarize the key signals).
    """
    digest_files = sorted(
        glob.glob(os.path.join(DIGEST_DIR, "*.html")),
        reverse=True,
    )

    if not digest_files:
        return {
            "status": "no_data",
            "message": f"No digest files found in {DIGEST_DIR}",
        }

    latest = digest_files[0]
    stat = os.stat(latest)
    modified = datetime.fromtimestamp(stat.st_mtime).isoformat()

    with open(latest, "r") as f:
        content = f.read()

    return {
        "status": "ok",
        "file": os.path.basename(latest),
        "generated_at": modified,
        "size_bytes": len(content),
        "content_preview": content[:3000],
    }


@mcp.tool()
def list_recent_files(directory: str = "scores", limit: int = 10) -> dict:
    """List recent output files from a Caerus output directory.

    Args:
        directory: Which subdirectory to list — "scores", "digests", or "logs".
        limit: Max number of files to return.
    """
    dir_map = {
        "scores": SCORES_DIR,
        "digests": DIGEST_DIR,
        "logs": LOG_DIR,
    }

    target = dir_map.get(directory)
    if not target:
        return {"status": "error", "message": f"Unknown directory '{directory}'. Use: scores, digests, logs"}

    if not os.path.isdir(target):
        return {"status": "error", "message": f"Directory not found: {target}"}

    files = sorted(Path(target).iterdir(), key=lambda p: p.stat().st_mtime, reverse=True)
    files = [f for f in files if f.is_file()][:limit]

    return {
        "status": "ok",
        "directory": directory,
        "path": target,
        "files": [
            {
                "name": f.name,
                "size_bytes": f.stat().st_size,
                "modified": datetime.fromtimestamp(f.stat().st_mtime).isoformat(),
            }
            for f in files
        ],
    }


# ---------------------------------------------------------------------------
# Run
# ---------------------------------------------------------------------------
if __name__ == "__main__":
    print(f"Starting Caerus MCP server on 0.0.0.0:8765")
    print(f"Base dir: {CAERUS_BASE}")
    print(f"Scores:   {SCORES_DIR}")
    print(f"Digests:  {DIGEST_DIR}")
    print(f"Logs:     {LOG_DIR}")
    mcp.run(transport="streamable-http")
