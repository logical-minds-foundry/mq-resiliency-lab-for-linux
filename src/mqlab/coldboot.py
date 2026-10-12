"""Dev-box staleness nudge (epic .github#91, T6; corrected in #1010).

A NOTICE-only reminder that this ephemeral Vergil dev box (the VM) has not been
re-provisioned for a while, so its OS packages and tooling may be getting stale.

**What is measured.** The age of the VM itself, read from the VM-local Vergil
provisioning marker ``/etc/vergil/provisioned.base``. Vergil's base provisioning
script is ``cadence=once``: it exits early when the marker exists and ``touch``-es
it only at the end of the first successful base provision. So the marker's mtime is
the moment the box was provisioned; it is never re-touched on reboot, and it lives
on the VM's own disk, so ``vrg-vm rebuild`` (destroy + create) genuinely resets it.
(mtime rather than birth time: Python's ``os.stat`` exposes no birth time on Linux,
and for a write-once marker the two are identical.)

Before #1010 this read a write-once stamp in the host-mounted ``build/state/``
bucket. That bucket survives ``vrg-vm rebuild`` by design, so the reported age was
the time since ``build/`` was last scorched, not the box's age, and the suggested
``vrg-vm rebuild`` remedy never reset it. That stamp is retired.

The nudge is **banded**: silent below ``QUIET_DAYS``, an informational line in the
mid band, and a louder ``NOTICE`` line at/above ``LOUD_DAYS``. It is advisory and
**never blocks and never raises**. Outside a Vergil box (no marker) it is silent:
the remedy it names (``vrg-vm rebuild``) is Vergil-specific. A marker that exists
but cannot be stat-ed is NOT folded into silence or a wrong number: the nudge
returns a line naming the failure. Every I/O seam (clock, marker path) is an
injectable keyword argument so it is fully unit-testable.
"""

from __future__ import annotations

from datetime import UTC, datetime
from pathlib import Path
from typing import TYPE_CHECKING

if TYPE_CHECKING:
    from collections.abc import Callable

# Placeholder thresholds (whole days). Tunable; #93's scheduled automation
# supersedes this manual bridge. Below QUIET the nudge stays silent; at/above
# LOUD it turns into the louder NOTICE.
QUIET_DAYS = 7
LOUD_DAYS = 30

# VM-local, write-once Vergil provisioning marker (see the module docstring).
PROVISIONED_MARKER = Path("/etc/vergil/provisioned.base")


def _now() -> datetime:  # pragma: no cover - real clock (injected in tests)
    return datetime.now(tz=UTC)


def provisioned_age_days(
    *,
    now: Callable[[], datetime] = _now,
    marker: Path = PROVISIONED_MARKER,
) -> int | None:
    """Whole days since this Vergil dev box was provisioned, or ``None`` when the
    marker is absent (not a Vergil box, or one whose base provisioning never
    completed — either way there is no box age to report).

    Any other ``OSError`` (e.g. the marker exists but cannot be stat-ed) is
    deliberately left to propagate: it must not be mistaken for "not a Vergil box"
    or turned into a wrong age. ``nudge`` reports it."""
    try:
        mtime = marker.stat().st_mtime
    except FileNotFoundError:
        return None
    provisioned = datetime.fromtimestamp(mtime, tz=UTC)
    return (now() - provisioned).days


def nudge(
    *,
    now: Callable[[], datetime] = _now,
    marker: Path = PROVISIONED_MARKER,
) -> str | None:
    """A banded dev-box staleness message, or ``None``.

    ``None`` below ``QUIET_DAYS`` or outside a Vergil box, an informational line in
    the mid band, and a louder ``NOTICE`` line at/above ``LOUD_DAYS``. NEVER blocks,
    NEVER raises — safe to call from any surface. An unreadable marker yields a
    line saying the age could not be determined (and why) rather than silence."""
    try:
        age = provisioned_age_days(now=now, marker=marker)
    except OSError as exc:
        # Advisory surface: report the failure visibly instead of raising (which
        # would break doctor / box status / bootstrap) or swallowing it.
        return f"note: could not determine this dev box's age from {marker}: {exc}"
    if age is None or age < QUIET_DAYS:
        return None
    if age >= LOUD_DAYS:
        return (
            f"NOTICE: this Vergil dev box was provisioned {age} days ago — re-provision "
            "it with `vrg-vm rebuild` to pick up current OS packages and tooling."
        )
    return (
        f"note: this Vergil dev box was provisioned {age} days ago; "
        "`vrg-vm rebuild` re-provisions it fresh."
    )
