# Box bake manifest — per-role bake vs. configure classification

Authoritative classification of every Ansible role along the **bake / configure**
line, for the lab-bootstrap-performance epic (`logical-minds-foundry/.github#70`,
Task 1 / #602).

## The bake/configure line

The epic builds three box images — `mq-rdqm-rhel9`, `obs-ubuntu2404`,
`infra-ubuntu2404` — and wants each to carry, as a **baked golden image**, the
slow install work that never varies per run, so a per-run bootstrap can skip it.

- **Bake** = image-bakeable install: packages, downloaded/compiled binaries, users,
  directory scaffolding, and *static* config that is identical for every lab. Runs
  once, when the image is built. Never contains secrets, never contains
  topology-/host-specific data.
- **Configure** = per-run: everything keyed to a specific lab instance — queue
  managers, clustering, TLS/PKI material, injected secrets, topology-rendered
  scrape targets / dashboards / DNS zones, and per-host config.

This document is the classification. The **bake playbooks**
(`ansible/bake-mq-rdqm.yml`, `ansible/bake-obs.yml`, `ansible/bake-infra.yml`) and
the single-host inventory (`ansible/inventory/bake-host.ini`) are the mechanism.

> **Scope of #602 (this task): additive only.** The bake playbooks are a new
> foundation. They do **not** change any normal bootstrap behavior — the per-run
> skips (making `site-rdqm.yml` / `site-obs.yml` / `site-dns.yml` skip the baked
> install) land in later tasks (#603–#605). The `main.yml` of every split role
> still runs install **and** configure in sequence, so the per-run path reaches
> the same end state it did before.

## Single-host bakeability

A bake runs against **one host, no lab** (`inventory/bake-host.ini`, group `bake`).
The acceptance is that each baked role is coupling-free: no `run_once`, no
`delegate_to`, no cross-host-group dependency (`groups[...]`, `hostvars[...]`,
`ansible_play_hosts`). An empirical scan plus a read of every role's
`tasks/main.yml` confirmed this for the bake set; the only per-host coupling in a
bake-candidate role is `bind-dns` (keyed by `inventory_hostname`), which is why it
is the one role split (below).

## Split mechanism

Roles that mix install and per-run config are split using the repo's existing
`tasks_from` idiom (the same pattern `mq-exporter` already uses with
`build`/`instance`), **not** separate `<role>-install` role directories:

- `tasks/install.yml` — the bakeable half.
- `tasks/configure.yml` — the per-run half.
- `tasks/main.yml` — `import_tasks: install.yml` then `import_tasks: configure.yml`,
  so the per-run path is unchanged.

The bake playbooks then invoke `include_role: { name: <role>, tasks_from: install }`.
Keeping both halves in one role dir keeps that role's templates, files, and handlers
in scope for both paths (a separate `-install` role would have to duplicate or
re-home them). This satisfies the task's "extract `bind-dns-install`" intent while
staying DRY and behavior-preserving.

Roles split this way: **`prometheus`, `grafana`, `alloy`, `bind-dns`**.

## Per-box bake sets

### `mq-rdqm-rhel9` → `ansible/bake-mq-rdqm.yml`

| Role | In bake | Notes |
|------|---------|-------|
| `acl` (dnf pkg) | ✅ full | Unprivileged-become prereq (from `_rdqm-cluster-ha.yml`). |
| `rdqm-install` | ✅ full | MQ server set + SDK + samples + web, plus bundled LINBIT/DRBD + Pacemaker + MQSeriesRDQM, one pre-QM pass. Its last step seeds the #282/#569 journald `DiagnosticMessages` drop-in (journald-only on RDQM) — the diagnostic default baked into the image. |
| `node-exporter` | ✅ full | All-install (static config); no split needed. |
| `alloy` | ✅ install half | Binary + unit baked; `config.alloy` (per-QM-node mqweb-tail, loki endpoint) + start stay per-run. |

### `obs-ubuntu2404` → `ansible/bake-obs.yml`

| Role | In bake | Notes |
|------|---------|-------|
| `node-exporter` | ✅ full | All-install. |
| `alloy` | ✅ install half | As above. |
| `loki` | ✅ full | All-install (loki binary + logcli + static config). |
| `prometheus` | ✅ install half | Binary + **static** `prometheus.yml` + recording rules + unit baked; the topology-rendered `targets/{node,ibmmq}.json` (`mqlab obs targets` → `build/work/…`) stay per-run. |
| `grafana` | ✅ install half | Package + **static** datasource + dashboard-provider + service baked; the rendered dashboard tree (`build/work/grafana/dashboards/`) and the **admin-password secret** env drop-in stay per-run. |
| `mq-exporter` (`build`) | ✅ build entry | The slow cgo build of `mq_prometheus` against the MQ SDK + Go toolchain. **Ubuntu-only** (pulls MQ via the Ubuntu-deb `mq-install`, apt `golang-go`) → it lives here, on the obs/probe box, not the RHEL rdqm box. Per-instance units + TLS CCDT/keystore stay per-run (`instance` entry, gated by `mq_exporter_tls`). |

### `infra-ubuntu2404` → `ansible/bake-infra.yml`

| Role | In bake | Notes |
|------|---------|-------|
| `bind-dns` | ✅ install half | The one real SPLIT: bind9 package + `/etc/bind/zones` scaffolding baked; zone data + per-host `named.conf.{options,local}` (keyed by `inventory_hostname`, rendered by `mqlab dns render`) + service start stay per-run. |
| `node-exporter` | ✅ full | All-install. |
| `alloy` | ✅ install half | As above. |

## Stays configure (per-run) — never baked

The whole configure surface: queue-manager and cluster creation
(`mq-qmgr`, `mq-pcmk-qmgr`, `mq-nativeha`, `rdqm-ha`, `pcmk-cluster`,
`pcmk-stonith`); RDQM/HA/DR state and reconcile (`rdqm-active-node`, `rdqm-state`,
`cluster-state`, `nativeha-state`, `host-net-state`, `host-resolver`, `net-reach`);
all PKI/TLS (`lab-pki`, `pki-distribute`, `rdqm-replication-tls`, `rdqm-app-tls`,
`rdqm-ssh-access`); messaging config (`mq-inter-qm`, `mq-event-monitor`,
`app-requester`, `mq-diag-logging` per-QM `qmini`); the SAN/iSCSI substrate
(`drbd-san`, `iscsi-target`, `iscsi-initiator`); and `mqweb` (per-QM REST config +
injected `mqweb_admin_password`, so it is configure even though the mqweb *server*
binary is installed by `rdqm-install`).

## Deviations from the epic plan's classification head-start

Verified by reading each role's `tasks/main.yml`:

1. **`mq-install` is NOT in `bake-mq-rdqm`.** It is Ubuntu-only (copies the
   UbuntuLinux deb tar, `apt-get install`). The RHEL rdqm box installs MQ via
   `rdqm-install` (LinuxX64 rpm tar). Listing both would break on RHEL.
2. **`rhel-ha-repo` is NOT in `bake-mq-rdqm`.** It serves the HighAvailability repo
   for the **pcmk-rhel** arm only (`site-pcmk-rhel.yml`, `pcmk-cluster`). RDQM gets
   Pacemaker/DRBD from the MQ Advanced tar's PreReqs, so the rdqm box never uses it.
3. **`mq-exporter` build is in `bake-obs`, not `bake-mq-rdqm`.** It is Ubuntu-only
   (includes the Ubuntu-deb `mq-install`, apt `golang-go`) and the exporter runs on
   the obs/probe box.
4. **`node-exporter` and `loki` are baked as full roles** (not split): their config
   is static, so the whole role is effectively install.

## Naming note: `rdqm-install` = "MQ product **+** RDQM stack"

`rdqm-install` installs the **entire MQ product** (server/SDK/samples/web) *plus*
the bundled DRBD/Pacemaker/MQSeriesRDQM in one pre-QM pass — it is not just the
RDQM add-on. The name is nonetheless accurate: it is the install path **for an
RDQM node** (which requires MQ), and it is used *only* by the RDQM plays. **Native
HA does not use it** — `nha-rhel` installs via `mq-nativeha/tasks/install-RedHat.yml`
(its own MQ-product install), so no DRBD is ever baked into a native-HA image.

The real smell this surfaces is duplication, not misnaming: the MQ-product install
(fetch LinuxX64 tar → `mqlicense -accept` → install) is copied across
`rdqm-install`, `mq-nativeha` (RedHat + Debian), `mq-install`, and `mq-client`,
with no shared building block. Baking makes it visible — `mq-rdqm-rhel9` bakes MQ
via `rdqm-install` while the follow-on `mq-nativeha-rhel9` box would bake the same
product via `mq-nativeha`. Extracting a shared `mq-product-install` `tasks_from`
is deferred to the follow-on brainstorm (`logical-minds-foundry/.github#72`), where
the nativeha/pcmk box generalization forces the duplication into the open.

## Full-apply proof is deferred

Per the epic plan, full proof of correctness (MQ media + install DVD present, a real
box build) rides the later fat-box build task. #602's acceptance is: bake playbooks
well-formed, disjoint from configure, the one coupling split (`bind-dns`) extracted,
`ansible-playbook --syntax-check` green, and `vrg-validate` green.
