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


_ATTEMPT_ID = "gate-a-1111111111111111-dddddddddddddddd"


def _canonical(value) -> bytes:
    return gate._canonical_bytes(value)


def _fake_network(namespace: str = "net:[4026532260]") -> dict:
    return {
        "mechanism": "systemd_private_network_loopback_only_v1",
        "current_network_namespace": namespace,
        "interfaces": ["lo"],
        "proc_net_dev_interfaces": ["lo"],
        "ipv4_nonlocal_route_count": 0,
        "ipv6_nonlocal_route_count": 0,
        "ipv4_connect_address": "1.1.1.1",
        "ipv6_connect_address": "2606:4700:4700::1111",
        "connect_port": 53,
        "ipv4_connect_errno": 101,
        "ipv6_connect_errno": 101,
        "outbound_connect_blocked": True,
    }


def _install_network_probe(monkeypatch, *, interfaces=None, proc=None, errnos=None) -> None:
    monkeypatch.setattr(gate.platform, "system", lambda: "Linux")
    monkeypatch.setattr(
        gate.os,
        "readlink",
        lambda path: "net:[4026532260]" if path == "/proc/self/ns/net" else None,
    )
    monkeypatch.setattr(
        gate.socket, "if_nameindex", lambda: [(1, "lo")] if interfaces is None else interfaces
    )
    payloads = {
        "/proc/net/dev": (
            "Inter-| Receive | Transmit\n"
            " face |bytes packets errs drop fifo frame compressed multicast|"
            "bytes packets errs drop fifo colls carrier compressed\n"
            " lo: 0 0 0 0 0 0 0 0 0 0 0 0 0 0 0 0\n"
        ),
        "/proc/net/route": (
            "Iface Destination Gateway Flags RefCnt Use Metric Mask MTU Window IRTT\n"
        ),
        "/proc/net/ipv6_route": (
            "00000000000000000000000000000000 00 "
            "00000000000000000000000000000000 00 "
            "00000000000000000000000000000000 00000000 00000000 00000000 "
            "00200200 lo\n"
        ),
    }
    payloads.update(proc or {})
    monkeypatch.setattr(gate, "_read_network_proc", lambda path: payloads[path])
    results = iter(errnos or (101, 101))
    monkeypatch.setattr(gate, "_literal_connect_errno", lambda *_args: next(results))


def _fake_base_runtime() -> dict:
    def file_record(path: str) -> dict:
        return {
            "path": path, "type": "file", "bytes": 1,
            "sha256": "e" * 64, "mode": "0555", "uid": 0, "gid": 0,
            "nlink": 1, "filesystem_readonly": True,
            "effective_principal_writable": False,
        }

    return {
        "schema_version": gate.EXTERNAL_BASE_RUNTIME_RECEIPT_SCHEMA,
        "base_executable": file_record("/usr/bin/python3.10"),
        "base_exec_prefix": "/usr", "base_prefix": "/usr",
        "loaded_shared_objects": [],
        "operating_system_release": {
            **file_record("/usr/lib/os-release"),
            "id": "ubuntu", "version_id": "22.04",
        },
        "reviewed_tools": {
            "git": file_record("/usr/bin/git"),
        },
        "stdlib_roots": [],
        "protected_ancestor_census": [],
        "production_seal_policy": {
            "mechanism": "administrator_established_read_only_runtime_image_v1",
            "established_before_python_start": True,
            "filesystem_readonly_required": True,
            "different_principal_alone_accepted": False,
            "external_owner_outside_attacker_model": True,
            "lazy_loaded_objects_confined_to_read_only_image": True,
            "per_object_mount_check": True,
            "post_execution_rescan_required": True,
        },
    }


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


def _minimal_release_inputs(tmp_path: Path, *, content_addressed: bool) -> gate.ReleaseInputs:
    source = gate.SourceBundle(
        archive_path=tmp_path / "input/source.tar", archive_bytes=0,
        archive_sha256="a" * 64, archive_records=(), archive_directories=(),
        source_manifest={"commit_sha": "1" * 40, "tree_oid_sha1": "2" * 40},
        source_manifest_bytes=b"{}",
        source_manifest_sha256="b" * 64, file_manifest=(),
        file_manifest_bytes=b"[]", file_manifest_sha256="c" * 64,
    )
    return gate.ReleaseInputs(
        source=source, release_input={}, release_input_bytes=b"{}",
        release_input_sha256="d" * 64, wheelhouse=tmp_path / "wheelhouse",
        release_parent=tmp_path / "release-parent", repo_root=tmp_path / "repo",
        lock_bytes=b"", wheel_manifest_bytes=b"",
        builder_origin={"content_addressed": content_addressed},
    )


def _failure_root(tmp_path: Path, attempt_id: str = _ATTEMPT_ID) -> Path:
    root = tmp_path / attempt_id
    root.mkdir(mode=0o700)
    root.chmod(0o700)
    return root


def _failure_inputs(tmp_path: Path) -> gate.ReleaseInputs:
    source_manifest = {
        "commit_sha": "1" * 40,
        "tree_oid_sha1": "2" * 40,
    }
    modules = []
    for relative, content in (
        (gate.BUILDER_RELATIVE_PATH, b"builder"),
        (gate.DEPENDENCY_VALIDATOR_RELATIVE_PATH, b"dependencies"),
    ):
        modules.append(
            {
                "bytes": len(content),
                "path": str(tmp_path / "bootstrap" / relative),
                "sha256": hashlib.sha256(content).hexdigest(),
                "source_relative_path": str(relative),
            }
        )
    source = gate.SourceBundle(
        archive_path=tmp_path / "source.tar",
        archive_bytes=0,
        archive_sha256="a" * 64,
        archive_records=(),
        archive_directories=(),
        source_manifest=source_manifest,
        source_manifest_bytes=_canonical(source_manifest),
        source_manifest_sha256="b" * 64,
        file_manifest=(),
        file_manifest_bytes=b"[]",
        file_manifest_sha256="c" * 64,
    )
    return gate.ReleaseInputs(
        source=source,
        release_input={},
        release_input_bytes=b"{}",
        release_input_sha256="d" * 64,
        wheelhouse=tmp_path / "wheelhouse",
        release_parent=tmp_path / "release-parent",
        repo_root=tmp_path / "bootstrap",
        lock_bytes=b"",
        wheel_manifest_bytes=b"",
        builder_origin={
            "content_addressed": True,
            "expected_bootstrap_root": str(tmp_path / "bootstrap"),
            "modules": modules,
            "repo_root": str(tmp_path / "bootstrap"),
            "source_archive_sha256": "a" * 64,
        },
    )


def _authorized_failure_root(tmp_path: Path, monkeypatch) -> gate.FailureEvidenceRoot:
    root = _failure_root(tmp_path)
    monkeypatch.setattr(gate.os, "listxattr", lambda _fd: [], raising=False)
    return gate._validate_failure_evidence_root(
        root, attempt_id=_ATTEMPT_ID, protected_roots=()
    )


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


def test_dry_build_proves_network_without_creating_release_parent(
    tmp_path: Path, monkeypatch,
) -> None:
    inputs = _minimal_release_inputs(tmp_path, content_addressed=False)
    monkeypatch.setattr(gate, "_network_isolation_contract", _fake_network)
    result = gate.build_release(
        inputs, write=False, authorized_release_input_sha256=None
    )
    assert result["schema_version"] == "caerus_alpha_lab_release_build_plan_v2"
    assert result["network_isolation"] == _fake_network()
    assert not inputs.release_parent.exists()


def test_write_network_failure_precedes_content_address_creation(
    tmp_path: Path, monkeypatch,
) -> None:
    inputs = _minimal_release_inputs(tmp_path, content_addressed=True)
    temporary_parent = tmp_path / "temporary"
    temporary_parent.mkdir()
    failure_root = _failure_root(tmp_path)

    def reject_network():
        raise gate.ReleaseBuildError("inherited network isolation unavailable")

    monkeypatch.setattr(gate, "_network_isolation_contract", reject_network)
    monkeypatch.setattr(gate.os, "listxattr", lambda _fd: [], raising=False)
    with pytest.raises(gate.ReleaseBuildError, match="network isolation unavailable"):
        gate.build_release(
            inputs,
            write=True,
            authorized_release_input_sha256=inputs.release_input_sha256,
            interpreter=Path(sys._base_executable).resolve(),
            temporary_parent=temporary_parent,
            attempt_id=_ATTEMPT_ID,
            failure_evidence_root=failure_root,
        )
    assert not inputs.release_parent.exists()


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


def test_command_runs_without_a_substitutable_prefix(monkeypatch, tmp_path: Path) -> None:
    captured = {}

    def fake_run(command, **kwargs):
        captured["command"] = command
        return subprocess.CompletedProcess(command, 0, stdout="ok")

    monkeypatch.setattr(gate.subprocess, "run", fake_run)
    gate._run_command(["python", "test.py"], cwd=tmp_path, environment={})
    assert captured["command"] == ["python", "test.py"]


