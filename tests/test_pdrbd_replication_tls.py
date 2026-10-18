"""DRBD kernel TLS on the pcmk-drbd-ubuntu arm (lab #1609, epic .github#310 Task 6b).

RDQM encrypts both of its DRBD resources with DRBD 9 kernel TLS: `tls yes;` in the HA
resource's `net` and in the DR resource's `connection { net }`
(docs/reference/rdqm-blueprint/rdqm-a1/drbd-conf.txt), with `tlshd` doing the handshake
from per-node certificates. Pinned statically here because the live proof needs the lab:

- the bake installs ktls-utils (tlshd) and leaves it inert; the per-run role fails loud on
  a box that predates #1609;
- the certificates come from the RDQM arm's #545 minting tasks, into a separate CA store,
  with the DR resource's logical peer name in every SAN (RDQM: `encrypted.remote`);
- tlshd's config matches the installed ktls-utils: 0.9 (Ubuntu 24.04) has no
  `x509.truststore` and reads `[main] debug`; 0.10+ reads `[debug] loglevel` and the
  truststore; 1.3.0+ (Ubuntu 26.04) reads /etc/tlshd/config, shadowed by /etc/tlshd.conf;
- both DRBD templates set `tls yes` where RDQM does;
- a re-provision over live plaintext connections fails before any config is rendered
  (the kernel refuses to switch TLS on a connected link);
- the verify play fails unless every connection is Connected with tls, and the kernel's
  kTLS socket counters cover them.
"""

from __future__ import annotations

import base64
import json
import re
from pathlib import Path
from typing import Any

import pytest
import yaml
from ansible.parsing.dataloader import DataLoader
from ansible.template import Templar, trust_as_template

REPO_ROOT = Path(__file__).resolve().parents[1]
ANSIBLE = REPO_ROOT / "ansible"
ROLE = ANSIBLE / "roles" / "pdrbd-replication-tls"
QM_ROLE = ANSIBLE / "roles" / "pdrbd-qm"
RDQM_TLS = ANSIBLE / "roles" / "rdqm-replication-tls"
DRBD9_INSTALL = ANSIBLE / "roles" / "drbd9" / "tasks" / "install.yml"
SITE = ANSIBLE / "site-pdrbd.yml"
VERIFY = ANSIBLE / "site-pdrbd-tls-verify.yml"
BLUEPRINT = REPO_ROOT / "docs" / "reference" / "rdqm-blueprint" / "rdqm-a1"
LEDGER = REPO_ROOT / "docs" / "reference" / "rdqm-vs-vanilla-deviations.md"


def _load(path: Path) -> Any:
    return yaml.safe_load(path.read_text(encoding="utf-8"))


def _flat(tasks: list[dict[str, Any]]) -> list[dict[str, Any]]:
    out: list[dict[str, Any]] = []
    for t in tasks:
        out.append(t)
        out.extend(_flat(t.get("block", [])))
    return out


def _task(tasks: list[dict[str, Any]], prefix: str) -> dict[str, Any]:
    (task,) = [t for t in _flat(tasks) if t.get("name", "").startswith(prefix)]
    return task


def _role_tasks() -> list[dict[str, Any]]:
    return list(_load(ROLE / "tasks" / "main.yml"))


def _defaults() -> dict[str, Any]:
    return dict(_load(ROLE / "defaults" / "main.yml"))


def _template(expr: str, variables: dict[str, Any]) -> Any:
    templar = Templar(loader=DataLoader(), variables=variables)
    return templar.template(trust_as_template(expr.strip()))


def _cond(expr: str, variables: dict[str, Any]) -> Any:
    """A bare Ansible conditional (until/that/when), evaluated through the real templar."""
    return _template("{{ " + expr.strip() + " }}", variables)


def _conn(name: str, state: str = "Connected", tls: bool = True) -> dict[str, Any]:
    return {"peer-node-id": 1, "name": name, "connection-state": state, "tls": tls}


def _status_json(name: str, conns: list[dict[str, Any]]) -> str:
    """`drbdsetup status --json <res>` as drbd-utils 9.35 prints it (one resource)."""
    return json.dumps([{"name": name, "role": "Secondary", "devices": [], "connections": conns}])


