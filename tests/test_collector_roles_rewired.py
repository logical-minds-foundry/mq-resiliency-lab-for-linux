"""Guard the collector roles' rewire onto the baked component (epic .github#294 T7).

The collectors (lab-cluster-state, lab-nativeha-state + lab-loglifecycle-state,
lab-rdqm-state) are the mq-resiliency-observability component, baked into each box that
lab/versions.yaml lists it for (runtime-install, then component-install). The per-run roles
cluster-state / nativeha-state / rdqm-state only: assert the component is baked, retire the
pre-#294 payloads AND their /etc/systemd/system unit copies (systemd prefers /etc over the
component's /usr/lib units, so a surviving copy would keep running the old /usr/bin/python3
ExecStart — Review Focus 1), render the deployer-owned env file, daemon-reload and enable
the timer. Structural tests over the YAML; the live proof is the V1/V2 cold rebuilds.
"""

from __future__ import annotations

import re
from pathlib import Path
from typing import Any

import pytest
import yaml

from mqlab import versions
from tests.test_boot_trim_baked import _iter_tasks, _plays, _role_includes

REPO_ROOT = Path(__file__).resolve().parents[1]
ANSIBLE = REPO_ROOT / "ansible"
ROLES = ANSIBLE / "roles"
COMPONENT = "mq-resiliency-observability"
UNITS_DIR = REPO_ROOT / "components" / COMPONENT / "systemd"
INSTALLED = f"/opt/logical-minds-foundry/{COMPONENT}/INSTALLED.json"
ENV_DIR = f"/etc/opt/logical-minds-foundry/{COMPONENT}"

# role -> the component unit (service + timer stem) its per-run half drives.
UNIT = {
    "cluster-state": "lab-cluster-state",
    "nativeha-state": "lab-nativeha-state",
    "rdqm-state": "lab-rdqm-state",
}
# role -> the pre-#294 payloads it deployed outside the component layout.
OLD_PAYLOADS = {
    "cluster-state": {"/usr/local/bin/lab-cluster-state"},
    "nativeha-state": {
        "/usr/local/bin/lab-nativeha-state",
        "/usr/local/bin/nativehastate.py",
        "/usr/local/bin/lab-loglifecycle-state",
    },
    "rdqm-state": {"/usr/local/lib/lab-rdqm-state"},
}
# The bakes of every box role whose catalog entry lists the component (spec §5.8).
OBSERVABILITY_BAKES = {"pcmk-ubuntu", "san", "nativeha-ubuntu", "nativeha-rhel", "mq-rdqm"}


def _tasks(role: str) -> list[dict[str, Any]]:
    tasks = yaml.safe_load((ROLES / role / "tasks" / "main.yml").read_text(encoding="utf-8"))
    assert isinstance(tasks, list) and tasks
    return tasks


def _absent_paths(task: dict[str, Any]) -> set[str]:
    spec = task.get("ansible.builtin.file") or {}
    if spec.get("state") != "absent":
        return set()
    if spec.get("path") == "{{ item }}":
        return set(task.get("loop") or [])
    return {str(spec.get("path"))}


def _retire(role: str) -> tuple[int, set[str]]:
    """The single loop task that retires the pre-#294 payloads and unit copies."""
    hits = [
        (i, _absent_paths(t))
        for i, t in enumerate(_tasks(role))
        if isinstance(t.get("loop"), list) and _absent_paths(t)
    ]
    assert len(hits) == 1, f"{role}: expected one retire-loop task, got {hits}"
    return hits[0]


def _systemd(role: str) -> list[tuple[int, dict[str, Any]]]:
    return [
        (i, t["ansible.builtin.systemd"])
        for i, t in enumerate(_tasks(role))
        if "ansible.builtin.systemd" in t
    ]


def _unit_vars(unit_file: Path) -> set[str]:
    return set(re.findall(r"\$\{([A-Za-z_][A-Za-z0-9_]*)\}", unit_file.read_text(encoding="utf-8")))


def _env_keys(template: Path) -> list[str]:
    text = re.sub(r"\{#.*?#\}", "", template.read_text(encoding="utf-8"), flags=re.DOTALL)
    keys = []
    for line in text.splitlines():
        line = line.strip()
        if line and not line.startswith("#"):
            key, sep, _ = line.partition("=")
            assert sep, f"{template.name}: not a KEY=value line: {line!r}"
            keys.append(key)
    return keys


# --- per-run roles -------------------------------------------------------------------


@pytest.mark.parametrize("role", sorted(UNIT))
def test_collector_roles_assert_baked_component(role: str) -> None:
    tasks = _tasks(role)
    stat, check = tasks[0], tasks[1]
    assert stat["ansible.builtin.stat"]["path"] == INSTALLED, "the FIRST task looks for it"
    spec = check["ansible.builtin.assert"]
    assert spec["that"] == f"{stat['register']}.stat.exists"
    assert "mqlab box build" in spec["fail_msg"]
    assert f"mqlab component install {COMPONENT} --host" in spec["fail_msg"]


@pytest.mark.parametrize("role", sorted(UNIT))
def test_rewired_roles_remove_etc_unit_copies(role: str) -> None:  # Review Focus 1
    unit = UNIT[role]
    at, paths = _retire(role)
    assert {f"/etc/systemd/system/{unit}.service", f"/etc/systemd/system/{unit}.timer"} <= paths
    reloads = [i for i, spec in _systemd(role) if spec.get("daemon_reload") is True]
    assert reloads and min(reloads) > at, f"{role}: daemon-reload must follow the removal"
    (enable,) = [(i, s) for i, s in _systemd(role) if s.get("enabled") is True]
    assert enable[0] > at
    assert enable[1]["name"] == f"{unit}.timer"
    assert enable[1]["state"] == "started"