def test_release_pytest_failure_captures_exact_combined_bytes_and_junit(
    monkeypatch, tmp_path: Path,
) -> None:
    stdout = b"exact\x00stdout\xff\n"
    junit = b"<testsuites failures='1'/>\n"
    (tmp_path / "pytest-results.xml").write_bytes(junit)

    def fake_run(command, **_kwargs):
        return subprocess.CompletedProcess(command, 7, stdout=stdout)

    monkeypatch.setattr(gate.subprocess, "run", fake_run)
    with pytest.raises(gate.ReleaseCommandFailure) as captured:
        gate._run_command(
            ["fixed", "pytest"],
            cwd=tmp_path,
            environment={},
            failure_role=gate.FAILURE_COMMAND_ROLE,
            failure_temporary_root=tmp_path,
        )
    assert captured.value.command_role == "RELEASE_PYTEST"
    assert captured.value.return_code == 7
    assert captured.value.stdout == stdout
    assert captured.value.junit == junit
    assert captured.value.junit_state == "PRESENT"
    assert captured.value.junit_unsafe_reason is None
    assert stdout.decode("latin1") not in str(captured.value)


def test_release_pytest_failure_records_explicit_absent_junit(
    monkeypatch, tmp_path: Path,
) -> None:
    monkeypatch.setattr(
        gate.subprocess,
        "run",
        lambda command, **_kwargs: subprocess.CompletedProcess(
            command, -9, stdout=b"terminated"
        ),
    )
    with pytest.raises(gate.ReleaseCommandFailure) as captured:
        gate._run_command(
            ["fixed", "pytest"], cwd=tmp_path, environment={},
            failure_role=gate.FAILURE_COMMAND_ROLE,
            failure_temporary_root=tmp_path,
        )
    assert captured.value.return_code == -9
    assert captured.value.junit is None
    assert captured.value.junit_state == "ABSENT"
    assert captured.value.junit_unsafe_reason is None


def test_release_pytest_absent_junit_race_preserves_primary_evidence(
    monkeypatch, tmp_path: Path,
) -> None:
    real_stat = gate.os.stat

    def racing_stat(path, *args, **kwargs):
        if path == "pytest-results.xml":
            (tmp_path / "raced-entry").write_bytes(b"x")
            raise FileNotFoundError(path)
        return real_stat(path, *args, **kwargs)

    monkeypatch.setattr(gate.os, "stat", racing_stat)
    monkeypatch.setattr(
        gate.subprocess,
        "run",
        lambda command, **_kwargs: subprocess.CompletedProcess(
            command, 1, stdout=b"\x00race\xff"
        ),
    )
    with pytest.raises(gate.ReleaseCommandFailure) as captured:
        gate._run_command(
            ["fixed", "pytest"], cwd=tmp_path, environment={},
            failure_role=gate.FAILURE_COMMAND_ROLE,
            failure_temporary_root=tmp_path,
        )
    assert captured.value.stdout == b"\x00race\xff"
    assert captured.value.junit_state == "UNSAFE"
    assert captured.value.junit_unsafe_reason == "ABSENCE_RACE"
    assert captured.value.junit is None
    root = _authorized_failure_root(tmp_path, monkeypatch)
    receipt = gate._write_failure_evidence(
        root=root,
        inputs=_failure_inputs(tmp_path),
        attempt_id=_ATTEMPT_ID,
        failure=captured.value,
    )
    assert (
        root.path / gate.FAILURE_BUNDLE_NAME / gate.FAILURE_STDOUT_NAME
    ).read_bytes() == b"\x00race\xff"
    assert receipt["junit_evidence"] == {
        "reason_code": "ABSENCE_RACE",
        "state": "UNSAFE",
    }


@pytest.mark.parametrize("kind", ["symlink", "hardlink", "fifo"])
def test_release_pytest_failure_persists_stdout_with_unsafe_junit(
    monkeypatch, tmp_path: Path, kind: str,
) -> None:
    target = tmp_path / "target"
    target.write_bytes(b"junit")
    junit = tmp_path / "pytest-results.xml"
    if kind == "symlink":
        junit.symlink_to(target)
    elif kind == "hardlink":
        os.link(target, junit)
    else:
        os.mkfifo(junit)
    monkeypatch.setattr(
        gate.subprocess,
        "run",
        lambda command, **_kwargs: subprocess.CompletedProcess(
            command, 1, stdout=b"\x00unsafe\xff"
        ),
    )
    with pytest.raises(gate.ReleaseCommandFailure) as captured:
        gate._run_command(
            ["fixed", "pytest"], cwd=tmp_path, environment={},
            failure_role=gate.FAILURE_COMMAND_ROLE,
            failure_temporary_root=tmp_path,
        )
    assert captured.value.stdout == b"\x00unsafe\xff"
    assert captured.value.junit_state == "UNSAFE"
    assert captured.value.junit_unsafe_reason == "NOT_SINGLE_LINK_REGULAR"
    assert captured.value.junit is None

    root = _authorized_failure_root(tmp_path, monkeypatch)
    receipt = gate._write_failure_evidence(
        root=root,
        inputs=_failure_inputs(tmp_path),
        attempt_id=_ATTEMPT_ID,
        failure=captured.value,
    )
    bundle = root.path / gate.FAILURE_BUNDLE_NAME
    assert (bundle / gate.FAILURE_STDOUT_NAME).read_bytes() == b"\x00unsafe\xff"
    assert receipt["junit_evidence"] == {
        "reason_code": "NOT_SINGLE_LINK_REGULAR",
        "state": "UNSAFE",
    }
    assert not (bundle / gate.FAILURE_JUNIT_NAME).exists()


def test_monkeypatched_junit_observer_cannot_discard_primary_evidence(
    monkeypatch, tmp_path: Path,
) -> None:
    stdout = b"\x00primary\xff"
    monkeypatch.setattr(
        gate.subprocess,
        "run",
        lambda command, **_kwargs: subprocess.CompletedProcess(
            command, 3, stdout=stdout
        ),
    )
    monkeypatch.setattr(
        gate,
        "_observe_junit",
        lambda _root: (_ for _ in ()).throw(
            RuntimeError("unsafe free text must never escape")
        ),
    )
    with pytest.raises(gate.ReleaseCommandFailure) as captured:
        gate._run_command(
            ["fixed", "pytest"], cwd=tmp_path, environment={},
            failure_role=gate.FAILURE_COMMAND_ROLE,
            failure_temporary_root=tmp_path,
        )
    assert captured.value.stdout == stdout
    assert captured.value.return_code == 3
    assert captured.value.junit_state == "UNSAFE"
    assert captured.value.junit_unsafe_reason == "OBSERVATION_ERROR"

    root = _authorized_failure_root(tmp_path, monkeypatch)
    receipt = gate._write_failure_evidence(
        root=root,
        inputs=_failure_inputs(tmp_path),
        attempt_id=_ATTEMPT_ID,
        failure=captured.value,
    )
    receipt_bytes = _canonical(receipt)
    assert (
        root.path / gate.FAILURE_BUNDLE_NAME / gate.FAILURE_STDOUT_NAME
    ).read_bytes() == stdout
    assert receipt["junit_evidence"] == {
        "reason_code": "OBSERVATION_ERROR",
        "state": "UNSAFE",
    }
    assert b"unsafe free text" not in receipt_bytes


def test_release_command_failure_role_is_closed(monkeypatch, tmp_path: Path) -> None:
    called = False

    def fake_run(*_args, **_kwargs):
        nonlocal called
        called = True
        raise AssertionError("must not execute")

    monkeypatch.setattr(gate.subprocess, "run", fake_run)
    with pytest.raises(gate.ReleaseBuildError, match="role is not closed"):
        gate._run_command(
            ["pip"], cwd=tmp_path, environment={},
            failure_role="PIP_INSTALL", failure_temporary_root=tmp_path,
        )
    assert called is False


