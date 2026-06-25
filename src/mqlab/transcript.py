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
        resolved = path.resolve()
        # The transcript must land inside the build/ tree (#286). But state/ —
        # which holds runs/ — is a SHARED bucket: in a git worktree it is a
        # symlink to the primary checkout's build/state, so the resolved path
        # legitimately sits outside *this* worktree's build/ (#69). Accept the
        # resolved runs anchor too, so the lab can be driven from a worktree —
        # without letting a transcript escape to an arbitrary path.
        anchors = (build_root().resolve(), runs_dir().resolve())
        if not any(anchor in resolved.parents for anchor in anchors):
            raise TranscriptError(
                f"transcript {resolved} is not under {anchors[0]} or its runs dir {anchors[1]}"
            )
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
