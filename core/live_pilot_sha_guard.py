"""Deploy-SHA drift guard for the LIVE_PILOT execution lane (BLOCKER 4).

The audit (PRE_ARM_SWEEP_2026-07-13 §f) found that the cron runs the VM *working
tree* while ``outputs/deploy_state.json`` could name a different, stale SHA — so
"the audited SHA == the deployed SHA" was aspirational, not enforced. This module
makes it mechanical:

  * the ONE running-truth SHA is ``git rev-parse HEAD`` (never the deploy marker);
  * the marker must be a v2 PASS attestation containing one exact full SHA for
    deployed, validated, and pinned target source;
  * if the running SHA differs from that attestation — or the working tree is
    DIRTY, or either side cannot be resolved — the SUBMIT path must fail closed;
  * the DRY path proceeds but the drift is flagged prominently.

The verdict logic is a pure function (``evaluate_sha_drift``) so it is unit
testable without a git checkout; ``resolve_sha_state`` wires it to a real repo.
"""

from __future__ import annotations

import json
import re
import subprocess
from dataclasses import asdict, dataclass
from pathlib import Path
from typing import Any, Mapping

# reason codes (stable, loud, greppable)
REASON_RUNNING_SHA_UNAVAILABLE = "live_pilot_running_sha_unavailable"
REASON_DEPLOY_SHA_DRIFT = "live_pilot_deploy_sha_drift"
REASON_WORKING_TREE_DIRTY = "live_pilot_working_tree_dirty"
REASON_DEPLOY_SHA_UNKNOWN = "live_pilot_deploy_sha_unknown"
REASON_DEPLOY_ATTESTATION_INVALID = "live_pilot_deploy_attestation_invalid"

DEPLOY_STATE_SCHEMA = "caerus.deploy_state.v2"
_FULL_SHA_RE = re.compile(r"^[0-9a-f]{40}$")


def _norm(sha: str | None) -> str | None:
    if sha is None:
        return None
    value = str(sha).strip().lower()
    return value or None


def _sha_matches(running: str | None, deployed: str | None) -> bool:
    """True only for an exact pair of full, canonical commit SHAs."""
    r = _norm(running)
    d = _norm(deployed)
    if not r or not d or not _FULL_SHA_RE.fullmatch(r) or not _FULL_SHA_RE.fullmatch(d):
        return False
    return r == d


@dataclass(frozen=True)
class ShaDriftVerdict:
    running_sha: str | None
    deployed_sha: str | None
    tree_dirty: bool
    sha_drift: bool
    block_submit: bool
    reason_code: str | None
    message: str

    def to_dict(self) -> dict:
        return asdict(self)


def evaluate_sha_drift(
    running_sha: str | None,
    deployed_sha: str | None,
    tree_dirty: bool,
    *,
    attestation_valid: bool = True,
) -> ShaDriftVerdict:
    """Pure verdict: does deploy drift / a dirty tree require blocking SUBMIT?

    Fail-closed: an unresolved running SHA, an unknown deploy marker, a SHA
    mismatch, or a dirty working tree all block the submit path.
    """
    running = _norm(running_sha)
    deployed = _norm(deployed_sha)
    tree_dirty = bool(tree_dirty)

    reasons: list[str] = []
    if running is None:
        reasons.append(REASON_RUNNING_SHA_UNAVAILABLE)
    if deployed is None:
        reasons.append(REASON_DEPLOY_SHA_UNKNOWN)
    if not attestation_valid:
        reasons.append(REASON_DEPLOY_ATTESTATION_INVALID)

    sha_drift = bool(running and deployed and not _sha_matches(running, deployed))
    if sha_drift:
        reasons.append(REASON_DEPLOY_SHA_DRIFT)
    if tree_dirty:
        reasons.append(REASON_WORKING_TREE_DIRTY)

    block_submit = bool(reasons)
    reason_code = ";".join(reasons) or None

    if not block_submit:
        message = (
            f"deploy pin verified: running HEAD {running} == deployed_sha "
            f"{deployed}, working tree clean"
        )
    else:
        parts = []
        if REASON_RUNNING_SHA_UNAVAILABLE in reasons:
            parts.append("git HEAD could not be resolved")
        if REASON_DEPLOY_SHA_UNKNOWN in reasons:
            parts.append("deploy_state.json deployed_sha is missing/unreadable")
        if REASON_DEPLOY_ATTESTATION_INVALID in reasons:
            parts.append(
                "deploy_state.json is not a valid PASS attestation for one exact full SHA"
            )
        if sha_drift:
            parts.append(
                f"running HEAD {running} != deployed_sha {deployed} "
                "(the code about to run is NOT the audited/deployed SHA)"
            )
        if tree_dirty:
            parts.append(
                "working tree is DIRTY (uncommitted changes float outside any "
                "pinned SHA and defeat pinning)"
            )
        message = "DEPLOY DRIFT GUARD: " + "; ".join(parts)

    return ShaDriftVerdict(
        running_sha=running,
        deployed_sha=deployed,
        tree_dirty=tree_dirty,
        sha_drift=sha_drift,
        block_submit=block_submit,
        reason_code=reason_code,
        message=message,
    )