@pytest.mark.parametrize("junit", [None, b"<testsuites failures='1'/>\n"])
def test_failure_bundle_is_closed_exact_and_sealed(
    monkeypatch, tmp_path: Path, junit: bytes | None,
) -> None:
    root = _authorized_failure_root(tmp_path, monkeypatch)
    inputs = _failure_inputs(tmp_path)
    stdout = b"private exact output\x00\xff"
    failure = gate.ReleaseCommandFailure(
        command_role=gate.FAILURE_COMMAND_ROLE,
        return_code=1,
        stdout=stdout,
        junit=junit,
    )
    receipt = gate._write_failure_evidence(
        root=root, inputs=inputs, attempt_id=_ATTEMPT_ID, failure=failure
    )
    bundle = root.path / gate.FAILURE_BUNDLE_NAME
    expected_names = {gate.FAILURE_STDOUT_NAME, gate.FAILURE_RECEIPT_NAME}
    if junit is not None:
        expected_names.add(gate.FAILURE_JUNIT_NAME)
    assert {path.name for path in bundle.iterdir()} == expected_names
    assert stat.S_IMODE(bundle.stat().st_mode) == 0o550
    for path in bundle.iterdir():
        assert path.is_file() and not path.is_symlink()
        assert path.stat().st_nlink == 1
        assert stat.S_IMODE(path.stat().st_mode) == 0o440
    assert (bundle / gate.FAILURE_STDOUT_NAME).read_bytes() == stdout
    assert gate._strict_json(
        (bundle / gate.FAILURE_RECEIPT_NAME).read_bytes(), label="failure receipt"
    ) == receipt
    assert set(receipt) == {
        "attempt_id", "builder_identity", "command_role", "junit_evidence",
        "release_input_sha256", "return_code", "schema_version",
        "source_identity", "status", "stdout_evidence",
    }
    assert receipt["schema_version"] == gate.FAILURE_EVIDENCE_SCHEMA
    assert receipt["status"] == "FAILED_CLOSED"
    assert receipt["command_role"] == "RELEASE_PYTEST"
    assert receipt["stdout_evidence"] == {
        "bytes": len(stdout),
        "path": "stdout.bin",
        "sha256": hashlib.sha256(stdout).hexdigest(),
    }
    if junit is None:
        assert receipt["junit_evidence"] == {"state": "ABSENT"}
        assert not (bundle / gate.FAILURE_JUNIT_NAME).exists()
    else:
        assert receipt["junit_evidence"] == {
            "bytes": len(junit), "path": "junit.xml",
            "sha256": hashlib.sha256(junit).hexdigest(), "state": "PRESENT",
        }
        assert (bundle / gate.FAILURE_JUNIT_NAME).read_bytes() == junit
    receipt_bytes = (bundle / gate.FAILURE_RECEIPT_NAME).read_bytes()
    assert b"private exact output" not in receipt_bytes
    assert b"argv" not in receipt_bytes and b"environment" not in receipt_bytes


def test_failure_receipt_binds_source_and_builder_without_absolute_module_paths(
    tmp_path: Path,
) -> None:
    inputs = _failure_inputs(tmp_path)
    receipt = gate._failure_receipt(
        inputs=inputs,
        attempt_id=_ATTEMPT_ID,
        failure=gate.ReleaseCommandFailure(
            command_role=gate.FAILURE_COMMAND_ROLE,
            return_code=2,
            stdout=b"failure",
            junit=None,
        ),
    )
    assert receipt["source_identity"] == {
        "archive_sha256": "a" * 64,
        "commit_sha": "1" * 40,
        "file_manifest_sha256": "c" * 64,
        "source_manifest_sha256": "b" * 64,
        "tree_oid_sha1": "2" * 40,
    }
    assert receipt["builder_identity"] == {
        "modules": [
            {
                "bytes": len(content),
                "sha256": hashlib.sha256(content).hexdigest(),
                "source_relative_path": str(relative),
            }
            for relative, content in (
                (gate.BUILDER_RELATIVE_PATH, b"builder"),
                (gate.DEPENDENCY_VALIDATOR_RELATIVE_PATH, b"dependencies"),
            )
        ],
        "source_archive_sha256": "a" * 64,
    }
    assert str(tmp_path).encode() not in _canonical(receipt)


@pytest.mark.parametrize(
    ("mutation", "message"),
    [
        ("mode", "mode 0700"),
        ("dirty_file", "dedicated and empty"),
        ("dirty_symlink", "dedicated and empty"),
        ("dirty_fifo", "dedicated and empty"),
        ("acl", "POSIX ACL"),
        ("uid", "nonroot Gate A principal"),
        ("gid", "nonroot Gate A principal"),
    ],
)
def test_failure_root_rejects_mode_acl_owner_and_dirty_state(
    monkeypatch, tmp_path: Path, mutation: str, message: str,
) -> None:
    root = _failure_root(tmp_path)
    monkeypatch.setattr(gate.os, "listxattr", lambda _fd: [], raising=False)
    if mutation == "mode":
        root.chmod(0o755)
    elif mutation == "dirty_file":
        (root / "unexpected").write_bytes(b"x")
    elif mutation == "dirty_symlink":
        (root / "unexpected").symlink_to(tmp_path)
    elif mutation == "dirty_fifo":
        os.mkfifo(root / "unexpected")
    elif mutation == "acl":
        monkeypatch.setattr(
            gate.os, "listxattr", lambda _fd: ["system.posix_acl_access"]
        )
    elif mutation == "uid":
        monkeypatch.setattr(gate.os, "geteuid", lambda: os.getuid() + 1000)
    else:
        monkeypatch.setattr(gate.os, "getegid", lambda: os.getgid() + 1000)
    with pytest.raises(gate.ReleaseBuildError, match=message):
        gate._validate_failure_evidence_root(
            root, attempt_id=_ATTEMPT_ID, protected_roots=()
        )


def test_failure_root_rejects_attempt_drift_symlink_and_overlap(
    monkeypatch, tmp_path: Path,
) -> None:
    root = _failure_root(tmp_path)
    monkeypatch.setattr(gate.os, "listxattr", lambda _fd: [], raising=False)
    with pytest.raises(gate.ReleaseBuildError, match="attempt ID"):
        gate._validate_failure_evidence_root(
            root, attempt_id="gate-a-not-closed", protected_roots=()
        )
    with pytest.raises(gate.ReleaseBuildError, match="bound to the attempt ID"):
        gate._validate_failure_evidence_root(
            root,
            attempt_id="gate-a-1111111111111111-2222222222222222",
            protected_roots=(),
        )
    with pytest.raises(gate.ReleaseBuildError, match="overlaps"):
        gate._validate_failure_evidence_root(
            root, attempt_id=_ATTEMPT_ID, protected_roots=(tmp_path,)
        )
    alias_parent = tmp_path / "alias-parent"
    alias_parent.mkdir()
    alias = alias_parent / _ATTEMPT_ID
    alias.symlink_to(root)
    with pytest.raises(OSError):
        gate._validate_failure_evidence_root(
            alias, attempt_id=_ATTEMPT_ID, protected_roots=()
        )
    missing = tmp_path / "missing" / _ATTEMPT_ID
    with pytest.raises(OSError):
        gate._validate_failure_evidence_root(
            missing, attempt_id=_ATTEMPT_ID, protected_roots=()
        )


@pytest.mark.parametrize("mutation", ["mode", "acl", "dirty", "replaced"])
def test_success_reproof_is_independent_and_exact(
    monkeypatch, tmp_path: Path, mutation: str,
) -> None:
    root = _authorized_failure_root(tmp_path, monkeypatch)
    # Disabling the initial validator cannot disable the success-boundary proof.
    monkeypatch.setattr(
        gate, "_validate_failure_evidence_root", lambda *_args, **_kwargs: root
    )
    if mutation == "mode":
        root.path.chmod(0o755)
    elif mutation == "acl":
        monkeypatch.setattr(
            gate.os, "listxattr", lambda _fd: ["system.posix_acl_default"]
        )
    elif mutation == "dirty":
        (root.path / "rogue").write_bytes(b"x")
    else:
        original = tmp_path / "original-root"
        root.path.rename(original)
        root.path.mkdir(mode=0o700)
        root.path.chmod(0o700)
    with pytest.raises(gate.ReleaseBuildError):
        gate._reprove_failure_root_empty(root, attempt_id=_ATTEMPT_ID)


def test_failure_bundle_rejects_collision_and_root_path_race(
    monkeypatch, tmp_path: Path,
) -> None:
    root = _authorized_failure_root(tmp_path, monkeypatch)
    inputs = _failure_inputs(tmp_path)
    failure = gate.ReleaseCommandFailure(
        command_role=gate.FAILURE_COMMAND_ROLE,
        return_code=1,
        stdout=b"failure",
        junit=None,
    )
    (root.path / gate.FAILURE_BUNDLE_NAME).mkdir()
    with pytest.raises(gate.ReleaseBuildError, match="dedicated and empty"):
        gate._write_failure_evidence(
            root=root, inputs=inputs, attempt_id=_ATTEMPT_ID, failure=failure
        )

    # A substituted path with the same name/mode cannot replace the authorized inode.
    old = tmp_path / "authorized-root-old"
    root.path.rename(old)
    root.path.mkdir(mode=0o700)
    root.path.chmod(0o700)
    with pytest.raises(gate.ReleaseBuildError, match="raced after authorization"):
        gate._write_failure_evidence(
            root=root, inputs=inputs, attempt_id=_ATTEMPT_ID, failure=failure
        )


@pytest.mark.parametrize("replacement", ["symlink", "hardlink", "fifo"])
def test_failure_bundle_rejects_leaf_replacement_race(
    monkeypatch, tmp_path: Path, replacement: str,
) -> None:
    root = _authorized_failure_root(tmp_path, monkeypatch)
    inputs = _failure_inputs(tmp_path)
    original = gate._write_failure_file_exclusive
    replaced = False

    def replace_after_write(parent_fd, name, value):
        nonlocal replaced
        descriptor = original(parent_fd, name, value)
        if name == gate.FAILURE_STDOUT_NAME and not replaced:
            replaced = True
            os.unlink(name, dir_fd=parent_fd)
            if replacement == "symlink":
                os.symlink("FAILURE_EVIDENCE.json", name, dir_fd=parent_fd)
            elif replacement == "hardlink":
                target = tmp_path / "race-target"
                target.write_bytes(b"replacement")
                os.link(target, name, dst_dir_fd=parent_fd)
            else:
                os.mkfifo(name, dir_fd=parent_fd)
        return descriptor

    monkeypatch.setattr(gate, "_write_failure_file_exclusive", replace_after_write)
    with pytest.raises((gate.ReleaseBuildError, OSError)):
        gate._write_failure_evidence(
            root=root,
            inputs=inputs,
            attempt_id=_ATTEMPT_ID,
            failure=gate.ReleaseCommandFailure(
                command_role=gate.FAILURE_COMMAND_ROLE,
                return_code=1,
                stdout=b"failure",
                junit=None,
            ),
        )