# --- bake: ktls-utils (tlshd) in the pdrbd box, inert ---------------------------------------


def test_bake_installs_ktls_utils_after_drbd_and_leaves_tlshd_inert() -> None:
    tasks = _load(DRBD9_INSTALL)
    module = _task(tasks, "DRBD 9 userland + DKMS module")
    ktls = _task(tasks, "tlshd (ktls-utils)")
    assert ktls["ansible.builtin.apt"]["name"] == ["ktls-utils"]
    assert ktls["ansible.builtin.apt"]["state"] == "present"
    inert = _task(tasks, "tlshd inert in the image")
    unit = inert["ansible.builtin.systemd"]
    assert (unit["name"], unit["enabled"], unit["state"]) == ("tlshd", False, "stopped")
    assert tasks.index(module) < tasks.index(ktls) < tasks.index(inert)


def test_per_run_role_fails_loud_on_a_box_without_tlshd() -> None:
    tasks = _role_tasks()
    check = _task(tasks, "assert ktls-utils (tlshd) is baked")
    assert check["ansible.builtin.assert"]["that"] == "'ktls-utils' in ansible_facts.packages"
    msg = check["ansible.builtin.assert"]["fail_msg"]
    assert "mqlab box build pdrbd-ubuntu" in msg
    # never an apt install at run time: the box is the install path (#642/#659)
    assert "ansible.builtin.apt" not in (ROLE / "tasks" / "main.yml").read_text(encoding="utf-8")


# --- tlshd version decides the config's format and path -------------------------------------


@pytest.mark.parametrize(
    ("deb_version", "legacy", "conf"),
    [
        ("0.9-2build2", True, "/etc/tlshd.conf"),  # Ubuntu 24.04 (noble)
        ("0.11-1", False, "/etc/tlshd.conf"),  # 25.04 (plucky): truststore, old path
        ("1.0.0-1", False, "/etc/tlshd.conf"),  # 25.10 (questing)
        ("1.3.0-2", False, "/etc/tlshd/config"),  # Ubuntu 26.04 (resolute)
        ("1.4.0-1", False, "/etc/tlshd/config"),
    ],
)
def test_tlshd_flavour_follows_the_installed_ktls_utils(
    deb_version: str, legacy: bool, conf: str
) -> None:
    version = _task(_role_tasks(), "tlshd version")["ansible.builtin.set_fact"]
    facts = _task(_role_tasks(), "tlshd flavour")["ansible.builtin.set_fact"]
    variables: dict[str, Any] = {
        "ansible_facts": {"packages": {"ktls-utils": [{"version": deb_version}]}}
    }
    variables["pdrbd_tlshd_version"] = _template(version["pdrbd_tlshd_version"], variables)
    assert _template(facts["pdrbd_tlshd_legacy"], variables) is legacy
    assert _template(facts["pdrbd_tlshd_conf"], variables) == conf


def _render_tlshd(legacy: bool) -> str:
    """Through Ansible's own templar, as the template module renders it."""
    d = _defaults()
    variables = {
        "ansible_managed": "managed",
        "inventory_hostname": "pdrbd-a1",
        "pdrbd_tlshd_legacy": legacy,
        "pdrbd_repl_drbd_dir": d["pdrbd_repl_drbd_dir"],
        "pdrbd_repl_ca_file": d["pdrbd_repl_ca_file"],
        "pdrbd_repl_tlshd_loglevel": d["pdrbd_repl_tlshd_loglevel"],
    }
    text = (ROLE / "templates" / "tlshd.conf.j2").read_text(encoding="utf-8")
    return str(_template(text, variables))


def _sections(text: str) -> dict[str, dict[str, str]]:
    out: dict[str, dict[str, str]] = {}
    current = ""
    for raw in text.splitlines():
        line = raw.strip()
        if not line or line.startswith("#"):
            continue
        if line.startswith("["):
            current = line.strip("[]")
            out[current] = {}
        else:
            key, _, value = line.partition("=")
            out[current][key.strip()] = value.strip()
    return out


