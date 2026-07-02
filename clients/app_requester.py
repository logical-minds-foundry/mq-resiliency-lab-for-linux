"""App requester -- Business A's application in the distributed flow (client mode).

Connects client-mode to our HA queue manager via a CONNAME list, with
MQCNO_RECONNECT, so it follows the QM across HA (the floating VIP within a data
centre or a Native HA instance list) and DR (the other site). Puts a request to
SVC.REQUEST -- a remote-queue definition that routes across the WAN to the SVC
counterparty QM -- and gets the reply from APP.REPLY, matched by
CorrelId == the request's MsgId, the correlation contract the SVC service honours.

Run continuously as a managed service (`mq-app-requester`) it is the always-on
workload behind the messaging cockpit (#429): a steady request->reply stream whose
round-trips are (a) logged to stdout -> journald -> Loki and (b) recorded into a
node-exporter textfile (`app_roundtrip_*` counters + a latency histogram) that
node-exporter scrapes with the rest of the host metrics -- no new scrape config.

Config seam: --rate / --msg-size / --fault-rate are knobs the future
workload-realism task (burst, slow-network queuing, DR message-loss) fills in; v1
is a fixed-rate, fixed-shape laminar stream -- good-enough noise, not a faithful
app model. --count 0 (the service default) streams forever; a positive count is
for a bounded manual run.
"""

from __future__ import annotations

import argparse
import os
import tempfile
import time

# Latency histogram bucket edges (ms), cumulative in the Prometheus sense. Sized
# for a lab round-trip: sub-ms replies up to multi-second queuing under a burst.
_BUCKETS_MS: tuple[float, ...] = (1, 2, 5, 10, 20, 50, 100, 200, 500, 1000, 2000, 5000)

# Both site VIPs by default (site-A floats within DC-A for HA; site-B is the DR
# endpoint). A per-stack service overrides --conn with that stack's app-QM address.
DEFAULT_CONN = "10.10.1.200(1414),10.10.2.200(1414)"

_TEXTFILE_HELP = (
    ("app_roundtrip_total", "counter", "Total app round-trips attempted."),
    ("app_roundtrip_failures_total", "counter", "App round-trips that failed (no reply / error)."),
    ("app_roundtrip_latency_ms", "histogram", "Successful round-trip latency (ms)."),
)


class RoundTripStats:
    """Accumulates the round-trip signal: attempt/failure counters and a latency
    histogram over the successful round-trips. Pure + side-effect-free so it is unit
    tested; render_prom turns it into node-exporter textfile exposition text."""

    def __init__(self, buckets: tuple[float, ...] = _BUCKETS_MS) -> None:
        self.buckets = buckets
        self.total = 0  # every attempt (success + failure)
        self.failures = 0
        self.sum_ms = 0.0
        # cumulative bucket hits: _bucket_hits[i] = successes with latency <= buckets[i]
        self._bucket_hits = [0] * len(buckets)

    @property
    def successes(self) -> int:
        return self.total - self.failures

    def record_success(self, latency_ms: float) -> None:
        self.total += 1
        self.sum_ms += latency_ms
        for i, edge in enumerate(self.buckets):
            if latency_ms <= edge:
                self._bucket_hits[i] += 1

    def record_failure(self) -> None:
        self.total += 1
        self.failures += 1


def render_prom(stats: RoundTripStats) -> str:
    """Render RoundTripStats as Prometheus text-exposition for a node-exporter textfile."""
    help_by_name = {name: (kind, desc) for name, kind, desc in _TEXTFILE_HELP}
    lines: list[str] = []

    def _counter(name: str, value: int) -> None:
        kind, desc = help_by_name[name]
        lines.append(f"# HELP {name} {desc}")
        lines.append(f"# TYPE {name} {kind}")
        lines.append(f"{name} {value}")

    _counter("app_roundtrip_total", stats.total)
    _counter("app_roundtrip_failures_total", stats.failures)

    _kind, desc = help_by_name["app_roundtrip_latency_ms"]
    lines.append("# HELP app_roundtrip_latency_ms " + desc)
    lines.append("# TYPE app_roundtrip_latency_ms " + _kind)
    for edge, hits in zip(stats.buckets, stats._bucket_hits, strict=True):
        lines.append(f'app_roundtrip_latency_ms_bucket{{le="{edge:g}"}} {hits}')
    lines.append(f'app_roundtrip_latency_ms_bucket{{le="+Inf"}} {stats.successes}')
    lines.append(f"app_roundtrip_latency_ms_sum {stats.sum_ms:g}")
    lines.append(f"app_roundtrip_latency_ms_count {stats.successes}")
    return "\n".join(lines) + "\n"


def write_textfile(path: str, text: str) -> None:
    """Atomically publish the metric textfile: write a sibling temp file then
    os.replace it, so node-exporter never reads a half-written .prom."""
    directory = os.path.dirname(path) or "."
    fd, tmp = tempfile.mkstemp(dir=directory, suffix=".tmp")
    try:
        with os.fdopen(fd, "w") as handle:
            handle.write(text)
        os.chmod(tmp, 0o644)  # mkstemp is 0600; node-exporter (another user) must read it
        os.replace(tmp, path)
    except BaseException:
        if os.path.exists(tmp):
            os.unlink(tmp)
        raise