def test_failure_receipt_is_created_last(monkeypatch, tmp_path: Path) -> None:
    root = _authorized_failure_root(tmp_path, monkeypatch)
    inputs = _failure_inputs(tmp_path)
    original = gate._write_failure_file_exclusive
    order = []

    def record_order(parent_fd, name, value):
        order.append(name)
        return original(parent_fd, name, value)

    monkeypatch.setattr(gate, "_write_failure_file_exclusive", record_order)
    gate._write_failure_evidence(
        root=root,
        inputs=inputs,
        attempt_id=_ATTEMPT_ID,
        failure=gate.ReleaseCommandFailure(
            command_role=gate.FAILURE_COMMAND_ROLE,
            return_code=1,
            stdout=b"failure",
            junit=b"junit",
        ),
    )
    assert order == [
        gate.FAILURE_STDOUT_NAME,
        gate.FAILURE_JUNIT_NAME,
        gate.FAILURE_RECEIPT_NAME,
    ]


def test_failure_file_uses_exclusive_nofollow_and_fsync(
    monkeypatch, tmp_path: Path,
) -> None:
    parent = tmp_path / "bundle"
    parent.mkdir()
    parent_fd = gate._open_absolute_directory(parent)
    real_open = gate.os.open
    real_fsync = gate.os.fsync
    opened_flags = []
    synced = []

    def capture_open(path, flags, *args, **kwargs):
        if path == gate.FAILURE_STDOUT_NAME:
            opened_flags.append(flags)
        return real_open(path, flags, *args, **kwargs)

    def capture_fsync(descriptor):
        synced.append(descriptor)
        return real_fsync(descriptor)

    monkeypatch.setattr(gate.os, "open", capture_open)
    monkeypatch.setattr(gate.os, "fsync", capture_fsync)
    monkeypatch.setattr(gate.os, "listxattr", lambda _fd: [], raising=False)
    try:
        descriptor = gate._write_failure_file_exclusive(
            parent_fd, gate.FAILURE_STDOUT_NAME, b"exact"
        )
        try:
            assert opened_flags[-1] & os.O_EXCL
            assert opened_flags[-1] & os.O_CREAT
            assert opened_flags[-1] & gate._O_NOFOLLOW
            assert descriptor in synced
            assert parent_fd in synced
        finally:
            os.close(descriptor)
        with pytest.raises(FileExistsError):
            gate._write_failure_file_exclusive(
                parent_fd, gate.FAILURE_STDOUT_NAME, b"collision"
            )
    finally:
        os.close(parent_fd)


def test_failure_bundle_rejects_extra_entry_race(monkeypatch, tmp_path: Path) -> None:
    root = _authorized_failure_root(tmp_path, monkeypatch)
    inputs = _failure_inputs(tmp_path)
    original = gate._write_failure_file_exclusive

    def add_extra_after_receipt(parent_fd, name, value):
        descriptor = original(parent_fd, name, value)
        if name == gate.FAILURE_RECEIPT_NAME:
            extra = os.open(
                "unexpected",
                os.O_WRONLY | os.O_CREAT | os.O_EXCL | gate._O_NOFOLLOW,
                0o400,
                dir_fd=parent_fd,
            )
            os.close(extra)
        return descriptor

    monkeypatch.setattr(gate, "_write_failure_file_exclusive", add_extra_after_receipt)
    with pytest.raises(gate.ReleaseBuildError, match="unexpected state"):
        gate._write_failure_evidence(
            root=root,
            inputs=inputs,
            attempt_id=_ATTEMPT_ID,
            failure=gate.ReleaseCommandFailure(
                command_role=gate.FAILURE_COMMAND_ROLE,
                return_code=1,
                stdout=b"failure",
                junit=None,
            ),
        )


def test_dry_build_rejects_failure_write_arguments_without_mutation(
    tmp_path: Path,
) -> None:
    inputs = _minimal_release_inputs(tmp_path, content_addressed=False)
    with pytest.raises(gate.ReleaseBuildError, match="dry build forbids"):
        gate.build_release(
            inputs,
            write=False,
            authorized_release_input_sha256=None,
            attempt_id=_ATTEMPT_ID,
            failure_evidence_root=tmp_path / _ATTEMPT_ID,
        )
    assert not inputs.release_parent.exists()


def test_write_build_requires_failure_authorization_before_mutation(tmp_path: Path) -> None:
    inputs = _minimal_release_inputs(tmp_path, content_addressed=True)
    with pytest.raises(gate.ReleaseBuildError, match="requires exact attempt ID"):
        gate.build_release(
            inputs,
            write=True,
            authorized_release_input_sha256=inputs.release_input_sha256,
        )
    assert not inputs.release_parent.exists()


def test_attempt_id_is_exact_source_and_release_binding_before_write(
    tmp_path: Path,
) -> None:
    inputs = _failure_inputs(tmp_path)
    assert gate._expected_gate_a_attempt_id(inputs) == _ATTEMPT_ID
    near_misses = [
        "gate-a-0111111111111111-dddddddddddddddd",
        "gate-a-1111111111111111-cddddddddddddddd",
        "gate-a-1111111111111111-ddddddddddddddddd",
    ]
    for attempt_id in near_misses:
        with pytest.raises(gate.ReleaseBuildError, match="identity binding"):
            gate._validate_exact_gate_a_attempt_id(inputs, attempt_id)
        with pytest.raises(gate.ReleaseBuildError, match="identity binding"):
            gate._failure_receipt(
                inputs=inputs,
                attempt_id=attempt_id,
                failure=gate.ReleaseCommandFailure(
                    command_role=gate.FAILURE_COMMAND_ROLE,
                    return_code=1,
                    stdout=b"failed",
                    junit=None,
                ),
            )


def test_write_near_miss_attempt_and_root_path_fail_before_release_mutation(
    monkeypatch, tmp_path: Path,
) -> None:
    inputs = _minimal_release_inputs(tmp_path, content_addressed=True)
    temporary_parent = tmp_path / "temporary"
    temporary_parent.mkdir()
    correct = gate._expected_gate_a_attempt_id(inputs)
    near_miss = "gate-a-2111111111111111-dddddddddddddddd"
    wrong_root = _failure_root(tmp_path, near_miss)
    with pytest.raises(gate.ReleaseBuildError, match="identity binding"):
        gate.build_release(
            inputs,
            write=True,
            authorized_release_input_sha256=inputs.release_input_sha256,
            temporary_parent=temporary_parent,
            attempt_id=near_miss,
            failure_evidence_root=wrong_root,
        )
    assert not inputs.release_parent.exists()

    wrong_root.rmdir()
    wrong_path = _failure_root(tmp_path, near_miss)
    monkeypatch.setattr(gate.os, "listxattr", lambda _fd: [], raising=False)
    with pytest.raises(gate.ReleaseBuildError, match="bound to the attempt ID"):
        gate.build_release(
            inputs,
            write=True,
            authorized_release_input_sha256=inputs.release_input_sha256,
            temporary_parent=temporary_parent,
            attempt_id=correct,
            failure_evidence_root=wrong_path,
        )
    assert not inputs.release_parent.exists()


def test_disabled_validator_cannot_substitute_a_different_failure_root(
    monkeypatch, tmp_path: Path,
) -> None:
    inputs = _minimal_release_inputs(tmp_path, content_addressed=True)
    temporary_parent = tmp_path / "temporary"
    temporary_parent.mkdir()
    authorized_path = _failure_root(tmp_path)
    substitute_parent = tmp_path / "substitute"
    substitute_parent.mkdir()
    substitute_path = substitute_parent / _ATTEMPT_ID
    substitute_path.mkdir(mode=0o700)
    substitute_path.chmod(0o700)
    monkeypatch.setattr(gate.os, "listxattr", lambda _fd: [], raising=False)
    substitute = gate._validate_failure_evidence_root(
        substitute_path, attempt_id=_ATTEMPT_ID, protected_roots=()
    )
    monkeypatch.setattr(
        gate, "_validate_failure_evidence_root",
        lambda *_args, **_kwargs: substitute,
    )
    with pytest.raises(gate.ReleaseBuildError, match="exact authorized root"):
        gate.build_release(
            inputs,
            write=True,
            authorized_release_input_sha256=inputs.release_input_sha256,
            temporary_parent=temporary_parent,
            attempt_id=_ATTEMPT_ID,
            failure_evidence_root=authorized_path,
        )
    assert not inputs.release_parent.exists()


