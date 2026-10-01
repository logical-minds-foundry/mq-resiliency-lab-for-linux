"""Guard that the per-login dynamic MOTD is OFF in every baked Ubuntu box (#1229).

The #1200 macOS runs showed Ubuntu's pam_motd running /etc/update-motd.d on every SSH
login; 50-landscape-sysinfo alone held ~4.5 of obs's 12 vCPUs for minutes. These guards pin
the bake-time fix:

* every Ubuntu bake (derived from build-fatbox.sh, so a new Ubuntu box cannot slip past)
  includes the `motd-off` role, and no RHEL bake does;
* the role's regex comments out exactly Ubuntu 24.04's real pam_motd session lines, is
  idempotent, and the role verifies the result fail-loud;
* the role is in the manifest-hash closure, so a later edit to it forces a rebake.
"""

from __future__ import annotations

import re
import shutil
import subprocess
from pathlib import Path
from typing import Any

import yaml

REPO_ROOT = Path(__file__).resolve().parents[1]
ANSIBLE = REPO_ROOT / "ansible"
ROLE = ANSIBLE / "roles" / "motd-off"
FATBOX = REPO_ROOT / "lab" / "boxes" / "build-fatbox.sh"
MANIFEST_HASH = REPO_ROOT / "lab" / "boxes" / "_manifest-hash.sh"

# The motd stanza Ubuntu 24.04 (noble) ships in /etc/pam.d/sshd (openssh
# debian/openssh-server.sshd.pam.in); /etc/pam.d/login (shadow debian/login.pam) carries the
# same two session lines with different column spacing.
NOBLE_SSHD_MOTD = """\
# Print the message of the day upon successful login.
# This includes a dynamically generated part from /run/motd.dynamic
# and a static (admin-editable) part from /etc/motd.
session    optional     pam_motd.so  motd=/run/motd.dynamic
session    optional     pam_motd.so noupdate

# Set up user limits from /etc/security/limits.conf.
session    required     pam_limits.so
"""
NOBLE_LOGIN_MOTD = """\
session    optional   pam_motd.so motd=/run/motd.dynamic
session    optional   pam_motd.so noupdate
session    optional   pam_mail.so standard
"""


def _box_bakes() -> dict[str, tuple[str, str]]:
    """box -> (base kind, bake stem), parsed from build-fatbox.sh's --box case."""
    text = FATBOX.read_text(encoding="utf-8")
    rows = re.findall(r"^\s*([a-z0-9-]+)\)\s+BASE_KIND=(\w+);.*BAKE=([a-z0-9-]+)", text, re.M)
    assert rows, f"could not parse the --box table from {FATBOX}"
    return {box: (kind, stem) for box, kind, stem in rows}


def _iter_tasks(node: Any) -> Any:
    if isinstance(node, list):
        for item in node:
            yield from _iter_tasks(item)
    elif isinstance(node, dict):
        yield node
        for key in ("tasks", "pre_tasks", "post_tasks", "handlers", "block", "rescue", "always"):
            if key in node:
                yield from _iter_tasks(node[key])


def _includes_role(path: Path, role: str) -> bool:
    doc = yaml.safe_load(path.read_text(encoding="utf-8"))
    for task in _iter_tasks(doc):
        for verb in ("ansible.builtin.include_role", "ansible.builtin.import_role"):
            spec = task.get(verb)
            if isinstance(spec, dict) and spec.get("name") == role:
                return True
    return False


def _role_tasks() -> list[dict]:
    tasks = yaml.safe_load((ROLE / "tasks" / "main.yml").read_text(encoding="utf-8"))
    assert isinstance(tasks, list) and tasks
    return tasks


def _defaults() -> dict:
    return yaml.safe_load((ROLE / "defaults" / "main.yml").read_text(encoding="utf-8"))


def _task(fragment: str) -> dict:
    return next(t for t in _role_tasks() if fragment in t.get("name", ""))


def _apply_replace(text: str) -> str:
    """Mimic ansible.builtin.replace: re.sub with re.MULTILINE over the whole file."""
    spec = _task("comment out the pam_motd")["ansible.builtin.replace"]
    assert spec["regexp"] == "{{ motd_off_active_regex }}"
    return re.compile(_defaults()["motd_off_active_regex"], re.MULTILINE).sub(spec["replace"], text)


def _active(text: str) -> list[str]:
    return re.findall(_defaults()["motd_off_active_regex"], text, re.MULTILINE)


