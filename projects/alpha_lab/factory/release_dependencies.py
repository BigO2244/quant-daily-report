"""Validate the Phase 1 binary-only clean-release dependency contract.

The validator is intentionally standard-library-only so Gate A can run before
the release environment exists.  It validates repository inputs on every run
and, when given a wheelhouse, validates the exact binary artifacts and their
embedded metadata without installing or executing them.
"""

from __future__ import annotations

import argparse
import ast
import hashlib
import io
import json
import os
import re
import stat
import sys
import zipfile
from email.parser import BytesParser
from email.policy import compat32
from pathlib import Path
from typing import Any, Dict, Iterable, Mapping, Optional, Sequence, Tuple


MANIFEST_RELATIVE_PATH = Path(
    "projects/alpha_lab/release/phase1-cp310-linux-x86_64-wheel-manifest.json"
)
MANIFEST_SCHEMA = "caerus_alpha_lab_phase1_wheel_manifest_v1"
EXPECTED_TARGET = {
    "operating_system": "Ubuntu",
    "operating_system_version": "22.04",
    "architecture": "x86_64",
    "glibc_version": "2.35",
    "python_implementation": "CPython",
    "python_version": "3.10.12",
    "python_tag": "cp310",
    "abi_tag": "cp310",
}
_TARGET_VERSION = (3, 10, 12)
_TARGET_GLIBC_MINOR = 35
_LOCK_LINE = re.compile(
    r"^(?P<name>[A-Za-z0-9][A-Za-z0-9._-]*)==(?P<version>[^\s]+) "
    r"--hash=sha256:(?P<sha256>[0-9a-f]{64})$"
)
_REQUIREMENT_NAME = re.compile(r"^\s*([A-Za-z0-9][A-Za-z0-9._-]*)")
_MARKER_TOKEN = re.compile(
    r"\s*(?:(and|or|not\s+in|in|==|!=|<=|>=|<|>)|([A-Za-z_][A-Za-z0-9_]*)|"
    r"('(?:[^'\\]|\\.)*'|\"(?:[^\"\\]|\\.)*\")|(\()|(\)))"
)


class ReleaseDependencyError(ValueError):
    """The release dependency contract is incomplete or has drifted."""


def _normalize_name(value: str) -> str:
    return re.sub(r"[-_.]+", "-", value).lower()


def _sha256(value: bytes) -> str:
    return hashlib.sha256(value).hexdigest()


def _strict_json_object(path: Path) -> Dict[str, Any]:
    def object_pairs(pairs: Sequence[Tuple[str, Any]]) -> Dict[str, Any]:
        result: Dict[str, Any] = {}
        for key, value in pairs:
            if key in result:
                raise ReleaseDependencyError(f"duplicate JSON key in {path}: {key}")
            result[key] = value
        return result

    try:
        value = json.loads(
            path.read_text(encoding="utf-8"),
            object_pairs_hook=object_pairs,
            parse_constant=lambda token: (_ for _ in ()).throw(
                ReleaseDependencyError(f"non-finite JSON value in {path}: {token}")
            ),
        )
    except (OSError, UnicodeError, json.JSONDecodeError) as exc:
        raise ReleaseDependencyError(f"cannot read strict JSON manifest {path}") from exc
    if not isinstance(value, dict):
        raise ReleaseDependencyError("wheel manifest must be a JSON object")
    return value


def _safe_repo_file(repo_root: Path, relative: str) -> Path:
    candidate = (repo_root / relative).resolve()
    try:
        candidate.relative_to(repo_root)
    except ValueError as exc:
        raise ReleaseDependencyError(f"manifest path escapes repository: {relative}") from exc
    if not candidate.is_file():
        raise ReleaseDependencyError(f"manifest file is missing: {relative}")
    return candidate


def _parse_lock(value: bytes) -> Dict[str, Tuple[str, str]]:
    try:
        text = value.decode("utf-8")
    except UnicodeDecodeError as exc:
        raise ReleaseDependencyError("lock must be UTF-8") from exc
    requirements: Dict[str, Tuple[str, str]] = {}
    for number, raw_line in enumerate(text.splitlines(), start=1):
        line = raw_line.strip()
        if not line or line.startswith("#"):
            continue
        match = _LOCK_LINE.fullmatch(line)
        if match is None:
            raise ReleaseDependencyError(
                f"lock line {number} is not one exact pinned, singly hashed requirement"
            )
        name = _normalize_name(match.group("name"))
        if name in requirements:
            raise ReleaseDependencyError(f"duplicate locked requirement: {name}")
        requirements[name] = (match.group("version"), match.group("sha256"))
    if not requirements:
        raise ReleaseDependencyError("lock has no requirements")
    return requirements


