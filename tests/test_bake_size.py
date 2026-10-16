"""Per-box bake-guest sizing (#1596).

build-fatbox.sh used to define every transient bake domain at a fixed 2 vCPU / 2048 MiB.
The obs bake starts the OpenSearch / Dashboards / Data Prepper JVMs through its handlers,
and in that 2 GB, swapless guest the next play's SSH session dropped (145 MB free). The
bake guest is now sized from the largest topology node that boots the box
(platforms.bake_size), floored at the old 2 / 2048, capped at BAKE_CAP_*, and passed to
the builder as --bake-cpus / --bake-memory.

The size is the build environment, not box content: it is NOT folded into the manifest
hash, so a resize never rebakes a cached box (covered at the bottom of this file).
"""

from __future__ import annotations

import copy
import os
import subprocess
from pathlib import Path
from typing import Any

import pytest
import typer
import yaml

from mqlab import box, topology
from mqlab import platforms as p
from mqlab.versions import load_catalog, node_boxes
from tests.boxfleet import (
    REAL_TOPOLOGY,
    X86_FACTS,
    fatbox_args,
    manifest_hash_args,
    real_bake_topology,
    x86_fleet,
)

_REPO = Path(__file__).resolve().parents[1]
_FATBOX = _REPO / "lab" / "boxes" / "build-fatbox.sh"
_MANIFEST = _REPO / "lab" / "boxes" / "_manifest-hash.sh"


def _topo(**nodes: dict[str, Any]) -> dict[str, Any]:
    return {"defaults": {"cpus": 1, "memory": 1024}, "nodes": nodes}


# --------------------------------------------------------------------------- #
# platforms.bake_size                                                         #
# --------------------------------------------------------------------------- #
def test_bake_size_is_the_max_across_the_nodes_that_boot_the_role():
    topo = _topo(
        a={"box": "obs", "cpus": 4, "memory": 3072},
        b={"box": "obs", "cpus": 3, "memory": 8192},
        c={"box": "infra", "cpus": 8, "memory": 9000},  # another role: ignored
    )
    assert p.bake_size(topo, "obs") == p.BakeSize(cpus=4, memory=8192, nodes=("a", "b"))


def test_bake_size_is_floored_at_two_vcpu_and_2048_mib():
    # infra nodes take the topology defaults (1 / 1024): the bake keeps the old floor.
    size = p.bake_size(_topo(i={"box": "infra"}), "infra")
    assert (size.cpus, size.memory) == (p.BAKE_FLOOR_CPUS, p.BAKE_FLOOR_MEMORY_MIB) == (2, 2048)


def test_bake_size_falls_back_to_builtin_defaults_without_a_defaults_block():
    size = p.bake_size({"nodes": {"i": {"box": "infra"}}}, "infra")
    assert (size.cpus, size.memory) == (2, 2048)


def test_bake_size_fails_loud_when_no_node_boots_the_role():
    with pytest.raises(p.PlatformError, match="no topology node boots it"):
        p.bake_size(_topo(i={"box": "infra"}), "obs")


@pytest.mark.parametrize(
    ("key", "value"),
    [("cpus", 0), ("cpus", -2), ("cpus", "4"), ("cpus", True), ("memory", 2.5), ("memory", None)],
)
def test_bake_size_fails_loud_on_an_invalid_node_size(key, value):
    with pytest.raises(p.PlatformError, match=f"node o: {key} must be a positive integer"):
        p.bake_size(_topo(o={"box": "obs", key: value}), "obs")


@pytest.mark.parametrize(
    "node",
    [
        {"box": "obs", "cpus": p.BAKE_CAP_CPUS + 1},
        {"box": "obs", "memory": p.BAKE_CAP_MEMORY_MIB + 1},
    ],
)
def test_bake_size_over_the_host_cap_fails_loud_never_clamps(node):
    with pytest.raises(p.PlatformError, match="over the 12 vCPU / 12288 MiB cap"):
        p.bake_size(_topo(o=node), "obs")


def test_bake_size_fails_loud_on_a_node_without_a_box_role():
    with pytest.raises(box.VersionError, match="declares no box role"):
        p.bake_size(_topo(o={"cpus": 2}), "obs")


def test_bake_size_follows_an_env_profile_node_override():
    # A profile's per-node cpus lever sizes the bake exactly as it sizes the node.
    raw = yaml.safe_load(REAL_TOPOLOGY.read_text())
    raw["env_profiles"]["macos"] = {"nodes": {"obs": {"cpus": 8}}}
    size = p.bake_size(topology.effective(raw, env="macos"), "obs")
    assert (size.cpus, size.memory) == (8, 10240)


