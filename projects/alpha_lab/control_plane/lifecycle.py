"""Fail-closed candidate assessment and CIO queue generation."""

from __future__ import annotations

from datetime import datetime
from typing import Any, Dict, Iterable, List, Tuple

from projects.alpha_lab.factory.canonical import canonical_hash, format_datetime
from projects.alpha_lab.factory.contracts import _require_aware

from .models import (
    CandidateAssessment,
    CandidateSnapshot,
    DataStatus,
    OwnerDecision,
    QueueItem,
    QueueItemType,
    ResearchVerdict,
    ShadowStatus,
)


def _failed_gates(prefix: str, values: Dict[str, bool]) -> List[str]:
    return ["{}:{}".format(prefix, name) for name, passed in sorted(values.items()) if not passed]


def _data_item(candidate: CandidateSnapshot, requirement: Any) -> QueueItem:
    costs = {
        "estimated_one_time_cost_usd": requirement.estimated_one_time_cost_usd,
        "estimated_monthly_cost_usd": requirement.estimated_monthly_cost_usd,
    }
    return QueueItem(
        item_id="{}:data:{}".format(candidate.hypothesis_id, requirement.requirement_id),
        item_type=QueueItemType.DATA_ACCESS_REVIEW,
        hypothesis_id=candidate.hypothesis_id,
        title="Data access review — {}".format(candidate.title),
        priority=2,
        summary=(
            "The frozen experiment requires {} / {} and cannot read returns until "
            "the dataset passes its acceptance audit."
        ).format(requirement.provider_id, requirement.dataset_id),
        decision_requested="Approve a trial/purchase request, defer it, or reject it.",
        options=("APPROVE_REQUEST", "DEFER", "REJECT"),
        blockers=("data_not_certified:{}".format(requirement.requirement_id),),
        evidence=candidate.evidence,
        payload={
            "requirement": requirement.to_dict(),
            "costs": costs,
            "alpha_claim_permitted": False,
            "purchase_performed": False,
        },
    )


