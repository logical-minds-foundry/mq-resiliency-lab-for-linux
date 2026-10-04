"""box.py fleet model + read-only `mqlab box status` (epic .github#91, T1).

The fleet is DERIVED from cli._LOCAL_BOX_BUILDERS (not a second literal list);
box_decision shells the builders' --dry-run decision surface (single source of
truth) and parses it. Every subprocess seam is monkeypatchable so these unit
tests never touch real git/fs/virsh/vagrant.
"""

from __future__ import annotations

import dataclasses
import io
import os

import pytest
import typer
from rich.console import Console
from typer.testing import CliRunner

from mqlab import box, cli
from mqlab.box import (  # captured before conftest's _neutralize_box_gc autouse stub
    gc_orphaned_images_best_effort as _real_gc_best_effort,
)
from mqlab.hostfacts import AARCH64, X86_64, HostFacts
from mqlab.orchestrator import StepFailedError
from mqlab.render import Renderer
from mqlab.retired_boxes import RETIRED_BOX_NAMES
from mqlab.runner import Command
from mqlab.transcript import Transcript, transcript_path
from mqlab.versions import BuildFile, OsRef, VersionError, load_catalog
from tests.fakes import RecordingRunner

runner = CliRunner()

_FACTS = HostFacts(arch=X86_64, kvm=True, distro_family="dnf", in_vergil=True)


class _NoPause:
    def wait(self) -> None:
        return None


def _fake_deps() -> cli.Deps:
    # run_steps is stubbed in the build_boxes tests, so these are never exercised;
    # they exist only to satisfy the typed Deps contract (mirrors test_cli_vm._deps).
    return cli.Deps(
        runner=RecordingRunner(results=[]),
        renderer=Renderer(Console(file=io.StringIO(), force_terminal=False, width=80)),
        transcript=Transcript(transcript_path("box-build", "20260716T000000Z")),
        pauser=_NoPause(),
    )


# --------------------------------------------------------------------------- #
# Fleet definition                                                            #
# --------------------------------------------------------------------------- #
def test_fleet_has_the_catalog_boxes():
    # Plan T4 (#1274): the fleet is generated from the catalog as <role>-<os><major>, plus
    # one RHEL base box per catalog RHEL major. The log-search stack was folded into obs
    # (#1178) and the standalone logsearch box was retired (#1179, epic .github#267).
    assert set(box.FLEET) == {
        "rhel/9-x86_64",
        "mq-rdqm-rhel9",
        "obs-ubuntu24",
        "infra-ubuntu24",
        "mq-client-ubuntu24",
        "san-ubuntu24",
        "mq-nativeha-rhel9",
        "mq-nativeha-ubuntu24",
        "pcmk-ubuntu24",
    }


def test_fleet_derives_from_catalog():
    # Plan T4 Step 1: every generated name is present on an x86 host, and no retired
    # name survives.
    names = set(box._build_fleet(facts=_FACTS))
    assert {
        "mq-nativeha-ubuntu24",
        "mq-client-ubuntu24",
        "san-ubuntu24",
        "infra-ubuntu24",
        "obs-ubuntu24",
        "pcmk-ubuntu24",
        "mq-rdqm-rhel9",
        "mq-nativeha-rhel9",
        "rhel/9-x86_64",
    } <= names
    assert not names & set(RETIRED_BOX_NAMES)


def test_fleet_skips_rhel_on_an_arm_host():
    # Catalog.all_boxes skips an OS the host cannot run (RHEL is x86_64-pinned), and so
    # does the base box: the arm64 fleet is the Ubuntu boxes only.
    arm = HostFacts(arch=AARCH64, kvm=True, distro_family="apt", in_vergil=True)
    fleet = box._build_fleet(arm)
    assert set(fleet) == {
        "obs-ubuntu24",
        "infra-ubuntu24",
        "mq-client-ubuntu24",
        "san-ubuntu24",
        "mq-nativeha-ubuntu24",
        "pcmk-ubuntu24",
    }


def test_fleet_specs_carry_the_catalog_inputs():
    spec = box.FLEET["mq-client-ubuntu24"]
    assert (spec.role, spec.bake_stem, spec.mq_bearing) == ("mq-client", "mq-ubuntu", True)
    assert box.FLEET["pcmk-ubuntu24"].mq_bearing is False  # pcmk is deliberately not
    assert box.FLEET["rhel/9-x86_64"].role is None


def test_logsearch_box_retired_from_fleet():
    # The log-search tier now rides the obs box (#1179): no standalone box in the fleet.
    assert "logsearch-ubuntu2404" not in box.FLEET


def test_base_box_has_no_manifest_hash():
    assert box.FLEET["rhel/9-x86_64"].has_manifest_hash is False
    assert box.FLEET["mq-rdqm-rhel9"].has_manifest_hash is True


def test_cache_artifact_names():
    # Cache filenames are uniformly arch-suffixed (#103 D4); the base box's follows its
    # name ('/' -> '-'), which already carries the arch.
    assert box.FLEET["rhel/9-x86_64"].cache_artifact == "rhel-9-x86_64.box"
    assert box.FLEET["mq-rdqm-rhel9"].cache_artifact == "mq-rdqm-rhel9-x86_64.box"
    assert box.FLEET["mq-nativeha-rhel9"].cache_artifact == "mq-nativeha-rhel9-x86_64.box"


def test_os_pin_uses_point_then_box_version_then_none():
    rhel = box.FLEET["rhel/9-x86_64"].os
    ubuntu = box.FLEET["obs-ubuntu24"].os
    assert box.os_pin(rhel) == f"rhel/9-x86_64@{rhel.point}"
    assert box.os_pin(ubuntu) == f"{ubuntu.base_box}@{ubuntu.base_box_version}"
    floating = dataclasses.replace(ubuntu, base_box_version=None)
    assert box.os_pin(floating) == f"{ubuntu.base_box}@none"


# --------------------------------------------------------------------------- #
# _build_fleet arch derivation (#103 D4/D5, T4)                               #
# --------------------------------------------------------------------------- #
def test_build_fleet_fat_boxes_carry_arch_x86():
    fleet = box._build_fleet(_FACTS)
    assert fleet["mq-client-ubuntu24"].arch == "x86_64"
    assert fleet["mq-client-ubuntu24"].cache_artifact == "mq-client-ubuntu24-x86_64.box"
    assert fleet["mq-rdqm-rhel9"].arch == "x86_64"
    assert fleet["mq-rdqm-rhel9"].cache_artifact == "mq-rdqm-rhel9-x86_64.box"
    assert fleet["rhel/9-x86_64"].arch == "x86_64"


def test_build_fleet_unpinned_ubuntu_tracks_host_arm64():
    facts = HostFacts(arch=AARCH64, kvm=True, distro_family="apt", in_vergil=True)
    fleet = box._build_fleet(facts)
    # Un-pinned Ubuntu fat box tracks the host arch (arm64 on Apple Silicon).
    assert fleet["mq-client-ubuntu24"].arch == "aarch64"
    assert fleet["mq-client-ubuntu24"].cache_artifact == "mq-client-ubuntu24-aarch64.box"


