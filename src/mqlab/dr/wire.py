"""Self-identifying DR message body.

Layout (UTF-8): "DRv1|<seq>|<uuid>|<busdate>|<trade...>"
The trade field is last, so it may contain the '|' delimiter without ambiguity
(we split with maxsplit=4).
"""

from __future__ import annotations

from dataclasses import dataclass

_PREFIX = "DRv1"


@dataclass(frozen=True)
class WireMessage:
    seq: int
    uuid: str
    busdate: str
    trade: str


def build_body(*, seq: int, uuid: str, busdate: str, trade: str) -> bytes:
    return "|".join([_PREFIX, str(seq), uuid, busdate, trade]).encode("utf-8")


def parse_body(body: bytes) -> WireMessage:
    parts = body.decode("utf-8").split("|", 4)
    if len(parts) != 5 or parts[0] != _PREFIX:
        raise ValueError(f"not a DRv1 body: {body!r}")
    _, seq, uuid, busdate, trade = parts
    return WireMessage(seq=int(seq), uuid=uuid, busdate=busdate, trade=trade)
