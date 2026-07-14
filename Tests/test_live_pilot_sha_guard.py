"""Tests for the BLOCKER 4 deploy-SHA drift guard (core.live_pilot_sha_guard).

The verdict logic is a pure function so drift/dirty/unresolved scenarios are
exercised without a real checkout; a couple of integration tests drive the
resolver against a throwaway git repo + deploy_state.json.
"""

from __future__ import annotations

import json
import subprocess
from pathlib import Path

import pytest

from core.live_pilot_sha_guard import (
    REASON_DEPLOY_SHA_DRIFT,
    REASON_DEPLOY_SHA_UNKNOWN,
    REASON_RUNNING_SHA_UNAVAILABLE,
    REASON_WORKING_TREE_DIRTY,
    evaluate_sha_drift,
    read_deployed_sha,
    resolve_sha_state,
    write_deploy_state,
)

FULL = "5277cea75b625d55b5244d1d5e1255c88f8e897f"
SHORT = "5277cea"
OTHER = "2a995bcabcdef0123456789abcdef0123456789a"


# --------------------------------------------------------------------------- #
# Pure verdict logic
# --------------------------------------------------------------------------- #
def test_pin_verified_full_match_clean_tree_allows_submit():
    v = evaluate_sha_drift(FULL, FULL, tree_dirty=False)
    assert v.block_submit is False
    assert v.sha_drift is False
    assert v.reason_code is None
    assert "pin verified" in v.message


def test_short_deploy_marker_matches_full_head():
    # deploy.sh historically wrote `git rev-parse --short HEAD`; short/full must
    # be treated as the same commit (prefix equivalence), not drift.
    v = evaluate_sha_drift(FULL, SHORT, tree_dirty=False)
    assert v.block_submit is False
    assert v.sha_drift is False


def test_sha_drift_blocks_submit():
    v = evaluate_sha_drift(FULL, OTHER, tree_dirty=False)
    assert v.sha_drift is True
    assert v.block_submit is True
    assert REASON_DEPLOY_SHA_DRIFT in v.reason_code
    assert "NOT the audited/deployed SHA" in v.message


def test_dirty_tree_blocks_submit_even_when_sha_matches():
    # A floating uncommitted tree defeats pinning even if HEAD == deployed_sha.
    v = evaluate_sha_drift(FULL, FULL, tree_dirty=True)
    assert v.sha_drift is False
    assert v.tree_dirty is True
    assert v.block_submit is True
    assert REASON_WORKING_TREE_DIRTY in v.reason_code


def test_missing_deploy_marker_fails_closed():
    v = evaluate_sha_drift(FULL, None, tree_dirty=False)
    assert v.block_submit is True
    assert REASON_DEPLOY_SHA_UNKNOWN in v.reason_code
    # Cannot confirm a match against a missing marker -> not counted as "drift"
    # but still blocks (unknown).
    assert v.sha_drift is False


def test_unresolvable_running_sha_fails_closed():
    v = evaluate_sha_drift(None, FULL, tree_dirty=False)
    assert v.block_submit is True
    assert REASON_RUNNING_SHA_UNAVAILABLE in v.reason_code


def test_multiple_reasons_accumulate():
    v = evaluate_sha_drift(FULL, OTHER, tree_dirty=True)
    assert v.block_submit is True
    assert REASON_DEPLOY_SHA_DRIFT in v.reason_code
    assert REASON_WORKING_TREE_DIRTY in v.reason_code


def test_whitespace_and_case_are_normalized():
    v = evaluate_sha_drift("  " + FULL.upper() + "\n", FULL, tree_dirty=False)
    assert v.block_submit is False


# --------------------------------------------------------------------------- #
# read_deployed_sha
# --------------------------------------------------------------------------- #
def test_read_deployed_sha_missing_file(tmp_path: Path):
    assert read_deployed_sha(tmp_path / "nope.json") is None


def test_read_deployed_sha_roundtrip(tmp_path: Path):
    p = write_deploy_state(tmp_path / "deploy_state.json", FULL, branch="release/x")
    assert read_deployed_sha(p) == FULL
    data = json.loads(p.read_text())
    assert data["deployed_sha"] == FULL
    assert data["branch"] == "release/x"
    assert data["deployed_at"].endswith("Z")


def test_read_deployed_sha_malformed(tmp_path: Path):
    p = tmp_path / "deploy_state.json"
    p.write_text("{not json", encoding="utf-8")
    assert read_deployed_sha(p) is None


# --------------------------------------------------------------------------- #
# resolve_sha_state against a real throwaway git repo
# --------------------------------------------------------------------------- #
def _git(repo: Path, *args: str) -> str:
    return subprocess.check_output(
        ["git", *args], cwd=str(repo), text=True, stderr=subprocess.DEVNULL
    ).strip()


@pytest.fixture()
def git_repo(tmp_path: Path) -> Path:
    repo = tmp_path / "repo"
    repo.mkdir()
    _git(repo, "init", "-q")
    _git(repo, "config", "user.email", "t@t.t")
    _git(repo, "config", "user.name", "t")
    (repo / "a.txt").write_text("hello\n", encoding="utf-8")
    _git(repo, "add", "-A")
    _git(repo, "commit", "-q", "-m", "init")
    return repo


def test_resolve_clean_tree_matching_deploy_allows(git_repo: Path):
    head = _git(git_repo, "rev-parse", "HEAD")
    ds = git_repo / "outputs" / "deploy_state.json"
    write_deploy_state(ds, head)
    v = resolve_sha_state(git_repo, ds)
    assert v.block_submit is False
    assert v.running_sha == head
    assert v.tree_dirty is False


def test_resolve_dirty_tree_blocks(git_repo: Path):
    head = _git(git_repo, "rev-parse", "HEAD")
    ds = git_repo / "outputs" / "deploy_state.json"
    write_deploy_state(ds, head)
    (git_repo / "a.txt").write_text("dirty change\n", encoding="utf-8")
    v = resolve_sha_state(git_repo, ds)
    assert v.tree_dirty is True
    assert v.block_submit is True
    assert REASON_WORKING_TREE_DIRTY in v.reason_code


def test_resolve_drift_blocks(git_repo: Path):
    ds = git_repo / "outputs" / "deploy_state.json"
    write_deploy_state(ds, OTHER)  # deploy marker names a different commit
    v = resolve_sha_state(git_repo, ds)
    assert v.sha_drift is True
    assert v.block_submit is True


def test_resolve_untracked_artifacts_do_not_count_as_dirty(git_repo: Path):
    # outputs/ (gitignored runtime evidence) must not trip the dirty guard.
    head = _git(git_repo, "rev-parse", "HEAD")
    ds = git_repo / "outputs" / "deploy_state.json"
    write_deploy_state(ds, head)
    (git_repo / "runtime_artifact.log").write_text("evidence\n", encoding="utf-8")
    v = resolve_sha_state(git_repo, ds)
    assert v.tree_dirty is False
    assert v.block_submit is False
