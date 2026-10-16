"""mqoperator (MQWebUser) account and IBM-minimum grants (epic .github#341 spec §2.2, S15).

Under the ``MQWebUser`` role mqweb authorizes every request as the logged-in user's own
OS identity (IBM S8), so ``mqoperator`` needs (a) an identical OS account on every node
its queue manager can run on (IBM S10), and (b) OAM grants on every queue manager. These
tests pin both, and guard that every QM-bearing play applies them (plan Review Focus 4).
"""

from __future__ import annotations

from pathlib import Path
from typing import Any

import jinja2
import yaml

REPO_ROOT = Path(__file__).resolve().parent.parent
ANSIBLE = REPO_ROOT / "ansible"
AUTHZ = ANSIBLE / "group_vars" / "all" / "authz.yml"
TEMPLATE = ANSIBLE / "roles" / "mq-pcmk-qmgr" / "templates" / "mqoperator.mqsc.j2"
TOPOLOGY = REPO_ROOT / "lab" / "topology.yaml"
SHARED = ANSIBLE / "site-distributed-shared.yml"


def _authz() -> dict[str, Any]:
    data: dict[str, Any] = yaml.safe_load(AUTHZ.read_text(encoding="utf-8"))
    return data


def _render(grants: list[dict[str, str]]) -> str:
    # Renders MQSC text, never HTML: string templates must NOT be HTML-escaped (the
    # quotes in GROUP('mqoperator') would be mangled). An explicit select_autoescape
    # policy states that instead of autoescape=False (CodeQL py/jinja2/autoescape-false).
    env = jinja2.Environment(
        undefined=jinja2.StrictUndefined,
        trim_blocks=True,
        autoescape=jinja2.select_autoescape(
            enabled_extensions=(), default_for_string=False, default=False
        ),
    )
    return env.from_string(TEMPLATE.read_text(encoding="utf-8")).render(
        qm_name="QM1", authz_mqoperator_grants=grants
    )


def test_mqoperator_account_declared() -> None:
    assert {"name": "mqoperator", "group": "mqoperator"} in _authz()["authz_accounts"]


def test_grants_cover_ibm_minimum_for_mqwebuser() -> None:
    grants = {
        (g["objtype"], g.get("profile", "")): g["authadd"]
        for g in _authz()["authz_mqoperator_grants"]
    }
    # IBM S15, "Any users with the MQWebUser role must have the following authorities".
    assert grants[("QMGR", "")] == "CONNECT, DSP"
    assert grants[("TOPIC", "SYSTEM.ADMIN.TOPIC")] == "SUB, DSP"
    assert grants[("QUEUE", "SYSTEM.REST.REPLY.QUEUE")] == "PUT, GET, BROWSE, INQ"
    assert grants[("QUEUE", "SYSTEM.ADMIN.COMMAND.QUEUE")] == "PUT"
    # Display-only object access (spec §2.2 decision).
    assert grants[("QUEUE", "**")] == "DSP, INQ, BROWSE"
    assert grants[("CHANNEL", "**")] == "DSP"
    assert grants[("TOPIC", "**")] == "DSP"
    assert grants[("NAMELIST", "**")] == "DSP"
    assert len(grants) == len(_authz()["authz_mqoperator_grants"]), "duplicate grant key"


def test_grants_are_display_only_beyond_ibm_minimum() -> None:
    forbidden = {
        "CHG", "CLR", "DLT", "CRT", "SET", "SETALL", "SETID", "ALTUSR",
        "CTRL", "CTRLX", "ALL", "ALLADM", "ALLMQI", "PASSALL", "PASSID", "PUB", "RESUME",
    }  # fmt: skip
    for g in _authz()["authz_mqoperator_grants"]:
        auths = {a.strip() for a in g["authadd"].split(",")}
        assert not auths & forbidden, g


def test_template_renders_one_set_authrec_per_grant() -> None:
    grants = _authz()["authz_mqoperator_grants"]
    out = _render(grants)
    lines = [ln for ln in out.splitlines() if ln.startswith("SET AUTHREC")]
    assert len(lines) == len(grants)
    assert all("GROUP('mqoperator')" in ln for ln in lines)
    assert "SET AUTHREC OBJTYPE(QMGR) GROUP('mqoperator') AUTHADD(CONNECT, DSP)" in out
    assert (
        "SET AUTHREC PROFILE('SYSTEM.REST.REPLY.QUEUE') OBJTYPE(QUEUE) "
        "GROUP('mqoperator') AUTHADD(PUT, GET, BROWSE, INQ)"
    ) in out
    # Every non-comment, non-blank line is a SET AUTHREC: nothing else rides along.
    body = [ln for ln in out.splitlines() if ln.strip() and not ln.startswith("*")]
    assert body == lines


