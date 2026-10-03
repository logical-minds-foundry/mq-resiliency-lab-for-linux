"""Huge-page-backed guest RAM — the topology lever, sizing, resolved render, play pin (#1241).

Spike #1240: on Apple Silicon nested virtualization 2 MiB huge-page backing removes the
300-1000x page-fault slowdown a memory-churning guest causes. The lever lives in the
macos env profile; it reaches the Vagrantfile through the resolved topology (like the
#1202 `cpus` override), and mqlab sizes + reserves the pages before any `vagrant up`.
"""

from __future__ import annotations

import copy
from typing import Any

import pytest
import yaml

from mqlab import hugepages, platforms
from mqlab import topology as t
from mqlab.hostfacts import AARCH64, X86_64, HostFacts
from mqlab.paths import repo_root
from mqlab.versions import load_catalog, node_boxes

X86_KVM = HostFacts(arch=X86_64, kvm=True, distro_family="dnf", in_vergil=True)
ARM_KVM = HostFacts(arch=AARCH64, kvm=True, distro_family="apt", in_vergil=True)

BASE: dict[str, Any] = {
    "defaults": {"cpus": 1, "memory": 1024},
    "boot_batch": 4,
    "nodes": {
        "obs": {"box": "obs", "cpus": 12, "memory": 10240},
        "nha-a1": {"box": "infra", "memory": 2048},
        "mon-probe": {"box": "mq-client"},
        "odd": {"box": "infra", "memory": 1025},
    },
    "env_profiles": {"macos": {"memory_backing": "hugepages"}, "cloud": {}},
}


def _boxes(topo: dict[str, Any]) -> dict[str, Any]:
    """The version layer's per-node box selection (what ensure_resolved passes in)."""
    return node_boxes(topo, load_catalog())


def _real_topology() -> dict[str, Any]:
    data: dict[str, Any] = yaml.safe_load((repo_root() / "lab" / "topology.yaml").read_text())
    return data


# --------------------------------------------------------------------------- #
# the topology lever
# --------------------------------------------------------------------------- #
def test_macos_profile_sets_hugepage_backing():
    out = t.effective(copy.deepcopy(BASE), env="macos")
    assert out["memory_backing"] == "hugepages"
    assert t.memory_backing(out) == "hugepages"
    assert hugepages.enabled(out)


@pytest.mark.parametrize("env", ["", "cloud"])
def test_default_and_cloud_have_no_backing(env):
    out = t.effective(copy.deepcopy(BASE), env=env)
    assert "memory_backing" not in out
    assert t.memory_backing(out) is None
    assert not hugepages.enabled(out)


def test_misspelled_lever_is_rejected():
    base = copy.deepcopy(BASE)
    base["env_profiles"]["macos"] = {"memory_backin": "hugepages"}
    with pytest.raises(ValueError, match="may not override 'memory_backin'"):
        t.effective(base, env="macos")


@pytest.mark.parametrize("value", ["huge", "HugePages", True, None, 2048])
def test_invalid_lever_value_is_rejected(value):
    base = copy.deepcopy(BASE)
    base["env_profiles"]["macos"] = {"memory_backing": value}
    with pytest.raises(ValueError, match=r"env_profiles\.macos\.memory_backing must be one of"):
        t.effective(base, env="macos")


def test_invalid_value_in_base_topology_fails_loud_for_consumers():
    with pytest.raises(ValueError, match=r"topology\.memory_backing must be one of"):
        t.memory_backing({"memory_backing": "4k"})
    with pytest.raises(ValueError, match="must be one of"):
        hugepages.enabled({"memory_backing": "4k"})


def test_real_topology_macos_is_hugepage_backed_and_cloud_is_not():
    raw = _real_topology()
    assert hugepages.enabled(t.effective(copy.deepcopy(raw), env="macos"))
    assert not hugepages.enabled(t.effective(copy.deepcopy(raw), env="cloud"))
    assert not hugepages.enabled(t.effective(copy.deepcopy(raw), env=""))


# --------------------------------------------------------------------------- #
# sizing
# --------------------------------------------------------------------------- #
def test_guest_memory_mirrors_the_provider_resolution():
    topo = t.effective(copy.deepcopy(BASE), env="macos")
    assert hugepages.guest_memory_mib(topo, "obs") == 10240
    assert hugepages.guest_memory_mib(topo, "mon-probe") == 1024  # defaults.memory
    no_defaults = {"nodes": {"bare": None}}
    assert hugepages.guest_memory_mib(no_defaults, "bare") == 1024  # platforms' fallback
    resolved = platforms.resolve(topo, X86_KVM, _boxes(topo))
    for guest in ("obs", "nha-a1", "mon-probe", "odd"):
        assert hugepages.guest_memory_mib(topo, guest) == resolved[guest].memory


def test_unknown_guest_fails_loud():
    with pytest.raises(ValueError, match="'ghost' is not a topology node"):
        hugepages.guest_memory_mib(BASE, "ghost")
    with pytest.raises(ValueError, match="not a topology node"):
        hugepages.guest_memory_mib({}, "ghost")


@pytest.mark.parametrize(("mib", "pages"), [(1024, 512), (1025, 513), (1, 1), (10240, 5120)])
def test_pages_round_up_to_whole_2mib_pages(mib, pages):
    assert hugepages.pages_for(mib) == pages


def test_needed_pages_sums_guests_plus_margin():
    topo = t.effective(copy.deepcopy(BASE), env="macos")
    guests = ["obs", "nha-a1", "mon-probe", "odd"]
    expected = 5120 + 1024 + 512 + 513 + hugepages.MARGIN_PAGES
    assert hugepages.needed_pages(topo, guests) == expected