def test_tlshd_config_modern_matches_rdqm_layout() -> None:
    """ktls-utils >= 0.10: the RDQM arm's layout (rdqm-replication-tls tlshd.conf.j2)."""
    s = _sections(_render_tlshd(legacy=False))
    assert s["debug"]["loglevel"] == "1"  # #558: never silent
    for side in ("authenticate.client", "authenticate.server"):
        assert s[side] == {
            "x509.truststore": "/etc/drbd.d/pdrbd-repl-ca.crt",
            "x509.certificate": "/etc/drbd.d/pdrbd-a1.crt",
            "x509.private_key": "/etc/drbd.d/pdrbd-a1.key",
        }
    assert "main" not in s


def test_tlshd_config_legacy_uses_ktls_utils_0_9_keys() -> None:
    """ktls-utils 0.9 reads [main] debug and has no x509.truststore (system trust only)."""
    s = _sections(_render_tlshd(legacy=True))
    assert s["main"]["debug"] == "1"
    for side in ("authenticate.client", "authenticate.server"):
        assert s[side] == {
            "x509.certificate": "/etc/drbd.d/pdrbd-a1.crt",
            "x509.private_key": "/etc/drbd.d/pdrbd-a1.key",
        }
    assert "debug" not in s


def test_legacy_tlshd_trusts_the_ca_through_the_system_store_only_when_needed() -> None:
    tasks = _role_tasks()
    add = _task(tasks, "replication CA in the system trust store (ktls-utils 0.9 only")
    assert add["when"] == "pdrbd_tlshd_legacy | bool"
    assert add["ansible.builtin.copy"]["dest"] == "{{ pdrbd_repl_system_ca }}"
    drop = _task(tasks, "replication CA NOT in the system trust store")
    assert drop["when"] == "not (pdrbd_tlshd_legacy | bool)"
    assert drop["ansible.builtin.file"]["state"] == "absent"
    refresh_name = "refresh the system trust bundle (pdrbd)"
    assert add["notify"] == drop["notify"] == refresh_name
    refresh, restart = _load(ROLE / "handlers" / "main.yml")
    assert refresh["name"] == refresh_name
    assert refresh["ansible.builtin.command"]["argv"] == ["update-ca-certificates"]
    assert refresh["notify"] == restart["name"]  # listed first, so it runs first
    assert _defaults()["pdrbd_repl_system_ca"].startswith("/usr/local/share/ca-certificates/")


def test_new_path_tlshd_is_never_shadowed_by_a_legacy_conf() -> None:
    """ktls-utils 1.3.0 reads /etc/tlshd.conf FIRST when it exists (src/tlshd/main.c)."""
    task = _task(_role_tasks(), "no legacy /etc/tlshd.conf")
    assert task["ansible.builtin.file"] == {"path": "/etc/tlshd.conf", "state": "absent"}
    assert task["when"] == "pdrbd_tlshd_conf != '/etc/tlshd.conf'"
    render = _task(_role_tasks(), "render tlshd's config")
    assert render["ansible.builtin.template"]["dest"] == "{{ pdrbd_tlshd_conf }}"


# --- certificates: the #545 minting, a separate CA, the DR peer name in the SAN -------------


def test_certs_reuse_the_rdqm_arms_minting_into_a_separate_store() -> None:
    inc = _task(_role_tasks(), "mint the replication CA + this node's cert")
    role = inc["ansible.builtin.include_role"]
    assert role == {"name": "rdqm-replication-tls", "tasks_from": "certs.yml"}
    v = inc["vars"]
    assert v["rdqm_repl_ca_dir"] == "{{ pdrbd_repl_ca_dir }}"
    assert v["rdqm_repl_group_dns"] == "{{ pdrbd_repl_group_dns }}"
    assert v["rdqm_repl_ca_common_name"] == "{{ pdrbd_repl_ca_common_name }}"
    d = _defaults()
    assert d["pdrbd_repl_ca_dir"] == "{{ mqlab_state_dir }}/secrets/pki/pdrbd-repl"
    rdqm = _load(RDQM_TLS / "defaults" / "main.yml")
    assert d["pdrbd_repl_ca_dir"] != rdqm["rdqm_repl_ca_dir"]


