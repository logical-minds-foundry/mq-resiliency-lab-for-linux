"""Guard the client roles' rewire onto the baked component (epic .github#294 T9).

The clients (mq-app-requester, mq-bench, mq-svc-responder, the authz/DLQ probes, the DR
flow) are the mq-resiliency-clients component, baked into the mq-client box
(runtime-install, then component-install). The per-run roles mq-client, app-requester,
bench-client and mq-inter-qm only: assert the component is baked, retire the pre-#294
payloads (the per-provision pymqi venv, the responder venv, the loose scripts) AND the
/etc/systemd/system unit copies (systemd prefers /etc over the component's /usr/lib units,
so a surviving copy would keep running the old ExecStart, Review Focus 1), render the
deployer-owned env files and the responder's QM-ordering drop-in, daemon-reload and
enable/start. Structural tests over the YAML; the live proof is the V1/V2 cold rebuilds.
"""

from __future__ import annotations

import re
from pathlib import Path
from typing import Any

import pytest
import yaml

from tests.test_boot_trim_baked import _iter_tasks, _plays, _role_includes

REPO_ROOT = Path(__file__).resolve().parents[1]
ANSIBLE = REPO_ROOT / "ansible"
ROLES = ANSIBLE / "roles"
COMPONENT = "mq-resiliency-clients"
UNITS_DIR = REPO_ROOT / "components" / COMPONENT / "systemd"
INSTALLED = f"/opt/logical-minds-foundry/{COMPONENT}/INSTALLED.json"
ENV_DIR = f"/etc/opt/logical-minds-foundry/{COMPONENT}"
BIN = f"/opt/logical-minds-foundry/{COMPONENT}/venv/bin"

CLIENT_ROLES = ("app-requester", "bench-client", "mq-client", "mq-inter-qm")
# role -> the pre-#294 payloads its retire loop removes.
OLD_PAYLOADS = {
    "mq-client": {
        "/home/vagrant/mqvenv",
        *(
            f"/home/vagrant/{name}.py"
            for name in (
                "app_requester",
                "bench_client",
                "authz_probe",
                "dlq_probe",
                "reconnect_probe",
                "dr_flow",
                "dr_responder",
                "dr_mqi",
                "dr_baseline",
                "dr_forced",
            )
        ),
    },
    "bench-client": {"/home/vagrant/bench_client.py", "/home/vagrant/mq-bench"},
    "app-requester": {"/etc/systemd/system/mq-app-requester.service"},
    "mq-inter-qm": {
        "/var/mqm/rvenv",
        "/var/mqm/svc_responder.py",
        "/etc/systemd/system/mq-svc-responder@.service",
    },
}
# role -> (the component unit it enables, its /etc copy, its multi-user.target.wants link).
SERVICES = {
    "app-requester": (
        "mq-app-requester",
        "/etc/systemd/system/mq-app-requester.service",
        "/etc/systemd/system/multi-user.target.wants/mq-app-requester.service",
    ),
    "mq-inter-qm": (
        "mq-svc-responder@{{ svc_req_queue }}.service",
        "/etc/systemd/system/mq-svc-responder@.service",
        "/etc/systemd/system/multi-user.target.wants/mq-svc-responder@{{ svc_req_queue }}.service",
    ),
}
# role -> (env template, the component unit file that reads it).
ENV = {
    "app-requester": ("mq-app-requester.env.j2", "mq-app-requester.service"),
    "mq-inter-qm": ("mq-svc-responder.env.j2", "mq-svc-responder@.service"),
}
AUTHZ_PLAYS = {
    "site-pcmk-authz-validate.yml": 10,
    "site-nativeha-ubuntu-authz-validate.yml": 4,
    "site-rdqm-authz-validate.yml": 4,
}
DROPIN = "/etc/systemd/system/mq-svc-responder@.service.d/qm.conf"


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


def _enable(role: str) -> tuple[int, dict[str, Any]]:
    (hit,) = [(i, s) for i, s in _systemd(role) if s.get("enabled") is True]
    return hit