def test_nothing_to_boot_needs_nothing():
    assert hugepages.needed_pages(BASE, []) == 0


# --------------------------------------------------------------------------- #
# reserve / release commands + meminfo
# --------------------------------------------------------------------------- #
def test_reserve_command_runs_the_localhost_play():
    cmd = hugepages.reserve_command(4736)
    assert cmd.argv == [
        "ansible-playbook",
        "host-hugepages.yml",
        "-c",
        "local",
        "-i",
        "localhost,",
        "-e",
        "hugepages_needed=4736",
    ]
    assert cmd.cwd == repo_root() / "ansible"
    assert "uv" not in cmd.argv


@pytest.mark.parametrize("needed", [0, -1])
def test_reserve_command_refuses_a_non_positive_size(needed):
    with pytest.raises(ValueError, match="positive page count"):
        hugepages.reserve_command(needed)


def test_release_command():
    assert hugepages.release_command().argv[-2:] == ["-e", "hugepages_release=true"]


MEMINFO = (
    "MemTotal:       65708740 kB\n"
    "HugePages_Total:     100\n"
    "HugePages_Free:       60\n"
    "HugePages_Rsvd:       10\n"
    "HugePages_Surp:        0\n"
    "Hugepagesize:       2048 kB\n"
)


def test_read_meminfo_parses_the_huge_page_counters(tmp_path):
    path = tmp_path / "meminfo"
    path.write_text(MEMINFO)
    info = hugepages.read_meminfo(path)
    assert info == {
        "HugePages_Total": 100,
        "HugePages_Free": 60,
        "HugePages_Rsvd": 10,
        "Hugepagesize": 2048,
    }
    assert hugepages.summary(info) == "HugePages_Total=100 HugePages_Free=60 HugePages_Rsvd=10"


def test_read_meminfo_defaults_to_the_module_path(monkeypatch, tmp_path):
    path = tmp_path / "meminfo"
    path.write_text(MEMINFO)
    monkeypatch.setattr(hugepages, "MEMINFO", path)
    assert hugepages.read_meminfo()["HugePages_Total"] == 100


def test_read_meminfo_fails_loud_without_hugetlb(tmp_path):
    path = tmp_path / "meminfo"
    path.write_text("MemTotal:       65708740 kB\n")
    with pytest.raises(RuntimeError, match="kernel lacks hugetlb support"):
        hugepages.read_meminfo(path)


# --------------------------------------------------------------------------- #
# resolved topology -> Vagrantfile
# --------------------------------------------------------------------------- #
@pytest.mark.parametrize("facts", [X86_KVM, ARM_KVM])
def test_resolved_render_carries_backing_for_every_guest(facts):
    topo = t.effective(copy.deepcopy(BASE), env="macos")
    rendered = yaml.safe_load(platforms.render_resolved(topo, facts, _boxes(topo)))
    assert {n["memory_backing"] for n in rendered["nodes"].values()} == {"hugepages"}


def test_resolved_render_without_lever_carries_none():
    topo = t.effective(copy.deepcopy(BASE), env="cloud")
    rendered = yaml.safe_load(platforms.render_resolved(topo, X86_KVM, _boxes(topo)))
    assert {n["memory_backing"] for n in rendered["nodes"].values()} == {None}


def test_ensure_resolved_follows_mqlab_env(monkeypatch, tmp_path):
    """The Vagrantfile reads build/work/lab/topology.resolved.yaml; the effective lever
    must land there (never read from MQLAB_ENV by the Vagrantfile)."""
    raw = copy.deepcopy(BASE)
    monkeypatch.setattr(t, "_read_raw", lambda: copy.deepcopy(raw))
    monkeypatch.setattr(platforms, "resolved_topology_path", lambda: tmp_path / "r.yaml")
    monkeypatch.setenv(t.ENV_VAR, "macos")
    rendered = yaml.safe_load(platforms.ensure_resolved(facts=X86_KVM).read_text())
    assert rendered["nodes"]["obs"]["memory_backing"] == "hugepages"
    monkeypatch.delenv(t.ENV_VAR)
    rendered = yaml.safe_load(platforms.ensure_resolved(facts=X86_KVM).read_text())
    assert rendered["nodes"]["obs"]["memory_backing"] is None


def test_vagrantfile_applies_memorybacking_from_the_resolved_node():
    vf = (repo_root() / "lab" / "Vagrantfile").read_text()
    assert 'lv.memorybacking n["memory_backing"].to_sym if n["memory_backing"]' in vf
    assert "MQLAB_ENV" not in vf  # the lever arrives via the resolved topology only


# --------------------------------------------------------------------------- #
# the play
# --------------------------------------------------------------------------- #
def _play_tasks() -> list[dict[str, Any]]:
    plays = yaml.safe_load((repo_root() / "ansible" / hugepages.PLAYBOOK).read_text())
    (play,) = plays
    assert play["hosts"] == "localhost"
    assert play["become"] is True
    out: list[dict[str, Any]] = []
    for task in play["tasks"]:
        out.extend(task.get("block", [task]))
    return out


def test_play_reclaim_task_name_is_pinned_for_the_perf_note():
    names = [task["name"] for task in _play_tasks()]
    assert hugepages.RECLAIM_TASK in names


def test_play_is_runtime_only_sysctl():
    text = (repo_root() / "ansible" / hugepages.PLAYBOOK).read_text()
    assert "sysctl.d" not in text.replace("never /etc/sysctl.d", "")
    tasks = [task for task in _play_tasks() if "ansible.builtin.command" in task]
    argvs = [task["ansible.builtin.command"]["argv"] for task in tasks]
    assert ["sysctl", "-w", "vm.nr_hugepages=0"] in argvs
