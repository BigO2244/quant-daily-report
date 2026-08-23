# Alpha Lab Public Signing Ceremony

Status: `RESEARCH_ONLY_CONTROL`  
Authority: public preparation and verification only

## Boundary

The ceremony command prepares exact canonical bytes for an external Ed25519
signer and verifies the detached signature afterward. It never creates, loads,
accepts, or stores a private key. A signing service, hardware device, or offline
owner credential receives only `signing_payload.json`.

Run all commands from the repository root:

```bash
python -m projects.alpha_lab.factory.ceremony --help
python -m projects.alpha_lab.factory.ceremony registry --help
python -m projects.alpha_lab.factory.ceremony attestation --help
python -m projects.alpha_lab.factory.ceremony migration --help
python -m projects.alpha_lab.factory.ceremony publication --help
python -m projects.alpha_lab.factory.ceremony projection --help
```

All JSON input is strict: duplicate keys, non-finite numbers, malformed public
keys, unknown signing fields, path substitutions, stale pins, and secret
material fail closed. Output files and preparation directories are create-only.

## Gate A: clean release dependencies

Gate A must pass on the release candidate before any registry, migration,
publication, or projection preparation. Gate A targets exactly Ubuntu 22.04
x86_64, glibc 2.35, and CPython 3.10.12. The dependency contract declares
Alpha's runtime imports in `requirements.in`, locks every test dependency to
one hashed binary wheel, and records each wheel's filename, size, SHA-256,
metadata hashes, target tags, and target dependency closure. The wheel
manifest's `dependency_resolution_base_commit` records resolver lineage only;
it is not the release source identity. The separately reviewed source archive,
source manifest, file manifest, and canonical release-input manifest bind the
final committed source.
The source verifier also requires the `git archive` global PAX `comment` to
equal the declared commit and reconstructs the complete Git tree OID from the
exact file modes and bytes; commit and tree labels are therefore checked
provenance, not caller assertions.

The operator receives these reviewed, absolute-path inputs:

- the exact `git archive` tar, canonical source manifest, and canonical
  file-only manifest for the final reviewed commit;
- the 25 binary wheels named by
  `phase1-cp310-linux-x86_64-wheel-manifest.json`;
- the canonical `caerus_alpha_lab_clean_release_input_v1` manifest; and
- the approved release parent. Wheel binaries and generated release/source
  manifests are ceremony inputs and must not enter Git.

### Protected Gate A handoff and dirty-checkout preservation

The network-enabled staging area is not a Gate A input root. Before bootstrap,
an administrator must use the separately reviewed, standard-library-only
`gate_a_handoff.py` to copy exactly the five named files plus the 25-wheel
`wheelhouse/` into an absent leaf below `/var/lib/caerus/gate-a/inputs`. The
identity summary is verified in place but is not copied. The staging directory
must contain exactly these six logical entries; missing files, extra files or
wheels, links, special files, hard-linked packet inputs, authorization drift,
and source races fail closed.

The handoff tool authenticates its own bytes against a separately approved
SHA-256 before reading any packet input. That self-check is defense in depth,
not the tool's initial trust anchor. Before the ceremony, an administrator must
exclusive-create the reviewed tool bytes at the content-addressed path
`/var/lib/caerus/gate-a/tools/sha256/<EXACT_HANDOFF_TOOL_SHA256>/gate_a_handoff.py`.
The tool leaf and every ancestor must be root-owned and non-group/other-
writable; the file must be `0444`, its directories `0555`, and the administrator
must verify the file's exact SHA-256 both before and after sealing it. The leaf
and file must have been absent: no overwrite, rename-replace, repair, or reuse
is permitted. Production commands execute only that protected path.

Install the reviewed tool before defining any ceremony command from it. This
Ubuntu command uses `mkdir`'s absent-leaf requirement and a fixed inline
standard-library installer that opens the target with `O_EXCL`; it does not
depend on a platform-specific `dd` output flag or a copying command that can
overwrite. The installer opens the reviewed source without following links,
requires a single-link regular file, verifies stable bytes against the
separately approved digest, writes all bytes through the exclusively created
descriptor, seals the file, and fsyncs the file and parent. The sealed target
digest and metadata are checked afterward. The `sha256/` parent and all of its
named ancestors must already pass the root-owner/non-writable loop; a failure
stops and abandons that content-addressed tool leaf rather than repairing or
reusing it:

```bash
set -euo pipefail

REVIEWED_HANDOFF=/approved/review/gate_a_handoff.py
HANDOFF_SHA=<EXACT_HANDOFF_TOOL_SHA256>
TOOLS_SHA_ROOT=/var/lib/caerus/gate-a/tools/sha256
TOOL_DIR="$TOOLS_SHA_ROOT/$HANDOFF_SHA"
HANDOFF_TOOL="$TOOL_DIR/gate_a_handoff.py"

for ancestor in \
  /var /var/lib /var/lib/caerus /var/lib/caerus/gate-a \
  /var/lib/caerus/gate-a/tools "$TOOLS_SHA_ROOT"
do
  read -r owner mode type <<EOF
$(sudo /usr/bin/stat -c '%u %a %F' "$ancestor")
EOF
  test "$owner" = 0
  test "$type" = directory
  test $((8#$mode & 8#22)) -eq 0
done

sudo /usr/bin/mkdir --mode=0755 -- "$TOOL_DIR"
sudo /usr/bin/python3.10 -I -S -B - \
  "$REVIEWED_HANDOFF" "$HANDOFF_TOOL" "$HANDOFF_SHA" <<'PY'
import hashlib
import os
import stat
import sys

source, target, expected_sha256 = sys.argv[1:]
read_flags = os.O_RDONLY | os.O_NOFOLLOW | os.O_CLOEXEC | os.O_NONBLOCK
source_fd = os.open(source, read_flags)
try:
    before = os.fstat(source_fd)
    if not stat.S_ISREG(before.st_mode) or before.st_nlink != 1:
        raise SystemExit("reviewed handoff source is not a single-link file")
    os.set_blocking(source_fd, True)
    chunks = []
    while True:
        chunk = os.read(source_fd, 1024 * 1024)
        if not chunk:
            break
        chunks.append(chunk)
    after = os.fstat(source_fd)
    if (
        before.st_dev,
        before.st_ino,
        before.st_mode,
        before.st_nlink,
        before.st_uid,
        before.st_gid,
        before.st_size,
        before.st_mtime_ns,
        before.st_ctime_ns,
    ) != (
        after.st_dev,
        after.st_ino,
        after.st_mode,
        after.st_nlink,
        after.st_uid,
        after.st_gid,
        after.st_size,
        after.st_mtime_ns,
        after.st_ctime_ns,
    ):
        raise SystemExit("reviewed handoff source changed while reading")
    payload = b"".join(chunks)
finally:
    os.close(source_fd)
if len(payload) != before.st_size:
    raise SystemExit("reviewed handoff source short read")
if hashlib.sha256(payload).hexdigest() != expected_sha256:
    raise SystemExit("reviewed handoff source hash mismatch")

parent, name = os.path.split(target)
parent_fd = os.open(
    parent, os.O_RDONLY | os.O_DIRECTORY | os.O_NOFOLLOW | os.O_CLOEXEC
)
target_fd = None
try:
    target_fd = os.open(
        name,
        os.O_WRONLY | os.O_CREAT | os.O_EXCL | os.O_NOFOLLOW | os.O_CLOEXEC,
        0o400,
        dir_fd=parent_fd,
    )
    view = memoryview(payload)
    while view:
        written = os.write(target_fd, view)
        if written <= 0:
            raise SystemExit("short write while installing handoff tool")
        view = view[written:]
    os.fchmod(target_fd, 0o444)
    os.fsync(target_fd)
    installed = os.fstat(target_fd)
    if (
        not stat.S_ISREG(installed.st_mode)
        or stat.S_IMODE(installed.st_mode) != 0o444
        or installed.st_nlink != 1
        or installed.st_size != len(payload)
    ):
        raise SystemExit("installed handoff tool metadata mismatch")
    os.fsync(parent_fd)
finally:
    if target_fd is not None:
        os.close(target_fd)
    os.close(parent_fd)
PY
sudo /usr/bin/chmod 0555 -- "$TOOL_DIR"

test "$(sudo /usr/bin/sha256sum "$HANDOFF_TOOL" | /usr/bin/cut -d' ' -f1)" = \
  "$HANDOFF_SHA"
test "$(sudo /usr/bin/stat -c '%u:%g:%a:%h' "$HANDOFF_TOOL")" = 0:0:444:1
test "$(sudo /usr/bin/stat -c '%u:%g:%a' "$TOOL_DIR")" = 0:0:555
```

The approved digest and the administrator-reviewed install operation are the
initial trust anchor. The running tool's `--authorized-handoff-tool-sha256`
self-check then detects post-install drift as defense in depth.

Production transfer also verifies
every existing target ancestor is root-owned and has no group/other write bit.
The administrator must therefore create the `/var/lib/caerus/gate-a` hierarchy
in advance with root ownership; the tool creates only the final protected leaf.
After writing `TRANSFER_RECEIPT.json` last, it seals the leaf and wheelhouse to
`0555` and every copied file plus the receipt to `0444`. Root ownership remains
unchanged, allowing the distinct non-root `caerus-gate-a` principal to traverse
and read the inputs without being able to modify them.

