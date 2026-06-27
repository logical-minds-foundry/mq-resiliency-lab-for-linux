from __future__ import annotations

from mqlab.fleet import fleet_rows, lab_guests, parse_domain_states
from mqlab.hostfacts import AARCH64, X86_64, HostFacts

ARM = HostFacts(arch=AARCH64, kvm=True, distro_family="apt", in_vergil=True)
X86 = HostFacts(arch=X86_64, kvm=True, distro_family="dnf", in_vergil=False)

VIRSH = """\
 Id   Name             State
---------------------------------
 -    lab_rdqm-a1      shut off
 49   lab_pcmk-b1      running
"""


def test_parse_domain_states_extracts_name_and_multiword_state():
    assert parse_domain_states(VIRSH) == {"lab_rdqm-a1": "shut off", "lab_pcmk-b1": "running"}


def test_parse_domain_states_skips_chrome_and_short_lines():
    assert parse_domain_states("\n   \nId Name State\n----\n bad\n") == {}


def test_lab_guests_default_tracks_host(monkeypatch, tmp_path):
    monkeypatch.setenv("MQLAB_REPO_ROOT", str(tmp_path))
    (tmp_path / "lab").mkdir(parents=True)
    (tmp_path / "lab" / "topology.yaml").write_text(
        "defaults: { cpus: 1 }\nnodes:\n  rdqm-a1: { platform: rhel96-x86_64 }\n  pcmk-a1: {}\n"
    )
    assert lab_guests(ARM) == {"rdqm-a1": "rhel96-x86_64", "pcmk-a1": "ubuntu2404-arm64"}
    assert lab_guests(X86) == {"rdqm-a1": "rhel96-x86_64", "pcmk-a1": "ubuntu2404-x86_64"}


def test_fleet_rows_joins_state_and_stacks_and_sorts_by_columns(monkeypatch, tmp_path):
    monkeypatch.setenv("MQLAB_REPO_ROOT", str(tmp_path))
    (tmp_path / "lab").mkdir(parents=True)
    (tmp_path / "lab" / "topology.yaml").write_text(
        "groups:\n  san_a: [san-a]\n  pcmk_a: [pcmk-a1]\n  rdqm_a: [rdqm-a1]\n"
        "stacks:\n  pcmk-ubuntu:\n    groups: [san_a, pcmk_a]\n"
        "  rdqm-rhel:\n    groups: [rdqm_a]\n"
    )
    platforms = {
        "rdqm-a1": "rhel96-x86_64",
        "pcmk-a1": "ubuntu2404-arm64",
        "san-a": "ubuntu2404-arm64",
    }
    states = {"lab_pcmk-a1": "running"}
    rows = fleet_rows(platforms, states)
    by_guest = {r.guest: r for r in rows}
    assert by_guest["rdqm-a1"].state == "not created"
    assert by_guest["rdqm-a1"].stacks == "rdqm-rhel"
    assert by_guest["san-a"].stacks == "pcmk-ubuntu"
    assert by_guest["pcmk-a1"].state == "running"
    # sorted by column left-to-right (guest first): plain alphabetical by guest
    assert [r.guest for r in rows] == ["pcmk-a1", "rdqm-a1", "san-a"]