def test_rdqm_ca_name_is_unchanged_by_the_parameterisation() -> None:
    rdqm = _load(RDQM_TLS / "defaults" / "main.yml")
    assert rdqm["rdqm_repl_ca_common_name"] == "RDQM Replication CA"
    certs = _load(RDQM_TLS / "tasks" / "certs.yml")
    csr = _task(certs, "replication CA CSR")["community.crypto.openssl_csr"]
    assert csr["common_name"] == "{{ rdqm_repl_ca_common_name }}"


def test_every_cert_san_carries_the_dr_peer_name_rdqm_uses() -> None:
    """DRBD hands tlshd the peer's configured name as the name to verify
    (drbd_transport_tcp.c: peername = net_conf name), so every node's certificate must
    carry the DR resource's logical peer name, as RDQM's carry `encrypted.remote`."""
    qm = _load(QM_ROLE / "defaults" / "main.yml")
    assert qm["pdrbd_dr_peer_name"] == "encrypted.remote"
    assert _defaults()["pdrbd_repl_group_dns"] == qm["pdrbd_dr_peer_name"]
    dump = (BLUEPRINT / "drbd-conf.txt").read_text(encoding="utf-8")
    assert "on encrypted.remote {" in dump


def test_private_key_is_root_only_and_never_logged() -> None:
    key = _task(_role_tasks(), "deploy this node's replication private key")
    assert key["ansible.builtin.copy"]["mode"] == "0600"
    assert key["ansible.builtin.copy"]["owner"] == "root"
    assert key["no_log"] is True


def test_no_key_material_in_the_repo_role() -> None:
    for path in ROLE.rglob("*"):
        if path.is_file():
            text = path.read_text(encoding="utf-8")
            assert "BEGIN" not in text or "PRIVATE KEY" not in text, path


# --- kernel prerequisites + tlshd running ---------------------------------------------------


def test_kernel_handshake_upcall_and_ktls_are_proven() -> None:
    tasks = _role_tasks()
    hs = _task(tasks, "the kernel's TLS handshake upcall")
    assert "genl ctrl get name handshake" in hs["ansible.builtin.shell"]
    assert "exit 1" in hs["ansible.builtin.shell"]
    boot = _task(tasks, "load kTLS at every boot")
    assert boot["ansible.builtin.copy"]["dest"] == "/etc/modules-load.d/pdrbd-tls.conf"
    assert boot["ansible.builtin.copy"]["content"].strip() == "tls"
    load = _task(tasks, "load kTLS now")
    assert "modprobe tls" in load["ansible.builtin.shell"]
    proof = _task(tasks, "assert kTLS is live")
    assert "/proc/net/tls_stat" in str(proof)


def test_tlshd_enabled_started_and_proven_running() -> None:
    tasks = _role_tasks()
    start = _task(tasks, "enable + start tlshd")
    svc = start["ansible.builtin.systemd"]
    assert (svc["name"], svc["enabled"], svc["state"]) == ("tlshd", True, "started")
    flush = _task(tasks, "apply a changed config")
    assert flush["ansible.builtin.meta"] == "flush_handlers"
    alive = _task(tasks, "tlshd stayed up")
    assert "ActiveState" in alive["ansible.builtin.shell"]
    names = [t["name"] for t in _flat(tasks)]
    assert names.index(start["name"]) < names.index(flush["name"]) < names.index(alive["name"])


def test_tlshd_restart_handler_retries_only_the_dbus_race() -> None:
    handler = _load(ROLE / "handlers" / "main.yml")[-1]
    assert handler["name"] == "restart tlshd (pdrbd)"
    assert handler["ansible.builtin.systemd"]["state"] == "restarted"
    assert "Service is in unknown state" in handler["until"]


