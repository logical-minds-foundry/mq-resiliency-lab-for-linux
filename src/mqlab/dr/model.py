"""Core enums and the reconciled per-message fact record."""

from __future__ import annotations

from dataclasses import dataclass
from enum import StrEnum


class Bucket(StrEnum):
    CONFIRMED = "confirmed"  # reply received at/before cutover
    CONTINUED = "continued"  # replicated + processed on the secondary
    STRANDED = "stranded"  # sent, unreplicated, still on the dead primary
    LOST_UNPROCESSED = "lost_unprocessed"  # sent, never reached DTCC, gone
    AMBIGUOUS = "ambiguous"  # DTCC processed it, reply lost — resend = dup risk
    DUPLICATED = "duplicated"  # DTCC received it more than once


class MessageState(StrEnum):
    NEVER_SENT = "never_sent"
    IN_PIPELINE = "in_pipeline"  # firm: local QM ACKed, no reply yet
    CONFIRMED = "confirmed"  # firm: reply matched


@dataclass(frozen=True)
class MessageFacts:
    """Everything the analyzer needs about one message to assign a bucket.

    In Plan 1 these are built from fixtures; in Plan 2 the live harness derives
    them from ledgers + queue/post-mortem snapshots.
    """

    seq: int
    uuid: str
    firm_confirmed: bool  # firm received its reply at/before cutover
    dtcc_received: int  # Watcher: number of times DTCC received this message
    dtcc_replied: bool  # Watcher: DTCC produced a reply
    on_secondary: bool  # present/processable on the secondary after cutover
    on_primary_disk: bool  # physically present on the failed primary (post-mortem)
