"""Host-side FORCED-DR loss analyzer (spec §7, the RPO>0 marquee).

Unlike dr_baseline.py (which asserts RPO 0 for HA), this expects loss: an abrupt
full-site-A loss after the cross-site DRBD link was broken means every message
the firm committed at A *after* the break never reached san-b, so the forced
promote of san-b loses exactly that tail. We feed the framework:

  - secondary_present = seqs the firm committed BEFORE the replication break
    (everything after the break was on the dead primary only -> gone), plus any
    the firm later re-committed against the DR site after cutover.
  - primary_disk_present = empty: full site loss, the primary's disk is gone
    (a milder "QM crash, disk survives" run would pass the stranded set here).
  - cutover_ts = the break instant.

Then reconcile -> classify -> report, WITHOUT asserting self-correctness.

Run on the host:
    uv run python clients/dr_forced.py <app.jsonl> <svc.jsonl> <break_ts> <kill_ts>
"""

import json
import sys

from mqlab.dr import Ledger, build_report, exposure, reconcile


def _sent_ts(path):
    out = {}
    for line in open(path):
        e = json.loads(line)
        if e.get("event") == "sent":
            out[e["seq"]] = e["ts"]
    return out


def main(app_path, svc_path, break_ts, kill_ts):
    break_ts, kill_ts = float(break_ts), float(kill_ts)
    firm = Ledger.read_jsonl(app_path)
    dtcc = Ledger.read_jsonl(svc_path)
    sent_ts = _sent_ts(app_path)

    # The lost window: committed at A after replication broke, before the primary
    # died -- on the dead primary only, never shipped to san-b.
    lost = {s for s, t in sent_ts.items() if break_ts <= t <= kill_ts}
    secondary_present = set(firm.sent_seqs()) - lost

    facts = reconcile(
        firm,
        dtcc,
        secondary_present=secondary_present,
        primary_disk_present=set(),  # full site loss
        cutover_ts=break_ts,
    )
    report = build_report("DR-FORCE-1", "pcmk-dr", facts, peak_exposure=exposure(firm))
    print(report.to_markdown())
    print(
        f"\nFORCED-DR: {len(firm.sent_seqs())} sent, "
        f"{len(lost)} committed at A after the replication break "
        f"(window [{break_ts:.0f},{kill_ts:.0f}]) -> not shipped to san-b -> LOST. "
        f"RPO > 0 by construction; the bucket census above attributes it."
    )
    return 0


if __name__ == "__main__":
    raise SystemExit(main(*sys.argv[1:5]))
