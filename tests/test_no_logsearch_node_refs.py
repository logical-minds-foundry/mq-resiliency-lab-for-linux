"""Two-pronged guardrail for the logsearch→obs consolidation (#1179, epic .github#267).

The log-search tier (OpenSearch + Dashboards + Data Prepper) was folded onto the `obs`
node: the `logsearch` node, the `logsearch_box` group, and the `logsearch-ubuntu2404` box
are retired, and every hardcoded reference to them was retargeted to obs (mgmt IP
10.50.0.2, host/group `obs`/`obs_box`). Two failure modes hide a stranded reference:

  (a) a hardcoded ``10.50.0.4`` IP literal — the old logsearch mgmt NIC. A guardrail that
      greps only the string ``logsearch`` sails right past it, so this prong greps the IP
      literal directly across the runtime paths (``src/``, ``ansible/``, ``lab/``,
      ``manifests/``). The match is exact: ``10.50.0.41`` (an rdqm-b node) must NOT trip it.

  (b) a ``logsearch`` **host/group** reference in a topology/inventory consumer — the
      retired node in ``topology.yaml``, the ``logsearch_box`` group, a ``hosts: logsearch``
      ansible play, or the retired ``phases`` helpers. This is DISTINCT from the
      OpenSearch/Dashboards/Data-Prepper roles' own cluster-/box-/CLI-/bucket-name usage of
      the word "logsearch" (``opensearch_cluster_name: logsearch``, the ``logsearch-fs``
      snapshot repo, the ``mqlab logsearch`` CLI verb, ``build/*/logsearch/`` buckets,
      ``group_vars/all/logsearch.yml``): those move WITH the services onto obs and are fine.

Both prongs go RED against a stranded reference and GREEN when the retarget is clean.
"""

from __future__ import annotations

import re

import yaml

from mqlab import phases
from mqlab.paths import repo_root

# --- prong (a): the retired mgmt IP literal ------------------------------------------

# Exact ``10.50.0.4`` — the lookbehind/lookahead reject a longer octet so ``10.50.0.41``
# / ``10.50.0.42`` / ``10.50.0.43`` (the rdqm-b nodes) never false-positive.
_RETIRED_IP = re.compile(r"(?<!\d)10\.50\.0\.4(?!\d)")

# The runtime paths a stranded literal would actually break a bring-up from.
_RUNTIME_ROOTS = ("src", "ansible", "lab", "manifests")


def _contains_retired_ip(text: str) -> bool:
    """True iff ``text`` carries the exact retired logsearch mgmt IP literal."""
    return bool(_RETIRED_IP.search(text))


def _runtime_files() -> list:
    """Every file under the runtime roots (text read with errors ignored downstream)."""
    files: list = []
    for root in _RUNTIME_ROOTS:
        for path in (repo_root() / root).rglob("*"):
            if path.is_file():
                files.append(path)
    return files


def test_ip_matcher_matches_exact_literal_only():
    # Both outcomes of the matcher are exercised here, independent of the repo state.
    assert _contains_retired_ip("nics: { net-mgmt: 10.50.0.4 }")
    assert _contains_retired_ip("http://10.50.0.4:9200")
    assert not _contains_retired_ip("net-mgmt: 10.50.0.41")  # rdqm-b1 — must NOT trip
    assert not _contains_retired_ip("net-mgmt: 10.50.0.2")  # obs — the retarget target
    assert not _contains_retired_ip("no ip here at all")


def test_no_retired_logsearch_ip_literal_in_runtime_paths():
    offenders = sorted(
        str(p.relative_to(repo_root()))
        for p in _runtime_files()
        if _contains_retired_ip(p.read_text(errors="ignore"))
    )
    assert offenders == [], (
        "stranded 10.50.0.4 (retired logsearch mgmt IP) — retarget these to obs "
        f"(10.50.0.2 / localhost): {offenders}"
    )


# --- prong (b): no logsearch host/group reference in topology/inventory consumers -----


def _topology() -> dict:
    return yaml.safe_load((repo_root() / "lab" / "topology.yaml").read_text())


def test_topology_has_no_logsearch_node_group_or_box():
    topo = _topology()
    assert "logsearch" not in (topo.get("nodes") or {}), "the logsearch node must be retired"
    assert "logsearch_box" not in (topo.get("groups") or {}), "the logsearch_box group is retired"
    assert "logsearch-ubuntu2404" not in (topo.get("boxes") or {}), (
        "the logsearch-ubuntu2404 box must be dropped from the fleet"
    )


def test_obs_absorbed_the_logsearch_resources():
    # The consolidation grew obs to the combined 4 vCPU / 10 GB figure (spec §5.1).
    obs = _topology()["nodes"]["obs"]
    assert obs["cpus"] == 4
    assert obs["memory"] == 10240


def test_no_ansible_play_targets_the_logsearch_host_or_group():
    offenders: list[str] = []
    for path in (repo_root() / "ansible").rglob("*.yml"):
        for lineno, line in enumerate(path.read_text().splitlines(), start=1):
            stripped = line.strip()
            if stripped.startswith("- hosts:") or stripped.startswith("hosts:"):
                target = stripped.split("hosts:", 1)[1].strip()
                if re.search(r"\blogsearch(_box)?\b", target):
                    offenders.append(f"{path.relative_to(repo_root())}:{lineno}: {stripped}")
    assert offenders == [], (
        f"ansible plays still target the retired logsearch host/group: {offenders}"
    )


def test_phases_retired_the_logsearch_member_helpers():
    # The topology/inventory consumers no longer carry a separate logsearch member set —
    # obs (with the log-search tier) rides the obs_box commons group.
    assert not hasattr(phases, "_logsearch_members")
    assert not hasattr(phases, "_LOGSEARCH_GROUP")
