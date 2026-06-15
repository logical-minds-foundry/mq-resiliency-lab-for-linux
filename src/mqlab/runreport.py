"""Run metadata + the run-report bundle that turns DR/HA scenario reports into a
durable, self-describing corpus entry (pivot spec §4.4).

A bundle pairs the machine-readable + human ScenarioReports with the coordinates
that make a run reproducible: the setup/arm, the exact git commit, a digest of
the generated config actually used, tool/box/MQ versions, and a UTC timestamp.
"""

from __future__ import annotations

from dataclasses import asdict, dataclass, field
from typing import TYPE_CHECKING, Any

if TYPE_CHECKING:
    from collections.abc import Callable
    from pathlib import Path

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
