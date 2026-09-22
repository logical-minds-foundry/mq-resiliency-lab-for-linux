"""Guard that cold provision quiesces apt auto-updates (#1173, epic .github#249).

VAL-A (#1154) showed that on a freshly-booted Ubuntu node `apt-daily-upgrade.timer`
fires and `unattended-upgrade` grabs `/var/lib/dpkg/lock-frontend`, so the stack's
later apt tasks block on the lock — a ~20-min silent cold-bootstrap stall. The lab
boxes are pinned/ephemeral and must not auto-update at boot (epic #70 §4.5). The fix
lives in `site-dns.yml` (the earliest all-hosts touch): mask the apt timers and wait
out any in-flight run before provisioning. These guards pin that so a regression is
caught at `vrg-validate` time, not at the slow cold-rebuild gate.
"""

from __future__ import annotations

from pathlib import Path
from typing import Any

import yaml

REPO_ROOT = Path(__file__).resolve().parent.parent
SITE_DNS = REPO_ROOT / "ansible" / "site-dns.yml"


def _all_tasks() -> list[Any]:
    """Every task across every play in site-dns.yml (flattened)."""
    plays = yaml.safe_load(SITE_DNS.read_text(encoding="utf-8"))
    assert isinstance(plays, list) and plays, "site-dns.yml did not parse to plays"
    tasks: list[Any] = []
    for play in plays:
        if isinstance(play, dict):
            tasks.extend(play.get("tasks", []) or [])
    return tasks


def _task(name_substr: str) -> dict:
    for t in _all_tasks():
        if isinstance(t, dict) and name_substr in (t.get("name") or ""):
            return t
    raise AssertionError(f"site-dns.yml has no task whose name contains {name_substr!r} (#1173)")


def test_apt_auto_update_timers_are_masked() -> None:
    """Both apt auto-update timers must be masked so they never (re-)fire (#1173)."""
    mask = _task("mask apt auto-update timers")
    systemd = (
        mask.get("ansible.builtin.systemd") or mask.get("ansible.builtin.systemd_service") or {}
    )
    assert systemd.get("masked") is True, (
        f"the apt-timer task must set masked: true (#1173); got {mask!r}"
    )
    loop = mask.get("loop") or []
    assert "apt-daily.timer" in loop and "apt-daily-upgrade.timer" in loop, (
        f"must mask both apt-daily.timer and apt-daily-upgrade.timer (#1173); loop={loop!r}"
    )
    assert "Debian" in str(mask.get("when", "")) or "_apt_debian" in str(mask.get("when", "")), (
        f"the mask must be gated to Debian/Ubuntu guests (#1173); when={mask.get('when')!r}"
    )


def test_provision_waits_out_in_flight_apt_update() -> None:
    """Provision must wait (bounded, fail-loud) for the apt auto-update services to go
    idle so the dpkg lock is free before later apt tasks run (#1173)."""
    wait = _task("wait for the apt auto-update services to go idle")
    assert "apt-daily" in str(wait.get("ansible.builtin.command", "")), (
        f"the wait must query the apt-daily services (#1173); got {wait!r}"
    )
    assert wait.get("until") and wait.get("retries") and wait.get("delay"), (
        f"the wait must be a bounded until/retries/delay loop (fail-loud) (#1173); got {wait!r}"
    )
    # The until must treat oneshot 'activating' as busy, not just 'active'.
    assert "activating" in str(wait.get("until")), (
        f"the until must count 'activating' (oneshot) as busy, not only 'active' (#1173); "
        f"until={wait.get('until')!r}"
    )
