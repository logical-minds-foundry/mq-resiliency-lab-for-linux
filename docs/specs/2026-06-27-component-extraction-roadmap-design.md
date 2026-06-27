# Component Extraction Roadmap — Design

- **Date:** 2026-06-27
- **Status:** Draft (for review)
- **Issue:** #368
- **Type:** Program-level roadmap / architecture (not a single-component build)
- **Related:** `2026-06-10-lab-observability-design.md`,
  `2026-06-11-observability-layered-dashboard-design.md`,
  `2026-06-12-lab-log-streaming-design.md`,
  `2026-06-14-cluster-drill-cockpit-design.md`

---

## 1. Purpose & scope

This is a **roadmap**, not a build spec for any one component. Its job is to
define, once and coherently, how we harvest reusable software out of the lab and
publish it as standalone open-source packages:

- the inventory of harvestable components and their boundaries;
- the source-of-truth model (the lab consumes what it publishes);
- the canonical, gated migration pattern every component follows;
- the build order;
- the family-wide conventions (naming, license, versioning, "done").

Each component then gets its **own** `spec → plan → build` cycle. This roadmap
is the contract that keeps those independent cycles coherent. It deliberately
does **not** design the internals of any component.

## 2. Background & motivation

Building the lab forced us to solve several non-trivial problems that have no
off-the-shelf answer and are valuable well beyond the lab:

- **MQ HA/DR posture as Prometheus metrics.** IBM ships `dspmq`/`rdqmstatus`
  text output and nothing that turns Native HA, CRR, or RDQM/DRBD state into
  time-series. We wrote collectors that do exactly this
  (`nativehastate.py`, `rdqmstate.py`, `clusterstate.py`).
- **The cockpit dashboards** (`clusterboard.py`, `dashboard.py`) that visualise
  that state.
- **systemd lifecycle units** for MQ queue managers and the web/REST server.
- **A log pipeline** (Alloy → Loki) plus the MQ JSON-diagnostic-logging setup
  that makes MQ error logs queryable.

The strategic goal: spend the next couple of years contributing these as
open-source products, then be able to walk away with the work standing on its
own — forkable by anyone, owned by no one. The collectors are the crown jewel;
the dashboards are a thin face over them; the dashboard's value is gated by the
collectors existing.

## 3. Non-goals

- **No off-the-shelf infrastructure is republished.** The `mq_prometheus`
  exporter, `node_exporter`, Prometheus, and Grafana are configured, not
  re-shipped. The observability bundle's docs explain how to wire them,
  including the **required scrape-side label contract** (see §9).
- **No IBM binaries, entitlements, keystores, or licenses** enter any published
  repo. The exporter is built-from-source against the MQ redist client. The
  repo's existing secrets policy carries forward unchanged.
- **Lab-fabric collectors are not extracted.** `netstate.py` and `net-reach`
  shell out to `virsh net-list` and key on `virbr-*` bridge names; they are
  libvirt-coupled scaffolding, not product. They stay in the lab.
- **No single-component internals are designed here.** Those belong to each
  component's own spec.

## 4. Component inventory & boundaries

Two extracted components, each its own repo, plus one explicit non-extraction.

| # | Component (working name) | Artifact | Source in lab today | Family dependency |
|---|---|---|---|---|
| 1 | `mq-resiliency-observability` | **RPM** (collectors + timers + MQ-lifecycle units + `render-dashboards` CLI) | `src/mqlab/{nativehastate,rdqmstate,clusterstate}.py`, `src/mqlab/{clusterboard,dashboard}.py`, collector `.service`/`.timer` templates, `mq-qmgr`/`mqweb` unit templates | none — it is the root; owns the contract internally |
| 2 | `mq-resiliency-logging` | **Ansible roles** (Galaxy) | `ansible/roles/{alloy,loki,mq-diag-logging}` | independent |
| — | Lab-fabric collectors | **not extracted** | `src/mqlab/netstate.py`, `ansible/roles/net-reach` | stays in lab |

### 4.1 `mq-resiliency-observability` (the bundle)

Collectors + dashboards live **together in one repo** because the metrics
contract forces them to change in lockstep: any contract change (rename a
metric, add a label) touches both the collector that *emits* it and the panel
that *queries* it. Co-locating them turns the contract from a cross-repo
versioned interface into a single-repo consistency invariant enforced by a test
(see §9). The dashboards are too thin to justify their own repo or release
cadence.

Contents:

- **State collectors** — `nativehastate.py`, `rdqmstate.py`, `clusterstate.py`
  (stdlib-only, timeout-bounded), emitting the `cluster_*` / `cluster_nha_*` /
  `cluster_rdqm_*` / `cluster_drbd_*` metric families to a node_exporter
  textfile dir.
