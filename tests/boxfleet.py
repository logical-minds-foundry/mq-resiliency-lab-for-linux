"""Test helpers: the catalog-derived box fleet, host-independent (epic .github#280).

The fleet is generated from lab/versions.yaml (box.FLEET), and Catalog.all_boxes skips
RHEL on an aarch64 host, so the live FLEET depends on the machine running the tests.
These helpers pin an x86_64 host so every box (RHEL included) is present, and expose
the catalog inputs tests used to scrape from the shell builders' old `case` tables.
"""

from __future__ import annotations

from pathlib import Path

from mqlab import box
from mqlab.hostfacts import X86_64, HostFacts

X86_FACTS = HostFacts(arch=X86_64, kvm=True, distro_family="apt", in_vergil=True)

# The committed catalog, located from this file (not paths.repo_root, which a test may
# have pointed at a tmp dir via MQLAB_REPO_ROOT).
REAL_CATALOG = Path(__file__).resolve().parents[1] / "lab" / "versions.yaml"


def x86_fleet() -> dict[str, box.BoxSpec]:
    """The full fleet as an x86_64 host sees it (every RHEL and Ubuntu box)."""
    return box._build_fleet(X86_FACTS)


def box_bakes() -> dict[str, tuple[str, str]]:
    """fat box -> (base OS family, bake stem), from the catalog-derived fleet."""
    return {
        name: (spec.os.ref.family, str(spec.bake_stem))
        for name, spec in x86_fleet().items()
        if spec.role is not None
    }


def manifest_hash_args(name: str) -> list[str]:
    """The flags build-fatbox.sh passes _manifest-hash.sh for fat box ``name``."""
    spec = x86_fleet()[name]
    return [
        "--bake-stem",
        str(spec.bake_stem),
        "--mq-bearing",
        "1" if spec.mq_bearing else "0",
        "--os-pin",
        box.os_pin(spec.os),
    ]


def fatbox_args(name: str) -> list[str]:
    """The full build-fatbox.sh argv tail mqlab passes for fat box ``name`` on x86_64."""
    return box.builder_args(x86_fleet()[name], X86_FACTS)
