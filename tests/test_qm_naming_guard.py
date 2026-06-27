"""Regression guard (#351 Phase 2): no retired QM-name literal in production code.

Scans the production code paths only — ansible/, src/mqlab/, lab/scripts/, clients/,
and lab/topology.yaml. It deliberately does NOT scan tests/ (seeded test topologies
legitimately use the old names to exercise the no-`short` fallback) or docs/ (which
cite the history). The single source of QM names is the per-arm `short` token; nothing
in production code should hardcode QMPCMK/QMSVC/QMNATIVE/QMRDQM anymore.
"""

from __future__ import annotations

import re
from pathlib import Path

REPO = Path(__file__).resolve().parents[1]
SCAN_DIRS = ["ansible", "src/mqlab", "lab/scripts", "clients"]
SCAN_FILES = ["lab/topology.yaml"]
SCAN_SUFFIXES = {".yml", ".yaml", ".j2", ".py", ".sh", ".mqsc", ".json"}
RETIRED = re.compile(r"\bQM(PCMK|SVC|NATIVE|RDQM)\b")
ALLOW = {
    Path(__file__).resolve(),  # this file names them by definition
    # The source-of-truth's no-`short` fallback default (for seeded test topologies that
    # don't declare a short); the only legitimate retired-name literal in production.
    REPO / "src" / "mqlab" / "setups.py",
}


def _retired_literals() -> list[str]:
    paths: list[Path] = []
    for d in SCAN_DIRS:
        paths += [p for p in (REPO / d).rglob("*") if p.is_file() and p.suffix in SCAN_SUFFIXES]
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
