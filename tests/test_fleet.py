from __future__ import annotations

from mqlab.fleet import fleet_rows, lab_guests, parse_domain_states

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


def test_lab_guests_reads_platform_with_default(monkeypatch, tmp_path):
    monkeypatch.setenv("MQLAB_REPO_ROOT", str(tmp_path))
    (tmp_path / "lab").mkdir(parents=True)
    (tmp_path / "lab" / "topology.yaml").write_text(
        "defaults: { platform: ubuntu2404-arm64 }\nnodes:\n"
        "  rdqm-a1: { platform: rhel96-x86_64 }\n  pcmk-a1: {}\n"
    )
    assert lab_guests() == {"rdqm-a1": "rhel96-x86_64", "pcmk-a1": "ubuntu2404-arm64"}


def test_fleet_rows_joins_state_and_setups_and_sorts_by_setup(monkeypatch, tmp_path):
    monkeypatch.setenv("MQLAB_REPO_ROOT", str(tmp_path))
    (tmp_path / "lab").mkdir(parents=True)
    (tmp_path / "lab" / "topology.yaml").write_text(
        "setups:\n  pcmk-san-ha:\n    members: [san-a, pcmk-a1]\n"
        "  rdqm-ha:\n    members: [rdqm-a1]\n"
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
    assert by_guest["rdqm-a1"].setups == "rdqm-ha"
    assert by_guest["san-a"].setups == "pcmk-san-ha"
    assert by_guest["pcmk-a1"].state == "running"
    # sorted by (setups, guest): pcmk-san-ha guests before rdqm-ha
    assert [r.guest for r in rows] == ["pcmk-a1", "san-a", "rdqm-a1"]
