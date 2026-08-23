from __future__ import annotations

import hashlib
import io
import os
import stat
import subprocess
import sys
import tarfile
from pathlib import Path

import pytest

from projects.alpha_lab.factory import release_build as gate
from projects.alpha_lab.factory import ceremony
from projects.alpha_lab.release import gate_a_bootstrap as bootstrap


def _canonical(value) -> bytes:
    return gate._canonical_bytes(value)


def _git_tree_oid(files: dict[str, tuple[int, bytes]]) -> str:
    tree: dict[str, object] = {}
    for path, (mode, content) in files.items():
        node = tree
        parts = Path(path).parts
        for part in parts[:-1]:
            node = node.setdefault(part, {})  # type: ignore[assignment]
        blob = hashlib.sha1(
            f"blob {len(content)}\0".encode("ascii") + content
        ).digest()
        node[parts[-1]] = ("100755" if mode & 0o111 else "100644", blob)

    def build(node: dict[str, object]) -> bytes:
        entries = []
        for name, value in node.items():
            is_tree = isinstance(value, dict)
            entries.append((name.encode() + (b"/" if is_tree else b""), name, value))
        body = bytearray()
        for _key, name, value in sorted(entries, key=lambda item: item[0]):
            if isinstance(value, dict):
                mode, digest = "40000", build(value)
            else:
                mode, digest = value
            body.extend(mode.encode() + b" " + name.encode() + b"\0" + digest)
        return hashlib.sha1(
            f"tree {len(body)}\0".encode("ascii") + bytes(body)
        ).digest()

    return build(tree).hex()


def _source_bundle(tmp_path: Path) -> tuple[Path, Path, Path]:
    archive_path = tmp_path / "source.tar"
    content = b"exact source\n"
    with tarfile.open(
        archive_path, "w", format=tarfile.PAX_FORMAT,
        pax_headers={"comment": "1" * 40},
    ) as archive:
        directory = tarfile.TarInfo("pkg")
        directory.type = tarfile.DIRTYPE
        directory.mode = 0o755
        archive.addfile(directory)
        member = tarfile.TarInfo("pkg/module.py")
        member.mode = 0o644
        member.size = len(content)
        archive.addfile(member, io.BytesIO(content))
    file_manifest = [
        {
            "bytes": len(content),
            "mode": "0644",
            "path": "pkg/module.py",
            "sha256": hashlib.sha256(content).hexdigest(),
            "type": "file",
        }
    ]
    file_bytes = _canonical(file_manifest)
    archive_bytes = archive_path.read_bytes()
    source_manifest = {
        "archive_bytes": len(archive_bytes),
        "archive_format": "git-archive-tar",
        "archive_sha256": hashlib.sha256(archive_bytes).hexdigest(),
        "commit_sha": "1" * 40,
        "file_manifest_member_count": 1,
        "file_manifest_schema": gate.FILE_MANIFEST_SCHEMA,
        "file_manifest_sha256": hashlib.sha256(file_bytes).hexdigest(),
        "schema_version": gate.SOURCE_SCHEMA,
        "tree_oid_sha1": _git_tree_oid({"pkg/module.py": (0o644, content)}),
    }
    source_path = tmp_path / "source-manifest.json"
    files_path = tmp_path / "file-manifest.json"
    source_path.write_bytes(_canonical(source_manifest))
    files_path.write_bytes(file_bytes)
    return archive_path, source_path, files_path


def _malicious_tar(tmp_path: Path, kind: str) -> Path:
    result = tmp_path / f"{kind}.tar"
    with tarfile.open(result, "w", format=tarfile.USTAR_FORMAT) as archive:
        if kind == "traversal":
            member = tarfile.TarInfo("../escape")
            member.size = 1
            archive.addfile(member, io.BytesIO(b"x"))
        elif kind == "absolute":
            member = tarfile.TarInfo("/escape")
            member.size = 1
            archive.addfile(member, io.BytesIO(b"x"))
        elif kind == "symlink":
            member = tarfile.TarInfo("link")
            member.type = tarfile.SYMTYPE
            member.linkname = "target"
            archive.addfile(member)
        elif kind == "hardlink":
            member = tarfile.TarInfo("hard")
            member.type = tarfile.LNKTYPE
            member.linkname = "target"
            archive.addfile(member)
        elif kind == "fifo":
            member = tarfile.TarInfo("fifo")
            member.type = tarfile.FIFOTYPE
            archive.addfile(member)
        elif kind == "duplicate":
            for value in (b"x", b"y"):
                member = tarfile.TarInfo("same")
                member.size = 1
                archive.addfile(member, io.BytesIO(value))
        elif kind == "prefix":
            member = tarfile.TarInfo("a")
            member.size = 1
            archive.addfile(member, io.BytesIO(b"x"))
            directory = tarfile.TarInfo("a/b")
            directory.type = tarfile.DIRTYPE
            archive.addfile(directory)
        else:  # pragma: no cover - test helper guard
            raise AssertionError(kind)
    return result


def _sealed_payload(tmp_path: Path):
    release = tmp_path / "release"
    (release / "app").mkdir(parents=True)
    (release / "venv/bin").mkdir(parents=True)
    (release / "app/code.py").write_bytes(b"pass\n")
    (release / "venv/bin/python").write_bytes(b"python")
    (release / "venv/bin/python").chmod(0o755)
    root_fd = gate._open_absolute_directory(release)
    try:
        gate._seal_tree_fd(root_fd)
        records = gate._scan_tree_fd(root_fd, exclude_root_names=gate._METADATA_NAMES)
    finally:
        os.close(root_fd)
    source = gate.SourceBundle(
        archive_path=tmp_path / "unused.tar",
        archive_bytes=0,
        archive_sha256="a" * 64,
        archive_records=(),
        archive_directories=(),
        source_manifest={},
        source_manifest_bytes=b"{}",
        source_manifest_sha256="b" * 64,
        file_manifest=(),
        file_manifest_bytes=b"[]",
        file_manifest_sha256="c" * 64,
    )
    inputs = gate.ReleaseInputs(
        source=source,
        release_input={},
        release_input_bytes=b"{}",
        release_input_sha256="d" * 64,
        wheelhouse=tmp_path,
        release_parent=tmp_path,
        repo_root=tmp_path,
        lock_bytes=b"lock",
        wheel_manifest_bytes=b"manifest",
        builder_origin={"content_addressed": True},
    )
    manifest = gate._build_manifest_payload(
        inputs=inputs, records=records, runtime_evidence={}, source_store={}
    )
    return release, manifest