- **systemd timer units** that drive the collectors on a fixed cadence
  (currently 5s).
- **MQ-lifecycle systemd units** — the queue-manager unit
  (`strmqm` / `endmqm -w`, `Type=forking`, `User=mqm`) and the mqweb/REST unit.
  Thin but genuinely hand-written; folded in here rather than given their own
  repo.
- **`render-dashboards` CLI** — the de-hardcoded dashboard generator. Takes a
  *profile* (QM name, node/group selectors, datasource UIDs, network planes)
  and emits Grafana dashboard JSON. This replaces today's hardcoded
  `lab_*_dashboard()` entry points.
- **The metrics contract** — the versioned schema of emitted metrics and the
  scrape-side labels the dashboards require (§9).

The single RPM serves multiple host roles: collectors + timers run on the MQ
nodes; `render-dashboards` runs on the obs/build host. One artifact with
multiple entry points is fine; whether it later splits into sub-RPMs is a
bundle-spec implementation detail, **not** decided here.

### 4.2 `mq-resiliency-logging`

The `alloy` and `loki` roles (generic log transport, no MQ specificity) plus the
`mq-diag-logging` role (the MQ-specific gem: the `mqs.ini`
`DiagnosticMessagesTemplate` that routes MQ diagnostics to syslog/journald, and
the journald rate-limit drop-in that prevents silent log loss). Published as
Ansible roles. Independent of the observability bundle.

### 4.3 De-hardcoding required for extraction

The dashboard generator currently bakes in lab specifics that must become
profile inputs: Ansible group selectors (`groups=~"pcmk_a|pcmk_b"`), QM resource
names (`mq_qm`, `NHARAPP`, `RDQMAPP`), host-name patterns (`nha-rhel-.*`),
libvirt device patterns (`virbr-*`), network-plane lists, datasource UIDs
(`prometheus`/`loki`), and the topology file path. The coupling is shallow and
mechanical — `render_cluster_dashboard(topo, arm, ds_uid)` already takes
parameters; the work is removing the wrappers that re-hardcode them. This is
detailed in the bundle's own spec, not here.

## 5. Source-of-truth & dependency architecture

**The lab dogfoods every published package.** After extraction the lab owns no
harvested *code* — only configuration and orchestration:

```
lab (pure consumer)
 ├─ installs → mq-resiliency-observability RPM   (collectors + dashboards + mq-systemd + contract)
 ├─ pulls    → mq-resiliency-logging roles (Ansible Galaxy)
 ├─ keeps    → fabric collectors (netstate / net-reach)   ← lab-only scaffolding
 └─ owns     → topology, obs orchestration (site-obs.yml), and the render *profile*
               (which QM names / nodes / datasource UIDs feed render-dashboards)
```

The split is **capability vs configuration**: the published packages provide the
capability; the lab provides the topology-specific configuration that points the
capability at this particular deployment. No intra-family cross-repo dependency
remains — the contract is internal to the observability bundle, and logging is
independent. The lab becomes a reference deployment that wires published parts
together.

## 6. The dogfooding migration pattern (canonical, gated)

Every component runs the same six-step gate. This *is* the reusable machinery
the build order proves once and then repeats:

1. **Extract & scrub.** Lift code into the new repo; strip lab-internal
   assumptions; parameterise (QM name, node list, datasource UIDs, textfile dir,
   Loki endpoint); define the public interface (README + contract).
2. **Build + CI.** RPM build / role packaging; CI runs the component's own
   tests, including the bundle's contract-consistency test (*every metric a
   panel queries is one a collector emits*).
3. **Publish.** Cut a `0.x` release to the public channel; semver from day one.
4. **Repoint the lab (dogfood).** The lab installs the *published* artifact
   instead of its local copy. This is the integration moment.
5. **Delete the in-lab copy.** Remove `src/mqlab/<module>` / the local role. The
   deletion is the proof that there is no silent fallback or drift.
6. **Verify against the cold-rebuild gate.** A full VM cold rebuild must pass
   one-pass. Lint-green ≠ extracted; the bring-up must prove the published
   artifact works end-to-end in a fresh lab.

A component is **"extracted"** only when all six steps are complete.

## 7. Build order — vertical slice first

1. **`mq-resiliency-observability`** — drive it through all six steps end-to-end
   before starting anything else. It is the highest-value component and exercises
   the full pipeline (RPM build, public publish, lab dogfood, cold-rebuild
   verify). Doing it first means the scary parts surface early, on the piece most
   worth the pain.
