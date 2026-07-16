"""box.py fleet model + read-only `mqlab box status` (epic .github#91, T1).

The fleet is DERIVED from cli._LOCAL_BOX_BUILDERS (not a second literal list);
box_decision shells the builders' --dry-run decision surface (single source of
truth) and parses it. Every subprocess seam is monkeypatchable so these unit
tests never touch real git/fs/virsh/vagrant.
"""

from __future__ import annotations

from typer.testing import CliRunner

from mqlab import box, cli
from mqlab.hostfacts import X86_64, HostFacts
from mqlab.runner import Command

runner = CliRunner()

_FACTS = HostFacts(arch=X86_64, kvm=True, distro_family="dnf", in_vergil=True)


# --------------------------------------------------------------------------- #
# Fleet definition                                                            #
# --------------------------------------------------------------------------- #
def test_fleet_has_six_local_boxes():
    assert set(box.FLEET) == {
        "rhel/9.6-x86_64",
        "mq-rdqm-rhel9",
        "obs-ubuntu2404",
        "infra-ubuntu2404",
        "mq-ubuntu2404",
        "mq-nativeha-rhel9",
    }


def test_base_box_has_no_manifest_hash():
    assert box.FLEET["rhel/9.6-x86_64"].has_manifest_hash is False
    assert box.FLEET["mq-rdqm-rhel9"].has_manifest_hash is True


def test_fleet_is_derived_from_cli_builders():
    # DERIVED, not a second literal list: same keys + builder scripts as cli's map.
    assert {name: spec.builder for name, spec in box.FLEET.items()} == cli._LOCAL_BOX_BUILDERS


def test_cache_artifact_names():
    assert box.FLEET["rhel/9.6-x86_64"].cache_artifact == "rhel-9.6-x86_64-libvirt.box"
    assert box.FLEET["mq-rdqm-rhel9"].cache_artifact == "mq-rdqm-rhel9.box"
    assert box.FLEET["obs-ubuntu2404"].cache_artifact == "obs-ubuntu2404.box"


# --------------------------------------------------------------------------- #
# Decision-line parsing                                                       #
# --------------------------------------------------------------------------- #
def test_box_decision_parses_reuse(monkeypatch):
    monkeypatch.setattr(
        box, "_run_builder_dry_run", lambda name: "action: REUSE (age 3d, hash match)"
    )
    monkeypatch.setattr(box, "_cache_present", lambda name: True)
    monkeypatch.setattr(box, "_registered_boxes", lambda: {"mq-rdqm-rhel9": ""})
    d = box.box_decision("mq-rdqm-rhel9")
    assert d.action == "REUSE"
    assert d.registered is True
    assert d.cached is True
    assert d.age_days == 3
    assert d.hash_match is True


def test_box_decision_build_on_hash_mismatch(monkeypatch):
    # decision:  BUILD with a present cache means the stored manifest-hash drifted.
    monkeypatch.setattr(box, "_run_builder_dry_run", lambda name: "decision:  BUILD")
    monkeypatch.setattr(box, "_cache_present", lambda name: True)
    monkeypatch.setattr(box, "_registered_boxes", lambda: {})
    d = box.box_decision("mq-rdqm-rhel9")
    assert d.action == "BUILD"
    assert d.hash_match is False
    assert d.registered is False
    assert d.age_days is None


def test_box_decision_build_on_clean_cache(monkeypatch):
    # BUILD with no cache: nothing to hash-compare -> unknown.
    monkeypatch.setattr(box, "_run_builder_dry_run", lambda name: "decision:  BUILD")
    monkeypatch.setattr(box, "_cache_present", lambda name: False)
    monkeypatch.setattr(box, "_registered_boxes", lambda: {})
    d = box.box_decision("mq-rdqm-rhel9")
    assert d.hash_match is None


def test_box_decision_stale(monkeypatch):
    monkeypatch.setattr(
        box,
        "_run_builder_dry_run",
        lambda name: "decision:  STALE\nERROR: cached box is 20d old (>= 14d)",
    )
    monkeypatch.setattr(box, "_cache_present", lambda name: True)
    monkeypatch.setattr(box, "_registered_boxes", lambda: {"mq-rdqm-rhel9": ""})
    d = box.box_decision("mq-rdqm-rhel9")
    assert d.action == "STALE"
    assert d.hash_match is True
    assert d.age_days == 20


def test_box_decision_force_build(monkeypatch):
    monkeypatch.setattr(box, "_run_builder_dry_run", lambda name: "decision:  FORCE-BUILD")
    monkeypatch.setattr(box, "_cache_present", lambda name: True)
    monkeypatch.setattr(box, "_registered_boxes", lambda: {})
    d = box.box_decision("mq-rdqm-rhel9")
    assert d.action == "FORCE-BUILD"
    assert d.hash_match is None  # not evaluated on a forced rebuild


def test_box_decision_base_box_hash_is_none(monkeypatch):
    monkeypatch.setattr(box, "_run_builder_dry_run", lambda name: "decision:  REUSE")
    monkeypatch.setattr(box, "_cache_present", lambda name: True)
    monkeypatch.setattr(box, "_registered_boxes", lambda: {})
    d = box.box_decision("rhel/9.6-x86_64")
    assert d.hash_match is None  # base box carries no manifest hash