After the protected handoff tool has been installed and immediately before the
first ceremony write (the protected transfer), create the `before` dirty-
checkout snapshot outside the checkout. Do not create the matching `after`
snapshot until bootstrap, the release build, durable `READY`, and independent
release verification have all completed. This preservation window therefore
covers the complete Gate A operation, not only packet transfer. Each receipt
binds the canonical repository root, HEAD, exact raw
NUL-delimited porcelain bytes and hash, every expanded dirty file/symlink/
absence record, fixed `/usr/bin/git` identity, and scanner identity. Expansion
includes ignored files beneath a reported untracked directory. Comparison is
exact and its semantic hash excludes only the capture timestamp.

Handoff tool version 1.3 keeps the receipt writer privileged but never runs Git
as root. It opens the canonical repository directory without following links,
requires that directory to have a non-root owner and group, and launches only
the fixed `/usr/bin/git` children as that exact numeric UID/GID with all
supplementary groups cleared. Each child changes to the already-open repository
descriptor, so it does not resolve an attacker-swappable repository path again.
The checkout must contain an actual `.git/` directory—not a symlink or gitfile—
with the same UID/GID as the repository root. Fixed global
`--git-dir=.git --work-tree=.` arguments prevent parent discovery and override a
local `core.worktree` redirect. Before HEAD or status inspection, Git's exact
`rev-parse --show-toplevel` output must equal the canonical repository path.

The child receives only the fixed six-variable Git environment documented by
the tool; system and global config are disabled. The command-scoped empty
`core.fsmonitor=` value disables fsmonitor without invoking a hook on both the
target Ubuntu Git 2.34.1 and newer Git. Do not substitute
`core.fsmonitor=false`: Git 2.35.1 and earlier interpret `false` as a hook
pathname. `core.untrackedCache=false` separately disables the untracked cache.
Local `.git/config` is still parsed, as Git requires for normal repository
interpretation, but it is parsed with only the checkout owner's authority—not
root authority—and its work-tree/fsmonitor redirections are overridden. The
receipt and semantic hash record the exact Git inspection UID, GID, empty
supplementary group set, and proven top-level path.

Do not add `safe.directory`, including a command-scoped exact path, wildcard,
prefix, persistent config entry, or value derived from `SUDO_UID`. That approach
would bypass Git's dubious-ownership defense while leaving the root process to
parse the user-controlled repository and local config. The owner-identity Git
child removes the ownership mismatch without expanding root's trusted Git
surface. The privileged parent retains the already-open repository descriptor
for its independent descriptor-relative material scans and retains root only
to exclusive-create a `root:root`, `0440` receipt outside the checkout.

The following is the required command shape. Replace every placeholder with a
separately reviewed absolute path or digest; do not derive an authorization
value from the staging files during the ceremony:

```bash
HANDOFF_SHA=<EXACT_HANDOFF_TOOL_SHA256>
HANDOFF_TOOL=/var/lib/caerus/gate-a/tools/sha256/$HANDOFF_SHA/gate_a_handoff.py
DIRTY_REPO=/mnt/disks/alpha-lab/alpha-lab-project
STAGING=/approved/staging/exact-six-inputs
IDENTITY_SUMMARY=/approved/review/identity_summary.json
PROTECTED_INPUT=/var/lib/caerus/gate-a/inputs/<EXACT_RELEASE_INPUT_SHA256>
PRESERVATION=/var/lib/caerus/gate-a/preservation/<APPROVED_CEREMONY_ID>

PRESERVATION_PARENT=/var/lib/caerus/gate-a/preservation
for ancestor in \
  /var /var/lib /var/lib/caerus /var/lib/caerus/gate-a \
  "$PRESERVATION_PARENT"
do
  read -r owner mode type <<EOF
$(sudo /usr/bin/stat -c '%u %a %F' "$ancestor")
EOF
  test "$owner" = 0
  test "$type" = directory
  test $((8#$mode & 8#22)) -eq 0
done
sudo /usr/bin/mkdir --mode=0755 -- "$PRESERVATION"
test "$(sudo /usr/bin/stat -c '%u:%g:%a:%F' "$PRESERVATION")" = \
  "0:0:755:directory"

sudo /usr/bin/python3.10 -I -S -B "$HANDOFF_TOOL" dirty-snapshot \
  --repo-root "$DIRTY_REPO" \
  --output "$PRESERVATION/before.json"

sudo /usr/bin/python3.10 -I -S -B "$HANDOFF_TOOL" protected-transfer \
  --staging "$STAGING" \
  --identity-summary "$IDENTITY_SUMMARY" \
  --protected-leaf "$PROTECTED_INPUT" \
  --authorized-packet-summary-sha256 <EXACT_PACKET_SUMMARY_SHA256> \
  --authorized-source-archive-sha256 <EXACT_SOURCE_ARCHIVE_SHA256> \
  --authorized-bootstrap-sha256 <EXACT_BOOTSTRAP_SHA256> \
  --authorized-release-input-sha256 <EXACT_RELEASE_INPUT_SHA256> \
  --authorized-handoff-tool-sha256 "$HANDOFF_SHA"
```