def _bootstrap_packet(tmp_path: Path):
    archive_path = tmp_path / "bootstrap-source.tar"
    bootstrap_bytes = Path(bootstrap.__file__).read_bytes()
    with tarfile.open(
        archive_path, "w", format=tarfile.PAX_FORMAT,
        pax_headers={"comment": "1" * 40},
    ) as archive:
        for directory_name in ("projects", "projects/alpha_lab", "projects/alpha_lab/release"):
            directory = tarfile.TarInfo(directory_name)
            directory.type = tarfile.DIRTYPE
            directory.mode = 0o755
            archive.addfile(directory)
        member = tarfile.TarInfo(bootstrap.BOOTSTRAP_RELATIVE_PATH)
        member.mode = 0o644
        member.size = len(bootstrap_bytes)
        archive.addfile(member, io.BytesIO(bootstrap_bytes))
    records = [
        {
            "bytes": len(bootstrap_bytes),
            "mode": "0644",
            "path": bootstrap.BOOTSTRAP_RELATIVE_PATH,
            "sha256": hashlib.sha256(bootstrap_bytes).hexdigest(),
            "type": "file",
        }
    ]
    file_bytes = bootstrap._canonical(records)
    archive_bytes = archive_path.read_bytes()
    source = {
        "archive_bytes": len(archive_bytes),
        "archive_format": "git-archive-tar",
        "archive_sha256": hashlib.sha256(archive_bytes).hexdigest(),
        "commit_sha": "1" * 40,
        "file_manifest_member_count": 1,
        "file_manifest_schema": bootstrap.FILE_MANIFEST_SCHEMA,
        "file_manifest_sha256": hashlib.sha256(file_bytes).hexdigest(),
        "schema_version": bootstrap.SOURCE_SCHEMA,
        "tree_oid_sha1": _git_tree_oid(
            {bootstrap.BOOTSTRAP_RELATIVE_PATH: (0o644, bootstrap_bytes)}
        ),
    }
    source_path = tmp_path / "bootstrap-source-manifest.json"
    file_path = tmp_path / "bootstrap-file-manifest.json"
    source_path.write_bytes(bootstrap._canonical(source))
    file_path.write_bytes(file_bytes)
    return archive_path, source_path, file_path, source, records[0]["sha256"]


def test_exact_source_bundle_and_extraction_pass(tmp_path: Path) -> None:
    archive, source, files = _source_bundle(tmp_path)
    bundle = gate.verify_source_bundle(
        archive_path=archive, source_manifest_path=source, file_manifest_path=files
    )
    app = tmp_path / "app"
    app.mkdir()
    descriptor = gate._open_absolute_directory(app)
    try:
        gate._extract_tar_exact(bundle, descriptor)
    finally:
        os.close(descriptor)
    assert (app / "pkg/module.py").read_bytes() == b"exact source\n"


@pytest.mark.parametrize(
    ("field", "message"),
    [("commit_sha", "PAX commit"), ("tree_oid_sha1", "Git tree OID")],
)
def test_source_git_provenance_labels_are_derived_not_asserted(
    tmp_path: Path, field: str, message: str,
) -> None:
    archive, source, files = _source_bundle(tmp_path)
    value = gate._strict_json(source.read_bytes(), label="source")
    value[field] = "f" * 40
    source.write_bytes(_canonical(value))
    with pytest.raises(gate.ReleaseBuildError, match=message):
        gate.verify_source_bundle(
            archive_path=archive, source_manifest_path=source,
            file_manifest_path=files,
        )


def test_separately_authorized_bootstrap_is_create_only_and_idempotent(
    tmp_path: Path,
) -> None:
    archive, source, files, source_record, bootstrap_sha = _bootstrap_packet(tmp_path)
    release_parent = tmp_path / "release-parent"
    dry = bootstrap.bootstrap(
        archive=archive,
        source_manifest=source,
        file_manifest=files,
        release_parent=release_parent,
        authorized_source_archive_sha256=source_record["archive_sha256"],
        authorized_bootstrap_sha256=bootstrap_sha,
        write=False,
    )
    assert dry["status"] == "DRY_RUN_VERIFIED"
    assert not release_parent.exists()
    first = bootstrap.bootstrap(
        archive=archive,
        source_manifest=source,
        file_manifest=files,
        release_parent=release_parent,
        authorized_source_archive_sha256=source_record["archive_sha256"],
        authorized_bootstrap_sha256=bootstrap_sha,
        write=True,
    )
    second = bootstrap.bootstrap(
        archive=archive,
        source_manifest=source,
        file_manifest=files,
        release_parent=release_parent,
        authorized_source_archive_sha256=source_record["archive_sha256"],
        authorized_bootstrap_sha256=bootstrap_sha,
        write=True,
    )
    assert first == second
    assert first["status"] == "READY"


def test_incomplete_bootstrap_collision_is_never_reused(tmp_path: Path) -> None:
    archive, source, files, source_record, bootstrap_sha = _bootstrap_packet(tmp_path)
    release_parent = tmp_path / "release-parent"
    target = (
        release_parent / "bootstrap/sha256" / source_record["archive_sha256"]
    )
    target.mkdir(parents=True)
    with pytest.raises(bootstrap.BootstrapError, match="incomplete"):
        bootstrap.bootstrap(
            archive=archive,
            source_manifest=source,
            file_manifest=files,
            release_parent=release_parent,
            authorized_source_archive_sha256=source_record["archive_sha256"],
            authorized_bootstrap_sha256=bootstrap_sha,
            write=True,
        )


@pytest.mark.parametrize(
    "kind", ["traversal", "absolute", "symlink", "hardlink", "fifo", "duplicate", "prefix"]
)
def test_bootstrap_rejects_malicious_tar_members(tmp_path: Path, kind: str) -> None:
    with pytest.raises(bootstrap.BootstrapError):
        bootstrap._inspect_tar(_malicious_tar(tmp_path, kind))


