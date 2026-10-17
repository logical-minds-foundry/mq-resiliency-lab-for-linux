"""Structure tests for ansible/capture-rdqm-blueprint.yml (epic .github#310 T1, #1422).

The capture is READ-ONLY and secret-free by construction; these tests pin that
contract statically because the live run needs the RDQM arm up (the human runs it, #1425).
"""

import pathlib
import re
import subprocess

import jinja2
import yaml
from ansible.modules.find import contentfilter
from ansible.parsing.dataloader import DataLoader
from ansible.template import Templar, trust_as_template

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


# --- #1603: /etc/drbd.d holds each node's DRBD TLS private key; it must never be captured.

# Synthetic PEM bodies (not real keys): the tests assert these bodies never reach the output.
_KEY_BODY = "MIIEvQIBADANBgkqhkiG9w0BAQEFAASCBKcwggSjAgEAAoIBAQC-fake-key-body"
_CERT_BODY = "MIIDazCCAlOgAwIBAgIUfake-cert-body"
_KEY_PEM = f"-----BEGIN PRIVATE KEY-----\n{_KEY_BODY}\n-----END PRIVATE KEY-----\n"
_CERT_PEM = f"-----BEGIN CERTIFICATE-----\n{_CERT_BODY}\n-----END CERTIFICATE-----\n"


def _key_redact(text: str) -> str:
    """Run the playbook's own private-key-block awk program over `text`."""
    prog = _capture_play()["vars"]["drbd_key_block_redact_awk"]
    result = subprocess.run(["awk", prog], input=text, capture_output=True, text=True, check=True)
    return result.stdout


def _run_drbd_conf(etc: pathlib.Path) -> subprocess.CompletedProcess[str]:
    """Render the drbd-conf task body and run it against a fake /etc tree under `etc`."""
    play_vars = _capture_play()["vars"]
    variables = {k: v for k, v in play_vars.items() if k != "cap_items"}
    templar = Templar(loader=DataLoader(), variables=variables)  # Ansible's own quote filter
    rendered = str(templar.template(trust_as_template(_body(_task_by_register("cap_drbd_conf")))))
    assert "/etc/drbd" in rendered
    script = rendered.replace("/etc/drbd", f"{etc}/drbd")
    return subprocess.run(["bash", "-c", script], capture_output=True, text=True, check=False)


def _fake_drbd_etc(tmp_path: pathlib.Path) -> pathlib.Path:
    (tmp_path / "drbd.conf").write_text('include "drbd.d/*.res";\n')
    drbd_d = tmp_path / "drbd.d"
    drbd_d.mkdir()
    (drbd_d / "global_common.conf").write_text('net {\n    shared-secret "s3cr3t";\n}\n')
    (drbd_d / "rdqm-a1.key").write_text(_KEY_PEM)
    (drbd_d / "rdqm-a1.crt").write_text(_CERT_PEM)
    (drbd_d / "bundle.pem").write_text(_CERT_PEM + _KEY_PEM)  # key by content, not name
    (drbd_d / "store.p12").write_text("binary-keystore-bytes\n")
    (drbd_d / "subdir").mkdir()  # not a regular file: skipped
    return tmp_path


def test_key_file_patterns_cover_every_key_and_keystore_type():
    patterns = _capture_play()["vars"]["drbd_key_file_case"].split("|")
    assert set(patterns) >= {"*.key", "*.p12", "*.kdb", "*.sth", "*.jks"}


def test_drbd_conf_excludes_key_files_by_name_and_by_content(tmp_path):
    result = _run_drbd_conf(_fake_drbd_etc(tmp_path))
    assert result.returncode == 0, result.stderr
    out = result.stdout
    # Key files: path recorded, contents never read or printed.
    assert f"### {tmp_path}/drbd.d/rdqm-a1.key (key file: path recorded" in out
    assert f"### {tmp_path}/drbd.d/store.p12 (key file: path recorded" in out
    assert f"### {tmp_path}/drbd.d/bundle.pem (carries a private key block" in out
    assert _KEY_BODY not in out and "binary-keystore-bytes" not in out
    assert "PRIVATE KEY" not in out
    # Public material and ordinary config are still captured; shared-secret still redacted.
    assert f"### {tmp_path}/drbd.d/rdqm-a1.crt\n" + _CERT_PEM in out
    assert 'shared-secret "REDACTED";' in out and "s3cr3t" not in out
    assert "rdqm-a1.key" in out.split("### ls -l")[1]  # the ls -l listing keeps the name


def test_drbd_conf_fails_loudly_on_an_unreadable_drbd_conf(tmp_path):
    # set -euo pipefail: a missing drbd.conf fails the item, never an empty capture.
    (tmp_path / "drbd.d").mkdir()
    assert _run_drbd_conf(tmp_path).returncode != 0


def test_both_drbd_items_pipe_through_the_key_block_redaction():
    for reg in ("cap_drbd_dump", "cap_drbd_conf"):
        body = _body(_task_by_register(reg))
        assert re.search(r"\|\s*awk \{\{ drbd_key_block_redact_awk \| quote \}\}\s*$", body), reg