2. **`mq-resiliency-logging`** — repeat the now-known-good pattern. Lower risk,
   independent, mostly generic.

Rationale: dogfooding plus "collectors are the crown jewel" both point to
proving the machinery once on the bundle, then treating every later extraction
as a replay of a validated process. We carry at most one half-migrated component
at a time.

## 8. Conventions

- **Naming.** Provenance prefix `mq-resiliency-`; **no platform suffix** on
  components (they are generic across modern Unix — Linux, AIX, Solaris). The
  `-for-<platform>` suffix is reserved for artifacts that *bundle* OS-specific
  implementations — i.e. the lab itself stays `mq-resiliency-lab-for-linux`.
  Working component handles (`mq-resiliency-observability`,
  `mq-resiliency-logging`) are placeholders; **final names get a dedicated
  naming pass before any repo is created.**
- **License.** **MIT** for all extracted components. Chosen for lowest adoption
  friction and maximum corporate comfort; resale/proprietary reuse is explicitly
  acceptable. (The lab repo's own license is out of scope here.)
- **Versioning.** Semver per repo, starting `0.x`. A component earns `1.0` only
  after the lab successfully dogfoods it through a cold rebuild.
- **Definition of "extracted"** (per component): published release **+** lab
  installs the published artifact **+** in-lab copy deleted **+** cold rebuild
  passes one-pass **+** README documents the off-the-shelf wiring (including, for
  the observability bundle, the scrape-side label contract).
- **Secrets/licensing policy carries forward.** No IBM binaries, entitlements,
  or keystores in any published repo; exporter built-from-source.

## 9. The metrics contract (internal to the observability bundle)

The contract has two halves, both versioned inside the bundle:

1. **Emitted metrics** — the `cluster_*`, `cluster_nha_*`, `cluster_rdqm_*`,
   `cluster_drbd_*` families, their labels, and their value semantics, as
   produced by the collectors.
2. **Required scrape-side labels** — the labels the dashboards' PromQL/LogQL
   assume but that the collectors do *not* themselves emit: notably the `groups`
   label (from Ansible inventory groups) and host-name patterns. A stock
   Prometheus will not have these unless the operator configures the relabeling.

Because the dashboards live in the same repo as the collectors, half (1) is a
single-repo consistency invariant — a CI test asserts every metric a panel
queries is one a collector emits. Half (2) cannot be enforced by code on the
consumer's Prometheus; it is the **single biggest trap for an external user**
(dashboards render empty against a stock Prometheus). Mitigation: the bundle
ships a documented example scrape/relabel snippet alongside the dashboards, and
the README leads with the label requirement.

## 10. Risks & open questions

- **Scrape-side label contract (highest risk).** See §9. Must be documented
  prominently or external adopters get empty dashboards. Ship an example scrape
  config.
- **RPM publishing channel — open.** COPR vs self-hosted yum vs plain GitHub
  Releases. Decided when the observability bundle gets its own build spec.
- **Render-command host.** The bundle RPM installs collectors on MQ nodes but
  `render-dashboards` runs on the obs/build host. One RPM serving both is fine;
  whether it splits into sub-RPMs is a bundle-spec detail, not decided here.
- **Final naming pass + repo creation.** Working names are placeholders; the
  naming pass precedes any repo creation.
- **IBM redistribution constraints.** Exporter is built-from-source against the
  MQ redist client; the bundle ships no IBM artifacts and documents the build.

## 11. Appendix — source-file → component map

For the per-component specs to start from a known inventory.

**`mq-resiliency-observability`:**

- Collectors: `src/mqlab/nativehastate.py`, `src/mqlab/rdqmstate.py`,
  `src/mqlab/clusterstate.py`
- Dashboards: `src/mqlab/clusterboard.py`, `src/mqlab/dashboard.py`
- Collector units: `ansible/roles/{nativeha-state,rdqm-state,cluster-state}/templates/*.{service,timer}.j2`
  and their `tasks/main.yml`
- MQ-lifecycle units: `ansible/roles/mq-qmgr/templates/qm.service.j2`,
  `ansible/roles/mqweb/templates/mqweb.service.j2`

**`mq-resiliency-logging`:**

- `ansible/roles/alloy/` (incl. `templates/config.alloy.j2`)
- `ansible/roles/loki/` (incl. `templates/loki-config.yml.j2`)
- `ansible/roles/mq-diag-logging/` (incl. `tasks/system.yml`)

**Not extracted (stays in lab):**

- `src/mqlab/netstate.py`, `ansible/roles/net-reach/`
- Off-the-shelf provisioning: `ansible/roles/{prometheus,grafana,node-exporter,mq-exporter}/`
  (documented as wiring, not republished)
