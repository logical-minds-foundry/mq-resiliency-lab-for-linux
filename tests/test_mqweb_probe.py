"""Native HA follow-the-active state probe (epic .github#341 spec §2.3)."""

from __future__ import annotations

import json
import ssl
from email.message import Message
from pathlib import Path
from typing import TYPE_CHECKING
from urllib.error import URLError

import pytest

from mqlab import mqweb_probe as p

if TYPE_CHECKING:
    import urllib.request
    from collections.abc import Callable, Mapping

URLS = ["https://10.10.1.91:9443", "https://10.10.1.92:9443", "https://10.10.1.93:9443"]
QM = "NHARCAPP"


def _ans(state: str | None) -> p.Answer:
    return p.Answer(state, state or "unreachable (refused)")


def _fn(states: Mapping[str, str | None]) -> Callable[[str, str], p.Answer]:
    return lambda u, q: _ans(states[u])


def _list_body(*records: object) -> bytes:
    return json.dumps({"qmgr": list(records)}).encode()


def _probe(monkeypatch: pytest.MonkeyPatch, get: Callable[..., bytes]) -> p.Answer:
    monkeypatch.setattr(p, "_get", get)
    return p.qm_state("https://h:9443", QM, user="u", password="p", cafile=None)


# --- classification (spec §2.3 step 3) ---------------------------------------------------


def test_classify_follows_the_spec_table() -> None:
    assert p.classify("running") == "live"
    assert p.classify("replica") == "member"
    assert p.classify("recoveryGroupLeader") == "member"
    for other in ("ended", "starting", "", None, 42):
        assert p.classify(other) == "unknown"


def test_probe_logs_in_as_the_read_only_human_user() -> None:
    # No machine REST identity (Global Constraints): the probe is mqreadonly/mqreadonly.
    assert (p.PROBE_USER, p.PROBE_PASSWORD) == ("mqreadonly", "mqreadonly")


# --- resolve_live (spec §2.3 steps 3-4) --------------------------------------------------


def test_resolve_live_picks_running() -> None:
    states = {URLS[0]: "replica", URLS[1]: "running", URLS[2]: "replica"}
    assert p.resolve_live(URLS, QM, state_fn=_fn(states)) == URLS[1]


def test_resolve_live_stops_at_the_first_running() -> None:
    asked: list[str] = []

    def fn(u: str, q: str) -> p.Answer:
        asked.append(u)
        return _ans("running")

    assert p.resolve_live(URLS, QM, state_fn=fn) == URLS[0]
    assert asked == URLS[:1]


def test_resolve_live_skips_recovery_group_leader() -> None:
    # Review Focus 6: the CRR leader is a valid non-live member, skipped without error.
    states = {URLS[0]: "recoveryGroupLeader", URLS[1]: "replica", URLS[2]: "running"}
    assert p.resolve_live(URLS, QM, state_fn=_fn(states)) == URLS[2]


def test_resolve_live_skips_unreachable_candidate() -> None:
    # Review Focus 3: a down node is skipped, not fatal.
    states = {URLS[0]: None, URLS[1]: "replica", URLS[2]: "running"}
    assert p.resolve_live(URLS, QM, state_fn=_fn(states)) == URLS[2]


def test_resolve_live_skips_unknown_answer() -> None:
    # Review Focus 3: an unknown state (or an HTTP 500 answer) is skipped, not fatal.
    answers = {
        URLS[0]: p.Answer("ended", "ended"),
        URLS[1]: p.Answer(None, "HTTP 500"),
        URLS[2]: p.Answer("running", "running"),
    }
    assert p.resolve_live(URLS, QM, state_fn=lambda u, q: answers[u]) == URLS[2]


def test_resolve_live_raises_when_none_running() -> None:
    # Review Focus 2: mid-election, never forward to a replica; name every answer.
    states = {URLS[0]: "replica", URLS[1]: None, URLS[2]: "replica"}
    with pytest.raises(p.ProbeError, match=r"NHARCAPP: no candidate reports 'running'") as exc:
        p.resolve_live(URLS, QM, state_fn=_fn(states))
    msg = str(exc.value)
    assert f"{URLS[0]}=replica [member]" in msg
    assert f"{URLS[1]}=unreachable (refused) [unknown]" in msg
    assert f"{URLS[2]}=replica [member]" in msg


def test_resolve_live_raises_on_recovery_site() -> None:
    # Review Focus 2: the CRR recovery site has no live instance until a switchover.
    states = {URLS[0]: "recoveryGroupLeader", URLS[1]: "replica", URLS[2]: "replica"}
    with pytest.raises(p.ProbeError, match=r"recoveryGroupLeader \[member\]"):
        p.resolve_live(URLS, QM, state_fn=_fn(states))


def test_resolve_live_single_vip_still_probed() -> None:
    with pytest.raises(p.ProbeError):
        p.resolve_live(URLS[:1], "RDQMAPP", state_fn=lambda u, q: _ans(None))


# --- qm_state (spec §2.3 steps 1-3) ------------------------------------------------------


def test_qm_state_uses_list_query_and_selects_by_name(monkeypatch: pytest.MonkeyPatch) -> None:
    # Review Focus 6: the LIST query, never the per-QM one; select by name, not by position.
    seen: list[str] = []

    def get(url: str, **kw: object) -> bytes:
        seen.append(url)
        return _list_body({"name": "OTHERQM", "state": "running"}, {"name": QM, "state": "replica"})

    assert _probe(monkeypatch, get) == p.Answer("replica", "replica")
    assert seen == ["https://h:9443/ibmmq/rest/v3/admin/qmgr?attributes=*"]


