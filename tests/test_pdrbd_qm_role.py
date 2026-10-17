"""pdrbd-qm role + site-A provision (epic .github#310 T5, #1427).

Pinned statically because the live run needs the pcmk-drbd-ubuntu lab up:

- destructive steps (create-md, the initial promote + mkfs, crtmqm) are guarded so a
  re-run never repeats them over live data (Review Focus 3);
- no masked failures;
- the values are the RDQM blueprint's (docs/reference/rdqm-blueprint/, reconciled on
  #1427 and adopted under .github#362);
- the SAN arm's role is untouched and its moved templates still resolve;
- the provision wires the authz accounts, the mqoperator grants and mqweb like every
  other QM arm (#1501, #1502).
"""

from __future__ import annotations

import hashlib
from pathlib import Path
from typing import Any

import jinja2
import yaml

REPO_ROOT = Path(__file__).resolve().parents[1]
ANSIBLE = REPO_ROOT / "ansible"
ROLE = ANSIBLE / "roles" / "pdrbd-qm"
TASKS = ROLE / "tasks" / "main.yml"
SITE = ANSIBLE / "site-pdrbd.yml"
CLUSTER_HA = ANSIBLE / "_pdrbd-cluster-ha.yml"
QM_DOWN = ANSIBLE / "site-pdrbd-qm-down.yml"
AUTHZ = ANSIBLE / "group_vars" / "all" / "authz.yml"
# sha256 of ansible/roles/mq-pcmk-qmgr/tasks/main.yml on develop at f1f290e (2026-10-17),
# the branch point of #1427. The plan's 84d9bda value predates #1586 (mqoperator grants),
# which legitimately changed the file outside this epic. The SAN arm's role must not
# change in this epic (spec D1/D7).
_MQ_PCMK_QMGR_TASKS_SHA256 = "9248399dada3b236038c9e5fbd02122aaeca9df978782a1a950de93e7904ff57"


def _load(path: Path) -> Any:
    return yaml.safe_load(path.read_text(encoding="utf-8"))


def _tasks() -> list[tuple[dict[str, Any], str]]:
    """Every task with the `when` it inherits from an enclosing block."""
    out: list[tuple[dict[str, Any], str]] = []
    for t in _load(TASKS):
        if "block" in t:
            out.extend((inner, str(t.get("when", ""))) for inner in t["block"])
        else:
            out.append((t, ""))
    return out


def _by_name(fragment: str) -> dict[str, Any]:
    (task,) = [t for t, _ in _tasks() if fragment in t["name"]]
    return task


def _guard(fragment: str) -> str:
    task, inherited = next((t, w) for t, w in _tasks() if fragment in t["name"])
    return inherited + str(task.get("when", ""))


def _plays(path: Path) -> list[dict[str, Any]]:
    return [p for p in _load(path) if isinstance(p, dict)]


def _play_roles(play: dict[str, Any]) -> list[str]:
    return [r if isinstance(r, str) else r["role"] for r in play.get("roles") or []]


# --- re-run safety (Review Focus 3) --------------------------------------------------------


def test_pdrbd_qm_destructive_steps_are_guarded() -> None:
    for frag in ("create-md", "initial promote + mkfs", "create the queue manager"):
        assert "pdrbd_formed" in _guard(frag), f"{frag} is not guarded by pdrbd_formed"


def test_mkfs_also_requires_metadata_created_by_this_run() -> None:
    # Second, independent guard: the promote + mkfs only follows a create-md that THIS run
    # performed on the creation node, so a mis-read formation probe can never format a
    # volume that already carries metadata (and therefore data).
    assert "MD-CREATED" in _guard("initial promote + mkfs")
    assert "MD-CREATED" in _by_name("create-md")["ansible.builtin.shell"]


def test_formation_probe_separates_absent_from_error() -> None:
    # "Not formed" must mean "the CIB has no drbd_qm", never "pcs/the cluster errored":
    # an error read as "absent" would re-run first-time formation over live data.
    probe = _by_name("already own the QM's HA DRBD resource")
    assert "drbd_qm" in str(probe["ansible.builtin.command"])
    assert "105" in str(probe["failed_when"])


