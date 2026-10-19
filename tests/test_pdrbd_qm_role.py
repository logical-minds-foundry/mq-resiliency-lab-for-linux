"""pdrbd-qm role + the pcmk-drbd-ubuntu provision (epic .github#310 T5 #1427, T6 #1428).

Pinned statically because the live run needs the pcmk-drbd-ubuntu lab up:

- destructive steps (create-md, the stacked create-md, the initial promotes + mkfs,
  crtmqm) are guarded so a re-run never repeats them over live data, and a formed QM's DR
  shape never changes (Review Focus 3);
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
DR_REPLICATION = ANSIBLE / "_pdrbd-dr-replication.yml"
PEERS = ANSIBLE / "vars" / "pdrbd-peers.yml"
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


_HA_CREATE_MD = "create-md (only when"


def test_pdrbd_qm_destructive_steps_are_guarded() -> None:
    for frag in (
        _HA_CREATE_MD,
        "stacked create-md",
        "HA initial promote",
        "initial promote + mkfs",
        "create the queue manager",
    ):
        assert "pdrbd_formed" in _guard(frag), f"{frag} is not guarded by pdrbd_formed"


def test_mkfs_also_requires_metadata_created_by_this_run() -> None:
    # Second, independent guard: every promote-with-cleared-bitmap and the mkfs only follow
    # a create-md that THIS run performed on the creation node, so a mis-read formation
    # probe can never clear a bitmap over, or format, a volume that already carries data.
    for frag in ("HA initial promote", "stacked create-md", "initial promote + mkfs"):
        assert "'MD-CREATED' in (pdrbd_md.stdout" in _guard(frag), frag
    assert "DR-MD-CREATED" in _guard("initial promote + mkfs")
    assert "MD-CREATED" in _by_name(_HA_CREATE_MD)["ansible.builtin.shell"]
    assert "DR-MD-CREATED" in _by_name("stacked create-md")["ansible.builtin.shell"]


def test_formation_probe_separates_absent_from_error() -> None:
    # "Not formed" must mean "the CIB has no drbd_qm", never "pcs/the cluster errored":
    # an error read as "absent" would re-run first-time formation over live data.
    probe = _by_name("already own the QM's HA DRBD resource")
    assert "drbd_qm" in str(probe["ansible.builtin.command"])
    assert "105" in str(probe["failed_when"])


def test_pdrbd_qm_create_md_cannot_hang() -> None:
    for frag in (_HA_CREATE_MD, "stacked create-md"):
        body = _by_name(frag)["ansible.builtin.shell"]
        assert "</dev/null" in body, frag
        assert "timeout" in body, frag


def test_pdrbd_qm_asserts_attach() -> None:
    task = _by_name("assert the drbd disk attached")
    assert "Diskless" in task["failed_when"]


def test_pdrbd_qm_never_masks_a_failure() -> None:
    for path in (TASKS, QM_DOWN, SITE, CLUSTER_HA, DR_REPLICATION):
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
        "meta-disk internal;", "pdrbd_site_peers", "tls yes;",
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
    assert "dr_enabled" in d["pdrbd_dr"]
    assert (d["pdrbd_dr_port"], d["pdrbd_dr_minor"]) == (7001, 7001)
    assert d["pdrbd_dr_res"] == "{{ pdrbd_res }}.dr"


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


def _qm_play() -> dict[str, Any]:
    [play] = [p for p in _plays(SITE) if "pdrbd-qm" in _play_roles(p)]
    return play


def test_site_pdrbd_has_no_dr_refusal_preflight() -> None:
    # Task 5's temporary "refuse a DR provision" play is gone: the DR/HA shape is built.
    assert not [p for p in _plays(SITE) if p.get("hosts") == "localhost"]
    assert "refuse a DR provision" not in SITE.read_text(encoding="utf-8")


def test_site_pdrbd_order() -> None:
    plays = _plays(SITE)
    order = [p.get("import_playbook") or ",".join(_play_roles(p)) or p.get("hosts") for p in plays]
    assert order == [
        "_pdrbd-cluster-ha.yml",
        "_pdrbd-dr-replication.yml",
        "pdrbd-replication-tls",  # certs + tlshd before any DRBD resource (#1609)
        "mq-authz-accounts",
        "pdrbd-qm",
        "pdrbd_a",  # the watched DR-sync wait
        "site-pdrbd-tls-verify.yml",  # every connection encrypted (#1609)
        "site-distributed-shared.yml",
    ]


def test_site_b_is_skipped_by_no_dr() -> None:
    plays = _plays(SITE)
    [imp] = [p for p in plays if p.get("import_playbook") == "_pdrbd-dr-replication.yml"]
    assert imp["when"] == "dr_enabled | default(true) | bool"
    both = "pdrbd_a{{ ':pdrbd_b' if (dr_enabled | default(true) | bool) else '' }}"
    [authz] = [p for p in plays if "mq-authz-accounts" in _play_roles(p)]
    assert authz["hosts"] == both  # the OAM principals exist wherever DR lands the QM
    assert _qm_play()["hosts"] == both


def test_one_pdrbd_qm_play_over_both_sites_derives_per_site_values() -> None:
    # ONE play over both sites, so formation interleaves across them in task order (site
    # B's DR member is up before site A clears the DR bitmap).
    play = _qm_play()
    assert play["vars_files"] == ["vars/pdrbd-peers.yml", "vars/pdrbd-crm-mon.yml"]
    v = play["vars"]
    assert v["pdrbd_site_group"] == "pdrbd_{{ pdrbd_site }}"
    assert "selectattr('site', 'equalto', pdrbd_site)" in v["pdrbd_site_peers"]
    assert "rejectattr('site', 'equalto', pdrbd_site)" in v["pdrbd_remote_peers"]
    assert v["pdrbd_dr_local_id"] == "{{ 0 if pdrbd_site == 'a' else 1 }}"
    qm = _load(REPO_ROOT / "lab" / "topology.yaml")["stacks"]["pcmk-drbd-ubuntu"]["qm"]
    vip_a, vip_b = qm["vip"], qm["vip_b"]
    assert v["qm_vip"] == "{{ '" + vip_a + "' if pdrbd_site == 'a' else '" + vip_b + "' }}"


def test_peer_vars_match_the_topology_groups() -> None:
    peers = _load(PEERS)["pdrbd_peers_all"]
    groups = _load(REPO_ROOT / "lab" / "topology.yaml")["groups"]
    for site in ("a", "b"):
        assert [p["name"] for p in peers if p["site"] == site] == groups[f"pdrbd_{site}"]


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


def test_site_b_is_its_own_cluster_with_no_fencing() -> None:
    # RDQM: each site its own 3-node cluster, none spanning the sites (CL-1).
    plays = _plays(DR_REPLICATION)
    assert all(p["hosts"] == "pdrbd_b" for p in plays)
    roles = [r for p in plays for r in _play_roles(p)]
    assert roles == ["drbd9", "mq-install", "pcmk-cluster", "mqweb"]
    [cluster] = [p for p in plays if "pcmk-cluster" in _play_roles(p)]
    topo = _load(REPO_ROOT / "lab" / "topology.yaml")
    assert cluster["vars"]["pcmk_cluster_name"] == "mqpdrbd-b"
    assert [(n["name"], n["hb"]) for n in cluster["vars"]["pcmk_site_nodes"]] == [
        (h, topo["nodes"][h]["nics"]["net-hb-b"]) for h in topo["groups"]["pdrbd_b"]
    ]
    [web] = [p for p in plays if "mqweb" in _play_roles(p)]
    assert web["serial"] == 1


def test_distributed_import_reaches_the_qm_at_either_site() -> None:
    # Both sites, site A first: after a DR cutover the QM runs at site B, and with only
    # site-A names e2e failed live with MQRC 2538 (#1428).
    [imp] = [p for p in _plays(SITE) if p.get("import_playbook") == "site-distributed-shared.yml"]
    v = imp["vars"]
    assert v["our_conn"] == ",".join(
        f"pdrbd-{s}{i}-ext.client.com(1414)" for s in ("a", "b") for i in (1, 2, 3)
    )
    assert v["app_conn"] == "pdrbd-vip-a.client.com(1414),pdrbd-vip-b.client.com(1414)"
    assert v["app_tls"] is True


def test_every_pdrbd_node_named_in_our_conn_has_a_net_ext_nic() -> None:
    topo = _load(REPO_ROOT / "lab" / "topology.yaml")
    for h in topo["groups"]["pdrbd_a"] + topo["groups"]["pdrbd_b"]:
        assert "net-ext" in topo["nodes"][h]["nics"], h


# --- the DR/HA shape (T6, #1428) --------------------------------------------------------------


def _render_dr_res(host: str) -> str:
    """Render qm.dr.res.j2 for `host` with the play's derivations, computed here."""
    peers = _load(PEERS)["pdrbd_peers_all"]
    me = next(p for p in peers if p["name"] == host)
    env = jinja2.Environment(
        undefined=jinja2.StrictUndefined,
        trim_blocks=True,
        autoescape=jinja2.select_autoescape(
            enabled_extensions=(), default_for_string=False, default=False
        ),
    )
    src = (ROLE / "templates" / "qm.dr.res.j2").read_text(encoding="utf-8")
    return env.from_string(src).render(
        ansible_managed="managed",
        inventory_hostname=host,
        pdrbd_res="pdrbdapp",
        pdrbd_dr_res="pdrbdapp.dr",
        pdrbd_dr_minor=7001,
        pdrbd_dr_port=7001,
        pdrbd_dr_peer_name="encrypted.remote",
        pdrbd_dr_local_id=0 if me["site"] == "a" else 1,
        pdrbd_my_wan=me["addr_wan"],
        pdrbd_remote_peers=[p for p in peers if p["site"] != me["site"]],
    )


