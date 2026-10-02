# Box bake manifest — per-role bake vs. configure classification

Authoritative classification of every Ansible role along the **bake / configure**
line, for the lab-bootstrap-performance epic (`logical-minds-foundry/.github#70`,
Task 1 / #602).

> For the wider picture this classification serves — the box taxonomy, the
> `build-fatbox.sh` build pipeline, and the three rebuild tiers — see
> [`box-model.md`](box-model.md).

## The bake/configure line

The lab bakes **seven fat box images** — `mq-rdqm-rhel9`, `obs-ubuntu2404`,
`infra-ubuntu2404`, and `mq-ubuntu2404` (#659) from the bootstrap-performance
epic; `mq-nativeha-rhel9` (#667) from the follow-on native-HA-RHEL baking epic
(`logical-minds-foundry/.github#88`); and `mq-nativeha-ubuntu` (#103 T6) and
`pcmk-ubuntu` (#103 T7) from the arch-native box-building epic
(`logical-minds-foundry/.github#103`). The log-search stack (OpenSearch + Dashboards
+ Data Prepper, `logical-minds-foundry/.github#149`) was originally its own
`logsearch-ubuntu2404` box, but the observability-consolidation epic
(`logical-minds-foundry/.github#267`) folded it into `obs-ubuntu2404` (#1178) and
retired the standalone box (#1179). Each box carries, as a **baked golden image**,
the slow install work that never varies per run, so a per-run bootstrap can skip it.

- **Bake** = image-bakeable install: packages, downloaded/compiled binaries, users,
  directory scaffolding, and *static* config that is identical for every lab. Runs
  once, when the image is built. Never contains secrets, never contains
  topology-/host-specific data.
- **Configure** = per-run: everything keyed to a specific lab instance — queue
  managers, clustering, TLS/PKI material, injected secrets, topology-rendered
  scrape targets / dashboards / DNS zones, and per-host config.

This document is the classification. The **bake playbooks**
(`ansible/bake-mq-rdqm.yml`, `ansible/bake-obs.yml`, `ansible/bake-infra.yml`,
`ansible/bake-mq-ubuntu.yml`, `ansible/bake-nativeha-rhel.yml`,
`ansible/bake-nativeha-ubuntu.yml`, `ansible/bake-pcmk-ubuntu.yml`) and the
single-host inventory (`ansible/inventory/bake-host.ini`) are the mechanism.

> **Scope of #602 (this task): additive only.** The bake playbooks are a new
> foundation. They do **not** change any normal bootstrap behavior — the per-run
> skips (making `site-rdqm.yml` / `site-obs.yml` / `site-dns.yml` skip the baked
> install) landed in later tasks (#603–#606, #659). The `main.yml` of every split
> role still runs install **and** configure in sequence, so the per-run path
> reaches the same end state it did before.

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

Roles split this way: **`prometheus`, `grafana`, `alloy`, `bind-dns`**, plus the
three log-search-tier roles the log-search epic added the same way —
**`opensearch`, `opensearch-dashboards`, `data-prepper`** (`logical-minds-foundry/.github#149`).

## Phased startup — baked inert, started per-run

Baking a service's *software* does not mean baking it *running*. A service that
comes up on first boot before its per-run config exists would crash-loop or race
the configure step, so the rule is: **bake the install half, leave the service
inert** (unit present, not started), and let the per-run configure half drop the
instance config and start it. This is why the split roles above bake only their
install halves (e.g. `alloy` needs a per-run `config.alloy`; on the obs box
`loki`/`prometheus`/`grafana` — and, since the log-search tier was consolidated onto
obs (#1179), `opensearch`/`opensearch-dashboards`/`data-prepper`, after their per-run
renders (index template, snapshot repo, pipelines) — are all started by
`site-obs.yml`).

Two services are the deliberate **benign exceptions**, left *enabled* at bake
(#642) because they have no per-run config dependency and cannot boot in a broken
pre-config state:

- **`node_exporter`** — static host-metrics exporter; baked whole and enabled, so
  a clone's tiles go green as soon as it boots.
- **`rdqm.service`** — IBM's rpm-shipped `oneshot` RDQM reboot daemon
  (auto-enabled by `MQSeriesRDQM`); production-intended for reboot survival and
  verified benign, so it is left enabled rather than forced inert.

## Every Ubuntu box: per-login dynamic MOTD disabled at bake (#1229)

Each Ubuntu bake playbook (`bake-obs.yml`, `bake-infra.yml`, `bake-mq-ubuntu.yml`,
`bake-nativeha-ubuntu.yml`, `bake-pcmk-ubuntu.yml`) has its own play near the end
that runs the `motd-off` role (only the #1250 boot-trim play comes after it). The RHEL bakes do not include it: the role is
Ubuntu-specific and asserts a Debian-family host.

| Role | In bake | Notes |
|------|---------|-------|
| `motd-off` | ✅ full | Comments out the `pam_motd.so` session lines in `/etc/pam.d/sshd` and `/etc/pam.d/login`, so no login runs `/etc/update-motd.d/` (and `50-landscape-sysinfo` with it). Masks `motd-news.timer`. Ends with fail-loud checks: no `/etc/pam.d` file may keep an active `pam_motd` line, and the timer must read `masked`. No per-run half. |

Rationale and evidence: [`box-model.md` §2](box-model.md#no-per-login-dynamic-motd-1229).
The role is in each Ubuntu bake's manifest-hash closure, so introducing it (and
any later edit to it) flips all five Ubuntu boxes to BUILD. The RHEL boxes are
unaffected.

## Every Ubuntu box: cloud-init and snapd trimmed off the boot path (#1250)

Each Ubuntu bake playbook (`bake-obs.yml`, `bake-infra.yml`, `bake-mq-ubuntu.yml`,
`bake-nativeha-ubuntu.yml`, `bake-pcmk-ubuntu.yml`) ends with its own play that
runs `cloud-init-trim` and then `snapd-off`. It runs last so the snapd guard sees
every snap the bake installed. The RHEL bakes include neither: both roles are
Ubuntu-specific and assert a Debian-family host.

| Role | In bake | Notes |
|------|---------|-------|
| `cloud-init-trim` | ✅ full | Keeps `cloud-init-local` (re-renders the mgmt NIC's netplan for the clone's MAC) and `cloud-init` (trimmed to `growpart` + `resizefs`, which grow `/` to the 20G guest disk). Drops `/etc/cloud/cloud.cfg.d/99_lab_trim.cfg` (empty config/final module lists, `preserve_hostname: true`) and masks `cloud-config.service` and `cloud-final.service`. Fail-loud checks: cloud-init's own merged config carries the trimmed lists, the two services read `masked`, the two kept services read `enabled`, and no `cloud-init.disabled` marker exists. No per-run half. |
| `snapd-off` | ✅ full | `snapd_off_mode: purge` (default): refuses if `snap list` shows any snap, purges `snapd`, pins it out (`/etc/apt/preferences.d/99lab-no-snapd`), and verifies it is gone with no install candidate. `keep` (obs on aarch64 only, for the chromium snap behind `grafana-image-renderer`): masks only `snapd.seeded.service` and verifies that `snapd.service`/`snapd.socket` stay enabled. No per-run half. |

Rationale and evidence: [`box-model.md` §2](box-model.md#cloud-init-and-snapd-trimmed-off-the-boot-path-1250).
Both roles are in each Ubuntu bake's manifest-hash closure, so introducing them
(and any later edit) flips all five Ubuntu boxes to BUILD. The RHEL boxes are
unaffected.

## Per-box bake sets

### `mq-rdqm-rhel9` → `ansible/bake-mq-rdqm.yml`

| Role | In bake | Notes |
|------|---------|-------|
| `acl` (dnf pkg) | ✅ full | Unprivileged-become prereq (from `_rdqm-cluster-ha.yml`). |
| `rdqm-install` | ✅ full | MQ server set + SDK + samples + web, plus bundled LINBIT/DRBD + Pacemaker + MQSeriesRDQM, one pre-QM pass. Its last step seeds the #282/#569 journald `DiagnosticMessages` drop-in (journald-only on RDQM) — the diagnostic default baked into the image. |
| `node-exporter` | ✅ full | All-install (static config); no split needed. |
| `alloy` | ✅ install half | Binary + unit baked; `config.alloy` (per-QM-node mqweb-tail, loki endpoint) + start stay per-run. |

### `obs-ubuntu2404` → `ansible/bake-obs.yml`

Since the observability-consolidation epic (`logical-minds-foundry/.github#267`), this
box bakes the **whole** observability platform: the metrics stack
(Prometheus/Grafana/Loki + the `mq_prometheus` exporter) **and** the log-search stack
(OpenSearch + Dashboards + Data Prepper), folded in from the retired
`logsearch-ubuntu2404` box (#1178/#1179). All are baked **inert** (install half only):
binaries/packages + static config + inert units are baked; service enable+start and every
per-run render stay in `site-obs.yml`.

| Role | In bake | Notes |
|------|---------|-------|
| `node-exporter` | ✅ full | All-install. |
| `alloy` | ✅ install half | As above. On obs, alloy also carries the fleet-wide OpenSearch fan-out, gated per-run by `group_vars/all/logsearch.yml`. |
| `loki` | ✅ full | All-install (loki binary + logcli + static config). |
| `prometheus` | ✅ install half | Binary + **static** `prometheus.yml` + recording rules + unit baked; the topology-rendered `targets/{node,ibmmq}.json` (`mqlab obs targets` → `build/work/…`) stay per-run. |
| `grafana` | ✅ install half | Package + **static** datasource + dashboard-provider + service baked; the rendered dashboard tree (`build/work/grafana/dashboards/`) and the **admin-password secret** env drop-in stay per-run. Its OpenSearch datasource now points at `localhost` (co-located, #1179). |
| `mq-exporter` (`build`) | ✅ build entry | Installs the **prebuilt** `mq_prometheus` (built once in the Go container against the MQ SDK, copied in — #1065; no in-guest Go toolchain). **Ubuntu-only** (pulls MQ via the Ubuntu-deb `mq-install` for the runtime libs) → it lives here, on the obs/probe box, not the RHEL rdqm box. Per-instance units + TLS CCDT/keystore stay per-run (`instance` entry, gated by `mq_exporter_tls`). |
| `opensearch` | ✅ install half | sysctl + user + binary + data/repo dirs + static `opensearch.yml` + inert unit baked; service enable+start + `logs` index template (`number_of_replicas:0`) + snapshot repo + restore-on-bring-up stay per-run. |
| `opensearch-dashboards` | ✅ install half | User + binary + static config + security-plugin removal + inert unit baked; service enable+start + `/api/status` wait + the default `logs-*` index pattern stay per-run. |
| `data-prepper` | ✅ install half | JDK-bundled binary + data dir + OpenSearch-sink DLQ dir + static config + pipeline templates + inert unit baked, plus the DLQ logrotate rule and its inert hourly rotate timer (#1239; see [`data-prepper-dlq.md`](../reference/data-prepper-dlq.md)); the Alloy→OTLP→Data-Prepper→OpenSearch connector's service enable+start + readiness wait, and the DLQ rotate timer enable, stay per-run (#939, #1239). Its OpenSearch sink is `localhost:9200` (co-located). |

### `infra-ubuntu2404` → `ansible/bake-infra.yml`

| Role | In bake | Notes |
|------|---------|-------|
| `bind-dns` | ✅ install half | The one real SPLIT: bind9 package + `/etc/bind/zones` scaffolding baked; zone data + per-host `named.conf.{options,local}` (keyed by `inventory_hostname`, rendered by `mqlab dns render`) + service start stay per-run. |
| `node-exporter` | ✅ full | All-install. |
| `alloy` | ✅ install half | As above. |

### `mq-ubuntu2404` → `ansible/bake-mq-ubuntu.yml` (#659)

The Ubuntu peer of `bake-mq-rdqm.yml`, for the three shared Ubuntu MQ commons
(`svc-sim`, `app-client`, `mon-probe`), repointed to this box so a bootstrap skips
their ~15–20 min of per-run installs.

| Role | In bake | Notes |
|------|---------|-------|
| `acl` (apt pkg) | ✅ full | Unprivileged-become prereq for `site-distributed-shared.yml`. Baked, not fetched per-run — kills the #659 acl stall. |
| `mq-install` | ✅ full | The Ubuntu MQ product via the deb path — the **server set includes client + SDK + samples**, so one install serves svc (server + QM), app (client + SDK for pymqi), and the exporter's cgo SDK. No QM created. |
| `mq-inter-qm` (`tasks_from: install`) | ✅ install half | The svc responder's pymqi venv (`/var/mqm/rvenv`, owned by `mqm`; #1227): venv create + the PyPI pymqi install (unpinned, as before) + an import check. Must follow `mq-install` (pymqi compiles against the MQ SDK; the half asserts `cmqc.h` first). The channel MQSC, the SVC QM / responder keystores and the `mq-svc-responder@` service stay per-run in `main.yml`, which re-imports this half as a near no-op (`creates:` guard + pip's satisfied check). Pulling in the role also folds `mq-inter-qm` (and `pki-distribute`, which its `main.yml` includes) into this box's manifest hash: deliberate over-inclusion, so an edit to either rebakes this box. |
| `mq-exporter` (`build`) | ✅ build entry | Installs the **prebuilt** `mq_prometheus` (built once in the Go container, copied in — #1065; no in-guest Go toolchain, which used to auto-download a full Go toolchain and overflow this guest). Its `mq-install` include (runtime libs) is an idempotent no-op here. Per-instance units + TLS CCDT/keystore stay per-run (`instance` entry, gated by `mq_exporter_tls`). |
| `node-exporter` | ✅ full | All-install (static config), left **enabled** (#642 benign exception). |
| `alloy` | ✅ install half | Binary + unit baked (inert); `config.alloy` + start stay per-run. |

### `mq-nativeha-rhel9` → `ansible/bake-nativeha-rhel.yml` (#667, epic .github#88)

The RHEL native-HA peer of `bake-mq-rdqm.yml`, for the six `nha-rhel-crr-*` nodes
(`nha-rhel-crr-a1..3`, `nha-rhel-crr-b1..3`) repointed to this box so a bootstrap skips
their per-run base-MQ install. Native HA replicates in the raft log, so — unlike
the RDQM box — **no DRBD/RDQM and no kernel pin** are baked.

| Role | In bake | Notes |
|------|---------|-------|
| `acl` (dnf pkg) | ✅ full | Unprivileged-become prereq for `site-nativeha.yml` — the RHEL-side #659 acl-stall kill. |
| `mq-nativeha` (`tasks_from: install-RedHat`) | ✅ install half | Base IBM MQ (server + client + SDK + samples + web, **no** RDQM) via the native-HA OS adapter's **install body only**. `main.yml`'s `crtmqm` / peer-set / `mqmonitor@` **formation stays per-run** (see "Stays configure" below). The per-run `install-RedHat.yml` skip-if-baked-guards the tar copy/unpack on a stat of `/opt/mqm/inc/cmqc.h` (#659), so the media is copied once — here. |
| `node-exporter` | ✅ full | All-install (static config), left **enabled** (#642 benign exception). No `rdqm.service` daemon exists on a native-HA box. |
| `alloy` | ✅ install half | Binary + unit baked (inert); `config.alloy` + start stay per-run. |

Per-run skips are enforced by #648-style **skip-if-baked** guards rather than a
role split: `mq-install`/`mq-client` gate the ~700 MB tar copy + unpack on a stat
of the installed `/opt/mqm/inc/cmqc.h`, and `alloy`'s install half gates the
GitHub download on a stat of `/usr/local/bin/alloy`. The `mq_prometheus` install
copies the prebuilt binary and skips on a stat of the baked binary (#1065). So the
repointed commons run only
per-run config + service start (QMs/channels, the app-requester, exporter
instances, `config.alloy`), never the baked installs.

### `mq-nativeha-ubuntu` → `ansible/bake-nativeha-ubuntu.yml` (#103 T6, epic .github#103)

The Ubuntu OS-as-only-variable peer of `bake-nativeha-rhel.yml`, for the six
`nha-ubuntu-*` nodes (`nha-ubuntu-a1..3`, `nha-ubuntu-b1..3`) repointed to this box
so a bootstrap skips their per-run base-MQ install. Native HA replicates in the
raft log, so — like the RHEL native-HA box — **no DRBD/RDQM and no kernel pin** are
baked. Host-resolved: the box bakes natively per host (arm64 or x86), so its guest
arch is not pinned.

| Role | In bake | Notes |
|------|---------|-------|
| `acl` (apt pkg) | ✅ full | Unprivileged-become prereq for `site-nativeha-ubuntu.yml` — the Ubuntu-side #659 acl-stall kill. |
| `mq-nativeha` (`tasks_from: install-Debian`) | ✅ install half | Base IBM MQ (server + client + SDK + samples debs, **no** RDQM) via the native-HA OS adapter's **install body only** — the Ubuntu peer of the RHEL box's `install-RedHat`. `main.yml`'s `crtmqm` / peer-set / `mqmonitor@` **formation stays per-run** (see "Stays configure" below). The per-run `install-Debian.yml` skip-if-baked-guards the tar copy/unpack on a stat of `/opt/mqm/inc/cmqc.h` (#103 T6), so the host-arch Ubuntu MQ media is copied once — here. |
| `node-exporter` | ✅ full | All-install (static config), left **enabled** (#642 benign exception). No `rdqm.service` daemon exists on a native-HA box. |
| `alloy` | ✅ install half | Binary + unit baked (inert); `config.alloy` + start stay per-run. |

### `pcmk-ubuntu` → `ansible/bake-pcmk-ubuntu.yml` (#103 T7, epic .github#103)

The Pacemaker/SAN peer, for the six Pacemaker **cluster** nodes (`pcmk-a1..3`,
`pcmk-b1..3`) repointed to this box so a bootstrap skips their per-run base-MQ
install. It bakes the MQ product install the cluster nodes run via `mq-install`
(the same role `_pcmk-cluster-ha.yml` drives on `pcmk_a`/`pcmk_b`). It does **not**
touch the SAN targets (`san-a`/`san-b`) — they carry no IBM-MQ payload, so they
stay host-resolved on the base Ubuntu box (D8, deferred to the SAN-hosts epic
#108). Host-resolved: baked natively per host (arm64 or x86), guest arch not pinned.

| Role | In bake | Notes |
|------|---------|-------|
| `acl` (apt pkg) | ✅ full | Unprivileged-become prereq for `site-pcmk.yml` — the pcmk-side #659 acl-stall kill. |
| `mq-install` | ✅ full | The Ubuntu MQ product via the deb path (server + client + SDK + samples; unpack debs, licence, `setmqinst`, ulimits) — the way the Pacemaker cluster nodes install MQ in `_pcmk-cluster-ha.yml` (`roles: [mq-install]`). **No** RDQM/DRBD, **no** QM created; `crtmqm` / resource-group / cluster formation stay per-run (`mq-pcmk-qmgr`). Already carries the stat-of-`cmqc.h` skip-if-baked guard (#648/#659) and the arch-derived tarball, so the ~700 MB tar copy/unpack + install runs once — here. |
| `node-exporter` | ✅ full | All-install (static config), left **enabled** (#642 benign exception). No `rdqm.service` daemon exists on a Pacemaker box. |
| `alloy` | ✅ install half | Binary + unit baked (inert); `config.alloy` + start stay per-run. |

> The log-search stack (`opensearch`, `opensearch-dashboards`, `data-prepper`)
> formerly baked into a standalone `logsearch-ubuntu2404` box (`ansible/bake-logsearch.yml`,
> #830). The observability-consolidation epic (`logical-minds-foundry/.github#267`)
> folded those roles into `obs-ubuntu2404` (#1178) and retired the standalone box and its
> bake playbook (#1179) — see the `obs-ubuntu2404` table above.

## Every Ubuntu box: apt auto-updates disabled at bake (#1225)

Each Ubuntu bake playbook (`bake-obs.yml`, `bake-infra.yml`, `bake-mq-ubuntu.yml`,
`bake-nativeha-ubuntu.yml`, `bake-pcmk-ubuntu.yml`) opens with its own play that
runs the `apt-autoupdate-off` role. It runs first so the bake's own apt work never
races an auto-update run. The RHEL bakes do not include it: the role is
apt-specific and asserts a Debian-family host.

| Role | In bake | Notes |
|------|---------|-------|
| `apt-autoupdate-off` | ✅ full | Masks `apt-daily{,-upgrade}.timer`, waits out any run already in flight (never kills it mid-dpkg), then masks `apt-daily{,-upgrade}.service` and `unattended-upgrades.service`. Drops `/etc/apt/apt.conf.d/99lab-no-auto-upgrades`, which zeroes every `APT::Periodic::*` knob. Ends with a fail-loud check that every unit reads `masked`. No per-run half. |

Rationale: the weekly cold rebuild plus the staleness gate is the update path,
so the boxes carry no in-guest updater. See [`box-model.md` §5](box-model.md#5-os-currency-comes-from-rebuilding-the-box).
The role is in each Ubuntu bake's manifest-hash closure, so introducing it (and
any later edit to it) flips all five Ubuntu boxes to BUILD. The RHEL boxes are
unaffected.

## Stays configure (per-run) — never baked

The whole configure surface: queue-manager and cluster creation
(`mq-qmgr`, `mq-pcmk-qmgr`, `mq-nativeha`, `rdqm-ha`, `pcmk-cluster`,
`pcmk-stonith`); RDQM/HA/DR state and reconcile (`rdqm-active-node`, `rdqm-state`,
`cluster-state`, `nativeha-state`, `host-resolver`, `net-reach`);
all PKI/TLS (`lab-pki`, `pki-distribute`, `rdqm-replication-tls`, `rdqm-app-tls`,
`rdqm-ssh-access`); messaging config (`mq-inter-qm` — bar its pymqi-venv install half, baked into `mq-ubuntu2404` (#1227) — `mq-event-monitor`,
`app-requester`, `mq-diag-logging` per-QM `qmini`); the SAN/iSCSI substrate
(`drbd-san`, `iscsi-target`, `iscsi-initiator`); and `mqweb` (per-QM REST config +
injected `mqweb_admin_password`, so it is configure even though the mqweb *server*
binary is installed by `rdqm-install`).

> **Note on `mq-nativeha`:** only its *formation* half (`crtmqm` / peer-set /
> `mqmonitor@` linking, in `main.yml`) is per-run. Its `install-RedHat` product
> install is **baked** into `mq-nativeha-rhel9` (bake-set above), exactly as
> `rdqm-install`'s product install is baked into `mq-rdqm-rhel9`. The role's
> `install-Debian` half is now **baked** into `mq-nativeha-ubuntu` too (#103 T6,
> bake-set above), so on both native-HA arms only the per-run formation remains.

## Deviations from the epic plan's classification head-start

Verified by reading each role's `tasks/main.yml`:

1. **`mq-install` is NOT in `bake-mq-rdqm`.** It is Ubuntu-only (copies the
   UbuntuLinux deb tar, `apt-get install`). The RHEL rdqm box installs MQ via
   `rdqm-install` (LinuxX64 rpm tar). Listing both would break on RHEL.
2. **`mq-exporter` build is in `bake-obs`, not `bake-mq-rdqm`.** It is Ubuntu-only
   (includes the Ubuntu-deb `mq-install` for the runtime libs; the binary is prebuilt
   in the Go container — #1065) and the exporter runs on the obs/probe box.
3. **`node-exporter` and `loki` are baked as full roles** (not split): their config
   is static, so the whole role is effectively install.

## Naming note: `rdqm-install` = "MQ product **+** RDQM stack"

`rdqm-install` installs the **entire MQ product** (server/SDK/samples/web) *plus*
the bundled DRBD/Pacemaker/MQSeriesRDQM in one pre-QM pass — it is not just the
RDQM add-on. The name is nonetheless accurate: it is the install path **for an
RDQM node** (which requires MQ), and it is used *only* by the RDQM plays. **Native
HA does not use it** — `nha-rhel-crr` installs via `mq-nativeha/tasks/install-RedHat.yml`
(its own MQ-product install), so no DRBD is ever baked into a native-HA image.

The real smell this surfaces is duplication, not misnaming: the MQ-product install
(fetch the arch-correct tar → `mqlicense -accept` → install) is copied across
`rdqm-install`, `mq-nativeha` (RedHat + Debian), `mq-install`, and `mq-client`,
with no shared building block. Baking makes it visible — the product install is now
baked into **five** boxes: `mq-rdqm-rhel9` (via `rdqm-install`), `mq-nativeha-rhel9`
and `mq-nativeha-ubuntu` (via `mq-nativeha` `install-RedHat`/`install-Debian`), and
`mq-ubuntu2404` and `pcmk-ubuntu` (via `mq-install`). Now that the arch-native
box-building epic (`logical-minds-foundry/.github#103`) has baked the remaining
Ubuntu arms, that duplication is fully in the open across every box — so extracting
a shared `mq-product-install` `tasks_from` is the live cleanup this surfaces, no
longer a future one.

## Full-apply proof is deferred

Per the epic plan, full proof of correctness (MQ media + install DVD present, a real
box build) rides the later fat-box build task. #602's acceptance is: bake playbooks
well-formed, disjoint from configure, the one coupling split (`bind-dns`) extracted,
`ansible-playbook --syntax-check` green, and `vrg-validate` green.