def test_pdrbd_qm_create_md_cannot_hang() -> None:
    body = _by_name("create-md")["ansible.builtin.shell"]
    assert "</dev/null" in body
    assert "timeout" in body


def test_pdrbd_qm_asserts_attach() -> None:
    task = _by_name("assert the drbd disk attached")
    assert "Diskless" in task["failed_when"]


def test_pdrbd_qm_never_masks_a_failure() -> None:
    for path in (TASKS, QM_DOWN, SITE, CLUSTER_HA):
        text = path.read_text(encoding="utf-8")
        assert "|| true" not in text, path.name
        assert "failed_when: false" not in text, path.name
        assert "ignore_errors" not in text, path.name


def test_every_mqsc_apply_requires_rc_zero() -> None:
    # runmqsc rc 10 means a command was refused; on a freshly created QM that is a real
    # failure, so every apply accepts rc 0 only.
    applies = [t for t, _ in _tasks() if "runmqsc" in str(t.get("ansible.builtin.command", ""))]
    assert len(applies) == 5, [t["name"] for t in applies]
    for task in applies:
        assert task["failed_when"] == "pdrbd_mqsc.rc != 0", task["name"]


def test_keystore_password_never_logged() -> None:
    task = _by_name("base queue manager MQSC")
    assert task["no_log"] is True
    assert "pki_keyrpwd" in (ROLE / "templates" / "base.mqsc.j2").read_text(encoding="utf-8")


# --- the SAN arm is untouched -------------------------------------------------------------


def test_mq_pcmk_qmgr_templates_still_resolve() -> None:
    for name in ("inter-qm.mqsc.j2", "authz.mqsc.j2"):
        p = ANSIBLE / "roles" / "mq-pcmk-qmgr" / "templates" / name
        assert p.is_symlink()
        assert p.resolve() == (ANSIBLE / "templates" / "mq" / name).resolve()


def test_mq_pcmk_qmgr_tasks_unchanged() -> None:
    data = (ANSIBLE / "roles" / "mq-pcmk-qmgr" / "tasks" / "main.yml").read_bytes()
    assert hashlib.sha256(data).hexdigest() == _MQ_PCMK_QMGR_TASKS_SHA256


# --- blueprint values (docs/reference/rdqm-blueprint/, reconciled on #1427) -----------------


def test_ha_res_is_rdqm_shaped() -> None:
    """rdqm-a1/drbd-conf.txt rdqmapp.res: site-local mesh, quorum 2, io-error, no auto-promote."""
    text = (ROLE / "templates" / "qm.res.j2").read_text(encoding="utf-8")
    for want in (
        "quorum 2;", "on-no-quorum io-error;", "auto-promote no;", "connection-mesh",
        "verify-alg sha1;", "rr-conflict retry-connect;", "disable-write-same yes;",
        "meta-disk internal;", "pdrbd_site_peers",
    ):  # fmt: skip
        assert want in text, want
    # Not set by RDQM: split-brain auto-recovery, DRBD fencing, the old six-peer WAN wiring.
    for banned in ("after-sb-", "fencing", "suspend-io", "addr_wan", "protocol A"):
        assert banned not in text, banned


def test_global_common_is_rdqms() -> None:
    """rdqm-a1/drbd-conf.txt global_common.conf (RDQM replaces the stock file)."""
    text = (ROLE / "templates" / "global_common.conf.j2").read_text(encoding="utf-8")
    for want in (
        "usage-count no;", "udev-always-use-vnr;", "twopc-timeout 100;", "ping-timeout 40;",
        "socket-check-timeout 5;", "max-buffers 40k;", "c-fill-target 1M;", "c-max-rate 500M;",
        "c-min-rate 100M;", "resync-without-replication no;",
    ):  # fmt: skip
        assert want in text, want


