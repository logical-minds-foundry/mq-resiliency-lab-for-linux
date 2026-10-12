"""Lab shell scripts never pipe into an early-exiting reader (#1451, the #921/#1274 class).

Every script under lab/boxes/ and lab/scripts/ runs under `set -euo pipefail` (the sourced
helpers inherit it from their caller). A reader that can exit before its input is drained
— `grep -q`, `grep -m`, `head`, an `awk` that calls `exit` — lets the producer take
SIGPIPE; under pipefail the pipeline then returns 141, which set -e turns into an abort or
a `cmd | grep -q ... || ...` misreads as "no match". The safe idioms are to capture the
producer first and match a here-string (`grep -q PAT <<<"$out"`), or to end the pipeline in
a stage that drains (`grep PAT >/dev/null`, `awk 'NR == 1'`).

This is a structural guard: it scans the script text (comments excluded, so comments that
document the rule never trip it) for a pipe into one of those readers.
"""

from __future__ import annotations

import re
from pathlib import Path

import pytest

_REPO = Path(__file__).resolve().parents[1]
_SCRIPT_DIRS = (_REPO / "lab" / "boxes", _REPO / "lab" / "scripts")

# A single `|` (never the `||` operator) followed by an early-exiting reader.
_PIPE = r"(?<!\|)\|(?!\|)\s*"
_EARLY_EXIT_READER = re.compile(
    _PIPE
    + r"(?:"
    + r"head\b"
    + r"|grep\b[^|]*\s(?:-[A-Za-z]*[qm]|--quiet\b|--silent\b|--max-count\b)"
    + r"|awk\b[^|]*\bexit\b"
    + r")"
)
# A shell comment starts at a word boundary: line start or after whitespace. `${x#y}` and
# `$#` are not comments (no whitespace before the `#`).
_COMMENT = re.compile(r"(?:^|\s)#.*$")


def sigpipe_hazards(text: str) -> list[tuple[int, str]]:
    """(line number, line) for every non-comment pipe into an early-exiting reader."""
    hits = []
    for lineno, line in enumerate(text.splitlines(), start=1):
        code = _COMMENT.sub("", line)
        if _EARLY_EXIT_READER.search(code):
            hits.append((lineno, line.strip()))
    return hits


def _scripts() -> list[Path]:
    return sorted(p for d in _SCRIPT_DIRS for p in d.rglob("*.sh"))


def test_scan_covers_the_box_builders():
    names = {p.relative_to(_REPO).as_posix() for p in _scripts()}
    assert {
        "lab/boxes/build-fatbox.sh",
        "lab/boxes/_box-register.sh",
        "lab/boxes/_manifest-hash.sh",
        "lab/boxes/rhel/build-box.sh",
    } <= names


@pytest.mark.parametrize("script", _scripts(), ids=lambda p: p.relative_to(_REPO).as_posix())
def test_no_pipe_into_an_early_exiting_reader(script: Path):
    assert sigpipe_hazards(script.read_text()) == []


@pytest.mark.parametrize(
    "line",
    [
        'vagrant box list | grep -q "^${BASE_BOX} " \\',
        "vagrant box list | grep -qF x || vagrant box add x",
        "  | awk '{print $5}' | cut -d/ -f1 | head -n1)\"",
        "x=$(producer | head -1)",
        "producer | grep -m1 pat",
        "producer | grep -Eq pat",
        "producer | grep --quiet pat",
        "producer | awk '/pat/ {print; exit}'",
        "\"status $other | grep -q 'ROLE(Active)'\"",
    ],
)
def test_detects_each_hazard(line: str):
    assert sigpipe_hazards(line) == [(1, line.strip())]


@pytest.mark.parametrize(
    "line",
    [
        "# never `vagrant box list | grep -q x` — capture first (#1451)",
        "  # e.g. printf | head -n1 SIGPIPEs under pipefail",
        "foo  # trailing note: avoid `| grep -q` here",
        'grep -q "^${BASE_BOX} " <<<"$BOX_LIST" || exit 1',
        "producer | awk -v m=x 'tolower($0) ~ m {print $5}' | cut -d/ -f1 | awk 'NR == 1'",
        "producer | grep 'STATUS(RUNNING)' >/dev/null || start",
        "producer | grep -oE 'nha-rhel-crr-a[123]'",
        "producer | grep -c pat",
        "test -f x || head_count=1",
        'role="${worklist%%$\'\\n\'*}"; n="$#"',
    ],
)
def test_ignores_comments_and_safe_forms(line: str):
    assert sigpipe_hazards(line) == []