@pytest.mark.parametrize("role", sorted(UNIT))
def test_rewired_roles_drop_only_the_stale_wants_link(role: str) -> None:
    """A pre-#294 enable linked timers.target.wants to the /etc copy; only THAT link goes,
    before the enable re-links the component unit (idempotent on a rewired guest)."""
    unit = UNIT[role]
    link = f"/etc/systemd/system/timers.target.wants/{unit}.timer"
    tasks = _tasks(role)
    (look,) = [
        i for i, t in enumerate(tasks) if t.get("ansible.builtin.stat", {}).get("path") == link
    ]
    assert tasks[look]["ansible.builtin.stat"]["follow"] is False
    (drop,) = [i for i, t in enumerate(tasks) if _absent_paths(t) == {link}]
    assert look < drop
    when = tasks[drop]["when"]
    assert tasks[look]["register"] in when and f"/etc/systemd/system/{unit}.timer" in when
    (enable,) = [i for i, s in _systemd(role) if s.get("enabled") is True]
    assert drop < enable


@pytest.mark.parametrize("role", sorted(UNIT))
def test_rewired_roles_remove_old_payloads(role: str) -> None:
    _, paths = _retire(role)
    assert OLD_PAYLOADS[role] <= paths, f"{role}: misses {OLD_PAYLOADS[role] - paths}"


@pytest.mark.parametrize("role", sorted(UNIT))
def test_rewired_roles_never_copy_source(role: str) -> None:
    for path in (ROLES / role).rglob("*.yml"):
        for task in _iter_tasks(yaml.safe_load(path.read_text(encoding="utf-8"))):
            for spec in task.values():
                if isinstance(spec, dict) and "src" in spec:
                    src = str(spec["src"])
                    assert "src/mqlab" not in src and "clients/" not in src, (path, src)
                    assert "playbook_dir" not in src, (path, src)
    # The units belong to the component now; the role keeps only its env template.
    templates = sorted(p.name for p in (ROLES / role / "templates").iterdir())
    assert templates == [f"{UNIT[role]}.env.j2"]


@pytest.mark.parametrize("role", sorted(UNIT))
def test_env_templates_match_unit_variables(role: str) -> None:
    unit = UNIT[role]
    service = UNITS_DIR / f"{unit}.service"
    env_file = f"{ENV_DIR}/{unit}.env"
    assert f"EnvironmentFile={env_file}\n" in service.read_text(encoding="utf-8")
    keys = _env_keys(ROLES / role / "templates" / f"{unit}.env.j2")
    assert len(keys) == len(set(keys)), f"{unit}.env.j2: duplicate keys {keys}"
    assert set(keys) == _unit_vars(service), f"{unit}: env keys vs the unit's ${{VAR}}s"
    (render,) = [t for t in _tasks(role) if "ansible.builtin.template" in t]
    spec = render["ansible.builtin.template"]
    assert (spec["src"], spec["dest"], spec["mode"]) == (f"{unit}.env.j2", env_file, "0644")
    renders_at = _tasks(role).index(render)
    (enable,) = [i for i, s in _systemd(role) if s.get("enabled") is True]
    assert renders_at < enable, "the env file must exist before the timer starts the unit"


@pytest.mark.parametrize(
    ("role", "value"),
    [
        ("cluster-state", "ROLE={{ cluster_state_role }}"),
        ("nativeha-state", "QM={{ nativeha_qm | default('NHARCAPP') }}"),
        ("rdqm-state", "QM={{ rdqm_qm | default('RDQMAPP') }}"),
    ],
)
def test_env_templates_carry_the_overlay_vars(role: str, value: str) -> None:
    """The site values are the observability overlay's vars (ansible/observability.yml)."""
    text = (ROLES / role / "templates" / f"{UNIT[role]}.env.j2").read_text(encoding="utf-8")
    assert value in text.splitlines()


# --- bakes ---------------------------------------------------------------------------


def _catalog_bakes() -> set[str]:
    roles = versions.load_catalog().roles
    return {
        stem
        for spec in roles.values()
        if COMPONENT in spec["components"]
        for stem in spec["bake"].values()
    }


def test_observability_bakes_are_the_catalogs() -> None:
    assert _catalog_bakes() == OBSERVABILITY_BAKES


@pytest.mark.parametrize("stem", sorted(OBSERVABILITY_BAKES))
def test_bakes_install_observability_component(stem: str) -> None:
    plays = _plays(stem)
    (play,) = [p for p in plays if _role_includes(p, "component-install")]
    tasks = list(_iter_tasks(play["tasks"]))
    (runtime,) = _role_includes(play, "runtime-install")
    (comp,) = _role_includes(play, "component-install")
    assert tasks.index(runtime) < tasks.index(comp), f"{stem}: runtime-install goes FIRST"
    assert comp["vars"] == {"component_name": COMPONENT}
    assert "component_start_units" not in comp["vars"], "units stay inert at bake"
    assert play["become"] is True
    assert sum(len(_role_includes(p, "runtime-install")) for p in plays) == 1


def test_other_bakes_do_not_install_observability() -> None:
    for bake in sorted(ANSIBLE.glob("bake-*.yml")):
        stem = bake.stem.removeprefix("bake-")
        if stem in OBSERVABILITY_BAKES:
            continue
        for task in _role_includes(_plays(stem), "component-install"):
            assert (task.get("vars") or {}).get("component_name") != COMPONENT, stem