def test_network_isolation_fails_closed_off_linux(monkeypatch, tmp_path: Path) -> None:
    monkeypatch.setattr(gate.platform, "system", lambda: "Darwin")
    with pytest.raises(gate.ReleaseBuildError, match="requires Linux"):
        gate._network_isolation_contract()


def test_network_isolation_fails_closed_for_root(monkeypatch) -> None:
    monkeypatch.setattr(gate.platform, "system", lambda: "Linux")
    monkeypatch.setattr(gate.os, "geteuid", lambda: 0)
    with pytest.raises(gate.ReleaseBuildError, match="must be non-root"):
        gate._network_isolation_contract()


def test_network_isolation_proves_exact_inherited_private_namespace(monkeypatch) -> None:
    _install_network_probe(monkeypatch)
    calls = []
    results = iter((101, 101))

    def connect_errno(family, address):
        calls.append((family, address))
        return next(results)

    monkeypatch.setattr(gate, "_literal_connect_errno", connect_errno)
    assert gate._network_isolation_contract() == _fake_network()
    assert calls == [
        (gate.socket.AF_INET, ("1.1.1.1", 53)),
        (gate.socket.AF_INET6, ("2606:4700:4700::1111", 53, 0, 0)),
    ]


@pytest.mark.parametrize(
    ("field", "value"),
    [
        ("ipv4_connect_address", "8.8.8.8"),
        ("ipv6_connect_address", "2606:4700:4700::1001"),
        ("connect_port", 443),
        ("ipv4_connect_errno", 13),
        ("ipv4_nonlocal_route_count", False),
        ("interfaces", ["lo", "ens4"]),
    ],
)
def test_network_isolation_receipt_rejects_semantic_drift(field, value) -> None:
    record = _fake_network()
    record[field] = value
    with pytest.raises(gate.ReleaseBuildError, match="proof evidence drift"):
        gate._verify_network_isolation_record(record)


def test_network_isolation_receipt_rejects_extra_fields() -> None:
    record = {**_fake_network(), "unshare": "/usr/bin/unshare"}
    with pytest.raises(gate.ReleaseBuildError, match="schema drift"):
        gate._verify_network_isolation_record(record)


def test_network_isolation_rejects_malformed_namespace_identity(monkeypatch) -> None:
    _install_network_probe(monkeypatch)
    monkeypatch.setattr(gate.os, "readlink", lambda _path: "not-a-namespace")
    with pytest.raises(gate.ReleaseBuildError, match="not established"):
        gate._network_isolation_contract()


def test_network_isolation_rejects_host_interface_from_socket_census(monkeypatch) -> None:
    _install_network_probe(monkeypatch, interfaces=[(1, "lo"), (2, "ens4")])
    with pytest.raises(gate.ReleaseBuildError, match="non-loopback interface"):
        gate._network_isolation_contract()


def test_network_isolation_rejects_host_interface_from_proc(monkeypatch) -> None:
    proc_dev = (
        "Inter-| Receive | Transmit\n"
        " face |bytes packets errs drop fifo frame compressed multicast|"
        "bytes packets errs drop fifo colls carrier compressed\n"
        " lo: 0 0 0 0 0 0 0 0 0 0 0 0 0 0 0 0\n"
        " ens4: 0 0 0 0 0 0 0 0 0 0 0 0 0 0 0 0\n"
    )
    _install_network_probe(monkeypatch, proc={"/proc/net/dev": proc_dev})
    with pytest.raises(gate.ReleaseBuildError, match="/proc/net/dev"):
        gate._network_isolation_contract()


def test_network_isolation_rejects_ipv4_route(monkeypatch) -> None:
    route = (
        "Iface Destination Gateway Flags RefCnt Use Metric Mask MTU Window IRTT\n"
        "lo 00000000 00000000 0001 0 0 0 00000000 0 0 0\n"
    )
    _install_network_probe(monkeypatch, proc={"/proc/net/route": route})
    with pytest.raises(gate.ReleaseBuildError, match="non-loopback route"):
        gate._network_isolation_contract()


def test_network_isolation_rejects_ipv6_non_loopback_route(monkeypatch) -> None:
    route = (
        "00000000000000000000000000000000 00 "
        "00000000000000000000000000000000 00 "
        "00000000000000000000000000000000 00000000 00000000 00000000 "
        "00200200 ens4\n"
    )
    _install_network_probe(monkeypatch, proc={"/proc/net/ipv6_route": route})
    with pytest.raises(gate.ReleaseBuildError, match="non-loopback route"):
        gate._network_isolation_contract()


@pytest.mark.parametrize("errnos", [(0, 101), (101, 0), (13, 101), (101, 13)])
def test_network_isolation_requires_exact_enetunreach_for_both_families(
    monkeypatch, errnos,
) -> None:
    _install_network_probe(monkeypatch, errnos=errnos)
    with pytest.raises(gate.ReleaseBuildError, match="ENETUNREACH"):
        gate._network_isolation_contract()


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


def test_linux_venv_lib64_compatibility_link_is_removed_exactly(
    tmp_path: Path,
) -> None:
    venv = tmp_path / "venv"
    (venv / "lib").mkdir(parents=True)
    (venv / "lib64").symlink_to("lib")
    gate._remove_redundant_venv_lib64_link(venv)
    assert not (venv / "lib64").exists()
    gate._remove_redundant_venv_lib64_link(venv)
    (venv / "lib64").write_bytes(b"unexpected")
    with pytest.raises(gate.ReleaseBuildError, match="not exact"):
        gate._remove_redundant_venv_lib64_link(venv)


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
    before = gate._base_runtime_receipt(identity, production_seal=False)
    (stdlib / "module.py").write_bytes(b"two")
    after = gate._base_runtime_receipt(identity, production_seal=False)
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
        "base_runtime": _fake_base_runtime(),
        "distribution_closure": {
            "bootstrap_distributions": {"pip": "22.0.2"},
            "locked_distributions": {"demo": "1"},
        },
        "site_packages_absolute_path": str(site),
        "site_packages_relative_path": str(site.relative_to(release)),
        "network_isolation": _fake_network(),
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
    assert receipt["network_isolation"] == _fake_network()
    assert set(receipt["base_runtime"]["reviewed_tools"]) == {"git"}
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
            "base_runtime": _fake_base_runtime(),
            "dependency_validation": dependency_result,
            "distribution_closure": {
                "bootstrap_distributions": {"pip": "22.0.2"},
                "locked_distributions": {},
            },
            "entire_release_unchanged": True,
            "network_isolation": _fake_network(),
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
    monkeypatch.setattr(gate, "_network_isolation_contract", _fake_network)
    monkeypatch.setattr(gate.os, "listxattr", lambda _fd: [], raising=False)
    temporary_parent = tmp_path.parent / f"{tmp_path.name}-temporary"
    temporary_parent.mkdir()
    attempt_id = gate._expected_gate_a_attempt_id(inputs)
    failure_root = _failure_root(tmp_path, attempt_id)
    copied_interpreter = temporary_parent / "python"
    copied_interpreter.write_bytes(Path(sys._base_executable).resolve().read_bytes())
    copied_interpreter.chmod(0o755)
    result = gate.build_release(
        inputs, write=True,
        authorized_release_input_sha256=inputs.release_input_sha256,
        interpreter=copied_interpreter, temporary_parent=temporary_parent,
        attempt_id=attempt_id, failure_evidence_root=failure_root,
        runtime_executor=fake_runtime, runtime_verifier=lambda *_args, **_kwargs: None,
    )
    release = release_parent / "releases/sha256" / inputs.release_input_sha256
    assert result["status"] == "PASS"
    assert list(failure_root.iterdir()) == []
    assert (release / gate.READY_NAME).is_file()
    reopened = gate.verify_sealed_release(release)
    assert reopened["ready_sha256"] == result["ready_sha256"]
    assert reopened["atlas_gate_e_runtime_receipt"] == result[
        "atlas_gate_e_runtime_receipt"
    ]
    already_ready = gate.build_release(
        inputs, write=True,
        authorized_release_input_sha256=inputs.release_input_sha256,
        interpreter=copied_interpreter, temporary_parent=temporary_parent,
        attempt_id=attempt_id, failure_evidence_root=failure_root,
        runtime_executor=lambda **_kwargs: (_ for _ in ()).throw(
            AssertionError("ALREADY_READY must not execute runtime")
        ),
        runtime_verifier=lambda *_args, **_kwargs: None,
    )
    assert already_ready["status"] == "ALREADY_READY"
    assert list(failure_root.iterdir()) == []

    authorized_root = gate._validate_failure_evidence_root(
        failure_root, attempt_id=attempt_id, protected_roots=()
    )
    monkeypatch.setattr(
        gate, "_validate_failure_evidence_root",
        lambda *_args, **_kwargs: authorized_root,
    )
    real_verify = gate.verify_sealed_release

    def verify_then_dirty(release_dir):
        verified = real_verify(release_dir)
        (failure_root / "rogue").write_bytes(b"not success")
        return verified

    monkeypatch.setattr(gate, "verify_sealed_release", verify_then_dirty)
    with pytest.raises(gate.ReleaseBuildError, match="nonempty at successful boundary"):
        gate.build_release(
            inputs, write=True,
            authorized_release_input_sha256=inputs.release_input_sha256,
            interpreter=copied_interpreter, temporary_parent=temporary_parent,
            attempt_id=attempt_id, failure_evidence_root=failure_root,
            runtime_executor=lambda **_kwargs: (_ for _ in ()).throw(
                AssertionError("ALREADY_READY must not execute runtime")
            ),
            runtime_verifier=lambda *_args, **_kwargs: None,
        )


