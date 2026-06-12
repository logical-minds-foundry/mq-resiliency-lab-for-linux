"""Generic, syslog-shaped structured log emitter for the lab's observation apps.

One JSON object per line to stdout: {"ts", "level", "msg"}. host + unit are added
downstream as journald + Alloy labels, never in the line. Deliberately tiny and
dependency-free so it ships flat next to the client scripts (deployed as
/home/vagrant/obslog.py, imported as `from obslog import emit`) and still imports
as mqlab.obslog in tests.
"""

from __future__ import annotations

import json
import sys
from datetime import UTC, datetime
from typing import TYPE_CHECKING

if TYPE_CHECKING:
    from collections.abc import Callable
    from typing import TextIO


def format_line(level: str, msg: str, ts: str) -> str:
    """Pure: build the one-line JSON record (no trailing newline)."""
    return json.dumps({"ts": ts, "level": level, "msg": msg}, separators=(",", ":"))


def _format_ts(now: datetime) -> str:
    """RFC 3339, UTC, millisecond precision, Z suffix."""
    return now.isoformat(timespec="milliseconds").replace("+00:00", "Z")


def emit(
    level: str,
    msg: str,
    *,
    stream: TextIO | None = None,
    clock: Callable[[], datetime] | None = None,
) -> None:
    """Print one JSON log line to stdout. stream + clock are injectable for tests.

    flush=True so lines reach journald promptly for live tail.
    """
    out = sys.stdout if stream is None else stream
    now = datetime.now(tz=UTC) if clock is None else clock()
    print(format_line(level, msg, _format_ts(now)), file=out, flush=True)
