from __future__ import annotations

import hashlib
import json
import os
import shutil
import stat
import subprocess
import sys
import threading
from types import SimpleNamespace
from pathlib import Path

import pytest

from projects.alpha_lab.release import gate_a_handoff as handoff


def _canonical(value) -> bytes:
    return handoff._canonical(value)


def _file_record(path: str, value: bytes) -> dict:
    return {"bytes": len(value), "path": path, "sha256": hashlib.sha256(value).hexdigest()}


def _packet(tmp_path: Path) -> tuple[Path, Path, dict[str, str]]:
    staging = tmp_path / "staging"
    staging.mkdir()
    wheelhouse = staging / "wheelhouse"
    wheelhouse.mkdir()
    wheels = []
    for number in range(25):
        name = f"demo{number}-1-py3-none-any.whl"
        value = f"wheel-{number}\n".encode()
        (wheelhouse / name).write_bytes(value)
        wheels.append({"bytes": len(value), "filename": name, "sha256": hashlib.sha256(value).hexdigest()})
    source_tar = b"source archive"
    file_manifest = _canonical([])
    source_manifest_value = {
        "archive_bytes": len(source_tar),
        "archive_sha256": hashlib.sha256(source_tar).hexdigest(),
        "file_manifest_member_count": 0,
        "file_manifest_sha256": hashlib.sha256(file_manifest).hexdigest(),
    }
    source_manifest = _canonical(source_manifest_value)
    wheel_manifest = {"bytes": 1, "path": "manifest.json", "schema_version": "v1", "sha256": "a" * 64}
    release_value = {
        "dependencies": {
            "wheel_bytes_total": sum(item["bytes"] for item in wheels),
            "wheel_count": 25,
            "wheel_manifest": wheel_manifest,
            "wheels": wheels,
        },
        "source": {
            "source_manifest": source_manifest_value,
            "source_manifest_sha256": hashlib.sha256(source_manifest).hexdigest(),
        },
    }
    release = _canonical(release_value)
    bootstrap = b"print('bootstrap')\n"
    values = {
        "gate_a_bootstrap.py": bootstrap,
        "source.tar": source_tar,
        "source_manifest.json": source_manifest,
        "file_manifest.json": file_manifest,
        "release_input_manifest.json": release,
    }
    for name, value in values.items():
        (staging / name).write_bytes(value)
    core = [_file_record(name, value) for name, value in values.items()]
    summary = {
        "core_artifact_record_count": len(core),
        "core_artifact_records": core,
        "core_artifact_records_sha256": hashlib.sha256(_canonical(core)).hexdigest(),
        "dependencies": {
            "wheel_bytes_total": sum(item["bytes"] for item in wheels),
            "wheel_count": 25,
            "wheel_manifest": wheel_manifest,
            "wheel_records": wheels,
            "wheel_records_sha256": hashlib.sha256(_canonical(wheels)).hexdigest(),
        },
        "release_input": _file_record("release_input_manifest.json", release),
        "reviewed_tools": {"gate_a_bootstrap": _file_record("gate_a_bootstrap.py", bootstrap)},
        "schema_version": "caerus_alpha_lab_post_commit_identity_packet_v1",
        "source": {
            "archive": _file_record("source.tar", source_tar),
            "file_manifest": _file_record("file_manifest.json", file_manifest),
            "source_manifest": _file_record("source_manifest.json", source_manifest),
        },
        "status": "PASS",
    }
    summary_path = tmp_path / "identity_summary.json"
    summary_bytes = _canonical(summary)
    summary_path.write_bytes(summary_bytes)
    hashes = {
        "packet": hashlib.sha256(summary_bytes).hexdigest(),
        "archive": hashlib.sha256(source_tar).hexdigest(),
        "bootstrap": hashlib.sha256(bootstrap).hexdigest(),
        "release": hashlib.sha256(release).hexdigest(),
    }
    return staging, summary_path, hashes


