"""Huge-page-backed guest RAM: sizing + the host reserve/release commands (#1241).

Spike #1240 found the macOS/arm64 bootstrap bottleneck: on Apple Silicon nested
virtualization, a guest that churns memory (obs's JVM tier) makes every guest's page
faults 300-1000x slower, paid in the macOS hypervisor's handling of 4 KiB second-level
mappings. Backing guest RAM with 2 MiB huge pages removed it (~110x on obs's churn). x86
cloud does not show the problem, so the lever lives in the ``macos`` env profile only.

When the effective topology sets ``memory_backing: hugepages`` the Vagrantfile backs every
guest with huge pages, and a guest cannot boot without enough FREE pages. So before any
`vagrant up` the orchestrator reserves them in the Vergil VM (the libvirt host) with the
``host-hugepages.yml`` localhost play (``become``; ``sysctl vm.nr_hugepages``, with a
drop_caches + compact_memory retry), and fails loud — needed vs got — if the kernel cannot
supply them. There is never a silent fallback to 4 KiB backing. The last stack's teardown
releases them (``vm.nr_hugepages=0``).

Sizing: the guests about to boot (not already live), each rounded up to whole 2 MiB pages,
plus ``MARGIN_PAGES``. The play adds that to the pages already committed to running guests,
so a resume or a second stack never steals pages from guests that hold them.
"""

from __future__ import annotations

from pathlib import Path
from typing import Any

from mqlab import topology
from mqlab.paths import repo_root
from mqlab.runner import Command

PLAYBOOK = "host-hugepages.yml"
PAGE_KIB = 2048  # the only huge-page size the play reserves (asserted against Hugepagesize)
PAGE_MIB = PAGE_KIB // 1024
# A modest cushion (256 MiB) over the guests' summed RAM, so a rounding or accounting
# surprise does not fail the last guest's boot. Not a substitute for correct sizing.
MARGIN_PAGES = 128
# Mirrors platforms._provider's memory default for a node with neither its own memory nor
# a topology defaults.memory, so the sizing agrees with what the Vagrantfile gets.
_DEFAULT_MEMORY_MIB = 1024
MEMINFO = Path("/proc/meminfo")
# The play's reclaim task (its TASK banner name): its presence with ok/changed in the run
# transcript is how the perf note learns the first reservation fell short and was retried.
RECLAIM_TASK = "Reclaim: drop caches and compact memory, then retry"

_MEMINFO_KEYS = ("HugePages_Total", "HugePages_Free", "HugePages_Rsvd", "Hugepagesize")


def enabled(topo: dict[str, Any]) -> bool:
    """True iff the effective topology backs guest RAM with huge pages (validated)."""
    return topology.memory_backing(topo) == topology.HUGEPAGES


def guest_memory_mib(topo: dict[str, Any], guest: str) -> int:
    """A guest's RAM in MiB, resolved exactly as platforms._provider resolves it."""
    nodes = topo.get("nodes") or {}
    if guest not in nodes:
        msg = f"huge-page sizing: guest {guest!r} is not a topology node"
        raise ValueError(msg)
    spec = nodes[guest] or {}
    defaults = topo.get("defaults") or {}
    return int(spec.get("memory", defaults.get("memory", _DEFAULT_MEMORY_MIB)))


def pages_for(memory_mib: int) -> int:
    """Whole 2 MiB pages covering ``memory_mib`` (rounded up)."""
    return -(-memory_mib // PAGE_MIB)


def needed_pages(topo: dict[str, Any], guests: list[str]) -> int:
    """Free 2 MiB pages the given guests need to boot: their RAM + ``MARGIN_PAGES``.

    Zero when there is nothing to boot (no margin for nothing).
    """
    if not guests:
        return 0
    return sum(pages_for(guest_memory_mib(topo, g)) for g in guests) + MARGIN_PAGES


def _play(*extra: str) -> Command:
    return Command(
        ["ansible-playbook", PLAYBOOK, "-c", "local", "-i", "localhost,", *extra],
        cwd=repo_root() / "ansible",
    )


def reserve_command(needed: int) -> Command:
    """Ensure ``needed`` free (unreserved) 2 MiB pages exist on the host, or fail loud."""
    if needed < 1:
        msg = f"huge-page reservation needs a positive page count, got {needed!r}"
        raise ValueError(msg)
    return _play("-e", f"hugepages_needed={needed}")


def release_command() -> Command:
    """Return every huge page to the kernel (``vm.nr_hugepages=0``), verified."""
    return _play("-e", "hugepages_release=true")


def read_meminfo(path: Path | None = None) -> dict[str, int]:
    """The huge-page counters from ``/proc/meminfo`` (Hugepagesize in kB). Fails loud
    when a counter is missing — a kernel without hugetlb cannot honour the lever."""
    text = (path or MEMINFO).read_text(encoding="utf-8")
    found: dict[str, int] = {}
    for line in text.splitlines():
        key, _, rest = line.partition(":")
        if key in _MEMINFO_KEYS:
            found[key] = int(rest.split()[0])
    missing = [k for k in _MEMINFO_KEYS if k not in found]
    if missing:
        msg = f"{path or MEMINFO}: no {', '.join(missing)} — kernel lacks hugetlb support"
        raise RuntimeError(msg)
    return found


def summary(info: dict[str, int]) -> str:
    """``total=T free=F rsvd=R`` — the counters a reservation note reports."""
    return (
        f"HugePages_Total={info['HugePages_Total']} HugePages_Free={info['HugePages_Free']} "
        f"HugePages_Rsvd={info['HugePages_Rsvd']}"
    )
