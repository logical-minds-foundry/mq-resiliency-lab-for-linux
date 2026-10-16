"""Structure tests for ansible/capture-rdqm-blueprint.yml (epic .github#310 T1, #1422).

The capture is READ-ONLY and secret-free by construction; these tests pin that
contract statically because the live run needs the RDQM arm up (the human runs it, #1425).
"""

import pathlib
import re
import subprocess

import jinja2
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


def _copy_task() -> dict:
    [write] = [
        t for t in _capture_play()["tasks"] if _is_write(t) and _module(t) == "ansible.builtin.copy"
    ]
    return write


def _nonempty_check() -> dict:
    [check] = [t for t in _capture_play()["tasks"] if t["name"].startswith("check - every capture")]
    return check


def test_capture_writes_every_item_once():
    items = _capture_play()["vars"]["cap_items"]
    keys = [item["key"] for item in items]
    assert sorted(keys) == sorted(_ITEMS)
    assert len(keys) == len(set(keys))
    assert _copy_task()["loop"] == "{{ cap_items }}"
    registered = {t.get("register") for t in _capture_play()["tasks"]}
    for item in items:
        reg = item["value"].strip("{} ")
        assert reg in registered, f"{item['key']}: {reg} is never registered"


# --- #1600: rdqmstatus reports on stderr; no item may silently lose a stream or come out empty.


def _task_by_register(reg: str) -> dict:
    [task] = [t for t in _capture_play()["tasks"] if t.get("register") == reg]
    return task


def test_rdqmstatus_items_fold_stderr_into_the_capture():
    # rdqmstatus prints its whole report on stderr with an empty stdout (rc 0) — observed on
    # the live rdqm-rhel arm. Both rdqmstatus items must carry that report as their data.
    for reg in ("cap_rdqmstatus_qm", "cap_rdqmstatus_group"):
        task = _task_by_register(reg)
        assert _module(task) == "ansible.builtin.shell", reg
        assert _body(task).rstrip().endswith("2>&1"), reg


def test_ha_status_preflight_reads_both_streams():
    [check] = [t for t in _capture_play()["tasks"] if "HA status Normal" in t["name"]]
    that = check["ansible.builtin.assert"]["that"]
    assert "cap_rdqmstatus_qm.stdout" in that and "cap_rdqmstatus_qm.stderr" in that
    msg = check["ansible.builtin.assert"]["fail_msg"]
    assert "cap_rdqmstatus_qm.stdout" in msg and "cap_rdqmstatus_qm.stderr" in msg


def test_drbd_config_items_redact_stderr_too():
    # stderr is folded in BEFORE the redaction sed, so no stream can bypass it.
    for reg in ("cap_drbd_dump", "cap_drbd_conf"):
        body = _body(_task_by_register(reg))
        assert re.search(r"2>&1\s*\|\s*sed -E \{\{ drbd_secret_redact_sed", body), reg


def _render(template: str, stdout: str, stderr: str) -> str:
    item = {"key": "x", "value": {"stdout": stdout, "stderr": stderr, "rc": 0}}
    # Plain-text capture files, not HTML: select_autoescape leaves these sample values intact.
    env = jinja2.Environment(keep_trailing_newline=True, autoescape=jinja2.select_autoescape())
    return env.from_string(template).render(item=item)


def test_write_keeps_stderr_alongside_stdout():
    content = _copy_task()["ansible.builtin.copy"]["content"]
    assert _render(content, "data", "") == "data\n"
    assert _render(content, "data", "warn") == "data\n### stderr\nwarn\n"


def test_nonempty_check_rejects_empty_and_whitespace_items():
    that = _nonempty_check()["ansible.builtin.assert"]["that"]
    tmpl = "{{ " + that + " }}"
    assert _render(tmpl, "data", "") == "True"
    assert _render(tmpl, "", "report on stderr") == "True"
    assert _render(tmpl, "", "") == "False"
    assert _render(tmpl, " \n", "\n") == "False"


def test_nonempty_check_covers_every_item_on_both_streams_before_any_write():
    check = _nonempty_check()
    assert check["loop"] == "{{ cap_items }}"
    that = check["ansible.builtin.assert"]["that"]
    assert "item.value.stdout" in that and "item.value.stderr" in that
    assert "length > 0" in that
    tasks = _capture_play()["tasks"]
    pos = tasks.index(check)
    first_write = next(i for i, t in enumerate(tasks) if _is_write(t))
    captures = [i for i, t in enumerate(tasks) if str(t.get("register", "")).startswith("cap_")]
    last_capture = max(captures)
    assert last_capture < pos < first_write


def test_mounts_item_is_never_empty_on_a_non_owner_node():
    # No /var/mqm/vols mount is valid on a non-owner node; the item says so explicitly so the
    # non-empty check can treat every empty file as missing data.
    body = _body(_task_by_register("cap_mounts"))
    assert "END" in body and "no filesystem mounted under /var/mqm/vols" in body


def test_mounts_awk_reports_none_when_nothing_matches():
    body = _body(_task_by_register("cap_mounts"))
    match = re.search(r"awk '(.*?)'", body, re.DOTALL)
    assert match, body
    awk_prog = match.group(1)
    sample = "/ /dev/sda1 xfs rw\n/boot /dev/sda2 xfs rw\n"
    none = subprocess.run(
        ["awk", awk_prog], input=sample, capture_output=True, text=True, check=True
    )
    assert none.stdout.strip() == "(no filesystem mounted under /var/mqm/vols on this node)"
    owned = sample + "/var/mqm/vols/rdqmapp /dev/drbd100 ext4 rw\n"
    some = subprocess.run(
        ["awk", awk_prog], input=owned, capture_output=True, text=True, check=True
    )
    assert some.stdout == "/var/mqm/vols/rdqmapp /dev/drbd100 ext4 rw\n"