def test_bootstrap_wrong_self_or_source_authorization_does_not_mutate(tmp_path: Path) -> None:
    archive, source, files, source_record, bootstrap_sha = _bootstrap_packet(tmp_path)
    release_parent = tmp_path / "release-parent"
    with pytest.raises(bootstrap.BootstrapError, match="owner-authorized"):
        bootstrap.bootstrap(
            archive=archive,
            source_manifest=source,
            file_manifest=files,
            release_parent=release_parent,
            authorized_source_archive_sha256="f" * 64,
            authorized_bootstrap_sha256=bootstrap_sha,
            write=True,
        )
    with pytest.raises(bootstrap.BootstrapError, match="executed bootstrap"):
        bootstrap.bootstrap(
            archive=archive,
            source_manifest=source,
            file_manifest=files,
            release_parent=release_parent,
            authorized_source_archive_sha256=source_record["archive_sha256"],
            authorized_bootstrap_sha256="e" * 64,
            write=True,
        )
    assert not release_parent.exists()


def test_bootstrap_source_mutation_fails_before_write(tmp_path: Path) -> None:
    archive, source, files, source_record, bootstrap_sha = _bootstrap_packet(tmp_path)
    with archive.open("ab") as stream:
        stream.write(b"mutation")
    release_parent = tmp_path / "release-parent"
    with pytest.raises(bootstrap.BootstrapError, match="source identity"):
        bootstrap.bootstrap(
            archive=archive,
            source_manifest=source,
            file_manifest=files,
            release_parent=release_parent,
            authorized_source_archive_sha256=source_record["archive_sha256"],
            authorized_bootstrap_sha256=bootstrap_sha,
            write=True,
        )
    assert not release_parent.exists()


@pytest.mark.parametrize("mutation", ["extra", "mode", "symlink", "hardlink"])
def test_bootstrap_target_tampering_fails_closed(tmp_path: Path, mutation: str) -> None:
    archive, source, files, source_record, bootstrap_sha = _bootstrap_packet(tmp_path)
    release_parent = tmp_path / "release-parent"
    arguments = dict(
        archive=archive,
        source_manifest=source,
        file_manifest=files,
        release_parent=release_parent,
        authorized_source_archive_sha256=source_record["archive_sha256"],
        authorized_bootstrap_sha256=bootstrap_sha,
        write=True,
    )
    ready = bootstrap.bootstrap(**arguments)
    target = Path(ready["app_path"]).parent
    app = Path(ready["app_path"])
    source_file = app / bootstrap.BOOTSTRAP_RELATIVE_PATH
    if mutation == "mode":
        app.chmod(0o755)
    elif mutation == "extra":
        target.chmod(0o755)
        (target / "extra").write_bytes(b"x")
        target.chmod(0o555)
    else:
        for parent in (target, app, source_file.parent):
            parent.chmod(0o755)
        if mutation == "symlink":
            source_file.unlink()
            source_file.symlink_to(target / "source.tar")
        else:
            os.link(source_file, source_file.parent / "hardlink.py")
        for parent in (source_file.parent, app, target):
            parent.chmod(0o555)
    with pytest.raises((bootstrap.BootstrapError, OSError)):
        bootstrap.bootstrap(**arguments)


def test_bootstrap_target_symlink_collision_fails_closed(tmp_path: Path) -> None:
    archive, source, files, source_record, bootstrap_sha = _bootstrap_packet(tmp_path)
    release_parent = tmp_path / "release-parent"
    sha_parent = release_parent / "bootstrap/sha256"
    sha_parent.mkdir(parents=True)
    outside = tmp_path / "outside"
    outside.mkdir()
    (sha_parent / source_record["archive_sha256"]).symlink_to(outside)
    with pytest.raises((bootstrap.BootstrapError, OSError)):
        bootstrap.bootstrap(
            archive=archive,
            source_manifest=source,
            file_manifest=files,
            release_parent=release_parent,
            authorized_source_archive_sha256=source_record["archive_sha256"],
            authorized_bootstrap_sha256=bootstrap_sha,
            write=True,
        )


def test_bootstrap_direct_isolated_invocation_has_no_package_dependency() -> None:
    result = subprocess.run(
        [sys.executable, "-I", "-S", "-B", str(Path(bootstrap.__file__)), "--help"],
        env={"PATH": "/usr/bin:/bin", "PYTHONPATH": "/definitely/forbidden"},
        text=True,
        stdout=subprocess.PIPE,
        stderr=subprocess.STDOUT,
        check=False,
    )
    assert result.returncode == 0
    assert "Materialize the owner-authorized" in result.stdout


def test_bootstrap_parser_disables_write_abbreviation() -> None:
    common = [
        "--source-archive", "/source.tar", "--source-manifest", "/source.json",
        "--file-manifest", "/files.json", "--release-parent", "/release",
        "--authorized-source-archive-sha256", "a" * 64,
        "--authorized-bootstrap-sha256", "b" * 64,
    ]
    with pytest.raises(SystemExit):
        bootstrap._parser().parse_args([*common, "--wri"])


@pytest.mark.parametrize(
    "kind", ["traversal", "absolute", "symlink", "hardlink", "fifo", "duplicate"]
)
def test_malicious_tar_members_fail_closed(tmp_path: Path, kind: str) -> None:
    with pytest.raises(gate.ReleaseBuildError):
        gate._inspect_tar(_malicious_tar(tmp_path, kind))


def test_file_directory_prefix_collision_fails(tmp_path: Path) -> None:
    with pytest.raises(gate.ReleaseBuildError, match="used as a directory"):
        gate._inspect_tar(_malicious_tar(tmp_path, "prefix"))


def test_source_manifest_symlink_record_is_forbidden() -> None:
    with pytest.raises(gate.ReleaseBuildError, match="forbids symlinks"):
        gate._validated_file_record(
            {"mode": "0777", "path": "link", "target": "target", "type": "symlink"}
        )


def test_duplicate_and_noncanonical_json_fail() -> None:
    with pytest.raises(gate.ReleaseBuildError, match="duplicate JSON key"):
        gate._strict_json(b'{"x":1,"x":2}', label="test")
    with pytest.raises(gate.ReleaseBuildError, match="not exact canonical"):
        gate._strict_canonical_json(b'{"x": 1}', label="test", object_required=True)