def test_write_build_persists_pytest_failure_before_temporary_cleanup(
    tmp_path: Path, monkeypatch,
) -> None:
    inputs = _failure_inputs(tmp_path)
    inputs.release_parent.mkdir()
    temporary_parent = tmp_path / "temporary"
    temporary_parent.mkdir()
    failure_root_path = _failure_root(tmp_path)
    interpreter = tmp_path / "python"
    interpreter.write_bytes(Path(sys._base_executable).resolve().read_bytes())
    interpreter.chmod(0o755)
    transient = None

    monkeypatch.setattr(gate.os, "listxattr", lambda _fd: [], raising=False)
    monkeypatch.setattr(gate, "_network_isolation_contract", _fake_network)
    monkeypatch.setattr(
        gate, "_materialize_source_store", lambda *_args, **_kwargs: {"status": "READY"}
    )
    monkeypatch.setattr(gate, "_extract_tar_exact", lambda *_args, **_kwargs: None)
    monkeypatch.setattr(gate, "_copy_wheelhouse", lambda *_args, **_kwargs: None)

    forged = object.__new__(gate.ValidatedCommandFailure)
    object.__setattr__(forged, "command_role", "BAD")
    object.__setattr__(forged, "return_code", 0)
    object.__setattr__(forged, "stdout", "not-bytes")
    object.__setattr__(forged, "junit_state", "PRESENT")
    object.__setattr__(forged, "junit", None)
    object.__setattr__(forged, "junit_unsafe_reason", "free text")
    monkeypatch.setattr(
        gate, "_validated_command_failure", lambda _failure: forged
    )
    monkeypatch.setattr(
        gate,
        "_failure_receipt_from_validated",
        lambda **_kwargs: {"status": "BYPASSED"},
    )

    def fail_runtime(*, temporary_root, **_kwargs):
        nonlocal transient
        transient = temporary_root
        (temporary_root / "ephemeral").write_bytes(b"removed after evidence write")
        raise gate.ReleaseCommandFailure(
            command_role=gate.FAILURE_COMMAND_ROLE,
            return_code=1,
            stdout=b"exact failed pytest output",
            junit=b"<testsuites failures='1'/>\n",
        )

    with pytest.raises(gate.ReleaseCommandFailure, match="RELEASE_PYTEST"):
        gate.build_release(
            inputs,
            write=True,
            authorized_release_input_sha256=inputs.release_input_sha256,
            interpreter=interpreter,
            temporary_parent=temporary_parent,
            attempt_id=_ATTEMPT_ID,
            failure_evidence_root=failure_root_path,
            runtime_executor=fail_runtime,
        )
    assert transient is not None and not transient.exists()
    assert list(temporary_parent.iterdir()) == []
    bundle = failure_root_path / gate.FAILURE_BUNDLE_NAME
    assert (bundle / gate.FAILURE_STDOUT_NAME).read_bytes() == b"exact failed pytest output"
    assert (bundle / gate.FAILURE_JUNIT_NAME).read_bytes() == b"<testsuites failures='1'/>\n"
    receipt = gate._strict_json(
        (bundle / gate.FAILURE_RECEIPT_NAME).read_bytes(), label="failure receipt"
    )
    assert receipt["status"] == "FAILED_CLOSED"
    assert receipt["attempt_id"] == _ATTEMPT_ID
    assert not (inputs.release_parent / "releases/sha256" / inputs.release_input_sha256 / gate.READY_NAME).exists()


@pytest.mark.parametrize(
    "mutation",
    [
        "command_role",
        "return_bool",
        "return_zero",
        "stdout_bytearray",
        "stdout_subclass",
        "state_unknown",
        "present_without_bytes",
        "present_with_reason",
        "present_bytes_subclass",
        "absent_with_bytes",
        "absent_with_reason",
        "unsafe_with_bytes",
        "unsafe_without_reason",
        "unsafe_free_reason",
        "subclass",
        "proxy_subclass",
    ],
)
def test_full_build_rejects_mutated_or_nonexact_failure_before_bundle_write(
    tmp_path: Path, monkeypatch, mutation: str,
) -> None:
    inputs = _failure_inputs(tmp_path)
    inputs.release_parent.mkdir()
    temporary_parent = tmp_path / "temporary"
    temporary_parent.mkdir()
    failure_root_path = _failure_root(tmp_path)
    interpreter = tmp_path / "python"
    interpreter.write_bytes(Path(sys._base_executable).resolve().read_bytes())
    interpreter.chmod(0o755)

    monkeypatch.setattr(gate.os, "listxattr", lambda _fd: [], raising=False)
    monkeypatch.setattr(gate, "_network_isolation_contract", _fake_network)
    monkeypatch.setattr(
        gate, "_materialize_source_store", lambda *_args, **_kwargs: {"status": "READY"}
    )
    monkeypatch.setattr(gate, "_extract_tar_exact", lambda *_args, **_kwargs: None)
    monkeypatch.setattr(gate, "_copy_wheelhouse", lambda *_args, **_kwargs: None)

    forged = object.__new__(gate.ValidatedCommandFailure)
    object.__setattr__(forged, "command_role", "BAD")
    object.__setattr__(forged, "return_code", 0)
    object.__setattr__(forged, "stdout", "not-bytes")
    object.__setattr__(forged, "junit_state", "PRESENT")
    object.__setattr__(forged, "junit", None)
    object.__setattr__(forged, "junit_unsafe_reason", "free text")
    monkeypatch.setattr(
        gate, "_validated_command_failure", lambda _failure: forged
    )
    monkeypatch.setattr(
        gate,
        "_failure_receipt_from_validated",
        lambda **_kwargs: {"status": "BYPASSED"},
    )

    class BytesSubclass(bytes):
        pass

    class FailureSubclass(gate.ReleaseCommandFailure):
        pass

    class FailureProxy(gate.ReleaseCommandFailure):
        def __getattribute__(self, name):
            if name == "stdout":
                return b"proxy"
            return super().__getattribute__(name)

    def mutated_failure():
        failure_class = (
            FailureSubclass if mutation == "subclass"
            else FailureProxy if mutation == "proxy_subclass"
            else gate.ReleaseCommandFailure
        )
        failure = failure_class(
            command_role=gate.FAILURE_COMMAND_ROLE,
            return_code=1,
            stdout=b"primary",
            junit=b"junit",
        )
        if mutation == "command_role":
            failure.command_role = "NOT_RELEASE_PYTEST"
        elif mutation == "return_bool":
            failure.return_code = True
        elif mutation == "return_zero":
            failure.return_code = 0
        elif mutation == "stdout_bytearray":
            failure.stdout = bytearray(b"primary")
        elif mutation == "stdout_subclass":
            failure.stdout = BytesSubclass(b"primary")
        elif mutation == "state_unknown":
            failure.junit_state = "UNKNOWN"
        elif mutation == "present_without_bytes":
            failure.junit = None
        elif mutation == "present_with_reason":
            failure.junit_unsafe_reason = "OBSERVATION_ERROR"
        elif mutation == "present_bytes_subclass":
            failure.junit = BytesSubclass(b"junit")
        elif mutation == "absent_with_bytes":
            failure.junit_state = "ABSENT"
        elif mutation == "absent_with_reason":
            failure.junit_state = "ABSENT"
            failure.junit = None
            failure.junit_unsafe_reason = "OBSERVATION_ERROR"
        elif mutation == "unsafe_with_bytes":
            failure.junit_state = "UNSAFE"
            failure.junit_unsafe_reason = "OBSERVATION_ERROR"
        elif mutation == "unsafe_without_reason":
            failure.junit_state = "UNSAFE"
            failure.junit = None
        elif mutation == "unsafe_free_reason":
            failure.junit_state = "UNSAFE"
            failure.junit = None
            failure.junit_unsafe_reason = "raw /tmp/path and free text"
        return failure

    def fail_runtime(**_kwargs):
        raise mutated_failure()

    with pytest.raises(gate.ReleaseBuildError):
        gate.build_release(
            inputs,
            write=True,
            authorized_release_input_sha256=inputs.release_input_sha256,
            interpreter=interpreter,
            temporary_parent=temporary_parent,
            attempt_id=_ATTEMPT_ID,
            failure_evidence_root=failure_root_path,
            runtime_executor=fail_runtime,
        )
    assert list(failure_root_path.iterdir()) == []
    assert not (failure_root_path / gate.FAILURE_BUNDLE_NAME).exists()


