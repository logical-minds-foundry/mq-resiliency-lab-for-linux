"""Dev-box staleness nudge (epic .github#91 T6; corrected in #1010).

The box age is the mtime of the VM-local, write-once Vergil provisioning marker
(/etc/vergil/provisioned.base), which `vrg-vm rebuild` genuinely resets — not a
stamp in the host-mounted build/state bucket, which survives a rebuild. The nudge
NEVER blocks and NEVER raises: no marker (not a Vergil box) is silent, and an
unreadable marker is reported as a line rather than swallowed. The clock and the
marker path are injected as keyword arguments, so no test touches /etc or the
real clock.
"""

from __future__ import annotations

import os
from datetime import UTC, datetime, timedelta

from mqlab import coldboot

_EPOCH = datetime(2026, 1, 1, tzinfo=UTC)


def _marker(tmp_path, *, provisioned: datetime = _EPOCH):
    """A provisioning marker whose mtime is `provisioned` (like `touch` at provision)."""
    marker = tmp_path / "provisioned.base"
    marker.touch()
    ts = provisioned.timestamp()
    os.utime(marker, (ts, ts))
    return marker


def _clock(days_after_epoch: int):
    return lambda: _EPOCH + timedelta(days=days_after_epoch)


# --------------------------------------------------------------------------- #
# provisioned_age_days                                                        #
# --------------------------------------------------------------------------- #
def test_age_is_none_outside_a_vergil_box(tmp_path):
    assert coldboot.provisioned_age_days(marker=tmp_path / "absent") is None


def test_age_is_whole_days_since_provisioning(tmp_path):
    marker = _marker(tmp_path)
    assert coldboot.provisioned_age_days(now=_clock(5), marker=marker) == 5


def test_age_tracks_the_marker_so_a_rebuild_resets_it(tmp_path):
    # a rebuild lays a fresh marker; the age is measured from it, not some older stamp
    marker = _marker(tmp_path, provisioned=_EPOCH + timedelta(days=40))
    assert coldboot.provisioned_age_days(now=_clock(41), marker=marker) == 1


def test_default_marker_is_the_vm_local_vergil_provisioning_marker():
    assert str(coldboot.PROVISIONED_MARKER) == "/etc/vergil/provisioned.base"


# --------------------------------------------------------------------------- #
# nudge banding                                                               #
# --------------------------------------------------------------------------- #
def test_nudge_silent_outside_a_vergil_box(tmp_path):
    assert coldboot.nudge(now=_clock(400), marker=tmp_path / "absent") is None


def test_nudge_silent_in_quiet_band(tmp_path):
    marker = _marker(tmp_path)
    assert coldboot.nudge(now=_clock(coldboot.QUIET_DAYS - 1), marker=marker) is None


def test_nudge_informational_in_mid_band(tmp_path):
    marker = _marker(tmp_path)
    msg = coldboot.nudge(now=_clock(coldboot.QUIET_DAYS), marker=marker)
    assert msg is not None
    assert "NOTICE" not in msg  # mid band is informational, not the loud NOTICE
    assert f"provisioned {coldboot.QUIET_DAYS} days ago" in msg
    assert "vrg-vm rebuild" in msg


def test_nudge_loud_notice_at_loud_threshold(tmp_path):
    marker = _marker(tmp_path)
    msg = coldboot.nudge(now=_clock(coldboot.LOUD_DAYS), marker=marker)
    assert msg is not None
    assert msg.startswith("NOTICE")
    assert f"provisioned {coldboot.LOUD_DAYS} days ago" in msg
    assert "vrg-vm rebuild" in msg


def test_nudge_reports_an_unreadable_marker_instead_of_raising(tmp_path):
    # a stat failure other than "absent" must be surfaced, not swallowed or aged
    marker = tmp_path / "not-a-dir" / "provisioned.base"
    (tmp_path / "not-a-dir").write_text("a file, so stat-ing beneath it fails")
    msg = coldboot.nudge(now=_clock(400), marker=marker)
    assert msg is not None
    assert "could not determine" in msg
    assert str(marker) in msg
