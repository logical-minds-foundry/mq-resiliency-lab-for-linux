"""mqlab.e2e — `qm e2e` argument derivation (#1342).

The per-stack cases read the COMMITTED topology, provision playbooks and app-requester
role defaults, so a drift between what the always-on requester is deployed with and what
`mqlab qm e2e` sends fails here.
"""

from __future__ import annotations

import re
import shlex
from typing import TYPE_CHECKING

import pytest
import yaml

from mqlab import e2e, topology
from mqlab.paths import repo_root
from mqlab.stacks import QmConfig, Stack, lab_stacks

if TYPE_CHECKING:
    from pathlib import Path

_KEYREPO = "/home/vagrant/ssl/key"


def _real(stack_name: str) -> e2e.E2EParams:
    return e2e.derive(lab_stacks()[stack_name], repo=repo_root(), topo=topology.load())


@pytest.mark.parametrize(
    ("stack_name", "qm", "conn"),
    [
        (
            "nativeha-ubuntu",
            "NHAUAPP",
            "nha-ubuntu-a1-data-a.client.com(1414),nha-ubuntu-a2-data-a.client.com(1414),"
            "nha-ubuntu-a3-data-a.client.com(1414)",
        ),
        (
            "nativeha-rhel-crr",
            "NHARCAPP",
            "nha-rhel-crr-a1-data-a.client.com(1414),nha-rhel-crr-a2-data-a.client.com(1414),"
            "nha-rhel-crr-a3-data-a.client.com(1414)",
        ),
        ("pcmk-ubuntu", "PCMKAPP", "pcmk-vip-a.client.com(1414),pcmk-vip-b.client.com(1414)"),
        ("rdqm-rhel", "RDQMAPP", "rdqm-vip-a.client.com(1414)"),
    ],
)
def test_derive_per_stack_from_committed_sources(stack_name, qm, conn):
    params = _real(stack_name)
    assert params == e2e.E2EParams(
        host="app-client",
        qm=qm,
        conn=conn,
        channel="APP.SVRCONN",
        tls=True,
        keyrepo=_KEYREPO,
        certlabel="app-client",
    )


def test_role_tls_default_is_true():
    # derive() treats an unset app_tls as true, mirroring the role default; bind it.
    defaults = yaml.safe_load(repo_root().joinpath("ansible", *e2e.ROLE_DEFAULTS).read_text())
    assert defaults["app_requester_tls"] == "{{ app_tls | default(true) }}"


def test_requester_constants_match_the_committed_unit():
    unit = (
        repo_root() / "components/mq-resiliency-clients/systemd/mq-app-requester.service"
    ).read_text()
    assert f"Environment=LD_LIBRARY_PATH={e2e.MQ_LIB_PATH}\n" in unit
    assert f"ExecStart={e2e.REQUESTER_BIN} " in unit


def test_remote_command_with_tls():
    cmd = e2e.remote_command(_real("nativeha-ubuntu"), 20)
    env, *argv = shlex.split(cmd)
    assert env == "LD_LIBRARY_PATH=/opt/mqm/lib64"
    assert argv == [
        e2e.REQUESTER_BIN,
        "--qm",
        "NHAUAPP",
        "--conn",
        "nha-ubuntu-a1-data-a.client.com(1414),nha-ubuntu-a2-data-a.client.com(1414),"
        "nha-ubuntu-a3-data-a.client.com(1414)",
        "--channel",
        "APP.SVRCONN",
        "--count",
        "20",
        "--keyrepo",
        _KEYREPO,
        "--certlabel",
        "app-client",
    ]


def test_remote_command_plaintext_omits_tls_args():
    params = e2e.E2EParams("h", "Q", "c(1414)", "APP.SVRCONN", False, "/k", "l")
    assert shlex.split(e2e.remote_command(params, 3))[1:] == [
        e2e.REQUESTER_BIN,
        "--qm",
        "Q",
        "--conn",
        "c(1414)",
        "--channel",
        "APP.SVRCONN",
        "--count",
        "3",
    ]


