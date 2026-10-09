"""Fat-box builder (#603): mqlab resolution of the provision-then-snapshot fat
boxes, and the build-fatbox.sh / _manifest-hash.sh decision surface.

The Python half covers how mqlab resolves the boxes a guest set needs and the builder
argv it issues: every box input comes from the catalog (lab/versions.yaml) as a flag
(epic .github#280, #1274) — the builders carry no box table.

The shell half exercises only the cheap, side-effect-free arg-validation and
`--dry-run` decision path (REUSE / BUILD / stale), driving the cache dir at a
tmp path via LAB_BOX_CACHE_DIR so no host-durable state is touched.
"""

from __future__ import annotations

import dataclasses
import os
import shutil
import subprocess
from pathlib import Path

import pytest

from mqlab import box, cli
from mqlab.hostfacts import AARCH64, X86_64, HostFacts
from mqlab.orchestrator import StepFailedError
from tests.boxfleet import box_bakes, fatbox_args, manifest_hash_args

_REPO = Path(__file__).resolve().parents[1]
_FATBOX = _REPO / "lab" / "boxes" / "build-fatbox.sh"
_MANIFEST = _REPO / "lab" / "boxes" / "_manifest-hash.sh"
_X86 = HostFacts(arch=X86_64, kvm=True, distro_family="dnf", in_vergil=True, x86_64_v3=True)
_ARM = HostFacts(arch=AARCH64, kvm=True, distro_family="apt", in_vergil=True, x86_64_v3=False)


# --------------------------------------------------------------------------- #
# mqlab resolution                                                            #
# --------------------------------------------------------------------------- #
def test_needed_local_boxes_resolves_fat_boxes(monkeypatch):
    monkeypatch.setattr(
        cli,
        "_resolved_nodes",
        lambda: {
            "rdqm-a1": {"box": "mq-rdqm-rhel9"},
            "obs": {"box": "obs-ubuntu26"},
            "infra-client": {"box": "infra-ubuntu26"},
            "nha-rhel-crr-a1": {"box": "mq-nativeha-rhel9"},
            "san-a": {"box": "cloud-image/ubuntu-24.04"},  # a cloud box: nothing to build
        },
    )
    needed = cli._needed_local_boxes(["rdqm-a1", "obs", "infra-client", "nha-rhel-crr-a1", "san-a"])
    assert needed == ["infra-ubuntu26", "mq-nativeha-rhel9", "mq-rdqm-rhel9", "obs-ubuntu26"]


def test_fleet_builders_cover_base_and_fat_boxes():
    assert box.FLEET["rhel/9-x86_64"].builder == "lab/boxes/rhel/build-box.sh"
    for fat in (
        "mq-rdqm-rhel9",
        "obs-ubuntu26",
        "infra-ubuntu26",
        "mq-client-ubuntu24",
        "mq-nativeha-rhel9",
        "mq-nativeha-ubuntu24",
        "pcmk-ubuntu24",
        "san-ubuntu26",
    ):
        assert box.FLEET[fat].builder == "lab/boxes/build-fatbox.sh"
    # The standalone logsearch-ubuntu2404 box was retired (#1179) — folded into obs.
    assert "logsearch-ubuntu2404" not in box.FLEET


def test_build_steps_pass_catalog_flags_for_fatbox(monkeypatch, tmp_path):
    monkeypatch.setenv("MQLAB_REPO_ROOT", str(tmp_path))
    # tmp_path is no git repo: stub the component source identity (its own seam).
    monkeypatch.setattr(box, "_component_tree", lambda name: "f" * 40)
    steps = box._build_steps([("mq-rdqm-rhel9", False)], _X86)
    assert len(steps) == 1
    argv = steps[0].command.argv
    # The component tail follows the catalog's roles.mq-rdqm.components (epic
    # .github#294); its mechanics are covered in test_box_components.
    cut = argv.index("--components")
    baked = box.FLEET["mq-rdqm-rhel9"].components
    assert argv[cut + 1] == ",".join(f"{name}@{'f' * 40}" for name in baked)
    assert argv[cut + 2] == "--install-vars"
    assert [argv[:cut]] == [
        [
            "bash",
            str(tmp_path / "lab/boxes/build-fatbox.sh"),
            "--box",
            "mq-rdqm-rhel9",
            "--arch",
            "x86_64",
            "--base-kind",
            "rhel",
            "--base-box",
            "rhel/9-x86_64",
            "--base-box-version",
            "none",
            "--bake",
            "mq-rdqm",
            "--dvd",
            box.FLEET["mq-rdqm-rhel9"].os.iso,
            "--os-pin",
            f"rhel/9-x86_64@{box.FLEET['mq-rdqm-rhel9'].os.point}",
            "--mq-bearing",
            "1",
            "--domain-type",
            "kvm",
            "--cpu-mode",
            "host-passthrough",
            "--runtime-pin",
            box.FLEET["mq-rdqm-rhel9"].runtime_pin,
        ]
    ]


def test_build_steps_base_builder_takes_major_point_iso(monkeypatch, tmp_path):
    monkeypatch.setenv("MQLAB_REPO_ROOT", str(tmp_path))
    entry = box.FLEET["rhel/9-x86_64"].os
    steps = box._build_steps([("rhel/9-x86_64", True)], _X86)
    argv = steps[0].command.argv
    assert argv[:2] == ["bash", str(tmp_path / "lab/boxes/rhel/build-box.sh")]
    assert argv[2:] == [
        "--major",
        "9",
        "--point",
        entry.point,
        "--iso",
        entry.iso,
        "--domain-type",
        "kvm",
        "--cpu-mode",
        "host-passthrough",
        "--rebuild-box",
    ]
    assert "--box" not in argv  # the base-OS builder is not box-parameterized