def test_dr_res_is_rdqm_shaped() -> None:
    """rdqm-a1/drbd-conf.txt rdqmapp.dr.res: stacked, 2 members, 3 paths, protocol A."""
    text = (ROLE / "templates" / "qm.dr.res.j2").read_text(encoding="utf-8")
    for want in (
        "stacked-on-top-of {{ pdrbd_res }}", "quorum majority;", "on-no-quorum io-error;",
        "auto-promote no;", "protocol A;", "verify-alg sha1;", "allow-remote-read no;",
        "disk _remote;", "path {", "pdrbd_remote_peers", "inventory_hostname", "tls yes;",
    ):  # fmt: skip
        assert want in text, want
    # Not set by RDQM: split-brain auto-recovery, DRBD fencing; and never the HA layout.
    # Handlers are IBM's rdqmhandler (TL-3/DR-3). tls: tests/test_pdrbd_replication_tls.py.
    for banned in ("after-sb-", "fencing", "meta-disk", "connection-mesh", "handlers"):
        assert banned not in text, banned


def test_dr_res_renders_per_node_as_rdqm_does() -> None:
    # rdqm-a1/drbd-dump.txt: stacked node-id 0, remote node-id 1, paths from rdqm-a1's WAN
    # address to each site-B WAN address. rdqm-b1/drbd-dump.txt: the ids swapped.
    a2 = _render_dr_res("pdrbd-a2")
    assert "stacked-on-top-of pdrbdapp {\n        node-id 0;" in a2
    assert "on encrypted.remote {\n        node-id 1;" in a2
    assert a2.count("path {") == 3
    for wan in ("10.99.0.81", "10.99.0.82", "10.99.0.83"):
        assert (
            f"host pdrbd-a2 address ipv4 10.99.0.72:7001;\n"
            f"            host encrypted.remote address ipv4 {wan}:7001;"
        ) in a2
    b1 = _render_dr_res("pdrbd-b1")
    assert "stacked-on-top-of pdrbdapp {\n        node-id 1;" in b1
    assert "on encrypted.remote {\n        node-id 0;" in b1
    assert "host encrypted.remote address ipv4 10.99.0.71:7001;" in b1
    assert "device minor 7001;" in b1