def _transfer(tmp_path: Path, *, staging: Path | None = None, summary: Path | None = None, hashes=None):
    if staging is None or summary is None or hashes is None:
        staging, summary, hashes = _packet(tmp_path)
    protected = tmp_path / "protected"
    protected.mkdir(exist_ok=True)
    leaf = protected / "leaf"
    result = handoff.protected_transfer(
        staging=staging.absolute(), identity_summary=summary.absolute(),
        protected_leaf=leaf.absolute(),
        authorized_packet_summary_sha256=hashes["packet"],
        authorized_source_archive_sha256=hashes["archive"],
        authorized_bootstrap_sha256=hashes["bootstrap"],
        authorized_release_input_sha256=hashes["release"],
        authorized_handoff_tool_sha256=hashlib.sha256(Path(handoff.__file__).read_bytes()).hexdigest(),
        require_privileged=False,
        _enforce_protected_ancestors=False,
    )
    return leaf, result


def test_protected_transfer_is_exact_create_only_and_receipt_last(tmp_path: Path) -> None:
    staging, summary, hashes = _packet(tmp_path)
    leaf, receipt = _transfer(tmp_path, staging=staging, summary=summary, hashes=hashes)
    assert receipt["wheel_census"]["count"] == 25
    assert set(path.name for path in leaf.iterdir()) == handoff.EXPECTED_INPUTS | {handoff.RECEIPT_NAME}
    persisted = handoff._strict_json((leaf / handoff.RECEIPT_NAME).read_bytes(), label="receipt")
    assert persisted["target_root"] == str(leaf)
    assert persisted["seal_policy"] == {
        "directory_mode": "0555", "file_mode": "0444",
        "ownership_changed": False, "sealed_after_receipt_creation": True,
    }
    assert {record["mode"] for record in persisted["target_records"]} == {"0444"}
    assert stat.S_IMODE(leaf.stat().st_mode) == 0o555
    assert stat.S_IMODE((leaf / "wheelhouse").stat().st_mode) == 0o555
    for path in leaf.rglob("*"):
        expected = 0o555 if path.is_dir() else 0o444
        mode = stat.S_IMODE(path.lstat().st_mode)
        assert mode == expected
        # A distinct non-owner principal can traverse directories and read files,
        # but receives no write bit through owner/group/other mode classes.
        assert mode & 0o222 == 0
        assert mode & (0o001 if path.is_dir() else 0o004)
    with pytest.raises(handoff.HandoffError, match="absent"):
        _transfer(tmp_path, staging=staging, summary=summary, hashes=hashes)


@pytest.mark.parametrize("mutation", ["extra", "missing", "symlink", "hardlink", "extra-wheel", "tamper"])
def test_protected_transfer_rejects_adversarial_inputs(tmp_path: Path, mutation: str) -> None:
    staging, summary, hashes = _packet(tmp_path)
    if mutation == "extra":
        (staging / "extra").write_bytes(b"x")
    elif mutation == "missing":
        (staging / "file_manifest.json").unlink()
    elif mutation == "symlink":
        (staging / "source.tar").unlink()
        (staging / "source.tar").symlink_to("source_manifest.json")
    elif mutation == "hardlink":
        os.link(staging / "source.tar", tmp_path / "other-link")
    elif mutation == "extra-wheel":
        (staging / "wheelhouse" / "extra.whl").write_bytes(b"x")
    else:
        (staging / "source.tar").write_bytes(b"tampered")
    with pytest.raises(handoff.HandoffError):
        _transfer(tmp_path, staging=staging, summary=summary, hashes=hashes)


def test_protected_transfer_rejects_path_and_collision(tmp_path: Path) -> None:
    staging, summary, hashes = _packet(tmp_path)
    with pytest.raises(handoff.HandoffError, match="canonical absolute"):
        handoff.protected_transfer(
            staging=Path("relative"), identity_summary=summary.absolute(),
            protected_leaf=(tmp_path / "leaf").absolute(),
            authorized_packet_summary_sha256=hashes["packet"],
            authorized_source_archive_sha256=hashes["archive"],
            authorized_bootstrap_sha256=hashes["bootstrap"],
            authorized_release_input_sha256=hashes["release"], require_privileged=False,
            authorized_handoff_tool_sha256=hashlib.sha256(Path(handoff.__file__).read_bytes()).hexdigest(),
            _enforce_protected_ancestors=False,
        )
    (tmp_path / "protected").mkdir()
    (tmp_path / "protected" / "leaf").mkdir()
    with pytest.raises(handoff.HandoffError, match="absent"):
        _transfer(tmp_path, staging=staging, summary=summary, hashes=hashes)