def _git(repo_root: Path, *args: str) -> str:
    return subprocess.check_output(
        ["git", *args], cwd=str(repo_root), text=True, stderr=subprocess.DEVNULL
    ).strip()


def resolve_running_sha(repo_root: Path | str) -> str | None:
    try:
        return _git(Path(repo_root), "rev-parse", "HEAD") or None
    except Exception:
        return None


def resolve_tree_dirty(repo_root: Path | str) -> bool:
    """True when the working tree has staged/unstaged tracked changes.

    Fail-closed: if git cannot be interrogated we cannot prove the tree is clean,
    so we report DIRTY. Non-ignored untracked files count as drift because an
    untracked Python module or executable can alter runtime behavior. Generated
    evidence under gitignored outputs/log paths remains invisible to git status.
    """
    try:
        porcelain = subprocess.check_output(
            ["git", "status", "--porcelain", "--untracked-files=all"],
            cwd=str(Path(repo_root)),
            text=True,
            stderr=subprocess.DEVNULL,
        )
    except Exception:
        return True
    return bool(porcelain.strip())


def read_deployed_sha(deploy_state_path: Path | str) -> str | None:
    try:
        path = Path(deploy_state_path)
        if not path.exists():
            return None
        data = json.loads(path.read_text(encoding="utf-8"))
        return _norm(data.get("deployed_sha"))
    except Exception:
        return None


def read_deploy_state(deploy_state_path: Path | str) -> dict[str, Any] | None:
    try:
        payload = json.loads(Path(deploy_state_path).read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError):
        return None
    return payload if isinstance(payload, dict) else None


def deploy_attestation_valid(payload: Mapping[str, Any] | None) -> bool:
    if not isinstance(payload, Mapping):
        return False
    deployed = _norm(payload.get("deployed_sha"))
    validated = _norm(payload.get("validated_sha"))
    target = _norm(payload.get("target_sha"))
    return bool(
        payload.get("schema_version") == DEPLOY_STATE_SCHEMA
        and str(payload.get("validation_status") or "").upper() == "PASS"
        and str(payload.get("branch") or "") == "main"
        and str(payload.get("source_ref") or "") == "origin/main"
        and bool(str(payload.get("deployed_at") or "").strip())
        and bool(str(payload.get("validated_at") or "").strip())
        and bool(str(payload.get("validation_command") or "").strip())
        and deployed
        and _FULL_SHA_RE.fullmatch(deployed)
        and deployed == validated == target
    )


def resolve_sha_state(
    repo_root: Path | str,
    deploy_state_path: Path | str | None = None,
) -> ShaDriftVerdict:
    repo_root = Path(repo_root)
    if deploy_state_path is None:
        deploy_state_path = repo_root / "outputs" / "deploy_state.json"
    deploy_state = read_deploy_state(deploy_state_path)
    return evaluate_sha_drift(
        running_sha=resolve_running_sha(repo_root),
        deployed_sha=_norm((deploy_state or {}).get("deployed_sha")),
        tree_dirty=resolve_tree_dirty(repo_root),
        attestation_valid=deploy_attestation_valid(deploy_state),
    )


def write_deploy_state(
    deploy_state_path: Path | str,
    deployed_sha: str,
    branch: str | None = None,
    metadata: Mapping[str, Any] | None = None,
) -> Path:
    """Atomically record the deployed SHA (temp file + os.replace)."""
    import datetime as _dt
    import os as _os

    path = Path(deploy_state_path)
    path.parent.mkdir(parents=True, exist_ok=True)
    payload = {
        "deployed_sha": str(deployed_sha).strip(),
        "deployed_at": _dt.datetime.now(_dt.timezone.utc)
        .isoformat()
        .replace("+00:00", "Z"),
    }
    if branch:
        payload["branch"] = branch
    if metadata:
        protected = {"deployed_sha", "deployed_at", "branch"}
        payload.update(
            {
                str(key): value
                for key, value in metadata.items()
                if str(key) not in protected
            }
        )
    tmp = path.with_suffix(path.suffix + ".tmp")
    tmp.write_text(json.dumps(payload, indent=2, sort_keys=True) + "\n", encoding="utf-8")
    _os.replace(tmp, path)
    return path