def test_wheelhouse_symlink_and_extra_entry_fail(tmp_path: Path) -> None:
    wheelhouse = tmp_path / "wheelhouse"
    wheelhouse.mkdir()
    target = tmp_path / "target.whl"
    target.write_bytes(b"wheel")
    (wheelhouse / "demo.whl").symlink_to(target)
    expected = {
        "demo.whl": {
            "bytes": 5,
            "sha256": hashlib.sha256(b"wheel").hexdigest(),
        }
    }
    with pytest.raises(gate.ReleaseBuildError, match="regular file"):
        gate._validate_wheelhouse_nofollow(wheelhouse, expected)
    (wheelhouse / "demo.whl").unlink()
    target.unlink()
    (wheelhouse / "demo.whl").write_bytes(b"wheel")
    (wheelhouse / "extra").mkdir()
    with pytest.raises(gate.ReleaseBuildError, match="extra or missing"):
        gate._validate_wheelhouse_nofollow(wheelhouse, expected)


def test_sealed_manifest_rejects_mutation_extra_and_cache(tmp_path: Path) -> None:
    release, manifest = _sealed_payload(tmp_path)
    gate._verify_built_payload(release, manifest)
    os.chmod(release / "app", 0o755)
    (release / "app/.pytest_cache").mkdir()
    os.chmod(release / "app/.pytest_cache", 0o555)
    os.chmod(release / "app", 0o555)
    with pytest.raises(gate.ReleaseBuildError, match="extra, missing, or mutated"):
        gate._verify_built_payload(release, manifest)


def test_sealed_manifest_rejects_file_content_mutation(tmp_path: Path) -> None:
    release, manifest = _sealed_payload(tmp_path)
    path = release / "app/code.py"
    os.chmod(path, 0o644)
    path.write_bytes(b"changed\n")
    os.chmod(path, 0o444)
    with pytest.raises(gate.ReleaseBuildError, match="extra, missing, or mutated"):
        gate._verify_built_payload(release, manifest)


def test_self_consistent_built_metadata_cannot_replace_canonical_source(
    tmp_path: Path,
) -> None:
    release = tmp_path / "release"
    (release / "app/pkg").mkdir(parents=True)
    changed = b"attacker changed code\n"
    (release / "app/pkg/module.py").write_bytes(changed)
    for path in (release / "app/pkg/module.py",):
        path.chmod(0o444)
    for path in (release / "app/pkg", release / "app"):
        path.chmod(0o555)
    expected = b"exact source\n"
    source = gate.SourceBundle(
        archive_path=tmp_path / "source.tar",
        archive_bytes=0,
        archive_sha256="a" * 64,
        archive_records=(),
        archive_directories=(
            {"mode": "0755", "path": "pkg", "type": "directory"},
        ),
        source_manifest={},
        source_manifest_bytes=b"{}",
        source_manifest_sha256="b" * 64,
        file_manifest=(
            {
                "bytes": len(expected),
                "mode": "0644",
                "path": "pkg/module.py",
                "sha256": hashlib.sha256(expected).hexdigest(),
                "type": "file",
            },
        ),
        file_manifest_bytes=b"[]",
        file_manifest_sha256="c" * 64,
    )
    with pytest.raises(gate.ReleaseBuildError, match="canonical source manifest"):
        gate._verify_sealed_app_matches_source(release, source)


def test_co_mutated_self_consistent_metadata_still_revalidates_external_inputs(
    tmp_path: Path, monkeypatch
) -> None:
    release_input = b"{}"
    release_input_sha256 = hashlib.sha256(release_input).hexdigest()
    release = tmp_path / "releases/sha256" / release_input_sha256
    (release / "app").mkdir(parents=True)
    (release / "venv/bin").mkdir(parents=True)
    (release / "app/code.py").write_bytes(b"attacker changed code\n")
    (release / "venv/bin/python").write_bytes(b"python")
    (release / "venv/bin/python").chmod(0o755)
    (release / "release_input_manifest.json").write_bytes(release_input)
    (release / gate.LOCK_RELEASE_NAME).write_bytes(b"co-mutated lock")
    (release / gate.WHEEL_MANIFEST_RELEASE_NAME).write_bytes(b"co-mutated manifest")
    root_fd = gate._open_absolute_directory(release)
    try:
        gate._seal_tree_fd(root_fd)
        records = gate._scan_tree_fd(root_fd, exclude_root_names=gate._METADATA_NAMES)
    finally:
        os.close(root_fd)
    source = gate.SourceBundle(
        archive_path=tmp_path / "unused", archive_bytes=0,
        archive_sha256="a" * 64, archive_records=(), archive_directories=(),
        source_manifest={}, source_manifest_bytes=b"{}",
        source_manifest_sha256="b" * 64, file_manifest=(),
        file_manifest_bytes=b"[]", file_manifest_sha256="c" * 64,
    )
    inputs = gate.ReleaseInputs(
        source=source, release_input={}, release_input_bytes=release_input,
        release_input_sha256=release_input_sha256, wheelhouse=tmp_path,
        release_parent=tmp_path, repo_root=tmp_path,
        lock_bytes=b"co-mutated lock", wheel_manifest_bytes=b"co-mutated manifest",
        builder_origin={"content_addressed": True},
    )
    manifest = gate._build_manifest_payload(
        inputs=inputs, records=records, runtime_evidence={},
        source_store={"path": str(tmp_path / "sources/sha256" / ("a" * 64))},
    )
    manifest_bytes = _canonical(manifest)
    receipt_bytes = _canonical({"co_mutated": True})
    ready = {
        "app_path": str(release / "app"),
        "build_identity_sha256": manifest["build_identity_sha256"],
        "built_runtime_manifest_sha256": hashlib.sha256(manifest_bytes).hexdigest(),
        "python_path": str(release / "venv/bin/python"),
        "release_dir": str(release),
        "release_input_sha256": release_input_sha256,
        "schema_version": gate.READY_SCHEMA,
        "status": "READY",
        "verification_receipt_sha256": hashlib.sha256(receipt_bytes).hexdigest(),
    }
    (release / gate.BUILT_MANIFEST_NAME).write_bytes(manifest_bytes)
    (release / gate.RECEIPT_NAME).write_bytes(receipt_bytes)
    (release / gate.READY_NAME).write_bytes(_canonical(ready))
    for name in gate._METADATA_NAMES:
        (release / name).chmod(0o444)
    release.chmod(0o555)

    def reject_co_mutation(**_kwargs):
        raise gate.ReleaseBuildError("external source/release input chain drift")

    monkeypatch.setattr(gate, "verify_release_inputs", reject_co_mutation)
    with pytest.raises(gate.ReleaseBuildError, match="external source"):
        gate.verify_sealed_release(release)


