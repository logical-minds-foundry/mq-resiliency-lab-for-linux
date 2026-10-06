"""Host-side FORCED-DR loss analyzer (spec §7, the RPO>0 marquee).

Unlike dr_baseline.py (which asserts RPO 0 for HA), this expects loss: an abrupt
full-site-A loss after the cross-site DRBD link was broken means every message
the app committed at A *after* the break never reached san-b, so the forced
promote of san-b loses exactly that tail. We feed the framework:

  - secondary_present = seqs the app committed BEFORE the replication break
    (everything after the break was on the dead primary only -> gone), plus any
    the app later re-committed against the DR site after cutover.
  - primary_disk_present = empty: full site loss, the primary's disk is gone
    (a milder "QM crash, disk survives" run would pass the stranded set here).
  - cutover_ts = the break instant.

Then reconcile -> classify -> report, WITHOUT asserting self-correctness.

Run:
    mq-dr-forced <app.jsonl> <svc.jsonl> <break_ts> <kill_ts>
"""

import argparse
import json

from mqrc.dr import Ledger, build_report, exposure, reconcile


def _sent_ts(path):
    out = {}
    with open(path) as fh:
        for line in fh:
            e = json.loads(line)
            if e.get("event") == "sent":
                out[e["seq"]] = e["ts"]
    return out


def main(argv=None):
    ap = argparse.ArgumentParser(description="Attribute the loss of a forced cross-site DR.")
    ap.add_argument("app_ledger", help="the app's JSONL ledger (mq-dr-flow --ledger)")
    ap.add_argument("svc_ledger", help="the Watcher's JSONL ledger (mq-dr-responder --ledger)")
    ap.add_argument("break_ts", type=float, help="epoch seconds the replication link broke")
    ap.add_argument("kill_ts", type=float, help="epoch seconds the primary site died")
    args = ap.parse_args(argv)
    app_path, svc_path = args.app_ledger, args.svc_ledger
    break_ts, kill_ts = args.break_ts, args.kill_ts
    app = Ledger.read_jsonl(app_path)
    svc = Ledger.read_jsonl(svc_path)
    sent_ts = _sent_ts(app_path)

    # The lost window: committed at A after replication broke, before the primary
    # died -- on the dead primary only, never shipped to san-b.
    lost = {s for s, t in sent_ts.items() if break_ts <= t <= kill_ts}
    secondary_present = set(app.sent_seqs()) - lost

    facts = reconcile(
        app,
        svc,
        secondary_present=secondary_present,
        primary_disk_present=set(),  # full site loss
        cutover_ts=break_ts,
    )
    report = build_report("DR-FORCE-1", "pcmk-dr", facts, peak_exposure=exposure(app))
    print(report.to_markdown())
    print(
        f"\nFORCED-DR: {len(app.sent_seqs())} sent, "
        f"{len(lost)} committed at A after the replication break "
        f"(window [{break_ts:.0f},{kill_ts:.0f}]) -> not shipped to san-b -> LOST. "
        f"RPO > 0 by construction; the bucket census above attributes it."
    )
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
