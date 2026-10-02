"""mqlab is invoked only via `uv run` or an activated environment (#1252).

Never by an absolute venv path (`<checkout>/.venv/bin/mqlab`), never through a
second venv (`.venv-host`), and never handed to a host service as `mqlab_bin`. The
last such path was the root `lab-net-state` service (retired, #1253): it ran a
checkout's venv mqlab as root, leaving root-owned `__pycache__` in that checkout and
failing every tick once the checkout was deleted (#1251).

This guard scans the code, playbooks, scripts and user-facing docs. Historical
design records (docs/plans, docs/specs, docs/reports) and tests are exempt: they
quote the old invocation to explain it.
"""

from __future__ import annotations

import re
from pathlib import Path

REPO_ROOT = Path(__file__).resolve().parents[1]
SCANNED = ["src", "ansible", "lab", "tools", "scripts", "docs", "README.md", "CLAUDE.md"]
EXEMPT_DIRS = {
    REPO_ROOT / "docs" / "plans",
    REPO_ROOT / "docs" / "specs",
    REPO_ROOT / "docs" / "reports",
}
TEXT_SUFFIXES = {"", ".py", ".sh", ".yml", ".yaml", ".j2", ".md", ".toml", ".cfg", ".tpl"}
FORBIDDEN = {
    "absolute venv mqlab": re.compile(r"\.venv/bin/(mqlab|python3?\s+-m\s+mqlab)\b"),
    "second host venv": re.compile(r"\.venv-host\b"),
    "mqlab_bin hand-off": re.compile(r"\bmqlab_bin\b"),
    "interpreter-sibling mqlab": re.compile(r"sys\.executable\)?\.parent\s*/\s*['\"]mqlab"),
}


def _scanned_files() -> list[Path]:
    files: list[Path] = []
    for entry in SCANNED:
        path = REPO_ROOT / entry
        candidates = [path] if path.is_file() else sorted(path.rglob("*"))
        for f in candidates:
            if not f.is_file() or f.suffix not in TEXT_SUFFIXES:
                continue
            if any(parent in EXEMPT_DIRS for parent in f.parents):
                continue
            files.append(f)
    return files


def test_guard_scans_the_real_tree() -> None:
    files = _scanned_files()
    assert REPO_ROOT / "src" / "mqlab" / "phases.py" in files
    assert REPO_ROOT / "ansible" / "host-obs.yml" in files
    assert REPO_ROOT / "lab" / "scripts" / "net-state-publish.sh" in files
    assert not any(REPO_ROOT / "docs" / "specs" in f.parents for f in files)


def test_patterns_catch_the_retired_invocations() -> None:
    assert FORBIDDEN["absolute venv mqlab"].search("/repo/.venv/bin/mqlab obs net-state")
    assert FORBIDDEN["absolute venv mqlab"].search(".venv/bin/python -m mqlab status")
    assert FORBIDDEN["second host venv"].search("prepend .venv-host/bin to PATH")
    assert FORBIDDEN["mqlab_bin hand-off"].search("-e mqlab_bin=/x")
    assert FORBIDDEN["interpreter-sibling mqlab"].search('Path(sys.executable).parent / "mqlab"')
    # The sanctioned forms stay legal.
    for ok in ("uv run mqlab bootstrap x", ". .venv/bin/activate", "uv run --project . mqlab"):
        assert not any(p.search(ok) for p in FORBIDDEN.values()), ok


def test_no_absolute_or_side_venv_mqlab_invocations() -> None:
    hits = [
        f"{f.relative_to(REPO_ROOT)}:{n}: {label}: {line.strip()}"
        for f in _scanned_files()
        for n, line in enumerate(f.read_text(encoding="utf-8", errors="replace").splitlines(), 1)
        for label, pattern in FORBIDDEN.items()
        if pattern.search(line)
    ]
    assert not hits, "invoke mqlab via `uv run` or an activated env (#1252):\n" + "\n".join(hits)