def test_rhel10_base_and_fat_box_take_their_catalog_inputs():
    # T10 (#1286): the RHEL 10 base box is built from the catalog's os.rhel.10 pin, and
    # its Native HA fat box bakes from it with the same nativeha-rhel stem.
    base = box.builder_args(box.FLEET["rhel/10-x86_64"], _X86)
    assert base[:6] == ["--major", "10", "--point", "10.2", "--iso", "rhel-10.2-x86_64-dvd.iso"]
    fat = box.FLEET["mq-nativeha-rhel10"]
    assert (fat.arch, fat.bake_stem, fat.os.base_box) == (
        "x86_64",
        "nativeha-rhel",
        "rhel/10-x86_64",
    )


def test_build_steps_ubuntu_fat_box_tracks_host_arch_under_kvm(monkeypatch, tmp_path):
    monkeypatch.setattr(box, "FLEET", box._build_fleet(_ARM))
    monkeypatch.setenv("MQLAB_REPO_ROOT", str(tmp_path))
    # mq-client bakes mq-resiliency-clients (epic .github#294) and tmp_path is no git
    # repo: stub the component source identity (its own seam, test_box_components).
    monkeypatch.setattr(box, "_component_tree", lambda name: "f" * 40)
    argv = box._build_steps([("mq-client-ubuntu24", False)], _ARM)[0].command.argv
    assert argv[argv.index("--arch") + 1] == "aarch64"
    assert argv[argv.index("--domain-type") + 1] == "kvm"
    assert argv[argv.index("--dvd") + 1] == "none"


def test_build_steps_refuses_base_box_on_arm(monkeypatch, tmp_path):
    # The x86_64 base box on an arm64 host would be emulated (TCG): refused (#103 D11).
    monkeypatch.setenv("MQLAB_REPO_ROOT", str(tmp_path))
    with pytest.raises(StepFailedError, match="emulated cross-arch box builds are disabled"):
        box._build_steps([("rhel/9-x86_64", False)], _ARM)


def test_build_steps_refuses_rhel_fat_box_on_arm(monkeypatch, tmp_path):
    monkeypatch.setenv("MQLAB_REPO_ROOT", str(tmp_path))
    with pytest.raises(StepFailedError) as excinfo:
        box._build_steps([("mq-rdqm-rhel9", False)], _ARM)
    assert excinfo.value.exit_code == 2


def test_build_steps_tcg_without_kvm(monkeypatch, tmp_path):
    monkeypatch.setenv("MQLAB_REPO_ROOT", str(tmp_path))
    no_kvm = HostFacts(arch=X86_64, kvm=False, distro_family="dnf", in_vergil=True, x86_64_v3=False)
    argv = box._build_steps([("rhel/9-x86_64", False)], no_kvm)[0].command.argv
    assert argv[argv.index("--domain-type") + 1] == "qemu"
    assert argv[argv.index("--cpu-mode") + 1] == "maximum"


def test_builder_args_refuse_a_base_entry_without_point_or_iso():
    spec = box.FLEET["rhel/9-x86_64"]
    broken = box.BoxSpec(
        name=spec.name,
        builder=spec.builder,
        arch=spec.arch,
        cache_artifact=spec.cache_artifact,
        has_manifest_hash=False,
        os=dataclasses.replace(spec.os, iso=None),
    )
    with pytest.raises(box.VersionError, match="needs point and iso"):
        box.builder_args(broken, _X86)


def test_build_plan_puts_a_local_base_box_first_and_never_forces_it():
    plan = box._build_plan(["mq-rdqm-rhel9", "obs-ubuntu26", "mq-nativeha-rhel9"], force=True)
    assert plan == [
        ("rhel/9-x86_64", False),  # dependency: ensured, never force-rebuilt
        ("mq-rdqm-rhel9", True),
        ("obs-ubuntu26", True),
        ("mq-nativeha-rhel9", True),
    ]


def _decide(action: str):
    return lambda name: box.BoxDecision(
        name=name, cached=True, age_days=None, hash_match=True, registered=True, action=action
    )


def test_build_plan_skips_the_base_box_when_the_fat_box_is_reused(monkeypatch):
    # A REUSEd fat box never needs its base (build-fatbox.sh only used it on BUILD), so a
    # bootstrap after a VM rebuild does not re-register a base box it will not use.
    monkeypatch.setattr(box, "box_decision", _decide("REUSE"))
    assert box._build_plan(["mq-rdqm-rhel9"], force=False) == [("mq-rdqm-rhel9", False)]


def test_build_plan_pulls_the_base_box_when_the_fat_box_will_bake(monkeypatch):
    monkeypatch.setattr(box, "box_decision", _decide("BUILD"))
    assert box._build_plan(["mq-rdqm-rhel9", "mq-nativeha-rhel9"], force=False) == [
        ("rhel/9-x86_64", False),
        ("mq-rdqm-rhel9", False),
        ("mq-nativeha-rhel9", False),
    ]


def test_build_plan_named_base_box_keeps_its_force():
    plan = box._build_plan(["mq-rdqm-rhel9", "rhel/9-x86_64"], force=True)
    assert plan == [("mq-rdqm-rhel9", True), ("rhel/9-x86_64", True)]


