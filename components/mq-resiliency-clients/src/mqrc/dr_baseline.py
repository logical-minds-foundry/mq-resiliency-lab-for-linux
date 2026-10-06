"""Host-side LIVE self-correctness baseline (spec §7).

Loads the app + Watcher ledgers collected from a NO-FAULT run through the live
message path and asserts the instrument agrees with itself: every message the
app SENT is CONFIRMED, and SVC received each exactly once. Fail loud
(SelfCorrectnessError) otherwise — if the oracle and the app ledger disagree with
no fault injected, no later drill can be trusted.

Run:
    mq-dr-baseline <app.jsonl> <svc.jsonl>
"""

import argparse

from mqrc.dr import Ledger, assert_self_correct, build_report, exposure, reconcile


def main(argv=None):
    ap = argparse.ArgumentParser(description="Assert a no-fault run's ledgers agree (RPO 0).")
    ap.add_argument("app_ledger", help="the app's JSONL ledger (mq-dr-flow --ledger)")
    ap.add_argument("svc_ledger", help="the Watcher's JSONL ledger (mq-dr-responder --ledger)")
    args = ap.parse_args(argv)
    app = Ledger.read_jsonl(args.app_ledger)
    svc = Ledger.read_jsonl(args.svc_ledger)
    facts = reconcile(
        app,
        svc,
        secondary_present=app.sent_seqs(),  # no fault: everything is present
        primary_disk_present=set(),
        cutover_ts=float("inf"),
    )
    assert_self_correct(facts)  # raises if the instrument disagrees with itself
    report = build_report("BASELINE", "msgpath", facts, peak_exposure=exposure(app))
    print(report.to_markdown())
    print(f"\nSELF-CORRECT OK: {len(facts)} messages, all confirmed.")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
