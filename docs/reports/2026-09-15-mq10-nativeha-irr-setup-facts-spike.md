# IBM MQ 10.0 Native HA IRR Setup Facts — Spike Findings

**Date:** 2026-09-15
**Issue:** #1103 (epic logical-minds-foundry/.github#227 — dual-mechanism Native HA:
CRR async vs IRR strict-sync, latency-tunable)
**Task:** Plan Task 1 (investigation spike). Pin the authoritative 10.0 Native HA
**IRR** (In-Region Replication) *setup/config* facts — the exact config delta vs
CRR, the replication port, the strict-sync start-ordering constraint, the minimum
MQ version, and Developer-edition availability — so Tasks 3 (role `replication_mode`),
4 (playbook parameterization), and 5 (IRR stack) build on verified behaviour, not
assumptions. Gates Tasks 3/4/5.

**Status:** DECIDED — every fact below is cited to an IBM Docs 10.0.x page actually
cached under `build/refs/ibm-docs/ibm-mq/10.0.x/` on 2026-09-15 (product code
`SSYHRD_10.0.0`). No fact is asserted from memory or a search snippet. Where IBM's
docs are silent, this note says so plainly rather than guessing.

**Verdict:** GO on the facts — all five spike questions are answered from primary
sources. **But one finding materially contradicts the plan's downstream assumptions
and must be reconciled before Tasks 3/4/5 build:** IBM's Native HA **IRR is a
two-node, single-instance-per-group topology, not a pair of three-node HA groups**,
and the CRR↔IRR config delta is **two** `NativeHARecoveryGroup` keys
(`SyncReplication=Yes` **plus** `SyncConsistency`), not the single `SyncConsistency`
line the plan's best-known form assumed. See §7 (Findings) and §8 (Implications).

---

## 1. Cached authoritative sources