# --------------------------------------------------------------------------- #
# build-fatbox.sh — arg validation                                            #
# --------------------------------------------------------------------------- #
def _run(*args: str, cache_dir: Path | None = None) -> subprocess.CompletedProcess[str]:
    env = None
    if cache_dir is not None:
        env = {**os.environ, "LAB_BOX_CACHE_DIR": str(cache_dir)}
    return subprocess.run(  # noqa: S603
        ["bash", str(_FATBOX), *args],
        capture_output=True,
        text=True,
        check=False,
        env=env,
    )


def _without(args: list[str], flag: str) -> list[str]:
    """args with ``flag`` and its value removed."""
    i = args.index(flag)
    return args[:i] + args[i + 2 :]


def _with(args: list[str], flag: str, value: str) -> list[str]:
    """args with ``flag``'s value replaced."""
    out = list(args)
    out[out.index(flag) + 1] = value
    return out


def test_build_fatbox_requires_bake_flag():
    # Plan T4: the builder carries no box table, so --bake (and every catalog input) is a
    # required flag; a missing one is a usage error, never a silent default.
    r = _run(
        "--box", "x", "--arch", "x86_64", "--domain-type", "kvm",
        "--cpu-mode", "host-passthrough", "--dry-run",
    )  # fmt: skip
    assert r.returncode == 2
    assert "--bake is required" in r.stderr


@pytest.mark.parametrize(
    "flag",
    [
        "--box",
        "--bake",
        "--base-kind",
        "--base-box",
        "--base-box-version",
        "--dvd",
        "--os-pin",
        "--mq-bearing",
        "--runtime-pin",
        "--components",
    ],
)
def test_each_catalog_flag_is_required(flag, tmp_path):
    r = _run(*_without(fatbox_args("mq-rdqm-rhel9"), flag), "--dry-run", cache_dir=tmp_path)
    assert r.returncode == 2
    assert f"ERROR: {flag} is required" in r.stderr


@pytest.mark.parametrize(
    ("flag", "value", "message"),
    [
        ("--base-kind", "debian", "--base-kind must be 'ubuntu' or 'rhel'"),
        ("--mq-bearing", "yes", "--mq-bearing must be 0 or 1"),
        ("--dvd", "none", "--dvd is required for a rhel bake"),
        ("--dvd", "/abs/x.iso", "--dvd is a filename under build/state/"),
    ],
)
def test_invalid_catalog_flag_values_die(flag, value, message, tmp_path):
    r = _run(*_with(fatbox_args("mq-rdqm-rhel9"), flag, value), "--dry-run", cache_dir=tmp_path)
    assert r.returncode == 2
    assert message in r.stderr


def test_ubuntu_bake_accepts_no_dvd(tmp_path):
    r = _run(*fatbox_args("mq-nativeha-ubuntu24"), "--dry-run", cache_dir=tmp_path)
    assert r.returncode == 0, r.stderr
    assert "BUILD" in r.stdout


def test_missing_domain_type_dies(tmp_path):
    r = _run(*_without(fatbox_args("mq-rdqm-rhel9"), "--domain-type"), cache_dir=tmp_path)
    assert r.returncode != 0
    assert "--domain-type" in r.stderr


def test_invalid_cpu_mode_dies(tmp_path):
    r = _run(*_with(fatbox_args("mq-rdqm-rhel9"), "--cpu-mode", "bogus"), cache_dir=tmp_path)
    assert r.returncode != 0
    assert "--cpu-mode" in r.stderr


def test_unknown_flag_dies(tmp_path):
    r = _run(*fatbox_args("mq-rdqm-rhel9"), "--x", cache_dir=tmp_path)
    assert r.returncode != 0
    assert "unknown arg: --x" in r.stderr


# --------------------------------------------------------------------------- #
# build-fatbox.sh — --dry-run decision surface                                #
# --------------------------------------------------------------------------- #
def _dry_run(cache_dir: Path, *extra: str) -> subprocess.CompletedProcess[str]:
    # RHEL is x86_64 always (#103); mqlab supplies --arch and every catalog flag.
    return _run(*fatbox_args("mq-rdqm-rhel9"), "--dry-run", *extra, cache_dir=cache_dir)


def _manifest_hash(box_name: str) -> str:
    out = subprocess.run(  # noqa: S603
        ["bash", str(_MANIFEST), box_name, *manifest_hash_args(box_name)],
        capture_output=True,
        text=True,
        check=True,
    )
    return out.stdout.strip()


def _seed_cached_box(cache_dir: Path, box_name: str, *, age_days: int = 0) -> Path:
    # The cache is arch-suffixed <box>-<arch>.box (#103 D4); these decision-surface
    # tests all drive the x86_64 RHEL box, so seed the -x86_64 entry the script keys on.
    cache_dir.mkdir(parents=True, exist_ok=True)
    boxfile = cache_dir / f"{box_name}-x86_64.box"
    boxfile.write_text("fake box tarball\n")
    (cache_dir / f"{box_name}-x86_64.manifest-hash").write_text(_manifest_hash(box_name) + "\n")
    if age_days:
        subprocess.run(  # noqa: S603
            ["touch", "-d", f"{age_days} days ago", str(boxfile)], check=True
        )
    return boxfile


def test_dry_run_build_on_clean_cache(tmp_path):
    result = _dry_run(tmp_path / "boxes")
    assert result.returncode == 0
    assert "BUILD" in result.stdout


def test_dry_run_reuse_when_matching_hash_cached(tmp_path):
    cache_dir = tmp_path / "boxes"
    _seed_cached_box(cache_dir, "mq-rdqm-rhel9")
    result = _dry_run(cache_dir)
    assert result.returncode == 0
    assert "REUSE" in result.stdout


