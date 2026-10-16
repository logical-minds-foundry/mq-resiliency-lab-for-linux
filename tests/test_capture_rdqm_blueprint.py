"""Structure tests for ansible/capture-rdqm-blueprint.yml (epic .github#310 T1, #1422).

The capture is READ-ONLY and secret-free by construction; these tests pin that
contract statically because the live run needs the RDQM arm up (the human runs it, #1425).
"""

import pathlib
import re
import subprocess

import yaml

PLAYBOOK = pathlib.Path("ansible/capture-rdqm-blueprint.yml")
_ALLOWED_REMOTE = {"ansible.builtin.command", "ansible.builtin.shell", "ansible.builtin.assert"}
_CONTROLLER_WRITES = {"ansible.builtin.copy", "ansible.builtin.file"}
_SECRET_PATTERNS = re.compile(r"\.kdb|\.p12|\.sth|\.jks|authkey|/ssl/|keystore", re.IGNORECASE)
# The stable item names Task 2 commits under docs/reference/rdqm-blueprint/<host>/.
_ITEMS = {
    "drbd-version",
    "drbd-conf",
    "drbd-dump",
    "drbd-status",
    "packages",
    "pcmk-config",
    "pcmk-status",
    "ocf-agents",
    "corosync-conf",
    "quorum",
    "vgs",
    "lvs",
    "mounts",
    "lsblk",
    "rdqm-ini",
    "rdqmstatus-qm",
    "rdqmstatus-group",
    "units",
    "mqs-ini",
    "dspmqinf",
}


def _plays() -> list[dict]:
    return yaml.safe_load(PLAYBOOK.read_text())


def _capture_play() -> dict:
    [play] = [p for p in _plays() if p.get("hosts") == "rdqm_a:rdqm_b"]
    return play


def _module(task: dict) -> str:
    return next(k for k in task if k.startswith("ansible.builtin."))


def _body(task: dict) -> str:
    return str(task.get("ansible.builtin.shell") or task.get("ansible.builtin.command") or "")


def _is_write(task: dict) -> bool:
    return task.get("delegate_to") == "localhost" and _module(task) in _CONTROLLER_WRITES


def _redact(text: str) -> str:
    """Run the playbook's own redaction sed expression over `text` (GNU sed -E)."""
    expr = _capture_play()["vars"]["drbd_secret_redact_sed"]
    result = subprocess.run(
        ["sed", "-E", expr], input=text, capture_output=True, text=True, check=True
    )
    return result.stdout


def test_capture_targets_both_rdqm_sites():
    assert _capture_play()["hosts"] == "rdqm_a:rdqm_b"


def test_capture_is_the_only_play():
    assert len(_plays()) == 1


def test_capture_any_host_failure_aborts_every_host():
    # A preflight failure on ONE node must stop the run everywhere before any write —
    # otherwise the healthy nodes would still lay down a partial tree that looks complete.
    assert _capture_play()["any_errors_fatal"] is True


def test_capture_remote_tasks_are_read_only():
    for task in _capture_play()["tasks"]:
        mod = _module(task)
        if task.get("delegate_to") == "localhost":
            continue  # controller-side: checking/writing the capture tree
        assert mod in _ALLOWED_REMOTE, f"{task['name']}: {mod} is not a read-only module"
        if mod != "ansible.builtin.assert":
            assert task.get("changed_when") is False, f"{task['name']}: must be changed_when: false"


def test_capture_never_swallows_errors():
    for task in _capture_play()["tasks"]:
        assert "failed_when" not in task or task["failed_when"] is not False, task["name"]
        assert "ignore_errors" not in task, task["name"]
        assert "|| true" not in _body(task), task["name"]


def test_capture_never_reads_secret_material():
    for task in _capture_play()["tasks"]:
        assert not _SECRET_PATTERNS.search(_body(task)), f"{task['name']} touches secret material"


def test_capture_redacts_drbd_shared_secret():
    tasks = {t.get("register"): t for t in _capture_play()["tasks"]}
    # Both items that can print a DRBD net section (the effective dump and the raw files).
    for reg in ("cap_drbd_dump", "cap_drbd_conf"):
        assert "drbd_secret_redact_sed" in _body(tasks[reg]), reg
        assert "pipefail" in _body(tasks[reg]), reg


def test_redaction_expression_scrubs_quoted_and_bare_secrets():
    sample = (
        "    net {\n"
        '        shared-secret    "s3cr3t-quoted";\n'
        "        shared-secret bare_s3cr3t;\n"
        "        cram-hmac-alg    sha1;\n"
        "    }\n"
    )
    out = _redact(sample)
    assert "s3cr3t" not in out
    assert out.count('"REDACTED"') == 2
    assert "cram-hmac-alg    sha1;" in out  # neighbouring options untouched


def test_redaction_expression_leaves_secretless_config_unchanged():
    sample = "resource rdqmapp {\n    protocol C;\n}\n"
    assert _redact(sample) == sample


def test_capture_preflight_precedes_every_write():
    tasks = _capture_play()["tasks"]
    first_write = next(i for i, t in enumerate(tasks) if _is_write(t))
    preflight = [i for i, t in enumerate(tasks) if "preflight" in t["name"]]
    assert preflight and max(preflight) < first_write


def test_capture_preflight_checks_rdqm_health_and_fresh_output():
    names = " ".join(t["name"] for t in _capture_play()["tasks"] if "preflight" in t["name"])
    assert "HA status" in names  # RDQM healthy, not merely present
    assert "does not exist" in names  # never merge into an older capture tree


def test_capture_writes_every_item_once():
    [write] = [
        t for t in _capture_play()["tasks"] if _is_write(t) and _module(t) == "ansible.builtin.copy"
    ]
    keys = [item["key"] for item in write["loop"]]
    assert sorted(keys) == sorted(_ITEMS)
    registered = {t.get("register") for t in _capture_play()["tasks"]}
    for item in write["loop"]:
        reg = item["value"].strip("{} ")
        assert reg in registered, f"{item['key']}: {reg} is never registered"