All cached under `build/refs/ibm-docs/ibm-mq/10.0.x/<slug>/` (gitignored — IBM
content is not redistributed). Cite `content.txt`; the `source_url` is in
`meta.json`. Newly cached by this spike unless marked *(pre-existing, from the CRR
upgrade spike #1070)*.

| Slug | Topic (IBM title) | `source_url` |
|---|---|---|
| `recovery-native-ha-irr` | Native HA IRR (overview: topology, roles, sync, rebase, naming) | `?topic=recovery-native-ha-irr` |
| `irr-creating-native-ha-configuration` | **Creating a Native HA IRR configuration** (the setup procedure + worked qm.ini) | `?topic=irr-creating-native-ha-configuration` |
| `chadr-comparison-native-ha-crr-native-ha-irr-solutions` | Comparison of Native HA CRR and Native HA IRR solutions | `?topic=chadr-comparison-native-ha-crr-native-ha-irr-solutions` |
| `qmini-nativeharecoverygroup-stanza-file` | **NativeHARecoveryGroup stanza of the qm.ini file** (where `SyncReplication`/`SyncConsistency` live) | `?topic=qmini-nativeharecoverygroup-stanza-file` |
| `qmini-nativehalocalinstance-stanza-file` | NativeHALocalInstance stanza of the qm.ini file (`GroupLocalAddress` / port default) | `?topic=qmini-nativehalocalinstance-stanza-file` |
| `qmini-nativehainstance-stanza-file` | NativeHAInstance stanza of the qm.ini file (must be **absent** for IRR) | `?topic=qmini-nativehainstance-stanza-file` |
| `crr-creating-native-ha-solution` | Creating a Native HA CRR solution (the CRR baseline for the delta) | `?topic=crr-creating-native-ha-solution` |
| `recovery-native-ha` | Native HA (overview — the base 3-instance HA group) | `?topic=recovery-native-ha` |
| `irr-upgrading-native-ha-configurations` *(pre-existing)* | Upgrading a Native HA IRR configuration | `?topic=irr-upgrading-native-ha-configurations` |

All `source_url`s share the prefix `https://www.ibm.com/docs/en/ibm-mq/10.0.x`.

> **Method note.** `tools/ibm_doc_cache.py` resolves a `?topic=<slug>` URL to IBM's
> content API. The IRR *setup* slugs were not known up front; they were recovered by
> following IBM's own redirect from the legacy `SSYHRD_10.0.0/configure/<file>.html`
> link targets (found in the already-cached CRR pages' link graph) to their canonical
> `?topic=` form, then fetched normally through the tool. No tool change was needed
> (the #1070 header fix still holds).

---

## 2. Terminology pinned (IRR vs CRR vs base Native HA)

From `recovery-native-ha`, `recovery-native-ha-irr`, and
`chadr-comparison-native-ha-crr-native-ha-irr-solutions` (`content.txt`):

- **Native HA (base)** — a single high-availability queue manager made of **three
  log-replicating instances** (one active), giving automatic failover *within* the
  group. Instances are wired by the **`NativeHAInstance`** stanzas.
- **Native HA CRR (Cross-Region Replication)** — "comprises **two three-node** high
  availability solutions", one Live and one Recovery, "any geographical distance
  apart", using **asynchronous** replication. In-group auto-failover on each side
  **plus** manual cross-site switchover/failover for DR. *(This is the lab's current
  arm.)*
- **Native HA IRR (In-Region Replication)** — "comprises **two single-node**
  systems", one Live and one Recovery, within ~100 mi / 160 km, **< 5 ms** network
  latency, using **synchronous** replication. **No** in-group HA, **no** automatic
  failover — manual switchover only. Intended purely as a **DR** solution with an
  RPO of 0. Available on **Linux (VMs and bare metal)**; **not supported in
  containers**.

The crucial contrast for this epic: **CRR pairs two 3-instance groups (6 nodes);
IRR pairs two 1-instance groups (2 nodes).** They are *not* the same topology with a
flipped replication flag.

---

## 3. Q1 — Exact IRR config delta vs CRR

**Answer: the IRR delta is two keys added to the `NativeHARecoveryGroup` stanza —
`SyncReplication=Yes` (the actual synchronous-replication enabler) and, optionally,
`SyncConsistency=Strict` (the default consistency once sync is on) — combined with
the *removal* of the `NativeHAInstance` stanzas that CRR/base-NHA require.**

### 3.1 `SyncReplication` — the real enabler (was missing from the plan's assumption)

From `qmini-nativeharecoverygroup-stanza-file/content.txt`:

> "**SyncReplication** — Specifies whether a Native HA configuration uses synchronous
> (`Yes`) or asynchronous (`No`) replication to communicate with the recovery group.
> The default is `No`. **You can only set this option to `Yes` if you are specifying a
> Native HA IRR configuration.** The Native HA CRR configuration always uses
> asynchronous replication."

So the line that makes replication synchronous at all is **`SyncReplication=Yes`**,
and it is IRR-exclusive. **`SyncConsistency` has no effect unless `SyncReplication=Yes`.**

### 3.2 `SyncConsistency` — the consistency mode (belongs in `NativeHARecoveryGroup`)

From the same stanza reference:

> "**SyncConsistency** — If `SyncReplication` is set to `Yes` (which is only available
> for the Native HA IRR configurations) you can use `SyncConsistency` to specify
> behavior … Set to `Strict` … set to `Eventual` … **The default is `Strict`**, which
> indicates that both Live and Recovery groups must be connected and in-sync before a
> queue manager can start and progress new log writes. If set to `Eventual`, the Live
> group makes a single attempt to connect … and starts to progress log writes without
> waiting for the acknowledgement from the Recovery group."

Confirmed both by `recovery-native-ha-irr` ("`SyncReplication=Yes` in the
`NativeHARecoveryGroup` stanza … The default behavior is the equivalent of setting
`SyncConsistency` to `Strict` in the `NativeHARecoveryGroup` stanza") and by
`irr-upgrading-native-ha-configurations` ("set `SyncConsistency=Eventual` in the
qm.ini `NativeHARecoveryGroup` definition").

**Placement is unambiguous: both keys go in `NativeHARecoveryGroup`, not
`NativeHALocalInstance`.** Values: `SyncReplication` ∈ {`Yes`,`No`} (default `No`);
`SyncConsistency` ∈ {`Strict`,`Eventual`} (default `Strict`).

### 3.3 The `NativeHAInstance` stanza must be **absent** for IRR

From `qmini-nativehainstance-stanza-file/content.txt`:

> "The `NativeHAInstance` stanza is required for Native HA and Native HA CRR
> configurations. **It is not required for Native HA IRR configurations, and supplying
> it causes the creation of a Native HA IRR configuration to fail.**"

And from `irr-creating-native-ha-configuration`: "(You do not add `NativeHAInstance`
stanza when defining a Native HA IRR configuration.)"

### 3.4 The confirmed IRR qm.ini form (verbatim from IBM's creation procedure)

From `irr-creating-native-ha-configuration/content.txt`, the Live-node stanza (the
Recovery node is the mirror image, pointing back at the Live address):

```ini
NativeHALocalInstance:
   Name=alpha
   GroupName=datacenter1
   KeyRepository=/var/mqm/qmgrs/MYQMGR/ssl/keystore
   CertificateLabel=MyCertificate
   GroupRole=Live
   GroupCipherSpec=ANY_TLS12
   GroupLocalAddress=(4454)

NativeHARecoveryGroup:
   GroupName=datacenter2
   ReplicationAddress=beta.example.com(4454)
   SyncReplication=Yes
```

Note IBM's example does **not** spell out `SyncConsistency` because `Strict` is the
default; the lab should set it **explicitly** for clarity and to make the async-vs-strict
knob self-documenting.

**Delta relative to the lab's current CRR role (`ansible/roles/mq-nativeha/tasks/crr.yml`),
for the `strict` branch:**
- Add `SyncReplication=Yes` to the `NativeHARecoveryGroup` block. *(mandatory)*
- Add `SyncConsistency=Strict` to the same block. *(optional — the default — but set it explicitly)*
- Do **not** emit `NativeHAInstance` stanzas for the IRR stack. *(the CRR/base path emits them for the 3 in-group members; the IRR single-instance group has no such members — see §8)*

> **Correction to plan Task 3 Step 2.** The plan's "best-known form" appends only
> `SyncConsistency=Strict`. That line alone is inert: without `SyncReplication=Yes`
> the config stays asynchronous (CRR behaviour). Task 3 must gate **both** keys on
> `replication_mode == 'strict'`.

---

## 4. Q2 — Replication port

**Answer: IRR uses the *same* configurable Native HA replication-port mechanism as
CRR; there is no fixed, different IRR port. The default is 9415, which the lab
already uses, so IRR can keep 9415.**

From `qmini-nativehalocalinstance-stanza-file/content.txt`:

> "**GroupLocalAddress** — Specifies the replication network interface and port to
> advertise for the group. **If not specified, defaults to all network interfaces and
> port 9415.**"

The port is set on both sides — `GroupLocalAddress=(<port>)` in `NativeHALocalInstance`
and the `(<port>)` suffix on `ReplicationAddress` in `NativeHARecoveryGroup`. IBM's
IRR worked example happens to use `4454` (and the base-NHA `NativeHAInstance` example
uses `4444`), but those are illustrative custom values, not IRR-specific requirements.

The lab already pins `GroupLocalAddress=(9415)` / `ReplicationAddress=…(9415)` for CRR
(e.g. `ansible/site-nativeha-spike-crr.yml`, and the `crr.yml` role template). The IRR
stack runs on its own nodes as its own queue manager, so **9415 can be reused** with no
conflict. The only caveat IBM notes: "**If you have several queue managers in your
Native HA IRR configurations, each would need a different replication port** (although
the two instances of the queue manager would use the same port)" — irrelevant to the
lab, which runs one IRR queue manager on dedicated nodes
(`irr-creating-native-ha-configuration`).

---

## 5. Q3 — Strict-sync start/stop ordering constraint

**Answer: Yes. Under `SyncConsistency=Strict` (the default), a Live instance cannot
complete startup until the Recovery instance is up, and a running Live instance
*abdicates* (forcibly disconnecting applications) if it loses the Recovery instance for
60 s. The lab's existing Recovery-first start ordering already satisfies the startup
constraint; the runtime abdication has direct consequences for the latency experiment
(Task 8).**

### 5.1 Startup blocks until Recovery is present

From `irr-creating-native-ha-configuration/content.txt`, starting the Live node:

> `strmqm MYQMGR` → "Queue manager startup is waiting to synchronize log data with the
> 'datacenter2' recovery group. … **The waiting message continues until the Recovery
> group instance is started.**"

### 5.2 Runtime: Strict Live abdicates if Recovery is lost

From `recovery-native-ha-irr/content.txt` (Synchronization consistency):

> "By default, data consistency is prioritized and both Live and Recovery groups must
> be connected and in-sync before a queue manager can start and progress new log
> writes. **The Live group active instance abdicates if it is unable to reconnect to
> the Recovery group within 60 seconds. The abdication forcibly disconnects
> applications.**"

Corroborated by `irr-upgrading-native-ha-configurations`: "If your configuration has
the default setting of `SyncConsistency=Strict`, **the instance on the Live system
will stop if it cannot replicate log data to the Recovery system.**"

### 5.3 Implication for `nha_start` ordering

The lab already brings the Recovery group up **first** (the role's `nha_start: false`
gate on the recovery group, started only after `crr.yml` sets `GroupRole=Recovery`;
see `ansible/roles/mq-nativeha/tasks/main.yml` and `_nativeha-dr-replication.yml`
step 5 — "restart Recovery-first"). **That ordering is sufficient for strict IRR** —
Recovery-up-first means the Live instance's startup sync completes immediately rather
than hanging on the waiting message. No new *start* ordering is required.

The runtime abdication is a **new operational fact for Task 8**: injecting WAN latency
or a partition on the cross-region plane against a *Strict* Live can trip the 60 s
abdication and forcibly disconnect the benchmark client (an availability event, not
merely a throughput dip). The sweep harness must treat abdication/disconnect as a
first-class outcome, and the `Eventual` mode is the contrast that keeps running. This
is exactly the sync-vs-async cost the epic sets out to quantify.

---

## 6. Q4 — Minimum MQ version for IRR strict-sync

**Answer: IRR / synchronous replication is a Native HA feature of MQ 10.0; it is
absent from 9.4.x. The strict-sync floor is therefore 10.0.0.0 — above the role's
current 9.4.4 Native HA floor.** (Data below; the "10.0.0.0" floor is judgment drawn
from that data — IBM does not print a single "minimum version N" sentence in the
cached setup pages.)

**Data (checked 2026-09-15):**
- The `?topic=irr-creating-native-ha-configuration` and `?topic=recovery-native-ha-irr`
  slugs **do not resolve in the `ibm-mq/9.4.x` doc set** (the content API returns no
  content path for them at 9.4.x) — the IRR setup/overview topics exist only at 10.0.x.
- The **9.4.0** `NativeHARecoveryGroup` stanza reference
  (`SSFKSJ_9.4.0/configure/NativeHARecoveryGroup_stanza.html`, fetched for comparison)
  contains **no** mention of `SyncReplication`, `SyncConsistency`, `IRR`, or
  `in-region` — only `cross-region`. Those keys are new at 10.0.x.

**Judgment:** IRR strict-sync requires **MQ 10.0.0.0** (the lab's current pin,
`lab/mq-version = 10.0.0.0`). The lab is already at that level, so the pin satisfies
the floor. However, the role's 9.4.4 assertion in
`ansible/roles/mq-nativeha/tasks/main.yml` would **not** catch a strict-mode
misconfiguration on a sub-10.0 level. Per plan Task 3 Step 4, gate the floor on mode:
`replication_mode == 'strict'` ⇒ assert `>= 10.0.0.0`; `async` keeps the `>= 9.4.4`
floor unchanged.

---

## 7. Q5 — Developer-edition availability

**Answer: The cached docs do not gate IRR by MQ edition. IRR is part of the same
Native HA family that already runs on the lab's IBM MQ Advanced for Developers
tarball (CRR is proven on it), so IRR is expected to be available on the Developer
edition. Confirm empirically at the Task 5 bring-up.** (Data: docs silent on edition;
the rest is judgment, low risk.)

**Data:** `recovery-native-ha-irr` and
`chadr-comparison-native-ha-crr-native-ha-irr-solutions` state only a **platform**
constraint — "available on **Linux** (virtual machines and bare metal)", "**not
supported in containers**" — and no edition/entitlement restriction. None of the
cached IRR pages mention "Advanced" or "Developers" as a gate.

**Judgment:** Native HA (CRR and base) already runs on the no-charge *IBM MQ Advanced
for Developers* media the lab installs (`install-RedHat.yml`), and IRR is the same
Native HA engine differentiated only by the `SyncReplication`/`SyncConsistency`
config and topology. So IRR should install and run on the same media. This mirrors
the plan's own low-risk assessment. Treat as confirmed-pending-bring-up, not a
sourced guarantee.

---

## 8. Findings summary (the answers, condensed)

| # | Question | Answer | Primary source |
|---|---|---|---|
| Q1 | `SyncConsistency` stanza + exact key/placement | `NativeHARecoveryGroup` stanza. **Delta is two keys:** `SyncReplication=Yes` (mandatory, IRR-only enabler) **+** `SyncConsistency=Strict` (default; set explicitly). Values: `SyncReplication`∈{Yes,No}/def No; `SyncConsistency`∈{Strict,Eventual}/def Strict. **`NativeHAInstance` stanzas must be absent.** | `qmini-nativeharecoverygroup-stanza-file`, `qmini-nativehainstance-stanza-file`, `irr-creating-native-ha-configuration` |
| Q2 | Same port 9415 or different? | Same configurable mechanism (`GroupLocalAddress`/`ReplicationAddress`); **default 9415**; no fixed different IRR port. Lab keeps 9415. | `qmini-nativehalocalinstance-stanza-file`, `irr-creating-native-ha-configuration` |
| Q3 | Strict-sync start/stop ordering? | Yes: Live startup **blocks until Recovery is up**; running Strict Live **abdicates after 60 s** without Recovery. Lab's Recovery-first ordering suffices; abdication is a Task-8 outcome. | `irr-creating-native-ha-configuration`, `recovery-native-ha-irr`, `irr-upgrading-native-ha-configurations` |
| Q4 | Min MQ version for IRR strict-sync? | **10.0.0.0** (absent from 9.4.x; keys absent from 9.4.0 stanza). Above the 9.4.4 role floor. Lab pin already 10.0.0.0. | `qmini-nativeharecoverygroup-stanza-file` (10.0 vs 9.4.0), 9.4.x topic non-existence |
| Q5 | Developer-edition availability? | Docs silent on edition (only "Linux, not containers"). Judgment: available on the Developer media (same Native HA family as CRR, which runs on it). Confirm at bring-up. | `recovery-native-ha-irr`, `chadr-comparison-native-ha-crr-native-ha-irr-solutions` |

---

## 9. Implications for the plan (Tasks 3, 4, 5) — reconcile before building

This spike surfaced **two facts that conflict with the plan/epic as written.** Task 1's
job is to record them, not to redesign the epic; the calls below are flagged for the
human / the downstream tasks.

1. **Topology conflict (biggest).** The plan's Task 5 models the IRR stack as **six**
   nodes — `nha-rhel-irr-a1..3` / `-b1..3`, with `nha_rhel_irr_a/b` as **three-node**
   groups — and the epic's framing is "two parallel stacks that differ **only** by
   names and a single `SyncConsistency` value (12 nodes total)". IBM's IRR is **two
   single-instance groups (2 nodes total)**: "two groups each containing **one
   instance**", "two single-node systems", "**no** high availability functions"
   (`recovery-native-ha-irr`, `chadr-comparison…`). An IRR group is **not** a 3-node
   Native HA group, and supplying the 3-node `NativeHAInstance` wiring **fails IRR
   creation** (`qmini-nativehainstance-stanza-file`). **Decision needed:** either
   (a) model the IRR stack faithfully as 2 nodes (1 Live + 1 Recovery) — which makes
   it genuinely "CRR minus in-group HA, plus strict sync", not a mirror of the 6-node
   CRR stack — or (b) if the epic specifically wants two *3-node HA groups* replicating
   synchronously, that is **not** what IBM calls IRR and is not supported by the IRR
   config path. Option (a) is the documented, supported design and shrinks the VM
   footprint (2 IRR nodes, not 6). This directly affects Task 5's `lab/topology.yaml`
   node/group additions and the "≥64 GiB / 12 nodes" prerequisite-gate sizing.

2. **Config delta is two keys, not one, and drops a stanza.** Task 3 must gate
   **`SyncReplication=Yes` + `SyncConsistency=Strict`** on `replication_mode == 'strict'`
   (not `SyncConsistency` alone — that line is inert without `SyncReplication=Yes`),
   and the IRR path must **not** emit `NativeHAInstance` stanzas. The "one conditional
   line in `crr.yml`" premise (plan Architecture / Task 3) understates the delta,
   though it is still a small, well-bounded change.

3. **Version floor gate is warranted (Task 3 Step 4).** Gate the role's floor: strict
   ⇒ `>= 10.0.0.0`; async keeps `>= 9.4.4`. The lab pin is already 10.0.0.0.

4. **Task 8 must treat Strict abdication as an outcome.** The 60 s Live abdication
   under injected WAN latency/partition is an availability event the sweep should
   capture, and the `Eventual` mode is the availability-prioritizing contrast.

None of these block *this* task (the facts are complete and cited). They are inputs the
downstream tasks — and the human owning the epic — need before Tasks 3/4/5 build.

---

## 10. Blockers / open items

- **No blocker for Task 1.** All five spike questions are answered from cached
  `ibm.com/docs` 10.0.x pages.
- **Escalated for the epic (not this task):** the IRR **topology** finding (§9.1) —
  IBM IRR is 2 single-instance nodes, not two 3-node groups — needs a design decision
  before Task 5 edits `lab/topology.yaml`. Recorded here; downstream tasks reference
  this report by path.
- **Not fetched (not needed):** the step-by-step *"Example: Deploying a simple Native
  HA IRR configuration on Linux"* walkthrough (a longer worked example) — the
  authoritative config facts came from the creation procedure and the stanza
  references above. If Task 5 wants the end-to-end cert+deploy transcript, cache
  `?topic=` for that example page at build time.