def test_manifest_hash_artifact_is_arch_suffixed():
    facts = HostFacts(arch=AARCH64, kvm=True, distro_family="apt", in_vergil=True)
    fleet = box._build_fleet(facts)
    assert (
        fleet["mq-client-ubuntu24"].manifest_hash_artifact
        == "mq-client-ubuntu24-aarch64.manifest-hash"
    )


# --------------------------------------------------------------------------- #
# Interim stack roles (until T2) + boxes_for_build (`box build --config`)      #
# --------------------------------------------------------------------------- #
def test_topology_stack_roles_derive_from_node_box_roles():
    roles = box._topology_stack_roles(load_catalog())
    assert roles == {
        "pcmk-ubuntu": {"pcmk"},  # the SAN targets are `box: san`, an infra role
        "rdqm-rhel": {"mq-rdqm"},
        "nativeha-rhel-crr": {"mq-nativeha"},
        "nativeha-ubuntu": {"mq-nativeha"},
    }


def test_topology_stack_roles_refuse_an_unknown_role(monkeypatch):
    topo = {
        "nodes": {"x1": {"box": "mystery"}},
        "stacks": {"s": {"groups": ["g"]}},
        "groups": {"g": ["x1"]},
    }
    monkeypatch.setattr(box, "_load_topology", lambda: topo)
    with pytest.raises(VersionError, match="box role 'mystery'"):
        box._topology_stack_roles(load_catalog())


def test_topology_stack_roles_drop_infra_roles(monkeypatch):
    topo = {
        "nodes": {"x1": {"box": "obs"}, "x2": {"box": "pcmk"}, "x3": {"box": "san"}},
        "stacks": {"s": {"groups": ["g"]}},
        "groups": {"g": ["x1", "x2", "x3"]},
    }
    monkeypatch.setattr(box, "_load_topology", lambda: topo)
    assert box._topology_stack_roles(load_catalog()) == {"s": {"pcmk"}}


def test_boxes_for_build_default_covers_every_stack():
    names = box.boxes_for_build(BuildFile(os=None), facts=_FACTS)
    assert names == [
        "infra-ubuntu24",
        "obs-ubuntu24",
        "mq-client-ubuntu24",
        "san-ubuntu24",
        "pcmk-ubuntu24",
        "mq-rdqm-rhel9",
        "mq-nativeha-rhel9",
        "mq-nativeha-ubuntu24",
    ]


def test_boxes_for_build_family_selects_its_stacks():
    names = box.boxes_for_build(BuildFile(os=OsRef("rhel", 9)), facts=_FACTS)
    assert names == [
        "infra-ubuntu24",
        "obs-ubuntu24",
        "mq-client-ubuntu24",
        "san-ubuntu24",
        "mq-rdqm-rhel9",
        "mq-nativeha-rhel9",
    ]


def test_boxes_for_build_default_skips_what_the_host_cannot_run():
    arm = HostFacts(arch=AARCH64, kvm=True, distro_family="apt", in_vergil=True)
    names = box.boxes_for_build(BuildFile(os=None), facts=arm)
    assert "mq-rdqm-rhel9" not in names
    assert "mq-nativeha-ubuntu24" in names


def test_boxes_for_build_refuses_an_explicit_request_the_host_cannot_run():
    arm = HostFacts(arch=AARCH64, kvm=True, distro_family="apt", in_vergil=True)
    with pytest.raises(VersionError, match="needs an x86_64 host"):
        box.boxes_for_build(BuildFile(os=OsRef("rhel", 9)), facts=arm)


def test_boxes_for_build_refuses_an_unsupported_major():
    with pytest.raises(VersionError, match="supports"):
        box.boxes_for_build(BuildFile(os=OsRef("ubuntu", 26)), facts=_FACTS)


def test_boxes_for_build_refuses_a_stack_missing_from_the_catalog():
    with pytest.raises(VersionError, match="unknown stack 'ghost'"):
        box.boxes_for_build(BuildFile(os=None), facts=_FACTS, stack_roles={"ghost": {"pcmk"}})


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


def test_dry_run_passes_arch_for_fat_box(monkeypatch):
    # Regression (#731): the dry-run must supply --arch, which build-fatbox.sh
    # requires post-#701 — else it usage-dies and box_decision cannot parse.
    # Assert against FLEET[name].arch (same host) so this holds on x86 CI and arm64.
    captured = {}

    def _fake_capture(cmd):
        captured["argv"] = cmd.argv
        return "action: REUSE (age 1d, hash match)"

    monkeypatch.setattr(box, "_capture", _fake_capture)
    box._run_builder_dry_run("mq-nativeha-ubuntu24")
    argv = captured["argv"]
    assert "--arch" in argv
    assert argv[argv.index("--arch") + 1] == box.FLEET["mq-nativeha-ubuntu24"].arch


def test_box_decision_base_box_hash_is_none(monkeypatch):
    monkeypatch.setattr(box, "_run_builder_dry_run", lambda name: "decision:  REUSE")
    monkeypatch.setattr(box, "_cache_present", lambda name: True)
    monkeypatch.setattr(box, "_registered_boxes", lambda: {})
    d = box.box_decision("rhel/9-x86_64")
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
    monkeypatch.setattr(box, "_capture", lambda cmd: seen.setdefault("argv", cmd.argv) and "")
    box._run_builder_dry_run("mq-rdqm-rhel9")
    argv = seen["argv"]
    assert argv[argv.index("--box") + 1] == "mq-rdqm-rhel9"
    assert argv[argv.index("--bake") + 1] == "mq-rdqm"
    assert argv[-1] == "--dry-run"
    assert argv[argv.index("--domain-type") + 1] == "kvm"


def test_run_builder_dry_run_base_box_has_no_box_flag(monkeypatch):
    seen: dict[str, list[str]] = {}
    monkeypatch.setattr(box, "probe", lambda: _FACTS)
    monkeypatch.setattr(box, "_capture", lambda cmd: seen.setdefault("argv", cmd.argv) and "")
    box._run_builder_dry_run("rhel/9-x86_64")
    assert "--box" not in seen["argv"]
    assert seen["argv"][seen["argv"].index("--major") + 1] == "9"


def test_registered_boxes_parses_vagrant_list(monkeypatch):
    monkeypatch.setattr(
        box, "_capture", lambda cmd: "mq-rdqm-rhel9 (libvirt, 0)\nobs-ubuntu24 (libvirt, 0)"
    )
    reg = box._registered_boxes()
    assert set(reg) == {"mq-rdqm-rhel9", "obs-ubuntu24"}


