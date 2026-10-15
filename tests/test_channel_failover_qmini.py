"""Guard: SVCQM's qm.ini lets a failed-over sender adopt its stale receiver (#1557).

After an unplanned HA failover of an app QM (a hung node sends no TCP reset), the new
active's sender reaches SVCQM from another address while the old receiver instance still
looks alive. With MQ's defaults (AdoptNewMCA=NO, AdoptNewMCACheck=NAME,ADDRESS,QM) it was
refused with AMQ9514E for the 360 s receive timeout. This pins the ``mq-qmgr`` settings,
that ADDRESS stays out of the check, how they are written (one line each, in the stanza
crtmqm writes), and that the QM restarts when they change, since MQ reads qm.ini only at
start.
"""

from __future__ import annotations

import re
from pathlib import Path
from typing import Any

import jinja2
import yaml

ROLE = Path(__file__).resolve().parents[1] / "ansible" / "roles" / "mq-qmgr"

# The Channels and TCP stanzas as crtmqm writes them on MQ 10.0.1 (svc-sim, #1557 run).
QMINI = """\
Channels:
   ChlauthEarlyAdopt=Yes
   ChlauthIgnoreUserCase=No
TCP:
   SndBuffSize=0
   SecureCommsOnly=NO
"""


def _settings() -> list[dict[str, str]]:
    defaults = yaml.safe_load((ROLE / "defaults" / "main.yml").read_text())
    return defaults["mq_qmgr_qmini_settings"]


def _tasks() -> list[dict[str, Any]]:
    return yaml.safe_load((ROLE / "tasks" / "main.yml").read_text())


def _index(name_part: str) -> int:
    hits = [i for i, t in enumerate(_tasks()) if name_part in t["name"]]
    assert len(hits) == 1, f"expected one task named like {name_part!r}"
    return hits[0]


def _render(template: str, item: dict[str, str]) -> str:
    # Escaping cannot alter these plain identifiers (no &<>"'), and keeps CodeQL's
    # Jinja2WithoutEscaping rule satisfied.
    env = jinja2.Environment(autoescape=True)
    return env.from_string(template).render(item=item)


def test_the_settings_are_ibms_with_address_dropped() -> None:
    settings = {(s["stanza"], s["key"]): s["value"] for s in _settings()}
    assert settings == {
        ("Channels", "AdoptNewMCA"): "ALL",
        ("Channels", "AdoptNewMCACheck"): "NAME,QM",
        ("TCP", "KeepAlive"): "Yes",
    }
    assert "ADDRESS" not in settings[("Channels", "AdoptNewMCACheck")]


def test_each_regexp_matches_only_its_own_key() -> None:
    edit = _tasks()[_index("channel failover settings")]["ansible.builtin.lineinfile"]
    lines = [f"   {s['key']}={s['value']}" for s in _settings()]
    for setting in _settings():
        pattern = re.compile(_render(edit["regexp"], setting))
        own = f"   {setting['key']}={setting['value']}"
        assert [ln for ln in lines if pattern.search(ln)] == [own]


def test_each_setting_lands_in_its_stanza() -> None:
    edit = _tasks()[_index("channel failover settings")]["ansible.builtin.lineinfile"]
    for setting in _settings():
        anchor = re.compile(_render(edit["insertafter"], setting), re.M)
        assert len(anchor.findall(QMINI)) == 1, setting
    assert edit["firstmatch"] is True


def test_stanzas_checked_first_and_the_qm_restarts_on_change() -> None:
    check = _index("has the Channels and TCP stanzas")
    edit = _index("channel failover settings")
    start = _index("enable + start the queue-manager service")
    verify = _index("holds exactly one of each failover setting")
    assert check < edit < start < verify
    tasks = _tasks()
    assert tasks[edit]["register"] == "_mq_qmgr_qmini"
    state = tasks[start]["ansible.builtin.systemd"]["state"]
    assert "restarted" in state
    assert "_mq_qmgr_qmini is changed" in state
    assert tasks[verify]["failed_when"] == "_mq_qmgr_qmini_check.stdout | trim != '1'"


# --- HBINT on both ends of every inter-QM channel pair ---------------------------------

ANSIBLE = ROLE.parents[1]
CHANNEL_LINE = re.compile(r"(DEFINE|ALTER) CHANNEL\([^)]*\) CHLTYPE\((SDR|RCVR)\)")


def _channel_lines() -> list[tuple[str, str]]:
    sources = [*ANSIBLE.glob("*.yml"), *ANSIBLE.glob("roles/*/templates/*.j2")]
    return [
        (str(path.relative_to(ANSIBLE)), line)
        for path in sources
        for line in path.read_text().splitlines()
        if CHANNEL_LINE.search(line)
    ]


def test_hbint_is_under_60_so_the_timeout_is_twice_it() -> None:
    value = yaml.safe_load((ANSIBLE / "group_vars" / "all" / "channels.yml").read_text())
    assert value["inter_qm_hbint"] == 10


def test_every_inter_qm_sdr_and_rcvr_sets_the_shared_hbint() -> None:
    lines = _channel_lines()
    files = {path for path, _ in lines}
    assert files == {
        "site-nativeha.yml",
        "site-nativeha-ubuntu.yml",
        "roles/mq-inter-qm/templates/their-side.mqsc.j2",
        "roles/mq-pcmk-qmgr/templates/inter-qm.mqsc.j2",
        "roles/rdqm-app-tls/templates/app-tls.mqsc.j2",
    }
    assert len(lines) == 10  # an SDR and a RCVR at each of the five sites
    hbint = "HBINT({{ inter_qm_hbint }})"
    missing = [f"{path}: {line.strip()[:80]}" for path, line in lines if hbint not in line]
    assert missing == []