def assess_candidate(candidate: CandidateSnapshot, *, assessed_at: datetime) -> CandidateAssessment:
    """Return the next governed action without performing a lifecycle transition."""

    _require_aware(assessed_at, "assessed_at")
    queue: List[QueueItem] = []
    blockers: List[str] = []

    missing_data = [item for item in candidate.data_requirements if not item.ready]
    for requirement in missing_data:
        blockers.append("data_not_certified:{}".format(requirement.requirement_id))
        if requirement.needs_owner_review:
            queue.append(_data_item(candidate, requirement))

    if candidate.research_verdict is ResearchVerdict.REJECT or (
        candidate.owner_research_decision is OwnerDecision.KILL
    ):
        return CandidateAssessment(
            hypothesis_id=candidate.hypothesis_id,
            state="CLOSED_REJECTED",
            recommendation="RETAIN_NEGATIVE_EVIDENCE",
            blockers=tuple(blockers),
            queue_items=tuple(queue),
            assessed_at=assessed_at,
        )
    if candidate.owner_research_decision is OwnerDecision.PARK:
        return CandidateAssessment(
            hypothesis_id=candidate.hypothesis_id,
            state="PARKED",
            recommendation="NO_ACTION_UNTIL_OWNER_REOPENS",
            blockers=tuple(blockers),
            queue_items=tuple(queue),
            assessed_at=assessed_at,
        )
    if missing_data:
        return CandidateAssessment(
            hypothesis_id=candidate.hypothesis_id,
            state="BLOCKED_DATA",
            recommendation=("REQUEST_DATA_DECISION" if queue else "COMPLETE_FREE_DATA_GATES"),
            blockers=tuple(blockers),
            queue_items=tuple(queue),
            assessed_at=assessed_at,
        )

    research_failures = _failed_gates("research_gate_failed", dict(candidate.research_gates))
    if not candidate.evidence:
        research_failures.append("decision_grade_evidence_missing")
    blockers.extend(research_failures)
    if candidate.research_verdict is not ResearchVerdict.EVIDENCE_READY_FOR_OWNER_REVIEW:
        return CandidateAssessment(
            hypothesis_id=candidate.hypothesis_id,
            state="RESEARCH_ACTIVE",
            recommendation="RUN_OR_REVIEW_FROZEN_EXPERIMENT",
            blockers=tuple(blockers),
            queue_items=tuple(queue),
            assessed_at=assessed_at,
        )
    if research_failures:
        return CandidateAssessment(
            hypothesis_id=candidate.hypothesis_id,
            state="RESEARCH_GATES_FAILED",
            recommendation="ITERATE_OR_REJECT_WITHOUT_PROMOTION",
            blockers=tuple(blockers),
            queue_items=tuple(queue),
            assessed_at=assessed_at,
        )
    if candidate.owner_research_decision is OwnerDecision.PENDING:
        queue.append(
            QueueItem(
                item_id="{}:research-owner-review".format(candidate.hypothesis_id),
                item_type=QueueItemType.RESEARCH_DECISION_REVIEW,
                hypothesis_id=candidate.hypothesis_id,
                title="Research decision — {}".format(candidate.title),
                priority=3,
                summary="All frozen research gates passed; the candidate is ready for owner disposition.",
                decision_requested="Choose whether this candidate should be pursued toward Shadow.",
                options=("PURSUE", "PARK", "KILL"),
                blockers=(),
                evidence=candidate.evidence,
                payload={"classification": candidate.classification},
            )
        )
        return CandidateAssessment(
            hypothesis_id=candidate.hypothesis_id,
            state="EVIDENCE_READY_FOR_OWNER_REVIEW",
            recommendation="OWNER_RESEARCH_DECISION_REQUIRED",
            blockers=(),
            queue_items=tuple(queue),
            assessed_at=assessed_at,
        )

    if candidate.shadow_status in {
        ShadowStatus.NOT_REQUESTED,
        ShadowStatus.AWAITING_OWNER_APPROVAL,
    }:
        queue.append(
            QueueItem(
                item_id="{}:shadow-activation-review".format(candidate.hypothesis_id),
                item_type=QueueItemType.SHADOW_ACTIVATION_REVIEW,
                hypothesis_id=candidate.hypothesis_id,
                title="Shadow activation review — {}".format(candidate.title),
                priority=3,
                summary="The owner elected to pursue the research candidate; Shadow remains non-capitalized.",
                decision_requested="Approve or decline a separately governed Shadow onboarding task.",
                options=("APPROVE_SHADOW_ONBOARDING", "DEFER", "DECLINE"),
                blockers=(),
                evidence=candidate.evidence,
                payload={"runtime_change_performed": False, "registry_change_performed": False},
            )
        )
        return CandidateAssessment(
            hypothesis_id=candidate.hypothesis_id,
            state="AWAITING_SHADOW_APPROVAL",
            recommendation="OWNER_SHADOW_DECISION_REQUIRED",
            blockers=(),
            queue_items=tuple(queue),
            assessed_at=assessed_at,
        )
    if candidate.shadow_status is ShadowStatus.DECLINED:
        return CandidateAssessment(
            hypothesis_id=candidate.hypothesis_id,
            state="SHADOW_DECLINED",
            recommendation="PARK_OR_KILL",
            blockers=(),
            queue_items=tuple(queue),
            assessed_at=assessed_at,
        )

    due = [
        checkpoint
        for checkpoint in candidate.shadow_review_checkpoints
        if checkpoint <= candidate.shadow_observation_days
        and checkpoint > candidate.last_reviewed_shadow_checkpoint
    ]
    shadow_failures = _failed_gates("shadow_gate_failed", dict(candidate.shadow_gates))
    shadow_evidence = [
        item for item in candidate.evidence if "shadow" in item.label.lower()
    ]
    if not shadow_evidence:
        shadow_failures.append("shadow_evidence_missing")
    final_ready = candidate.shadow_observation_days >= candidate.final_shadow_checkpoint
    if final_ready and not shadow_failures:
        queue.append(
            QueueItem(
                item_id="{}:paper-promotion-review".format(candidate.hypothesis_id),
                item_type=QueueItemType.PAPER_PROMOTION_REVIEW,
                hypothesis_id=candidate.hypothesis_id,
                title="Paper promotion review — {}".format(candidate.title),
                priority=5,
                summary=(
                    "The candidate reached its frozen final Shadow checkpoint and all supplied "
                    "research and Shadow gates pass. This is a nomination, not a promotion."
                ),
                decision_requested="Approve a separately scoped Paper promotion task, extend Shadow, park, or reject.",
                options=("APPROVE_PAPER_SCOPING", "EXTEND_SHADOW", "PARK", "KILL"),
                blockers=(),
                evidence=candidate.evidence,
                payload={
                    "shadow_observation_days": candidate.shadow_observation_days,
                    "final_checkpoint": candidate.final_shadow_checkpoint,
                    "promotion_performed": False,
                },
            )
        )
        return CandidateAssessment(
            hypothesis_id=candidate.hypothesis_id,
            state="PAPER_NOMINATION_READY",
            recommendation="OWNER_PAPER_DECISION_REQUIRED",
            blockers=(),
            queue_items=tuple(queue),
            assessed_at=assessed_at,
        )

    blockers.extend(shadow_failures)
    if due:
        checkpoint = max(due)
        queue.append(
            QueueItem(
                item_id="{}:shadow-checkpoint:{}".format(candidate.hypothesis_id, checkpoint),
                item_type=QueueItemType.SHADOW_CHECKPOINT_REVIEW,
                hypothesis_id=candidate.hypothesis_id,
                title="Shadow checkpoint {} — {}".format(checkpoint, candidate.title),
                priority=4,
                summary="A frozen Shadow observation checkpoint is due for evidence review.",
                decision_requested="Continue observation, investigate failed gates, park, or reject.",
                options=("CONTINUE_SHADOW", "INVESTIGATE", "PARK", "KILL"),
                blockers=tuple(shadow_failures),
                evidence=candidate.evidence,
                payload={
                    "checkpoint": checkpoint,
                    "observation_days": candidate.shadow_observation_days,
                    "shadow_gates": dict(candidate.shadow_gates),
                },
            )
        )
        return CandidateAssessment(
            hypothesis_id=candidate.hypothesis_id,
            state="SHADOW_CHECKPOINT_DUE",
            recommendation="OWNER_SHADOW_CHECKPOINT_REVIEW",
            blockers=tuple(blockers),
            queue_items=tuple(queue),
            assessed_at=assessed_at,
        )

    return CandidateAssessment(
        hypothesis_id=candidate.hypothesis_id,
        state="SHADOW_OBSERVING",
        recommendation="CONTINUE_UNTIL_NEXT_FROZEN_CHECKPOINT",
        blockers=tuple(blockers),
        queue_items=tuple(queue),
        assessed_at=assessed_at,
    )


