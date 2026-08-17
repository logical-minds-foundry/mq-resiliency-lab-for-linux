# `mqmetric` corrupts the queue-manager `hostname` label — dashes rewritten to dots

> **Issue:** #1042 · **Epic:** `logical-minds-foundry/.github`#29 (ad-hoc) ·
> also relevant to `logical-minds-foundry/.github`#128 (observability component) ·
> **Date:** 2026-08-17 · **Status:** Findings captured — upstream bug to be filed by
> hand (see Appendix A).
> **Scope:** The `hostname` label emitted by the stock IBM MQ Prometheus exporter
> (`mq_prometheus`, from `ibm-messaging/mq-metric-samples`) via its collector library
> `ibm-messaging/mq-golang`. Distributed (Linux) queue managers, MQ 9.3.2+.

---

## 0. How to read this report

Every claim is labelled **[data]** (what the source code / commit history / issue tracker
actually shows, cited) vs **[judgment]** (reasoning on top of those facts). The separation is
deliberate: this finding will be reported upstream, and the case has to survive independent
verification.

**Provenance.** All source and history claims were taken from the upstream repositories via
the GitHub REST API and the raw file contents (retrieved 2026-08-17), then confirmed against
the raw file with `grep`/`sed` rather than a summarizer. Commit SHAs, file paths, and line
numbers are cited inline; full source list in §8.

---

## 1. The defect

**[data]** The stock exporter emits a per-queue-manager `hostname` label whose value has
**every dash (`-`) replaced with a dot (`.`)**. A queue manager whose OS hostname is
`mq-prod-01` is reported as `mq.prod.01`. The mangled string is not the machine's name and
does not resolve.

**[data]** The transformation is a single unconditional line in the shared collector library
`ibm-messaging/mq-golang`, file **`mqmetric/qmgr.go`**, function
**`GetQueueManagerAttribute()`** (current `master`, ~line 606):

```go
func GetQueueManagerAttribute(key string, attribute int32) string {
	v := DUMMY_STRING
	switch attribute {
	case ibmmq.MQCACF_HOST_NAME:
		v = qMgrInfo.HostName
		v = strings.ReplaceAll(v, "-", ".")   // ← corrupts the hostname
	default:
		v = DUMMY_STRING
	}
	v = strings.TrimSpace(v)
	if v == "" {
		v = DUMMY_STRING
	}
	return v
}
```

There is no configuration option to disable it, no version guard, and no code comment
explaining it.

## 2. The data path — where the corruption enters

**[data]** The queue manager's hostname originates from MQ's own `DIS QMSTATUS` response
(the `MQCACF_HOST_NAME` PCF attribute, available from MQ 9.3.2). Tracing it through the
library:

1. **Parse** (`mqmetric/qmgr.go`, `parseQMgrData`): the value is stored **verbatim**, only
   whitespace-trimmed — `hostname = strings.TrimSpace(elem.String[0])`. Dashes intact.
2. **Store**: `qMgrInfo.HostName = hostname`. Still intact.
3. **Read** (`GetQueueManagerAttribute`, §1): every read applies
   `strings.ReplaceAll(v, "-", ".")`. **This is the only place the value is altered.**

**[data]** The exporter command
(`ibm-messaging/mq-metric-samples`, `cmd/mq_prometheus/exporter.go`) applies **no** further
processing — it assigns the already-mangled value directly: `labels["hostname"] = lastHostname`
(where `lastHostname = mqmetric.GetQueueManagerAttribute(config.cf.QMgrName, ibmmq.MQCACF_HOST_NAME)`).

**[judgment]** The corruption is therefore entirely upstream in `mq-golang`, not in the
exporter, not in MQ, not in Prometheus, and not in Grafana. It reaches the exposition
faithfully because every layer below the library trusts the library's output.

## 3. Provenance — when the line was introduced, and why that matters

**[data]** The `hostname` label feature did **not** originally contain this defect. Commit
`d5bb8e3f0` "Add hostname for 9.3.2 qmgrs" (2023-01-11) created `GetQueueManagerAttribute()`
returning the hostname **clean**:

```go
case ibmmq.MQCACF_HOST_NAME:
    v = qMgrInfo.HostName   // no replacement in the original feature
default:
```

**[data]** The `strings.ReplaceAll(v, "-", ".")` line was added ~2 years later in commit
**`76d2dec31`**, released as **mq-golang v5.6.2 — "Update for MQ 9.4.2" (2025-02-27)**. The
entire change to `qmgr.go` in that release was `+2/−1`: a copyright-year bump and this one
line. The 9.4.2 release drop touched 28 files; its commit message is a 15-bullet changelog
covering PCF mapping, EXTENDED queue metrics, Native HA cross-region metrics, and other work
— and **does not mention the hostname change at all**. The `CHANGELOG.md` entry for v5.6.2 is
likewise silent on it.

