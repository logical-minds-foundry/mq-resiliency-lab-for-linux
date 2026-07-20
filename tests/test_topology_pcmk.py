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


def _topology() -> dict:
    return yaml.safe_load(pathlib.Path("lab/topology.yaml").read_text())


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