def test_dr_template_rendered_before_the_parse_check() -> None:
    names = [t["name"] for t, _ in _tasks()]
    dr = names.index("DR resource definition (stacked; rendered per node, RDQM's rdqmapp.dr.res)")
    assert dr < next(i for i, n in enumerate(names) if "rendered DRBD config parses" in n)
    assert _by_name("DR resource definition")["when"] == "pdrbd_dr | bool"


def test_dr_clone_matches_blueprint() -> None:
    text = TASKS.read_text(encoding="utf-8")
    for want in (
        "drbd_resource={{ pdrbd_dr_res }} wfc_timeout=false",
        "clone-max=1",
        "constraint colocation add drbd_dr_qm-clone with Promoted drbd_qm-clone INFINITY",
        "constraint order promote drbd_qm-clone then start drbd_dr_qm-clone",
        "constraint colocation add mq_group with Promoted drbd_dr_qm-clone INFINITY",
        "constraint order promote drbd_dr_qm-clone then start mq_group",
        "constraint order start drbd_dr_qm-clone then start mq_group",
        "resource meta drbd_dr_qm-clone target-role="
        "{{ 'Promoted' if pdrbd_site_group == 'pdrbd_a' else 'Unpromoted' }}",
    ):
        assert want in text, want


def test_dr_role_marker_set_only_at_formation() -> None:
    # After a DR switch the marker belongs to the switch playbook; a provision re-run must
    # never flip it back.
    marker = _by_name("DR role marker in the CIB")
    assert "--update" in marker["ansible.builtin.command"]["argv"]
    assert "not pdrbd_formed" in marker["when"]


def test_dr_shape_is_fixed_at_creation() -> None:
    """The DR role is a creation-time property (RDQM: crtmqm -rr); a formed QM never
    changes shape under live data — the stacked metadata would overwrite the fs tail."""
    guard = _by_name("the DR shape of a formed QM never changes")
    assert "pdrbd_dr_formed" in str(guard["ansible.builtin.assert"]["that"])
    assert "qm-destroy" in guard["ansible.builtin.assert"]["fail_msg"]
    probe = _by_name("already own the QM's DR DRBD resource")
    assert "drbd_dr_qm" in str(probe["ansible.builtin.command"])
    assert "105" in str(probe["failed_when"])
    names = [t["name"] for t, _ in _tasks()]
    assert names.index(guard["name"]) < next(i for i, n in enumerate(names) if _HA_CREATE_MD in n)


