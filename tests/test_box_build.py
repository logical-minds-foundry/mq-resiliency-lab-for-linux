"""Fat-box builder (#603): mqlab resolution of the provision-then-snapshot fat
boxes, and the build-fatbox.sh / _manifest-hash.sh decision surface.

The Python half TDDs that `_LOCAL_BOX_BUILDERS` now maps the fat boxes
(mq-rdqm-rhel9 / obs-ubuntu2404 / infra-ubuntu2404) to build-fatbox.sh while
keeping rhel/9.6-x86_64 -> build-box.sh (the base-OS builder the RHEL fat build
depends on), and that `_box_build_steps` passes `--box <name>` for fatbox
entries only.

The shell half exercises only the cheap, side-effect-free arg-validation and
`--dry-run` decision path (REUSE / BUILD / stale), driving the cache dir at a
tmp path via LAB_BOX_CACHE_DIR so no host-durable state is touched.
"""

from __future__ import annotations

import subprocess
from pathlib import Path

from mqlab import cli
from mqlab.hostfacts import X86_64, HostFacts

_REPO = Path(__file__).resolve().parents[1]
_FATBOX = _REPO / "lab" / "boxes" / "build-fatbox.sh"
_MANIFEST = _REPO / "lab" / "boxes" / "_manifest-hash.sh"


# --------------------------------------------------------------------------- #
# mqlab resolution                                                            #
# --------------------------------------------------------------------------- #
def test_needed_local_boxes_resolves_fat_rdqm_box(monkeypatch):
    monkeypatch.setattr(
        cli,
        "_resolved_nodes",
        lambda: {
            "rdqm-a1": {"box": "mq-rdqm-rhel9"},
            "obs": {"box": "obs-ubuntu2404"},
        },
    )
    needed = cli._needed_local_boxes(["rdqm-a1", "obs"])
    assert "mq-rdqm-rhel9" in needed
    assert needed["mq-rdqm-rhel9"].endswith("build-fatbox.sh")
    assert "obs-ubuntu2404" in needed
    assert needed["obs-ubuntu2404"].endswith("build-fatbox.sh")


def test_needed_local_boxes_resolves_fat_infra_box(monkeypatch):
    monkeypatch.setattr(
        cli, "_resolved_nodes", lambda: {"infra-client": {"box": "infra-ubuntu2404"}}
    )
    needed = cli._needed_local_boxes(["infra-client"])
    assert needed["infra-ubuntu2404"].endswith("build-fatbox.sh")


def test_local_box_builders_registry_covers_base_and_fat_boxes():
    reg = cli._LOCAL_BOX_BUILDERS
    assert reg["rhel/9.6-x86_64"].endswith("rhel96/build-box.sh")  # base-OS builder
    for fat in ("mq-rdqm-rhel9", "obs-ubuntu2404", "infra-ubuntu2404"):
        assert reg[fat].endswith("build-fatbox.sh")


def test_box_build_steps_passes_box_flag_for_fatbox(monkeypatch, tmp_path):
    monkeypatch.setenv("MQLAB_REPO_ROOT", str(tmp_path))
    facts = HostFacts(arch=X86_64, kvm=True, distro_family="dnf", in_vergil=True)
    steps = cli._box_build_steps({"mq-rdqm-rhel9": "lab/boxes/build-fatbox.sh"}, {}, facts)
    assert [s.command.argv for s in steps] == [
        [
            "bash",
            str(tmp_path / "lab/boxes/build-fatbox.sh"),
            "--box",
            "mq-rdqm-rhel9",
            "--domain-type",
            "kvm",
            "--cpu-mode",
            "host-passthrough",
        ]
    ]


def test_box_build_steps_base_builder_has_no_box_flag(monkeypatch, tmp_path):
    monkeypatch.setenv("MQLAB_REPO_ROOT", str(tmp_path))
    facts = HostFacts(arch=X86_64, kvm=True, distro_family="dnf", in_vergil=True)
    steps = cli._box_build_steps({"rhel/9.6-x86_64": "lab/boxes/rhel96/build-box.sh"}, {}, facts)
    assert "--box" not in steps[0].command.argv  # base-OS builder is not box-parameterized