def _parse_version(value: str) -> Tuple[int, ...]:
    match = re.match(r"^(\d+(?:\.\d+)*)", value)
    if match is None:
        raise ReleaseDependencyError(f"unsupported version expression: {value}")
    return tuple(int(part) for part in match.group(1).split("."))


def _compare(left: Tuple[int, ...], right: Tuple[int, ...], operator: str) -> bool:
    width = max(len(left), len(right))
    lhs = left + (0,) * (width - len(left))
    rhs = right + (0,) * (width - len(right))
    return {
        "==": lhs == rhs,
        "!=": lhs != rhs,
        "<": lhs < rhs,
        "<=": lhs <= rhs,
        ">": lhs > rhs,
        ">=": lhs >= rhs,
    }[operator]


def _requires_python_allows_target(specifier: Optional[str]) -> bool:
    if specifier is None:
        return True
    for item in specifier.split(","):
        item = item.strip()
        match = re.fullmatch(r"(==|!=|<=|>=|<|>)(\d+(?:\.\d+)*(?:\.\*)?)", item)
        if match is None:
            raise ReleaseDependencyError(f"unsupported Requires-Python: {specifier}")
        operator, version = match.groups()
        if version.endswith(".*"):
            prefix = tuple(int(part) for part in version[:-2].split("."))
            equal = _TARGET_VERSION[: len(prefix)] == prefix
            if operator == "==" and not equal:
                return False
            if operator == "!=" and equal:
                return False
            if operator not in {"==", "!="}:
                raise ReleaseDependencyError(f"unsupported wildcard specifier: {item}")
        elif not _compare(_TARGET_VERSION, _parse_version(version), operator):
            return False
    return True


def _wheel_components(filename: str, distribution: str, version: str) -> Tuple[str, str, str]:
    if not filename.endswith(".whl"):
        raise ReleaseDependencyError(f"source distribution is forbidden: {filename}")
    body = filename[:-4]
    try:
        base, python_tag, abi_tag, platform_tag = body.rsplit("-", 3)
    except ValueError as exc:
        raise ReleaseDependencyError(f"malformed wheel filename: {filename}") from exc
    expected_base = f"{distribution.replace('-', '_')}-{version}"
    if base != expected_base:
        raise ReleaseDependencyError(
            f"wheel filename/version drift: {filename} != {expected_base}"
        )
    return python_tag, abi_tag, platform_tag


def _tag_compatible(python_tag: str, abi_tag: str, platform_tag: str) -> bool:
    python_tags = python_tag.split(".")
    abi_tags = abi_tag.split(".")
    platform_tags = platform_tag.split(".")
    for py_tag in python_tags:
        for abi in abi_tags:
            python_abi_ok = False
            if py_tag in {"py3", "py2.py3"} and abi == "none":
                python_abi_ok = True
            elif py_tag == "cp310" and abi == "cp310":
                python_abi_ok = True
            elif re.fullmatch(r"cp3\d+", py_tag) and abi == "abi3":
                python_abi_ok = int(py_tag[3:]) <= 10
            if not python_abi_ok:
                continue
            for platform in platform_tags:
                if platform == "any" and abi == "none":
                    return True
                if platform == "manylinux2014_x86_64":
                    return True
                match = re.fullmatch(r"manylinux_2_(\d+)_x86_64", platform)
                if match and int(match.group(1)) <= _TARGET_GLIBC_MINOR:
                    return True
    return False


def _expanded_filename_tags(python_tag: str, abi_tag: str, platform_tag: str) -> set[str]:
    return {
        f"{py}-{abi}-{platform}"
        for py in python_tag.split(".")
        for abi in abi_tag.split(".")
        for platform in platform_tag.split(".")
    }