def test_hard_link_in_release_tree_is_rejected(tmp_path: Path) -> None:
    root = tmp_path / "tree"
    root.mkdir()
    (root / "one").write_bytes(b"x")
    os.link(root / "one", root / "two")
    with pytest.raises(gate.ReleaseBuildError, match="hard-linked"):
        gate._scan_tree(root)


def test_wrong_authorization_hash_never_creates_release_parent(tmp_path: Path) -> None:
    missing_parent = tmp_path / "not-created"
    source = gate.SourceBundle(
        archive_path=tmp_path / "unused", archive_bytes=0,
        archive_sha256="a" * 64, archive_records=(), archive_directories=(),
        source_manifest={}, source_manifest_bytes=b"{}",
        source_manifest_sha256="b" * 64, file_manifest=(),
        file_manifest_bytes=b"[]", file_manifest_sha256="c" * 64,
    )
    inputs = gate.ReleaseInputs(
        source=source, release_input={}, release_input_bytes=b"{}",
        release_input_sha256="d" * 64, wheelhouse=tmp_path,
        release_parent=missing_parent, repo_root=tmp_path, lock_bytes=b"",
        wheel_manifest_bytes=b"",
        builder_origin={"content_addressed": False},
    )
    with pytest.raises(gate.ReleaseBuildError, match="exact authorized"):
        gate.build_release(
            inputs, write=True, authorized_release_input_sha256="e" * 64
        )
    assert not missing_parent.exists()


def test_independent_verifier_must_execute_from_bound_bootstrap() -> None:
    origin = {
        "modules": [
            {
                "bytes": 1,
                "path": "/reviewed/bootstrap/projects/alpha_lab/factory/release_build.py",
                "sha256": "a" * 64,
                "source_relative_path": str(gate.BUILDER_RELATIVE_PATH),
            },
            {
                "bytes": 1,
                "path": "/reviewed/bootstrap/projects/alpha_lab/factory/release_dependencies.py",
                "sha256": "b" * 64,
                "source_relative_path": str(gate.DEPENDENCY_VALIDATOR_RELATIVE_PATH),
            },
        ]
    }
    with pytest.raises(gate.ReleaseBuildError, match="outside the bound bootstrap"):
        gate._verify_executing_builder_is_bound(origin)


def test_incomplete_content_address_is_never_reused(tmp_path: Path) -> None:
    parent_fd = gate._open_absolute_directory(tmp_path)
    try:
        first, created = gate._content_address_directory(parent_fd, "releases", "a" * 64)
        assert created is True
        os.close(first)
        second, created = gate._content_address_directory(parent_fd, "releases", "a" * 64)
        assert created is False
        os.close(second)
    finally:
        os.close(parent_fd)
    with pytest.raises(gate.ReleaseBuildError):
        gate.verify_sealed_release(tmp_path / "releases/sha256" / ("a" * 64))


def test_fault_injection_is_deterministic() -> None:
    gate._fault(None, "point")
    with pytest.raises(gate.ReleaseBuildError, match="FAULT_INJECTED:point"):
        gate._fault("point", "point")


def test_sanitized_environment_removes_inherited_secrets_and_plugins(tmp_path: Path) -> None:
    environment = gate._sanitized_environment(temporary_root=tmp_path)
    assert "PYTHONPATH" not in environment
    assert "PYTHONHOME" not in environment
    assert not any("PROXY" in key or "TOKEN" in key for key in environment)
    assert environment["PIP_CONFIG_FILE"] == "/dev/null"
    assert environment["PYTEST_DISABLE_PLUGIN_AUTOLOAD"] == "1"
    assert environment["TZ"] == "UTC"
    assert environment["HOME"] == str(tmp_path / "home")
    assert (tmp_path / "home").is_dir()


def test_command_prefix_is_applied_to_the_process(monkeypatch, tmp_path: Path) -> None:
    captured = {}

    def fake_run(command, **kwargs):
        captured["command"] = command
        return subprocess.CompletedProcess(command, 0, stdout="ok")

    monkeypatch.setattr(gate.subprocess, "run", fake_run)
    gate._run_command(
        ["python", "test.py"], cwd=tmp_path, environment={}, prefix=["unshare", "--net", "--"]
    )
    assert captured["command"] == ["unshare", "--net", "--", "python", "test.py"]


def test_network_isolation_fails_closed_off_linux(monkeypatch, tmp_path: Path) -> None:
    monkeypatch.setattr(gate.platform, "system", lambda: "Darwin")
    with pytest.raises(gate.ReleaseBuildError, match="requires Linux"):
        gate._network_isolation_contract(
            Path("/usr/bin/python3"), cwd=tmp_path, environment={}
        )


def test_wrong_runtime_target_fails_closed() -> None:
    identity = {
        "architecture": "arm64",
        "base_exec_prefix": "/usr",
        "base_executable": "/usr/bin/python3.10",
        "base_prefix": "/usr",
        "distributions": {},
        "executable": "/release/venv/bin/python",
        "libc": ["glibc", "2.35"],
        "loaded_shared_objects": ["/usr/lib/libc.so.6"],
        "operating_system": {
            "id": "ubuntu", "receipt_path": "/usr/lib/os-release", "version_id": "22.04"
        },
        "python_implementation": "CPython",
        "python_version": "3.10.12",
        "reviewed_tools": {"git": "/usr/bin/git"},
        "stdlib_paths": ["/usr/lib/python3.10"],
    }
    with pytest.raises(gate.ReleaseBuildError, match="architecture drift"):
        gate._validate_runtime_target(identity, Path("/release/venv/bin/python"))


def test_isolated_module_command_uses_fixed_bootstrap() -> None:
    command = gate._isolated_module_command(
        Path("/release/venv/bin/python"), Path("/release/app"), "pytest", ["-q"]
    )
    assert command[:4] == ["/release/venv/bin/python", "-I", "-B", "-c"]
    assert command[-3:] == ["/release/app", "pytest", "-q"]


