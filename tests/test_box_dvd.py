"""RHEL DVD verify-and-guide at the base-box build path (epic .github#91, T4).

`box.verify_rhel_dvd` preflights the operator-supplied RHEL DVD before the
expensive base-box BUILD: MISSING file blocks, a pinned-but-MISMATCHED checksum
blocks, and an UNPINNED version emits a loud NOTICE and PROCEEDS (so we add the
integrity check without regressing the pre-existing no-SHA cold rebuild). The
fixture ISO carries a *computed* sha256 — the mechanism, never a real DVD hash.
Every seam is monkeypatchable so these unit tests never touch a real ISO.
"""

from __future__ import annotations

import hashlib
import io

import pytest
import typer
from rich.console import Console

from mqlab import box, cli
from mqlab.hostfacts import X86_64, HostFacts
from mqlab.render import Renderer
from mqlab.transcript import Transcript, transcript_path
from tests.fakes import RecordingRunner

_FACTS = HostFacts(arch=X86_64, kvm=True, distro_family="dnf", in_vergil=True)


class _NoPause:
    def wait(self) -> None:
        return None


def _fake_deps() -> cli.Deps:
    # run_steps is stubbed in the build_boxes tests, so these are never exercised;
    # they exist only to satisfy the typed Deps contract (mirrors test_cli_box).
    return cli.Deps(
        runner=RecordingRunner(results=[]),
        renderer=Renderer(Console(file=io.StringIO(), force_terminal=False, width=80)),
        transcript=Transcript(transcript_path("box-build", "20260716T000000Z")),
        pauser=_NoPause(),
    )


# --------------------------------------------------------------------------- #
# _rhel_dvd_path — env overrides then the state/ default                       #
# --------------------------------------------------------------------------- #
def test_rhel_dvd_path_defaults_to_state(monkeypatch):
    monkeypatch.delenv("MQLAB_RHEL_ISO", raising=False)
    monkeypatch.delenv("RHEL_ISO", raising=False)
    assert box._rhel_dvd_path().name == "rhel-9.6-x86_64-dvd.iso"
    assert box._rhel_dvd_path().parent.name == "state"


def test_rhel_dvd_path_honors_mqlab_env(monkeypatch, tmp_path):
    iso = tmp_path / "custom.iso"
    monkeypatch.setenv("MQLAB_RHEL_ISO", str(iso))
    monkeypatch.delenv("RHEL_ISO", raising=False)
    assert box._rhel_dvd_path() == iso


def test_rhel_dvd_path_honors_rhel_env_fallback(monkeypatch, tmp_path):
    iso = tmp_path / "fallback.iso"
    monkeypatch.delenv("MQLAB_RHEL_ISO", raising=False)
    monkeypatch.setenv("RHEL_ISO", str(iso))
    assert box._rhel_dvd_path() == iso


# --------------------------------------------------------------------------- #
# verify_rhel_dvd — missing / mismatch / unpinned / pass                        #
# --------------------------------------------------------------------------- #
def test_verify_dvd_missing_raises(monkeypatch, tmp_path):
    monkeypatch.setattr(box, "_rhel_dvd_path", lambda: tmp_path / "absent.iso")
    with pytest.raises(typer.Exit) as excinfo:
        box.verify_rhel_dvd("9.6")
    assert excinfo.value.exit_code == 2


def test_verify_dvd_checksum_mismatch_raises(monkeypatch, tmp_path):
    iso = tmp_path / "dvd.iso"
    iso.write_bytes(b"wrong")
    monkeypatch.setattr(box, "_rhel_dvd_path", lambda: iso)
    monkeypatch.setattr(box, "RHEL_DVD_SHA256", {"9.6": "0" * 64})
    with pytest.raises(typer.Exit) as excinfo:
        box.verify_rhel_dvd("9.6")
    assert excinfo.value.exit_code == 2


def test_verify_dvd_unpinned_notices_and_proceeds(monkeypatch, tmp_path, capsys):
    iso = tmp_path / "dvd.iso"
    iso.write_bytes(b"content")
    monkeypatch.setattr(box, "_rhel_dvd_path", lambda: iso)
    monkeypatch.setattr(box, "RHEL_DVD_SHA256", {})  # 9.6 unpinned
    box.verify_rhel_dvd("9.6")  # returns, no raise
    err = capsys.readouterr().err
    assert "not pinned" in err
    assert "not verified" in err


def test_verify_dvd_pass(monkeypatch, tmp_path):
    iso = tmp_path / "dvd.iso"
    iso.write_bytes(b"content")
    monkeypatch.setattr(box, "_rhel_dvd_path", lambda: iso)
    monkeypatch.setattr(box, "RHEL_DVD_SHA256", {"9.6": hashlib.sha256(b"content").hexdigest()})
    box.verify_rhel_dvd("9.6")  # returns, no raise


# --------------------------------------------------------------------------- #
# build_boxes hook — verify for a RHEL BUILD, never for REUSE / non-base        #
# --------------------------------------------------------------------------- #
def _stub_build_env(monkeypatch, verified):
    monkeypatch.setattr(box, "probe", lambda: _FACTS)
    monkeypatch.setattr(box, "ensure_resolved", lambda: None)
    monkeypatch.setattr(box.cli, "build_deps", lambda verb, ts: _fake_deps())
    monkeypatch.setattr(box, "run_steps", lambda steps, **kw: None)
    monkeypatch.setattr(box, "verify_rhel_dvd", lambda version: verified.append(version))


def test_build_boxes_verifies_dvd_on_rhel_force_build(monkeypatch):
    verified: list = []
    _stub_build_env(monkeypatch, verified)
    box.build_boxes([box._BASE_BOX], force=True)
    assert verified == [box._RHEL_VERSION]


def test_build_boxes_verifies_dvd_on_rhel_build_decision(monkeypatch):
    verified: list = []
    _stub_build_env(monkeypatch, verified)
    monkeypatch.setattr(box, "box_decision", lambda name: _decision("BUILD"))
    box.build_boxes([box._BASE_BOX], force=False)
    assert verified == [box._RHEL_VERSION]


def test_build_boxes_skips_dvd_on_rhel_reuse_decision(monkeypatch):
    verified: list = []
    _stub_build_env(monkeypatch, verified)
    monkeypatch.setattr(box, "box_decision", lambda name: _decision("REUSE"))
    box.build_boxes([box._BASE_BOX], force=False)
    assert verified == []


def test_build_boxes_skips_dvd_for_non_base_box(monkeypatch):
    verified: list = []
    _stub_build_env(monkeypatch, verified)
    box.build_boxes(["mq-rdqm-rhel9"], force=True)
    assert verified == []


def _decision(action: str) -> box.BoxDecision:
    return box.BoxDecision(
        name=box._BASE_BOX,
        cached=action == "REUSE",
        age_days=None,
        hash_match=None,
        registered=True,
        action=action,
    )
