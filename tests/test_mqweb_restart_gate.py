"""Guard for the mqweb TLS-restart race fix (#1169, epic .github#249).

The VAL-A arm64 reproducibility gate (#1154) caught mqweb `failed` on a first cold
`nativeha-ubuntu --no-dr` bootstrap: the role started mqweb non-blocking and then,
because `mqwebuser_xml is changed` on first render, restarted it for the TLS cert
milliseconds later — while Liberty was still in its ~215 s cold-start. The restart's
`strmqweb` found the server lock held ("file lock obtained, and server process is
running", exit 22), the `Type=forking` unit failed, and systemd reaped the still-
initialising JVM. A clean single start succeeds on the same box, and
`WLP_STARTUP_TIMEOUT` was not the gating knob — it is a start/restart race.

The fix gates the TLS restart on `mqweb_pre_active.rc == 0` (mqweb was ALREADY
running before this play — a genuine cert rotation on a live server), so a first
bootstrap's fresh start (which already loaded the pre-rendered TLS config) is never
interrupted. These guards pin that gate + its ordering so a regression is caught at
`vrg-validate` time, not at the slow cold-rebuild acceptance gate.
"""

from __future__ import annotations

from pathlib import Path

import yaml

REPO_ROOT = Path(__file__).resolve().parent.parent
MQWEB_TASKS = REPO_ROOT / "ansible" / "roles" / "mqweb" / "tasks" / "main.yml"


def _load_tasks(path: Path) -> list[dict]:
    tasks = yaml.safe_load(path.read_text(encoding="utf-8"))
    assert isinstance(tasks, list) and tasks, f"{path} did not parse to a non-empty task list"
    return tasks


def _index_of(tasks: list[dict], name_substr: str) -> int:
    for i, t in enumerate(tasks):
        if name_substr in (t.get("name") or ""):
            return i
    raise AssertionError(f"no mqweb task whose name contains {name_substr!r} (#1169)")


def test_tls_restart_is_gated_on_mqweb_already_running() -> None:
    """The TLS restart must only fire for a cert change against an ALREADY-running
    mqweb — its `when:` must require `mqweb_pre_active.rc == 0`. Without the gate the
    restart races the fresh cold-start and gets the JVM reaped (#1169)."""
    tasks = _load_tasks(MQWEB_TASKS)
    restart = tasks[_index_of(tasks, "restart mqweb to pick up the TLS cert")]
    when = restart.get("when")
    when_str = " ".join(when) if isinstance(when, list) else str(when)
    assert "mqweb_pre_active.rc == 0" in when_str, (
        "the mqweb TLS-restart must be gated on `mqweb_pre_active.rc == 0` so it does not "
        f"race a first-bootstrap cold-start (#1169); got when={when!r}"
    )
    # The original conditions must remain (only restart on a real TLS-cert change).
    assert "mqweb_tls" in when_str and "mqwebuser_xml is changed" in when_str, (
        f"the TLS-restart lost its original cert-change conditions (#1169); when={when!r}"
    )


def test_pre_active_probe_runs_before_the_start() -> None:
    """The `mqweb_pre_active` probe must be registered BEFORE the start task, so it
    captures the pre-start state (not the state after we just started it) (#1169)."""
    tasks = _load_tasks(MQWEB_TASKS)
    probe_idx = next(
        (i for i, t in enumerate(tasks) if t.get("register") == "mqweb_pre_active"),
        None,
    )
    assert probe_idx is not None, "no task registers `mqweb_pre_active` (#1169)"
    start_idx = _index_of(tasks, "start mqweb without blocking")
    restart_idx = _index_of(tasks, "restart mqweb to pick up the TLS cert")
    assert probe_idx < start_idx < restart_idx, (
        "the mqweb_pre_active probe must run before the start, which must run before the "
        f"TLS restart (#1169); got probe={probe_idx}, start={start_idx}, restart={restart_idx}"
    )
    probe = tasks[probe_idx]
    assert probe.get("changed_when") is False and probe.get("failed_when") is False, (
        "the mqweb_pre_active probe must be a non-mutating, non-fatal check "
        f"(changed_when/failed_when false) (#1169); got {probe!r}"
    )


def test_failed_mqweb_is_surfaced_distinctly_from_slow() -> None:
    """A mqweb unit that actually FAILED (not merely slow) must be surfaced — the
    masking of the two is what let a reaped mqweb pass as a green bootstrap (#1169)."""
    tasks = _load_tasks(MQWEB_TASKS)
    warn = tasks[_index_of(tasks, "warn loudly if mqweb failed to start")]
    when = warn.get("when")
    when_str = " ".join(when) if isinstance(when, list) else str(when)
    assert "'failed'" in when_str or '"failed"' in when_str, (
        f"the loud-warn task must fire on a 'failed' mqweb unit state (#1169); got when={when!r}"
    )