# --- synthetic repos: the fail-loud paths ------------------------------------------------
_DEFAULTS = (
    "app_requester_channel: APP.SVRCONN\n"
    "app_requester_keyrepo: /home/vagrant/ssl/key\n"
    "app_requester_certlabel: app-client\n"
)
_TOPO = {"groups": {"app": ["app-client"]}}


def _stack(provision: str | None = "ansible/site.yml") -> Stack:
    return Stack(
        name="s",
        mechanism="native-ha",
        os_family="ubuntu",
        short="S",
        verbs={},
        cluster_group=None,
        groups=[],
        dr_groups=[],
        qm=QmConfig(name="SAPP", short="S"),
        provision=provision,
        secrets=[],
        alloc={},
    )


def _repo(tmp_path: Path, playbook: str, defaults: str = _DEFAULTS) -> Path:
    (tmp_path / "ansible" / "roles" / "app-requester" / "defaults").mkdir(parents=True)
    (tmp_path / "ansible" / "site.yml").write_text(playbook)
    (tmp_path / "ansible" / "roles" / "app-requester" / "defaults" / "main.yml").write_text(
        defaults
    )
    return tmp_path


def _import(vars_: str) -> str:
    return f"- import_playbook: site-distributed-shared.yml\n  vars:\n{vars_}"


def test_derive_explicit_tls_false(tmp_path):
    repo = _repo(tmp_path, _import("    app_conn: a(1414)\n    app_tls: false\n"))
    params = e2e.derive(_stack(), repo=repo, topo=_TOPO)
    assert (params.qm, params.conn, params.tls) == ("SAPP", "a(1414)", False)


@pytest.mark.parametrize(
    ("playbook", "defaults", "topo", "message"),
    [
        ("{}\n", _DEFAULTS, _TOPO, "is not a playbook"),
        ("- hosts: all\n", _DEFAULTS, _TOPO, "exactly once (found 0)"),
        (_import("    app_conn: a\n") * 2, _DEFAULTS, _TOPO, "exactly once (found 2)"),
        (
            "- import_playbook: site-distributed-shared.yml\n  vars: [x]\n",
            _DEFAULTS,
            _TOPO,
            "non-mapping vars",
        ),
        (_import("    app_tls: true\n"), _DEFAULTS, _TOPO, "app_conn in"),
        (_import("    app_conn: ''\n"), _DEFAULTS, _TOPO, "missing or not a string"),
        (_import("    app_conn: '{{ x }}'\n"), _DEFAULTS, _TOPO, "is templated"),
        (_import("    app_conn: a\n    app_tls: 'yes'\n"), _DEFAULTS, _TOPO, "must be a boolean"),
        (_import("    app_conn: a\n"), "- x\n", _TOPO, "is not a mapping"),
        (
            _import("    app_conn: a\n"),
            _DEFAULTS.replace("/home/vagrant/ssl/key", "'{% raw %}k'"),
            _TOPO,
            "app_requester_keyrepo",
        ),
        (_import("    app_conn: a\n"), _DEFAULTS, {"groups": {}}, "no hosts in the 'app' group"),
        (_import("    app_conn: a\n"), _DEFAULTS, {}, "no hosts in the 'app' group"),
    ],
)
def test_derive_fails_loud(tmp_path, playbook, defaults, topo, message):
    repo = _repo(tmp_path, playbook, defaults)
    with pytest.raises(e2e.E2EConfigError, match=re.escape(message)):
        e2e.derive(_stack(), repo=repo, topo=topo)


def test_derive_without_provision_playbook_fails(tmp_path):
    with pytest.raises(e2e.E2EConfigError, match="has no provision playbook"):
        e2e.derive(_stack(provision=None), repo=tmp_path, topo=_TOPO)


def test_derive_missing_playbook_file_fails(tmp_path):
    with pytest.raises(e2e.E2EConfigError, match="cannot read"):
        e2e.derive(_stack(), repo=tmp_path, topo=_TOPO)