def test_write_all_retries_short_writes(monkeypatch) -> None:
    writes = []

    def short(_fd, value):
        count = min(2, len(value))
        writes.append(bytes(value[:count]))
        return count

    monkeypatch.setattr(handoff.os, "write", short)
    handoff._write_all(9, b"abcdef")
    assert b"".join(writes) == b"abcdef"


def test_create_file_sets_explicit_owner_before_final_mode(
    tmp_path: Path, monkeypatch,
) -> None:
    calls = []
    original_fchmod = handoff.os.fchmod

    monkeypatch.setattr(
        handoff.os, "fchown",
        lambda _fd, uid, gid: calls.append(("owner", uid, gid)),
    )

    def recording_fchmod(fd, mode):
        calls.append(("mode", mode))
        original_fchmod(fd, mode)

    monkeypatch.setattr(handoff.os, "fchmod", recording_fchmod)
    parent_fd = handoff._open_dir(tmp_path.absolute())
    try:
        metadata = handoff._create_file_at(
            parent_fd, "receipt.json", b"{}", 0o440, owner=(0, 0)
        )
    finally:
        os.close(parent_fd)
    assert calls == [("owner", 0, 0), ("mode", 0o440)]
    assert stat.S_IMODE(metadata.st_mode) == 0o440


def test_required_file_probe_rejects_fifo_without_blocking(tmp_path: Path) -> None:
    directory = tmp_path / "fifo-input"
    directory.mkdir()
    os.mkfifo(directory / "source.tar")
    outcome = []

    def probe() -> None:
        parent_fd = handoff._open_dir(directory.absolute())
        try:
            handoff._read_regular_at(parent_fd, "source.tar", label="FIFO input")
        except BaseException as exc:  # captured for the timeout-backed assertion
            outcome.append(exc)
        finally:
            os.close(parent_fd)

    worker = threading.Thread(target=probe, daemon=True)
    worker.start()
    worker.join(timeout=1.0)
    assert not worker.is_alive(), "FIFO probe blocked before it could reject the input"
    assert len(outcome) == 1
    assert isinstance(outcome[0], handoff.HandoffError)
    assert "approved regular file" in str(outcome[0])


def test_regular_file_probe_restores_blocking_before_read(
    tmp_path: Path, monkeypatch,
) -> None:
    source = tmp_path / "regular.txt"
    source.write_bytes(b"ordinary bytes\n")
    calls = []
    original = handoff.os.set_blocking

    def recording(fd, blocking):
        calls.append(blocking)
        return original(fd, blocking)

    monkeypatch.setattr(handoff.os, "set_blocking", recording)
    parent_fd = handoff._open_dir(tmp_path.absolute())
    try:
        value, _metadata = handoff._read_regular_at(
            parent_fd, source.name, label="regular input"
        )
    finally:
        os.close(parent_fd)
    assert value == b"ordinary bytes\n"
    assert calls == [True]


def test_protected_transfer_rejects_source_race(tmp_path: Path, monkeypatch) -> None:
    staging, summary, hashes = _packet(tmp_path)
    original = handoff._create_file_at
    changed = False

    def racing(parent_fd, name, value, mode):
        nonlocal changed
        result = original(parent_fd, name, value, mode)
        if not changed:
            changed = True
            (staging / "source.tar").write_bytes((staging / "source.tar").read_bytes() + b"race")
        return result

    monkeypatch.setattr(handoff, "_create_file_at", racing)
    with pytest.raises(handoff.HandoffError, match="changed during transfer"):
        _transfer(tmp_path, staging=staging, summary=summary, hashes=hashes)


def test_protected_transfer_requires_separate_tool_authorization_and_root_hierarchy(
    tmp_path: Path,
) -> None:
    staging, summary, hashes = _packet(tmp_path)
    protected = tmp_path / "protected"
    protected.mkdir()
    arguments = dict(
        staging=staging.absolute(), identity_summary=summary.absolute(),
        protected_leaf=(protected / "leaf").absolute(),
        authorized_packet_summary_sha256=hashes["packet"],
        authorized_source_archive_sha256=hashes["archive"],
        authorized_bootstrap_sha256=hashes["bootstrap"],
        authorized_release_input_sha256=hashes["release"],
        require_privileged=False,
    )
    with pytest.raises(handoff.HandoffError, match="handoff-tool authorization"):
        handoff.protected_transfer(
            **arguments, authorized_handoff_tool_sha256="0" * 64,
            _enforce_protected_ancestors=False,
        )
    tool_hash = hashlib.sha256(Path(handoff.__file__).read_bytes()).hexdigest()
    with pytest.raises(handoff.HandoffError, match="root-owned"):
        handoff.protected_transfer(
            **arguments, authorized_handoff_tool_sha256=tool_hash,
        )