def test_dry_run_rebuild_on_hash_mismatch(tmp_path):
    cache_dir = tmp_path / "boxes"
    _seed_cached_box(cache_dir, "mq-rdqm-rhel9")
    (cache_dir / "mq-rdqm-rhel9-x86_64.manifest-hash").write_text("stale-different-hash\n")
    result = _dry_run(cache_dir)
    assert result.returncode == 0
    assert "BUILD" in result.stdout


def test_dry_run_rebuild_on_os_repin(tmp_path):
    # A re-pin (a new point release) flips the manifest hash, so a cache baked at the old
    # pin is void: BUILD, not REUSE (spec §4.7).
    cache_dir = tmp_path / "boxes"
    _seed_cached_box(cache_dir, "mq-rdqm-rhel9")
    repinned = _with(fatbox_args("mq-rdqm-rhel9"), "--os-pin", "rhel/9-x86_64@9.99")
    result = _run(*repinned, "--dry-run", cache_dir=cache_dir)
    assert result.returncode == 0
    assert "BUILD" in result.stdout


def test_dry_run_force_build(tmp_path):
    cache_dir = tmp_path / "boxes"
    _seed_cached_box(cache_dir, "mq-rdqm-rhel9")
    result = _dry_run(cache_dir, "--rebuild-box")
    assert result.returncode == 0
    assert "FORCE-BUILD" in result.stdout


def test_dry_run_reuse_with_warning_at_seven_days(tmp_path):
    cache_dir = tmp_path / "boxes"
    _seed_cached_box(cache_dir, "mq-rdqm-rhel9", age_days=8)
    result = _dry_run(cache_dir)
    assert result.returncode == 0
    assert "REUSE" in result.stdout
    assert "NOTICE" in result.stderr


def test_dry_run_stale_at_fourteen_days(tmp_path):
    cache_dir = tmp_path / "boxes"
    _seed_cached_box(cache_dir, "mq-rdqm-rhel9", age_days=20)
    result = _dry_run(cache_dir)
    assert result.returncode == 0  # dry-run reports and exits 0
    assert "STALE" in result.stdout


def test_stale_refused_without_rebuild_flag(tmp_path):
    # non-dry-run: a >=14d cache with matching hash must refuse (exit non-zero)
    cache_dir = tmp_path / "boxes"
    _seed_cached_box(cache_dir, "mq-rdqm-rhel9", age_days=20)
    result = _run(*fatbox_args("mq-rdqm-rhel9"), cache_dir=cache_dir)
    assert result.returncode != 0
    assert "--rebuild-box" in (result.stderr + result.stdout)


# --------------------------------------------------------------------------- #
# _manifest-hash.sh                                                           #
# --------------------------------------------------------------------------- #
def test_manifest_hash_is_stable_and_box_specific():
    a = _manifest_hash("mq-rdqm-rhel9")
    assert a == _manifest_hash("mq-rdqm-rhel9")  # deterministic
    assert a != _manifest_hash("obs-ubuntu26")  # box (bake playbook) enters the hash
    assert len(a) == 64  # sha256 hex digest


def test_manifest_hash_covers_nativeha_rhel_box():
    # #88/#667 + the #649-class guard: the mq-nativeha-rhel9 -> nativeha-rhel stem
    # must digest its own bake-nativeha-rhel.yml + role closure — a deterministic
    # 64-char, box-specific hash.
    h = _manifest_hash("mq-nativeha-rhel9")
    assert len(h) == 64
    assert h == _manifest_hash("mq-nativeha-rhel9")  # deterministic
    assert h != _manifest_hash("mq-rdqm-rhel9")  # its own bake playbook enters the hash


def test_manifest_hash_covers_ubuntu_ha_boxes():
    # #103 T6/T7: mq-nativeha-ubuntu24 -> nativeha-ubuntu and pcmk-ubuntu24 -> pcmk-ubuntu
    # each digest their own bake playbook + role closure — a deterministic 64-char,
    # box-specific hash.
    for name in ("mq-nativeha-ubuntu24", "pcmk-ubuntu24"):
        h = _manifest_hash(name)
        assert len(h) == 64
        assert h == _manifest_hash(name)  # deterministic
    assert _manifest_hash("mq-nativeha-ubuntu24") != _manifest_hash("pcmk-ubuntu24")


def test_fleet_has_the_baked_san_box():
    """#1278 (spec §4.7.1): the SAN targets' box is a host-resolved fat box on the infra
    OS, baked by ansible/bake-san.yml, not MQ-bearing, with its own manifest hash."""
    spec = box._build_fleet(_X86)["san-ubuntu26"]
    assert (spec.role, spec.bake_stem, spec.mq_bearing) == ("san", "san", False)
    assert spec.os.arch_pin is None and spec.has_manifest_hash
    assert box._build_fleet(_ARM)["san-ubuntu26"].arch == AARCH64
    h = _manifest_hash("san-ubuntu26")
    assert len(h) == 64 and h == _manifest_hash("san-ubuntu26")
    assert h != _manifest_hash("pcmk-ubuntu24")


def _hash_with(*args: str) -> subprocess.CompletedProcess[str]:
    return subprocess.run(  # noqa: S603
        ["bash", str(_MANIFEST), *args], capture_output=True, text=True, check=False
    )


# The runtime pin + baked components every caller passes (epic .github#294 T5): here, a
# box that bakes none.
_NO_COMPONENTS = ["--runtime-pin", "3.14.8+20261003", "--components", ""]