def _tokenize_marker(marker: str) -> list[Tuple[str, str]]:
    tokens: list[Tuple[str, str]] = []
    offset = 0
    while offset < len(marker):
        match = _MARKER_TOKEN.match(marker, offset)
        if match is None:
            raise ReleaseDependencyError(f"unsupported dependency marker: {marker}")
        operator, identifier, quoted, left, right = match.groups()
        if operator:
            tokens.append(("op", " ".join(operator.split())))
        elif identifier:
            tokens.append(("identifier", identifier))
        elif quoted:
            tokens.append(("string", ast.literal_eval(quoted)))
        elif left:
            tokens.append(("left", left))
        else:
            tokens.append(("right", right))
        offset = match.end()
    return tokens


class _MarkerParser:
    _environment = {
        "extra": "",
        "implementation_name": "cpython",
        "platform_python_implementation": "CPython",
        "python_full_version": "3.10.12",
        "python_version": "3.10",
        "sys_platform": "linux",
    }

    def __init__(self, tokens: Sequence[Tuple[str, str]]) -> None:
        self.tokens = tokens
        self.offset = 0

    def parse(self) -> bool:
        value = self._or_expression()
        if self.offset != len(self.tokens):
            raise ReleaseDependencyError("unexpected dependency marker token")
        return value

    def _accept(self, kind: str, value: Optional[str] = None) -> Optional[str]:
        if self.offset >= len(self.tokens):
            return None
        token_kind, token_value = self.tokens[self.offset]
        if token_kind != kind or (value is not None and token_value != value):
            return None
        self.offset += 1
        return token_value

    def _or_expression(self) -> bool:
        value = self._and_expression()
        while self._accept("op", "or") is not None:
            right = self._and_expression()
            value = value or right
        return value

    def _and_expression(self) -> bool:
        value = self._atom()
        while self._accept("op", "and") is not None:
            right = self._atom()
            value = value and right
        return value

    def _atom(self) -> bool:
        if self._accept("left") is not None:
            value = self._or_expression()
            if self._accept("right") is None:
                raise ReleaseDependencyError("unterminated dependency marker")
            return value
        variable = self._accept("identifier")
        if variable is None or variable not in self._environment:
            raise ReleaseDependencyError(f"unknown dependency marker variable: {variable}")
        operator = self._accept("op")
        expected = self._accept("string")
        if operator not in {"==", "!=", "<", "<=", ">", ">=", "in", "not in"} or expected is None:
            raise ReleaseDependencyError("malformed dependency marker comparison")
        actual = self._environment[variable]
        if operator in {"in", "not in"}:
            result = actual in expected
            return not result if operator == "not in" else result
        if variable in {"python_version", "python_full_version"}:
            return _compare(_parse_version(actual), _parse_version(expected), operator)
        return {
            "==": actual == expected,
            "!=": actual != expected,
            "<": actual < expected,
            "<=": actual <= expected,
            ">": actual > expected,
            ">=": actual >= expected,
        }[operator]


def _target_dependencies(requires_dist: Iterable[str]) -> list[str]:
    result: set[str] = set()
    for requirement in requires_dist:
        expression, separator, marker = requirement.partition(";")
        match = _REQUIREMENT_NAME.match(expression)
        if match is None:
            raise ReleaseDependencyError(f"malformed Requires-Dist: {requirement}")
        if separator and not _MarkerParser(_tokenize_marker(marker.strip())).parse():
            continue
        result.add(_normalize_name(match.group(1)))
    return sorted(result)


def _third_party_imports(alpha_root: Path) -> set[str]:
    internal_roots = {"projects"}
    standard_library = set(sys.stdlib_module_names)
    discovered: set[str] = set()
    for path in sorted(alpha_root.rglob("*.py")):
        if "__pycache__" in path.parts:
            continue
        try:
            tree = ast.parse(path.read_text(encoding="utf-8"), filename=str(path))
        except (OSError, UnicodeError, SyntaxError) as exc:
            raise ReleaseDependencyError(f"cannot inspect imports in {path}") from exc
        for node in ast.walk(tree):
            root: Optional[str] = None
            if isinstance(node, ast.Import):
                for alias in node.names:
                    root = alias.name.split(".", 1)[0]
                    if root not in standard_library | internal_roots:
                        discovered.add(root)
            elif isinstance(node, ast.ImportFrom) and node.level == 0 and node.module:
                root = node.module.split(".", 1)[0]
                if root not in standard_library | internal_roots:
                    discovered.add(root)
    return discovered