def test_every_ubuntu_bake_includes_the_role_and_no_rhel_bake_does() -> None:
    bakes = _box_bakes()
    ubuntu = {box: stem for box, (kind, stem) in bakes.items() if kind == "ubuntu"}
    rhel = {box: stem for box, (kind, stem) in bakes.items() if kind == "rhel"}
    assert ubuntu and rhel, f"expected both Ubuntu and RHEL boxes in {FATBOX}; got {bakes}"
    missing = [
        box
        for box, stem in ubuntu.items()
        if not _includes_role(ANSIBLE / f"bake-{stem}.yml", "motd-off")
    ]
    assert missing == [], f"Ubuntu bakes missing the motd-off role (#1229): {missing}"
    wrong = [
        box
        for box, stem in rhel.items()
        if _includes_role(ANSIBLE / f"bake-{stem}.yml", "motd-off")
    ]
    assert wrong == [], f"RHEL bakes must not include the Ubuntu-only role (#1229): {wrong}"


def test_role_targets_sshd_and_login() -> None:
    assert _defaults()["motd_off_pam_files"] == ["/etc/pam.d/sshd", "/etc/pam.d/login"]
    assert _task("comment out the pam_motd").get("loop") == "{{ motd_off_pam_files }}"


def test_regex_comments_out_exactly_the_noble_pam_motd_lines() -> None:
    for stock in (NOBLE_SSHD_MOTD, NOBLE_LOGIN_MOTD):
        assert len(_active(stock)) == 2, "both noble pam_motd session lines must match"
        edited = _apply_replace(stock)
        assert _active(edited) == [], f"pam_motd still active after the edit:\n{edited}"
        for line in edited.splitlines():
            if "pam_motd.so" in line:
                assert line.startswith("# session"), f"not commented out: {line!r}"
        # Every non-motd line is untouched.
        keep = [ln for ln in stock.splitlines() if "pam_motd.so" not in ln]
        assert [ln for ln in edited.splitlines() if "pam_motd.so" not in ln] == keep


def test_regex_edit_is_idempotent_and_handles_bracket_control() -> None:
    once = _apply_replace(NOBLE_SSHD_MOTD)
    assert _apply_replace(once) == once, "a second bake pass must change nothing"
    bracket = "session [success=ok default=ignore] pam_motd.so\n"
    assert _active(bracket), "bracketed PAM control syntax must be caught too"
    assert not _active("# session optional pam_motd.so\n")
    assert not _active("session optional pam_limits.so\n")


def test_role_masks_motd_news_timer() -> None:
    assert "motd-news.timer" in _defaults()["motd_off_timers"]
    systemd = _task("mask the motd-news timer")["ansible.builtin.systemd"]
    assert systemd.get("masked") is True


def test_role_verifies_fail_loud() -> None:
    grep = _task("verify no PAM service still runs pam_motd")
    argv = grep["ansible.builtin.command"]["argv"]
    assert argv[0] == "grep" and argv[-1] == "/etc/pam.d", f"must scan all of /etc/pam.d: {argv}"
    assert "{{ motd_off_active_regex }}" in argv
    assert grep.get("failed_when") == "_motd_off_active.rc != 1", (
        "only grep rc 1 (no match) may pass; a match (0) or an error (2) must fail the bake"
    )
    timer = _task("verify the motd-news timer is masked")
    assert "is-enabled" in str(timer["ansible.builtin.command"])
    assert "masked" in str(timer.get("failed_when"))


def _hash(root: Path, box: str) -> str:
    script = root / "lab" / "boxes" / "_manifest-hash.sh"
    return subprocess.run(
        [str(script), box], check=True, capture_output=True, text=True
    ).stdout.strip()


def test_role_is_in_the_manifest_hash_closure(tmp_path: Path) -> None:
    """Editing the role must flip every Ubuntu box's manifest hash (forces a rebake), and
    leave the RHEL boxes' hash alone. Runs against a scratch copy of the hash inputs so the
    real tree is never mutated."""
    (tmp_path / "lab" / "boxes").mkdir(parents=True)
    shutil.copy2(MANIFEST_HASH, tmp_path / "lab" / "boxes" / MANIFEST_HASH.name)
    shutil.copy2(REPO_ROOT / "lab" / "mq-version", tmp_path / "lab" / "mq-version")
    shutil.copytree(ANSIBLE / "roles", tmp_path / "ansible" / "roles")
    shutil.copytree(ANSIBLE / "group_vars", tmp_path / "ansible" / "group_vars")
    for bake in ANSIBLE.glob("bake-*.yml"):
        shutil.copy2(bake, tmp_path / "ansible" / bake.name)

    boxes = _box_bakes()
    before = {box: _hash(tmp_path, box) for box in boxes}
    probe = tmp_path / "ansible" / "roles" / "motd-off" / "defaults" / "main.yml"
    probe.write_text(probe.read_text(encoding="utf-8") + "# probe\n", encoding="utf-8")
    after = {box: _hash(tmp_path, box) for box in boxes}

    for box, (kind, _stem) in boxes.items():
        if kind == "ubuntu":
            assert before[box] != after[box], f"{box}: role edit did not flip the manifest hash"
        else:
            assert before[box] == after[box], f"{box}: Ubuntu-only role edit flipped a RHEL hash"
