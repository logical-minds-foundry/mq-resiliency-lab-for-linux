"""box.py fleet model + read-only `mqlab box status` (epic .github#91, T1).

The fleet is DERIVED from cli._LOCAL_BOX_BUILDERS (not a second literal list);
box_decision shells the builders' --dry-run decision surface (single source of
truth) and parses it. Every subprocess seam is monkeypatchable so these unit
tests never touch real git/fs/virsh/vagrant.
"""

from __future__ import annotations

import io

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
from mqlab.runner import Command
from mqlab.transcript import Transcript, transcript_path
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
def test_fleet_has_eight_local_boxes():
    assert set(box.FLEET) == {
        "rhel/9.6-x86_64",
        "mq-rdqm-rhel9",
        "obs-ubuntu2404",
        "infra-ubuntu2404",
        "mq-ubuntu2404",
        "mq-nativeha-rhel9",
        "mq-nativeha-ubuntu",
        "pcmk-ubuntu",
    }


def test_base_box_has_no_manifest_hash():
    assert box.FLEET["rhel/9.6-x86_64"].has_manifest_hash is False
    assert box.FLEET["mq-rdqm-rhel9"].has_manifest_hash is True


def test_fleet_is_derived_from_cli_builders():
    # DERIVED, not a second literal list: same keys + builder scripts as cli's map.
    assert {name: spec.builder for name, spec in box.FLEET.items()} == cli._LOCAL_BOX_BUILDERS


def test_cache_artifact_names():
    # Cache filenames are uniformly arch-suffixed (#103 D4). Only the host-INDEPENDENT
    # boxes are asserted against the module-level FLEET (built from real host facts):
    # the base box keeps its already-arch-tagged literal, and the RHEL fat boxes are
    # arch-pinned x86_64 on every host. The Ubuntu fat boxes are host-resolved after
    # the T5 un-pin (#703), so their cache name tracks the host arch — covered
    # deterministically by the injected-facts tests below (via _synthetic_registry),
    # not here where probe() would make the assertion host-dependent.
    assert box.FLEET["rhel/9.6-x86_64"].cache_artifact == "rhel-9.6-x86_64-libvirt.box"
    assert box.FLEET["mq-rdqm-rhel9"].cache_artifact == "mq-rdqm-rhel9-x86_64.box"
    assert box.FLEET["mq-nativeha-rhel9"].cache_artifact == "mq-nativeha-rhel9-x86_64.box"


# --------------------------------------------------------------------------- #
# _build_fleet arch derivation (#103 D4/D5, T4)                               #
# --------------------------------------------------------------------------- #
def _synthetic_registry() -> dict[str, dict]:
    """A topology `boxes:` registry with the Ubuntu fat boxes host-resolved
    (un-pinned) and the RHEL fat boxes arch-pinned — the post-#103 shape, so the
    arm64 branch is reachable without depending on the topology un-pin (T5)."""
    return {
        "mq-rdqm-rhel9": {"box": "mq-rdqm-rhel9", "arch": "x86_64"},
        "mq-nativeha-rhel9": {"box": "mq-nativeha-rhel9", "arch": "x86_64"},
        "obs-ubuntu2404": {"box": "obs-ubuntu2404"},
        "infra-ubuntu2404": {"box": "infra-ubuntu2404"},
        "mq-ubuntu2404": {"box": "mq-ubuntu2404"},
    }


def test_build_fleet_fat_boxes_carry_arch_x86():
    facts = HostFacts(arch=X86_64, kvm=True, distro_family="dnf", in_vergil=True)
    fleet = box._build_fleet(facts, _synthetic_registry())
    assert fleet["mq-ubuntu2404"].arch == "x86_64"
    assert fleet["mq-ubuntu2404"].cache_artifact == "mq-ubuntu2404-x86_64.box"
    assert fleet["mq-rdqm-rhel9"].arch == "x86_64"
    assert fleet["mq-rdqm-rhel9"].cache_artifact == "mq-rdqm-rhel9-x86_64.box"
    # The base box keeps its literal (already arch-tagged) artifact + x86_64 arch.
    assert fleet["rhel/9.6-x86_64"].arch == "x86_64"
    assert fleet["rhel/9.6-x86_64"].cache_artifact == "rhel-9.6-x86_64-libvirt.box"