The transfer is provisionally accepted only when the command succeeds, the
canonical receipt is present at `$PROTECTED_INPUT/TRANSFER_RECEIPT.json`, and
an independent reviewer pins the receipt bytes. Gate A as a whole is not
accepted until the final preservation comparison below returns `status=EQUAL`.
Any failed or interrupted target leaf is abandoned and never repaired or
reused. Subsequent bootstrap/build commands use only:

```text
$PROTECTED_INPUT/gate_a_bootstrap.py
$PROTECTED_INPUT/source.tar
$PROTECTED_INPUT/source_manifest.json
$PROTECTED_INPUT/file_manifest.json
$PROTECTED_INPUT/release_input_manifest.json
$PROTECTED_INPUT/wheelhouse/
```

Download the target wheels into a temporary wheelhouse before entering Gate A.
This is the only network-enabled dependency preparation step:

```bash
WHEELHOUSE="$STAGING/wheelhouse"

python3.10 -m pip download \
  --dest "$WHEELHOUSE" \
  --require-hashes \
  --only-binary=:all: \
  --platform manylinux_2_34_x86_64 \
  --platform manylinux_2_28_x86_64 \
  --platform manylinux_2_17_x86_64 \
  --platform manylinux2014_x86_64 \
  --python-version 3.10 \
  --implementation cp \
  --abi cp310 \
  -r projects/alpha_lab/release/phase1-cp310-linux-x86_64.lock
```

The following placeholders must be replaced by the exact paths and digest from
the reviewed release-input packet. The packet also delivers
`gate_a_bootstrap.py` as a separate byte-identical artifact and records its
owner-approved SHA-256. That minimal standard-library trust root authenticates
its own bytes against both the explicit authorization and source file manifest,
then verifies and extracts the complete archive using create-only,
descriptor-relative no-follow operations. It imports no code from a checkout.

Run its dry verification first, then repeat with `--write` to create and seal
`$RELEASE_PARENT/bootstrap/sha256/$SOURCE_ARCHIVE_SHA256/app`. Existing valid
content is verified idempotently; incomplete or divergent content is never
repaired, deleted, or reused:

```bash
RELEASE_PARENT=/approved/release/parent
SOURCE_ARCHIVE_SHA256=<EXACT_SOURCE_ARCHIVE_SHA256>
BOOTSTRAP_SHA256=<EXACT_OWNER_APPROVED_BOOTSTRAP_SHA256>
BOOTSTRAP_TOOL="$PROTECTED_INPUT/gate_a_bootstrap.py"

python3.10 -I -S -B "$BOOTSTRAP_TOOL" \
  --source-archive "$PROTECTED_INPUT/source.tar" \
  --source-manifest "$PROTECTED_INPUT/source_manifest.json" \
  --file-manifest "$PROTECTED_INPUT/file_manifest.json" \
  --release-parent "$RELEASE_PARENT" \
  --authorized-source-archive-sha256 "$SOURCE_ARCHIVE_SHA256" \
  --authorized-bootstrap-sha256 "$BOOTSTRAP_SHA256"

python3.10 -I -S -B "$BOOTSTRAP_TOOL" \
  --source-archive "$PROTECTED_INPUT/source.tar" \
  --source-manifest "$PROTECTED_INPUT/source_manifest.json" \
  --file-manifest "$PROTECTED_INPUT/file_manifest.json" \
  --release-parent "$RELEASE_PARENT" \
  --authorized-source-archive-sha256 "$SOURCE_ARCHIVE_SHA256" \
  --authorized-bootstrap-sha256 "$BOOTSTRAP_SHA256" \
  --write

BOOTSTRAP_ROOT="$RELEASE_PARENT/bootstrap/sha256/$SOURCE_ARCHIVE_SHA256/app"
GATE_A_BUILDER="$BOOTSTRAP_ROOT/projects/alpha_lab/factory/release_build.py"
```

Gate A refuses write mode unless the loaded builder and colocated dependency
validator are at that exact content-addressed path and their bytes match the
source file manifest. Never import the builder from a mutable legacy checkout,
ambient `PYTHONPATH`, user site, or a stale VM module. Invoke the exact file
with `-I -S -B`, which disables ambient Python paths, site initialization, and
bytecode writes. The operating-system runtime is part of the trust boundary:
an administrator must start the entire Gate A session inside a private mount
namespace or immutable Ubuntu image in which `/`, `/usr`, `/lib`, `/lib64`, and
every other system-runtime mount are read-only. The approved release parent,
temporary parent, and input mounts may be separate, explicitly scoped mounts;
only the release and temporary parents may be writable during construction.
This control must exist before `python3.10`, the bootstrap, or the builder is
started. The Python verifier only confirms it; it cannot establish trust in
stdlib code that the current process has already imported. A normal mutable VM
shell is therefore not a Gate A production environment.