def test_resolve_sizes_nodes_through_the_same_authority():
    # resolve() sizes each node through node_sizing, the authority bake_size uses, so a
    # bad size fails loud on the node render too.
    topo = copy.deepcopy(real_bake_topology())
    boxes = node_boxes(topo, load_catalog())
    assert p.resolve(topo, X86_FACTS, boxes)["obs"].memory == 10240
    topo["nodes"]["obs"]["memory"] = "lots"
    with pytest.raises(p.PlatformError, match="node obs: memory must be a positive integer"):
        p.resolve(topo, X86_FACTS, boxes)


# --------------------------------------------------------------------------- #
# The committed topology                                                      #
# --------------------------------------------------------------------------- #
def test_obs_bakes_at_the_obs_node_size():
    # The #1596 failure: obs runs at 12 vCPU / 10240 MiB, so its bake guest does too.
    assert p.bake_size(real_bake_topology(), "obs") == p.BakeSize(12, 10240, ("obs",))


@pytest.mark.parametrize(
    ("role", "expected"),
    [("infra", (2, 2048)), ("mq-nativeha", (2, 2048)), ("pcmk", (2, 2048)), ("san", (2, 2048))],
)
def test_small_boxes_keep_the_old_bake_size(role, expected):
    size = p.bake_size(real_bake_topology(), role)
    assert (size.cpus, size.memory) == expected


def test_every_fat_box_in_the_fleet_has_a_bake_size_within_the_cap():
    # Guard: a topology edit that would push a box over BAKE_CAP_* (or orphan a box role)
    # fails here, in CI, not mid-bootstrap.
    topo = real_bake_topology()
    for spec in x86_fleet().values():
        if spec.role is None:
            continue
        size = p.bake_size(topo, spec.role)
        assert p.BAKE_FLOOR_CPUS <= size.cpus <= p.BAKE_CAP_CPUS, spec.name
        assert p.BAKE_FLOOR_MEMORY_MIB <= size.memory <= p.BAKE_CAP_MEMORY_MIB, spec.name


# --------------------------------------------------------------------------- #
# box.builder_args                                                            #
# --------------------------------------------------------------------------- #
def test_builder_args_pass_the_bake_size(monkeypatch):
    monkeypatch.setattr(box, "_bake_topology", real_bake_topology)
    argv = box.builder_args(x86_fleet()["obs-ubuntu26"], X86_FACTS)
    assert argv[argv.index("--bake-cpus") + 1] == "12"
    assert argv[argv.index("--bake-memory") + 1] == "10240"


def test_builder_args_size_from_the_effective_topology(monkeypatch):
    # The real seam reads topology.load(): the base plus the MQLAB_ENV profile.
    raw = yaml.safe_load(REAL_TOPOLOGY.read_text())
    raw["env_profiles"]["cloud"] = {"nodes": {"obs": {"cpus": 6}}}
    monkeypatch.setattr(topology, "_read_raw", lambda: raw)
    monkeypatch.setenv(topology.ENV_VAR, "cloud")
    argv = box.builder_args(x86_fleet()["obs-ubuntu26"], X86_FACTS)
    assert argv[argv.index("--bake-cpus") + 1] == "6"


def test_base_box_builder_takes_no_bake_size():
    argv = box.builder_args(x86_fleet()["rhel/9-x86_64"], X86_FACTS)
    assert "--bake-cpus" not in argv
    assert "--bake-memory" not in argv


def test_builder_args_exit_1_on_an_unsizable_box(monkeypatch, capsys):
    monkeypatch.setattr(box, "_bake_topology", lambda: _topo(i={"box": "infra"}))
    with pytest.raises(typer.Exit) as exc:
        box.builder_args(x86_fleet()["obs-ubuntu26"], X86_FACTS)
    assert exc.value.exit_code == 1
    assert "mqlab box: obs-ubuntu26: box role 'obs'" in capsys.readouterr().err


# --------------------------------------------------------------------------- #
# build-fatbox.sh                                                             #
# --------------------------------------------------------------------------- #
def _run(*args: str, cache_dir: Path) -> subprocess.CompletedProcess[str]:
    return subprocess.run(  # noqa: S603
        ["bash", str(_FATBOX), *args],
        capture_output=True,
        text=True,
        check=False,
        env={**os.environ, "LAB_BOX_CACHE_DIR": str(cache_dir)},
    )


def _set(args: list[str], flag: str, value: str | None) -> list[str]:
    """args with ``flag``'s value replaced, or the flag dropped when value is None."""
    i = args.index(flag)
    if value is None:
        return args[:i] + args[i + 2 :]
    return [*args[: i + 1], value, *args[i + 2 :]]