def test_build_fleet_unpinned_ubuntu_tracks_host_arm64():
    facts = HostFacts(arch=AARCH64, kvm=True, distro_family="apt", in_vergil=True)
    fleet = box._build_fleet(facts, _synthetic_registry())
    # Un-pinned Ubuntu fat box tracks the host arch (arm64 on Apple Silicon).
    assert fleet["mq-ubuntu2404"].arch == "aarch64"
    assert fleet["mq-ubuntu2404"].cache_artifact == "mq-ubuntu2404-aarch64.box"
    # Pinned RHEL fat boxes stay x86_64 even on the Mac.
    assert fleet["mq-rdqm-rhel9"].arch == "x86_64"
    assert fleet["mq-rdqm-rhel9"].cache_artifact == "mq-rdqm-rhel9-x86_64.box"
    assert fleet["mq-nativeha-rhel9"].arch == "x86_64"
    assert fleet["mq-nativeha-rhel9"].cache_artifact == "mq-nativeha-rhel9-x86_64.box"


def test_manifest_hash_artifact_is_arch_suffixed():
    facts = HostFacts(arch=AARCH64, kvm=True, distro_family="apt", in_vergil=True)
    fleet = box._build_fleet(facts, _synthetic_registry())
    assert fleet["mq-ubuntu2404"].manifest_hash_artifact == "mq-ubuntu2404-aarch64.manifest-hash"


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
    box._run_builder_dry_run("mq-nativeha-ubuntu")
    argv = captured["argv"]
    assert "--arch" in argv
    assert argv[argv.index("--arch") + 1] == box.FLEET["mq-nativeha-ubuntu"].arch


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
    assert "ARCH" in out  # the arch column header (#103 T4)
    assert "aarch64" in out or "x86_64" in out  # a rendered arch cell from FLEET
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


# --------------------------------------------------------------------------- #
# build_boxes core (epic .github#91, T2)                                       #
# --------------------------------------------------------------------------- #
def _stub_build_env(monkeypatch, steps_sink):
    monkeypatch.setattr(box, "probe", lambda: _FACTS)
    monkeypatch.setattr(box, "ensure_resolved", lambda: None)
    monkeypatch.setattr(box.cli, "build_deps", lambda verb, ts: _fake_deps())
    monkeypatch.setattr(box, "run_steps", lambda steps, **kw: steps_sink.extend(steps))


def test_build_boxes_renders_resolved_topology(monkeypatch):
    # #737: box build must render build/work/lab/topology.resolved.yaml before the
    # builder's final `vagrant box add` loads the Vagrantfile (which guards on it) —
    # standalone `box build` otherwise dies at box registration on a fresh checkout.
    called: list = []
    monkeypatch.setattr(box, "probe", lambda: _FACTS)
    monkeypatch.setattr(box, "ensure_resolved", lambda: called.append(True))
    monkeypatch.setattr(box.cli, "build_deps", lambda verb, ts: _fake_deps())
    monkeypatch.setattr(box, "run_steps", lambda steps, **kw: None)
    box.build_boxes(["mq-rdqm-rhel9"], force=False)
    assert called == [True]


def test_build_boxes_force_adds_rebuild_flag(monkeypatch):
    captured: list = []
    _stub_build_env(monkeypatch, captured)
    box.build_boxes(["mq-rdqm-rhel9"], force=True)
    argv = captured[0].command.argv
    assert "--box" in argv and "mq-rdqm-rhel9" in argv and "--rebuild-box" in argv


def test_build_boxes_non_force_omits_rebuild_flag(monkeypatch):
    captured: list = []
    _stub_build_env(monkeypatch, captured)
    box.build_boxes(["mq-rdqm-rhel9"], force=False)
    argv = captured[0].command.argv
    assert "--box" in argv and "mq-rdqm-rhel9" in argv
    assert "--rebuild-box" not in argv