One concrete Ubuntu 22.04 handoff is an administrator-reviewed transient
systemd unit that creates the mount boundary before executing Python. The
administrator must replace the principal and paths, use direct argv (no shell),
and preserve the command exit status. `ReadOnlyPaths=/` is the default; Gate A
construction receives only the two explicit writable exceptions. The code's
per-object `fstatvfs` census remains authoritative and rejects a writable
nested mount even if the unit configuration was intended to be read-only:

```bash
sudo systemd-run --wait --pipe --collect \
  --property=User=<APPROVED_NONROOT_GATE_PRINCIPAL> \
  --property=NoNewPrivileges=yes \
  --property=ReadOnlyPaths=/ \
  --property="ReadWritePaths=$RELEASE_PARENT $GATE_A_TEMP_PARENT" \
  /usr/bin/python3.10 -I -S -B "$GATE_A_BUILDER" build \
  ...exact reviewed arguments... \
  --temporary-parent "$GATE_A_TEMP_PARENT" \
  --write \
  --authorized-release-input-sha256 <EXACT_RELEASE_INPUT_SHA256>
```

Bootstrap materialization uses the same boundary with only
`$RELEASE_PARENT` and its dedicated temporary parent writable. Preflight needs
no writable exception. Full verification creates a sanitized temporary
HOME/TMP/XDG tree, so its unit must provide one dedicated empty writable temp
root, set `TMPDIR` to it before Python starts, and leave every release/system
path read-only. If this launcher or its unit policy is not independently
approved and recorded, Gate A stops; the Python tool does not silently
substitute a weaker control.

First run the non-mutating preflight and default dry build inside that
administrator-established environment:

```bash
python3.10 -I -S -B "$GATE_A_BUILDER" preflight \
  --repo-root "$BOOTSTRAP_ROOT" \
  --source-archive "$PROTECTED_INPUT/source.tar" \
  --source-manifest "$PROTECTED_INPUT/source_manifest.json" \
  --file-manifest "$PROTECTED_INPUT/file_manifest.json" \
  --wheelhouse "$PROTECTED_INPUT/wheelhouse" \
  --release-input-manifest "$PROTECTED_INPUT/release_input_manifest.json" \
  --release-parent "$RELEASE_PARENT"

python3.10 -I -S -B "$GATE_A_BUILDER" build \
  --repo-root "$BOOTSTRAP_ROOT" \
  --source-archive "$PROTECTED_INPUT/source.tar" \
  --source-manifest "$PROTECTED_INPUT/source_manifest.json" \
  --file-manifest "$PROTECTED_INPUT/file_manifest.json" \
  --wheelhouse "$PROTECTED_INPUT/wheelhouse" \
  --release-input-manifest "$PROTECTED_INPUT/release_input_manifest.json" \
  --release-parent "$RELEASE_PARENT"
```

Only after the preflight output has been independently reviewed may the
operator add both write controls:

```bash
python3.10 -I -S -B "$GATE_A_BUILDER" build \
  --repo-root "$BOOTSTRAP_ROOT" \
  --source-archive "$PROTECTED_INPUT/source.tar" \
  --source-manifest "$PROTECTED_INPUT/source_manifest.json" \
  --file-manifest "$PROTECTED_INPUT/file_manifest.json" \
  --wheelhouse "$PROTECTED_INPUT/wheelhouse" \
  --release-input-manifest "$PROTECTED_INPUT/release_input_manifest.json" \
  --release-parent "$RELEASE_PARENT" \
  --interpreter /usr/bin/python3.10 \
  --write \
  --authorized-release-input-sha256 <EXACT_RELEASE_INPUT_SHA256>
```

The build is create-only and content-addressed. It copies the verified source
and wheels, extracts the source file-only with descriptor-relative no-follow
operations, creates a copied venv, installs with `--no-index` and
`--require-hashes`, and executes dependency validation, `pip check`, the exact
357-test inventory, and the 355-pass/2-DuckDB-skip suite inside a proven Linux
user/network namespace. It binds all 25 locked distributions, the explicit
`pip`/`setuptools` venv bootstrap allowance, the external base interpreter and
complete stdlib-tree identities, and the sealed app/venv/wheel/lock tree.
Caches, JUnit output, and temporary state remain outside the release.
CPython's redundant Linux `venv/lib64 -> lib` compatibility alias is removed
descriptor-relatively immediately after venv creation; an absent link is
accepted, while any different `lib64` object or target fails. The sealed release
therefore contains no symlink exception for this target-platform artifact.