def test_distribution_closure_records_bootstrap_and_rejects_extra() -> None:
    lock = b"demo==1.0 --hash=sha256:" + b"a" * 64 + b"\n"
    identity = {"distributions": {"demo": "1.0", "pip": "22.0.2"}}
    assert gate._validate_distribution_closure(identity, lock) == {
        "bootstrap_distributions": {"pip": "22.0.2"},
        "locked_distributions": {"demo": "1.0"},
    }
    identity["distributions"]["rogue"] = "9"
    with pytest.raises(gate.ReleaseBuildError, match="bootstrap distributions"):
        gate._validate_distribution_closure(identity, lock)


def test_base_runtime_receipt_detects_external_stdlib_drift(tmp_path: Path) -> None:
    executable = tmp_path / "python"
    executable.write_bytes(b"binary")
    executable.chmod(0o755)
    stdlib = tmp_path / "stdlib"
    stdlib.mkdir()
    (stdlib / "module.py").write_bytes(b"one")
    os_release = tmp_path / "os-release"
    os_release.write_bytes(b"ID=ubuntu\nVERSION_ID=22.04\n")
    shared = tmp_path / "libpython.so"
    shared.write_bytes(b"shared")
    git = tmp_path / "git"
    git.write_bytes(b"git")
    git.chmod(0o755)
    identity = {
        "base_executable": str(executable),
        "base_exec_prefix": str(tmp_path),
        "base_prefix": str(tmp_path),
        "loaded_shared_objects": [str(shared)],
        "operating_system": {
            "id": "ubuntu", "receipt_path": str(os_release), "version_id": "22.04"
        },
        "reviewed_tools": {"git": str(git)},
        "stdlib_paths": [str(stdlib)],
    }
    before = gate._base_runtime_receipt(identity)
    (stdlib / "module.py").write_bytes(b"two")
    after = gate._base_runtime_receipt(identity)
    assert before != after


def test_atlas_gate_e_receipt_binds_runtime_and_complete_site_packages(
    tmp_path: Path,
) -> None:
    release_parent = tmp_path / "release-parent"
    release = release_parent / "releases/sha256" / ("d" * 64)
    site = release / "venv/lib/python3.10/site-packages"
    (release / "app").mkdir(parents=True)
    (release / "venv/bin").mkdir(parents=True)
    site.mkdir(parents=True)
    (release / "app/code.py").write_bytes(b"pass\n")
    (release / "venv/bin/python").write_bytes(b"python")
    (release / "venv/bin/python").chmod(0o755)
    (site / "demo.py").write_bytes(b"value = 1\n")
    (release / gate.LOCK_RELEASE_NAME).write_bytes(b"demo==1\n")
    (release / gate.WHEEL_MANIFEST_RELEASE_NAME).write_bytes(b"{}")
    root_fd = gate._open_absolute_directory(release)
    try:
        gate._seal_tree_fd(root_fd)
        os.fchmod(root_fd, 0o555)
        records = gate._scan_tree_fd(root_fd, exclude_root_names=gate._METADATA_NAMES)
    finally:
        os.close(root_fd)
    source = gate.SourceBundle(
        archive_path=tmp_path / "unused.tar", archive_bytes=0,
        archive_sha256="a" * 64, archive_records=(), archive_directories=(),
        source_manifest={}, source_manifest_bytes=b"{}",
        source_manifest_sha256="b" * 64, file_manifest=(),
        file_manifest_bytes=b"[]", file_manifest_sha256="c" * 64,
    )
    inputs = gate.ReleaseInputs(
        source=source, release_input={}, release_input_bytes=b"{}",
        release_input_sha256="d" * 64, wheelhouse=tmp_path,
        release_parent=release_parent, repo_root=tmp_path, lock_bytes=b"demo==1\n",
        wheel_manifest_bytes=b"{}", builder_origin={"content_addressed": True},
    )
    runtime = {
        "base_runtime": {
            "reviewed_tools": {
                "git": {
                    "bytes": 1, "mode": "0555", "path": "/usr/bin/git",
                    "sha256": "e" * 64,
                }
            }
        },
        "distribution_closure": {
            "bootstrap_distributions": {"pip": "22.0.2"},
            "locked_distributions": {"demo": "1"},
        },
        "site_packages_absolute_path": str(site),
        "site_packages_relative_path": str(site.relative_to(release)),
    }
    manifest = gate._build_manifest_payload(
        inputs=inputs, records=records, runtime_evidence=runtime, source_store={}
    )
    manifest_hash = hashlib.sha256(_canonical(manifest)).hexdigest()
    receipt = gate._atlas_gate_e_runtime_receipt(
        release_dir=release, manifest=manifest,
        built_manifest_sha256=manifest_hash,
    )
    assert receipt["schema_version"] == gate.ATLAS_GATE_E_RUNTIME_RECEIPT_SCHEMA
    assert receipt["python"]["single_link"] is True
    assert receipt["site_packages"]["records"] == [
        {
            "bytes": len(b"value = 1\n"), "mode": "0444", "path": "demo.py",
            "sha256": hashlib.sha256(b"value = 1\n").hexdigest(), "type": "file",
        }
    ]
    assert receipt["seal_evidence"]["same_user_adversarial_seal"] is False


