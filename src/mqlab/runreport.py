"""Run metadata + the run-report bundle that turns DR/HA scenario reports into a
durable, self-describing corpus entry (pivot spec §4.4).

A bundle pairs the machine-readable + human ScenarioReports with the coordinates
that make a run reproducible: the setup/arm, the exact git commit, a digest of
the generated config actually used, tool/box/MQ versions, and a UTC timestamp.
"""

from __future__ import annotations

import hashlib
import json
import subprocess
from dataclasses import asdict, dataclass, field
from pathlib import Path
from typing import TYPE_CHECKING, Any

if TYPE_CHECKING:
    from collections.abc import Callable

    from mqlab.dr import ScenarioReport


@dataclass(frozen=True)
class RunMetadata:
    setup: str
    commit: str
    timestamp: str  # UTC, e.g. 20260615T143000Z
    config_digest: str
    versions: dict[str, str] = field(default_factory=dict)

    def to_dict(self) -> dict[str, Any]:
        return asdict(self)


def capture_metadata(
    setup: str,
    timestamp: str,
    *,
    commit_reader: Callable[[], str],
    digest_reader: Callable[[], str],
    version_reader: Callable[[], dict[str, str]],
) -> RunMetadata:
    """Assemble RunMetadata from injectable readers (real impls shell out; tests
    inject fakes). Readers MUST raise on failure — a metadata field that silently
    became "" would corrupt the corpus (no silent failures)."""
    return RunMetadata(
        setup=setup,
        commit=commit_reader(),
        timestamp=timestamp,
        config_digest=digest_reader(),
        versions=version_reader(),
    )


@dataclass(frozen=True)
class RunReport:
    metadata: RunMetadata
    scenarios: list[ScenarioReport]

    def to_dict(self) -> dict[str, Any]:
        return {
            "metadata": self.metadata.to_dict(),
            "scenarios": [s.to_dict() for s in self.scenarios],
        }

    def to_markdown(self) -> str:
        m = self.metadata
        versions = ", ".join(f"{k}={v}" for k, v in sorted(m.versions.items())) or "(none)"
        lines = [
            f"# Run report — {m.setup} @ {m.timestamp}",
            "",
            f"- Setup: `{m.setup}`",
            f"- Commit: `{m.commit}`",
            f"- Config digest: `{m.config_digest}`",
            f"- Versions: {versions}",
            "",
        ]
        for s in self.scenarios:
            lines.append(s.to_markdown())
            lines.append("")
        return "\n".join(lines)


def write_bundle(report: RunReport, root: Path) -> Path:
    """Persist the bundle under root/<timestamp>-<setup>/ as report.json + report.md.
    Returns the bundle directory."""
    m = report.metadata
    bundle = root / f"{m.timestamp}-{m.setup}"
    bundle.mkdir(parents=True, exist_ok=True)
    (bundle / "report.json").write_text(json.dumps(report.to_dict(), indent=2) + "\n")
    (bundle / "report.md").write_text(report.to_markdown())
    return bundle


def append_index(report: RunReport, bundle: Path, root: Path) -> None:
    """Append one line to root/index.jsonl: the (timestamp, setup, commit) -> bundle
    path + per-scenario rpo_zero verdicts that make the corpus searchable."""
    m = report.metadata
    entry = {
        "timestamp": m.timestamp,
        "setup": m.setup,
        "commit": m.commit,
        "bundle": str(bundle.relative_to(root)),
        "verdicts": {s.scenario_id: s.rpo_zero for s in report.scenarios},
    }
    with (root / "index.jsonl").open("a") as fh:
        fh.write(json.dumps(entry) + "\n")


def read_commit() -> str:  # pragma: no cover - shells out to git
    out = subprocess.check_output(["git", "rev-parse", "HEAD"], text=True)  # noqa: S603, S607
    return out.strip()


def read_config_digest(paths: list[Path]) -> str:  # pragma: no cover - reads files
    h = hashlib.sha256()
    for p in paths:
        h.update(p.read_bytes())
    return h.hexdigest()


def read_versions() -> dict[str, str]:  # pragma: no cover - shells out to host tools
    def _v(argv: list[str]) -> str:
        return subprocess.check_output(argv, text=True).strip().splitlines()[0]  # noqa: S603

    return {
        "vagrant": _v(["vagrant", "--version"]),  # noqa: S607
        "ansible": _v(["ansible", "--version"]),  # noqa: S607
    }