def _render(role: str, dest: str) -> int:
    (hit,) = [
        i
        for i, t in enumerate(_tasks(role))
        if (t.get("ansible.builtin.template") or {}).get("dest") == dest
    ]
    return hit


def _unit_vars(unit_file: Path) -> set[str]:
    """Every ${VAR} and bare $VAR the unit expands (TLS_ARGS is unbraced on purpose)."""
    text = unit_file.read_text(encoding="utf-8")
    return set(re.findall(r"\$\{?([A-Za-z_][A-Za-z0-9_]*)", text))


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


@pytest.mark.parametrize("role", CLIENT_ROLES)
def test_client_roles_assert_baked_component(role: str) -> None:
    tasks = _tasks(role)
    stat, check = tasks[0], tasks[1]
    assert stat["ansible.builtin.stat"]["path"] == INSTALLED, "the FIRST task looks for it"
    spec = check["ansible.builtin.assert"]
    assert spec["that"] == f"{stat['register']}.stat.exists"
    assert "mqlab box build" in spec["fail_msg"]
    assert f"mqlab component install {COMPONENT} --host" in spec["fail_msg"]


@pytest.mark.parametrize("role", sorted(SERVICES))
def test_client_roles_remove_etc_unit_copies(role: str) -> None:  # Review Focus 1
    unit, etc_copy, _ = SERVICES[role]
    at, paths = _retire(role)
    assert etc_copy in paths
    enable_at, enable = _enable(role)
    assert enable_at > at, f"{role}: enable must follow the removal"
    assert enable["daemon_reload"] is True, f"{role}: daemon-reload after the removal"
    assert enable["name"] == unit
    assert "started" in enable["state"]


@pytest.mark.parametrize("role", sorted(SERVICES))
def test_client_roles_drop_only_the_stale_wants_link(role: str) -> None:
    """A pre-#294 enable linked multi-user.target.wants to the /etc copy; only THAT link
    goes, before the enable re-links the component unit (idempotent on a rewired guest)."""
    _, etc_copy, link = SERVICES[role]
    tasks = _tasks(role)
    (look,) = [
        i for i, t in enumerate(tasks) if t.get("ansible.builtin.stat", {}).get("path") == link
    ]
    assert tasks[look]["ansible.builtin.stat"]["follow"] is False
    (drop,) = [i for i, t in enumerate(tasks) if _absent_paths(t) == {link}]
    assert look < drop
    when = tasks[drop]["when"]
    assert tasks[look]["register"] in when and f"'{etc_copy}'" in when
    assert drop < _enable(role)[0]


@pytest.mark.parametrize("role", CLIENT_ROLES)
def test_client_roles_remove_old_payloads(role: str) -> None:
    _, paths = _retire(role)
    assert OLD_PAYLOADS[role] <= paths, f"{role}: misses {OLD_PAYLOADS[role] - paths}"


@pytest.mark.parametrize("role", CLIENT_ROLES)
def test_client_roles_never_copy_source(role: str) -> None:
    for path in (ROLES / role).rglob("*.yml"):
        for task in _iter_tasks(yaml.safe_load(path.read_text(encoding="utf-8"))):
            for verb, spec in task.items():
                if verb == "ansible.builtin.copy" and isinstance(spec, dict):
                    src = str(spec.get("src", ""))
                    assert not src.endswith(".py") and not _OLD_REF.search(src), (path, src)


def test_client_roles_keep_only_env_templates() -> None:
    """The units belong to the component now; the roles keep their env/drop-in renders."""
    templates = {
        role: sorted(p.name for p in (ROLES / role / "templates").glob("*"))
        for role in CLIENT_ROLES
        if (ROLES / role / "templates").is_dir()
    }
    assert templates == {
        "app-requester": ["mq-app-requester.env.j2"],
        "mq-inter-qm": [
            "mq-svc-responder-qm.conf.j2",
            "mq-svc-responder.env.j2",
            "their-side.mqsc.j2",
        ],
    }