def test_build_boxes_raises_typer_exit_on_step_failure(monkeypatch):
    monkeypatch.setattr(box, "probe", lambda: _FACTS)
    monkeypatch.setattr(box, "ensure_resolved", lambda: None)
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
    assert cli._select_boxes(["mq-rdqm-rhel9", "obs-ubuntu2404"], all_=False) == [
        "mq-rdqm-rhel9",
        "obs-ubuntu2404",
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
    removed = box.clean_boxes(["obs-ubuntu2404"])
    assert removed_regs == ["obs-ubuntu2404"]
    assert removed == ["vagrant box 'obs-ubuntu2404'"]


def test_clean_base_box_has_no_manifest_hash(monkeypatch, tmp_path):
    # The base box carries no manifest-hash: only its .box artifact is removed;
    # a stray same-stem file is left untouched. Covers has_manifest_hash False.
    (tmp_path / "rhel-9.6-x86_64-libvirt.box").write_text("x")
    stray = tmp_path / "rhel/9.6-x86_64.manifest-hash"
    monkeypatch.setattr(box, "_boxes_cache_dir", lambda: tmp_path)
    monkeypatch.setattr(box, "_vagrant_box_remove", lambda n: None)
    removed = box.clean_boxes(["rhel/9.6-x86_64"])
    assert not (tmp_path / "rhel-9.6-x86_64-libvirt.box").exists()
    assert not stray.exists()  # never created — manifest-hash path skipped
    assert str(tmp_path / "rhel-9.6-x86_64-libvirt.box") in removed


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

    def __init__(self, names, xml_by_name, *, delete_rc=0, delete_out=""):
        self.names = names
        self.xml_by_name = xml_by_name
        self.deleted: list[str] = []
        self.delete_rc = delete_rc
        self.delete_out = delete_out

    def run(self, command, on_line):
        argv = command.argv
        if "vol-list" in argv:
            on_line(" Name        Path")
            on_line("-------------------")
            for n in self.names:
                on_line(f" {n}   /var/lib/libvirt/images/{n}")
            return 0
        if "vol-dumpxml" in argv:
            on_line(self.xml_by_name[argv[argv.index("vol-dumpxml") + 1]])
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
    assert result.kept_newest == [new]  # highest timestamp survives
    assert result.freed_bytes == 6_500_000_000
    assert set(fake.deleted) == {old, mid}
    assert result.skipped_in_use == []


def test_gc_skips_base_image_backing_a_live_overlay(monkeypatch):
    stem = "obs-ubuntu2404"
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
    assert result.kept_newest == [new]
    assert fake.deleted == []


def test_gc_dry_run_deletes_nothing(monkeypatch):
    stem = "mq-ubuntu2404"
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
    assert result.kept_newest == []
    assert result.skipped_in_use == []


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
    sentinel = box.GcResult(
        deleted=[], freed_bytes=0, kept_newest=[], skipped_in_use=[], dry_run=False
    )
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
    result = box.GcResult(
        deleted=[], freed_bytes=0, kept_newest=[], skipped_in_use=[], dry_run=False
    )
    assert "nothing to reclaim" in box.gc_summary(result)


def test_gc_summary_lists_deleted_and_skipped():
    result = box.GcResult(
        deleted=["a.img"],
        freed_bytes=1024**3,
        kept_newest=["n.img"],
        skipped_in_use=["b.img"],
        dry_run=False,
    )
    summary = box.gc_summary(result)
    assert "deleted 1 orphaned" in summary
    assert "- a.img" in summary
    assert "~ b.img" in summary


def test_gc_summary_dry_run_verb():
    result = box.GcResult(
        deleted=["a.img"], freed_bytes=0, kept_newest=[], skipped_in_use=[], dry_run=True
    )
    assert "would delete" in box.gc_summary(result)


def test_build_boxes_gcs_orphaned_images_after_bake(monkeypatch, capsys):
    captured: list = []
    _stub_build_env(monkeypatch, captured)
    gc = box.GcResult(
        deleted=["old_box.img"],
        freed_bytes=3_000_000_000,
        kept_newest=["new_box.img"],
        skipped_in_use=[],
        dry_run=False,
    )
    monkeypatch.setattr(box, "gc_orphaned_images_best_effort", lambda: gc)
    box.build_boxes(["mq-rdqm-rhel9"], force=False)
    err = capsys.readouterr().err
    assert "box gc: deleted 1 orphaned" in err
    assert "old_box.img" in err
