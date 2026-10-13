"""Regression guard (#351 Phase 2): no retired QM-name literal in production code.

Scans the production code paths only — ansible/, src/mqlab/, lab/scripts/, each
component's components/<name>/src/, and lab/topology.yaml. It deliberately does NOT
scan tests/ (seeded test topologies legitimately use the old names to exercise the
no-`short` fallback) or docs/ (which cite the history). The single source of QM names
is the per-arm `short` token; nothing in production code should hardcode
QMPCMK/QMSVC/QMNATIVE/QMRDQM anymore.
"""

from __future__ import annotations

import re
from pathlib import Path

REPO = Path(__file__).resolve().parents[1]
SCAN_DIRS = ["ansible", "src/mqlab", "lab/scripts"]
# Component sources only: not their tests (as above) nor a local .venv.
SCAN_GLOBS = ["components/*/src"]
SCAN_FILES = ["lab/topology.yaml"]
SCAN_SUFFIXES = {".yml", ".yaml", ".j2", ".py", ".sh", ".mqsc", ".json"}
# Case-insensitive: RDQM names its Pacemaker/DRBD resources after the QM in lowercase, so a
# retired name also hides as e.g. `qmrdqm` or `p_drbd_qmrdqm` (#1472 — the case-sensitive
# guard missed exactly that). `_` is a word character, so match on letters, not \b.
RETIRED = re.compile(r"(?<![A-Za-z0-9])QM(PCMK|SVC|NATIVE|RDQM)(?![A-Za-z0-9])", re.IGNORECASE)
ALLOW = {
    Path(__file__).resolve(),  # this file names them by definition
}


def _retired_literals() -> list[str]:
    paths: list[Path] = []
    dirs = [REPO / d for d in SCAN_DIRS] + [d for g in SCAN_GLOBS for d in REPO.glob(g)]
    for d in dirs:
        paths += [p for p in d.rglob("*") if p.is_file() and p.suffix in SCAN_SUFFIXES]
    paths += [REPO / f for f in SCAN_FILES]
    offenders: list[str] = []
    for p in paths:
        if p in ALLOW:
            continue
        for i, line in enumerate(p.read_text(errors="ignore").splitlines(), 1):
            if RETIRED.search(line):
                offenders.append(f"{p.relative_to(REPO)}:{i}: {line.strip()}")
    return offenders


def test_no_retired_qm_name_literals_in_production_code():
    offenders = _retired_literals()
    assert not offenders, "retired QM-name literals remain (#351 Phase 2):\n" + "\n".join(offenders)


def test_guard_catches_lowercase_resource_forms():
    # the #1472 regression: RDQM resource names are the QM lowercased, inside `_`-joined ids
    for hidden in ("qmrdqm", "p_drbd_qmrdqm", "qmrdqm.dr", "QMRDQM"):
        assert RETIRED.search(hidden), hidden
    for fine in ("RDQMAPP", "rdqmapp", "SVCQM", "XQMRDQMX"):
        assert not RETIRED.search(fine), fine
