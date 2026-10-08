"""RHEL DVD verify-and-guide at the base-box build path (epic .github#91, T4).

`box.verify_rhel_dvd` preflights the operator-supplied RHEL DVD before the
expensive base-box BUILD: MISSING file blocks, a pinned-but-MISMATCHED checksum
blocks, and an UNPINNED version emits a loud NOTICE and PROCEEDS (so we add the
integrity check without regressing the pre-existing no-SHA cold rebuild). The
fixture ISO carries a *computed* sha256 — the mechanism, never a real DVD hash.
Every seam is monkeypatchable so these unit tests never touch a real ISO.
"""

from __future__ import annotations

import dataclasses
import hashlib
import io

import pytest
import typer
from rich.console import Console

from mqlab import box, cli
from mqlab.hostfacts import X86_64, HostFacts
from mqlab.render import Renderer
from mqlab.transcript import Transcript, transcript_path
from tests.boxfleet import x86_fleet
from tests.fakes import RecordingRunner

_FACTS = HostFacts(arch=X86_64, kvm=True, distro_family="dnf", in_vergil=True, x86_64_v3=True)
_BASE_BOX = "rhel/9-x86_64"
_ENTRY = x86_fleet()[_BASE_BOX].os  # the catalog's os.rhel.9 entry
_ISO = str(_ENTRY.iso)


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
    assert box._rhel_dvd_path(_ISO).name == _ISO
    assert box._rhel_dvd_path(_ISO).parent.name == "state"


def test_rhel_dvd_path_honors_mqlab_env(monkeypatch, tmp_path):
    iso = tmp_path / "custom.iso"
    monkeypatch.setenv("MQLAB_RHEL_ISO", str(iso))
    monkeypatch.delenv("RHEL_ISO", raising=False)
    assert box._rhel_dvd_path(_ISO) == iso


def test_rhel_dvd_path_honors_rhel_env_fallback(monkeypatch, tmp_path):
    iso = tmp_path / "fallback.iso"
    monkeypatch.delenv("MQLAB_RHEL_ISO", raising=False)
    monkeypatch.setenv("RHEL_ISO", str(iso))
    assert box._rhel_dvd_path(_ISO) == iso


# --------------------------------------------------------------------------- #
# verify_rhel_dvd — missing / mismatch / unpinned / pass                        #
# --------------------------------------------------------------------------- #
def test_verify_dvd_missing_raises(monkeypatch, tmp_path):
    monkeypatch.setattr(box, "_rhel_dvd_path", lambda name: tmp_path / "absent.iso")
    with pytest.raises(typer.Exit) as excinfo:
        box.verify_rhel_dvd(_ENTRY)
    assert excinfo.value.exit_code == 2


def test_verify_dvd_checksum_mismatch_raises(monkeypatch, tmp_path):
    iso = tmp_path / "dvd.iso"
    iso.write_bytes(b"wrong")
    monkeypatch.setattr(box, "_rhel_dvd_path", lambda name: iso)
    monkeypatch.setattr(box, "RHEL_DVD_SHA256", {_ENTRY.point: "0" * 64})
    with pytest.raises(typer.Exit) as excinfo:
        box.verify_rhel_dvd(_ENTRY)
    assert excinfo.value.exit_code == 2


def test_verify_dvd_unpinned_notices_and_proceeds(monkeypatch, tmp_path, capsys):
    iso = tmp_path / "dvd.iso"
    iso.write_bytes(b"content")
    monkeypatch.setattr(box, "_rhel_dvd_path", lambda name: iso)
    monkeypatch.setattr(box, "RHEL_DVD_SHA256", {})  # unpinned
    box.verify_rhel_dvd(_ENTRY)  # returns, no raise
    err = capsys.readouterr().err
    assert "not pinned" in err
    assert "not verified" in err


def test_verify_dvd_pass(monkeypatch, tmp_path):
    iso = tmp_path / "dvd.iso"
    iso.write_bytes(b"content")
    monkeypatch.setattr(box, "_rhel_dvd_path", lambda name: iso)
    monkeypatch.setattr(
        box, "RHEL_DVD_SHA256", {_ENTRY.point: hashlib.sha256(b"content").hexdigest()}
    )
    box.verify_rhel_dvd(_ENTRY)  # returns, no raise


