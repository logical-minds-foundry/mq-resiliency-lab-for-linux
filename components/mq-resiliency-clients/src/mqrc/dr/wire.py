"""Self-identifying DR message body.

Layout (UTF-8): "DRv1|<seq>|<uuid>|<session_date>|<payload...>"
The payload field is last, so it may contain the '|' delimiter without ambiguity
(we split with maxsplit=4).

On the wire the body follows a fixed-format header (mqrc.header); split_framed()
locates it by its marker. A message with no well-formed DRv1 body -- a foreign
message, or a malformed one -- raises NotDRv1Error, never a parse of garbage (#1550).
"""

from __future__ import annotations

from dataclasses import dataclass

_PREFIX = "DRv1"
_MARKER = (_PREFIX + "|").encode("ascii")


class NotDRv1Error(ValueError):
    """The bytes carry no well-formed DRv1 body (a foreign or malformed message)."""


@dataclass(frozen=True)
class WireMessage:
    seq: int
    uuid: str
    session_date: str
    payload: str


def build_body(*, seq: int, uuid: str, session_date: str, payload: str) -> bytes:
    return "|".join([_PREFIX, str(seq), uuid, session_date, payload]).encode("utf-8")


def parse_body(body: bytes) -> WireMessage:
    """Parse a bare DRv1 body. Raises NotDRv1Error if it is not one."""
    reason = f"not a DRv1 body: {body!r}"
    try:
        parts = body.decode("utf-8").split("|", 4)
        if len(parts) == 5 and parts[0] == _PREFIX:
            _, seq, uuid, session_date, payload = parts
            return WireMessage(seq=int(seq), uuid=uuid, session_date=session_date, payload=payload)
    except ValueError as exc:  # not UTF-8, or a non-integer seq
        raise NotDRv1Error(reason) from exc
    raise NotDRv1Error(reason)


def split_framed(raw: bytes) -> tuple[bytes, WireMessage]:
    """Locate the DRv1 body after a message's header and parse it.

    Returns (body, message); body is the bytes from the marker on, for a responder
    to echo. Raises NotDRv1Error when the marker is absent -- a missing marker is a
    foreign message, so it is never sliced from find() == -1 -- or the body after
    it does not parse.
    """
    idx = raw.find(_MARKER)
    if idx < 0:
        raise NotDRv1Error(f"no DRv1 body in message: {raw[:64]!r}")
    body = raw[idx:]
    return body, parse_body(body)