The external-runtime receipt also binds Ubuntu's OS-release bytes, every shared
object loaded by the interpreter, the exact single-link `/usr/bin/git` and
`/usr/bin/unshare` binaries, every substitutable path ancestor, and the full
canonical record list for each stdlib and platstdlib root. Every external
regular record includes bytes, SHA-256, mode, owner UID/GID, link count,
per-object filesystem-read-only evidence, and the ACL-aware effective-write
result; directory records include the corresponding owner, mode, link, mount,
and write-denial facts. Symlinks, hard links, sockets, FIFOs, and device nodes
are forbidden in the external executable surface. The versioned
`caerus_alpha_lab_atlas_gate_e_runtime_receipt_v2` inside
`verification_receipt.json` is the direct Atlas provenance surface. It binds
the release-input, build, and built-manifest identities; copied Python bytes;
the canonical complete site-packages subtree; lock, wheel-manifest, and
installed-distribution closure; and the release/app/venv owner and mode census.
`READY.verification_receipt_sha256` binds that whole receipt, while independent
verification reports the exact `READY` SHA-256. No caller-derived runtime hash
is an alternative authority.

`verification_receipt.json` and canonical `READY` are written last. A crash
before durable `READY` leaves an unreferenced directory that is never repaired,
deleted, or reused automatically. An existing valid address is idempotently
verified; any incomplete or divergent collision fails closed.

Verify the finished release independently before any later ceremony invocation:

```bash
python3.10 -I -S -B "$GATE_A_BUILDER" verify \
  --release-dir /approved/release/parent/releases/sha256/<EXACT_RELEASE_INPUT_SHA256>
```

Only after that verification has accepted durable `READY`, create the second
dirty snapshot and compare it with the pre-transfer snapshot. These commands
are part of Gate A and must complete before the build is accepted:

```bash
sudo /usr/bin/python3.10 -I -S -B "$HANDOFF_TOOL" dirty-snapshot \
  --repo-root "$DIRTY_REPO" \
  --output "$PRESERVATION/after-build.json"

sudo /usr/bin/python3.10 -I -S -B "$HANDOFF_TOOL" compare \
  --before "$PRESERVATION/before.json" \
  --after "$PRESERVATION/after-build.json"
```

After Gate A and all later owner gates have independently passed, invoke a
ceremony only after re-verifying the release:

```bash
python3.10 -I -S -B "$GATE_A_BUILDER" verify \
  --release-dir /approved/release/parent/releases/sha256/<EXACT_RELEASE_INPUT_SHA256>

python3.10 -I -S -B "$GATE_A_BUILDER" ceremony \
  --release-dir /approved/release/parent/releases/sha256/<EXACT_RELEASE_INPUT_SHA256> \
  --ceremony-output-root /protected-review/approved-output-workspace \
  -- registry verify-history \
  --history /protected-review/identity_registry_history.json \
  --trust-anchor /protected-pin/root_trust_anchor.json \
  --external-pin <active-registry-hash>
```

The Gate E ceremony command itself must also be launched by the administrator,
not from an already-running Python or mutable shell namespace. Its unit uses
`ReadOnlyPaths=/` and grants write access only to the approved output root and
a dedicated empty temporary root supplied as `TMPDIR`; the release, bootstrap,
system runtime, source inputs, and all ancestors remain read-only:

```bash
sudo systemd-run --wait --pipe --collect \
  --property=User=<APPROVED_NONROOT_GATE_E_PRINCIPAL> \
  --property=NoNewPrivileges=yes \
  --property=ReadOnlyPaths=/ \
  --property="ReadWritePaths=$CEREMONY_OUTPUT_ROOT $GATE_E_TEMP_ROOT" \
  --setenv="TMPDIR=$GATE_E_TEMP_ROOT" \
  /usr/bin/python3.10 -I -S -B "$GATE_A_BUILDER" ceremony \
  --release-dir "$RELEASE_DIR" \
  --ceremony-output-root "$CEREMONY_OUTPUT_ROOT" \
  -- ...exact allowlisted ceremony arguments...
```

Mode `0555`/`0444` and a different Unix principal are tamper-evident controls,
not an adversarial seal: the owner, an ACL, or a path-ancestor owner can still
race verification. Before any Gate E Python starts, an administrator must
place the complete external system image plus the release and bootstrap trees
on read-only mounts for the entire ceremony window. A different principal by
itself is never accepted. The external administrator/image owner is explicitly
inside the trusted computing base and outside the attacker model. The launcher
fails closed for root, any writable protected object or ancestor, a non-read-
only nested mount, an ACL-aware effective write, any external symlink or
special entry, or a multi-link external regular file. It records the current
effective UID/GID and canonical per-object mount/write-denial census, then runs
a complete release and external-runtime verification before execution.

