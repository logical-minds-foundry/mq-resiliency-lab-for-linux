# MQ-service observability — mq_prometheus exporter — Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: superpowers:subagent-driven-development or executing-plans. Steps use `- [ ]`.
>
> **Issue:** #172. **Source recipe:** the #141 spike (verified end-to-end: 2,588 `ibmmq` series from QMPCMK). **Consumer:** the MQ Service panel (#144, merged).

**Goal:** Encapsulate the #141 `mq_prometheus` recipe into reproducible
provisioning so a fresh `mon-probe` + QMPCMK come up exporter-ready, and the MQ
Service panel lights up with live `ibmmq_*` data.

**Architecture:** The exporter runs **client-mode on `mon-probe`** (a stable,
off-cluster node) against the QM **VIP** — never bindings-mode on a cluster node
(the documented HA/DR trap: an exporter welded to a node can't follow the QM as
the VIP moves). A new `mq-exporter` role installs full MQ + builds the exporter
(Go+cgo against `/opt/mqm`) + runs it under systemd; the QM's MQSC enables the
monitoring attributes it needs; Prometheus scrapes `mon-probe:9157`.

**Tech Stack:** Ansible, IBM MQ client libs, Go (cgo), `ibm-messaging/mq-metric-samples`, systemd, Prometheus, Python (`scrape.py`), pytest.

---

## Verified facts (the #141 recipe)

- **Exporter:** build `mq_prometheus` from `ibm-messaging/mq-metric-samples`
  (`go build`/`buildRuntime.sh`, cgo: `CGO_CFLAGS=-I/opt/mqm/inc
  CGO_LDFLAGS=-L/opt/mqm/lib64`). No prebuilt arm64 image; the MQ container's
  `MQ_ENABLE_METRICS` does **not** apply to an RPM/deb install.
- **mon-probe MQ:** install full arm64 MQ from the lab tarball
  `build/mq/...UbuntuLinuxARM64.tar.gz` (no IBM arm64 redistributable client
  exists — 404). `mon-probe` runs **no QM**.
- **Run:** systemd client-mode — `-ibmmq.queueManager QMPCMK
  -ibmmq.connName "10.10.1.200(1414)" -ibmmq.channel APP.SVRCONN
  -ibmmq.monitoredQueues <curated> -ibmmq.monitoredChannels <curated>
  -ibmmq.httpListenPort 9157`, `LD_LIBRARY_PATH=/opt/mqm/lib64`. Defaults:
  namespace `ibmmq`, path `/metrics`.
- **QM side (the gate):** `ALTER QMGR MONQ(MEDIUM) MONCHL(MEDIUM) STATMQI(ON)
  STATQ(ON) STATCHL(MEDIUM)` (without it `$SYS` publications are sparse) +
  `ALTER QMGR MAXHANDS(512)` (the exporter opens a handle per monitored queue;
  default 256 < the ~275 it needed). Curate `monitoredQueues`/`monitoredChannels`
  rather than `*`. The exporter creates its own managed subscriptions — no manual
  ones. `APP.SVRCONN` (`CHLAUTH(DISABLED)`, MCAUSER `mqm`) is the client channel.
- **Prometheus:** no `--web.enable-lifecycle` here → the role restarts Prometheus
  on scrape-config change (SIGHUP-equivalent).
- `mon-probe` is on `net-mgmt` (10.50.0.3) + `net-data-a/-b` — reaches the QMPCMK
  data-plane VIP `10.10.1.200`. Scrape is over `net-mgmt`.

## Scope & boundaries

**In scope:** QMPCMK exporter, end-to-end (QM attrs → exporter on mon-probe →
scrape → panel data).