def test_defaults_carry_blueprint_values() -> None:
    d = _load(ROLE / "defaults" / "main.yml")
    assert (d["pdrbd_ha_port"], d["pdrbd_ha_minor"]) == (7000, 100)
    assert d["pdrbd_lv"] == "{{ pdrbd_res }}_00"
    assert d["pdrbd_lv_size"] == "3072M"
    assert d["pdrbd_stickiness"] == 1
    assert d["pdrbd_preferred_score"] == 50
    assert d["pdrbd_dr"] is False


def test_pacemaker_values_match_blueprint() -> None:
    text = TASKS.read_text(encoding="utf-8")
    for want in (
        'adjust_master_score="0 10 10000 10000"', "force_unmount=safe", "fast_stop=no",
        "resource-stickiness={{ pdrbd_stickiness }}", "stonith-enabled=false",
        "op monitor interval=20s timeout=20s role=Unpromoted",
        "op monitor interval=10s timeout=20s role=Promoted",
        "op start timeout=240s op stop timeout=100s op promote timeout=90s op demote timeout=90s",
        "cidr_netmask=24", "--no-default-ops",
    ):  # fmt: skip
        assert want in text, want
    assert "no-quorum-policy" not in text  # RDQM leaves the default (stop): CL-2
    assert "nic=" not in text  # CL-9: IPaddr2 infers the NIC from the address


def test_mkfs_targets_the_mounted_device() -> None:
    """One variable names the device mkfs formats AND Pacemaker mounts (stacked metadata
    makes the DR device smaller than the HA device, so they must never diverge)."""
    mkfs = _by_name("initial promote + mkfs")["ansible.builtin.shell"]
    assert "{{ pdrbd_fs_device }}" in mkfs
    assert "device={{ pdrbd_fs_device }}" in TASKS.read_text(encoding="utf-8")


# --- authorization: the grants every QM arm applies (#74, #1501) ---------------------------


def test_role_applies_the_shared_mqsc_templates() -> None:
    text = TASKS.read_text(encoding="utf-8")
    for name in ("templates/mq/inter-qm.mqsc.j2", "templates/mq/authz.mqsc.j2",
                 "roles/mq-pcmk-qmgr/templates/mqoperator.mqsc.j2", "grants.mqsc.j2"):  # fmt: skip
        assert name in text, name


def _render_grants() -> str:
    authz = _load(AUTHZ)
    env = jinja2.Environment(
        undefined=jinja2.StrictUndefined,
        trim_blocks=True,
        autoescape=jinja2.select_autoescape(
            enabled_extensions=(), default_for_string=False, default=False
        ),
    )
    grants = [
        {**g, "object": g["object"].replace("{{ mqsvc_dlq }}", authz["mqsvc_dlq"])}
        for g in authz["authz_grants"]
    ]
    src = (ROLE / "templates" / "grants.mqsc.j2").read_text(encoding="utf-8")
    return env.from_string(src).render(qm_name="QM1", authz_grants=grants)


def test_grants_template_is_one_set_authrec_per_setmqaut_grant() -> None:
    # The pcmk/rdqm arms apply authz_grants with setmqaut; this arm applies the SAME set as
    # MQSC SET AUTHREC (repo rule), one line per grant, same principals and authorities.
    out = _render_grants()
    lines = [ln for ln in out.splitlines() if ln.strip() and not ln.startswith("*")]
    assert len(lines) == len(_load(AUTHZ)["authz_grants"])
    assert all(ln.startswith("SET AUTHREC ") for ln in lines)
    assert "SET AUTHREC OBJTYPE(QMGR) GROUP('mqapp') AUTHADD(CONNECT, INQ)" in lines
    assert (
        "SET AUTHREC PROFILE('APP.REPLY') OBJTYPE(QUEUE) GROUP('mqsvc') AUTHADD(PUT, SETALL)"
    ) in lines
    assert (
        "SET AUTHREC PROFILE('SYSTEM.DEAD.LETTER.QUEUE') OBJTYPE(QUEUE) GROUP('mqsvc') "
        "AUTHADD(PUT, SETALL)"
    ) in lines


