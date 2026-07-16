"""Cold-boot staleness nudge (epic .github#91, T6).

A NOTICE-only reminder that this ephemeral /vergil dev box may be getting stale
— i.e. a while has passed since the last scorched-earth rebuild. The age is read
from the write-once ``state/.cold-boot-stamp`` that ``buildenv.ensure`` lays down
on the first lab command after a fresh box.

The nudge is **banded**: silent below ``QUIET_DAYS``, an informational line in the
mid band, and a louder ``NOTICE`` line at/above ``LOUD_DAYS``. It is a manual
bridge until the scheduled automation of #93. It **NEVER blocks and NEVER raises**
— a missing or unreadable stamp is treated as "unknown" and simply yields ``None``.
Every I/O seam (the clock, the stamp path) is injectable so it is fully unit-testable.
"""

from __future__ import annotations

from datetime import UTC, datetime
from typing import TYPE_CHECKING

from mqlab.buildenv import COLD_BOOT_STAMP
from mqlab.paths import state

if TYPE_CHECKING:
    from collections.abc import Callable
    from pathlib import Path

# Placeholder thresholds (whole days). Tunable; #93's scheduled automation
# supersedes this manual bridge. Below QUIET the nudge stays silent; at/above
# LOUD it turns into the louder NOTICE.
QUIET_DAYS = 7
LOUD_DAYS = 30


def _now() -> datetime:  # pragma: no cover - real clock (injected in tests)
    return datetime.now(tz=UTC)


def _stamp_path() -> Path:
    return state(COLD_BOOT_STAMP)


def cold_boot_age_days(*, now: Callable[[], datetime] = _now) -> int | None:
    """Whole-day age of the cold-boot stamp, or ``None`` when there is no stamp
    (a freshly rebuilt box that has not wired build/ yet) or it cannot be read /
    parsed. Never raises."""
    stamp = _stamp_path()
    if not stamp.is_file():
        return None
    try:
        stamped = datetime.fromisoformat(stamp.read_text().strip())
    except (OSError, ValueError):
        return None  # a corrupt/unreadable stamp is "unknown", never an error
    return (now() - stamped).days


def nudge(*, now: Callable[[], datetime] = _now) -> str | None:
    """A banded cold-boot staleness message, or ``None``.

    ``None`` below ``QUIET_DAYS`` (or with no stamp), an informational line in the
    mid band, and a louder ``NOTICE`` line at/above ``LOUD_DAYS``. NEVER blocks,
    NEVER raises — safe to call from any surface."""
    age = cold_boot_age_days(now=now)
    if age is None or age < QUIET_DAYS:
        return None
    if age >= LOUD_DAYS:
        return (
            f"NOTICE: this /vergil dev box is {age} days old — re-provision with "
            "`vrg-vm rebuild` to stay current (baked boxes and caches may be stale)."
        )
    return f"note: this /vergil dev box is {age} days old; a `vrg-vm rebuild` keeps it fresh."
