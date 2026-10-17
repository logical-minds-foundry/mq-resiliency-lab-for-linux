"""mqmon (mq_prometheus exporter) least-privilege grants (#936, #943, #1606).

The exporter's object-status poll runs PCF Inquire Topic Status, Inquire Channel and
Inquire Queue / Queue Status, and the command server checks dsp on every object they
touch (IBM "Authority checking for PCF commands in IBM MQ", SSYHRD_10.0.0). Without dsp
on topics, channels and client-connection channels, and on the command queue (whose
exact-name profile overrides the QUEUE ** grant), each poll was refused object by object
(#1606): ~11 AMQ8245W/AMQ8009W per second on the active instance, and those objects
were missing from the scrape.
These tests pin the profile, keep it display-only, and check the template renders it.
"""

from __future__ import annotations

from pathlib import Path
from typing import Any

import jinja2
import yaml

REPO_ROOT = Path(__file__).resolve().parent.parent
AUTHZ = REPO_ROOT / "ansible" / "group_vars" / "all" / "authz.yml"
TEMPLATE = REPO_ROOT / "ansible" / "roles" / "mq-pcmk-qmgr" / "templates" / "authz.mqsc.j2"


def _grants() -> list[dict[str, str]]:
    data: dict[str, Any] = yaml.safe_load(AUTHZ.read_text(encoding="utf-8"))
    grants: list[dict[str, str]] = data["authz_mqmon_grants"]
    return grants


def _by_key() -> dict[tuple[str, str], set[str]]:
    return {
        (g["objtype"], g.get("profile", "")): {a.strip() for a in g["authadd"].split(",")}
        for g in _grants()
    }


def test_grants_cover_the_exporters_pcf_inquiries() -> None:
    grants = _by_key()
    assert len(grants) == len(_grants()), "duplicate grant key"
    assert grants[("QMGR", "")] == {"CONNECT", "INQ", "DSP"}
    # $SYS/MQ resource publications (#936) and topic status on the subtree (#1606):
    # the exact-name profile overrides TOPIC **, so DSP must be named on it too.
    assert grants[("TOPIC", "SYSTEM.ADMIN.TOPIC")] == {"SUB", "DSP"}
    assert grants[("TOPIC", "**")] == {"DSP"}
    # Inquire Channel for defined-but-inactive channels (showInactiveChannels).
    assert grants[("CHANNEL", "**")] == {"DSP"}
    # CLNTCONN is a separate OAM object type that CHANNEL ** does not cover.
    assert grants[("CLNTCONN", "**")] == {"DSP"}
    # PCF requests go in by PUT; the exact-name profile overrides QUEUE **, so the
    # command queue's own DSP INQ must be here or it is never scraped.
    assert grants[("QUEUE", "SYSTEM.ADMIN.COMMAND.QUEUE")] == {"PUT", "DSP", "INQ"}
    assert grants[("QUEUE", "SYSTEM.DEFAULT.MODEL.QUEUE")] == {"DSP", "INQ", "GET"}
    # The #943 coverage gate: read-only on every queue.
    assert grants[("QUEUE", "**")] == {"DSP", "INQ"}


def test_grants_stay_least_privilege() -> None:
    # Monitoring never administers, publishes or controls anything (never mqm).
    forbidden = {
        "CHG", "CLR", "DLT", "CRT", "SET", "SETALL", "SETID", "ALTUSR",
        "CTRL", "CTRLX", "ALL", "ALLADM", "ALLMQI", "PASSALL", "PASSID", "PUB", "RESUME",
        "BROWSE",
    }  # fmt: skip
    for key, auths in _by_key().items():
        assert not auths & forbidden, key
    # PUT only on the command queue; GET only on the PCF reply model queue. So the N1
    # denial (mqmon MQPUT APP.REPLY -> 2035) holds under the QUEUE ** grant.
    assert [k for k, a in _by_key().items() if "PUT" in a] == [
        ("QUEUE", "SYSTEM.ADMIN.COMMAND.QUEUE")
    ]
    assert [k for k, a in _by_key().items() if "GET" in a] == [
        ("QUEUE", "SYSTEM.DEFAULT.MODEL.QUEUE")
    ]


def _render() -> str:
    # MQSC text, never HTML: the quotes in GROUP('mqmon') must not be escaped.
    env = jinja2.Environment(
        undefined=jinja2.StrictUndefined,
        trim_blocks=True,
        autoescape=jinja2.select_autoescape(
            enabled_extensions=(), default_for_string=False, default=False
        ),
    )
    return env.from_string(TEMPLATE.read_text(encoding="utf-8")).render(
        qm_name="QM1",
        mqsvc_dlq="DLQ",
        authz_chlauth_maps=[],
        authz_mqmon_grants=_grants(),
    )


def test_template_renders_one_set_authrec_per_grant() -> None:
    out = _render()
    lines = [ln for ln in out.splitlines() if "GROUP('mqmon')" in ln]
    assert len(lines) == len(_grants())
    assert all(ln.startswith("SET AUTHREC ") for ln in lines)
    for line in (
        "SET AUTHREC PROFILE('SYSTEM.ADMIN.TOPIC') OBJTYPE(TOPIC) GROUP('mqmon') AUTHADD(SUB, DSP)",
        "SET AUTHREC PROFILE('**') OBJTYPE(TOPIC) GROUP('mqmon') AUTHADD(DSP)",
        "SET AUTHREC PROFILE('**') OBJTYPE(CHANNEL) GROUP('mqmon') AUTHADD(DSP)",
        "SET AUTHREC PROFILE('**') OBJTYPE(CLNTCONN) GROUP('mqmon') AUTHADD(DSP)",
        "SET AUTHREC PROFILE('SYSTEM.ADMIN.COMMAND.QUEUE') OBJTYPE(QUEUE) GROUP('mqmon') "
        "AUTHADD(PUT, DSP, INQ)",
    ):
        assert line in out
