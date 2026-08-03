"""Standalone Mission Control HTML artifact; no deployed dashboard integration."""
from __future__ import annotations
from html import escape
from .service import AegisService

def render_mission_control(service: AegisService) -> str:
    missions = service.mission_control_model()
    rows = "".join(f"<tr><td>{escape(m['id'])}</td><td>{escape(m['state'])}</td><td>{escape(m['objective'])}</td><td>{len(m['tasks'])}</td></tr>" for m in missions)
    detail = "".join(f"<section><h2>{escape(m['id'])}</h2><p>Blockers: {escape(m['state']) if m['state'] in {'BLOCKED', 'DECISION_REQUIRED', 'APPROVAL_REQUIRED'} else 'none'}</p><p>DAG edges: {len(m['edges'])}; artifacts: {len(m['artifacts'])}; decisions: {len(m['decisions'])}</p></section>" for m in missions)
    return "<!doctype html><title>Aegis Mission Control</title><h1>Aegis Mission Control</h1><p>Read-only local control-plane view.</p><table><thead><tr><th>Mission</th><th>State</th><th>Objective</th><th>Tasks</th></tr></thead><tbody>" + rows + "</tbody></table>" + detail