@pytest.mark.parametrize("role", sorted(ENV))
def test_env_templates_match_unit_variables(role: str) -> None:
    template, unit_name = ENV[role]
    unit = UNITS_DIR / unit_name
    env_file = f"{ENV_DIR}/{template.removesuffix('.j2')}"
    assert f"EnvironmentFile={env_file}\n" in unit.read_text(encoding="utf-8")
    keys = _env_keys(ROLES / role / "templates" / template)
    assert len(keys) == len(set(keys)), f"{template}: duplicate keys {keys}"
    assert set(keys) == _unit_vars(unit), f"{unit_name}: env keys vs the unit's $VARs"
    at = _render(role, env_file)
    spec = _tasks(role)[at]["ansible.builtin.template"]
    assert (spec["src"], spec["mode"]) == (template, "0644")
    assert at < _enable(role)[0], "the env file must exist before the unit starts"


def test_app_requester_tls_args_render() -> None:
    lines = (ROLES / "app-requester" / "templates" / "mq-app-requester.env.j2").read_text(
        encoding="utf-8"
    )
    assert (
        "TLS_ARGS={% if app_requester_tls | bool %}--keyrepo {{ app_requester_keyrepo }}"
        " --certlabel {{ app_requester_certlabel }}{% endif %}"
    ) in lines.splitlines()


def test_svc_responder_qm_dropin() -> None:
    """The component unit cannot name the site QM; the deployer orders it on mq-<QM>."""
    text = (ROLES / "mq-inter-qm" / "templates" / "mq-svc-responder-qm.conf.j2").read_text(
        encoding="utf-8"
    )
    live = [ln for ln in text.splitlines() if ln and not ln.startswith(("#", "{#", "   "))]
    assert live == [
        "[Unit]",
        "After=mq-{{ qmgr_name }}.service",
        "Requires=mq-{{ qmgr_name }}.service",
    ]
    tasks = _tasks("mq-inter-qm")
    at = _render("mq-inter-qm", DROPIN)
    assert tasks[at]["ansible.builtin.template"]["src"] == "mq-svc-responder-qm.conf.j2"
    (mkdir,) = [
        i
        for i, t in enumerate(tasks)
        if (t.get("ansible.builtin.file") or {}).get("path") == str(Path(DROPIN).parent)
        and t["ansible.builtin.file"].get("state") == "directory"
    ]
    enable_at, enable = _enable("mq-inter-qm")
    assert mkdir < at < enable_at
    assert enable["daemon_reload"] is True, "the drop-in only takes effect after a reload"
    assert tasks[at]["register"] in enable["state"], "a changed drop-in restarts the unit"


def test_mq_client_keeps_compiler_drops_distro_python_dev() -> None:
    """gcc stays on the running box: a live component install recompiles pymqi (spec 5.8)."""
    tasks = _tasks("mq-client")
    (apt,) = [t for t in tasks if "apt-get install -y gcc" in str(t.get("ansible.builtin.shell"))]
    script = apt["ansible.builtin.shell"]
    assert "python3-venv" not in script and "python3-dev" not in script
    for task in _iter_tasks(tasks):
        spec = task.get("ansible.builtin.apt") or task.get("ansible.builtin.package") or {}
        assert not (spec.get("state") in ("absent", "purged") and "gcc" in str(spec)), task
        shell = str(task.get("ansible.builtin.shell", ""))
        assert not re.search(r"apt(-get)?\s+(-y\s+)?(remove|purge|autoremove)", shell), task
        assert "ansible.builtin.pip" not in task, "no per-provision pip install"


def test_bench_client_links_the_entry_point() -> None:
    tasks = _tasks("bench-client")
    (link,) = [
        t["ansible.builtin.file"] for t in tasks if "src" in t.get("ansible.builtin.file", {})
    ]
    assert link == {"src": f"{BIN}/mq-bench", "dest": "/usr/local/bin/mq-bench", "state": "link"}
    assert not (ROLES / "bench-client" / "templates").exists(), "the wrapper template is gone"


# --- playbooks + scripts -------------------------------------------------------------