def publish_metrics(path: str, stats: RoundTripStats) -> None:
    """Publish the round-trip metric textfile, tolerating a write failure loudly.

    The textfile is a *side* signal; the request/reply stream is the product. A
    transient inability to write it (e.g. the node-exporter textfile dir not yet
    writable by us) must never crash the workload -- but it must be loud, never
    swallowed. Mirrors the MQMIError policy in run(): report and keep streaming."""
    try:
        write_textfile(path, render_prom(stats))
    except OSError as exc:
        print(f"textfile publish FAILED (metrics only, stream continues): {exc}", flush=True)


def _build_args(argv: list[str] | None = None) -> argparse.Namespace:
    ap = argparse.ArgumentParser(description=__doc__)
    ap.add_argument("--qm", default=os.environ.get("MQLAB_QM", "PCMKAPP"))
    ap.add_argument("--conn", default=DEFAULT_CONN)
    ap.add_argument("--channel", default="APP.SVRCONN")
    ap.add_argument("--request-queue", default="SVC.REQUEST")
    ap.add_argument("--reply-queue", default="APP.REPLY")
    # --count 0 streams forever (the service default); >0 is a bounded manual run.
    ap.add_argument("--count", type=int, default=0)
    # Config seam (workload realism, #15 phase 7+): v1 uses rate only.
    ap.add_argument("--rate", type=float, default=1.0, help="messages per second")
    ap.add_argument("--msg-size", type=int, default=32, help="request payload bytes")
    ap.add_argument("--fault-rate", type=float, default=0.0, help="reserved; unused in v1")
    ap.add_argument("--textfile", default="", help="node-exporter .prom path; empty disables metrics")
    # TLS (#250): --keyrepo is the keystore *stem*; a sibling .sth supplies the
    # password (pymqi's SCO has none). Omit both -> plaintext.
    ap.add_argument("--keyrepo", default="", help="keystore stem, e.g. /home/vagrant/ssl/key")
    ap.add_argument("--certlabel", default="", help="client cert label (the entity CN)")
    return ap.parse_args(argv)


def run(args: argparse.Namespace) -> int:
    """The streaming loop. Imports pymqi lazily so the module (and its pure helpers)
    imports without the MQ client libs present (unit tests, controller-side)."""
    import pymqi

    cd = pymqi.CD(
        ChannelName=args.channel.encode(),
        ConnectionName=args.conn.encode(),
        TransportType=pymqi.CMQC.MQXPT_TCP,
    )
    # Always a valid SCO: connect_with_options calls sco.pack(), so a None sco crashes
    # (AttributeError) on the plaintext path (no keyrepo). TLS just sets its fields. (#442)
    sco = pymqi.SCO()
    if args.keyrepo:
        cd.SSLCipherSpec = b"ANY_TLS13_OR_HIGHER"
        sco.KeyRepository = args.keyrepo.encode()
        if args.certlabel:
            sco.CertificateLabel = args.certlabel.encode()

    qmgr = pymqi.QueueManager(None)
    qmgr.connect_with_options(args.qm, cd=cd, sco=sco, opts=pymqi.CMQC.MQCNO_RECONNECT)
    qreq = pymqi.Queue(qmgr, args.request_queue)
    qrep = pymqi.Queue(qmgr, args.reply_queue)
    gmo = pymqi.GMO(
        Options=(pymqi.CMQC.MQGMO_WAIT | pymqi.CMQC.MQGMO_FAIL_IF_QUIESCING),
        WaitInterval=15000,
        MatchOptions=pymqi.CMQC.MQMO_MATCH_CORREL_ID,
    )

    stats = RoundTripStats()
    payload = b"x" * max(1, args.msg_size)
    interval = 1.0 / args.rate if args.rate > 0 else 0.0
    i = 0
    try:
        while args.count == 0 or i < args.count:
            try:
                put_md = pymqi.MD(
                    ReplyToQ=args.reply_queue.encode(),
                    ReplyToQMgr=args.qm.encode(),
                    Format=pymqi.CMQC.MQFMT_STRING,
                    Persistence=pymqi.CMQC.MQPER_PERSISTENT,
                )
                t0 = time.monotonic()
                qreq.put(payload, put_md)  # MsgId is set on the MD here
                reply = qrep.get(None, pymqi.MD(CorrelId=put_md.MsgId), gmo)
                dt = (time.monotonic() - t0) * 1000
                stats.record_success(dt)
                print(f"[{i}] round-trip {dt:.0f}ms <- {reply.decode(errors='replace')[:48]}", flush=True)
            except pymqi.MQMIError as exc:
                # Fail loud (never swallow) but keep streaming: a missed reply / dead
                # peer is a data point, and MQCNO_RECONNECT re-establishes the QM.
                stats.record_failure()
                print(f"[{i}] round-trip FAILED: {exc}", flush=True)
            if args.textfile:
                publish_metrics(args.textfile, stats)
            i += 1
            if interval:
                time.sleep(interval)
    finally:
        qmgr.disconnect()
    return 0 if stats.failures == 0 else 1


def main(argv: list[str] | None = None) -> int:
    return run(_build_args(argv))


if __name__ == "__main__":
    raise SystemExit(main())
