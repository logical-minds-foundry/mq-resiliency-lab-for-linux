"""No script, role, playbook or Vagrantfile derives a build/ path from its own location (#1569).

`$(dirname "$0")/../../build/state`, `{{ playbook_dir }}/../build/state` and
`File.join(__dir__, "..", "build", ...)` all name the *running checkout's* build/. In a
git worktree whose shared-bucket symlinks are not wired yet, that is a fresh local tree,
so a write there silently forks the live-lab state (secrets regenerated, a PKI or fence
key the running cluster does not know). Consumers must resolve a bucket through the one
authority instead: MQLAB_<BUCKET>_DIR / `mqlab build path <bucket>` — in shell via
lab/scripts/_build-bucket.sh, in Ansible via group_vars/all/buckets.yml (mqlab_<b>_dir),
in the Vagrantfile via MQLAB_WORK_DIR.

This is a structural guard over the non-Python consumers (Python goes through
mqlab.paths / mqlab.buildenv). It scans code (comments excluded) for a build path joined
onto anything: `<x>/build/<bucket>`, `<x>/build` or a `"build", "<bucket>"` join. Prose
such as "a filename under build/state/" has no join and does not trip it. Each allowed
exception names why it is not the bug.
"""

from __future__ import annotations

import re
from pathlib import Path

import pytest

_REPO = Path(__file__).resolve().parents[1]
_ROOTS = ("lab", "ansible", "scripts", "tools")
_SUFFIXES = {".sh", ".yml", ".yaml", ".j2", ".cfg", ".rb", ".py", ".service"}

_BUCKET = r"(?:cache|state|work|temp)"
_JOINED = re.compile(
    r"/build/" + _BUCKET + r"\b"  # <x>/build/<bucket>
    r"|/build[\"']?\s*$"  # <x>/build at end of an assignment
    r"|/build[\"'](?=[\s;)])"  # "<x>/build" as a word
    r"|[\"']build[\"']\s*,\s*[\"']" + _BUCKET + r"[\"']"  # File.join(..., "build", "state")
)
# A comment starts at a word boundary (line start or after whitespace), as in
# tests/test_shell_sigpipe_safe.py; `${x#y}` and `$#` are not comments.
_COMMENT = re.compile(r"(?:^|\s)#.*$")

# file (repo-relative) -> why its build/ joins are not location-derived.
ALLOWED = {
    # Static INI read by ansible itself; it cannot run the authority. Both entries are
    # read-only lookups through the checkout's own build/: work/ is local by design (the
    # rendered inventory), and cache/ is the ensure-wired symlink to main.
    "ansible/ansible.cfg": "static ansible config; read-only, work/ local, cache/ wired by ensure",
    # These resolve the MAIN checkout via `git rev-parse --git-common-dir` — the same
    # algorithm as buildenv.main_build_root — not the script's location, so they land in
    # the shared bucket from any worktree.
    "lab/scripts/stage-rhel-iso.sh": "main checkout via git-common-dir",
    "lab/scripts/stage-rhel-dvd-from-archive.sh": "main checkout via git-common-dir",
    "lab/boxes/rhel/build-box.sh": "main checkout via git-common-dir",
    "lab/boxes/build-fatbox.sh": "main checkout via git-common-dir",
    # Runs on the operator's host (outside the VM, no mqlab venv): it resolves the host
    # checkout's main build/state via git-common-dir and pushes into the VM checkout's.
    "scripts/push-rhel-iso.sh": "host-side; main checkout via git-common-dir",
}


def joined_build_paths(text: str) -> list[tuple[int, str]]:
    """(line number, line) for every non-comment build/ path joined onto something."""
    hits = []
    for lineno, line in enumerate(text.splitlines(), start=1):
        code = _COMMENT.sub("", line)
        if _JOINED.search(code):
            hits.append((lineno, line.strip()))
    return hits


def _consumers() -> list[Path]:
    files = []
    for root in _ROOTS:
        for path in sorted((_REPO / root).rglob("*")):
            if path.is_file() and (path.suffix in _SUFFIXES or path.name == "Vagrantfile"):
                files.append(path)
    return files


@pytest.mark.parametrize(
    "line",
    [
        'DIR="$(cd "$(dirname "$0")/../.." && pwd)/build/state/secrets"',
        'SNAP_ROOT="${SNAP_ROOT:-$MAIN_ROOT/build/state/snapshots}"',
        'DEST="$ROOT/build/cache/mq"',
        'BUILD_DIR="$MAIN_ROOT/build"',
        '  src: "{{ playbook_dir }}/../build/state/fence_key"',
        'pki_root: "{{ repo_root }}/build/state/secrets/pki"',
        "x: \"{{ lookup('file', repo_root + '/build/work/pki/entities.json') }}\"",
        "x: \"{{ lookup('file', playbook_dir ~ '/../build/work/obs/a.json') }}\"",
        'resolved = File.join(__dir__, "..", "build", "work", "lab", "t.yaml")',
        "inventory = ../build/work/inventory.ini",
    ],
)
def test_guard_flags_location_derived_paths(line: str):
    assert joined_build_paths(line) == [(1, line.strip())]


@pytest.mark.parametrize(
    "line",
    [
        '. "$(dirname "$0")/_build-bucket.sh"',
        'STATE="$(mqlab_bucket state)"',
        'DIR="$STATE/secrets"',
        '  src: "{{ mqlab_state_dir }}/fence_key"',
        'echo "ERROR: --iso is a filename under build/state/, not a path" >&2',
        "# Goldens live under $MAIN_ROOT/build/state/snapshots (a comment)",
        '  cmd: "{{ playbook_dir }}/../lab/scripts/lab-secret.sh name"',
        'if [ -s "$stage/build-requirements.txt" ]; then',
    ],
)
def test_guard_ignores_authority_reads_prose_and_comments(line: str):
    assert joined_build_paths(line) == []


def test_no_consumer_derives_a_build_path_from_its_location():
    offenders = []
    for path in _consumers():
        rel = path.relative_to(_REPO).as_posix()
        if rel in ALLOWED:
            continue
        offenders.extend(f"{rel}:{n}: {line}" for n, line in joined_build_paths(path.read_text()))
    assert not offenders, (
        "build/ paths derived from a script/playbook location (resolve through "
        "MQLAB_<BUCKET>_DIR / `mqlab build path` instead, #1569):\n  " + "\n  ".join(offenders)
    )


@pytest.mark.parametrize("rel", sorted(ALLOWED))
def test_every_allowed_exception_is_still_needed(rel: str):
    # A stale allowance would silently exempt a file that later regresses.
    assert joined_build_paths((_REPO / rel).read_text()), f"{rel} no longer needs ALLOWED"


def test_ansible_buckets_resolve_through_the_authority():
    text = (_REPO / "ansible" / "group_vars" / "all" / "buckets.yml").read_text()
    for bucket in ("cache", "state", "work", "temp"):
        env = f"lookup('ansible.builtin.env', 'MQLAB_{bucket.upper()}_DIR')"
        pipe = f"lookup('ansible.builtin.pipe', 'mqlab build path {bucket}')"
        assert f'mqlab_{bucket}_dir: "{{{{ {env} or {pipe} }}}}"' in text


def test_vagrantfile_reads_the_work_bucket_from_the_authority():
    text = (_REPO / "lab" / "Vagrantfile").read_text()
    assert 'ENV.fetch("MQLAB_WORK_DIR", "")' in text
    assert 'IO.popen(["mqlab", "build", "path", "work"]' in text
    assert 'File.join(work_dir, "lab", "topology.resolved.yaml")' in text
