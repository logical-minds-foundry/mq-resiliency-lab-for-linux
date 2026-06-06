"""EPN-pattern fixed-format header (spec 9.1): blank-padded, left-justified
8-char fields - Password, Sender, Receiver, BusDate - then payload. Field
layouts are per-service and arrive at onboarding; this models the PATTERN."""

from dataclasses import dataclass

FIELD = 8
HEADER_LEN = 4 * FIELD
ACK_OK = "0000"
ACK_BAD_HEADER = "9001"
ACK_STALE_DATE = "9002"


@dataclass
class Header:
    password: str
    sender: str
    receiver: str
    busdate: str
    payload: str


def pack_header(*, password: str, sender: str, receiver: str, busdate: str) -> str:
    for name, value in (
        ("password", password),
        ("sender", sender),
        ("receiver", receiver),
        ("busdate", busdate),
    ):
        if len(value) > FIELD:
            raise ValueError(f"{name} exceeds {FIELD} chars")
    return f"{password:<8}{sender:<8}{receiver:<8}{busdate:<8}"


def parse_header(msg: str) -> Header:
    if len(msg) < HEADER_LEN:
        raise ValueError("message shorter than header")
    return Header(
        password=msg[0:8].rstrip(),
        sender=msg[8:16].rstrip(),
        receiver=msg[16:24].rstrip(),
        busdate=msg[24:32].rstrip(),
        payload=msg[32:],
    )


def validate_header(msg: str, *, today: str) -> str:
    try:
        header = parse_header(msg)
    except ValueError:
        return ACK_BAD_HEADER
    if not (header.password and header.sender and header.receiver):
        return ACK_BAD_HEADER
    if header.busdate != today:
        return ACK_STALE_DATE
    return ACK_OK