def test_cache_present(monkeypatch, tmp_path):
    monkeypatch.setattr(box, "_boxes_cache_dir", lambda: tmp_path)
    assert box._cache_present("mq-rdqm-rhel9") is False
    (tmp_path / "mq-rdqm-rhel9-x86_64.box").write_text("x")
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
        "rhel/9-x86_64": _canned(
            name="rhel/9-x86_64", hash_match=None, action="REUSE", age_days=None
        ),
        "mq-rdqm-rhel9": _canned(action="BUILD", hash_match=False, cached=True, registered=False),
        "mq-client-ubuntu24": _canned(
            name="mq-client-ubuntu24",
            action="BUILD",
            hash_match=None,
            cached=False,
            registered=False,
        ),
    }
    monkeypatch.setattr(box, "box_decision", lambda name: decisions[name])
    out = box.render_status(list(decisions))
    assert "BOX" in out and "DECISION" in out
    assert "ARCH" in out  # the arch column header (#103 T4)
    assert "aarch64" in out or "x86_64" in out  # a rendered arch cell from FLEET
    assert "rhel/9-x86_64" in out
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


# --------------------------------------------------------------------------- #
# build_boxes core (epic .github#91, T2)                                       #
# --------------------------------------------------------------------------- #
def _reuse(name: str) -> box.BoxDecision:
    return box.BoxDecision(
        name=name, cached=True, age_days=None, hash_match=None, registered=True, action="REUSE"
    )


def _stub_build_env(monkeypatch, steps_sink):
    monkeypatch.setattr(box, "probe", lambda: _FACTS)
    monkeypatch.setattr(box, "ensure_resolved", lambda: None)
    monkeypatch.setattr(box, "box_decision", _reuse)  # the base box dep: cached, no DVD
    monkeypatch.setattr(box.cli, "build_deps", lambda verb, ts: _fake_deps())
    monkeypatch.setattr(box, "run_steps", lambda steps, **kw: steps_sink.extend(steps))


def test_build_boxes_renders_resolved_topology(monkeypatch):
    # #737: box build must render build/work/lab/topology.resolved.yaml before the
    # builder's final `vagrant box add` loads the Vagrantfile (which guards on it) —
    # standalone `box build` otherwise dies at box registration on a fresh checkout.
    called: list = []
    monkeypatch.setattr(box, "probe", lambda: _FACTS)
    monkeypatch.setattr(box, "ensure_resolved", lambda: called.append(True))
    monkeypatch.setattr(box, "box_decision", _reuse)
    monkeypatch.setattr(box.cli, "build_deps", lambda verb, ts: _fake_deps())
    monkeypatch.setattr(box, "run_steps", lambda steps, **kw: None)
    box.build_boxes(["mq-rdqm-rhel9"], force=False)
    assert called == [True]


def test_build_boxes_force_adds_rebuild_flag(monkeypatch):
    captured: list = []
    _stub_build_env(monkeypatch, captured)
    box.build_boxes(["mq-rdqm-rhel9"], force=True)
    base, fat = (step.command.argv for step in captured)
    assert "--rebuild-box" not in base  # the base box dependency is ensured, never forced
    assert "--box" in fat and "mq-rdqm-rhel9" in fat and "--rebuild-box" in fat


def test_build_passes_catalog_flags(monkeypatch):
    # Plan T4 Step 1: the builder argv carries the catalog's base box, bake stem and OS
    # pin (read from the catalog, never a hard-coded version).
    captured: list = []
    _stub_build_env(monkeypatch, captured)
    box.build_boxes(["mq-nativeha-ubuntu24"], force=False)
    argv = captured[-1].command.argv
    ubuntu = load_catalog().oses[OsRef("ubuntu", 24)]
    assert argv[argv.index("--base-box") + 1] == ubuntu.base_box
    assert argv[argv.index("--bake") + 1] == "nativeha-ubuntu"
    assert argv[argv.index("--os-pin") + 1] == f"{ubuntu.base_box}@{ubuntu.base_box_version}"


def test_build_boxes_non_force_omits_rebuild_flag(monkeypatch):
    captured: list = []
    _stub_build_env(monkeypatch, captured)
    box.build_boxes(["mq-rdqm-rhel9"], force=False)
    argv = captured[-1].command.argv
    assert "--box" in argv and "mq-rdqm-rhel9" in argv
    assert "--rebuild-box" not in argv


def test_build_boxes_raises_typer_exit_on_step_failure(monkeypatch):
    monkeypatch.setattr(box, "probe", lambda: _FACTS)
    monkeypatch.setattr(box, "ensure_resolved", lambda: None)
    monkeypatch.setattr(box, "box_decision", _reuse)
    monkeypatch.setattr(box.cli, "build_deps", lambda verb, ts: _fake_deps())

    def _boom(steps, **kw):
        raise StepFailedError("box mq-rdqm-rhel9", 7)

    monkeypatch.setattr(box, "run_steps", _boom)
    with pytest.raises(typer.Exit) as excinfo:
        box.build_boxes(["mq-rdqm-rhel9"], force=False)
    assert excinfo.value.exit_code == 7


# --------------------------------------------------------------------------- #
# _select_boxes + `box build`/`box rebuild` verbs                              #
# --------------------------------------------------------------------------- #
def test_select_boxes_all_returns_whole_fleet():
    assert cli._select_boxes(None, all_=True) == list(box.FLEET)
    # --all wins even when names are also given (a superset request).
    assert cli._select_boxes(["mq-rdqm-rhel9"], all_=True) == list(box.FLEET)


def test_select_boxes_explicit_validated():
    assert cli._select_boxes(["mq-rdqm-rhel9", "obs-ubuntu24"], all_=False) == [
        "mq-rdqm-rhel9",
        "obs-ubuntu24",
    ]


def test_select_boxes_none_without_all_exits_2():
    with pytest.raises(typer.Exit) as excinfo:
        cli._select_boxes(None, all_=False)
    assert excinfo.value.exit_code == 2


def test_select_boxes_unknown_name_exits_2():
    with pytest.raises(typer.Exit) as excinfo:
        cli._select_boxes(["no-such-box"], all_=False)
    assert excinfo.value.exit_code == 2


def test_box_build_verb_issues_non_force(monkeypatch):
    calls: dict = {}
    monkeypatch.setattr(
        cli.box, "build_boxes", lambda names, *, force: calls.update(names=names, force=force)
    )
    result = runner.invoke(cli.app, ["box", "build", "mq-rdqm-rhel9"])
    assert result.exit_code == 0
    assert calls == {"names": ["mq-rdqm-rhel9"], "force": False}


def test_box_rebuild_verb_issues_force(monkeypatch):
    calls: dict = {}
    monkeypatch.setattr(
        cli.box, "build_boxes", lambda names, *, force: calls.update(names=names, force=force)
    )
    result = runner.invoke(cli.app, ["box", "rebuild", "mq-rdqm-rhel9"])
    assert result.exit_code == 0
    assert calls == {"names": ["mq-rdqm-rhel9"], "force": True}