def test_failure_receipt_and_writer_reject_plain_proxy_before_any_write(
    tmp_path: Path, monkeypatch,
) -> None:
    class PlainProxy:
        command_role = gate.FAILURE_COMMAND_ROLE
        return_code = 1
        stdout = b"primary"
        junit_state = "ABSENT"
        junit = None
        junit_unsafe_reason = None

    root = _authorized_failure_root(tmp_path, monkeypatch)
    inputs = _failure_inputs(tmp_path)
    proxy = PlainProxy()
    with pytest.raises(gate.ReleaseBuildError, match="exact ReleaseCommandFailure"):
        gate._failure_receipt(
            inputs=inputs, attempt_id=_ATTEMPT_ID, failure=proxy
        )
    monkeypatch.setattr(
        gate, "_failure_receipt", lambda **_kwargs: {"status": "BYPASSED"}
    )
    with pytest.raises(gate.ReleaseBuildError, match="exact ReleaseCommandFailure"):
        gate._write_failure_evidence(
            root=root, inputs=inputs, attempt_id=_ATTEMPT_ID, failure=proxy
        )
    assert list(root.path.iterdir()) == []


@pytest.mark.parametrize(
    "values",
    [
        {
            "command_role": "BAD", "return_code": 1, "stdout": b"x",
            "junit_state": "ABSENT", "junit": None,
            "junit_unsafe_reason": None,
        },
        {
            "command_role": gate.FAILURE_COMMAND_ROLE, "return_code": 0,
            "stdout": b"x", "junit_state": "ABSENT", "junit": None,
            "junit_unsafe_reason": None,
        },
        {
            "command_role": gate.FAILURE_COMMAND_ROLE, "return_code": 1,
            "stdout": "not-bytes", "junit_state": "ABSENT", "junit": None,
            "junit_unsafe_reason": None,
        },
        {
            "command_role": gate.FAILURE_COMMAND_ROLE, "return_code": 1,
            "stdout": b"x", "junit_state": "PRESENT", "junit": None,
            "junit_unsafe_reason": None,
        },
    ],
)
def test_validated_failure_constructor_enforces_all_invariants(values) -> None:
    with pytest.raises(gate.ReleaseBuildError):
        gate.ValidatedCommandFailure(**values)


def test_writer_rechecks_forged_snapshot_even_with_explicit_factory(
    tmp_path: Path, monkeypatch,
) -> None:
    forged = object.__new__(gate.ValidatedCommandFailure)
    object.__setattr__(forged, "command_role", "BAD")
    object.__setattr__(forged, "return_code", 0)
    object.__setattr__(forged, "stdout", "not-bytes")
    object.__setattr__(forged, "junit_state", "PRESENT")
    object.__setattr__(forged, "junit", None)
    object.__setattr__(forged, "junit_unsafe_reason", "free text")
    root = _authorized_failure_root(tmp_path, monkeypatch)
    with pytest.raises(gate.ReleaseBuildError):
        gate._write_failure_evidence(
            root=root,
            inputs=_failure_inputs(tmp_path),
            attempt_id=_ATTEMPT_ID,
            failure=gate.ReleaseCommandFailure(
                command_role=gate.FAILURE_COMMAND_ROLE,
                return_code=1,
                stdout=b"valid",
                junit=None,
            ),
            _snapshot_factory=lambda _failure: forged,
        )
    assert list(root.path.iterdir()) == []


def test_disabled_initial_validator_cannot_hide_runtime_rogue_success_artifact(
    tmp_path: Path, monkeypatch,
) -> None:
    inputs = _failure_inputs(tmp_path)
    inputs.release_parent.mkdir()
    temporary_parent = tmp_path / "temporary"
    temporary_parent.mkdir()
    failure_root_path = _failure_root(tmp_path)
    interpreter = tmp_path / "python"
    interpreter.write_bytes(Path(sys._base_executable).resolve().read_bytes())
    interpreter.chmod(0o755)

    monkeypatch.setattr(gate.os, "listxattr", lambda _fd: [], raising=False)
    authorized = gate._validate_failure_evidence_root(
        failure_root_path, attempt_id=_ATTEMPT_ID, protected_roots=()
    )
    monkeypatch.setattr(
        gate, "_validate_failure_evidence_root",
        lambda *_args, **_kwargs: authorized,
    )
    monkeypatch.setattr(gate, "_network_isolation_contract", _fake_network)
    monkeypatch.setattr(
        gate, "_materialize_source_store", lambda *_args, **_kwargs: {"status": "READY"}
    )
    monkeypatch.setattr(gate, "_extract_tar_exact", lambda *_args, **_kwargs: None)
    monkeypatch.setattr(gate, "_copy_wheelhouse", lambda *_args, **_kwargs: None)

    def rogue_runtime(**_kwargs):
        (failure_root_path / "rogue").write_bytes(b"must prevent READY")
        return {}

    with pytest.raises(gate.ReleaseBuildError, match="nonempty at successful boundary"):
        gate.build_release(
            inputs,
            write=True,
            authorized_release_input_sha256=inputs.release_input_sha256,
            interpreter=interpreter,
            temporary_parent=temporary_parent,
            attempt_id=_ATTEMPT_ID,
            failure_evidence_root=failure_root_path,
            runtime_executor=rogue_runtime,
        )
    release = (
        inputs.release_parent / "releases/sha256" / inputs.release_input_sha256
    )
    assert not (release / gate.READY_NAME).exists()
    assert not (release / gate.RECEIPT_NAME).exists()
    assert not (release / gate.BUILT_MANIFEST_NAME).exists()


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


def test_ceremony_output_cannot_enter_success_receipt_namespace(tmp_path: Path) -> None:
    output_root = tmp_path / "ceremony-output"
    output_root.mkdir()
    with pytest.raises(gate.ReleaseBuildError, match="receipt namespace"):
        gate._validate_ceremony_arguments(
            [
                "attestation", "prepare", "--output-dir",
                str(output_root / ".gate_e_success/forged"),
            ],
            release_dir=Path("/mnt/disks/alpha-lab/releases/sha256/" + "a" * 64),
            approved_output_root=output_root,
        )


