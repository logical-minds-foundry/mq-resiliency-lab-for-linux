"""Cold-boot staleness nudge (epic .github#91, T6).

The stamp is write-once (buildenv.ensure lays it down the first time a fresh
/vergil runs a lab command); coldboot reads it and returns a *banded* NOTICE.
The nudge NEVER blocks and NEVER raises — a missing or corrupt stamp yields None.
Every I/O seam is monkeypatchable so these unit tests never touch a real clock/fs.
"""

from __future__ import annotations

from datetime import UTC, datetime

from mqlab import coldboot


def _at(days_ago: int):
    """A `now` seam that reports the clock `days_ago` after a fixed stamp epoch."""
    epoch = datetime(2026, 1, 1, tzinfo=UTC)
    return epoch, (lambda: epoch + _timedelta(days_ago))


def _timedelta(days: int):
    from datetime import timedelta

    return timedelta(days=days)


def _write_stamp(monkeypatch, tmp_path, *, iso: str) -> None:
    stamp = tmp_path / coldboot.COLD_BOOT_STAMP
    stamp.write_text(iso)
    monkeypatch.setattr(coldboot, "_stamp_path", lambda: stamp)


# --------------------------------------------------------------------------- #
# cold_boot_age_days                                                          #
# --------------------------------------------------------------------------- #
def test_age_is_none_without_stamp(monkeypatch, tmp_path):
    monkeypatch.setattr(coldboot, "_stamp_path", lambda: tmp_path / "absent")
    assert coldboot.cold_boot_age_days() is None


def test_age_is_whole_days(monkeypatch, tmp_path):
    epoch, now = _at(5)
    _write_stamp(monkeypatch, tmp_path, iso=epoch.isoformat())
    assert coldboot.cold_boot_age_days(now=now) == 5


def test_age_is_none_on_corrupt_stamp(monkeypatch, tmp_path):
    _write_stamp(monkeypatch, tmp_path, iso="not-a-timestamp")
    # never raises — a corrupt stamp is treated as "unknown", not an error
    assert coldboot.cold_boot_age_days() is None


# --------------------------------------------------------------------------- #
# nudge banding                                                               #
# --------------------------------------------------------------------------- #
def test_nudge_silent_without_stamp(monkeypatch, tmp_path):
    monkeypatch.setattr(coldboot, "_stamp_path", lambda: tmp_path / "absent")
    assert coldboot.nudge() is None


def test_nudge_silent_in_quiet_band(monkeypatch, tmp_path):
    epoch, now = _at(coldboot.QUIET_DAYS - 1)
    _write_stamp(monkeypatch, tmp_path, iso=epoch.isoformat())
    assert coldboot.nudge(now=now) is None


def test_nudge_informational_in_mid_band(monkeypatch, tmp_path):
    epoch, now = _at(coldboot.QUIET_DAYS)
    _write_stamp(monkeypatch, tmp_path, iso=epoch.isoformat())
    msg = coldboot.nudge(now=now)
    assert msg is not None
    assert "NOTICE" not in msg  # mid band is informational, not the loud NOTICE
    assert str(coldboot.QUIET_DAYS) in msg


def test_nudge_loud_notice_at_loud_threshold(monkeypatch, tmp_path):
    epoch, now = _at(coldboot.LOUD_DAYS)
    _write_stamp(monkeypatch, tmp_path, iso=epoch.isoformat())
    msg = coldboot.nudge(now=now)
    assert msg is not None
    assert msg.startswith("NOTICE")
    assert str(coldboot.LOUD_DAYS) in msg


def test_nudge_never_raises_on_old_corrupt_stamp(monkeypatch, tmp_path):
    _write_stamp(monkeypatch, tmp_path, iso="garbage")
    assert coldboot.nudge() is None  # must not raise


def test_stamp_path_is_under_state_bucket():
    # the real stamp lives in the shared state bucket as the write-once dotfile
    p = coldboot._stamp_path()
    assert p.name == coldboot.COLD_BOOT_STAMP
    assert p.parent.name == "state"