# --------------------------------------------------------------------------- #
# build-fatbox.sh — arg validation                                            #
# --------------------------------------------------------------------------- #
def _run(*args: str, cache_dir: Path | None = None) -> subprocess.CompletedProcess[str]:
    env = None
    if cache_dir is not None:
        import os

        env = {**os.environ, "LAB_BOX_CACHE_DIR": str(cache_dir)}
    return subprocess.run(  # noqa: S603
        ["bash", str(_FATBOX), *args],
        capture_output=True,
        text=True,
        check=False,
        env=env,
    )


def test_no_box_dies_with_usage():
    result = _run("--domain-type", "kvm", "--cpu-mode", "host-passthrough")
    assert result.returncode != 0
    assert "--box" in (result.stderr + result.stdout)


def test_unknown_box_dies():
    result = _run("--box", "no-such-box", "--domain-type", "kvm", "--cpu-mode", "host-passthrough")
    assert result.returncode != 0
    assert "no-such-box" in (result.stderr + result.stdout)


def test_missing_domain_type_dies():
    result = _run("--box", "mq-rdqm-rhel9")
    assert result.returncode != 0
    assert "--domain-type" in (result.stderr + result.stdout)


def test_invalid_cpu_mode_dies():
    result = _run("--box", "mq-rdqm-rhel9", "--domain-type", "kvm", "--cpu-mode", "bogus")
    assert result.returncode != 0
    assert "--cpu-mode" in (result.stderr + result.stdout)


def test_unknown_flag_dies():
    result = _run("--box", "mq-rdqm-rhel9", "--domain-type", "kvm", "--cpu-mode", "maximum", "--x")
    assert result.returncode != 0


# --------------------------------------------------------------------------- #
# build-fatbox.sh — --dry-run decision surface                                #
# --------------------------------------------------------------------------- #
def _dry_run(cache_dir: Path, *extra: str) -> subprocess.CompletedProcess[str]:
    return _run(
        "--box",
        "mq-rdqm-rhel9",
        "--domain-type",
        "kvm",
        "--cpu-mode",
        "host-passthrough",
        "--dry-run",
        *extra,
        cache_dir=cache_dir,
    )


def _manifest_hash(box: str) -> str:
    out = subprocess.run(  # noqa: S603
        ["bash", str(_MANIFEST), box], capture_output=True, text=True, check=True
    )
    return out.stdout.strip()


def _seed_cached_box(cache_dir: Path, box: str, *, age_days: int = 0) -> Path:
    cache_dir.mkdir(parents=True, exist_ok=True)
    boxfile = cache_dir / f"{box}.box"
    boxfile.write_text("fake box tarball\n")
    (cache_dir / f"{box}.manifest-hash").write_text(_manifest_hash(box) + "\n")
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
    (cache_dir / "mq-rdqm-rhel9.manifest-hash").write_text("stale-different-hash\n")
    result = _dry_run(cache_dir)
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
    result = _run(
        "--box",
        "mq-rdqm-rhel9",
        "--domain-type",
        "kvm",
        "--cpu-mode",
        "host-passthrough",
        cache_dir=cache_dir,
    )
    assert result.returncode != 0
    assert "--rebuild-box" in (result.stderr + result.stdout)


# --------------------------------------------------------------------------- #
# _manifest-hash.sh                                                           #
# --------------------------------------------------------------------------- #
def test_manifest_hash_is_stable_and_box_specific():
    a = _manifest_hash("mq-rdqm-rhel9")
    assert a == _manifest_hash("mq-rdqm-rhel9")  # deterministic
    assert a != _manifest_hash("obs-ubuntu2404")  # box (bake playbook) enters the hash
    assert len(a) == 64  # sha256 hex digest


def test_manifest_hash_requires_a_box():
    result = subprocess.run(  # noqa: S603
        ["bash", str(_MANIFEST)], capture_output=True, text=True, check=False
    )
    assert result.returncode != 0
