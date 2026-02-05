from __future__ import annotations

import logging
import os
from typing import Any

from core.quant_report import send_email

logger = logging.getLogger(__name__)


def send_execution_email(subject: str, body_text: str, payload: dict[str, Any]) -> None:
    send_email(subject=subject, body_text=body_text)
    logger.info("[EXECUTION_EMAIL] sent to=%s", os.getenv("REPORT_TO_EMAIL", ""))
