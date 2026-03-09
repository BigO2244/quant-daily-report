from __future__ import annotations

import os
from pathlib import Path
from typing import Iterable


def append_step_summary(lines: Iterable[str]) -> bool:
    """Append lines to GitHub Actions step summary when available.

    Returns True when summary was written, False when unavailable.
    """
    summary_path = str(os.getenv("GITHUB_STEP_SUMMARY") or "").strip()
    if not summary_path:
        return False

    path = Path(summary_path)
    path.parent.mkdir(parents=True, exist_ok=True)
    text = "\n".join(str(line) for line in lines).rstrip() + "\n"
    with path.open("a", encoding="utf-8") as f:
        f.write(text)
    return True