def test_fake_runtime_build_writes_ready_then_real_verifier_reopens_chain(
    tmp_path: Path, monkeypatch,
) -> None:
    release_parent = tmp_path / "release-parent"
    release_parent.mkdir()
    wheelhouse = tmp_path / "wheelhouse"
    wheelhouse.mkdir()
    repo_root = tmp_path / "repo"
    lock = b""
    wheel_manifest = {
        "lock": {"requirement_count": 0},
        "schema_version": "test-wheel-manifest-v1",
        "wheels": [],
    }
    wheel_manifest_bytes = _canonical(wheel_manifest)
    source_files = {
        str(gate.BUILDER_RELATIVE_PATH): Path(gate.__file__).read_bytes(),
        str(gate.DEPENDENCY_VALIDATOR_RELATIVE_PATH): Path(
            gate.validate_release_dependency_contract.__code__.co_filename
        ).read_bytes(),
        str(gate.LOCK_RELATIVE_PATH): lock,
        str(gate.MANIFEST_RELATIVE_PATH): wheel_manifest_bytes,
    }
    archive = tmp_path / "source.tar"
    commit = "1" * 40
    directories = gate._expected_directories(
        {"path": path} for path in source_files
    )
    with tarfile.open(
        archive, "w", format=tarfile.PAX_FORMAT,
        pax_headers={"comment": commit},
    ) as stream:
        for directory_name in sorted(directories):
            directory = tarfile.TarInfo(directory_name)
            directory.type = tarfile.DIRTYPE
            directory.mode = 0o755
            stream.addfile(directory)
        for path, value in sorted(source_files.items()):
            member = tarfile.TarInfo(path)
            member.mode = 0o644
            member.size = len(value)
            stream.addfile(member, io.BytesIO(value))
    file_manifest = [
        {
            "bytes": len(value), "mode": "0644", "path": path,
            "sha256": hashlib.sha256(value).hexdigest(), "type": "file",
        }
        for path, value in sorted(source_files.items())
    ]
    file_bytes = _canonical(file_manifest)
    archive_bytes = archive.read_bytes()
    source_manifest = {
        "archive_bytes": len(archive_bytes),
        "archive_format": "git-archive-tar",
        "archive_sha256": hashlib.sha256(archive_bytes).hexdigest(),
        "commit_sha": commit,
        "file_manifest_member_count": len(file_manifest),
        "file_manifest_schema": gate.FILE_MANIFEST_SCHEMA,
        "file_manifest_sha256": hashlib.sha256(file_bytes).hexdigest(),
        "schema_version": gate.SOURCE_SCHEMA,
        "tree_oid_sha1": _git_tree_oid(
            {path: (0o644, value) for path, value in source_files.items()}
        ),
    }
    source_manifest_bytes = _canonical(source_manifest)
    source_path = tmp_path / "source.json"
    files_path = tmp_path / "files.json"
    source_path.write_bytes(source_manifest_bytes)
    files_path.write_bytes(file_bytes)
    source = gate.verify_source_bundle(
        archive_path=archive, source_manifest_path=source_path,
        file_manifest_path=files_path,
    )
    release_input = {
        "construction_policy": {
            "activation_pointer_created": False, "create_only": True,
            "isolated_venv": True, "network_dependency_resolution": False,
            "require_hashes": True, "scheduler_or_service_mutation": "NONE",
        },
        "dependencies": {
            "lock": {
                "bytes": 0, "path": str(gate.LOCK_RELATIVE_PATH),
                "requirement_count": 0, "sha256": hashlib.sha256(lock).hexdigest(),
            },
            "wheel_bytes_total": 0,
            "wheel_count": 0,
            "wheel_manifest": {
                "bytes": len(wheel_manifest_bytes),
                "path": str(gate.MANIFEST_RELATIVE_PATH),
                "schema_version": wheel_manifest["schema_version"],
                "sha256": hashlib.sha256(wheel_manifest_bytes).hexdigest(),
            },
            "wheels": [],
        },
        "roots": {
            "authoritative_data_root": gate.EXPECTED_AUTHORITATIVE_DATA_ROOT,
            "authoritative_repo_root": gate.EXPECTED_AUTHORITATIVE_REPO_ROOT,
            "canonical_ledger_path": gate.EXPECTED_CANONICAL_LEDGER,
            "release_parent": str(release_parent),
        },
        "schema_version": gate.RELEASE_INPUT_SCHEMA,
        "source": {
            "source_manifest": source_manifest,
            "source_manifest_sha256": hashlib.sha256(source_manifest_bytes).hexdigest(),
        },
        "target": gate.EXPECTED_TARGET,
        "verification_contract": {
            "dependency_validator_schema": "caerus_alpha_lab_phase1_dependency_validation_v1",
            "optional_duckdb_skipped": 2,
            "pytest_passed": 355,
        },
    }
    release_input_bytes = _canonical(release_input)
    release_input_path = tmp_path / "release-input.json"
    release_input_path.write_bytes(release_input_bytes)
    archive_hash = hashlib.sha256(archive_bytes).hexdigest()
    bootstrap_root = release_parent / "bootstrap/sha256" / archive_hash / "app"
    for relative in (gate.BUILDER_RELATIVE_PATH, gate.DEPENDENCY_VALIDATOR_RELATIVE_PATH):
        destination = bootstrap_root / relative
        destination.parent.mkdir(parents=True, exist_ok=True)
        destination.write_bytes(source_files[str(relative)])
    source_records = {record["path"]: record for record in file_manifest}
    builder_origin = {
        "content_addressed": True,
        "expected_bootstrap_root": str(bootstrap_root),
        "modules": [
            {
                "bytes": source_records[str(relative)]["bytes"],
                "path": str(bootstrap_root / relative),
                "sha256": source_records[str(relative)]["sha256"],
                "source_relative_path": str(relative),
            }
            for relative in (gate.BUILDER_RELATIVE_PATH, gate.DEPENDENCY_VALIDATOR_RELATIVE_PATH)
        ],
        "repo_root": str(bootstrap_root),
        "source_archive_sha256": archive_hash,
    }
    inputs = gate.ReleaseInputs(
        source=source, release_input=release_input,
        release_input_bytes=release_input_bytes,
        release_input_sha256=hashlib.sha256(release_input_bytes).hexdigest(),
        wheelhouse=wheelhouse, release_parent=release_parent,
        repo_root=repo_root, lock_bytes=lock,
        wheel_manifest_bytes=wheel_manifest_bytes, builder_origin=builder_origin,
    )
    dependency_result = {
        "schema_version": "caerus_alpha_lab_phase1_dependency_validation_v1",
        "status": "PASS", "wheelhouse_verified": True,
    }

    def fake_runtime(*, release_dir, **_kwargs):
        site = release_dir / "venv/lib/python3.10/site-packages"
        (release_dir / "venv/bin").mkdir(parents=True)
        site.mkdir(parents=True)
        (release_dir / "venv/bin/python").write_bytes(b"python")
        (release_dir / "venv/bin/python").chmod(0o755)
        (site / "demo.py").write_bytes(b"value=1\n")
        return {
            "base_runtime": {
                "reviewed_tools": {
                    "git": {
                        "bytes": 1, "mode": "0555", "path": "/usr/bin/git",
                        "sha256": "e" * 64,
                    }
                }
            },
            "dependency_validation": dependency_result,
            "distribution_closure": {
                "bootstrap_distributions": {"pip": "22.0.2"},
                "locked_distributions": {},
            },
            "entire_release_unchanged": True,
            "pytest_passed": 355, "pytest_skipped": 2,
            "site_packages_absolute_path": str(site),
            "site_packages_relative_path": str(site.relative_to(release_dir)),
            "source_and_venv_unchanged": True,
        }

    monkeypatch.setattr(
        gate, "validate_release_dependency_contract", lambda *_args, **_kwargs: dependency_result
    )
    monkeypatch.setattr(gate, "_verify_runtime_evidence", lambda *_args, **_kwargs: None)
    monkeypatch.setattr(gate, "_verify_executing_builder_is_bound", lambda _origin: None)
    temporary_parent = tmp_path.parent / f"{tmp_path.name}-temporary"
    temporary_parent.mkdir()
    copied_interpreter = temporary_parent / "python"
    copied_interpreter.write_bytes(Path(sys._base_executable).resolve().read_bytes())
    copied_interpreter.chmod(0o755)
    result = gate.build_release(
        inputs, write=True,
        authorized_release_input_sha256=inputs.release_input_sha256,
        interpreter=copied_interpreter, temporary_parent=temporary_parent,
        runtime_executor=fake_runtime, runtime_verifier=lambda *_args, **_kwargs: None,
    )
    release = release_parent / "releases/sha256" / inputs.release_input_sha256
    assert result["status"] == "PASS"
    assert (release / gate.READY_NAME).is_file()
    reopened = gate.verify_sealed_release(release)
    assert reopened["ready_sha256"] == result["ready_sha256"]
    assert reopened["atlas_gate_e_runtime_receipt"] == result[
        "atlas_gate_e_runtime_receipt"
    ]