@pytest.mark.parametrize(
    "bad_summary",
    [b'{"x":1,"x":2}', b'{"x":NaN}'],
)
def test_protected_transfer_rejects_ambiguous_json(tmp_path: Path, bad_summary: bytes) -> None:
    staging, summary, hashes = _packet(tmp_path)
    summary.write_bytes(bad_summary)
    hashes["packet"] = hashlib.sha256(bad_summary).hexdigest()
    with pytest.raises(handoff.HandoffError):
        _transfer(tmp_path, staging=staging, summary=summary, hashes=hashes)


def _git_repo(tmp_path: Path) -> Path:
    repo = tmp_path / "repo"
    repo.mkdir()
    subprocess.run(["/usr/bin/git", "init", "-q", str(repo)], check=True)
    subprocess.run(["/usr/bin/git", "-C", str(repo), "config", "user.email", "test@example.invalid"], check=True)
    subprocess.run(["/usr/bin/git", "-C", str(repo), "config", "user.name", "Test"], check=True)
    (repo / "tracked.txt").write_text("old\n")
    (repo / "deleted.txt").write_text("delete\n")
    subprocess.run(["/usr/bin/git", "-C", str(repo), "add", "."], check=True)
    subprocess.run(["/usr/bin/git", "-C", str(repo), "commit", "-qm", "base"], check=True)
    return repo


def _dirty_snapshot(*, repo_root: Path, output: Path):
    """Exercise receipt semantics without pretending a portable test is root."""
    return handoff.dirty_snapshot(
        repo_root=repo_root,
        output=output,
        _require_root=False,
        _enforce_protected_output=False,
    )


def test_git_child_uses_exact_repo_owner_identity_fd_and_fixed_argv(
    monkeypatch,
) -> None:
    calls = []
    captured = {}

    monkeypatch.setattr(handoff.os, "setgroups", lambda value: calls.append(("groups", value)))
    monkeypatch.setattr(handoff.os, "setgid", lambda value: calls.append(("gid", value)))
    monkeypatch.setattr(handoff.os, "setuid", lambda value: calls.append(("uid", value)))
    monkeypatch.setattr(handoff.os, "getgroups", lambda: [])
    monkeypatch.setattr(handoff.os, "getgid", lambda: 456)
    monkeypatch.setattr(handoff.os, "getegid", lambda: 456)
    monkeypatch.setattr(handoff.os, "getuid", lambda: 123)
    monkeypatch.setattr(handoff.os, "geteuid", lambda: 123)
    monkeypatch.setattr(handoff.os, "fchdir", lambda value: calls.append(("fchdir", value)))

    def fake_run(argv, **kwargs):
        captured["argv"] = argv
        captured.update(kwargs)
        kwargs["preexec_fn"]()
        return SimpleNamespace(returncode=0, stdout=b"result\n", stderr=b"")

    monkeypatch.setattr(handoff.subprocess, "run", fake_run)
    result = handoff._git(
        19,
        ["status", "--porcelain=v1", "-z", "--untracked-files=normal"],
        principal={"uid": 123, "gid": 456, "supplementary_gids": []},
        drop_privileges=True,
    )

    assert result == b"result\n"
    assert calls == [
        ("groups", []), ("gid", 456), ("uid", 123), ("fchdir", 19),
    ]
    assert captured["argv"] == [
        "/usr/bin/git", "--no-replace-objects",
        "--git-dir=.git", "--work-tree=.",
        "-c", "core.fsmonitor=",
        "-c", "core.untrackedCache=false",
        "status", "--porcelain=v1", "-z", "--untracked-files=normal",
    ]
    assert "safe.directory" not in " ".join(captured["argv"])
    assert "-C" not in captured["argv"]
    assert "core.fsmonitor=false" not in captured["argv"]
    assert captured["env"] == handoff.GIT_ENVIRONMENT
    assert set(captured["env"]) == {
        "GIT_CONFIG_GLOBAL", "GIT_CONFIG_NOSYSTEM", "GIT_OPTIONAL_LOCKS",
        "GIT_TERMINAL_PROMPT", "LANG", "LC_ALL",
    }
    assert "SUDO_UID" not in captured["env"]
    assert "HOME" not in captured["env"]
    assert captured["pass_fds"] == (19,)