def test_box_build_all_targets_whole_fleet(monkeypatch):
    calls: dict = {}
    monkeypatch.setattr(
        cli.box, "build_boxes", lambda names, *, force: calls.update(names=names, force=force)
    )
    result = runner.invoke(cli.app, ["box", "build", "--all"])
    assert result.exit_code == 0
    assert calls == {"names": list(box.FLEET), "force": False}


def test_box_build_no_selection_exits_2(monkeypatch):
    monkeypatch.setattr(
        cli.box, "build_boxes", lambda names, *, force: pytest.fail("must not build")
    )
    result = runner.invoke(cli.app, ["box", "build"])
    assert result.exit_code == 2


# --- cold-boot staleness nudge prepended to the status header (epic .github#91 T6) ---
def test_box_status_prepends_cold_boot_notice(monkeypatch):
    monkeypatch.setattr(cli.box, "render_status", lambda names: "BOX  ...\nTABLE")
    monkeypatch.setattr(cli.coldboot, "nudge", lambda: "NOTICE: this box is 40 days old.")
    result = runner.invoke(cli.app, ["box", "status"])
    assert result.exit_code == 0  # advisory only — never blocks
    # the NOTICE is prepended, before the table header
    assert result.stdout.index("NOTICE") < result.stdout.index("TABLE")


def test_box_status_silent_when_no_cold_boot_nudge(monkeypatch):
    monkeypatch.setattr(cli.box, "render_status", lambda names: "TABLE")
    monkeypatch.setattr(cli.coldboot, "nudge", lambda: None)
    result = runner.invoke(cli.app, ["box", "status"])
    assert result.exit_code == 0
    assert "NOTICE" not in result.stdout
    assert "TABLE" in result.stdout


# --------------------------------------------------------------------------- #
# `box clean` — pristine cache removal + deregister (epic .github#91, T3)       #
# --------------------------------------------------------------------------- #
def test_clean_removes_cache_and_deregisters(monkeypatch, tmp_path):
    # Both the cache artifact and its manifest-hash are arch-suffixed (#103 D4).
    (tmp_path / "mq-rdqm-rhel9-x86_64.box").write_text("x")
    (tmp_path / "mq-rdqm-rhel9-x86_64.manifest-hash").write_text("h")
    monkeypatch.setattr(box, "_boxes_cache_dir", lambda: tmp_path)
    removed_regs: list[str] = []
    monkeypatch.setattr(box, "_vagrant_box_remove", lambda n: removed_regs.append(n))
    removed = box.clean_boxes(["mq-rdqm-rhel9"])
    assert not (tmp_path / "mq-rdqm-rhel9-x86_64.box").exists()
    assert not (tmp_path / "mq-rdqm-rhel9-x86_64.manifest-hash").exists()
    assert removed_regs == ["mq-rdqm-rhel9"]
    # the removed report names both files and the deregistration.
    assert str(tmp_path / "mq-rdqm-rhel9-x86_64.box") in removed
    assert str(tmp_path / "mq-rdqm-rhel9-x86_64.manifest-hash") in removed
    assert any("mq-rdqm-rhel9" in item and "vagrant" in item for item in removed)


def test_clean_skips_absent_artifacts(monkeypatch, tmp_path):
    # A fat box with NEITHER file present: nothing to unlink, but it is still
    # deregistered and reported (idempotent). Covers the absent-file branches.
    monkeypatch.setattr(box, "_boxes_cache_dir", lambda: tmp_path)
    removed_regs: list[str] = []
    monkeypatch.setattr(box, "_vagrant_box_remove", lambda n: removed_regs.append(n))
    removed = box.clean_boxes(["obs-ubuntu24"])
    assert removed_regs == ["obs-ubuntu24"]
    assert removed == ["vagrant box 'obs-ubuntu24'"]


def test_clean_base_box_has_no_manifest_hash(monkeypatch, tmp_path):
    # The base box carries no manifest-hash: only its .box artifact is removed;
    # a stray same-stem file is left untouched. Covers has_manifest_hash False.
    (tmp_path / "rhel-9-x86_64.box").write_text("x")
    stray = tmp_path / "rhel/9-x86_64.manifest-hash"
    monkeypatch.setattr(box, "_boxes_cache_dir", lambda: tmp_path)
    monkeypatch.setattr(box, "_vagrant_box_remove", lambda n: None)
    removed = box.clean_boxes(["rhel/9-x86_64"])
    assert not (tmp_path / "rhel-9-x86_64.box").exists()
    assert not stray.exists()  # never created — manifest-hash path skipped
    assert str(tmp_path / "rhel-9-x86_64.box") in removed


def test_vagrant_box_remove_success(monkeypatch):
    class _FakeRunner:
        def run(self, command, on_line):
            on_line("Removing box 'mq-rdqm-rhel9'")
            return 0

    monkeypatch.setattr(box, "SubprocessRunner", lambda: _FakeRunner())
    box._vagrant_box_remove("mq-rdqm-rhel9")  # returns, no raise


def test_vagrant_box_remove_not_installed_is_swallowed(monkeypatch):
    class _FakeRunner:
        def run(self, command, on_line):
            on_line("Box 'mq-rdqm-rhel9' with provider 'libvirt' is not installed!")
            return 1

    monkeypatch.setattr(box, "SubprocessRunner", lambda: _FakeRunner())
    box._vagrant_box_remove("mq-rdqm-rhel9")  # idempotent: swallowed, no raise


def test_vagrant_box_remove_other_error_fails_loud(monkeypatch):
    class _FakeRunner:
        def run(self, command, on_line):
            on_line("some other vagrant failure")
            return 3

    monkeypatch.setattr(box, "SubprocessRunner", lambda: _FakeRunner())
    with pytest.raises(typer.Exit) as excinfo:
        box._vagrant_box_remove("mq-rdqm-rhel9")
    assert excinfo.value.exit_code == 3


def test_box_clean_all_without_flag_exits_2_and_removes_nothing(monkeypatch):
    monkeypatch.setattr(
        cli.box, "clean_boxes", lambda names: pytest.fail("must not clean without confirmation")
    )
    result = runner.invoke(cli.app, ["box", "clean", "--all"])
    assert result.exit_code == 2
    assert "refusing to clean --all" in result.output


def test_box_clean_all_with_flag_cleans_whole_fleet(monkeypatch):
    seen: dict = {}

    def _fake_clean(names):
        seen["names"] = names
        return ["a", "b"]

    monkeypatch.setattr(cli.box, "clean_boxes", _fake_clean)
    result = runner.invoke(cli.app, ["box", "clean", "--all", "--yes-rebake-all"])
    assert result.exit_code == 0
    assert seen["names"] == list(box.FLEET)
    assert "removed: a, b" in result.stdout


def test_box_clean_single_named_box_no_flag(monkeypatch):
    seen: dict = {}

    def _fake_clean(names):
        seen["names"] = names
        return ["/x/mq-rdqm-rhel9.box"]

    monkeypatch.setattr(cli.box, "clean_boxes", _fake_clean)
    result = runner.invoke(cli.app, ["box", "clean", "mq-rdqm-rhel9"])
    assert result.exit_code == 0
    assert seen["names"] == ["mq-rdqm-rhel9"]
    assert "removed: /x/mq-rdqm-rhel9.box" in result.stdout