def test_manifest_hash_flips_on_os_pin():
    # Plan T4: the OS pin (base box + point / box version) enters the digest, so a re-pin
    # forces a rebake.
    stem = ["--bake-stem", "nativeha-ubuntu", "--mq-bearing", "1", *_NO_COMPONENTS]
    a = _hash_with("mq-nativeha-ubuntu24", *stem, "--os-pin", "x@1")
    b = _hash_with("mq-nativeha-ubuntu24", *stem, "--os-pin", "x@2")
    assert a.returncode == b.returncode == 0
    assert a.stdout != b.stdout


def test_manifest_hash_requires_a_box():
    result = _hash_with()
    assert result.returncode == 2
    assert "<box> is required" in result.stderr


@pytest.mark.parametrize(
    "flag", ["--bake-stem", "--mq-bearing", "--os-pin", "--runtime-pin", "--components"]
)
def test_manifest_hash_requires_each_flag(flag):
    args = ["--bake-stem", "obs", "--mq-bearing", "0", "--os-pin", "x@1", *_NO_COMPONENTS]
    i = args.index(flag)
    result = _hash_with("obs-ubuntu26", *args[:i], *args[i + 2 :])
    assert result.returncode == 2
    assert f"ERROR: {flag} is required" in result.stderr


def test_manifest_hash_rejects_bad_mq_bearing_and_unknown_flag():
    base = ["obs-ubuntu26", "--bake-stem", "obs", "--os-pin", "x@1", *_NO_COMPONENTS]
    assert "--mq-bearing must be 0 or 1" in _hash_with(*base, "--mq-bearing", "2").stderr
    assert "unknown arg: --x" in _hash_with(*base, "--mq-bearing", "0", "--x").stderr


def test_manifest_hash_rejects_unknown_bake_stem():
    # #649: a stem with no bake playbook is a hard error, not a silent empty digest.
    # (Pre-#649 the script dereferenced bake-<full-box>.yml, which existed for NO box,
    # so the bake inputs were silently omitted instead.)
    result = _hash_with(
        "x", "--bake-stem", "no-such-stem", "--mq-bearing", "0", "--os-pin", "x", *_NO_COMPONENTS
    )
    assert result.returncode == 2
    assert "no-such-stem" in result.stderr


# --- the runtime pin + baked components (epic .github#294 T5, spec §5.8) ---------------
_OBS = ["obs-ubuntu26", "--bake-stem", "obs", "--mq-bearing", "0", "--os-pin", "x@1"]


def _component_hash(pin: str, components: str) -> str:
    result = _hash_with(*_OBS, "--runtime-pin", pin, "--components", components)
    assert result.returncode == 0, result.stderr
    return result.stdout.strip()


def test_manifest_hash_flips_on_component_tree_change():  # Review Focus 2
    # The hash keys on the component's SOURCE tree: a committed edit flips it even
    # before the component is rebuilt, so a stale box is never REUSEd.
    pin = "3.14.8+20261003"
    assert _component_hash(pin, "demo@aaa") != _component_hash(pin, "demo@bbb")
    assert _component_hash(pin, "demo@aaa") != _component_hash(pin, "")  # baking one at all
    # The digest is order-independent (mqlab's order is the catalog's; the bake is not).
    assert _component_hash(pin, "a@1,b@2") == _component_hash(pin, "b@2,a@1")


def test_manifest_hash_flips_on_runtime_pin_change():
    assert _component_hash("3.14.8+20261003", "demo@aaa") != _component_hash(
        "3.14.9+20261101", "demo@aaa"
    )


def test_manifest_hash_runtime_pin_spares_boxes_baking_no_component():
    # Like the MQ pin for commons boxes (#1087): a box that bakes no component never
    # bakes the runtime either, so a runtime bump must not spuriously rebake it.
    assert _component_hash("3.14.8+20261003", "") == _component_hash("3.14.9+20261101", "")


def test_manifest_hash_requires_runtime_pin_flag():
    result = _hash_with(*_OBS, "--components", "demo@aaa")
    assert result.returncode == 2
    assert "ERROR: --runtime-pin is required" in result.stderr


@pytest.mark.parametrize(
    ("flag", "value", "message"),
    [
        ("--runtime-pin", "3.14", "--runtime-pin must be <version>+<pbs_release>"),
        ("--components", "Demo@aaa", "--components entries are <name>@<tree hash>"),
        ("--components", "demo", "--components entries are <name>@<tree hash>"),
        ("--components", "demo@xyz", "--components entries are <name>@<tree hash>"),
    ],
)
def test_manifest_hash_rejects_malformed_pin_or_components(flag, value, message):
    args = ["--runtime-pin", "3.14.8+20261003", "--components", "demo@aaa"]
    args[args.index(flag) + 1] = value
    result = _hash_with(*_OBS, *args)
    assert result.returncode == 2
    assert message in result.stderr


def test_manifest_hash_components_needs_a_value():
    result = _hash_with(*_OBS, "--runtime-pin", "3.14.8+20261003", "--components")
    assert result.returncode == 2
    assert "--components needs a value" in result.stderr


