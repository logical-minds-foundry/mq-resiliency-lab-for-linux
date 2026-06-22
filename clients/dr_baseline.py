"""Host-side LIVE self-correctness baseline (spec §7).

Loads the app + Watcher ledgers collected from a NO-FAULT run through the live
message path and asserts the instrument agrees with itself: every message the
app SENT is CONFIRMED, and SVC received each exactly once. Fail loud
(SelfCorrectnessError) otherwise — if the oracle and the app ledger disagree with
no fault injected, no later drill can be trusted.

Run on the host:
    uv run python clients/dr_baseline.py <app.jsonl> <svc.jsonl>
"""

import sys

from mqlab.dr import Ledger, assert_self_correct, build_report, exposure, reconcile


def main(app_path, svc_path):
    app = Ledger.read_jsonl(app_path)
    svc = Ledger.read_jsonl(svc_path)
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
    raise SystemExit(main(sys.argv[1], sys.argv[2]))