def test_role_never_masks_a_failure() -> None:
    for path in (*ROLE.rglob("*.yml"), VERIFY):
        text = path.read_text(encoding="utf-8")
        assert "|| true" not in text, path
        assert "failed_when: false" not in text, path
        assert "ignore_errors" not in text, path


# --- DRBD config: tls yes where RDQM has it -------------------------------------------------


def _block(text: str, header: str) -> str:
    """The body of the first `header {` block, braces balanced."""
    start = text.index(header + " {") + len(header) + 2
    depth, i = 1, start
    while depth:
        depth += {"{": 1, "}": -1}.get(text[i], 0)
        i += 1
    return text[start : i - 1]


def test_blueprint_has_tls_in_both_resources() -> None:
    conf = (BLUEPRINT / "drbd-conf.txt").read_text(encoding="utf-8")
    ha = conf[conf.index("resource rdqmapp {") :]
    dr = conf[conf.index("resource rdqmapp.dr {") :]
    assert "tls yes;" in _block(ha, "    net")
    assert "tls yes;" in _block(_block(dr, "    connection"), "        net")


def test_ha_template_sets_tls_in_its_net_section() -> None:
    text = (QM_ROLE / "templates" / "qm.res.j2").read_text(encoding="utf-8")
    body = text[text.index("resource {{ pdrbd_res }} {") :]
    assert "tls yes;" in _block(body, "    net")


def test_dr_template_sets_tls_in_the_connection_net() -> None:
    text = (QM_ROLE / "templates" / "qm.dr.res.j2").read_text(encoding="utf-8")
    body = text[text.index("resource {{ pdrbd_dr_res }} {") :]
    net = _block(_block(body, "    connection"), "        net")
    assert "tls yes;" in net
    assert text.count("tls yes;") == 1


# --- re-provision over live plaintext connections -------------------------------------------


def _qm_tasks() -> list[dict[str, Any]]:
    return _flat(_load(QM_ROLE / "tasks" / "main.yml"))


def test_plaintext_preflight_runs_before_any_drbd_config_is_rendered() -> None:
    names = [t.get("name", "") for t in _qm_tasks()]
    probe = names.index(_task(_qm_tasks(), "probe — live DRBD connections")["name"])
    gate = names.index(_task(_qm_tasks(), "TLS cannot be switched on")["name"])
    first_render = min(i for i, t in enumerate(_qm_tasks()) if "ansible.builtin.template" in t)
    assert probe < gate < first_render


def test_plaintext_probe_separates_absent_from_error() -> None:
    probe = _task(_qm_tasks(), "probe — live DRBD connections")
    assert probe["ansible.builtin.command"]["argv"] == [
        "drbdsetup", "status", "--json", "{{ item }}",
    ]  # fmt: skip
    assert probe["failed_when"] == "pdrbd_tls_pre.rc not in [0, 10]"
    loop = probe["loop"]
    dr = {"pdrbd_res": "x", "pdrbd_dr_res": "x.dr", "pdrbd_dr": True}
    assert _template(loop, dr) == ["x", "x.dr"]
    assert _template(loop, {**dr, "pdrbd_dr": False}) == ["x"]


def _plaintext(results: list[dict[str, Any]]) -> Any:
    gate = _task(_qm_tasks(), "TLS cannot be switched on")
    expr = gate["vars"]["pdrbd_plaintext_live"]
    return list(_template(expr, {"pdrbd_tls_pre": {"results": results}}))


def test_plaintext_gate_names_only_connected_plaintext_links() -> None:
    up = _status_json("pdrbdapp", [_conn("pdrbd-a2", tls=False), _conn("pdrbd-a3")])
    dr = _status_json("pdrbdapp.dr", [_conn("encrypted.remote", "Connecting", tls=False)])
    absent = {"rc": 10, "stdout": ""}
    assert _plaintext([{"rc": 0, "stdout": up}, {"rc": 0, "stdout": dr}]) == ["pdrbd-a2"]
    assert _plaintext([{"rc": 0, "stdout": _status_json("x", [_conn("pdrbd-a2")])}]) == []
    assert _plaintext([absent, absent]) == []  # cold build: nothing is up yet