def _pytest_importorskip_modules(tests_root: Path) -> list[str]:
    modules: list[str] = []
    for path in sorted(tests_root.rglob("test_*.py")):
        tree = ast.parse(path.read_text(encoding="utf-8"), filename=str(path))
        for node in ast.walk(tree):
            if not isinstance(node, ast.Call) or not isinstance(node.func, ast.Attribute):
                continue
            if (
                isinstance(node.func.value, ast.Name)
                and node.func.value.id == "pytest"
                and node.func.attr == "importorskip"
                and node.args
                and isinstance(node.args[0], ast.Constant)
                and isinstance(node.args[0].value, str)
            ):
                modules.append(node.args[0].value.split(".", 1)[0])
    return modules


def _validate_manifest_shape(manifest: Mapping[str, Any]) -> None:
    required = {
        "schema_version",
        "classification",
        "dependency_resolution_base_commit",
        "target",
        "generator",
        "lock",
        "import_contract",
        "optional_exclusions",
        "wheels",
    }
    if set(manifest) != required or manifest.get("schema_version") != MANIFEST_SCHEMA:
        raise ReleaseDependencyError("wheel manifest schema is invalid")
    if manifest.get("classification") != "RELEASE_TEST_DEPENDENCY_CONTRACT":
        raise ReleaseDependencyError("wheel manifest classification is invalid")
    if not re.fullmatch(
        r"[0-9a-f]{40}",
        str(manifest.get("dependency_resolution_base_commit", "")),
    ):
        raise ReleaseDependencyError(
            "wheel manifest dependency-resolution base commit is invalid"
        )
    if manifest.get("target") != EXPECTED_TARGET:
        raise ReleaseDependencyError("wheel manifest target drifted")


def _validate_generator(generator: Mapping[str, Any]) -> None:
    fields = {
        "resolver", "index", "inputs", "platform_arguments",
        "download_command", "no_index_install_command",
    }
    if set(generator) != fields:
        raise ReleaseDependencyError("generator record schema is invalid")
    if generator.get("index") != "https://pypi.org/simple":
        raise ReleaseDependencyError("generator index drifted")
    if generator.get("platform_arguments") != [
        "manylinux_2_34_x86_64",
        "manylinux_2_28_x86_64",
        "manylinux_2_17_x86_64",
        "manylinux2014_x86_64",
    ]:
        raise ReleaseDependencyError("generator target platforms drifted")
    download = generator.get("download_command")
    install = generator.get("no_index_install_command")
    if not isinstance(download, str) or not all(
        token in download
        for token in (
            "pip download", "--require-hashes", "--only-binary=:all:",
            "--python-version 3.10", "--implementation cp", "--abi cp310",
            "phase1-cp310-linux-x86_64.lock",
        )
    ):
        raise ReleaseDependencyError("generator binary download command drifted")
    if not isinstance(install, str) or not all(
        token in install
        for token in (
            "pip install", "--no-index", "--find-links=", "--require-hashes",
            "phase1-cp310-linux-x86_64.lock",
        )
    ):
        raise ReleaseDependencyError("generator no-index install command drifted")


def _direct_pins(path: Path) -> Dict[str, str]:
    result: Dict[str, str] = {}
    for number, raw in enumerate(path.read_text(encoding="utf-8").splitlines(), start=1):
        line = raw.strip()
        if not line or line.startswith("#"):
            continue
        match = re.fullmatch(r"([A-Za-z0-9][A-Za-z0-9._-]*)==([^\s]+)", line)
        if match is None:
            raise ReleaseDependencyError(f"runtime input line {number} is not exactly pinned")
        name = _normalize_name(match.group(1))
        if name in result:
            raise ReleaseDependencyError(f"duplicate runtime input pin: {name}")
        result[name] = match.group(2)
    return result


