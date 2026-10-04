"""mq-event-monitor resolves the QM's qm.ini from MQ, not a hard-coded path (#1344).

The #809 log-type verify slurps the live QM's qm.ini. It used to read the default
``/var/mqm/qmgrs/<QM>/qm.ini``, which does not exist for a ``crtmqm -md`` QM: the
Pacemaker arm creates PCMKAPP with ``-md /mqshared/qmgrs``, so provisioning failed on
the creation node. The role now reads the QueueManager stanza (``dspmqinf -o stanza``)
and uses DataPath, else ``<Prefix>/qmgrs/<Directory>``, else fails loudly.

These checks pin the task shape statically and render the role's OWN parse expressions,
taken from service.yml, through Ansible's templar against sample ``dspmqinf`` output. The
real templar matters: Ansible protects backslashes only inside ``{{ }}``, so a ``'\\1'``
backreference that is correct in a task can break when moved into a ``{% set %}``.
"""

from __future__ import annotations

from pathlib import Path
from typing import Any

import pytest
import yaml
from ansible.parsing.dataloader import DataLoader
from ansible.template import Templar, trust_as_template

REPO_ROOT = Path(__file__).resolve().parents[1]
SERVICE = REPO_ROOT / "ansible" / "roles" / "mq-event-monitor" / "tasks" / "service.yml"

LOOKUP = "read the queue manager's QueueManager stanza (its data directory) (#1344)"
EXTRACT = "extract DataPath, Prefix and Directory from the QueueManager stanza (#1344)"
RESOLVE = "resolve the queue manager's data directory from its QueueManager stanza (#1344)"
ASSERT = "assert the queue manager's data directory resolved — fail loud if not (#1344)"
SLURP = "read the live queue manager's actual log type from qm.ini (#809)"

# `dspmqinf -o stanza` as MQ prints it (format per the IBM MQ 10.0 dspmqinf page).
DEFAULT_PATH_STANZA = """\
QueueManager:
   Name=NHAUAPP
   Prefix=/var/mqm
   Directory=NHAUAPP
   InstallationName=Installation1
"""
MD_STANZA = """\
QueueManager:
   Name=PCMKAPP
   Prefix=/var/mqm
   Directory=PCMKAPP
   DataPath=/mqshared/qmgrs/PCMKAPP
   InstallationName=Installation1
"""
RDQM_STANZA = """\
QueueManager:
   Name=RDQMAPP
   Prefix=/var/mqm
   Directory=RDQMAPP
   DataPath=/var/mqm/vols/rdqmapp/qmgr/rdqmapp
   InstallationName=Installation1
"""
# IBM's own example: a transformed name (QM.NAME -> QM!NAME) and a CRLF line ending.
IBM_EXAMPLE_STANZA = (
    "QueueManager:\r\n Name=QM.NAME\r\n Prefix=/var/mqm\r\n Directory=QM!NAME\r\n"
    " DataPath=/MQHA/qmgrs/QM!NAME\r\n InstallationName=Installation1\r\n"
)
TRANSFORMED_DEFAULT_STANZA = "QueueManager:\n Name=QM.NAME\n Prefix=/var/mqm\n Directory=QM!NAME\n"


def _tasks() -> dict[str, dict[str, Any]]:
    tasks = yaml.safe_load(SERVICE.read_text())
    return {t["name"]: t for t in tasks}


def _render(template: str, variables: dict[str, Any]) -> str:
    templar = Templar(loader=DataLoader(), variables=variables)
    return str(templar.template(trust_as_template(template)))


def _resolve(stdout: str) -> str:
    """Run the role's extract + resolve set_facts, in order, against ``stdout``."""
    tasks = _tasks()
    facts: dict[str, Any] = {"qm_stanza": {"stdout": stdout, "rc": 0}}
    extract = tasks[EXTRACT]["ansible.builtin.set_fact"]
    fields = {key: _render(expr, facts) for key, expr in extract.items()}
    resolve = tasks[RESOLVE]["ansible.builtin.set_fact"]["_qm_datapath"]
    return _render(resolve, {**facts, **fields})


@pytest.mark.parametrize(
    ("stdout", "expected"),
    [
        # Native HA / SVCQM: no DataPath -> exactly the old default path.
        (DEFAULT_PATH_STANZA, "/var/mqm/qmgrs/NHAUAPP"),
        # Pacemaker: crtmqm -md /mqshared/qmgrs.
        (MD_STANZA, "/mqshared/qmgrs/PCMKAPP"),
        # RDQM: DataPath on the DRBD volume (what rdqm-active-node already uses).
        (RDQM_STANZA, "/var/mqm/vols/rdqmapp/qmgr/rdqmapp"),
        (IBM_EXAMPLE_STANZA, "/MQHA/qmgrs/QM!NAME"),
        (TRANSFORMED_DEFAULT_STANZA, "/var/mqm/qmgrs/QM!NAME"),
    ],
)
def test_resolves_the_qm_data_directory(stdout: str, expected: str) -> None:
    assert _resolve(stdout) == expected


@pytest.mark.parametrize(
    "stdout",
    [
        "",
        "QueueManager:\n Name=X\n Prefix=/var/mqm\n",  # no Directory
        "QueueManager:\n Name=X\n Directory=X\n",  # no Prefix
        "QueueManager:\n Name=X\n DataPath=\n Prefix=/var/mqm\n",  # empty DataPath, no Directory
    ],
)
def test_unresolvable_stanza_yields_empty_for_the_assert(stdout: str) -> None:
    assert _resolve(stdout) == ""


def test_lookup_reads_the_stanza_as_mqm_and_fails_loud() -> None:
    task = _tasks()[LOOKUP]
    assert task["ansible.builtin.command"]["argv"] == [
        "/opt/mqm/bin/dspmqinf",
        "-o",
        "stanza",
        "{{ qmgr_name }}",
    ]
    assert task["become_user"] == "mqm"
    assert task["register"] == "qm_stanza"
    assert task["changed_when"] is False
    assert task["failed_when"] == "qm_stanza.rc != 0"


def test_unresolved_data_directory_fails_loud() -> None:
    task = _tasks()[ASSERT]["ansible.builtin.assert"]
    assert task["that"] == "_qm_datapath | length > 0"


def test_slurp_uses_the_resolved_data_directory() -> None:
    names = list(_tasks())
    assert names.index(LOOKUP) < names.index(EXTRACT) < names.index(RESOLVE)
    assert names.index(RESOLVE) < names.index(ASSERT) < names.index(SLURP)
    assert _tasks()[SLURP]["ansible.builtin.slurp"]["src"] == "{{ _qm_datapath }}/qm.ini"


def test_no_hard_coded_default_qm_data_path() -> None:
    text = SERVICE.read_text()
    code = "\n".join(line for line in text.splitlines() if not line.lstrip().startswith("#"))
    assert "/var/mqm/qmgrs/" not in code
    # The old comment claimed Native HA replicates qm.ini to every node; it is gone.
    assert "replicates qm.ini to every node" not in text


def test_parse_regexes_stay_inside_expressions() -> None:
    # Ansible escapes backslashes only inside {{ }}; a {% set %} would mangle '\1'.
    extract = _tasks()[EXTRACT]["ansible.builtin.set_fact"]
    for expr in extract.values():
        assert "{%" not in expr
