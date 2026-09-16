# Prometheus 2.x → 3.x Migration — Spike Findings

**Date:** 2026-09-16
**Issue:** #1128 (epic logical-minds-foundry/.github#236 — observability version
currency)
**Task:** Plan Task 6 (SPIKE). Prometheus `2.53.2` → 3.x is a **major** boundary
(breaking config / PromQL / scrape changes) and the 2.x line is **EOL**. This spike
audits the lab's actual Prometheus config surface against the documented 2→3
breaking set, verifies the download artifacts resolve for both arches and both
candidate versions, and produces a go/no-go recommendation. Gates T7 (#1133, the
executor) and VAL-C (#1136, live validation).

**Status:** DECIDED — every version/artifact fact below is cited to the GitHub
Releases API (queried 2026-09-16); every breaking-change and LTS fact is cited to
the official Prometheus migration guide / release-cycle page (fetched 2026-09-16).
No version, URL, or behaviour is asserted from memory. Where the decision rests on
judgment rather than a cited fact, it is labelled **(judgment)**.

**Verdict:** **GO — target `3.13.3` (current LTS).** The headline finding is that
for *this lab* the 2→3 boundary is a **version bump, not a config migration**: the
lab never adopted any of the five config surfaces that Prometheus 3.x breaks, so the
required config diff is effectively empty (one version-string change). The one live
risk — scrape Content-Type strictness — is low and is verified on a live scrape in
T7/VAL-C, not by a config change here.

---

## 1. Authoritative sources

**Artifacts / versions** — GitHub Releases API, `repos/prometheus/prometheus`,
queried 2026-09-16:

| Tag | Published | Prerelease | `linux-amd64.tar.gz` | `linux-arm64.tar.gz` |
|---|---|---|---|---|
| `v3.13.3` | 2026-09-07 | no | present (104,607,333 B) | present (95,663,317 B) |
| `v3.14.0` | 2026-08-18 | no | present (107,111,714 B) | present (98,015,563 B) |

Download URL pattern (both verified to resolve, both arches, both tags):
`https://github.com/prometheus/prometheus/releases/download/v<V>/prometheus-<V>.linux-<arch>.tar.gz`

**Breaking changes** — <https://prometheus.io/docs/prometheus/latest/migration/>
(fetched 2026-09-16).

**LTS designation** — <https://prometheus.io/docs/introduction/release-cycle/>
(fetched 2026-09-16): "Prometheus **3.13** (2026-07-01 → 2027-07-31) — Currently
Supported"; the prior 3.5 LTS reached end of life 2026-07-31; the lab's current pin
2.53 LTS reached end of life 2025-07-31.

---

## 2. The lab's Prometheus config surface (what actually exists)

The entire 3.x-relevant surface is two rendered files plus the install role:

- `ansible/roles/prometheus/templates/prometheus.yml.j2` — `global` scrape/eval
  intervals, one `rule_files` entry, and **three** `scrape_configs`, all of which
  are `file_sd_configs` (`node`, `ibmmq`). **No** `remote_write`, **no** `alerting`
  / `alertmanagers` block, **no** histogram scrape options, **no** regex relabel
  rules.
- `ansible/roles/prometheus/files/lab.rules.yml` — **one recording rule**
  (`lab_network_health`). Instant-vector arithmetic with `bool` modifiers,
  `min by` / `count by` aggregations, and `or`. **No** regex matchers (`=~`/`!~`),
  **no** range selectors (`[...]`), **no** subqueries. No alerting rules at all.
- `ansible/roles/prometheus/tasks/install.yml` — downloads
  `prometheus-<version>.linux-<arch>.tar.gz`, installs the binary, and writes a
  systemd unit whose `ExecStart` flags are `--config.file`,
  `--storage.tsdb.path`, `--storage.tsdb.retention.time`, `--web.listen-address`.
- `ansible/roles/prometheus/defaults/main.yml` — `prometheus_version: "2.53.2"`;
  `prometheus_arch` maps `aarch64→arm64`, else `amd64`.

---

## 3. Audit against the 2→3 breaking set

Each row: the documented breaking change (data) vs. whether the lab is exposed
(data), and the required action (judgment).

| # | Breaking change (per migration guide) | Lab exposed? | Action |
|---|---|---|---|
| 1 | **remote-write HTTP/2 default flip** — `http_config.enable_http2` in `remote_write` items now defaults to `false`. | **No** — the lab has no `remote_write` block. | None. |
| 2 | **`scrape_classic_histograms` renamed** to `always_scrape_classic_histograms`. | **No** — the key is not used; no histogram scrape config. | None. |
| 3 | **PromQL regex `.` now matches newline** (replace `.` with `[^\n]` to keep v2 behaviour). | **No** — no regex matchers anywhere in the rule or config. | None. |
| 4 | **PromQL range/lookback selectors now left-open, right-closed** (was left-closed). Affects subqueries/ranges. | **No** — the one recording rule has no range selectors and no subqueries; it is instant-vector arithmetic. | None. |
| 5 | **Scrape Content-Type strictness** — v3 fails a scrape when the exposition `Content-Type` is missing / unparsable / unrecognized; `fallback_scrape_protocol` sets a fallback. | **Low risk — verify live.** See §4. | No config change now; verify on a live scrape in T7/VAL-C. |
| 6 | **Alertmanager v1 API removed** — must use Alertmanager ≥0.16.0 with `api_version: v2`. | **No** — the lab runs no Alertmanager and has no `alerting` block; it uses recording rules only. | None. |

**Command-line flags:** none of the four flags in the systemd unit
(`--config.file`, `--storage.tsdb.path`, `--storage.tsdb.retention.time`,
`--web.listen-address`) appears in the migration guide's removed/renamed list
(data). The removed/graduated set is all feature flags
(`promql-at-modifier`, `promql-negative-offset`, `new-service-discovery-manager`,
`expand-external-labels`, `no-default-scrape-port`, `agent`,
`remote-write-receiver`, `auto-gomemlimit`, `auto-gomaxprocs`) plus two new CLI
flags (`--agent`, `--web.enable-remote-write-receiver`) — none of which the unit
uses. **(judgment)** These four are standard operational flags carried unchanged
since 2.x; they remain valid in 3.x. A boot smoke check in T7/VAL-C confirms this
against the real binary.

---

## 4. The one live risk: scrape Content-Type strictness

Prometheus 3 rejects a scrape whose exposition `Content-Type` is missing or
unrecognized (breaking change #5). The lab scrapes two exporters:

- **node_exporter** — the official Prometheus node exporter, which serves via the
  `client_golang` `promhttp` handler and sets a valid
  `text/plain; version=0.0.4; charset=utf-8` (or negotiated OpenMetrics)
  Content-Type. **(judgment)** Not at risk.
- **mq_prometheus** — IBM `mq-metric-samples` (bumped to v6.0.0 in #1120), which
  likewise exposes via the standard Go `promhttp` handler. **(judgment)** Expected
  to send a valid Content-Type and therefore not at risk.

Both assessments are **judgment**, not a captured live header, so they are not
asserted as fact here. The correct place to prove them is a live scrape against the
3.x binary — the job of T7 (execute) and VAL-C (validate). **If** a live scrape
surfaces a rejected target, the minimal, targeted fix is a per-job
`fallback_scrape_protocol: PrometheusText0.0.4` on the affected `scrape_config` —
not a broad rewrite. This is a contingency, not a required part of the diff.

---

## 5. Required config diff

**Effectively empty.** Because the lab is unexposed to breaking changes 1–4 and 6,
and change 5 needs no config unless a live scrape proves otherwise, the migration
diff is a single version bump:

```diff
# ansible/roles/prometheus/defaults/main.yml
-prometheus_version: "2.53.2"
+prometheus_version: "3.13.3"
```

Everything downstream of that string is already 3.x-correct: the download URL
template, the arch map (arm64/amd64), the systemd flags, `prometheus.yml.j2`, and
`lab.rules.yml` all carry over unchanged. (T7 owns making this edit and the
cold-rebuild proof; this spike only specifies it.)

---

## 6. Recommendation — GO, target `3.13.3` (judgment)

**Recommended decision: `go-3.13.3`.**

Rationale (judgment, built on the cited data above):

1. **LTS is the right posture for the observability substrate, and it is the lab's
   established convention.** The current pin `2.53.2` was itself chosen *because* it
   was the 2.x **LTS** (see `docs/plans/2026-06-10-lab-observability-foundation.md`).
   3.13 is the current LTS (supported through 2027-07-31); the non-LTS 3.14 minor
   will fall out of support sooner and puts the lab on the minor-version treadmill.
   For a substrate the lab wants boring and durable, LTS wins.
2. **Choosing LTS costs nothing in freshness here.** `3.13.3` (2026-09-07) is
   *newer* than `3.14.0` (2026-08-18): the LTS line is getting more recent patch
   backports than the 3.14 minor has had releases. So `3.13.3` is simultaneously the
   longest-supported *and* the most-recently-patched option. There is no
   "stability vs. currency" tradeoff to make — LTS dominates on both axes today.
3. **Migration risk is near-zero**, so there is no "let 3.14 bake first" argument
   pulling toward the newer minor, and no reason to defer.

Why not `3.14.0`: it is a standard (non-LTS) minor with a shorter support horizon,
no compensating benefit for this lab's needs, and — as of today — it is even
slightly staler than the LTS patch.

Why not **defer**: the driver for the epic is version currency and getting off the
EOL 2.x line; the audit shows the move is a one-line bump with no blocking config
work, so deferring would carry the EOL risk forward for no engineering saving. (Note
per the plan: a `defer` decision drops T7 (#1133) and VAL-C (#1136) and the epic
retrospective records the deferral — so `defer` is a real, scoped option, just not
the one the evidence supports.)

---

## 7. Go/no-go comment — pending human gate

The epic plan records the go/no-go as a **comment on epic #236**, and that decision
sets downstream scope (a `defer` drops T7 and VAL-C). Per the epic-driver handoff for
this task, **this spike does not post that comment itself** — it surfaces the
recommendation (`go-3.13.3`, §6) for the human decision gate. The comment is posted
by the epic driver once the recommendation is accepted.