def _validate_wheelhouse_files(
    wheelhouse: Path, expected_files: Mapping[str, Mapping[str, Any]]
) -> None:
    """Validate the exact wheel set, bytes, metadata, and embedded tags."""

    wheelhouse = wheelhouse.expanduser().absolute()
    flags = (
        os.O_RDONLY
        | getattr(os, "O_DIRECTORY", 0)
        | getattr(os, "O_NOFOLLOW", 0)
        | getattr(os, "O_CLOEXEC", 0)
    )
    try:
        wheelhouse_fd = os.open(str(wheelhouse), flags)
    except OSError as exc:
        raise ReleaseDependencyError(f"wheelhouse is not a no-follow directory: {wheelhouse}") from exc
    actual_names = set(os.listdir(wheelhouse_fd))
    if actual_names != set(expected_files):
        os.close(wheelhouse_fd)
        missing = sorted(set(expected_files) - actual_names)
        extra = sorted(actual_names - set(expected_files))
        raise ReleaseDependencyError(
            f"wheelhouse has extra or missing files; missing={missing}, extra={extra}"
        )
    try:
        for filename in sorted(actual_names):
            try:
                descriptor = os.open(
                    filename,
                    os.O_RDONLY
                    | getattr(os, "O_NOFOLLOW", 0)
                    | getattr(os, "O_CLOEXEC", 0),
                    dir_fd=wheelhouse_fd,
                )
            except OSError as exc:
                raise ReleaseDependencyError(
                    f"wheelhouse entry is not a no-follow regular file: {filename}"
                ) from exc
            try:
                before = os.fstat(descriptor)
                if not stat.S_ISREG(before.st_mode) or before.st_nlink != 1:
                    raise ReleaseDependencyError(
                        f"wheelhouse entry is not a single-link regular file: {filename}"
                    )
                chunks = []
                while True:
                    chunk = os.read(descriptor, 1024 * 1024)
                    if not chunk:
                        break
                    chunks.append(chunk)
                after = os.fstat(descriptor)
            finally:
                os.close(descriptor)
            if (before.st_dev, before.st_ino, before.st_size, before.st_mtime_ns) != (
                after.st_dev, after.st_ino, after.st_size, after.st_mtime_ns
            ):
                raise ReleaseDependencyError(
                    f"wheelhouse entry changed while reading: {filename}"
                )
            value = b"".join(chunks)
            record = expected_files[filename]
            if len(value) != record["bytes"] or _sha256(value) != record["sha256"]:
                raise ReleaseDependencyError(f"wheel file hash/size drift: {filename}")
            try:
                with zipfile.ZipFile(io.BytesIO(value)) as archive:
                    metadata_names = [
                        name for name in archive.namelist()
                        if name.endswith(".dist-info/METADATA")
                    ]
                    wheel_names = [
                        name for name in archive.namelist()
                        if name.endswith(".dist-info/WHEEL")
                    ]
                    if len(metadata_names) != 1 or len(wheel_names) != 1:
                        raise ReleaseDependencyError(f"wheel metadata layout is invalid: {filename}")
                    metadata_bytes = archive.read(metadata_names[0])
                    wheel_bytes = archive.read(wheel_names[0])
            except (OSError, zipfile.BadZipFile, KeyError) as exc:
                raise ReleaseDependencyError(f"cannot inspect wheel: {filename}") from exc
            if _sha256(metadata_bytes) != record["metadata_sha256"]:
                raise ReleaseDependencyError(f"embedded METADATA drift: {filename}")
            if _sha256(wheel_bytes) != record["wheel_metadata_sha256"]:
                raise ReleaseDependencyError(f"embedded WHEEL metadata drift: {filename}")
            metadata = BytesParser(policy=compat32).parsebytes(metadata_bytes)
            distribution = _normalize_name(str(metadata.get("Name", "")))
            if distribution != _normalize_name(str(record["distribution"])):
                raise ReleaseDependencyError(f"wheel distribution metadata drift: {filename}")
            if metadata.get("Version") != record["version"]:
                raise ReleaseDependencyError(f"wheel version metadata drift: {filename}")
            if metadata.get("Requires-Python") != record["requires_python"]:
                raise ReleaseDependencyError(f"wheel Requires-Python drift: {filename}")
            actual_dependencies = _target_dependencies(metadata.get_all("Requires-Dist", []))
            if actual_dependencies != record["target_dependencies"]:
                raise ReleaseDependencyError(f"target dependency metadata drift: {filename}")
            wheel_metadata = BytesParser(policy=compat32).parsebytes(wheel_bytes)
            python_tag, abi_tag, platform_tag = _wheel_components(
                filename, distribution, str(record["version"])
            )
            embedded_tags = set(wheel_metadata.get_all("Tag", []))
            if embedded_tags != _expanded_filename_tags(python_tag, abi_tag, platform_tag):
                raise ReleaseDependencyError(f"wheel tag metadata drift: {filename}")
    finally:
        os.close(wheelhouse_fd)