@pytest.mark.parametrize("uid,gid", [(0, 456), (123, 0)])
def test_repository_principal_rejects_any_root_identity(uid: int, gid: int) -> None:
    metadata = SimpleNamespace(st_mode=stat.S_IFDIR | 0o755, st_uid=uid, st_gid=gid)
    with pytest.raises(handoff.HandoffError, match="non-root owner and group"):
        handoff._repository_principal(metadata)


def test_dirty_snapshot_production_path_requires_root(tmp_path: Path, monkeypatch) -> None:
    monkeypatch.setattr(handoff.os, "geteuid", lambda: 501)
    with pytest.raises(handoff.HandoffError, match="root receipt principal"):
        handoff.dirty_snapshot(
            repo_root=(tmp_path / "repo").absolute(),
            output=(tmp_path / "receipt.json").absolute(),
        )


def test_dirty_snapshot_rejects_output_inside_checkout(tmp_path: Path) -> None:
    repo = _git_repo(tmp_path)
    with pytest.raises(handoff.HandoffError, match="outside the repository"):
        _dirty_snapshot(
            repo_root=repo.absolute(), output=(repo / "receipt.json").absolute()
        )


def test_dirty_snapshot_rejects_parent_repository_discovery(tmp_path: Path) -> None:
    repo = _git_repo(tmp_path)
    nested = repo / "nested"
    nested.mkdir()
    with pytest.raises(handoff.HandoffError, match="in-root .git directory"):
        _dirty_snapshot(
            repo_root=nested.absolute(), output=(tmp_path / "nested.json").absolute()
        )


def test_dirty_snapshot_overrides_worktree_redirect_and_fsmonitor_hook(
    tmp_path: Path,
) -> None:
    repo = _git_repo(tmp_path)
    redirected = tmp_path / "redirected-worktree"
    redirected.mkdir()
    (redirected / "tracked.txt").write_text("old\n")
    hook = tmp_path / "malicious-fsmonitor"
    marker = Path(f"{hook}.ran")
    hook.write_text('#!/bin/sh\n: > "$0.ran"\nexit 0\n')
    hook.chmod(0o755)
    subprocess.run(
        [
            "/usr/bin/git", f"--git-dir={repo / '.git'}", "config",
            "core.worktree", str(redirected),
        ],
        check=True,
    )
    subprocess.run(
        [
            "/usr/bin/git", f"--git-dir={repo / '.git'}", "config",
            "core.fsmonitor", str(hook),
        ],
        check=True,
    )
    (repo / "tracked.txt").write_text("new\n")
    subprocess.run(
        [
            "/usr/bin/git", f"--git-dir={repo / '.git'}",
            f"--work-tree={redirected}", "status", "--porcelain=v1",
        ],
        check=True,
        stdout=subprocess.PIPE,
        stderr=subprocess.PIPE,
    )
    assert marker.exists(), "adversarial fixture did not invoke its configured hook"
    marker.unlink()

    receipt = _dirty_snapshot(
        repo_root=repo.absolute(), output=(tmp_path / "override.json").absolute()
    )

    assert not marker.exists()
    records = {record["path"]: record for record in receipt["records"]}
    assert records["tracked.txt"]["status"] == " M"
    assert records["tracked.txt"]["sha256"] == hashlib.sha256(b"new\n").hexdigest()
    assert receipt["git"]["top_level"] == str(repo)


def test_dirty_snapshot_records_nested_file_under_deleted_parent_as_absent(
    tmp_path: Path,
) -> None:
    repo = _git_repo(tmp_path)
    nested = repo / "quant_research_agent"
    nested.mkdir()
    tracked = nested / "requirements.txt"
    tracked.write_text("legacy dependency\n")
    subprocess.run(["/usr/bin/git", "-C", str(repo), "add", "."], check=True)
    subprocess.run(
        ["/usr/bin/git", "-C", str(repo), "commit", "-qm", "nested"],
        check=True,
    )
    shutil.rmtree(nested)

    receipt = _dirty_snapshot(
        repo_root=repo.absolute(), output=(tmp_path / "deleted-parent.json").absolute()
    )

    assert {
        "path": "quant_research_agent/requirements.txt",
        "status": " D",
        "type": "absent",
    } in receipt["records"]