@pytest.mark.parametrize("flag", ["--bake-cpus", "--bake-memory"])
def test_fatbox_requires_the_bake_size(flag, tmp_path):
    r = _run(*_set(fatbox_args("obs-ubuntu26"), flag, None), "--dry-run", cache_dir=tmp_path)
    assert r.returncode == 2
    assert f"ERROR: {flag} is required" in r.stderr


@pytest.mark.parametrize(
    ("flag", "value", "message"),
    [
        ("--bake-cpus", "1", "--bake-cpus must be an integer >= 2"),
        ("--bake-cpus", "0", "--bake-cpus must be an integer >= 2"),
        ("--bake-cpus", "two", "--bake-cpus must be an integer >= 2"),
        ("--bake-memory", "1024", "--bake-memory must be an integer MiB >= 2048"),
        ("--bake-memory", "4G", "--bake-memory must be an integer MiB >= 2048"),
        ("--bake-memory", "-4096", "--bake-memory must be an integer MiB >= 2048"),
    ],
)
def test_fatbox_rejects_an_invalid_bake_size(flag, value, message, tmp_path):
    r = _run(*_set(fatbox_args("obs-ubuntu26"), flag, value), "--dry-run", cache_dir=tmp_path)
    assert r.returncode == 2
    assert message in r.stderr


def test_fatbox_dry_run_reports_the_bake_size(tmp_path):
    r = _run(*fatbox_args("obs-ubuntu26"), "--dry-run", cache_dir=tmp_path)
    assert r.returncode == 0, r.stderr
    assert "bake size: 12 vCPU / 10240 MiB" in r.stdout


def test_fatbox_domain_xml_takes_the_bake_size_not_a_constant():
    text = _FATBOX.read_text(encoding="utf-8")
    assert "<memory unit='MiB'>${BAKE_MEMORY}</memory>" in text
    assert "<vcpu>${BAKE_CPUS}</vcpu>" in text
    assert "<memory unit='MiB'>2048</memory>" not in text


# --------------------------------------------------------------------------- #
# Manifest hash: the bake size is build environment, not box content          #
# --------------------------------------------------------------------------- #
def _seed_cached_box(cache_dir: Path, name: str) -> None:
    arch = x86_fleet()[name].arch
    cache_dir.mkdir(parents=True, exist_ok=True)
    (cache_dir / f"{name}-{arch}.box").write_text("fake box tarball\n")
    digest = subprocess.run(  # noqa: S603
        ["bash", str(_MANIFEST), name, *manifest_hash_args(name)],
        capture_output=True,
        text=True,
        check=True,
    ).stdout.strip()
    (cache_dir / f"{name}-{arch}.manifest-hash").write_text(digest + "\n")


@pytest.mark.parametrize(("cpus", "memory"), [("2", "2048"), ("12", "10240"), ("4", "6144")])
def test_a_bake_size_change_does_not_void_a_cached_box(cpus, memory, tmp_path):
    # A box baked at the old fixed 2 / 2048 stays REUSE at any bake size: resizing the
    # build guest must not rebake every cached box.
    cache_dir = tmp_path / "boxes"
    _seed_cached_box(cache_dir, "obs-ubuntu26")
    args = _set(_set(fatbox_args("obs-ubuntu26"), "--bake-cpus", cpus), "--bake-memory", memory)
    r = _run(*args, "--dry-run", cache_dir=cache_dir)
    assert r.returncode == 0, r.stderr
    assert "decision:  REUSE" in r.stdout


def test_manifest_hash_takes_no_bake_size_input():
    # The hasher has no bake-size flag at all: the size cannot leak into the digest.
    r = subprocess.run(  # noqa: S603
        ["bash", str(_MANIFEST), "obs-ubuntu26", *manifest_hash_args("obs-ubuntu26"),
         "--bake-memory", "10240"],
        capture_output=True,
        text=True,
        check=False,
    )  # fmt: skip
    assert r.returncode == 2
    assert "unknown arg: --bake-memory" in r.stderr


def test_no_bake_role_reads_the_guest_size_into_the_box():
    # Why the hash may ignore the bake size: no ansible code reads the guest's memory or
    # CPU facts, so a bigger bake guest bakes the same box. A bake that starts sizing
    # from them must fold the bake size into _manifest-hash.sh first.
    facts = ("ansible_memtotal_mb", "ansible_memory_mb", "ansible_processor_vcpus",
             "ansible_processor_count", "ansible_processor_cores", "ansible_facts.memtotal_mb",
             "memtotal_mb", "processor_vcpus")  # fmt: skip
    hits = [
        f"{path.relative_to(_REPO)}: {fact}"
        for path in sorted((_REPO / "ansible").rglob("*"))
        if path.is_file() and path.suffix in {".yml", ".yaml", ".j2"}
        for fact in facts
        if fact in path.read_text(encoding="utf-8", errors="replace")
    ]
    assert hits == []