def test_production_seal_requires_per_object_readonly_mount_and_acl_denial(
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
    base_runtime = _fake_base_runtime()
    monkeypatch.setattr(
        gate, "_verify_external_base_runtime_receipt", lambda value: value
    )
    with pytest.raises(gate.ReleaseBuildError, match="read-only filesystem"):
        gate._production_seal_control(
            release, builder_origin=origin, base_runtime=base_runtime
        )
    actual_uid = os.geteuid()
    actual_gid = os.getegid()
    monkeypatch.setattr(gate.os, "geteuid", lambda: max(1, actual_uid))
    monkeypatch.setattr(gate.os, "getegid", lambda: max(1, actual_gid))
    monkeypatch.setattr(gate, "_filesystem_readonly", lambda _fd: True)
    monkeypatch.setattr(gate.os, "access", lambda *_args, **_kwargs: False)
    result = gate._production_seal_control(
        release, builder_origin=origin, base_runtime=base_runtime
    )
    assert result["mechanism"] == "administrator_established_read_only_runtime_image_v1"
    assert result["status"] == "PASS"
    monkeypatch.setattr(
        gate.os, "access", lambda path, *_args, **_kwargs: str(path) == "python"
    )
    with pytest.raises(gate.ReleaseBuildError, match="read-only filesystem"):
        gate._production_seal_control(
            release, builder_origin=origin, base_runtime=base_runtime
        )


@pytest.mark.parametrize("kind", ["symlink", "hardlink", "fifo"])
def test_external_stdlib_scan_rejects_links_and_special_entries(
    tmp_path: Path, kind: str,
) -> None:
    root = tmp_path / kind
    root.mkdir()
    target = root / "module.py"
    target.write_bytes(b"value = 1\n")
    if kind == "symlink":
        (root / "alias.py").symlink_to(target)
    elif kind == "hardlink":
        os.link(target, root / "alias.py")
    else:
        os.mkfifo(root / "pipe")
    descriptor = gate._open_absolute_directory(root)
    try:
        match = "symlink" if kind == "symlink" else (
            "hard-linked" if kind == "hardlink" else "unsupported"
        )
        with pytest.raises(gate.ReleaseBuildError, match=match):
            gate._scan_external_tree_fd(descriptor, production_seal=False)
    finally:
        os.close(descriptor)


def test_external_stdlib_scan_requires_readonly_each_file(
    tmp_path: Path, monkeypatch,
) -> None:
    root = tmp_path / "stdlib"
    root.mkdir()
    (root / "module.py").write_bytes(b"value = 1\n")
    monkeypatch.setattr(gate.os, "access", lambda *_args, **_kwargs: False)
    monkeypatch.setattr(
        gate,
        "_filesystem_readonly",
        lambda descriptor: stat.S_ISDIR(os.fstat(descriptor).st_mode),
    )
    descriptor = gate._open_absolute_directory(root)
    try:
        with pytest.raises(gate.ReleaseBuildError, match="read-only filesystem"):
            gate._scan_external_tree_fd(descriptor, production_seal=True)
    finally:
        os.close(descriptor)


def test_external_stdlib_scan_rejects_acl_writable_child(
    tmp_path: Path, monkeypatch,
) -> None:
    root = tmp_path / "stdlib"
    root.mkdir()
    (root / "module.py").write_bytes(b"value = 1\n")
    monkeypatch.setattr(gate, "_filesystem_readonly", lambda _descriptor: True)
    monkeypatch.setattr(
        gate,
        "_effective_writable_at",
        lambda _parent, name: name == "module.py",
    )
    descriptor = gate._open_absolute_directory(root)
    try:
        with pytest.raises(gate.ReleaseBuildError, match="read-only filesystem"):
            gate._scan_external_tree_fd(descriptor, production_seal=True)
    finally:
        os.close(descriptor)


def test_ceremony_checks_readonly_tcb_before_any_release_python(
    tmp_path: Path, monkeypatch,
) -> None:
    release = tmp_path / "release"
    app = release / "app"
    python = release / "venv/bin/python"
    app.mkdir(parents=True)
    python.parent.mkdir(parents=True)
    output = tmp_path / "output"
    output.mkdir()
    calls: list[str] = []
    base = _fake_base_runtime()
    common = {
        "release_dir": str(release), "release_input_sha256": "a" * 64,
        "build_identity_sha256": "b" * 64, "ready_sha256": "c" * 64,
        "app_path": str(app), "python_path": str(python), "record_count": 1,
        "builder_origin": {"expected_bootstrap_root": str(tmp_path / "bootstrap")},
        "atlas_gate_e_runtime_receipt": {"base_runtime": base},
        "atlas_gate_e_runtime_receipt_sha256": "d" * 64,
    }

    def fake_verify(_release: Path, *, verify_runtime: bool):
        calls.append("full_verify" if verify_runtime else "metadata_verify")
        return {
            **common,
            "schema_version": gate.VERIFY_SCHEMA,
            "status": "PASS" if verify_runtime else "METADATA_PASS",
            "runtime_verified": verify_runtime,
        }

    def fake_seal(*_args, **_kwargs):
        calls.append("seal")
        return {"status": "PASS"}

    monkeypatch.setattr(gate, "_verify_sealed_release", fake_verify)
    monkeypatch.setattr(
        gate, "verify_sealed_release",
        lambda value: fake_verify(value, verify_runtime=True),
    )
    monkeypatch.setattr(gate, "_production_seal_control", fake_seal)
    monkeypatch.setattr(gate, "_validate_ceremony_arguments", lambda *_a, **_k: None)
    monkeypatch.setattr(gate, "_network_isolation_contract", _fake_network)
    def fake_ceremony_command(*_args, maps_receipt_fd: int, **_kwargs):
        os.write(
            maps_receipt_fd,
            _canonical({
                "schema_version": "caerus_alpha_lab_ceremony_child_maps_v1",
                "external_mapped_paths": [],
            }),
        )
        return ["python"]

    monkeypatch.setattr(gate, "_isolated_ceremony_command", fake_ceremony_command)

    class Result:
        returncode = 0

    def fake_run(*_args, **_kwargs):
        calls.append("run")
        (output / "artifact.json").write_bytes(b"{}")
        return Result()

    monkeypatch.setattr(gate.subprocess, "run", fake_run)
    assert gate.run_ceremony(release, ["allowed"], approved_output_root=output) == 0
    assert calls == [
        "metadata_verify", "seal", "full_verify", "run", "full_verify", "seal"
    ]
    success_files = list((output / ".gate_e_success").glob("*.json"))
    assert len(success_files) == 1
    success = gate._strict_canonical_json(
        success_files[0].read_bytes(), label="test Gate E success", object_required=True
    )
    assert success["status"] == "PASS"
    assert success["schema_version"] == "caerus_alpha_lab_gate_e_ceremony_success_v2"
    assert success["network_isolation"] == _fake_network()
    assert success["post_execution_rescan_passed"] is True
    assert success["approved_output_root"] == str(output)
    assert success["output_delta"]["created_record_count"] == 1
    assert success["output_delta"]["created_records"][0]["path"] == "artifact.json"


def test_ceremony_postscan_drift_never_writes_success_receipt(
    tmp_path: Path, monkeypatch,
) -> None:
    release = tmp_path / "release"
    app = release / "app"
    python = release / "venv/bin/python"
    app.mkdir(parents=True)
    python.parent.mkdir(parents=True)
    output = tmp_path / "output"
    output.mkdir()
    calls = 0
    common = {
        "release_dir": str(release), "release_input_sha256": "a" * 64,
        "build_identity_sha256": "b" * 64, "ready_sha256": "c" * 64,
        "app_path": str(app), "python_path": str(python), "record_count": 1,
        "builder_origin": {"expected_bootstrap_root": str(tmp_path / "bootstrap")},
        "atlas_gate_e_runtime_receipt": {"base_runtime": _fake_base_runtime()},
        "atlas_gate_e_runtime_receipt_sha256": "d" * 64,
    }

    def fake_verify(_release: Path, *, verify_runtime: bool):
        nonlocal calls
        if verify_runtime:
            calls += 1
        return {
            **common,
            "ready_sha256": "f" * 64 if calls == 2 else common["ready_sha256"],
            "schema_version": gate.VERIFY_SCHEMA,
            "status": "PASS" if verify_runtime else "METADATA_PASS",
            "runtime_verified": verify_runtime,
        }

    monkeypatch.setattr(gate, "_verify_sealed_release", fake_verify)
    monkeypatch.setattr(
        gate, "verify_sealed_release",
        lambda value: fake_verify(value, verify_runtime=True),
    )
    monkeypatch.setattr(
        gate, "_production_seal_control", lambda *_a, **_k: {"status": "PASS"}
    )
    monkeypatch.setattr(gate, "_validate_ceremony_arguments", lambda *_a, **_k: None)
    monkeypatch.setattr(gate, "_network_isolation_contract", _fake_network)
    def fake_ceremony_command(*_args, maps_receipt_fd: int, **_kwargs):
        os.write(
            maps_receipt_fd,
            _canonical({
                "schema_version": "caerus_alpha_lab_ceremony_child_maps_v1",
                "external_mapped_paths": [],
            }),
        )
        return ["python"]

    monkeypatch.setattr(gate, "_isolated_ceremony_command", fake_ceremony_command)
    monkeypatch.setattr(
        gate.subprocess, "run",
        lambda *_a, **_k: subprocess.CompletedProcess(["python"], 0),
    )
    with pytest.raises(gate.ReleaseBuildError, match="changed during ceremony"):
        gate.run_ceremony(release, ["allowed"], approved_output_root=output)
    assert not (output / ".gate_e_success").exists()


def test_ceremony_child_maps_must_be_in_sealed_external_receipt(
    tmp_path: Path,
) -> None:
    receipt_bytes = _canonical(
        {
            "schema_version": "caerus_alpha_lab_ceremony_child_maps_v1",
            "external_mapped_paths": ["/usr/lib/liblate.so"],
        }
    )
    with pytest.raises(gate.ReleaseBuildError, match="outside the sealed TCB"):
        gate._verify_ceremony_child_maps(
            receipt_bytes, base_runtime=_fake_base_runtime()
        )
    base = _fake_base_runtime()
    base["loaded_shared_objects"] = [
        {
            **base["base_executable"],
            "path": "/usr/lib/liblate.so",
            "sha256": "f" * 64,
        }
    ]
    verified = gate._verify_ceremony_child_maps(receipt_bytes, base_runtime=base)
    assert verified["all_paths_present_in_sealed_base_runtime"] is True
    assert verified["external_mapped_path_count"] == 1


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


def test_preflight_parser_forbids_failure_write_arguments() -> None:
    common = [
        "--repo-root", "/repo", "--source-archive", "/source.tar",
        "--source-manifest", "/source.json", "--file-manifest", "/files.json",
        "--wheelhouse", "/wheels", "--release-input-manifest", "/input.json",
        "--release-parent", "/release",
    ]
    with pytest.raises(SystemExit):
        gate._parser().parse_args(
            ["preflight", *common, "--attempt-id", _ATTEMPT_ID]
        )
    with pytest.raises(SystemExit):
        gate._parser().parse_args(
            ["preflight", *common, "--failure-evidence-root", "/failure"]
        )


def test_build_parser_accepts_exact_failure_authorization_arguments() -> None:
    common = [
        "--repo-root", "/repo", "--source-archive", "/source.tar",
        "--source-manifest", "/source.json", "--file-manifest", "/files.json",
        "--wheelhouse", "/wheels", "--release-input-manifest", "/input.json",
        "--release-parent", "/release",
    ]
    arguments = gate._parser().parse_args(
        [
            "build", *common, "--write", "--attempt-id", _ATTEMPT_ID,
            "--failure-evidence-root", f"/failure/{_ATTEMPT_ID}",
        ]
    )
    assert arguments.attempt_id == _ATTEMPT_ID
    assert arguments.failure_evidence_root == Path(f"/failure/{_ATTEMPT_ID}")