**[data]** All `mq-golang` commits are authored by the release-bot account `ibmmqmet`
(author email `marke_taylor@uk.ibm.com`); the repository publishes squashed internal drops
rather than individual pull requests.

**[judgment]** Two consequences follow. First, the individual authorship and the reasoning
behind the specific line are not independently recoverable from the public record — there is
no PR, no review thread, and no comment to consult; the line simply appears inside a large
release commit whose stated purpose is something else. Second, this is precisely why the
change is hard to defend *as a change*, independent of who made it: a value-altering edit to a
widely-consumed label reached a shipping release with no changelog note, no comment, no
visible review, and no test that caught it. This report takes no position on the author's
competence; it documents that the change is undocumented, unexplained, and buried inside an
unrelated release.

## 4. Why this is a bug, not a normalization

**[data]** Dashes are valid in hostnames. DNS labels (RFC 952; RFC 1123 §2.1) permit letters,
digits, and hyphens; the dot is the *label separator*. Rewriting a hyphen (valid inside a
label) to a dot changes the name's structure — it invents subdomain boundaries and yields a
name that does not correspond to the host.

**[data]** Dashes are also valid in Prometheus **label values**, which may contain arbitrary
UTF-8. Only *metric names* and *label names* are constrained to `[a-zA-Z_][a-zA-Z0-9_]*`, and
the standard sanitization for those replaces invalid characters with an **underscore**, never
a dot. `hostname` is a label *value*, so no sanitization is warranted at all.

**[judgment]** So the transformation cannot be justified as Prometheus-compatibility hygiene:
it is neither required (label values are unconstrained) nor consistent with the convention it
superficially resembles (which uses `_`, and applies to names, not values). It converts a
correct value into an incorrect one. The most charitable technical hypotheses — an inverted
direction (intending to sanitize dots→dashes/underscores for a different backend and writing
it backwards), or a one-off environment-specific fix that leaked into the shared library — are
pure speculation; nothing in the record supports any of them.

## 5. Impact and affected versions

- **[data]** Only the `hostname` label is affected. Every other queue-manager, queue, and
  channel label passes through untouched.
- **[data]** Reproduces for queue managers at **9.3.2+** (which report `HOSTNAME`) scraped by
  an exporter built against **mq-golang ≥ v5.6.2** (2025-02 onward). Exporters built against
  v5.4.0–v5.6.1 emit the label **uncorrupted**.
- **[data]** The line is present in current `master`; upgrading does not fix it.
- **[judgment]** Diagnostic corollary: an environment exhibiting the corruption is running an
  exporter built against mq-golang ≥ v5.6.2 — a usable way to date/scope a fleet's exporters.
- **[judgment]** Any consumer that joins on, alerts on, or navigates from the `hostname` label
  (dashboards, inventory correlation, "click through to the host") is silently given a
  non-resolving string. In a resiliency context this is worse than a missing label, because it
  looks valid.

## 6. Has anyone reported it?

**[data]** A search of both upstream issue trackers on 2026-08-17 returned **no** report of
this behaviour: `"dashes"` matched **0** issues in each of `mq-metric-samples` and `mq-golang`.
The nearest hostname-related items are `mq-metric-samples`#184 (the original *request* to add
the hostname/description tags) and `mq-metric-samples`#445 (an unrelated `exported_qmgr` vs
`qmgr` label discrepancy). The defect has stood unreported for ~18 months since v5.6.2.

**[judgment]** The absence of any report over that span is consistent with a small population
of consumers exercising this specific label against dashed hostnames in earnest.

## 7. Recommendation

1. **[judgment] File upstream against `ibm-messaging/mq-golang`** (Appendix A draft). The fix
   is the deletion of a single line. A tight, evidence-backed report naming the exact file,
   line, and the introducing commit gives the maintainer everything needed to remove it.
2. **[judgment] Do not fix at the leaf.** A Grafana per-panel `.`→`-` transform treats the
   symptom three layers downstream and has to be repeated on every panel.
3. **[judgment] Interim mitigation, if one is needed before the upstream fix lands:** a
   Prometheus `metric_relabel_configs` rule on the scrape that maps `.`→`-` on the `hostname`
   label — still a symptom patch, but applied once, centrally, at the right altitude. This is
   lossy in the (rare) case of a hostname that legitimately contains a dot alongside dashes, so
   it is a stopgap, not a cure.

## 8. Sources

Upstream repositories, retrieved 2026-08-17:

- **Introducing commit** — `mq-golang` `76d2dec31`, "Update for MQ 9.4.2" (v5.6.2, 2025-02-27);
  adds `strings.ReplaceAll(v, "-", ".")` as a `+1` line:
  <https://github.com/ibm-messaging/mq-golang/commit/76d2dec31>
- **Original clean feature** — `mq-golang` `d5bb8e3f0`, "Add hostname for 9.3.2 qmgrs"
  (2023-01-11); `GetQueueManagerAttribute()` with no replacement:
  <https://github.com/ibm-messaging/mq-golang/commit/d5bb8e3f0>
- **Current source** — `mq-golang` `mqmetric/qmgr.go` (`GetQueueManagerAttribute`, the live
  `ReplaceAll` line): <https://github.com/ibm-messaging/mq-golang/blob/master/mqmetric/qmgr.go>
- **Exporter pass-through** — `mq-metric-samples` `cmd/mq_prometheus/exporter.go`
  (`labels["hostname"] = lastHostname`, no further processing):
  <https://github.com/ibm-messaging/mq-metric-samples/blob/master/cmd/mq_prometheus/exporter.go>
- **CHANGELOG** — `mq-golang` `CHANGELOG.md`, v5.6.2 entry (no mention of the hostname change):
  <https://github.com/ibm-messaging/mq-golang/blob/master/CHANGELOG.md>
- **Prometheus naming/labels** — data model (label values are unconstrained UTF-8; names use
  `[a-zA-Z_][a-zA-Z0-9_]*`): <https://prometheus.io/docs/concepts/data_model/>
- **Hostname syntax** — RFC 952; RFC 1123 §2.1 (hyphens valid in labels; dot is the separator):
  <https://www.rfc-editor.org/rfc/rfc1123>

### 8.1 In-repo references

- `ansible/roles/mq-exporter/` — the stock `mq_prometheus` deployment (client-mode on
  `mon-probe`) that surfaces this label.
- `docs/reports/2026-07-15-cli-only-mq-metrics-discovery.md` — establishes that the stock
  exporter is MQI-sourced and enumerates the `ibmmq_*` label families it emits.

---

## Appendix A — Proposed upstream bug report (DRAFT — to be rewritten by hand)

> **This is a starting-point draft, not the text to file.** It is to be rewritten in the
> reporter's own first-person voice before submission to `ibm-messaging/mq-golang`. Keep it
> factual and non-inflammatory; the one-line fix and the exact provenance are the persuasive
> parts. Fill in the reporter's actual exporter version and MQ level where marked `<…>`.

**Title:** `mqmetric` rewrites `-` to `.` in the queue-manager `hostname` label, producing an invalid hostname

**Component / version:** `mq-golang` `mqmetric` (observed via `mq_prometheus` /
`mq-metric-samples`, built against mq-golang `<v5.6.2 or later>`); queue managers at MQ
`<9.3.2+>` on Linux.

**Summary:**
The `hostname` label emitted for queue-manager metrics has every `-` replaced with `.`. A queue
manager whose hostname is `mq-prod-01` is reported as `hostname="mq.prod.01"`, which is not the
host's name and does not resolve.

**Root cause:**
`mqmetric/qmgr.go`, `GetQueueManagerAttribute()`:

```go
case ibmmq.MQCACF_HOST_NAME:
    v = qMgrInfo.HostName
    v = strings.ReplaceAll(v, "-", ".")   // <-- this line
```

`MQCACF_HOST_NAME` (from `DIS QMSTATUS`) is parsed and stored verbatim; this read-side
`ReplaceAll` is the only place the value is altered. `cmd/mq_prometheus/exporter.go` assigns
the result straight to `labels["hostname"]`.

**Expected vs actual:**
- Expected: `hostname="mq-prod-01"` (the value MQ reports).
- Actual: `hostname="mq.prod.01"`.

**Why this is incorrect:**
Hyphens are valid in hostnames (RFC 952 / RFC 1123 §2.1); the dot is the DNS label separator,
so the substitution changes the name's meaning and yields a non-resolving string. Hyphens are
also valid in Prometheus label *values* (only metric/label *names* are restricted, and those
sanitize to `_`, not `.`), so no normalization is warranted on this value.

**Provenance:**
The `hostname` feature originally shipped clean in `d5bb8e3f0` (2023-01-11). The `ReplaceAll`
line was introduced later in `76d2dec31` ("Update for MQ 9.4.2", v5.6.2), with no changelog
note, no code comment, and no stated rationale.

**Suggested fix:**
Delete the `strings.ReplaceAll(v, "-", ".")` line so the hostname is returned as MQ reports it.
If any downstream normalization is ever required, it should be opt-in and must not alter a valid
hostname by default.