# --------------------------------------------------------------------------- #
# build_boxes hook — verify for a RHEL BUILD, never for REUSE / non-base        #
# --------------------------------------------------------------------------- #
def _stub_build_env(monkeypatch, verified):
    monkeypatch.setattr(box, "probe", lambda: _FACTS)
    monkeypatch.setattr(box, "ensure_resolved", lambda: None)
    monkeypatch.setattr(box.cli, "build_deps", lambda verb, ts: _fake_deps())
    monkeypatch.setattr(box, "run_steps", lambda steps, **kw: None)
    monkeypatch.setattr(box, "verify_rhel_dvd", lambda entry: verified.append(entry))
    # A fat box that bakes guest components (epic .github#294) would run a REAL
    # `mqlab component build` (git + uv + network) before baking; that seam has its own
    # tests (test_box_components.py). Here it is part of the faked build environment.
    monkeypatch.setattr(box, "_ensure_component_artifacts", lambda plan, facts: None)


def test_build_boxes_verifies_dvd_on_rhel_force_build(monkeypatch):
    verified: list = []
    _stub_build_env(monkeypatch, verified)
    box.build_boxes([_BASE_BOX], force=True)
    assert verified == [_ENTRY]


def test_build_boxes_verifies_dvd_on_rhel_build_decision(monkeypatch):
    verified: list = []
    _stub_build_env(monkeypatch, verified)
    monkeypatch.setattr(box, "box_decision", lambda name: _decision("BUILD"))
    box.build_boxes([_BASE_BOX], force=False)
    assert verified == [_ENTRY]


def test_build_boxes_skips_dvd_on_rhel_reuse_decision(monkeypatch):
    verified: list = []
    _stub_build_env(monkeypatch, verified)
    monkeypatch.setattr(box, "box_decision", lambda name: _decision("REUSE"))
    box.build_boxes([_BASE_BOX], force=False)
    assert verified == []


def test_build_boxes_ensures_exporter_binary_for_exporter_box(monkeypatch, tmp_path):
    # An obs/mq-ubuntu bake needs the prebuilt mq_prometheus, so build_boxes ensures
    # it (in the Go container) before baking — #1065.
    verified: list = []
    _stub_build_env(monkeypatch, verified)
    ensured: list = []
    monkeypatch.setattr(box, "cache", lambda *parts: tmp_path)
    monkeypatch.setattr(
        box.mqexporter, "ensure_mq_exporter_binary", lambda root: ensured.append(root)
    )
    obs = next(n for n, s in x86_fleet().items() if s.role == "obs")
    box.build_boxes([obs], force=True)
    assert ensured == [tmp_path]


def test_build_boxes_skips_dvd_for_non_base_box(monkeypatch):
    # A RHEL fat box pulls in its base box as a dependency (ensured, never forced): with
    # the base cached (REUSE) no DVD is consumed, and the fat box itself never needs one.
    verified: list = []
    _stub_build_env(monkeypatch, verified)
    monkeypatch.setattr(box, "box_decision", lambda name: _decision("REUSE"))
    box.build_boxes(["mq-rdqm-rhel9"], force=True)
    assert verified == []


def test_build_boxes_verifies_dvd_when_a_fat_box_needs_an_uncached_base(monkeypatch):
    verified: list = []
    _stub_build_env(monkeypatch, verified)
    monkeypatch.setattr(box, "box_decision", lambda name: _decision("BUILD"))
    box.build_boxes(["mq-rdqm-rhel9"], force=False)
    assert verified == [_ENTRY]


def test_verify_dvd_refuses_an_entry_without_point_or_iso():
    broken = dataclasses.replace(_ENTRY, point=None)
    with pytest.raises(box.VersionError, match="needs point and iso"):
        box.verify_rhel_dvd(broken)


def _decision(action: str) -> box.BoxDecision:
    return box.BoxDecision(
        name=_BASE_BOX,
        cached=action == "REUSE",
        age_days=None,
        hash_match=None,
        registered=True,
        action=action,
    )
