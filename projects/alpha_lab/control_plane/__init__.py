"""Research-only discovery-to-decision control plane for Alpha Lab.

The package produces recommendations and owner-review artifacts.  It has no
authority to change the Caerus strategy registry or any trading runtime.
"""

from .lifecycle import assess_candidate, build_cio_queue, render_queue_markdown
from .models import (
    AccessMode,
    CandidateAssessment,
    CandidateSnapshot,
    DataRequirement,
    DataStatus,
    OwnerDecision,
    QueueItem,
    QueueItemType,
    ResearchVerdict,
    ShadowStatus,
)

__all__ = [
    "AccessMode",
    "CandidateAssessment",
    "CandidateSnapshot",
    "DataRequirement",
    "DataStatus",
    "OwnerDecision",
    "QueueItem",
    "QueueItemType",
    "ResearchVerdict",
    "ShadowStatus",
    "assess_candidate",
    "build_cio_queue",
    "render_queue_markdown",
]