# --------------------------------------------------------------------------- #
# _manifest-hash.sh — bake-playbook + transitive bake-role coverage (#649)    #
#                                                                             #
# Two bugs fixed here. (a) The script was invoked with the FULL box name and   #
# dereferenced ansible/bake-<full-box>.yml (e.g. bake-mq-rdqm-rhel9.yml) - a   #
# file that exists for NO box; the real playbooks are stem-named               #
# (bake-mq-rdqm.yml). So `[ -f "$BAKE" ]` never fired and even the bake        #
# PLAYBOOK content was omitted from the digest. (b) The real bake work lives   #
# in the ROLES the playbook imports (tasks/templates/defaults), which the      #
# digest never covered - so #642's inert-service edit didn't flip the hash and #
# build-fatbox.sh wrongly REUSEd. These build a minimal repo the script can    #
# hash (it reads inputs relative to itself) and prove a bake-playbook edit, a   #
# baked-role edit, and a transitively-included-role edit each flip the hash,    #
# while a role no bake playbook reaches leaves it stable.                       #
# --------------------------------------------------------------------------- #
def _fake_bake_repo(tmp_path: Path) -> Path:
    """A minimal repo tree _manifest-hash.sh can hash: the real script, a
    versions.yml, a stem-named bake-infra.yml that include_role's `baked-role`,
    and three roles - `baked-role` (which itself pulls in `nested-role`) and an
    unrelated `unbaked-role` no bake playbook references. Box `infra-ubuntu26`
    maps to the `infra` bake stem, matching bake-infra.yml (the box->stem map the
    script shares with build-fatbox.sh)."""
    root = tmp_path / "repo"
    boxes = root / "lab" / "boxes"
    boxes.mkdir(parents=True)
    shutil.copy(_MANIFEST, boxes / "_manifest-hash.sh")

    ans = root / "ansible"
    (ans / "group_vars" / "all").mkdir(parents=True)
    (ans / "group_vars" / "all" / "versions.yml").write_text("mq_version: '9.4.0'\n")
    (ans / "bake-infra.yml").write_text(
        "- name: bake\n"
        "  hosts: bake\n"
        "  tasks:\n"
        "    - name: install baked-role\n"
        "      ansible.builtin.include_role:\n"
        "        name: baked-role\n"
    )
    roles = ans / "roles"
    for r in ("baked-role", "nested-role", "unbaked-role"):
        (roles / r / "tasks").mkdir(parents=True)
    # baked-role transitively pulls in nested-role via a nested include_role.
    (roles / "baked-role" / "tasks" / "main.yml").write_text(
        "- name: do the install work\n"
        "  ansible.builtin.command: 'true'\n"
        "- name: pull nested\n"
        "  ansible.builtin.include_role:\n"
        "    name: nested-role\n"
    )
    (roles / "nested-role" / "tasks" / "main.yml").write_text(
        "- name: nested work\n  ansible.builtin.command: 'true'\n"
    )
    (roles / "unbaked-role" / "tasks" / "main.yml").write_text(
        "- name: unrelated work\n  ansible.builtin.command: 'true'\n"
    )
    return root


def _hash_in(root: Path, box_name: str = "infra-ubuntu26") -> str:
    out = subprocess.run(  # noqa: S603
        [
            "bash",
            str(root / "lab" / "boxes" / "_manifest-hash.sh"),
            box_name,
            *manifest_hash_args(box_name),
        ],
        capture_output=True,
        text=True,
        check=True,
    )
    return out.stdout.strip()


def _role_task(root: Path, role: str) -> Path:
    return root / "ansible" / "roles" / role / "tasks" / "main.yml"


def _append(path: Path, text: str) -> None:
    path.write_text(path.read_text() + text)


def test_manifest_hash_flips_on_bake_playbook_change(tmp_path):
    # Bug (a): the bake playbook content must actually enter the digest.
    root = _fake_bake_repo(tmp_path)
    before = _hash_in(root)
    assert len(before) == 64
    _append(root / "ansible" / "bake-infra.yml", "    # a bake-recipe tweak\n")
    assert _hash_in(root) != before


def test_manifest_hash_flips_on_baked_role_task_change(tmp_path):
    # Bug (b), the headline acceptance: editing a baked role's task file flips it.
    root = _fake_bake_repo(tmp_path)
    before = _hash_in(root)
    _append(_role_task(root, "baked-role"), "\n# bake change\n")
    assert _hash_in(root) != before


def test_manifest_hash_flips_on_transitively_included_role(tmp_path):
    # A role reached only through a NESTED include_role (baked-role -> nested-role)
    # must also enter the digest - the mq-exporter->mq-install / rdqm-install->
    # mq-diag-logging shape a direct-only parse would miss.
    root = _fake_bake_repo(tmp_path)
    before = _hash_in(root)
    _append(_role_task(root, "nested-role"), "\n# nested change\n")
    assert _hash_in(root) != before


def test_manifest_hash_stable_on_no_op_and_unbaked_role(tmp_path):
    # No-op is stable, and a role NO bake playbook reaches (a per-run configure
    # role) does NOT invalidate the box - the precision that keeps configure-role
    # edits from spuriously rebuilding every fat box.
    root = _fake_bake_repo(tmp_path)
    before = _hash_in(root)
    assert _hash_in(root) == before  # deterministic no-op
    _append(_role_task(root, "unbaked-role"), "\n# unrelated\n")
    assert _hash_in(root) == before