# --------------------------------------------------------------------------- #
# Box base-image GC (#759)                                                     #
# --------------------------------------------------------------------------- #
def _vol_xml(name, alloc_bytes, backing=None):
    """A libvirt volume XML fragment: name + exact byte allocation + optional
    <backingStore> (present only on overlays, pointing at their base image)."""
    backing_xml = (
        f"<backingStore><path>/var/lib/libvirt/images/{backing}</path></backingStore>"
        if backing
        else ""
    )
    return (
        f"<volume><name>{name}</name>"
        f"<allocation unit='bytes'>{alloc_bytes}</allocation>"
        f"<target><path>/var/lib/libvirt/images/{name}</path></target>"
        f"{backing_xml}</volume>"
    )


class _FakeVirsh:
    """Route virsh subcommands by argv: vol-list -> the name table, vol-dumpxml ->
    that volume's XML, vol-delete -> record + scripted rc. Unexpected argv raises."""

    def __init__(self, names, xml_by_name, *, delete_rc=0, delete_out="", dumpxml_errors=None):
        self.names = names
        self.xml_by_name = xml_by_name
        self.deleted: list[str] = []
        self.dumped: list[str] = []
        self.delete_rc = delete_rc
        self.delete_out = delete_out
        # name -> virsh error line: vol-dumpxml of that volume fails (rc=1) with it
        self.dumpxml_errors = dumpxml_errors or {}

    def run(self, command, on_line):
        argv = command.argv
        if "vol-list" in argv:
            on_line(" Name        Path")
            on_line("-------------------")
            for n in self.names:
                on_line(f" {n}   /var/lib/libvirt/images/{n}")
            return 0
        if "vol-dumpxml" in argv:
            name = argv[argv.index("vol-dumpxml") + 1]
            self.dumped.append(name)
            if name in self.dumpxml_errors:
                on_line(self.dumpxml_errors[name])
                return 1
            on_line(self.xml_by_name[name])
            return 0
        if "vol-delete" in argv:
            self.deleted.append(argv[argv.index("vol-delete") + 1])
            if self.delete_out:
                on_line(self.delete_out)
            return self.delete_rc
        raise AssertionError(f"unexpected virsh argv: {argv}")


def _install_virsh(monkeypatch, fake):
    monkeypatch.setattr(box, "SubprocessRunner", lambda: fake)


def test_gc_keeps_newest_and_deletes_older(monkeypatch):
    stem = "mq-rdqm-rhel9"
    old = f"{stem}_vagrant_box_image_0_100_box.img"
    mid = f"{stem}_vagrant_box_image_0_200_box.img"
    new = f"{stem}_vagrant_box_image_0_300_box.img"
    fake = _FakeVirsh(
        [old, mid, new],
        {
            old: _vol_xml(old, 3_000_000_000),
            mid: _vol_xml(mid, 3_500_000_000),
            new: _vol_xml(new, 4_000_000_000),
        },
    )
    _install_virsh(monkeypatch, fake)
    result = box.gc_orphaned_images()
    assert set(result.deleted) == {old, mid}
    assert result.kept == [new]  # highest timestamp survives
    assert result.freed_bytes == 6_500_000_000
    assert set(fake.deleted) == {old, mid}
    assert result.skipped_in_use == []


def test_gc_skips_base_image_backing_a_live_overlay(monkeypatch):
    stem = "obs-ubuntu24"
    old = f"{stem}_vagrant_box_image_0_100_box.img"
    new = f"{stem}_vagrant_box_image_0_200_box.img"
    overlay = "lab_obs.img"  # a live VM disk still backed by the OLD base image
    fake = _FakeVirsh(
        [old, new, overlay],
        {
            old: _vol_xml(old, 3_000_000_000),
            new: _vol_xml(new, 3_000_000_000),
            overlay: _vol_xml(overlay, 500_000_000, backing=old),
        },
    )
    _install_virsh(monkeypatch, fake)
    result = box.gc_orphaned_images()
    assert result.deleted == []  # old is protected — deleting it would corrupt the overlay
    assert result.skipped_in_use == [old]
    assert result.kept == [new]
    assert fake.deleted == []


def test_gc_dry_run_deletes_nothing(monkeypatch):
    stem = "mq-client-ubuntu24"
    old = f"{stem}_vagrant_box_image_0_1_box.img"
    new = f"{stem}_vagrant_box_image_0_2_box.img"
    fake = _FakeVirsh([old, new], {old: _vol_xml(old, 1000), new: _vol_xml(new, 2000)})
    _install_virsh(monkeypatch, fake)
    result = box.gc_orphaned_images(dry_run=True)
    assert result.deleted == [old]  # reported as would-delete
    assert result.dry_run is True
    assert fake.deleted == []  # but nothing actually removed


def test_gc_no_box_images_is_noop(monkeypatch):
    # A versioned cloud image (no `_0_<ts>_` segment) + a plain disk: neither matches.
    cloud = "cloud-image-x_vagrant_box_image_20260705.0.0_box.img"
    fake = _FakeVirsh(
        [cloud, "some-disk.qcow2"],
        {cloud: _vol_xml(cloud, 100), "some-disk.qcow2": _vol_xml("some-disk.qcow2", 200)},
    )
    _install_virsh(monkeypatch, fake)
    result = box.gc_orphaned_images()
    assert result.deleted == []
    assert result.kept == []
    assert result.skipped_in_use == []


def _register_box(tmp_path, monkeypatch, name, mtime, *, version="0", arch="arm64"):
    """Lay a registered box down under a fake VAGRANT_HOME with box.img at `mtime`."""
    home = tmp_path / "vagrant-home"
    monkeypatch.setenv("VAGRANT_HOME", str(home))
    escaped = name.replace("/", "-VAGRANTSLASH-")
    provider = home / "boxes" / escaped / version / arch / "libvirt"
    provider.mkdir(parents=True, exist_ok=True)
    img = provider / "box.img"
    img.write_bytes(b"qcow2")
    os.utime(img, (mtime, mtime))
    return img


def test_gc_keeps_the_registered_boxes_volume_not_the_newest(tmp_path, monkeypatch):
    # The registered box resolves to the MIDDLE image: it is the one kept, and the
    # newer one (not what the registered box would boot) is stale too (#1248).
    stem = "obs-ubuntu24"
    old, cur, newer = (f"{stem}_vagrant_box_image_0_{ts}_box.img" for ts in (100, 200, 300))
    _register_box(tmp_path, monkeypatch, stem, 200)
    fake = _FakeVirsh([old, cur, newer], {n: _vol_xml(n, 1000) for n in (old, cur, newer)})
    _install_virsh(monkeypatch, fake)
    result = box.gc_orphaned_images()
    assert result.kept == [cur]
    assert set(result.deleted) == {old, newer}
    assert set(fake.deleted) == {old, newer}