# The old delivery path: the two hand-built venvs, the repo-root clients/ dir (NOT the
# component's own mq-resiliency-clients/ path) and the loose requester script.
_OLD_REF = re.compile(r"mqvenv|rvenv|(?<![\w-])clients/|app_requester\.py")


def _retire_lines() -> set[str]:
    return {f"- {p}" for paths in OLD_PAYLOADS.values() for p in paths}


def test_no_role_or_playbook_references_mqvenv_or_rvenv() -> None:
    """Only the retire-list entries that remove the old payloads may name them."""
    allowed = _retire_lines()
    files = [
        *ANSIBLE.rglob("*.yml"),
        *ANSIBLE.rglob("*.j2"),
        *(REPO_ROOT / "lab" / "scripts").rglob("*"),
    ]
    hits = [
        f"{path.relative_to(REPO_ROOT)}:{n}: {line.strip()}"
        for path in files
        if path.is_file()
        for n, line in enumerate(path.read_text(encoding="utf-8").splitlines(), 1)
        if _OLD_REF.search(line) and line.strip() not in allowed
    ]
    assert hits == []


@pytest.mark.parametrize(
    ("line", "flagged"),
    [
        ("python3 -m venv /home/vagrant/mqvenv", True),
        ("/var/mqm/rvenv/bin/python", True),
        ('src: "{{ playbook_dir }}/../clients/x.py"', True),
        ("~/app_requester.py --count 1", True),
        (f"{BIN}/mq-app-requester --count 1", False),
    ],
)
def test_old_reference_pattern(line: str, flagged: bool) -> None:
    assert bool(_OLD_REF.search(line)) is flagged


def test_mq_ubuntu_bake_installs_clients_component() -> None:
    plays = _plays("mq-ubuntu")
    (play,) = [p for p in plays if _role_includes(p, "component-install")]
    tasks = list(_iter_tasks(play["tasks"]))
    (runtime,) = _role_includes(play, "runtime-install")
    (comp,) = _role_includes(play, "component-install")
    assert tasks.index(runtime) < tasks.index(comp), "runtime-install goes FIRST"
    assert comp["vars"] == {"component_name": COMPONENT}
    assert "component_start_units" not in comp["vars"], "units stay inert at bake"
    assert play["become"] is True


def _commands(playbook: str) -> list[list[str]]:
    plays = yaml.safe_load((ANSIBLE / playbook).read_text(encoding="utf-8"))
    return [
        [str(a) for a in task["ansible.builtin.command"]["argv"]]
        for task in _iter_tasks(plays)
        if isinstance(task.get("ansible.builtin.command"), dict)
        and "argv" in task["ansible.builtin.command"]
    ]


@pytest.mark.parametrize("playbook", sorted(AUTHZ_PLAYS))
def test_authz_validate_plays_call_entry_points(playbook: str) -> None:
    calls = [argv for argv in _commands(playbook) if argv[0].startswith(f"{BIN}/mq-")]
    assert len(calls) == AUTHZ_PLAYS[playbook], f"{playbook}: {len(calls)} client calls"
    for argv in calls:
        assert argv[0].removeprefix(f"{BIN}/") in {
            "mq-authz-probe",
            "mq-dlq-probe",
            "mq-app-requester",
        }, argv
    for argv in _commands(playbook):
        assert not any(a.endswith(".py") or "python" in a for a in argv), argv
    text = (ANSIBLE / playbook).read_text(encoding="utf-8")
    assert "venv_python" not in text and "probe_dest" not in text and "dlq_dest" not in text


def test_dr_run_uses_the_component_entry_points() -> None:
    text = (REPO_ROOT / "lab" / "scripts" / "dr-run.sh").read_text(encoding="utf-8")
    assert f"CLIENTS={BIN}\n" in text
    for host, entry in (("svc-sim", "mq-dr-responder"), ("app-client", "mq-dr-flow")):
        call = f'vagrant ssh {host} -c \\\n  "LD_LIBRARY_PATH=/opt/mqm/lib64 $CLIENTS/{entry} '
        assert call in text, entry
