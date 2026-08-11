"""Topology coverage for the pcmk-ubuntu Pacemaker/SAN stack under #103 T7.

The six Pacemaker cluster nodes (pcmk-a1..3, pcmk-b1..3) boot the baked,
host-resolved pcmk-ubuntu fat box so a bootstrap skips the MQ product install; the
SAN targets (san-a, san-b) carry no IBM-MQ payload (design D8) and stay on the
host-resolved base Ubuntu box, untouched. Assertions are host-arch-independent —
they name PLATFORMS, never an arch-suffixed cache value — because CI is x86 and the
human validates on arm64.
"""

import pathlib

import yaml

from mqlab.stacks import lab_stacks, stack_dr_hosts, stack_members, stack_members_effective


def _topology() -> dict:
    return yaml.safe_load(pathlib.Path("lab/topology.yaml").read_text())


def test_pcmk_ubuntu_declares_pcmk_b_as_its_dr_group():
    # #188/#997 (Option B): the dr_groups marker names the DR cluster group `--no-dr`
    # skips. Only pcmk_b (the 3 heavy DR cluster nodes) is dropped — san_b stays up so
    # the san_a→san_b HA DRBD pair keeps its two-peer bring-up. Its presence is the
    # signal that this stack supports HA-only bring-up.
    assert lab_stacks()["pcmk-ubuntu"].dr_groups == ["pcmk_b"]


def test_pcmk_ubuntu_dr_hosts_are_the_three_pcmk_b_nodes():
    # #188: the dr_groups marker resolves to exactly the three site-B cluster guests
    # (NOT san-b, which Option B keeps up as the DRBD secondary).
    assert stack_dr_hosts("pcmk-ubuntu") == ["pcmk-b1", "pcmk-b2", "pcmk-b3"]


def test_pcmk_ubuntu_effective_members_drop_pcmk_b_but_keep_san_b_under_no_dr():
    # #997 Option B: --no-dr drops the pcmk-b* DR cluster nodes but KEEPS san-b (the
    # DRBD secondary), so the HA storage mirror stays two-peer; a full bootstrap is
    # unchanged. Pins the Option-B semantics: pcmk-b* out, san-b in.
    full = stack_members("pcmk-ubuntu")
    assert full is not None
    effective = stack_members_effective("pcmk-ubuntu", no_dr=True)
    assert effective is not None
    assert effective == [m for m in full if not m.startswith("pcmk-b")]
    assert "pcmk-b1" not in effective
    assert "san-b" in effective  # Option B keeps the DR-peer SAN up
    assert stack_members_effective("pcmk-ubuntu", no_dr=False) == full


def test_pcmk_ubuntu_cluster_nodes_boot_the_baked_fat_box():
    # #103 T7: the six Pacemaker cluster nodes are repointed to the baked pcmk-ubuntu
    # fat box (not the bare host-resolved Ubuntu base), so a bootstrap skips the MQ
    # install; the per-run cluster/SAN/STONITH/QM formation still runs. The box carries
    # NO `arch:` pin, so host-arch resolution is preserved at the box layer (native
    # arm64 on the Mac, x86_64 on the cloud) — it can coexist with the Ubuntu arms.
    topo = _topology()
    nodes = topo["nodes"]
    for h in ("pcmk-a1", "pcmk-a2", "pcmk-a3", "pcmk-b1", "pcmk-b2", "pcmk-b3"):
        assert nodes[h]["platform"] == "pcmk-ubuntu"
    assert "arch" not in topo["boxes"]["pcmk-ubuntu"]
    # D8: san-a/san-b carry no IBM-MQ payload, so they are NOT baked and NOT repointed
    # — they keep booting the host-resolved base Ubuntu box (no `platform:` key means
    # default_platform(facts)). SAN treatment is deferred to the SAN-hosts epic (#108).
    for h in ("san-a", "san-b"):
        assert "platform" not in nodes[h]