@pytest.mark.parametrize("intermediate_kind", ["symlink", "file", "fifo"])
def test_dirty_record_rejects_unsafe_intermediate_component(
    tmp_path: Path, intermediate_kind: str,
) -> None:
    root = tmp_path / "root"
    root.mkdir()
    intermediate = root / "nested"
    if intermediate_kind == "symlink":
        outside = tmp_path / "outside"
        outside.mkdir()
        (outside / "tracked.txt").write_text("outside\n")
        intermediate.symlink_to(outside, target_is_directory=True)
    elif intermediate_kind == "file":
        intermediate.write_text("not a directory\n")
    else:
        os.mkfifo(intermediate)
    root_fd = handoff._open_dir(root.absolute())
    try:
        with pytest.raises(handoff.HandoffError, match="unsafe intermediate"):
            handoff._read_path_record(
                root_fd,
                ("nested", "tracked.txt"),
                path="nested/tracked.txt",
                status_code=" D",
            )
    finally:
        os.close(root_fd)


def test_dirty_snapshot_expands_untracked_and_exact_compare(tmp_path: Path) -> None:
    repo = _git_repo(tmp_path)
    (repo / ".gitignore").write_text("*.secret\n")
    (repo / "tracked.txt").write_text("new\n")
    (repo / "deleted.txt").unlink()
    nested = repo / "newdir" / "nested"
    nested.mkdir(parents=True)
    (nested / "visible.txt").write_text("visible\n")
    (nested / "hidden.secret").write_text("hidden\n")
    (repo / "newdir" / "link").symlink_to("nested/visible.txt")
    before = tmp_path / "before.json"
    after = tmp_path / "after.json"
    first = _dirty_snapshot(repo_root=repo.absolute(), output=before.absolute())
    second = _dirty_snapshot(repo_root=repo.absolute(), output=after.absolute())
    paths = {item["path"]: item for item in first["records"]}
    assert paths["deleted.txt"]["type"] == "absent"
    assert paths["newdir/nested/hidden.secret"]["type"] == "file"
    assert paths["newdir/link"]["type"] == "symlink"
    semantic = first["semantic_snapshot"]
    assert semantic["repo_root"] == str(repo)
    assert semantic["record_count"] == len(first["records"])
    assert semantic["total_file_bytes"] == sum(
        item["bytes"] for item in first["records"] if item["type"] == "file"
    )
    assert semantic["git_tool"] == first["git"]["tool"]
    assert semantic["git_inspection_principal"] == first["git"][
        "inspection_principal"
    ]
    assert semantic["git_top_level"] == str(repo)
    assert first["git"]["top_level"] == str(repo)
    assert first["git"]["inspection_principal"] == {
        "gid": repo.stat().st_gid,
        "supplementary_gids": [],
        "uid": repo.stat().st_uid,
    }
    assert semantic["git_tool"]["path"] == "/usr/bin/git"
    assert set(semantic["git_tool"]) == {
        "bytes", "gid", "mode", "nlink", "path", "sha256", "uid",
    }
    assert first["semantic_snapshot_sha256"] == second["semantic_snapshot_sha256"]
    assert handoff.compare_snapshots(before=before.absolute(), after=after.absolute())["status"] == "EQUAL"
    with pytest.raises(handoff.HandoffError, match="exclusively create"):
        _dirty_snapshot(repo_root=repo.absolute(), output=before.absolute())


def test_dirty_snapshot_compare_detects_tamper_and_change(tmp_path: Path) -> None:
    repo = _git_repo(tmp_path)
    (repo / "tracked.txt").write_text("before\n")
    before = tmp_path / "before.json"
    after = tmp_path / "after.json"
    _dirty_snapshot(repo_root=repo.absolute(), output=before.absolute())
    (repo / "tracked.txt").write_text("after\n")
    _dirty_snapshot(repo_root=repo.absolute(), output=after.absolute())
    with pytest.raises(handoff.HandoffError, match="differ"):
        handoff.compare_snapshots(before=before.absolute(), after=after.absolute())
    value = json.loads(before.read_text())
    value["semantic_snapshot_sha256"] = "0" * 64
    before.chmod(0o644)
    before.write_bytes(_canonical(value))
    with pytest.raises(handoff.HandoffError, match="semantic hash"):
        handoff.compare_snapshots(before=before.absolute(), after=after.absolute())