def test_parse_action_unparseable_fails_loud():
    import pytest

    with pytest.raises(ValueError, match="could not parse"):
        box._parse_action("nothing here")


# --------------------------------------------------------------------------- #
# Subprocess seams                                                            #
# --------------------------------------------------------------------------- #
def test_capture_joins_runner_output(monkeypatch):
    class _FakeRunner:
        def run(self, command, on_line):
            on_line("box cache: x")
            on_line("decision:  REUSE")
            return 0

    monkeypatch.setattr(box, "SubprocessRunner", lambda: _FakeRunner())
    assert box._capture(Command(["true"])) == "box cache: x\ndecision:  REUSE"


def test_run_builder_dry_run_fatbox_passes_box_flag(monkeypatch):
    seen: dict[str, list[str]] = {}
    monkeypatch.setattr(box, "probe", lambda: _FACTS)
    monkeypatch.setattr(box, "build_domain_virt", lambda facts: ("kvm", "host-passthrough"))
    monkeypatch.setattr(box, "_capture", lambda cmd: seen.setdefault("argv", cmd.argv) and "")
    box._run_builder_dry_run("mq-rdqm-rhel9")
    argv = seen["argv"]
    assert "--box" in argv and "mq-rdqm-rhel9" in argv
    assert argv[-1] == "--dry-run"
    assert "--domain-type" in argv and "kvm" in argv


def test_run_builder_dry_run_base_box_has_no_box_flag(monkeypatch):
    seen: dict[str, list[str]] = {}
    monkeypatch.setattr(box, "probe", lambda: _FACTS)
    monkeypatch.setattr(box, "build_domain_virt", lambda facts: ("qemu", "maximum"))
    monkeypatch.setattr(box, "_capture", lambda cmd: seen.setdefault("argv", cmd.argv) and "")
    box._run_builder_dry_run("rhel/9.6-x86_64")
    assert "--box" not in seen["argv"]


def test_registered_boxes_parses_vagrant_list(monkeypatch):
    monkeypatch.setattr(
        box, "_capture", lambda cmd: "mq-rdqm-rhel9 (libvirt, 0)\nobs-ubuntu2404 (libvirt, 0)"
    )
    reg = box._registered_boxes()
    assert set(reg) == {"mq-rdqm-rhel9", "obs-ubuntu2404"}


def test_cache_present(monkeypatch, tmp_path):
    monkeypatch.setattr(box, "_boxes_cache_dir", lambda: tmp_path)
    assert box._cache_present("mq-rdqm-rhel9") is False
    (tmp_path / "mq-rdqm-rhel9.box").write_text("x")
    assert box._cache_present("mq-rdqm-rhel9") is True


def test_boxes_cache_dir_is_state_boxes():
    assert box._boxes_cache_dir().name == "boxes"


# --------------------------------------------------------------------------- #
# render_status                                                               #
# --------------------------------------------------------------------------- #
def _canned(
    *,
    name: str = "mq-rdqm-rhel9",
    cached: bool = True,
    age_days: int | None = 3,
    hash_match: bool | None = True,
    registered: bool = True,
    action: str = "REUSE",
) -> box.BoxDecision:
    return box.BoxDecision(
        name=name,
        cached=cached,
        age_days=age_days,
        hash_match=hash_match,
        registered=registered,
        action=action,
    )


def test_render_status_covers_every_cell(monkeypatch):
    decisions = {
        "rhel/9.6-x86_64": _canned(
            name="rhel/9.6-x86_64", hash_match=None, action="REUSE", age_days=None
        ),
        "mq-rdqm-rhel9": _canned(action="BUILD", hash_match=False, cached=True, registered=False),
        "mq-ubuntu2404": _canned(
            name="mq-ubuntu2404", action="BUILD", hash_match=None, cached=False, registered=False
        ),
    }
    monkeypatch.setattr(box, "box_decision", lambda name: decisions[name])
    out = box.render_status(list(decisions))
    assert "BOX" in out and "DECISION" in out
    assert "rhel/9.6-x86_64" in out
    assert "N/A" in out  # base box hash column
    assert "mismatch" in out  # fat box BUILD + cache present
    assert "3d" in out  # age rendered
    assert "-" in out  # unknown age / unknown hash rendered as dash


# --------------------------------------------------------------------------- #
# `mqlab box status` verb                                                     #
# --------------------------------------------------------------------------- #
def test_box_status_defaults_to_whole_fleet(monkeypatch):
    captured: dict[str, list[str]] = {}
    monkeypatch.setattr(
        cli.box, "render_status", lambda names: captured.setdefault("names", names) and "TABLE"
    )
    result = runner.invoke(cli.app, ["box", "status"])
    assert result.exit_code == 0
    assert captured["names"] == list(box.FLEET)
    assert "TABLE" in result.stdout


def test_box_status_explicit_boxes(monkeypatch):
    captured: dict[str, list[str]] = {}
    monkeypatch.setattr(
        cli.box, "render_status", lambda names: captured.setdefault("names", names) and "T"
    )
    result = runner.invoke(cli.app, ["box", "status", "mq-rdqm-rhel9"])
    assert result.exit_code == 0
    assert captured["names"] == ["mq-rdqm-rhel9"]