# --------------------------------------------------------------------------- #
# _manifest-hash.sh — lab/mq-version pin folds into the digest for MQ-bearing  #
# boxes only (#1087)                                                          #
#                                                                             #
# The MQ version is sourced from the single authoritative pin at              #
# lab/mq-version via an Ansible `lookup('file', ...)` in versions.yml, so the #
# resolved value never appears in versions.yml's own text — digesting only    #
# versions.yml missed a pin bump entirely (a flipped pin left an MQ-bearing    #
# box at `hash: match` / REUSE, so a bootstrap silently cloned the old-version #
# box). The fix folds the pin CONTENT into the digest, but ONLY for the        #
# MQ-bearing boxes (those that bake an MQ install: mq-nativeha-rhel9,          #
# mq-nativeha-ubuntu24, mq-client-ubuntu24, mq-rdqm-rhel9); the MQ-version-           #
# independent commons boxes (obs/infra, pcmk) must stay REUSE across          #
# a pin bump so a version change doesn't spuriously rebake them.               #
# --------------------------------------------------------------------------- #
def _fake_bake_repo_with_pin(tmp_path: Path) -> Path:
    """Extend the minimal bake repo with a lab/mq-version pin and a second bake
    playbook for an MQ-bearing box. `infra-ubuntu26` (-> infra) is a commons
    box; `mq-client-ubuntu24` (-> mq-ubuntu) is MQ-bearing. Both hash cleanly against
    this tree so a single pin edit can be checked against both at once."""
    root = _fake_bake_repo(tmp_path)
    (root / "lab").mkdir(parents=True, exist_ok=True)
    (root / "lab" / "mq-version").write_text("9.4.5.0\n")
    # An MQ-bearing box's bake playbook (stem `mq-ubuntu`), structurally like the
    # commons `bake-infra.yml` so only the pin distinguishes the two boxes' fate.
    (root / "ansible" / "bake-mq-ubuntu.yml").write_text(
        "- name: bake\n"
        "  hosts: bake\n"
        "  tasks:\n"
        "    - name: install baked-role\n"
        "      ansible.builtin.include_role:\n"
        "        name: baked-role\n"
    )
    return root


def test_manifest_hash_flips_on_pin_change_for_mq_bearing_box(tmp_path):
    # (a) Acceptance: bumping lab/mq-version flips an MQ-bearing box's digest, so
    # build-fatbox.sh sees CURRENT_HASH != stored and decides BUILD (not REUSE).
    root = _fake_bake_repo_with_pin(tmp_path)
    before = _hash_in(root, "mq-client-ubuntu24")
    assert len(before) == 64
    (root / "lab" / "mq-version").write_text("10.0.0.0\n")
    assert _hash_in(root, "mq-client-ubuntu24") != before  # pin bump forces a rebake


def test_manifest_hash_stable_on_pin_change_for_commons_box(tmp_path):
    # (b) Acceptance: the same pin bump leaves a commons (non-MQ) box's digest
    # unchanged, so it stays REUSE — a version change never spuriously rebakes it.
    root = _fake_bake_repo_with_pin(tmp_path)
    before = _hash_in(root, "infra-ubuntu26")
    (root / "lab" / "mq-version").write_text("10.0.0.0\n")
    assert _hash_in(root, "infra-ubuntu26") == before  # commons box untouched


def test_manifest_hash_pin_is_box_scoped_not_global(tmp_path):
    # Guard the scoping directly: at a fixed pin, the MQ-bearing box's digest
    # embeds the pin while the commons box's does not — proven by the fact that
    # only one of them moves when the pin moves (above). Here we also assert the
    # MQ-bearing digest is deterministic at a fixed pin (no spurious churn).
    root = _fake_bake_repo_with_pin(tmp_path)
    assert _hash_in(root, "mq-client-ubuntu24") == _hash_in(root, "mq-client-ubuntu24")


# --------------------------------------------------------------------------- #
# _manifest-hash.sh — shared files outside ansible/roles/ (#1324)              #
#                                                                             #
# Roles pull in the per-OS-version loader as `include_tasks:                   #
# ../../../tasks/os-vars.yml` (#1277): a file OUTSIDE ansible/roles/, so the   #
# role closure (#649) never digested it and an edit to it alone left every     #
# box at REUSE. The closure now follows path literals that resolve to existing #
# files under ansible/ but outside ansible/roles/, from roles, the bake        #
# playbook and the shared files themselves.                                    #
# --------------------------------------------------------------------------- #
_OS_VARS_INCLUDE = (
    "- name: load per-OS vars\n"
    "  ansible.builtin.include_tasks: ../../../tasks/os-vars.yml\n"
    "  vars:\n"
    "    os_vars_role: baked-role\n"
)


def _fake_repo_with_shared(tmp_path: Path) -> Path:
    """The minimal bake repo plus ansible/tasks/os-vars.yml, included by
    `baked-role` from a non-main task file the way the real roles do, and an
    unreferenced ansible/tasks/unrelated.yml no bake reaches."""
    root = _fake_bake_repo(tmp_path)
    tasks = root / "ansible" / "tasks"
    tasks.mkdir()
    (tasks / "os-vars.yml").write_text("- name: load vars\n  ansible.builtin.debug: {}\n")
    (tasks / "unrelated.yml").write_text("- name: unrelated\n  ansible.builtin.debug: {}\n")
    (root / "ansible" / "roles" / "baked-role" / "tasks" / "install.yml").write_text(
        _OS_VARS_INCLUDE
    )
    return root


def _shared(root: Path, name: str) -> Path:
    return root / "ansible" / "tasks" / name


def test_manifest_hash_flips_on_shared_include_change(tmp_path):
    # The headline acceptance: editing the shared loader a baked role includes flips it.
    root = _fake_repo_with_shared(tmp_path)
    before = _hash_in(root)
    _append(_shared(root, "os-vars.yml"), "# loader change\n")
    assert _hash_in(root) != before


def test_manifest_hash_flips_on_shared_include_move(tmp_path):
    # Repointing the include at an identical copy under another name flips the hash,
    # and restoring the tree restores it (the digest is a pure function of the tree).
    root = _fake_repo_with_shared(tmp_path)
    before = _hash_in(root)
    shutil.copy(_shared(root, "os-vars.yml"), _shared(root, "os-vars-v2.yml"))
    _shared(root, "os-vars.yml").unlink()
    include = root / "ansible" / "roles" / "baked-role" / "tasks" / "install.yml"
    include.write_text(include.read_text().replace("os-vars.yml", "os-vars-v2.yml"))
    after = _hash_in(root)
    include.write_text(include.read_text().replace("os-vars-v2.yml", "os-vars.yml"))
    _shared(root, "os-vars-v2.yml").rename(_shared(root, "os-vars.yml"))
    assert after != before
    assert _hash_in(root) == before  # restoring the tree restores the digest