def test_gc_current_volume_survives_repeated_runs(tmp_path, monkeypatch):
    # The stack loop: the registered box's volume is the only one, so teardown's GC is a
    # no-op and the next `vagrant up` reuses it instead of uploading again (#1248).
    stem = "mq-client-ubuntu24"
    cur = f"{stem}_vagrant_box_image_0_500_box.img"
    _register_box(tmp_path, monkeypatch, stem, 500)
    fake = _FakeVirsh([cur], {cur: _vol_xml(cur, 1000)})
    _install_virsh(monkeypatch, fake)
    for _ in range(2):
        result = box.gc_orphaned_images()
        assert result.deleted == []
        assert result.kept == [cur]
    assert fake.deleted == []


def test_gc_rebaked_box_not_yet_uploaded_drops_the_stale_volume(tmp_path, monkeypatch):
    # A rebake re-added the box (new box.img mtime 900) but nothing booted it yet: the
    # old volume is stale and goes now, though it is the newest image in the pool.
    stem = "infra-ubuntu24"
    stale = f"{stem}_vagrant_box_image_0_400_box.img"
    _register_box(tmp_path, monkeypatch, stem, 900)
    fake = _FakeVirsh([stale], {stale: _vol_xml(stale, 2048)})
    _install_virsh(monkeypatch, fake)
    result = box.gc_orphaned_images()
    assert result.deleted == [stale]
    assert result.kept == []
    assert result.freed_bytes == 2048


def test_gc_stale_volume_backing_a_live_overlay_is_still_protected(tmp_path, monkeypatch):
    stem = "obs-ubuntu24"
    stale = f"{stem}_vagrant_box_image_0_100_box.img"
    cur = f"{stem}_vagrant_box_image_0_200_box.img"
    overlay = "lab_obs.img"
    _register_box(tmp_path, monkeypatch, stem, 200)
    fake = _FakeVirsh(
        [stale, cur, overlay],
        {
            stale: _vol_xml(stale, 1000),
            cur: _vol_xml(cur, 1000),
            overlay: _vol_xml(overlay, 10, backing=stale),
        },
    )
    _install_virsh(monkeypatch, fake)
    result = box.gc_orphaned_images()
    assert result.skipped_in_use == [stale]
    assert result.kept == [cur]
    assert fake.deleted == []


_VANISHED_ERRORS = [
    # what virsh printed in the D1 bake (#1336)
    "error: failed to get vol 'x', specifying --pool might help\n"
    "error: Storage volume not found: no storage vol with matching path 'x'",
    "error: Storage volume not found",
    "error: no storage vol with matching name 'x'",
]


@pytest.mark.parametrize("err", _VANISHED_ERRORS)
def test_gc_skips_a_volume_that_vanishes_before_dumpxml(monkeypatch, err):
    # A fatbox build domain's console log is listed, then removed before vol-dumpxml:
    # GC treats it as already gone and still evaluates (and reclaims) the rest (#1336).
    stem = "mq-rdqm-rhel9"
    old = f"{stem}_vagrant_box_image_0_100_box.img"
    new = f"{stem}_vagrant_box_image_0_200_box.img"
    gone = "fatbox-mq-nativeha-ubuntu24-build-console.log"
    fake = _FakeVirsh(
        [gone, old, new],
        {old: _vol_xml(old, 1000), new: _vol_xml(new, 2000)},
        dumpxml_errors={gone: err},
    )
    _install_virsh(monkeypatch, fake)
    result = box.gc_orphaned_images()
    assert fake.dumped == [gone, old, new]  # every listed volume was still evaluated
    assert result.vanished == [gone]
    assert result.deleted == [old]
    assert result.kept == [new]
    assert result.freed_bytes == 1000
    assert fake.deleted == [old]  # the vanished volume is never vol-deleted


def test_gc_vanished_volume_keeps_in_use_protection(monkeypatch):
    # Another volume vanishing does not weaken the backing-store guard for live overlays.
    stem = "obs-ubuntu24"
    old = f"{stem}_vagrant_box_image_0_100_box.img"
    new = f"{stem}_vagrant_box_image_0_200_box.img"
    overlay = "lab_obs.img"
    gone = "build-console.log"
    fake = _FakeVirsh(
        [old, gone, new, overlay],
        {
            old: _vol_xml(old, 1000),
            new: _vol_xml(new, 1000),
            overlay: _vol_xml(overlay, 10, backing=old),
        },
        dumpxml_errors={gone: "error: Storage volume not found"},
    )
    _install_virsh(monkeypatch, fake)
    result = box.gc_orphaned_images()
    assert result.vanished == [gone]
    assert result.skipped_in_use == [old]
    assert result.deleted == []
    assert fake.deleted == []


def test_gc_other_dumpxml_error_still_fails_loud(monkeypatch):
    # Only "the volume is gone" is tolerated; any other virsh failure aborts the pass.
    stem = "mq-rdqm-rhel9"
    old = f"{stem}_vagrant_box_image_0_100_box.img"
    new = f"{stem}_vagrant_box_image_0_200_box.img"
    fake = _FakeVirsh(
        [old, new],
        {new: _vol_xml(new, 2000)},
        dumpxml_errors={old: "error: failed to connect to the hypervisor"},
    )
    _install_virsh(monkeypatch, fake)
    with pytest.raises(RuntimeError, match="vol-dumpxml") as excinfo:
        box.gc_orphaned_images()
    assert not isinstance(excinfo.value, box.VolumeVanishedError)
    assert fake.deleted == []


def test_vol_detail_raises_vanished_only_for_missing_volume(monkeypatch):
    name = "x.img"
    fake = _FakeVirsh([name], {}, dumpxml_errors={name: "error: Storage volume not found"})
    _install_virsh(monkeypatch, fake)
    with pytest.raises(box.VolumeVanishedError, match="Storage volume not found"):
        box._vol_detail(name)


@pytest.mark.parametrize(
    ("output", "expected"),
    [
        *((err, True) for err in _VANISHED_ERRORS),
        ("error: failed to connect to the hypervisor", False),
        ("error: Storage pool not found: no storage pool with matching name 'x'", False),
        ("error: Requested operation is not valid: storage pool 'default' is not active", False),
        ("", False),
    ],
)
def test_is_volume_vanished_is_narrow(output, expected):
    assert box._is_volume_vanished(output) is expected


def test_current_box_volumes_escapes_slash_and_skips_versioned(tmp_path, monkeypatch):
    _register_box(tmp_path, monkeypatch, "rhel/9-x86_64", 42, arch="amd64")
    _register_box(tmp_path, monkeypatch, "cloud-image/ubuntu-24.04", 7, version="20260705.0.0")
    home = tmp_path / "vagrant-home" / "boxes"
    (home / "stray-file").write_text("not a box dir")
    # A box.img outside a libvirt provider dir is not a vagrant-libvirt image.
    other = home / "odd-box" / "0" / "virtualbox"
    other.mkdir(parents=True)
    (other / "box.img").write_bytes(b"x")
    assert box._current_box_volumes() == {
        "rhel-VAGRANTSLASH-9-x86_64": {"rhel-VAGRANTSLASH-9-x86_64_vagrant_box_image_0_42_box.img"}
    }