# --- the provision playbook -----------------------------------------------------------------


def test_site_pdrbd_refuses_dr_until_task_6() -> None:
    [pre] = [p for p in _plays(SITE) if p.get("hosts") == "localhost"]
    assert "dr_enabled" in str(pre["tasks"][0]["ansible.builtin.assert"]["that"])
    assert "--no-dr" in pre["tasks"][0]["ansible.builtin.assert"]["fail_msg"]


def test_site_pdrbd_order() -> None:
    plays = _plays(SITE)
    order = [p.get("import_playbook") or ",".join(_play_roles(p)) or p.get("hosts") for p in plays]
    assert order == [
        "localhost",
        "_pdrbd-cluster-ha.yml",
        "mq-authz-accounts",
        "pdrbd-qm",
        "site-distributed-shared.yml",
    ]


def test_site_pdrbd_peers_are_the_site_a_hb_addresses() -> None:
    [qm_play] = [p for p in _plays(SITE) if "pdrbd-qm" in _play_roles(p)]
    v = qm_play["vars"]
    assert v["pdrbd_site_group"] == "pdrbd_a"
    assert v["qm_vip"] == "10.10.1.170"  # topology.yaml pcmk-drbd-ubuntu qm.vip
    topo = _load(REPO_ROOT / "lab" / "topology.yaml")
    for i, peer in enumerate(v["pdrbd_site_peers"]):
        nics = topo["nodes"][peer["name"]]["nics"]
        assert peer["node_id"] == i
        assert peer["addr_hb"] == nics["net-hb-a"]
        assert peer["addr_wan"] == nics["net-wan"]
    assert [p["name"] for p in v["pdrbd_site_peers"]] == topo["groups"]["pdrbd_a"]


def test_cluster_ha_is_site_local_with_no_fencing() -> None:
    plays = _plays(CLUSTER_HA)
    assert all(p["hosts"] == "pdrbd_a" for p in plays)
    roles = [r for p in plays for r in _play_roles(p)]
    assert roles == ["drbd9", "mq-install", "pcmk-cluster", "mqweb"]
    [cluster] = [p for p in plays if "pcmk-cluster" in _play_roles(p)]
    topo = _load(REPO_ROOT / "lab" / "topology.yaml")
    assert cluster["vars"]["pcmk_cluster_name"] == "mqpdrbd-a"
    assert [(n["name"], n["hb"]) for n in cluster["vars"]["pcmk_site_nodes"]] == [
        (h, topo["nodes"][h]["nics"]["net-hb-a"]) for h in topo["groups"]["pdrbd_a"]
    ]
    [web] = [p for p in plays if "mqweb" in _play_roles(p)]
    assert web["serial"] == 1  # #1161: cold-start mqweb one node at a time


def test_distributed_import_uses_node_connames_and_the_site_a_vip() -> None:
    [imp] = [p for p in _plays(SITE) if p.get("import_playbook") == "site-distributed-shared.yml"]
    v = imp["vars"]
    assert v["our_conn"] == ",".join(f"pdrbd-a{i}-ext.client.com(1414)" for i in (1, 2, 3))
    assert v["app_conn"] == "pdrbd-vip-a.client.com(1414)"
    assert v["app_tls"] is True


def test_qm_down_stops_cleanly_before_delete() -> None:
    # `pcs resource delete --force` skips the clean stop; the QM and its volume must be
    # stopped through their constraints before the resources and the metadata go.
    text = QM_DOWN.read_text(encoding="utf-8")
    deletes = [
        ln
        for ln in text.splitlines()
        if "pcs resource delete" in ln and not ln.lstrip().startswith("#")
    ]
    assert deletes
    assert all("--force" not in ln for ln in deletes), deletes
    assert "wipe-md" in text
    assert "lvremove" in text