def test_qm_state_reports_recovery_group_leader(monkeypatch: pytest.MonkeyPatch) -> None:
    body = _list_body({"name": QM, "state": "recoveryGroupLeader"})
    assert _probe(monkeypatch, lambda url, **kw: body).state == "recoveryGroupLeader"


def test_qm_state_reports_running(monkeypatch: pytest.MonkeyPatch) -> None:
    body = _list_body({"name": QM, "state": "running"})
    assert _probe(monkeypatch, lambda url, **kw: body) == p.Answer("running", "running")


def test_qm_state_without_a_record_for_the_qm_is_unknown(monkeypatch: pytest.MonkeyPatch) -> None:
    body = _list_body({"name": "OTHERQM", "state": "running"})
    ans = _probe(monkeypatch, lambda url, **kw: body)
    assert ans == p.Answer(None, f"no {QM} record")


@pytest.mark.parametrize("state", [None, 42])
def test_qm_state_non_string_state_is_unknown(
    monkeypatch: pytest.MonkeyPatch, state: object
) -> None:
    body = _list_body({"name": QM, "state": state})
    ans = _probe(monkeypatch, lambda url, **kw: body)
    assert ans == p.Answer(None, f"state {state!r}")
    assert p.classify(ans.state) == "unknown"


@pytest.mark.parametrize(
    "body",
    [b"<html>", b"[]", b'{"qmgr": {"name": "NHARCAPP"}}', b'{"qmgr": ["NHARCAPP"]}', b"{}"],
)
def test_qm_state_unparseable_body_is_unknown(monkeypatch: pytest.MonkeyPatch, body: bytes) -> None:
    ans = _probe(monkeypatch, lambda url, **kw: body)
    assert ans.state is None
    assert ans.detail.startswith("unparseable answer")


def test_qm_state_unreachable_on_connection_error(monkeypatch: pytest.MonkeyPatch) -> None:
    def boom(url: str, **kw: object) -> bytes:
        raise OSError("refused")

    assert _probe(monkeypatch, boom) == p.Answer(None, "unreachable (refused)")


def test_qm_state_tls_failure_is_skipped(monkeypatch: pytest.MonkeyPatch) -> None:
    # Review Focus 3: a TLS failure (wrong chain) is "unreachable", skipped, not fatal.
    def bad_tls(url: str, **kw: object) -> bytes:
        raise URLError(ssl.SSLCertVerificationError("certificate verify failed"))

    ans = _probe(monkeypatch, bad_tls)
    assert ans.state is None
    assert ans.detail.startswith("unreachable (")
    assert "certificate verify failed" in ans.detail


@pytest.mark.parametrize("code", [401, 403])
def test_qm_state_raises_on_auth_failure(monkeypatch: pytest.MonkeyPatch, code: int) -> None:
    # 401/403 on the list query is a misconfiguration, not "node down": fail loud.
    def denied(url: str, **kw: object) -> bytes:
        raise p.HTTPError(url, code, "denied", Message(), None)

    with pytest.raises(p.ProbeError, match=f"HTTP {code}.*check the mqweb login"):
        _probe(monkeypatch, denied)


def test_qm_state_http_500_is_never_an_auth_failure(monkeypatch: pytest.MonkeyPatch) -> None:
    # Review Focus 6: the spike saw HTTP 500 (MQWB0004E, reason 2543) from the per-QM query on
    # every healthy replica. Any non-auth HTTP error is an unknown answer, skipped, never fatal.
    def err(url: str, **kw: object) -> bytes:
        raise p.HTTPError(url, 500, "MQWB0004E", Message(), None)

    ans = _probe(monkeypatch, err)
    assert ans == p.Answer(None, "HTTP 500")
    assert p.classify(ans.state) == "unknown"


# --- _get ----------------------------------------------------------------------------------


class _Resp:
    def __enter__(self) -> _Resp:
        return self

    def __exit__(self, *a: object) -> None:
        return None

    def read(self) -> bytes:
        return b"{}"


@pytest.mark.parametrize("cafile", [None, "ca.crt"])
def test_get_sends_basic_auth_and_skips_only_the_hostname_check(
    monkeypatch: pytest.MonkeyPatch, cafile: str | None
) -> None:
    # Spec §2.3 probe TLS decision: verify the chain, not the hostname (CN=mqweb, no IP SAN).
    seen: dict[str, object] = {}
    loaded: list[object] = []

    def fake_urlopen(
        req: urllib.request.Request, *, timeout: float, context: ssl.SSLContext
    ) -> _Resp:
        seen.update(auth=req.get_header("Authorization"), ctx=context, timeout=timeout)
        return _Resp()

    real_ctx = ssl.create_default_context

    def fake_ctx(*, cafile: str | None = None) -> ssl.SSLContext:
        loaded.append(cafile)
        return real_ctx()

    monkeypatch.setattr(p.urllib.request, "urlopen", fake_urlopen)
    monkeypatch.setattr(p.ssl, "create_default_context", fake_ctx)

    path = Path(cafile) if cafile else None
    assert p._get("https://h:9443/x", user="u", password="p", cafile=path, timeout=2.0) == b"{}"
    assert loaded == [cafile]
    assert seen["auth"] == "Basic dTpw"  # base64("u:p")
    assert seen["timeout"] == 2.0
    ctx = seen["ctx"]
    assert isinstance(ctx, ssl.SSLContext)
    assert ctx.check_hostname is False
    assert ctx.verify_mode == ssl.CERT_REQUIRED