def build_cio_queue(
    candidates: Iterable[CandidateSnapshot], *, generated_at: datetime
) -> Dict[str, Any]:
    _require_aware(generated_at, "generated_at")
    assessments = [assess_candidate(item, assessed_at=generated_at) for item in candidates]
    items = sorted(
        (queue_item for assessment in assessments for queue_item in assessment.queue_items),
        key=lambda item: (-item.priority, item.item_type.value, item.hypothesis_id),
    )
    payload = {
        "schema_version": "caerus_alpha_lab_cio_queue_v1",
        "generated_at": format_datetime(generated_at),
        "classification": "RESEARCH_ONLY_NON_EXECUTIONAL",
        "item_count": len(items),
        "items": [item.to_dict() for item in items],
        "assessments": [item.to_dict() for item in assessments],
        "owner_action_required": bool(items),
        "trading_behavior_changed": False,
        "promotion_performed": False,
        "purchase_performed": False,
    }
    payload["decision_fingerprint"] = canonical_hash(payload["items"])
    payload["queue_hash"] = canonical_hash(payload)
    return payload


def render_queue_markdown(queue: Dict[str, Any]) -> str:
    lines = [
        "# Alpha Lab CIO Review Queue",
        "",
        "Generated: `{}`".format(queue["generated_at"]),
        "",
        "This queue nominates decisions. It does not purchase data, activate Shadow, or promote a model.",
        "",
    ]
    if not queue["items"]:
        lines.append("No owner decisions are currently due.")
        lines.append("")
    for item in queue["items"]:
        lines.extend(
            [
                "## {}".format(item["title"]),
                "",
                "- Type: `{}`".format(item["item_type"]),
                "- Priority: `{}`".format(item["priority"]),
                "- Candidate: `{}`".format(item["hypothesis_id"]),
                "- Request: {}".format(item["decision_requested"]),
                "- Options: {}".format(", ".join("`{}`".format(value) for value in item["options"])),
                "",
                item["summary"],
                "",
            ]
        )
        if item["blockers"]:
            lines.append("Blockers: {}".format(", ".join("`{}`".format(value) for value in item["blockers"])))
            lines.append("")
    lines.append("Queue hash: `{}`".format(queue["queue_hash"]))
    lines.append("")
    return "\n".join(lines)
