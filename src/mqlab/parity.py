"""The cross-arm capability matrix (pivot spec §4.1): which operator verbs each
arm's backend supports. Parity = identical capability + identical correctness;
this declares the capability half. RDQM rows start NOT_YET until P3/P4 land.

This is a standalone declaration keyed by arm name (the historical RDQM-parity
catalog). The #350 cutover retired the topology `arms:` block; the live per-stack
verb implementations now live on each Stack (lab/topology.yaml `stacks:`).
"""

from __future__ import annotations

from enum import StrEnum


class Support(StrEnum):
    SUPPORTED = "supported"
    NOT_YET = "not_yet"
    NA = "n/a"


VERBS: tuple[str, ...] = (
    "form-group",
    "add-node",
    "evacuate-node",
    "failover",
    "status",
    "dr-bootstrap",
    "cutover",
    "failback",
    "diagnostics",
)

# arm -> verb -> Support. pcmk-ubuntu is the reference backend (all supported);
# rdqm-rhel is NOT_YET until its backend lands (P3/P4). pcmk-rhel (issue #238) is
# NOT_YET until its phases land — Phase 1 (#244) builds only the substrate.
# nativeha-rhel (#246) is fully supported: HA verbs (Phase 1), CRR/DR verbs
# (Phase 3), and diagnostics (runmqras capture) all proven on the live arm.
MATRIX: dict[str, dict[str, Support]] = {
    "pcmk-ubuntu": dict.fromkeys(VERBS, Support.SUPPORTED),
    "pcmk-rhel": dict.fromkeys(VERBS, Support.NOT_YET),
    "rdqm-rhel": dict.fromkeys(VERBS, Support.NOT_YET),
    "nativeha-rhel": dict.fromkeys(VERBS, Support.SUPPORTED),
}


def supported(arm: str, verb: str) -> Support:
    try:
        return MATRIX[arm][verb]
    except KeyError as exc:
        raise KeyError(f"unknown arm/verb: {arm}/{verb}") from exc


def render_markdown() -> str:
    arms = list(MATRIX)
    rows = [
        "| verb | " + " | ".join(arms) + " |",
        "|---" * (len(arms) + 1) + "|",
    ]
    for v in VERBS:
        cells = " | ".join(MATRIX[a][v].value for a in arms)
        rows.append(f"| {v} | {cells} |")
    return "\n".join(rows)