**Out of scope (follow-up):** monitoring **QMSVC** — it sits on `net-ext`, which
`mon-probe` isn't on; needs a `net-ext` NIC on `mon-probe` + a second exporter
instance (port 9158) + scrape job. The role is parameterized so adding it later is
a config addition, not a rewrite. (Panel's QMSVC row reads no-data until then.)

## File structure

- **New** `ansible/roles/mq-exporter/` — install MQ (reuse `mq-install`) + Go,
  build `mq_prometheus` (idempotent), render a per-QM systemd unit, enable+start.
  Templated `mq-exporter@.service.j2` (instanced by QM) + defaults (port, channel,
  monitored lists).
- **Modify** `ansible/roles/mq-pcmk-qmgr/tasks/main.yml` — add the monitoring
  `ALTER QMGR` + `MAXHANDS(512)` to the QM-create MQSC block (cold-boot one-pass).
- **Modify** `ansible/roles/mq-qmgr/tasks/main.yml` — same `ALTER QMGR` for
  standalone QMs (QMSVC), so it's exporter-ready when we add its instance.
- **Modify** `src/mqlab/scrape.py` — add the `ibmmq` scrape job (`mon-probe:9157`).
- **Modify** `ansible/site-obs.yml` — run `mq-exporter` on the probe (vars:
  QMPCMK / `10.10.1.200(1414)` / `APP.SVRCONN` / curated lists / 9157).
- **Tests** `tests/test_scrape.py` (the ibmmq job), `tests/test_topology_integrity.py` (scrape renders).

## Tasks (TDD for the Python; cold-boot-verified for the infra)

### Task 1 — QM-side monitoring attrs (cold-boot one-pass)
- [ ] `mq-pcmk-qmgr` MQSC block: append `ALTER QMGR MONQ(MEDIUM) MONCHL(MEDIUM) STATMQI(ON) STATQ(ON) STATCHL(MEDIUM)` and `ALTER QMGR MAXHANDS(512)`.
- [ ] `mq-qmgr`: same `ALTER QMGR` (after the listener task).
- [ ] `vrg-validate` (ansible-lint); `vrg-commit`.

### Task 2 — scrape.py: the `ibmmq` job (TDD)
- [ ] Failing `tests/test_scrape.py::test_ibmmq_exporter_scrape_target` — asserts a scrape job/target for `mon-probe:9157` (job `ibmmq`).
- [ ] Add the job to `render_scrape_*`; keep it data-driven off topology where possible.
- [ ] Green; `vrg-commit`.

### Task 3 — the `mq-exporter` role
- [ ] `tasks/main.yml`: (a) `include_role: mq-install` (or its deb step) for full MQ; (b) install Go (`apt: golang-go` or a pinned tarball); (c) fetch `mq-metric-samples` + `go build` `mq_prometheus` with the cgo flags, `creates:` the binary (idempotent, no rebuild on re-run); (d) `template` the systemd unit; (e) enable+start.
- [ ] `templates/mq-exporter@.service.j2`: `User=mqm` (or a dedicated user in mqm group), `Environment=LD_LIBRARY_PATH=/opt/mqm/lib64`, `ExecStart=<binary> -ibmmq.queueManager {{ ... }} -ibmmq.connName "{{ ... }}" -ibmmq.channel {{ ... }} -ibmmq.monitoredQueues {{ ... }} -ibmmq.monitoredChannels {{ ... }} -ibmmq.httpListenPort {{ ... }}`, `Restart=always`.
- [ ] `defaults/main.yml`: port 9157, channel `APP.SVRCONN`, curated `monitoredQueues` (`HA.TEST`, the xmitqs) / `monitoredChannels` (the SENDER/RECEIVER pairs + `APP.SVRCONN`).
- [ ] `vrg-validate`; `vrg-commit`.

### Task 4 — wire into site-obs.yml
- [ ] Add a `hosts: probe` play running `mq-exporter` with the QMPCMK vars.
- [ ] `vrg-validate`; `vrg-commit`.

### Task 5 — full validation + cold-boot verification
- [ ] `vrg-validate` green.
- [ ] **Cold-boot (operator):** rebuild → `mqlab net create/up all` → `mqlab obs up` → bring up `distributed` + `qm create` → `curl mon-probe:9157/metrics | grep ibmmq_qmgr_status` returns `2`; Prometheus has `ibmmq_*` series; the **MQ Service panel** shows QMPCMK status/rate/connections + channels/queues with live data.

## Acceptance (#172 / spec §13 obs)
A fresh cold boot brings `mon-probe` up **exporter-ready** (no manual R&D steps),
`ibmmq_*` flows from QMPCMK into Prometheus, and the MQ Service panel renders live
data — one-pass, per [[cold-rebuild-acceptance-gate]].