def validate_release_dependency_contract(
    repo_root: Path, *, wheelhouse: Optional[Path] = None
) -> Dict[str, Any]:
    repo_root = repo_root.expanduser().resolve()
    manifest_path = _safe_repo_file(repo_root, str(MANIFEST_RELATIVE_PATH))
    manifest = _strict_json_object(manifest_path)
    _validate_manifest_shape(manifest)

    generator = manifest["generator"]
    if not isinstance(generator, Mapping) or not isinstance(generator.get("inputs"), list):
        raise ReleaseDependencyError("generator inputs are invalid")
    _validate_generator(generator)
    if [item.get("path") for item in generator["inputs"] if isinstance(item, Mapping)] != [
        "projects/alpha_lab/requirements.in",
        "projects/alpha_lab/release/phase1-test-requirements.in",
    ]:
        raise ReleaseDependencyError("generator input path set drifted")
    for item in generator["inputs"]:
        if not isinstance(item, Mapping) or set(item) != {"path", "bytes", "sha256"}:
            raise ReleaseDependencyError("generator input record is invalid")
        path = _safe_repo_file(repo_root, str(item["path"]))
        value = path.read_bytes()
        if len(value) != item["bytes"] or _sha256(value) != item["sha256"]:
            raise ReleaseDependencyError(f"generator input drift: {item['path']}")

    lock_record = manifest["lock"]
    if not isinstance(lock_record, Mapping) or set(lock_record) != {
        "path", "bytes", "sha256", "requirement_count", "wheel_count"
    }:
        raise ReleaseDependencyError("lock record is invalid")
    lock_path = _safe_repo_file(repo_root, str(lock_record["path"]))
    lock_bytes = lock_path.read_bytes()
    if len(lock_bytes) != lock_record["bytes"] or _sha256(lock_bytes) != lock_record["sha256"]:
        raise ReleaseDependencyError("lock identity drift")
    locked = _parse_lock(lock_bytes)
    if len(locked) != lock_record["requirement_count"]:
        raise ReleaseDependencyError("lock requirement count drift")

    import_contract = manifest["import_contract"]
    if not isinstance(import_contract, Mapping):
        raise ReleaseDependencyError("import contract is invalid")
    discovered = _third_party_imports(repo_root / "projects/alpha_lab")
    if discovered != set(import_contract):
        missing = sorted(discovered - set(import_contract))
        extra = sorted(set(import_contract) - discovered)
        raise ReleaseDependencyError(
            f"undeclared import contract drift; missing={missing}, stale={extra}"
        )
    for imported, record in import_contract.items():
        if not isinstance(record, Mapping):
            raise ReleaseDependencyError(f"invalid import contract: {imported}")
        distribution = _normalize_name(str(record.get("distribution", "")))
        status = record.get("status")
        if status in {"REQUIRED_RUNTIME", "REQUIRED_TEST"} and distribution not in locked:
            raise ReleaseDependencyError(f"required import is not locked: {imported}")
        if status in {"OPTIONAL_EXCLUDED", "EXTERNAL_ADAPTER_MOCKED_FOR_TEST"} and distribution in locked:
            raise ReleaseDependencyError(f"excluded import unexpectedly entered lock: {imported}")
        if status not in {
            "REQUIRED_RUNTIME", "REQUIRED_TEST", "OPTIONAL_EXCLUDED",
            "EXTERNAL_ADAPTER_MOCKED_FOR_TEST",
        }:
            raise ReleaseDependencyError(f"unknown import contract status: {status}")
    runtime_contract = {
        _normalize_name(str(record["distribution"]))
        for record in import_contract.values()
        if isinstance(record, Mapping) and record.get("status") == "REQUIRED_RUNTIME"
    }
    runtime_input = _direct_pins(repo_root / "projects/alpha_lab/requirements.in")
    if set(runtime_input) != runtime_contract:
        raise ReleaseDependencyError("runtime requirement/import declaration drift")
    for name, version in runtime_input.items():
        if locked.get(name, (None, None))[0] != version:
            raise ReleaseDependencyError(f"runtime requirement/lock version drift: {name}")
    importorskip_modules = _pytest_importorskip_modules(
        repo_root / "projects/alpha_lab/tests"
    )
    if not set(importorskip_modules) <= set(import_contract):
        raise ReleaseDependencyError("pytest importorskip uses an undeclared dependency")
    exclusions = manifest["optional_exclusions"]
    if not isinstance(exclusions, list):
        raise ReleaseDependencyError("optional exclusions are invalid")
    excluded_contract = {
        _normalize_name(str(record["distribution"]))
        for record in import_contract.values()
        if isinstance(record, Mapping) and record.get("status") == "OPTIONAL_EXCLUDED"
    }
    excluded_records: set[str] = set()
    for exclusion in exclusions:
        if not isinstance(exclusion, Mapping) or set(exclusion) != {
            "distribution", "test_behavior", "expected_skipped_tests", "runtime_effect"
        }:
            raise ReleaseDependencyError("optional exclusion record is invalid")
        distribution = _normalize_name(str(exclusion["distribution"]))
        if exclusion["test_behavior"] != "pytest.importorskip":
            raise ReleaseDependencyError("optional exclusion test behavior drifted")
        if importorskip_modules.count(distribution) != exclusion["expected_skipped_tests"]:
            raise ReleaseDependencyError("optional exclusion skip census drifted")
        excluded_records.add(distribution)
    if excluded_records != excluded_contract:
        raise ReleaseDependencyError("optional exclusion/import contract drift")

    wheels = manifest["wheels"]
    if not isinstance(wheels, list) or len(wheels) != lock_record["wheel_count"]:
        raise ReleaseDependencyError("wheel manifest count drift")
    expected_files: Dict[str, Mapping[str, Any]] = {}
    manifested_names: set[str] = set()
    wheel_fields = {
        "distribution", "version", "filename", "bytes", "sha256",
        "requires_python", "metadata_sha256", "wheel_metadata_sha256",
        "target_dependencies",
    }
    for record in wheels:
        if not isinstance(record, Mapping) or set(record) != wheel_fields:
            raise ReleaseDependencyError("wheel record schema is invalid")
        distribution = _normalize_name(str(record["distribution"]))
        if distribution in manifested_names:
            raise ReleaseDependencyError(f"duplicate manifested distribution: {distribution}")
        manifested_names.add(distribution)
        filename = str(record["filename"])
        if filename in expected_files:
            raise ReleaseDependencyError(f"duplicate manifested wheel: {filename}")
        expected_files[filename] = record
        if distribution not in locked:
            raise ReleaseDependencyError(f"manifested distribution is not locked: {distribution}")
        version, digest = locked[distribution]
        if str(record["version"]) != version or str(record["sha256"]) != digest:
            raise ReleaseDependencyError(f"lock/manifest version or hash drift: {distribution}")
        python_tag, abi_tag, platform_tag = _wheel_components(
            filename, distribution, version
        )
        if not _tag_compatible(python_tag, abi_tag, platform_tag):
            raise ReleaseDependencyError(f"wrong target platform or ABI: {filename}")
        if not _requires_python_allows_target(record["requires_python"]):
            raise ReleaseDependencyError(f"wheel rejects CPython 3.10.12: {filename}")
        dependencies = record["target_dependencies"]
        if not isinstance(dependencies, list) or dependencies != sorted(set(dependencies)):
            raise ReleaseDependencyError(f"target dependency list is not canonical: {filename}")
        missing_dependencies = set(dependencies) - set(locked)
        if missing_dependencies:
            raise ReleaseDependencyError(
                f"wheel dependency closure is incomplete for {filename}: {sorted(missing_dependencies)}"
            )
    if manifested_names != set(locked):
        raise ReleaseDependencyError("lock has extra or missing manifested distributions")

    if wheelhouse is not None:
        _validate_wheelhouse_files(wheelhouse, expected_files)

    return {
        "schema_version": "caerus_alpha_lab_phase1_dependency_validation_v1",
        "status": "PASS",
        "lock_sha256": lock_record["sha256"],
        "requirement_count": len(locked),
        "wheel_count": len(wheels),
        "wheelhouse_verified": wheelhouse is not None,
        "target": EXPECTED_TARGET,
    }


def _parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(
        description="Validate the Alpha Lab Phase 1 clean-release dependency contract"
    )
    parser.add_argument("--repo-root", type=Path, default=Path.cwd())
    parser.add_argument("--wheelhouse", type=Path)
    return parser


def main(argv: Optional[Sequence[str]] = None) -> int:
    arguments = _parser().parse_args(argv)
    try:
        result = validate_release_dependency_contract(
            arguments.repo_root, wheelhouse=arguments.wheelhouse
        )
    except ReleaseDependencyError as exc:
        print(json.dumps({"status": "FAIL", "error": str(exc)}, sort_keys=True))
        return 1
    print(json.dumps(result, sort_keys=True))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
