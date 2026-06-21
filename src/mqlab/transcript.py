"""Run transcripts — tee everything to build/state/runs/, never outside build/ (spec §4.3)."""

from __future__ import annotations

from typing import TYPE_CHECKING

from mqlab.paths import build_root, runs_dir

if TYPE_CHECKING:
    from pathlib import Path
    from types import TracebackType


class TranscriptError(RuntimeError):
    """A transcript path escaped the gitignored build/ tree."""


def transcript_path(verb: str, timestamp: str) -> Path:
    """build/state/runs/<timestamp>-<verb>.log."""
    return runs_dir() / f"{timestamp}-{verb}.log"


class Transcript:
    """A line tee that refuses any destination outside the build/ tree."""

    def __init__(self, path: Path) -> None:
        build_tree = build_root()  # the whole build/ tree (#286), not just runs_dir().parent
        resolved = path.resolve()
        if build_tree not in resolved.parents:
            raise TranscriptError(f"transcript {resolved} is not under {build_tree}")
        resolved.parent.mkdir(parents=True, exist_ok=True)
        self.path = resolved
        self._fh = resolved.open("w", encoding="utf-8")

    def write(self, line: str) -> None:
        self._fh.write(line + "\n")
        self._fh.flush()

    def close(self) -> None:
        self._fh.close()

    def __enter__(self) -> Transcript:
        return self

    def __exit__(
        self,
        exc_type: type[BaseException] | None,
        exc: BaseException | None,
        tb: TracebackType | None,
    ) -> None:
        self.close()
