"""Single-source invariant for the MQ version pin (#1071, epic .github#219).

There is exactly one authoritative MQ-version pin -- ``lab/mq-version``, a bare
4-part version string -- and every consumer resolves to it rather than carrying
its own literal. These tests guard that invariant so a version bump (the
9.4.5.0 -> 10.0 flip in #1075) is a one-line edit to the pin file, with no stray
literal left behind in any consumer.
"""

from __future__ import annotations

import re
from pathlib import Path

import pytest

from mqlab import manifest as m
from mqlab.paths import mq_version_pin_path, repo_root

# A bare 4-part version string: MAJOR.MINOR.CDS.FIX (e.g. 9.4.5.0).
FOUR_PART = re.compile(r"^\d+\.\d+\.\d+\.\d+$")

# A *quoted numeric* 4-part literal in source (e.g. "9.4.5.0"). This matches only
# real digits, so it never false-positives on a validation pattern such as
# ``\d+\.\d+\.\d+\.\d+`` that a reader uses to check the pin.
QUOTED_VERSION = re.compile(r"""['"]\d+\.\d+\.\d+\.\d+['"]""")


def _pin_text() -> str:
    return mq_version_pin_path().read_text().strip()


def test_pin_file_exists_and_is_a_bare_four_part_version():
    path = mq_version_pin_path()
    assert path.is_file(), f"authoritative MQ-version pin missing: {path}"
    raw = path.read_text()
    pin = raw.strip()
    assert FOUR_PART.match(pin), f"pin is not a bare 4-part version: {raw!r}"
    # "Bare" means the file holds only the version, plus at most one trailing
    # newline -- no YAML key, no comment, nothing a parser has to strip.
    assert raw in (pin, pin + "\n"), f"pin file is not bare: {raw!r}"


def test_manifest_default_mq_version_resolves_to_pin():
    assert _pin_text() == m.DEFAULT_MQ_VERSION


def test_cli_still_exports_default_mq_version_from_the_pin():
    # cli.py re-exports the symbol (it imports DEFAULT_MQ_VERSION from manifest);
    # the symbol must survive the re-thread and resolve to the same pin.
    from mqlab import cli

    assert _pin_text() == cli.DEFAULT_MQ_VERSION


def test_manifest_reader_rejects_a_malformed_pin(tmp_path, monkeypatch):
    # The pin read is loud, never silent: a malformed pin raises rather than
    # falling back to a stale literal (no-silent-failures).
    bad = tmp_path / "mq-version"
    bad.write_text("not-a-version\n")
    monkeypatch.setattr(m, "mq_version_pin_path", lambda: bad)
    with pytest.raises(ValueError, match="not a bare 4-part version"):
        m._read_mq_version_pin()


def test_manifest_module_carries_no_hardcoded_version_literal():
    src = Path(m.__file__).read_text()
    assert QUOTED_VERSION.search(src) is None, "no hardcoded version literal"


def test_fetch_mq_script_resolves_to_the_pin():
    text = (repo_root() / "scripts" / "fetch-mq.sh").read_text()
    assert "lab/mq-version" in text, "fetch-mq.sh must read the pin path"
    assert QUOTED_VERSION.search(text) is None, "no hardcoded version literal"