def _plays(path: Path) -> list[dict[str, Any]]:
    loaded = yaml.safe_load(path.read_text(encoding="utf-8")) or []
    return [p for p in loaded if isinstance(p, dict)]


def _play_roles(play: dict[str, Any]) -> set[str]:
    return {r if isinstance(r, str) else r.get("role", "") for r in play.get("roles") or []}


def _plays_roles(path: Path) -> set[str]:
    roles: set[str] = set()
    for play in _plays(path):
        roles |= _play_roles(play)
    return roles


def _accounts_plays(path: Path) -> list[dict[str, Any]]:
    return [p for p in _plays(path) if "mq-authz-accounts" in _play_roles(p)]


# Stacks declared in the topology whose provision playbook has not landed yet. Such a
# stack is not bootstrappable, so it hosts no QM to guard. The entry is temporary on
# purpose: test_pending_provision_playbooks_are_still_absent fails the moment the
# playbook lands, which forces its removal here and puts it under the guards below.
#   ansible/site-pdrbd.yml — pcmk-drbd-ubuntu (#1426), provision playbook due in #1427.
PENDING_PROVISION = {"ansible/site-pdrbd.yml"}


def _provision_playbooks() -> set[Path]:
    topo = yaml.safe_load(TOPOLOGY.read_text(encoding="utf-8"))
    declared = {s["provision"] for s in topo["stacks"].values()}
    return {REPO_ROOT / p for p in declared - PENDING_PROVISION}


def test_pending_provision_playbooks_are_still_absent() -> None:
    for rel in sorted(PENDING_PROVISION):
        assert not (REPO_ROOT / rel).exists(), (
            f"{rel} has landed: drop it from PENDING_PROVISION so the mqoperator guards "
            "cover it (it must apply mq-authz-accounts and mqoperator.mqsc.j2)"
        )


def test_every_qm_play_applies_authz_accounts() -> None:
    # Review Focus 4: every stack's provision playbook, and the svc-sim play, apply
    # mq-authz-accounts, so a new arm cannot silently lack the mqoperator OS account.
    playbooks = _provision_playbooks()
    assert len(playbooks) >= 4, "stack discovery regressed"
    playbooks.add(SHARED)
    for pb in sorted(playbooks):
        assert "mq-authz-accounts" in _plays_roles(pb), f"{pb.name} lacks mq-authz-accounts"


def test_accounts_plays_become_root() -> None:
    # The role creates OS groups and users, so its play must escalate; svc-sim's mq-qmgr
    # play runs without play-level become, which is why the accounts get their own play.
    playbooks = _provision_playbooks() | {SHARED}
    for pb in sorted(playbooks):
        for play in _accounts_plays(pb):
            assert play.get("become") is True, f"{pb.name}: {play.get('hosts')} lacks become"


def test_nativeha_rhel_crr_accounts_cover_both_sites() -> None:
    # spec §2.2: every instance, both sites, including the CRR recovery group.
    hosts = [str(p["hosts"]) for p in _accounts_plays(ANSIBLE / "site-nativeha.yml")]
    assert any("nha_rhel_crr_a" in h and "nha_rhel_crr_b" in h for h in hosts), hosts


def test_svc_sim_accounts_precede_the_qm() -> None:
    plays = _plays(SHARED)
    idx_accounts = next(i for i, p in enumerate(plays) if "mq-authz-accounts" in _play_roles(p))
    idx_qmgr = next(i for i, p in enumerate(plays) if "mq-qmgr" in _play_roles(p))
    assert idx_accounts < idx_qmgr
    assert plays[idx_accounts]["hosts"] == "svc"


def test_every_qm_play_applies_mqoperator_mqsc() -> None:
    files = [
        ANSIBLE / "roles" / "mq-pcmk-qmgr" / "tasks" / "main.yml",
        ANSIBLE / "site-rdqm.yml",
        ANSIBLE / "site-nativeha-ubuntu.yml",
        ANSIBLE / "site-nativeha.yml",
        ANSIBLE / "roles" / "mq-qmgr" / "tasks" / "main.yml",
    ]
    for f in files:
        assert "mqoperator.mqsc.j2" in f.read_text(encoding="utf-8"), f.name