def test_plaintext_gate_message_names_the_kernel_refusal_and_the_fix() -> None:
    gate = _task(_qm_tasks(), "TLS cannot be switched on")
    a = gate["ansible.builtin.assert"]
    assert a["that"] == "pdrbd_plaintext_live | length == 0"
    assert "cannot switch tls" in a["fail_msg"]
    assert "mqlab teardown pcmk-drbd-ubuntu" in a["fail_msg"]


# --- site wiring ----------------------------------------------------------------------------


def _plays(path: Path) -> list[dict[str, Any]]:
    return [p for p in _load(path) if isinstance(p, dict)]


def _roles(play: dict[str, Any]) -> list[str]:
    return [r if isinstance(r, str) else r["role"] for r in play.get("roles") or []]


def test_tls_is_up_on_both_sites_before_any_resource_and_verified_after_sync() -> None:
    plays = _plays(SITE)
    labels = [p.get("import_playbook") or ",".join(_roles(p)) or p.get("hosts") for p in plays]
    tls = labels.index("pdrbd-replication-tls")
    assert labels.index("_pdrbd-dr-replication.yml") < tls < labels.index("pdrbd-qm")
    sync = labels.index("pdrbd_a")  # the watched DR-sync wait
    assert labels.index("site-pdrbd-tls-verify.yml") == sync + 1
    both = "pdrbd_a{{ ':pdrbd_b' if (dr_enabled | default(true) | bool) else '' }}"
    assert plays[tls]["hosts"] == both


# --- the TLS proof --------------------------------------------------------------------------


def _verify() -> dict[str, Any]:
    (play,) = _plays(VERIFY)
    return play


def _vtask(prefix: str) -> dict[str, Any]:
    return _task(_verify()["tasks"], prefix)


def test_verify_covers_both_sites_and_takes_the_qm_name() -> None:
    play = _verify()
    assert play["hosts"] == "pdrbd_a{{ ':pdrbd_b' if (dr_enabled | default(true) | bool) else '' }}"
    assert play["vars"]["pdrbd_res"] == "{{ qm_app | lower }}"
    assert play["vars"]["pdrbd_dr_res"] == "{{ pdrbd_res }}.dr"


HA_UNTIL_CASES = [
    ([_conn("pdrbd-a2"), _conn("pdrbd-a3")], 0, True),
    ([_conn("pdrbd-a2"), _conn("pdrbd-a3", tls=False)], 0, False),  # one plaintext peer
    ([_conn("pdrbd-a2"), _conn("pdrbd-a3", "Connecting")], 0, False),  # not up yet
    ([_conn("pdrbd-a2")], 0, False),  # a peer missing from the mesh
    ([], 10, False),  # the HA resource is not up on this node
]


@pytest.mark.parametrize(("conns", "rc", "ok"), HA_UNTIL_CASES)
def test_verify_ha_requires_every_mesh_peer_connected_over_tls(
    conns: list[dict[str, Any]], rc: int, ok: bool
) -> None:
    task = _vtask("HA resource: every site peer Connected over TLS")
    reg = {"rc": rc, "stdout": _status_json("pdrbdapp", conns) if rc == 0 else ""}
    assert _cond(task["until"], {"pdrbd_tls_ha": reg, "pdrbd_ha_peer_count": 2}) is ok


@pytest.mark.parametrize(
    ("conns", "rc", "ok"),
    [
        ([_conn("encrypted.remote")], 0, True),
        ([_conn("encrypted.remote", tls=False)], 0, False),
        ([_conn("encrypted.remote", "Connecting")], 0, False),
        ([], 10, True),  # not the node holding the DR resource
    ],
)
def test_verify_dr_requires_its_one_peer_connected_over_tls(
    conns: list[dict[str, Any]], rc: int, ok: bool
) -> None:
    task = _vtask("DR resource: its peer Connected over TLS")
    assert task["failed_when"] == "pdrbd_tls_dr.rc not in [0, 10]"
    reg = {"rc": rc, "stdout": _status_json("pdrbdapp.dr", conns) if rc == 0 else ""}
    assert _cond(task["until"], {"pdrbd_tls_dr": reg}) is ok


