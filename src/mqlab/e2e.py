"""`mqlab qm e2e` — a bounded request/reply burst through a stack's distributed flow (#1342).

app-client -> APP.SVRCONN -> <short>APP -> SDR/RCVR -> SVCQM -> mq-svc-responder -> reply.

Every argument is derived from the same sources the always-on requester is deployed
from, never re-typed here:

* QM       — the stack's app QM, ``<short>APP`` (stacks.QmConfig, #351).
* CONN     — the ``app_conn`` the stack's provision playbook passes to
             ``site-distributed-shared.yml`` (which deploys the app-requester role):
             the VIP FQDNs on the VIP arms, the site-A instance list on Native HA.
* TLS      — that import's ``app_tls`` (the role defaults it to true when unset).
* keyrepo / certlabel / channel — the app-requester role defaults
             (``app_requester_keyrepo`` etc.), so a keystore move updates both at once.
* client host — the topology's ``app`` group.

This module is pure (file reads only); cli.qm_e2e runs the result over vagrant ssh.
"""

from __future__ import annotations

import shlex
from dataclasses import dataclass
from typing import TYPE_CHECKING, Any

import yaml

if TYPE_CHECKING:
    from pathlib import Path

    from mqlab.stacks import Stack

# The playbook every stack's provision imports to deploy the app host (incl. the
# app-requester role); its import `vars:` carry the stack's app_conn / app_tls.
SHARED_PLAYBOOK = "site-distributed-shared.yml"
ROLE_DEFAULTS = ("roles", "app-requester", "defaults", "main.yml")
# The topology group holding the app client host.
APP_GROUP = "app"
# Mirrors components/mq-resiliency-clients/systemd/mq-app-requester.service (its
# Environment= and ExecStart= binary); a test binds these to the committed unit.
REQUESTER_BIN = "/opt/logical-minds-foundry/mq-resiliency-clients/venv/bin/mq-app-requester"
MQ_LIB_PATH = "/opt/mqm/lib64"


class E2EConfigError(ValueError):
    """The lab's playbooks/role/topology do not yield a usable e2e configuration."""


@dataclass(frozen=True)
class E2EParams:
    """Everything one burst needs: where to run it and what to pass the requester."""

    host: str
    qm: str
    conn: str
    channel: str
    tls: bool
    keyrepo: str
    certlabel: str


def _load_yaml(path: Path) -> Any:
    try:
        return yaml.safe_load(path.read_text())
    except OSError as exc:
        raise E2EConfigError(f"cannot read {path}: {exc}") from exc


def _literal(value: object, what: str, source: Path) -> str:
    """A non-empty, non-templated string — fail loud on anything else, since a Jinja
    expression here could only be resolved by Ansible, not by this verb."""
    if not isinstance(value, str) or not value.strip():
        raise E2EConfigError(f"{what} in {source} is missing or not a string")
    if "{{" in value or "{%" in value:
        raise E2EConfigError(f"{what} in {source} is templated ({value!r}); expected a literal")
    return value.strip()


def _shared_import_vars(playbook: Path) -> dict[str, Any]:
    """The `vars:` of the provision playbook's single site-distributed-shared import."""
    plays = _load_yaml(playbook)
    if not isinstance(plays, list):
        raise E2EConfigError(f"{playbook} is not a playbook (expected a list of plays)")
    imports = [
        p for p in plays if isinstance(p, dict) and p.get("import_playbook") == SHARED_PLAYBOOK
    ]
    if len(imports) != 1:
        raise E2EConfigError(
            f"{playbook} must import {SHARED_PLAYBOOK} exactly once (found {len(imports)})"
        )
    found = imports[0].get("vars") or {}
    if not isinstance(found, dict):
        raise E2EConfigError(f"the {SHARED_PLAYBOOK} import in {playbook} has non-mapping vars")
    return found


def _tls(value: object, playbook: Path) -> bool:
    # Unset -> the role's `app_tls | default(true)`; otherwise a YAML boolean only.
    if value is None:
        return True
    if not isinstance(value, bool):
        raise E2EConfigError(f"app_tls in {playbook} must be a boolean, got {value!r}")
    return value


def derive(stack: Stack, *, repo: Path, topo: dict[str, Any]) -> E2EParams:
    """The e2e burst parameters for `stack`, read from the repo at `repo` and the
    effective topology `topo`. Raises E2EConfigError naming the offending file."""
    if not stack.provision:
        raise E2EConfigError(f"stack {stack.name} has no provision playbook")
    playbook = repo / stack.provision
    found = _shared_import_vars(playbook)
    conn = _literal(found.get("app_conn"), "app_conn", playbook)
    tls = _tls(found.get("app_tls"), playbook)

    defaults_path = repo.joinpath("ansible", *ROLE_DEFAULTS)
    defaults = _load_yaml(defaults_path)
    if not isinstance(defaults, dict):
        raise E2EConfigError(f"{defaults_path} is not a mapping")
    channel, keyrepo, certlabel = (
        _literal(defaults.get(key), key, defaults_path)
        for key in ("app_requester_channel", "app_requester_keyrepo", "app_requester_certlabel")
    )

    hosts = (topo.get("groups") or {}).get(APP_GROUP) or []
    if not hosts:
        raise E2EConfigError(f"topology declares no hosts in the {APP_GROUP!r} group")
    return E2EParams(
        host=str(hosts[0]),
        qm=stack.qm.qm_app,
        conn=conn,
        channel=channel,
        tls=tls,
        keyrepo=keyrepo,
        certlabel=certlabel,
    )


def remote_command(params: E2EParams, count: int) -> str:
    """The shell line run on the app host: a `--count`-bounded mq-app-requester, which
    exits 1 unless every request round-trips."""
    argv = [
        REQUESTER_BIN,
        "--qm",
        params.qm,
        "--conn",
        params.conn,
        "--channel",
        params.channel,
        "--count",
        str(count),
    ]
    if params.tls:
        argv += ["--keyrepo", params.keyrepo, "--certlabel", params.certlabel]
    return f"LD_LIBRARY_PATH={MQ_LIB_PATH} " + shlex.join(argv)