def test_current_box_volumes_absent_store_is_empty(tmp_path):
    assert box._current_box_volumes(tmp_path / "missing") == {}


def test_vagrant_boxes_dir_defaults_to_home(monkeypatch, tmp_path):
    monkeypatch.delenv("VAGRANT_HOME", raising=False)
    monkeypatch.setenv("HOME", str(tmp_path))
    assert box._vagrant_boxes_dir() == tmp_path / ".vagrant.d" / "boxes"


def test_pool_volume_names_parses_table(monkeypatch):
    _install_virsh(monkeypatch, _FakeVirsh(["a.img", "b.img"], {}))
    assert box._pool_volume_names() == ["a.img", "b.img"]


def test_vol_detail_reads_allocation_and_backing(monkeypatch):
    name = "ov.img"
    _install_virsh(
        monkeypatch, _FakeVirsh([name], {name: _vol_xml(name, 12345, backing="base_box.img")})
    )
    assert box._vol_detail(name) == (12345, "base_box.img")


def test_vol_detail_missing_allocation_defaults_zero(monkeypatch):
    name = "x.img"
    _install_virsh(monkeypatch, _FakeVirsh([name], {name: "<volume><name>x.img</name></volume>"}))
    assert box._vol_detail(name) == (0, None)


def test_virsh_out_fails_loud_on_nonzero(monkeypatch):
    class _Boom:
        def run(self, command, on_line):
            on_line("error: failed to connect to the hypervisor")
            return 1

    monkeypatch.setattr(box, "SubprocessRunner", lambda: _Boom())
    with pytest.raises(RuntimeError, match="failed"):
        box._virsh_out(["vol-list", "--pool", "default"])


def test_vol_delete_success(monkeypatch):
    class _Ok:
        def run(self, command, on_line):
            return 0

    monkeypatch.setattr(box, "SubprocessRunner", lambda: _Ok())
    box._vol_delete("x.img")  # returns, no raise


def test_vol_delete_absent_is_swallowed(monkeypatch):
    class _Gone:
        def run(self, command, on_line):
            on_line("error: Storage volume not found: no storage vol with matching name")
            return 1

    monkeypatch.setattr(box, "SubprocessRunner", lambda: _Gone())
    box._vol_delete("x.img")  # idempotent: already gone, no raise


def test_vol_delete_other_error_fails_loud(monkeypatch):
    class _Err:
        def run(self, command, on_line):
            on_line("error: some other libvirt failure")
            return 5

    monkeypatch.setattr(box, "SubprocessRunner", lambda: _Err())
    with pytest.raises(RuntimeError, match="vol-delete"):
        box._vol_delete("x.img")


def test_gc_best_effort_returns_result_on_success(monkeypatch):
    sentinel = box.GcResult(deleted=[], freed_bytes=0, kept=[], skipped_in_use=[], dry_run=False)
    monkeypatch.setattr(box, "gc_orphaned_images", lambda: sentinel)
    assert _real_gc_best_effort() is sentinel


def test_gc_best_effort_reports_and_returns_none_on_failure(monkeypatch, capsys):
    def _boom():
        raise RuntimeError("virsh exploded")

    monkeypatch.setattr(box, "gc_orphaned_images", _boom)
    assert _real_gc_best_effort() is None  # cleanup never fatal...
    assert "box base-image GC failed" in capsys.readouterr().err  # ...but never silent


def test_human_bytes_small_and_large():
    assert box._human_bytes(0) == "0.0 B"
    assert box._human_bytes(1536) == "1.5 KiB"
    assert box._human_bytes(2 * 1024**4).endswith("TiB")


def test_gc_summary_empty():
    result = box.GcResult(deleted=[], freed_bytes=0, kept=[], skipped_in_use=[], dry_run=False)
    assert "nothing to reclaim" in box.gc_summary(result)


def test_gc_summary_lists_deleted_and_skipped():
    result = box.GcResult(
        deleted=["a.img"],
        freed_bytes=1024**3,
        kept=["n.img"],
        skipped_in_use=["b.img"],
        dry_run=False,
    )
    summary = box.gc_summary(result)
    assert "deleted 1 orphaned" in summary
    assert "- a.img" in summary
    assert "~ b.img" in summary


def test_gc_summary_dry_run_verb():
    result = box.GcResult(
        deleted=["a.img"], freed_bytes=0, kept=[], skipped_in_use=[], dry_run=True
    )
    assert "would delete" in box.gc_summary(result)


def test_build_boxes_gcs_orphaned_images_after_bake(monkeypatch, capsys):
    captured: list = []
    _stub_build_env(monkeypatch, captured)
    gc = box.GcResult(
        deleted=["old_box.img"],
        freed_bytes=3_000_000_000,
        kept=["new_box.img"],
        skipped_in_use=[],
        dry_run=False,
    )
    monkeypatch.setattr(box, "gc_orphaned_images_best_effort", lambda: gc)
    box.build_boxes(["mq-rdqm-rhel9"], force=False)
    err = capsys.readouterr().err
    assert "box gc: deleted 1 orphaned" in err
    assert "old_box.img" in err


# --------------------------------------------------------------------------- #
# Retired box names — the one-time rename migration (#1274, spec §4.9)          #
# --------------------------------------------------------------------------- #
def test_gc_removes_retired_names(monkeypatch):
    # Plan T4 Step 1: only the retired registration is removed; the new name stays.
    monkeypatch.setattr(
        box, "_registered_boxes", lambda: {"mq-nativeha-ubuntu": "", "mq-nativeha-ubuntu24": ""}
    )
    removed: list[str] = []
    monkeypatch.setattr(box, "_vagrant_box_remove", removed.append)
    assert box.clean_retired() == ["mq-nativeha-ubuntu"]
    assert removed == ["mq-nativeha-ubuntu"]


def test_clean_retired_dry_run_removes_nothing(monkeypatch):
    monkeypatch.setattr(
        box, "_registered_boxes", lambda: {"rhel/9.6-x86_64": "", "pcmk-ubuntu": ""}
    )
    monkeypatch.setattr(box, "_vagrant_box_remove", lambda name: pytest.fail("dry run"))
    assert box.clean_retired(dry_run=True) == ["pcmk-ubuntu", "rhel/9.6-x86_64"]