def test_verify_one_dr_holder_per_site() -> None:
    task = _vtask("exactly one node per site holds the DR resource")
    assert task["run_once"] is True
    that = task["ansible.builtin.assert"]["that"]
    hv = {
        "pdrbd-a1": {"pdrbd_tls_dr": {"rc": 0}},
        "pdrbd-a2": {"pdrbd_tls_dr": {"rc": 10}},
        "pdrbd-b1": {"pdrbd_tls_dr": {"rc": 10}},
        "pdrbd-b2": {"pdrbd_tls_dr": {"rc": 0}},
    }
    groups = {"pdrbd_a": ["pdrbd-a1", "pdrbd-a2"], "pdrbd_b": ["pdrbd-b1", "pdrbd-b2"]}
    variables = {"hostvars": hv, "groups": groups, "pdrbd_tls_sites": ["pdrbd_a", "pdrbd_b"]}
    assert _cond(that, variables) is True
    hv["pdrbd-a2"]["pdrbd_tls_dr"]["rc"] = 0  # two holders at site A
    assert _cond(that, variables) is False


TLS_STAT = """TlsCurrTxSw                     \t6
TlsCurrRxSw                     \t6
TlsCurrTxDevice                 \t0
TlsCurrRxDevice                 \t0
TlsTxSw                         \t6
TlsRxSw                         \t6
TlsTxDevice                     \t0
TlsRxDevice                     \t0
TlsDecryptError                 \t0
"""


def _ktls(stat: str, dr_rc: int) -> tuple[Any, Any, Any]:
    facts = _vtask("kTLS socket counts")["ansible.builtin.set_fact"]
    variables: dict[str, Any] = {
        "pdrbd_tls_stat": {"content": base64.b64encode(stat.encode()).decode()},
        "pdrbd_tls_dr": {"rc": dr_rc},
        "pdrbd_ha_peer_count": 2,
        "pdrbd_dr": True,
    }
    for key in ("pdrbd_ktls_tx", "pdrbd_ktls_rx", "pdrbd_tls_conns"):
        variables[key] = _template(facts[key], variables)
    that = _vtask("kTLS sockets cover every TLS connection")["ansible.builtin.assert"]["that"]
    ok = _cond(" and ".join(f"({c})" for c in that), variables)
    return variables["pdrbd_ktls_tx"], variables["pdrbd_tls_conns"], ok


def test_verify_ktls_counters_cover_two_sockets_per_tls_connection() -> None:
    # DR holder: 2 HA + 1 DR connections, each a control + a data socket = 6.
    assert _ktls(TLS_STAT, 0) == (6, 3, True)
    # A node with 6 sockets but one fewer connection still passes (>=).
    assert _ktls(TLS_STAT, 10)[2] is True
    fewer = re.sub(r"TlsCurrTxSw( +)\t6", r"TlsCurrTxSw\1\t5", TLS_STAT)
    assert _ktls(fewer, 0)[2] is False  # one socket of three connections is not kTLS


def test_verify_reads_tls_stat_without_masking_its_absence() -> None:
    slurp = _vtask("read the kernel's kTLS counters")
    assert slurp["ansible.builtin.slurp"]["src"] == "/proc/net/tls_stat"
    assert "failed_when" not in slurp


# --- ledger --------------------------------------------------------------------------------


def _ledger_row(row_id: str) -> list[str]:
    for line in LEDGER.read_text(encoding="utf-8").splitlines():
        if line.startswith(f"| {row_id} |"):
            return [c.strip() for c in line.strip("|").split("|")]
    raise AssertionError(row_id)


def test_ledger_sc1_and_rp5_reflect_the_build() -> None:
    sc1 = _ledger_row("SC-1")
    assert sc1[4] == "partial"
    assert "#1609" in sc1[3]
    assert "tls yes" in sc1[3]
    rp5 = _ledger_row("RP-5")
    assert rp5[4] == "exact"
    assert "encrypted.remote" in rp5[3]
    assert "dr-remote" not in LEDGER.read_text(encoding="utf-8")