def test_manifest_hash_stable_on_unreferenced_or_commented_shared_file(tmp_path):
    # A shared file nothing in the closure names stays out, and a path literal only
    # in a COMMENT does not pull a file in (the real vars files mention
    # ansible/tasks/os-vars.yml in their header comments). A literal that names no
    # real file (a templated path, a task name) is dropped rather than erroring.
    root = _fake_repo_with_shared(tmp_path)
    _append(
        _role_task(root, "baked-role"),
        "# see ../../../tasks/unrelated.yml\n"
        "- name: render prometheus.yml\n"
        "  ansible.builtin.include_vars: '{{ playbook_dir }}/vars/missing.yml'\n",
    )
    before = _hash_in(root)
    _append(_shared(root, "unrelated.yml"), "# unrelated change\n")
    assert _hash_in(root) == before


def test_manifest_hash_stable_on_shared_file_only_unbaked_roles_include(tmp_path):
    # Precision: a shared file reached only from a role NO bake playbook pulls in
    # (a per-run configure role) does not enter the box's digest.
    root = _fake_repo_with_shared(tmp_path)
    _append(_role_task(root, "unbaked-role"), _OS_VARS_INCLUDE.replace("os-vars", "unrelated"))
    before = _hash_in(root)
    _append(_shared(root, "unrelated.yml"), "# unrelated change\n")
    assert _hash_in(root) == before


def test_manifest_hash_follows_shared_file_transitively(tmp_path):
    # A shared file that itself includes another shared file, and include_role's a
    # role nothing else reaches: both are folded in (closure follows the shared edge).
    root = _fake_repo_with_shared(tmp_path)
    _append(
        _shared(root, "os-vars.yml"),
        "- name: more\n"
        "  ansible.builtin.import_tasks: unrelated.yml\n"
        "- name: pull a role\n"
        "  ansible.builtin.include_role:\n"
        "    name: unbaked-role\n",
    )
    before = _hash_in(root)
    _append(_shared(root, "unrelated.yml"), "# nested shared change\n")
    middle = _hash_in(root)
    assert middle != before
    _append(_role_task(root, "unbaked-role"), "\n# reached via the shared file\n")
    assert _hash_in(root) != middle


def test_manifest_hash_flips_on_shared_file_the_bake_playbook_includes(tmp_path):
    # A bake playbook's own playbook-relative include (tasks/<x>.yml) is digested too.
    root = _fake_repo_with_shared(tmp_path)
    _append(
        root / "ansible" / "bake-infra.yml",
        "    - name: shared bake step\n      ansible.builtin.include_tasks: tasks/unrelated.yml\n",
    )
    before = _hash_in(root)
    _append(_shared(root, "unrelated.yml"), "# bake-level shared change\n")
    assert _hash_in(root) != before


def _real_repo_copy(tmp_path: Path) -> Path:
    """A copy of the real inputs _manifest-hash.sh reads: ansible/, the script, and the
    MQ-version pin - so the real tree can be edited without touching the worktree."""
    root = tmp_path / "real"
    shutil.copytree(_REPO / "ansible", root / "ansible", symlinks=True)
    (root / "lab").mkdir(parents=True)
    shutil.copytree(_REPO / "lab" / "boxes", root / "lab" / "boxes")
    shutil.copy(_REPO / "lab" / "mq-version", root / "lab" / "mq-version")
    return root


def _real_hashes(root: Path) -> dict[str, str]:
    return {name: _hash_in(root, name) for name in sorted(box_bakes())}


def _os_vars_includers(root: Path) -> list[Path]:
    """Every role task file that includes ansible/tasks/os-vars.yml on a code line
    (comments in the vars files also name the loader; they don't include it)."""
    found = []
    for path in sorted((root / "ansible" / "roles").glob("*/tasks/**/*.yml")):
        code = [ln for ln in path.read_text().splitlines() if not ln.lstrip().startswith("#")]
        if any("tasks/os-vars.yml" in ln for ln in code):
            found.append(path)
    return found


def test_manifest_hash_real_os_vars_edit_flips_exactly_the_reaching_boxes(tmp_path):
    # The acceptance on the REAL tree. The expected set is DERIVED, not hard-coded:
    # the boxes whose digest flips when the roles that include os-vars.yml are edited
    # (the role closure, #649) are exactly the boxes that reach the loader, so they
    # must be exactly the ones an os-vars.yml edit flips - and no others.
    root = _real_repo_copy(tmp_path)
    includers = _os_vars_includers(root)
    assert includers, "no role includes ansible/tasks/os-vars.yml - update this test"
    base = _real_hashes(root)

    shared = root / "ansible" / "tasks" / "os-vars.yml"
    original = shared.read_text()
    _append(shared, "# loader change\n")
    flipped_by_loader = {n for n, h in _real_hashes(root).items() if h != base[n]}
    shared.write_text(original)

    for path in includers:
        _append(path, "\n# includer change\n")
    flipped_by_includers = {n for n, h in _real_hashes(root).items() if h != base[n]}

    assert flipped_by_loader == flipped_by_includers
    assert flipped_by_loader  # the loader reaches at least one box
    # Every baked box reaches it: each Ubuntu box via cloud-init-trim (#1282), each RHEL
    # box via its MQ install role.
    assert flipped_by_loader == set(box_bakes())