def test_dr_secondary_never_runs_first_time_formation() -> None:
    """Site B forms its OWN HA resource (RDQM: HA Promoted on b1) but never mkfs/crtmqm."""
    for frag in ("initial promote + mkfs", "create the queue manager"):
        assert "groups['pdrbd_a'][0]" in _guard(frag), frag
    for frag in ("HA initial promote", "stacked create-md"):
        guard = _guard(frag)
        assert "pdrbd_creator" in guard, frag  # once per SITE: site B runs it too
        assert "groups['pdrbd_a'][0]" not in guard, frag


def test_stacked_steps_follow_the_user_guide_sequence() -> None:
    # DRBD 9.0 User's Guide 5.20.3: lower up + Primary, then create-md/up/primary --stacked.
    names = [t["name"] for t, _ in _tasks()]
    order = [
        "HA initial promote",
        "stacked create-md",
        "stacked up",
        "wait for the DR peer to connect",
        "initial promote + mkfs",
    ]
    idx = [next(i for i, n in enumerate(names) if frag in n) for frag in order]
    assert idx == sorted(idx), dict(zip(order, idx, strict=True))
    mkfs = _by_name("initial promote + mkfs")["ansible.builtin.shell"]
    assert "drbdadm -S new-current-uuid --clear-bitmap {{ pdrbd_dr_res }}/0" in mkfs
    assert mkfs.index("drbdadm -S primary") < mkfs.index("mkfs.")
    assert "test -b {{ pdrbd_fs_device }}" in mkfs


def test_release_downs_the_stacked_resource_before_demoting_ha() -> None:
    # The lower device must stay Primary while the stacked one runs (UG 5.20.1).
    body = _by_name("release the volumes")["ansible.builtin.shell"]
    assert body.index("drbdadm -S secondary") < body.index("drbdadm -S down")
    assert body.index("drbdadm -S down") < body.index("drbdadm secondary {{ pdrbd_res }}")


def test_end_state_checks_follow_the_current_dr_role() -> None:
    # The DR primary runs the QM; the DR secondary shows RDQM's steady state
    # (rdqm-b1/pcmk-status.txt). Keyed on the marker, so a re-run after a switch holds.
    role = _by_name("this site's DR role")
    assert "--query" in role["ansible.builtin.command"]["argv"]
    wait = _by_name("wait for the QM to run under Pacemaker")
    assert "== '1'" in str(wait["when"])
    sec = _by_name("DR-secondary steady state")
    assert "== '2'" in str(sec["when"])
    for want in ("drbd_qm-clone", "drbd_dr_qm-clone", "mq_fs", "mq_vip", "mq_qm", "Stopped"):
        assert want in sec["until"], want


def test_defaults_mount_the_stacked_device_with_dr() -> None:
    d = _load(ROLE / "defaults" / "main.yml")
    assert d["pdrbd_fs_device"] == (
        "/dev/drbd/by-res/{{ pdrbd_dr_res if pdrbd_dr | bool else pdrbd_res }}/0"
    )


# --- qm-destroy -----------------------------------------------------------------------------


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


def test_qm_down_covers_both_sites_once_per_site() -> None:
    plays = _plays(QM_DOWN)
    both = "pdrbd_a{{ ':pdrbd_b' if (dr_enabled | default(true) | bool) else '' }}"
    assert all(p["hosts"] == both for p in plays)
    assert not [t["name"] for p in plays for t in p["tasks"] if "run_once" in t]
    [rsc] = [p for p in plays if p["name"].startswith("remove the Pacemaker resources")]
    for task in rsc["tasks"]:
        assert task["when"] == "inventory_hostname == groups[pdrbd_site_group][0]"
    body = rsc["tasks"][0]["ansible.builtin.shell"]
    assert "for r in mq_group drbd_dr_qm-clone drbd_qm-clone; do" in body
    assert "--delete" in rsc["tasks"][1]["ansible.builtin.shell"]


def test_qm_down_takes_the_stacked_resource_down_first() -> None:
    [vol] = [p for p in _plays(QM_DOWN) if p["name"].startswith("take the volumes down")]
    names = [t["name"] for t in vol["tasks"]]
    assert names.index("stacked DR resource down (absent is fine)") < names.index(
        "remove the DR resource file"
    )
    assert names.index("remove the DR resource file") < names.index(
        "drbd down + wipe-md (absent is fine)"
    )
    assert "drbdadm -S down {{ pdrbd_dr_res }}" in vol["tasks"][0]["ansible.builtin.shell"]
