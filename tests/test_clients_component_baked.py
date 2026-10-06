"""Guard the baked mq-resiliency-clients component on the mq-client box (epic .github#294 T9).

The clients (requester, bench, svc responder, probes, DR flow) used to run from two
hand-built pymqi venvs: one created on the app host every provision, and the svc
responder's, baked by mq-inter-qm's install half (#1227). Both are replaced by the
mq-resiliency-clients component, baked into the mq-client box (bake-mq-ubuntu.yml) by
runtime-install then component-install. pymqi compiles from sdist against the MQ SDK, so
the component MUST follow mq-install in the bake. These guards fail loudly if the bake
stops installing the component, installs it before MQ, or brings back the old venv bake.
"""

from __future__ import annotations

from pathlib import Path
from typing import Any

import pytest
import yaml

from mqlab import versions

REPO_ROOT = Path(__file__).resolve().parents[1]
ANSIBLE = REPO_ROOT / "ansible"
BAKE = ANSIBLE / "bake-mq-ubuntu.yml"
COMPONENT = "mq-resiliency-clients"


def _load(path: Path) -> Any:
    return yaml.safe_load(path.read_text(encoding="utf-8"))


def _includes(task: dict[str, Any], role: str) -> bool:
    spec = task.get("ansible.builtin.include_role")
    return isinstance(spec, dict) and spec.get("name") == role


def _main_bake_tasks(plays: Any) -> list[dict[str, Any]]:
    """The tasks of THE main bake play: the one play that includes mq-install. The bake
    playbook may carry other plays (e.g. OS-tuning plays before/after, #1225/#1229), so
    select by content rather than position; zero or several such plays is an error."""
    assert isinstance(plays, list), f"{BAKE.name}: expected a list of plays"
    main = [
        play["tasks"]
        for play in plays
        if isinstance(play, dict)
        and isinstance(play.get("tasks"), list)
        and any(_includes(t, "mq-install") for t in play["tasks"])
    ]
    count = len(main)
    assert count == 1, f"{BAKE.name}: expected one play including mq-install, got {count}"
    return main[0]


def _bake_tasks() -> list[dict[str, Any]]:
    return _main_bake_tasks(_load(BAKE))


def _index(tasks: list[dict[str, Any]], role: str) -> int:
    hits = [i for i, t in enumerate(tasks) if _includes(t, role)]
    assert len(hits) == 1, f"{BAKE.name}: expected one include_role {role}, got {len(hits)}"
    return hits[0]


def test_bake_installs_the_component_after_mq_install() -> None:
    """mq-install (SDK + gcc) -> runtime-install -> component-install mq-resiliency-clients."""
    tasks = _bake_tasks()
    mq_install = _index(tasks, "mq-install")
    runtime = _index(tasks, "runtime-install")
    component = _index(tasks, "component-install")
    assert mq_install < runtime < component, "pymqi compiles against the SDK; runtime FIRST"
    assert tasks[component]["vars"] == {"component_name": COMPONENT}


def test_bake_no_longer_builds_the_responder_venv() -> None:
    """The pre-#294 mq-inter-qm install half (the responder's own venv) is gone."""
    for task in _bake_tasks():
        assert not _includes(task, "mq-inter-qm"), "the component replaces the responder venv"
    assert not (ANSIBLE / "roles" / "mq-inter-qm" / "tasks" / "install.yml").exists()


def test_catalog_bakes_clients_only_into_the_mq_client_box() -> None:
    roles = versions.load_catalog().roles
    bakes = {
        stem
        for spec in roles.values()
        if COMPONENT in spec["components"]
        for stem in spec["bake"].values()
    }
    assert bakes == {"mq-ubuntu"}


_OTHER_PLAY: dict[str, Any] = {
    "name": "an OS-tuning play",
    "hosts": "bake",
    "tasks": [{"name": "noop", "ansible.builtin.debug": {"msg": "x"}}],
}


def test_main_bake_play_is_selected_among_extra_plays() -> None:
    """Sibling plays before/after the main bake play (#1225/#1229) must not break the
    selection: the main play is found by its mq-install include, not by position."""
    real = _load(BAKE)
    tasks = _main_bake_tasks([_OTHER_PLAY, *real, _OTHER_PLAY])
    assert _index(tasks, "mq-install") < _index(tasks, "component-install")


@pytest.mark.parametrize(("copies", "count"), [(0, 0), (2, 2)])
def test_main_bake_play_selection_fails_loud_on_zero_or_many(copies: int, count: int) -> None:
    plays = [_OTHER_PLAY] + _load(BAKE) * copies
    with pytest.raises(AssertionError, match=f"got {count}"):
        _main_bake_tasks(plays)
