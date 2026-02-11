from __future__ import annotations


def base_email_css() -> str:
    """Conservative email-safe CSS shared by report emails."""
    return (
        "body { font-family: -apple-system, BlinkMacSystemFont, 'Segoe UI', Arial, sans-serif; color:#111827; margin:0; padding:0; }"
        ".wrap { max-width: 980px; margin: 0 auto; padding: 16px; }"
        ".card { background:#f9fafb; border:1px solid #e5e7eb; border-radius:10px; padding:12px; margin:12px 0; }"
        ".muted { color:#6b7280; font-size:12px; }"
        ".kvs { margin:0; padding:0; list-style:none; }"
        ".kvs li { margin: 2px 0; }"
        ".tbl { width:100%; border-collapse: collapse; font-size:13px; }"
        ".tbl th { text-align:left; border-bottom:1px solid #d1d5db; padding:6px; background:#f3f4f6; }"
        ".tbl td { border-bottom:1px solid #e5e7eb; padding:6px; vertical-align:top; }"
        ".num { text-align:right; white-space:nowrap; }"
        "h1, h2, h3 { margin: 0 0 8px 0; }"
        "p { margin: 6px 0; }"
    )


def wrap_email_html(title: str, body_html: str) -> str:
    return (
        "<html><head><meta charset='utf-8'>"
        f"<style>{base_email_css()}</style>"
        "</head><body>"
        f"<div class='wrap'><h2>{title}</h2>{body_html}</div>"
        "</body></html>"
    )
