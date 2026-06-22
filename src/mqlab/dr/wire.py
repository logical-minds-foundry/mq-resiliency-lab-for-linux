"""Self-identifying DR message body.

Layout (UTF-8): "DRv1|<seq>|<uuid>|<session_date>|<payload...>"
The payload field is last, so it may contain the '|' delimiter without ambiguity
(we split with maxsplit=4).
"""

from __future__ import annotations

from dataclasses import dataclass

_PREFIX = "DRv1"


@dataclass(frozen=True)
class WireMessage:
    seq: int
    uuid: str
    session_date: str
    payload: str


def build_body(*, seq: int, uuid: str, session_date: str, payload: str) -> bytes:
    return "|".join([_PREFIX, str(seq), uuid, session_date, payload]).encode("utf-8")


def parse_body(body: bytes) -> WireMessage:
    parts = body.decode("utf-8").split("|", 4)
    if len(parts) != 5 or parts[0] != _PREFIX:
        raise ValueError(f"not a DRv1 body: {body!r}")
    _, seq, uuid, session_date, payload = parts
    return WireMessage(seq=int(seq), uuid=uuid, session_date=session_date, payload=payload)