@pytest.mark.parametrize(
    "arguments",
    [
        ["attestation", "prepare", "--output", "relative.json"],
        ["publication", "publish", "--write"],
        ["publication", "publish", "--w"],
        ["publication", "publish", "--wri"],
        ["publication", "publish", "--write=true"],
        ["arbitrary", "command"],
        [
            "attestation", "prepare", "--output",
            "/mnt/disks/alpha-lab/alpha-lab-project/output.json",
        ],
    ],
)
def test_ceremony_argument_boundary_rejects_unsafe_invocations(
    arguments: list[str], tmp_path: Path,
) -> None:
    output_root = tmp_path / "ceremony-output"
    output_root.mkdir()
    with pytest.raises(gate.ReleaseBuildError):
        gate._validate_ceremony_arguments(
            arguments,
            release_dir=Path("/mnt/disks/alpha-lab/releases/sha256/" + "a" * 64),
            approved_output_root=output_root,
        )


def test_ceremony_argument_boundary_allows_absolute_external_output(tmp_path: Path) -> None:
    output_root = tmp_path / "ceremony-output"
    output_root.mkdir()
    gate._validate_ceremony_arguments(
        [
            "attestation", "prepare", "--event-draft", "/protected/input.json",
            "--output-dir", str(output_root / "output"),
        ],
        release_dir=Path("/mnt/disks/alpha-lab/releases/sha256/" + "a" * 64),
        approved_output_root=output_root,
    )


def test_ceremony_output_symlink_escape_is_rejected(tmp_path: Path) -> None:
    output_root = tmp_path / "ceremony-output"
    outside = tmp_path / "outside"
    output_root.mkdir()
    outside.mkdir()
    (output_root / "link").symlink_to(outside)
    with pytest.raises(gate.ReleaseBuildError, match="without symlink"):
        gate._validate_ceremony_arguments(
            ["attestation", "prepare", "--output-dir", str(output_root / "link/out")],
            release_dir=Path("/mnt/disks/alpha-lab/releases/sha256/" + "a" * 64),
            approved_output_root=output_root,
        )


def test_production_seal_rejects_same_owner_and_accepts_nonwriting_principal(
    tmp_path: Path, monkeypatch,
) -> None:
    release_parent = tmp_path / "release-parent"
    release = release_parent / "releases/sha256" / ("a" * 64)
    (release / "app").mkdir(parents=True)
    (release / "venv/bin").mkdir(parents=True)
    (release / "venv/bin/python").write_bytes(b"python")
    bootstrap_app = release_parent / "bootstrap/sha256" / ("b" * 64) / "app"
    bootstrap_app.mkdir(parents=True)
    (bootstrap_app / "builder.py").write_bytes(b"pass\n")
    origin = {"expected_bootstrap_root": str(bootstrap_app)}
    with pytest.raises(gate.ReleaseBuildError, match="different non-writing principal"):
        gate._production_seal_control(release, builder_origin=origin)
    actual_uid = os.geteuid()
    actual_gid = os.getegid()
    monkeypatch.setattr(gate.os, "geteuid", lambda: actual_uid + 1000)
    monkeypatch.setattr(gate.os, "getegid", lambda: actual_gid + 1000)
    monkeypatch.setattr(gate.os, "access", lambda *_args, **_kwargs: False)
    result = gate._production_seal_control(release, builder_origin=origin)
    assert result["mechanism"] == "different_principal"
    assert result["status"] == "PASS"
    monkeypatch.setattr(
        gate.os, "access", lambda path, *_args, **_kwargs: str(path) == "python"
    )
    with pytest.raises(gate.ReleaseBuildError, match="write protected file"):
        gate._production_seal_control(release, builder_origin=origin)


def test_underlying_ceremony_parser_disables_write_abbreviation() -> None:
    arguments = [
        "publication", "publish",
        "--repo-root", "/repo",
        "--data-root", "/data",
        "--signed-plan", "/plan.json",
        "--authorization", "/authorization.json",
        "--identity-history", "/history.json",
        "--identity-trust-anchor", "/anchor.json",
        "--external-pin", "a" * 64,
        "--w",
    ]
    with pytest.raises(SystemExit):
        ceremony._build_parser().parse_args(arguments)


def test_release_builder_parser_disables_write_abbreviation() -> None:
    common = [
        "--repo-root", "/repo", "--source-archive", "/source.tar",
        "--source-manifest", "/source.json", "--file-manifest", "/files.json",
        "--wheelhouse", "/wheels", "--release-input-manifest", "/input.json",
        "--release-parent", "/release",
    ]
    with pytest.raises(SystemExit):
        gate._parser().parse_args(["build", *common, "--wri"])