def test_clean_retired_cache_deletes_dead_fat_box_caches(tmp_path):
    dead = [
        "obs-ubuntu2404-x86_64.box",
        "obs-ubuntu2404-x86_64.manifest-hash",
        "mq-nativeha-ubuntu-aarch64.box",
        "pcmk-ubuntu.box",  # the pre-#103 un-suffixed scheme
    ]
    keep = [
        "mq-nativeha-ubuntu24-x86_64.box",  # a new name sharing the retired prefix
        "pcmk-ubuntu24-aarch64.box",
        "rhel-9.6-x86_64-libvirt.box",  # the retired BASE cache: migrate renames it
    ]
    for name in dead + keep:
        (tmp_path / name).write_text("x")
    removed = box.clean_retired_cache(tmp_path)
    assert sorted(removed) == sorted(str(tmp_path / name) for name in dead)
    assert sorted(p.name for p in tmp_path.iterdir()) == sorted(keep)


def test_clean_retired_cache_dry_run_and_default_dir(monkeypatch, tmp_path):
    monkeypatch.setattr(box, "_boxes_cache_dir", lambda: tmp_path)
    (tmp_path / "infra-ubuntu2404-x86_64.box").write_text("x")
    assert box.clean_retired_cache(dry_run=True) == [str(tmp_path / "infra-ubuntu2404-x86_64.box")]
    assert (tmp_path / "infra-ubuntu2404-x86_64.box").exists()


def test_gc_drops_every_image_of_a_retired_box(monkeypatch):
    # A retired stem keeps NO image (not even the newest), still subject to the in-use guard.
    old = "pcmk-ubuntu_vagrant_box_image_0_100_box.img"
    new = "pcmk-ubuntu_vagrant_box_image_0_200_box.img"
    base_old = "rhel-VAGRANTSLASH-9.6-x86_64_vagrant_box_image_0_5_box.img"
    current = "pcmk-ubuntu24_vagrant_box_image_0_300_box.img"
    overlay = "lab_pcmk-a1.img"  # a live VM still backed by the retired box
    fake = _FakeVirsh(
        [old, new, base_old, current, overlay],
        {
            old: _vol_xml(old, 100),
            new: _vol_xml(new, 200),
            base_old: _vol_xml(base_old, 50),
            current: _vol_xml(current, 300),
            overlay: _vol_xml(overlay, 10, backing=new),
        },
    )
    _install_virsh(monkeypatch, fake)
    result = box.gc_orphaned_images()
    assert set(result.deleted) == {old, base_old}
    assert result.skipped_in_use == [new]
    assert result.kept == [current]
    assert result.freed_bytes == 150


def test_box_gc_verb_reports_retired_then_orphans(monkeypatch):
    monkeypatch.setattr(box, "clean_retired", lambda *, dry_run: ["pcmk-ubuntu"])
    monkeypatch.setattr(box, "clean_retired_cache", lambda *, dry_run: ["/c/pcmk-ubuntu.box"])
    seen: dict = {}

    def _gc(*, dry_run):
        seen["dry_run"] = dry_run
        return box.GcResult(deleted=[], freed_bytes=0, kept=[], skipped_in_use=[], dry_run=dry_run)

    monkeypatch.setattr(box, "gc_orphaned_images", _gc)
    result = runner.invoke(cli.app, ["box", "gc", "--dry-run"])
    assert result.exit_code == 0, result.output
    assert "would remove 1 retired box registration(s) and 1 retired cache file(s)" in result.output
    assert "  - vagrant box 'pcmk-ubuntu'" in result.output
    assert "  - /c/pcmk-ubuntu.box" in result.output
    assert "nothing to reclaim" in result.output
    assert seen == {"dry_run": True}


def test_box_gc_verb_quiet_when_nothing_retired(monkeypatch):
    monkeypatch.setattr(box, "clean_retired", lambda *, dry_run: [])
    monkeypatch.setattr(box, "clean_retired_cache", lambda *, dry_run: [])
    monkeypatch.setattr(
        box,
        "gc_orphaned_images",
        lambda *, dry_run: box.GcResult(
            deleted=[], freed_bytes=0, kept=[], skipped_in_use=[], dry_run=dry_run
        ),
    )
    result = runner.invoke(cli.app, ["box", "gc"])
    assert result.exit_code == 0, result.output
    assert "no retired box names registered or cached" in result.output


def test_retired_summary_removed_verb():
    out = cli._retired_summary(["a"], [], dry_run=False)
    assert out.startswith("box gc: removed 1 retired box registration(s) and 0 retired cache")


# --------------------------------------------------------------------------- #
# `mqlab box build --config` (#1274, spec §4.7)                               #
# --------------------------------------------------------------------------- #
def test_box_build_config_builds_the_build_files_boxes(monkeypatch, tmp_path):
    config = tmp_path / "rhel.yaml"
    config.write_text("os: rhel:9\n")
    monkeypatch.setattr(cli, "probe", lambda: _FACTS)
    calls: dict = {}
    monkeypatch.setattr(
        box, "build_boxes", lambda names, *, force: calls.update(names=names, force=force)
    )
    result = runner.invoke(cli.app, ["box", "build", "--config", str(config)])
    assert result.exit_code == 0, result.output
    assert calls == {
        "names": [
            "infra-ubuntu24",
            "obs-ubuntu24",
            "mq-client-ubuntu24",
            "san-ubuntu24",
            "mq-rdqm-rhel9",
            "mq-nativeha-rhel9",
        ],
        "force": False,
    }


def test_box_build_config_bad_request_exits_2_naming_the_fix(monkeypatch, tmp_path):
    config = tmp_path / "bad.yaml"
    config.write_text("os: ubuntu:99\n")
    monkeypatch.setattr(cli, "probe", lambda: _FACTS)
    monkeypatch.setattr(box, "build_boxes", lambda names, *, force: pytest.fail("no build"))
    result = runner.invoke(cli.app, ["box", "build", "--config", str(config)])
    assert result.exit_code == 2
    assert "mqlab box: " in result.output
    assert "--config" in result.output  # the version layer's fix names the build file


def test_box_build_config_excludes_names_and_all(tmp_path):
    config = tmp_path / "c.yaml"
    config.write_text("{}\n")
    result = runner.invoke(cli.app, ["box", "build", "--all", "--config", str(config)])
    assert result.exit_code == 2
    assert "--config cannot be combined" in result.output


def test_stage_rhel_dvd_stages_each_iso(monkeypatch, tmp_path):
    monkeypatch.setenv("MQLAB_REPO_ROOT", str(tmp_path))
    rec = RecordingRunner(results=[])
    monkeypatch.setattr(
        cli,
        "build_deps",
        lambda verb, ts: cli.Deps(
            runner=rec,
            renderer=Renderer(Console(file=io.StringIO(), force_terminal=False, width=80)),
            transcript=Transcript(transcript_path("dvd-stage", "20260716T000000Z")),
            pauser=_NoPause(),
        ),
    )
    seen: list = []
    monkeypatch.setattr(cli, "run_steps", lambda steps, **kw: seen.extend(steps))
    cli._stage_rhel_dvd(["a.iso", "b.iso"])
    assert [s.command.argv[-2:] for s in seen] == [["--iso", "a.iso"], ["--iso", "b.iso"]]