def test_key_block_redaction_replaces_every_private_key_block_kind():
    sample = (
        "before\n"
        + _KEY_PEM
        + "-----BEGIN RSA PRIVATE KEY-----\nrsa-body\n-----END RSA PRIVATE KEY-----\n"
        + "-----BEGIN ENCRYPTED PRIVATE KEY-----\nenc-body\n-----END ENCRYPTED PRIVATE KEY-----\n"
        + _CERT_PEM
        + "after\n"
    )
    out = _key_redact(sample)
    assert "PRIVATE KEY" not in out
    for body in (_KEY_BODY, "rsa-body", "enc-body"):
        assert body not in out
    assert out.count("[private key block redacted at capture (#1603)]") == 3
    assert out.startswith("before\n") and out.endswith(_CERT_PEM + "after\n")


def test_key_block_redaction_handles_a_one_line_block():
    out = _key_redact("x -----BEGIN PRIVATE KEY-----body-----END PRIVATE KEY----- y\nnext\n")
    assert out == "[private key block redacted at capture (#1603)]\nnext\n"


def test_key_block_redaction_drops_an_unterminated_block_and_says_so():
    out = _key_redact("head\n-----BEGIN EC PRIVATE KEY-----\nec-body\nmore\n")
    assert "ec-body" not in out and "more" not in out and "PRIVATE KEY" not in out
    assert out == (
        "head\n[private key block redacted at capture (#1603)]\n"
        "[private key block with no END line: rest of input dropped (#1603)]\n"
    )


def test_key_block_redaction_leaves_keyless_text_unchanged():
    sample = "resource rdqmapp {\n    protocol C;\n}\n" + _CERT_PEM
    assert _key_redact(sample) == sample


def _task_by_name(prefix: str) -> dict:
    [task] = [t for t in _capture_play()["tasks"] if t["name"].startswith(prefix)]
    return task


def test_prewrite_guard_rejects_private_key_text_on_either_stream():
    guard = _task_by_name("check - no capture item carries PRIVATE KEY")
    assert guard["loop"] == "{{ cap_items }}"
    tmpl = "{{ " + guard["ansible.builtin.assert"]["that"] + " }}"
    assert _render(tmpl, "plain config", "") == "True"
    assert _render(tmpl, "[private key block redacted at capture (#1603)]", "") == "True"
    assert _render(tmpl, _KEY_PEM, "") == "False"
    assert _render(tmpl, "", "-----BEGIN RSA PRIVATE KEY-----") == "False"
    # The stream join cannot fabricate a match: "PRIVATE" + "KEY" across streams is no key.
    assert _render(tmpl, "PRIVATE", " KEY") == "True"
    # The failure message names the item and host only, never the captured content.
    assert "item.value" not in guard["ansible.builtin.assert"]["fail_msg"]


def test_prewrite_guard_runs_after_every_capture_and_before_any_write():
    tasks = _capture_play()["tasks"]
    pos = tasks.index(_task_by_name("check - no capture item carries PRIVATE KEY"))
    captures = [i for i, t in enumerate(tasks) if str(t.get("register", "")).startswith("cap_")]
    first_write = next(i for i, t in enumerate(tasks) if _is_write(t))
    assert max(captures) < pos < first_write


def test_postwrite_guard_scans_every_written_file_once_after_the_write(tmp_path):
    tasks = _capture_play()["tasks"]
    scan = _task_by_name("guard - scan the written capture for PRIVATE KEY")
    check = _task_by_name("guard - no written capture file contains PRIVATE KEY")
    assert tasks.index(_copy_task()) < tasks.index(scan) < tasks.index(check)
    for task in (scan, check):
        assert task["delegate_to"] == "localhost" and task["run_once"] is True
    find = scan["ansible.builtin.find"]
    assert find["paths"] == "{{ blueprint_out }}"
    assert find["recurse"] is True and find["hidden"] is True and find["file_type"] == "file"
    assert check["ansible.builtin.assert"]["that"] == f"{scan['register']}.matched == 0"
    # Run Ansible's own content filter with the task's settings: a key anywhere in a file
    # (not only at a line start) is found; a redacted file is not.
    leaked = tmp_path / "leaked.txt"
    leaked.write_text("### /etc/drbd.d/x\n  " + _KEY_PEM)
    clean = tmp_path / "clean.txt"
    clean.write_text(_key_redact("### /etc/drbd.d/x\n" + _KEY_PEM))
    args = (find["contains"], None, find["read_whole_file"])
    assert contentfilter(str(leaked), *args) is True
    assert contentfilter(str(clean), *args) is False


def test_postwrite_guard_message_names_the_files():
    msg = _task_by_name("guard - no written capture file contains PRIVATE KEY")[
        "ansible.builtin.assert"
    ]["fail_msg"]
    assert "blueprint_key_scan.files | map(attribute='path')" in msg
