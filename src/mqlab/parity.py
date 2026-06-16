"""The cross-arm capability matrix (pivot spec §4.1): which operator verbs each
arm's backend supports. Parity = identical capability + identical correctness;
this declares the capability half. RDQM rows start NOT_YET until P3/P4 land.

The `provisional_arm` map is a P1 stopgap: the real arm registry arrives in P2
(topology-declared), at which point this map is replaced by a registry lookup.
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
# rdqm-rhel is NOT_YET until its backend lands (P3/P4).
MATRIX: dict[str, dict[str, Support]] = {
    "pcmk-ubuntu": dict.fromkeys(VERBS, Support.SUPPORTED),
    "rdqm-rhel": dict.fromkeys(VERBS, Support.NOT_YET),
}

# Provisional P1 setup -> arm map (replaced by the P2 registry).
_PROVISIONAL_ARM: dict[str, str] = {
    "distributed": "pcmk-ubuntu",
    "pcmk_san_ha": "pcmk-ubuntu",
    "pcmk_san_dr": "pcmk-ubuntu",
    "rdqm_ha": "rdqm-rhel",
    "rdqm_dr": "rdqm-rhel",
}


def supported(arm: str, verb: str) -> Support:
    try:
        return MATRIX[arm][verb]
    except KeyError as exc:
        raise KeyError(f"unknown arm/verb: {arm}/{verb}") from exc


def provisional_arm(setup: str) -> str:
    try:
        return _PROVISIONAL_ARM[setup]
    except KeyError as exc:
        raise KeyError(f"no provisional arm for setup {setup!r}") from exc


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