The same immutable system image must contain every library that can be loaded
later; the ceremony child creates a final `/proc/self/maps` receipt in its
`finally` boundary. The parent creates an anonymous open file description and
passes only its descriptor through `unshare`; the child never opens a receipt
path, and the parent verifies the same pinned descriptor after exit. A
same-principal process therefore cannot substitute a named receipt between
child exit and verification. Every external mapped path must already be an exact file in
the sealed shared-object receipt or complete stdlib manifest. A late DSO absent
from that predeclared closure, a missing child receipt (including a killed
process), or a substituted map path fails Gate E. The final map inventory and
hash are bound into the success receipt. After the command exits (including a
nonzero or signal return), the launcher also rescans the complete release and
external TCB and requires byte-identical pre/post receipts. Command outputs are
uncertified until return code zero and those checks all pass. Only then does
the launcher create a canonical receipt under
`<ceremony-output-root>/.gate_e_success/`. A postscan failure leaves any output
untrusted and creates no success receipt. The success receipt binds the exact
approved output-root path, the complete canonical before/after output manifests
(all file bytes/SHA-256, mode, UID/GID, and link count), and the
create-only path/record delta. Symlinks, hard-linked regular files, and special
entries fail the output scan; modifying or removing preexisting workspace state
also fails. Consumers must rehash the referenced output manifest and pin the
success-receipt bytes before treating an output as certified. Until this exact
Ubuntu/read-only image handoff is proven, Gate A/Gate E remain external
blockers.

The launcher executes only `projects.alpha_lab.factory.ceremony`, uses the
exact app and interpreter paths from `READY`, forbids relative paths and
publication `--write`, applies OS-level network isolation, and re-verifies the
sealed release and external runtime after the command. Outputs must be
create-only and beneath the
separately approved, preexisting no-symlink ceremony-output root, which must be
outside the release, authoritative checkout/data roots, and protected inputs.

The only permitted dependency-driven skips are the two named DuckDB tests.
DuckDB is not needed by the signing ceremony and remains an explicitly excluded
optional materialization engine. The yfinance adapter is injected by tests and
causes no skip. Any other skip, sdist, unpinned or unhashed requirement,
wheel-set difference, import-contract change, incompatible platform/ABI,
dependency-closure difference, external-runtime drift, cache, extra file,
mutable mode, source-link member, or metadata/hash-chain drift fails Gate A.
Do not continue to signing on a failure.

## Protected inputs

Two inputs must arrive separately from the reviewed history file:

- the root public trust-anchor JSON; and
- the active registry-directory SHA-256 external pin.

The canonical exported history schema is
`caerus_alpha_lab_identity_registry_history_v1` with exactly `trust_anchor`,
`registries`, `active_registry_hash`, and
`externally_pinned_registry_hash`. A history file cannot authenticate its own
embedded root or pin.

## 1. Registry release

Prepare a genesis release from a public-only unsigned directory:

```bash
python -m projects.alpha_lab.factory.ceremony registry prepare \
  --directory /protected-review/registry_directory.json \
  --trust-anchor /protected-pin/root_trust_anchor.json \
  --released-at 2026-08-22T20:00:00Z \
  --output-dir /protected-review/registry-release-1
```

Sign the exact bytes in
`registry-release-1/signing_payload.json` with the dedicated offline owner/root
credential. Do not reuse a GitHub or SSH key, and do not give the private key to
this command.

Retrieve an approved machine-role Cloud KMS public key without exporting a
credential:

```bash
gcloud kms keys versions get-public-key 1 \
  --location="<APPROVED_LOCATION>" \
  --keyring=caerus-research --key=research-author \
  --public-key-format=pem --output-file=/protected-review/research-author.pem
```

Finalize using the root anchor and registry hash from the protected pin, not
copies taken from the preparation directory:

```bash
python -m projects.alpha_lab.factory.ceremony registry finalize \
  --request /protected-review/registry-release-1/review_manifest.json \
  --signature /protected-review/registry-release-1/signature.raw \
  --trust-anchor /protected-pin/root_trust_anchor.json \
  --external-pin <exact-registry-directory-sha256> \
  --output-dir /protected-review/registry-release-1-final
```

For rotation, `registry prepare` and `registry finalize` both additionally
receive `--previous-history` and `--previous-external-pin`. The new public pin
is still supplied through `--external-pin` at finalization.

## 2. Event and generic attestations

An event draft contains its exact ID, type, payload, occurred/recorded times,
and prior ledger head. Preparation derives the typed payload hash and append
context; callers do not reproduce those hashes manually:

```bash
python -m projects.alpha_lab.factory.ceremony attestation prepare \
  --identity-history /protected-review/identity_registry_history.json \
  --identity-trust-anchor /protected-pin/root_trust_anchor.json \
  --external-pin <active-registry-hash> \
  --identity-id research.author --key-id research.author.2026 \
  --role PREREGISTRATION_AUTHOR \
  --attested-at 2026-08-22T20:05:00Z \
  --event-draft /protected-review/event_draft.json \
  --output-dir /protected-review/event-attestation
```

After external signing, use `attestation finalize`; the result is the exact
`caerus_alpha_lab_control_plane_event_attestation_v1` wrapper consumed by the
authenticated ledger. `attestation verify` rechecks it from the request,
protected root, external pin, and complete history. The `--generic` preparation
form covers an already-derived immutable artifact hash, context hash, ledger
head, and recording time for an eligible role.

