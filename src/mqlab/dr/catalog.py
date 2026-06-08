"""The §6 scenario catalog, as data.

Encoding the catalog (not just running it) lets the suite assert completeness
against the spec and gives the runner a single source of truth for each drill's
fault and expected outcome.
"""

from __future__ import annotations

from dataclasses import dataclass
from enum import StrEnum

from .model import Bucket


class Kind(StrEnum):
    HA = "ha"
    DR_CONTROLLED = "dr_controlled"
    DR_FORCED = "dr_forced"
    FAILBACK = "failback"


@dataclass(frozen=True)
class Scenario:
    id: str
    kind: Kind
    fault: str
    expect_rpo_zero: bool
    expect_buckets: tuple[Bucket, ...]


_C = (Bucket.CONTINUED,)
_SAFE = (Bucket.CONFIRMED, Bucket.CONTINUED)

CATALOG: tuple[Scenario, ...] = (
    Scenario("HA-1", Kind.HA, "kill -9 the QM process under load", True, _C),
    Scenario("HA-2", Kind.HA, "power-off the active node under load", True, _C),
    Scenario("HA-3", Kind.HA, "sever heartbeat/replication net under load", True, _C),
    Scenario("HA-4", Kind.HA, "sever shared storage under load", True, _C),
    Scenario("HA-5", Kind.HA, "rolling patch one node at a time under load", True, _C),
    Scenario(
        "DR-CTRL",
        Kind.DR_CONTROLLED,
        "quiesce -> drain -> confirm replication caught up -> cutover",
        True,
        _SAFE,
    ),
    Scenario(
        "DR-FORCE-1",
        Kind.DR_FORCED,
        "primary unrecoverable, flow continues through cutover",
        False,
        (Bucket.STRANDED, Bucket.AMBIGUOUS),
    ),
    Scenario(
        "DR-FORCE-2",
        Kind.DR_FORCED,
        "primary isolated from both DTCC and secondary, app keeps producing",
        False,
        (Bucket.STRANDED,),
    ),
    Scenario(
        "DR-FORCE-3",
        Kind.DR_FORCED,
        "replication lagged/broken then failover (chained)",
        False,
        (Bucket.STRANDED,),
    ),
    Scenario(
        "FB-REPLAY",
        Kind.FAILBACK,
        "recovered primary's QM brought online against stale storage before resync/discard",
        False,
        (Bucket.DUPLICATED,),
    ),
)
