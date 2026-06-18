"""The cross-arm capability matrix (pivot spec §4.1): which operator verbs each
arm's backend supports. Parity = identical capability + identical correctness;
this declares the capability half. RDQM rows start NOT_YET until P3/P4 land.

The arm-to-setup mapping lives in the topology-declared registry (`mqlab.arms`);
the test suite asserts this matrix and that registry agree on the arm set.
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
# rdqm-rhel is NOT_YET until its backend lands (P3/P4). nativeha-rhel (#246):
# HA verbs proven in Phase 1 (form-group/add-node/evacuate/failover/status),
# CRR/DR verbs in Phase 3 (dr-bootstrap/cutover/failback). diagnostics (runmqras
# capture) not yet exercised.
MATRIX: dict[str, dict[str, Support]] = {
    "pcmk-ubuntu": dict.fromkeys(VERBS, Support.SUPPORTED),
    "rdqm-rhel": dict.fromkeys(VERBS, Support.NOT_YET),
    "nativeha-rhel": {
        **dict.fromkeys(VERBS, Support.SUPPORTED),
        "diagnostics": Support.NOT_YET,
    },
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
