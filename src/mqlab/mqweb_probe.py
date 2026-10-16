"""Follow the active: find the live mqweb for a queue manager (epic .github#341 spec §2.3).

This is the lab's reference implementation of the "follow the active" algorithm, the one
the lab recommends to every REST API application that must find a Native HA queue
manager's live instance. Native HA has no VIP, so the client locates the active instance
itself. IBM's client model for Native HA is a list of addresses, using the one that
accepts (IBM MQ 10.0 Native HA overview, "Readiness – locating the current active
instance": https://www.ibm.com/docs/en/SSYHRD_10.0.0/configure/Native_HA_overview.html).
This is its HTTP analogue:

1. Ask each candidate in turn, authenticated, the LIST query
   ``GET /ibmmq/rest/v3/admin/qmgr?attributes=*``. It answers on every instance. The
   per-QM query (``GET …/admin/qmgr/<QM>``) is never used for discovery: it answers
   HTTP 500 (MQWB0004E, reason 2543 MQRC_STANDBY_Q_MGR) on every healthy replica and on
   the CRR recovery-group leader (lab ``docs/reference/mqweb-spike-findings.md`` §4.2).
2. Select the record whose ``name`` is the queue manager, never by position.
3. Classify by that record's ``state``: ``running`` is live (use it, stop);
   ``replica`` / ``recoveryGroupLeader`` are valid non-live members (skip); anything
   else, including no record, an unparseable body, any other HTTP status, or a
   transport/TLS failure, is unknown (skip). HTTP 401/403 is a login misconfiguration,
   not a node problem: fail loud.
4. If no candidate is live, fail loud, naming the queue manager and every candidate's
   answer. Never fall back to a replica or to the first candidate.
5. Re-run discovery whenever the chosen endpoint stops answering or stops reporting
   ``running`` (after a failover or a DR cutover). ``mqlab console open`` re-runs it
   when the user re-runs the verb.

VIP stacks (RDQM, Pacemaker) and svc-sim go through the same probe with a one-entry
candidate list, so an unreachable VIP fails just as loudly.
"""

from __future__ import annotations

import base64
import json
import ssl
import urllib.request
from typing import TYPE_CHECKING, Literal, NamedTuple
from urllib.error import HTTPError

if TYPE_CHECKING:
    from collections.abc import Callable
    from pathlib import Path

# The REST API version the T1 spike settled on (spike findings; spec §3.1).
REST_API_VERSION = "v3"

LIVE_STATES = frozenset({"running"})
MEMBER_STATES = frozenset({"replica", "recoveryGroupLeader"})
AUTH_FAILURES = frozenset({401, 403})

# The probe has no machine identity of its own: it logs in as the read-only human user
# (MQWebAdminRO). Lab convention: password = username (spec §2.2), never a real secret.
PROBE_USER = "mqreadonly"
PROBE_PASSWORD = PROBE_USER

__all__ = [
    "PROBE_PASSWORD",
    "PROBE_USER",
    "REST_API_VERSION",
    "Answer",
    "HTTPError",
    "ProbeError",
    "classify",
    "list_query_url",
    "qm_state",
    "resolve_live",
]


class ProbeError(Exception):
    """No live mqweb could be determined, or the login is wrong (fail loud, name the QM)."""


class Answer(NamedTuple):
    """One candidate's answer: the QM's REST ``state`` (None if none), and a human detail."""

    state: str | None
    detail: str


def classify(state: object) -> Literal["live", "member", "unknown"]:
    """Spec §2.3 step 3: ``running`` is live; ``replica`` / ``recoveryGroupLeader`` are
    valid non-live members; anything else is unknown."""
    if state in LIVE_STATES:
        return "live"
    if state in MEMBER_STATES:
        return "member"
    return "unknown"


def list_query_url(url: str) -> str:
    """The list query on one candidate (spec §2.3 step 1)."""
    return f"{url}/ibmmq/rest/{REST_API_VERSION}/admin/qmgr?attributes=*"


def _get(url: str, *, user: str, password: str, cafile: Path | None, timeout: float) -> bytes:
    ctx = ssl.create_default_context(cafile=str(cafile) if cafile else None)
    # The org-CA mqweb cert is CN=mqweb with no IP SAN (#250), and the probe connects by
    # IP: the chain is verified against the lab CA, the hostname is not (spec §2.3 probe
    # TLS decision; name-based certs, and the hostname check with them, are lab#1498).
    ctx.check_hostname = False
    token = base64.b64encode(f"{user}:{password}".encode()).decode()
    req = urllib.request.Request(url, headers={"Authorization": f"Basic {token}"})  # noqa: S310
    with urllib.request.urlopen(req, timeout=timeout, context=ctx) as resp:  # noqa: S310
        body: bytes = resp.read()
    return body


def _records(body: bytes) -> list[dict[str, object]] | None:
    """The ``qmgr`` records of a list-query answer, or None if the body is not that shape."""
    try:
        doc = json.loads(body)
    except ValueError:
        return None
    recs = doc.get("qmgr") if isinstance(doc, dict) else None
    if not isinstance(recs, list) or not all(isinstance(r, dict) for r in recs):
        return None
    return recs


def qm_state(
    url: str, qm: str, *, user: str, password: str, cafile: Path | None, timeout: float = 5.0
) -> Answer:
    """This candidate's answer for ``qm``, from the list query, selected by ``name``."""
    try:
        body = _get(
            list_query_url(url), user=user, password=password, cafile=cafile, timeout=timeout
        )
    except HTTPError as exc:  # before OSError: HTTPError is an OSError subclass
        if exc.code in AUTH_FAILURES:
            msg = (
                f"{qm}: {url} answered HTTP {exc.code} to the qmgr list query as {user!r}"
                " — check the mqweb login"
            )
            raise ProbeError(msg) from exc
        return Answer(None, f"HTTP {exc.code}")
    except OSError as exc:  # refused, timeout, DNS, TLS (URLError wraps the ssl errors)
        return Answer(None, f"unreachable ({getattr(exc, 'reason', exc)})")
    recs = _records(body)
    if recs is None:
        return Answer(None, f"unparseable answer ({body[:80]!r})")
    for rec in recs:
        if rec.get("name") == qm:
            state = rec.get("state")
            if isinstance(state, str):
                return Answer(state, state)
            return Answer(None, f"state {state!r}")
    return Answer(None, f"no {qm} record")


def resolve_live(urls: list[str], qm: str, *, state_fn: Callable[[str, str], Answer]) -> str:
    """The first candidate whose mqweb reports ``qm`` running; ProbeError naming every answer."""
    seen: list[str] = []
    for url in urls:
        answer = state_fn(url, qm)
        kind = classify(answer.state)
        if kind == "live":
            return url
        seen.append(f"{url}={answer.detail} [{kind}]")
    raise ProbeError(f"{qm}: no candidate reports 'running' ({', '.join(seen)})")