def test_dirty_snapshot_binds_repository_root(tmp_path: Path) -> None:
    first_repo = _git_repo(tmp_path)
    (first_repo / "tracked.txt").write_text("dirty\n")
    second_repo = tmp_path / "repo-copy"
    shutil.copytree(first_repo, second_repo, symlinks=True)
    first_path = tmp_path / "first-root.json"
    second_path = tmp_path / "second-root.json"
    first = _dirty_snapshot(repo_root=first_repo.absolute(), output=first_path.absolute())
    second = _dirty_snapshot(repo_root=second_repo.absolute(), output=second_path.absolute())
    assert first["git"]["head"] == second["git"]["head"]
    assert first["git"]["porcelain_sha256"] == second["git"]["porcelain_sha256"]
    assert first["semantic_snapshot_sha256"] != second["semantic_snapshot_sha256"]
    with pytest.raises(handoff.HandoffError, match="differ"):
        handoff.compare_snapshots(before=first_path.absolute(), after=second_path.absolute())


def test_dirty_snapshot_rejects_git_race(tmp_path: Path, monkeypatch) -> None:
    repo = _git_repo(tmp_path)
    (repo / "tracked.txt").write_text("dirty\n")
    original = handoff._git
    first_status = True

    def racing(repo_fd, arguments, **kwargs):
        nonlocal first_status
        result = original(repo_fd, arguments, **kwargs)
        if first_status and arguments and arguments[0] == "status":
            first_status = False
            (repo / "appeared.txt").write_text("race\n")
        return result

    monkeypatch.setattr(handoff, "_git", racing)
    with pytest.raises(handoff.HandoffError, match="changed during dirty scan"):
        _dirty_snapshot(
            repo_root=repo.absolute(), output=(tmp_path / "race.json").absolute()
        )


def test_dirty_snapshot_rejects_content_race_with_unchanged_porcelain(
    tmp_path: Path, monkeypatch,
) -> None:
    repo = _git_repo(tmp_path)
    tracked = repo / "tracked.txt"
    tracked.write_text("first dirty value\n")
    original = handoff._read_path_record
    changed = False

    def racing(root_fd, parts, *, path, status_code):
        nonlocal changed
        record = original(
            root_fd, parts, path=path, status_code=status_code
        )
        if path == "tracked.txt" and not changed:
            changed = True
            tracked.write_text("second dirty value\n")
        return record

    monkeypatch.setattr(handoff, "_read_path_record", racing)
    with pytest.raises(handoff.HandoffError, match="material records changed"):
        _dirty_snapshot(
            repo_root=repo.absolute(),
            output=(tmp_path / "content-race.json").absolute(),
        )


def test_dirty_snapshot_rejects_parent_disappearance_race(
    tmp_path: Path, monkeypatch,
) -> None:
    repo = _git_repo(tmp_path)
    nested = repo / "nested"
    nested.mkdir()
    tracked = nested / "tracked.txt"
    tracked.write_text("old\n")
    subprocess.run(["/usr/bin/git", "-C", str(repo), "add", "."], check=True)
    subprocess.run(
        ["/usr/bin/git", "-C", str(repo), "commit", "-qm", "nested"],
        check=True,
    )
    tracked.write_text("dirty\n")
    original = handoff._read_path_record
    removed = False

    def racing(root_fd, parts, *, path, status_code):
        nonlocal removed
        if path == "nested/tracked.txt" and not removed:
            removed = True
            shutil.rmtree(nested)
        return original(root_fd, parts, path=path, status_code=status_code)

    monkeypatch.setattr(handoff, "_read_path_record", racing)
    with pytest.raises(handoff.HandoffError, match="changed during dirty scan"):
        _dirty_snapshot(
            repo_root=repo.absolute(),
            output=(tmp_path / "parent-race.json").absolute(),
        )


