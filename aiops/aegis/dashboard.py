"""Deterministic, standalone, local Mission Control artifact."""

from __future__ import annotations

import json
from html import escape

from .service import AegisService


def render_mission_control(service: AegisService, generated_at: str | None = None) -> str:
    briefs = service.store.briefs()
    model = {
        "generated_at": generated_at or (briefs[-1]["as_of"] if briefs else "NOT_AVAILABLE"),
        "missions": service.mission_control_model(), "hierarchy": service.store.hierarchy(),
        "entities": service.store.entities(), "relationships": service.store.relationships(),
        "decisions": service.store.decisions_queue(), "reconciliation": service.store.reconciliation(),
        "priorities": service.store.priorities(), "sources": service.store.source_health(),
        "brief": briefs[-1]["payload"] if briefs else None,
    }
    serialized = json.dumps(model, sort_keys=True).replace("</", "<\\/")
    mission_rows = "".join(f"<tr><td><code>{escape(m['id'])}</code></td><td>{escape(m['state'])}</td><td>{escape(m['origin'])}</td><td>{escape(m['objective'])}</td><td>{len(m['tasks'])}</td></tr>" for m in model["missions"]) or "<tr><td colspan='5'>No missions recorded.</td></tr>"
    source_rows = "".join(f"<tr><td>{escape(s['source_type'])}</td><td>{escape(s['status'])}</td><td>{escape(s['source_uri'])}</td><td>{escape(s['fetched_at'])}</td></tr>" for s in model["sources"]) or "<tr><td colspan='4'>No imports recorded.</td></tr>"
    cards = (("Decision queue", len(model["decisions"])), ("Reconciliation", len(model["reconciliation"])), ("Relationships", len(model["relationships"])), ("Unresolved", sum(e["status"] == "STATUS_UNRESOLVED" for e in model["entities"])))
    card_html = "".join(f"<article><strong>{value}</strong><span>{escape(label)}</span></article>" for label, value in cards)
    return f"""<!doctype html><html lang='en'><head><meta charset='utf-8'><meta name='viewport' content='width=device-width,initial-scale=1'><title>Aegis Mission Control</title>
<style>:root{{--bg:#10151d;--panel:#18212c;--line:#334155;--text:#e5edf5;--muted:#9fb0c2;--accent:#62d6c8}}*{{box-sizing:border-box}}body{{margin:0;background:var(--bg);color:var(--text);font:14px system-ui,sans-serif}}header,main{{max-width:1440px;margin:auto;padding:24px}}header{{display:flex;justify-content:space-between;align-items:end;border-bottom:1px solid var(--line)}}h1,h2{{margin:.2em 0}}small,.muted{{color:var(--muted)}}nav{{display:flex;gap:12px;flex-wrap:wrap}}nav a{{color:var(--accent)}}.cards{{display:grid;grid-template-columns:repeat(4,minmax(130px,1fr));gap:12px;margin:20px 0}}article,section{{background:var(--panel);border:1px solid var(--line);border-radius:8px;padding:16px}}article strong{{font-size:28px;display:block}}article span{{color:var(--muted)}}section{{margin:14px 0;overflow:auto}}table{{border-collapse:collapse;width:100%;min-width:760px}}th,td{{padding:9px;border-bottom:1px solid var(--line);text-align:left}}th{{color:var(--muted)}}code{{color:var(--accent)}}.status{{padding:6px 10px;background:#3a2e19;border:1px solid #765f2b;border-radius:5px}}@media(max-width:760px){{.cards{{grid-template-columns:repeat(2,1fr)}}header{{display:block}}}}</style></head>
<body><header><div><small>LOCAL · READ ONLY · NON-TRADING</small><h1>Aegis Mission Control</h1><span class='muted'>Generated {escape(model['generated_at'])}</span></div><span class='status'>Source data may be stale—inspect Import Health</span></header><main>
<nav><a href='#portfolio'>Portfolio</a><a href='#hierarchy'>Hierarchy & Graph</a><a href='#queues'>Queues</a><a href='#lineage'>Artifact Lineage</a><a href='#brief'>Executive Brief</a><a href='#health'>Import Health</a></nav><div class='cards'>{card_html}</div>
<section id='portfolio'><h2>Mission Portfolio and Priority Ranking</h2><table><thead><tr><th>Mission</th><th>State</th><th>Origin</th><th>Objective</th><th>Tasks</th></tr></thead><tbody>{mission_rows}</tbody></table></section>
<section id='hierarchy'><h2>Hierarchy, Task DAG, and Relationship Graph</h2><p>{len(model['hierarchy'])} hierarchy links; {len(model['relationships'])} typed relationships. Select full records from the embedded deterministic model.</p></section>
<section id='queues'><h2>Blocker, Decision, and Reconciliation Queues</h2><p>{len(model['decisions'])} decisions; {len(model['reconciliation'])} reconciliation items. No queue action is executable from this artifact.</p></section>
<section id='lineage'><h2>Artifact Lineage, Source Provenance, and GitHub Links</h2><p>Imported and native records are distinguished by each entity's <code>origin</code>. GitHub is planning provenance only.</p></section>
<section id='brief'><h2>Executive Brief</h2><pre>{escape(json.dumps(model['brief'], indent=2, sort_keys=True) if model['brief'] else 'No brief generated.')}</pre></section>
<section id='health'><h2>Import Health and Unresolved State</h2><table><thead><tr><th>Source</th><th>Health</th><th>Source of truth</th><th>Fetched</th></tr></thead><tbody>{source_rows}</tbody></table></section>
<script type='application/json' id='aegis-data'>{serialized}</script></main></body></html>"""
