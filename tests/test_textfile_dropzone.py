"""Regression guard for the node-exporter textfile drop zone (#449/#458).

The drop zone must stay a group-writable, setgid shared dir: the unprivileged
app-requester (User=vagrant, in the node_exporter group) publishes its round-trip
metrics there, and a silent regression to a non-group-writable mode (e.g. 0755)
breaks that with mkstemp EACCES. The systemd-tmpfiles rule is the single
declarative owner of the mode; this test fails loudly if it ever drifts.
"""

from __future__ import annotations

from pathlib import Path

_REPO = Path(__file__).resolve().parents[1]
_TMPFILES = _REPO / "ansible/roles/node-exporter/files/node-exporter-textfile.conf"


def _rule() -> list[str]:
    line = next(
        ln for ln in _TMPFILES.read_text().splitlines() if ln.strip() and not ln.startswith("#")
    )
    return line.split()


def test_dropzone_tmpfiles_rule_is_group_writable_setgid():
    typ, path, mode, owner, group, *_ = _rule()
    assert typ == "d"  # a directory
    assert path == "/var/lib/node_exporter/textfile"
    # setgid (2) + rwxr-x with group-WRITE — NOT a non-group-writable 0755 (#449)
    assert mode == "2775"
    assert owner == "node_exporter"
    assert group == "node_exporter"