@pytest.mark.parametrize("field", ["records", "repo_root", "scanner", "git", "extra"])
def test_compare_rejects_top_level_duplicate_drift(tmp_path: Path, field: str) -> None:
    repo = _git_repo(tmp_path)
    (repo / "tracked.txt").write_text("dirty\n")
    before = tmp_path / "before-strict.json"
    after = tmp_path / "after-strict.json"
    _dirty_snapshot(repo_root=repo.absolute(), output=before.absolute())
    _dirty_snapshot(repo_root=repo.absolute(), output=after.absolute())
    value = json.loads(after.read_text())
    if field == "records":
        value[field] = []
    elif field == "repo_root":
        value[field] = str(tmp_path.absolute())
    elif field == "scanner":
        value[field] = {**value[field], "version": "drift"}
    elif field == "git":
        value[field] = {**value[field], "head": "0" * 40}
    else:
        value[field] = "not allowed"
    after.chmod(0o644)
    after.write_bytes(_canonical(value))
    with pytest.raises(handoff.HandoffError):
        handoff.compare_snapshots(before=before.absolute(), after=after.absolute())


def test_compare_rejects_git_principal_drift(tmp_path: Path) -> None:
    repo = _git_repo(tmp_path)
    (repo / "tracked.txt").write_text("dirty\n")
    before = tmp_path / "before-principal.json"
    after = tmp_path / "after-principal.json"
    _dirty_snapshot(repo_root=repo.absolute(), output=before.absolute())
    _dirty_snapshot(repo_root=repo.absolute(), output=after.absolute())
    value = json.loads(after.read_text())
    value["git"]["inspection_principal"]["uid"] = 0
    value["semantic_snapshot"]["git_inspection_principal"]["uid"] = 0
    value["semantic_snapshot_sha256"] = hashlib.sha256(
        _canonical(value["semantic_snapshot"])
    ).hexdigest()
    after.chmod(0o644)
    after.write_bytes(_canonical(value))
    with pytest.raises(handoff.HandoffError, match="inspection principal"):
        handoff.compare_snapshots(before=before.absolute(), after=after.absolute())


def _documented_installer() -> str:
    runbook = (
        Path(__file__).parents[1] / "RESEARCH_SIGNING_CEREMONY.md"
    ).read_text(encoding="utf-8")
    marker = '"$REVIEWED_HANDOFF" "$HANDOFF_TOOL" "$HANDOFF_SHA" <<\'PY\'\n'
    assert runbook.count(marker) == 1
    before_installer, after_marker = runbook.split(marker, 1)
    install_block_prefix = before_installer.rsplit("```bash\n", 1)[1]
    assert '"$REVIEWED_HANDOFF"' not in install_block_prefix
    installer = after_marker.split("\nPY\n", 1)[0]
    assert "os.O_EXCL" in installer
    assert "oflag=excl" not in runbook
    return installer


def test_documented_tool_installer_is_exclusive_and_target_portable(
    tmp_path: Path,
) -> None:
    installer = _documented_installer()
    source = tmp_path / "reviewed.py"
    target = tmp_path / "installed.py"
    payload = b"print('reviewed')\n"
    source.write_bytes(payload)
    digest = hashlib.sha256(payload).hexdigest()
    command = [
        sys.executable,
        "-I",
        "-S",
        "-B",
        "-",
        str(source),
        str(target),
        digest,
    ]
    first = subprocess.run(
        command,
        input=installer,
        text=True,
        capture_output=True,
        timeout=5,
        check=False,
    )
    assert first.returncode == 0, first.stderr
    assert target.read_bytes() == payload
    assert stat.S_IMODE(target.stat().st_mode) == 0o444
    assert target.stat().st_nlink == 1

    second = subprocess.run(
        command,
        input=installer,
        text=True,
        capture_output=True,
        timeout=5,
        check=False,
    )
    assert second.returncode != 0
    assert target.read_bytes() == payload


def test_documented_tool_installer_rejects_fifo_without_blocking(
    tmp_path: Path,
) -> None:
    installer = _documented_installer()
    source = tmp_path / "reviewed-fifo"
    target = tmp_path / "installed.py"
    os.mkfifo(source)
    result = subprocess.run(
        [
            sys.executable,
            "-I",
            "-S",
            "-B",
            "-",
            str(source),
            str(target),
            "0" * 64,
        ],
        input=installer,
        text=True,
        capture_output=True,
        timeout=5,
        check=False,
    )
    assert result.returncode != 0
    assert not target.exists()