For an approved Cloud KMS Ed25519 machine-role key, omit
`--digest-algorithm`; Ed25519 signs the exact message bytes directly:

```bash
gcloud kms asymmetric-sign \
  --location="<APPROVED_LOCATION>" --keyring=caerus-research \
  --key=research-author --version=1 \
  --input-file=/protected-review/event-attestation/signing_payload.json \
  --signature-file=/protected-review/event-attestation/signature.raw
```

Cloud KMS writes the detached Ed25519 signature as raw bytes. Ceremony finalize
commands accept that exact 64-byte `.raw` file directly; base64 conversion is
neither required nor preferred.

## 3. Migration plan and QS-003

`migration definition` converts the exact unsigned owner packet and a fresh
canonical audit into the complete definition. `migration prepare` repeats that
audit, validates the full pinned public history, deterministically builds every
legacy event and expected ledger byte, and emits the owner signing bytes:

```bash
python -m projects.alpha_lab.factory.ceremony migration prepare \
  --repo-root /mnt/disks/alpha-lab/alpha-lab-project \
  --data-root /mnt/disks/alpha-lab/alpha-lab-project/outputs/research/alpha_lab \
  --owner-packet projects/alpha_lab/templates/MIGRATION_OWNER_SIGNING_PACKET.json \
  --recorded-at 2026-08-22T20:10:00Z \
  --identity-history /protected-review/identity_registry_history.json \
  --identity-trust-anchor /protected-pin/root_trust_anchor.json \
  --external-pin <active-registry-hash> \
  --owner-identity-id owner.ratifier --owner-key-id owner.ratifier.2026 \
  --output-dir /protected-review/migration-plan
```

`migration finalize` ingests only the detached owner signature and immediately
verifies the signed plan. `migration verify` can repeat the complete GCP source
audit before any later step. `migration identity-bundle` composes the exact
`caerus_alpha_lab_control_plane_identity_bundle_v1` consumed by authenticated
Alpha control-plane commands from the verified history and signed plan; no
manual JSON assembly is required. The protected root and external pin remain
separate command inputs even though their public copies appear in the bundle.

The migration signature ratifies the exact legacy plan but does **not**
authorize publication. Prepare a distinct QS-003 artifact after the signed plan
exists:

```bash
python -m projects.alpha_lab.factory.ceremony publication prepare \
  --repo-root /mnt/disks/alpha-lab/alpha-lab-project \
  --data-root /mnt/disks/alpha-lab/alpha-lab-project/outputs/research/alpha_lab \
  --signed-plan /protected-review/signed_migration_plan.json \
  --authorized-at 2026-08-22T20:15:00Z \
  --identity-history /protected-review/identity_registry_history.json \
  --identity-trust-anchor /protected-pin/root_trust_anchor.json \
  --external-pin <active-registry-hash> \
  --owner-identity-id owner.ratifier --owner-key-id owner.ratifier.2026 \
  --output-dir /protected-review/publication-authorization
```

QS-003 binds the signed-plan hash, exact canonical ledger path, expected bytes,
event count and head, fresh receipt-set hash, create-only mode, authorization
time, active registry and pin, and prior `GENESIS`. `publication publish` is a
verification-only dry run by default. `--write` is accepted only on canonical
GCP and only with both valid owner-signed artifacts; the publisher uses a
create-only atomic hard link and never repairs or overwrites an existing ledger.

## 4. Projection export

`projection prepare` opens the authenticated canonical ledger, verifies the
signed activation plan and complete legacy prefix, snapshots the ledger under
lock, and emits exact `LEDGER_EXPORTER` signing bytes. `projection finalize`
reopens and replays the ledger before accepting the detached signature.
`projection verify` replays it again against the final envelope.

```bash
python -m projects.alpha_lab.factory.ceremony projection prepare \
  --ledger /mnt/disks/alpha-lab/alpha-lab-project/outputs/research/alpha_lab/ledger/research_events.v1.jsonl \
  --research-root /mnt/disks/alpha-lab/alpha-lab-project/outputs/research/alpha_lab \
  --repo-root /mnt/disks/alpha-lab/alpha-lab-project \
  --signed-migration-plan /protected-review/signed_migration_plan.json \
  --identity-history /protected-review/identity_registry_history.json \
  --identity-trust-anchor /protected-pin/root_trust_anchor.json \
  --external-pin <active-registry-hash> \
  --exported-at 2026-08-22T20:20:00Z \
  --exporter-identity-id ledger.exporter --exporter-key-id ledger.exporter.2026 \
  --output-dir /protected-review/projection-export
```

No command in this runbook authorizes data purchase, holdout access, Shadow,
Paper, capital, deployment, broker behavior, scheduling, or trading.
