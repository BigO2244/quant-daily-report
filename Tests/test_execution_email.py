from paper.build_execution_email import build_execution_email_text


def test_execution_email_body_is_defined_and_formats_shadow_payload_status():
    payload = {
        "trade_date": "2026-02-05",
        "mode": "SHADOW",
        "execution_status": "READY",
        "trades": [],
        "execution_payload_status": "NOT GENERATED (Expected in SHADOW)",
    }

    subject, body = build_execution_email_text(payload)

    assert subject == "TRADE EXECUTION — 2026-02-05 (SHADOW)"
    assert "• Execution Payload: NOT GENERATED (EXPECTED IN SHADOW)" in body
