# RDQM and Pacemaker/DRBD: strategic position and the lab's decision

> Scope: where IBM is taking **RDQM** relative to **Native HA**, how quickly IBM
> follows new **RHEL** releases with RDQM kernel modules, what the DRBD/Pacemaker
> ecosystem signals, how the lab justifies its **`pcmk-ubuntu`** stack, and the
> **recorded decision** on the future of the lab's two secondary HA stacks
> (`rdqm-rhel`, `pcmk-ubuntu`). This is the research write-up for
> [logical-minds-foundry/.github#309](https://github.com/logical-minds-foundry/.github/issues/309),
> a follow-on of the multi-version OS-axis epic
> [logical-minds-foundry/.github#280](https://github.com/logical-minds-foundry/.github/issues/280).
>
> Builds on [`mq10-rhel9-vs-rhel10-and-rdqm-support.md`](mq10-rhel9-vs-rhel10-and-rdqm-support.md)
> (#1095/#1096) and [`os-version-support-matrix.md`](os-version-support-matrix.md)
> (#1271). The support matrix is the authority for per-OS support cells; this
> note does not restate or override it. The RHEL 10 RDQM re-evaluation is
> tracked separately in
> [#1405](https://github.com/logical-minds-foundry/mq-resiliency-lab-for-linux/issues/1405).
>
> Convention: each claim is tagged **[data]** (what a cited source actually
> says) or **[judgment]** (our reasoning on top). Where no source was found we
> say **no data**. Quotes are verbatim.
>
> Retrieved: 2026-10-09 to 2026-10-10. IBM Docs pages were fetched with
> `python3 tools/ibm_doc_cache.py` (cite `content.txt`, with the `source_url`
> in `meta.json`, under `build/cache/refs/ibm-docs/`). IBM Support pages, the
> kmod JSON and the SPCR JSON were fetched with a browser user-agent.

## 1. Decision (recorded 2026-10-09)

The human decided the outcome on 2026-10-09. It corrects this task's original
premise, which was that RDQM's lag behind new RHEL releases signalled
deprecation and that the lab should "deprecate the trailing edge".

- **Native HA is the strategic HA technology** and the lab's primary
  requirement. The lab presents it as **the strategic choice for new HA
  applications**.
- **RDQM and Pacemaker/DRBD stay** as **completeness counter-options** under
  the lab's "Linux resiliency options" umbrella, **for as long as IBM supports
  them**. They are dropped only once IBM itself deems them non-strategic. In
  short: *we can't support what IBM doesn't, and we don't drop what IBM still
  supports.*
- **NAS-based multi-instance queue managers are deliberately out of scope.**
- **Outcome: keep as-is.** No deprecation epic, no "legacy" banner on the
  stacks, and no per-stack RHEL minor pins now.
- **RHEL 9 stays at 9.6 by decision**
  ([#1396](https://github.com/logical-minds-foundry/mq-resiliency-lab-for-linux/issues/1396)).
  That is revisitable: since
  [#1408](https://github.com/logical-minds-foundry/mq-resiliency-lab-for-linux/issues/1408)
  the lab selects the DRBD kmod with IBM's `modver` (kernel-family match)
  instead of its own exact-release glob, which was the only RDQM blocker on the
  RHEL 9.8 GA kernel.

§2–§5 are the evidence behind it; §6 lists the options that were considered.

## 2. IBM's stated direction: Native HA versus RDQM

### 2.1 What IBM's docs say now

- **[data]** MQ 10.0, *Configuring high availability and disaster recovery*
  ([source](https://www.ibm.com/docs/en/ibm-mq/10.0.x?topic=configuring-high-availability-disaster-recovery);
  cached `ibm-mq/10.0.x/configuring-high-availability-disaster-recovery/content.txt`):
  - "The primary high availability option is known as “native HA”."
  - "It is the preferred HA solution because it has no dependencies on external
    components such as shared storage or operating system features."
  - "Since IBM MQ 9.4.4, Native HA has been made available on Linux and is the
    preferred solution to RDQM in most circumstances because it does not have
    dependencies on the underlying operating system kernel. However, RDQM
    continues to be fully supported."
  - "You can use a Native HA CRR configuration to provide a disaster recovery
    solution."
- **[data]** The 9.4.x copy of that page has the same "primary" / "preferred" /
  "since 9.4.4" wording
  ([source](https://www.ibm.com/docs/en/SSFKSJ_9.4.0/configure/confureHA.html)).
  The 9.3.x and 9.2.x copies instead say "Native HA is a high availability
  solution aimed at containers", and offer RDQM for Linux
  ([9.3.x](https://www.ibm.com/docs/en/ibm-mq/9.3.x?topic=configuring-high-availability-recovery-restart)).
- **[data]** CRR is the DR answer for Native HA, and 10.0 adds a second DR
  topology: "There are two Native HA configurations that offer disaster
  recovery solutions … Native HA CRR is available on all the platforms that
  support Native HA. Native HA IRR is available on Linux (virtual machines and
  bare metal)."
  ([CRR vs IRR, MQ 10.0](https://www.ibm.com/docs/en/SSYHRD_10.0.0/configure/NativeHA_crr_v_irr.html))
- **[data]** The RDQM overview is neutral: "RDQM (replicated data queue manager)
  is a high availability solution that is available on Red Hat® Enterprise
  Linux® for x86-64 platforms." It carries no direction language
  ([RDQM HA, MQ 10.0](https://www.ibm.com/docs/en/SSYHRD_10.0.0/configure/q130280_.html)).

### 2.2 No deprecated or stabilized language

- **[data]** The MQ 10.0 *Deprecations, stabilizations, and removals* page,
  which covers every release from 7.1 to 10.0, has no match for RDQM, DRBD,
  Pacemaker, "replicated", multi-instance or Native HA
  ([source](https://www.ibm.com/docs/en/ibm-mq/10.0.x?topic=information-deprecations-stabilizations-removals-in-mq)).
  Nor do the per-release pages for
  [10.0.0](https://www.ibm.com/docs/en/SSYHRD_10.0.0/overview/mq1000_deprem.html),
  [9.4.0](https://www.ibm.com/docs/en/ibm-mq/9.4.x?topic=940-deprecated-stabilized-removed-features-in-mq)
  and [9.3.0](https://www.ibm.com/docs/en/SSFKSJ_9.3.0/overview/mq930_deprem.html).
- **[data]** The nearest thing is platform pruning: MQ 9.4.0 lists "RHEL 7 no
  longer supported for RDQM"
  ([What's new and changed in 9.4.0](https://www.ibm.com/docs/en/ibm-mq/9.4.x?topic=mq-whats-new-changed-in-940)).
- **[data]** The MQ 10.0.1 announcement (AD26-0859, §3.3) names **IBM MQ
  Explorer** as its deprecation, not RDQM.
- **No data:** any IBM roadmap, end date or "functionally stabilized" statement
  for RDQM. None was found.

### 2.3 What changed in each release, 9.2 to 10.0.1

- **[data]** From IBM's "What's new" pages (sources under the table):

  | Release | RDQM items | Native HA items |
  |---|---|---|
  | 9.2.0 LTS | DR/HA RDQM; RHEL 8 support (Pacemaker 2) alongside RHEL 7 | none (not yet released) |
  | 9.2.x CD | kmod serviceability, last-in-sync reporting, preferred location in status, clearing failed resource actions | Native HA for containers (OpenShift / Cloud Pak for Integration) |
  | 9.3.0 LTS | rolls up the 9.2.x CD items | rolls up; "aimed at containers" |
  | 9.3.1 CD | "RDQM available on RHEL 9" | none |
  | 9.3.2 CD | none | status attributes; Native HA entitlement |
  | 9.3.3 CD | none | autorecovery of damaged objects |
  | 9.4.0 LTS | "RHEL 7 no longer supported for RDQM" | LZ4 compression |
  | 9.4.2 CD | none | "Native HA cross-region replication in containers" |
  | 9.4.4 CD | replication links secured with TLS | "Native High Availability and Cross Region Replication on Linux" |
  | 9.4.5 CD | "Secure heartbeat for HA RDQM" | monitoring enhancements |
  | 10.0.0 LTS | rolls up the 9.4.4/9.4.5 TLS items | **new:** "Native HA In-Region Replication (IRR) support"; detailed status view |
  | 10.0.1 CD | "RDQM RHEL 10 support" (announcement, §3.3) | "Native HA Quantum Safe Encryption" for replication channels (announcement) |

  Sources: 9.2.0
  ([q134120_](https://www.ibm.com/docs/en/SSFKSJ_9.2.0/overview/q134120_.html),
  [q001060_](https://www.ibm.com/docs/en/SSFKSJ_9.2.0/overview/q001060_.html));
  9.2.x CD and 9.3.0
  ([mq930_new_multiadv](https://www.ibm.com/docs/en/SSFKSJ_9.3.0/overview/mq930_new_multiadv.html));
  9.3.x CD (`https://www.ibm.com/docs/en/SSFKSJ_9.3.0/overview/mq93N_new_overview.html`,
  N = 1–5); 9.4.x CD
  (`https://www.ibm.com/docs/en/SSFKSJ_9.4.0/overview/mq94N_newchgd.html`,
  N = 1–5, e.g.
  [9.4.2](https://www.ibm.com/docs/en/SSFKSJ_9.4.0/overview/mq942_newchgd.html),
  [9.4.4](https://www.ibm.com/docs/en/SSFKSJ_9.4.0/overview/mq944_newchgd.html));
  10.0.0
  ([What's new and changed in 10.0.0](https://www.ibm.com/docs/en/ibm-mq/10.0.x?topic=mq-whats-new-changed-in-1000)).
  The 10.0.1 docs are not published yet; the 10.0.1 row comes from the
  announcement only.
- **[data] Conflict on the CRR date.** IBM Docs list CRR (in containers) under
  9.4.2, and an IBM engineer's blog agrees ("Earlier in 2025 (IBM MQ 9.4.2) we
  announced an extension to Native HA in the form of Cross-region replication";
  [J. Rumsey, IBM Community, 2025-10-17](https://community.ibm.com/community/user/blogs/jonathan-rumsey/2025/10/14/nha-beyond-containers)).
  IBM's 9.4.4 announcement says "Built on the Native HA and CRR capabilities
  introduced in MQ 9.4.3"
  ([IBM, 2025-10-09](https://www.ibm.com/new/announcements/enhanced-resilience-compliance-and-simplicity-with-ibm-mq-9-4-4)).
  **[judgment]** We follow the docs: 9.4.2.
- **[judgment]** Since 9.4.0, every new **topology** capability (CRR, Native
  HA on Linux VMs and bare metal, IRR) has gone into Native HA. RDQM has had
  **maintenance**: OS currency (RHEL 9, now RHEL 10) and security hardening
  (TLS replication, secure heartbeat). That is the profile of a product IBM
  keeps healthy for its installed base, not one it is growing — and not one it
  is winding down either.

### 2.4 IBM product management and engineering voices

- **[data]** IBM MQ product management, 9.4.4 announcement
  ([A. McCormick, IBM, 2025-10-09](https://www.ibm.com/new/announcements/enhanced-resilience-compliance-and-simplicity-with-ibm-mq-9-4-4)):
  "Previous solutions for high availability and disaster recovery on Linux VMs
  (multi-instance, RDQM) had shared storage or operating system additions."
  The same announcement adds "RDQM replication traffic encrypted in transit".
- **[data]** IBM engineering
  ([J. Rumsey, 2025-10-17](https://community.ibm.com/community/user/blogs/jonathan-rumsey/2025/10/14/nha-beyond-containers)):
  "the component dependencies and limited platform support have been
  prohibitive barriers to adoption for some. Significantly, container
  environments cannot use RDQM as a solution due to the dependency on the DRBD
  kernel drivers."
- **[data]** *Introducing IBM MQ 10.0*
  ([IBM, 2026-04-21](https://www.ibm.com/new/announcements/introducing-ibm-mq-v10-0)):
  "Adopt zero-downtime architectures with Native HA and CRR, ensuring
  continuous operations and near-zero data loss". It does not mention RDQM.
- **[data]** IBM's MQ performance team publishes a **neutral** comparison, not
  a steer: "IBM MQ HA (NHA & RDQM) Performance Report", v1.0, February 2026, MQ
  9.4.5
  ([PDF](https://ibm-messaging.github.io/mqperf/MQ_9.4.5.HA_Report_for_NHA_and_RDQM_v1.0.pdf)).
- **[judgment]** IBM's voice is consistent: Native HA for new deployments,
  RDQM "fully supported" for the installed base.

## 3. RDQM's RHEL support cadence

### 3.1 How IBM ships RDQM for a RHEL kernel

- **[data]** RDQM needs a DRBD kernel module that IBM validates per RHEL
  kernel. The kernel-modules page
  ([node/1087143](https://www.ibm.com/support/pages/node/1087143), "Modified
  date: 07 October 2026") covers RHEL 7, 8 and 9 only. Its RHEL 9 entry is
  validated to kernel `5.14.0-687.54.1`. Its machine-readable twin
  ([ibm-mq-rdqm-kmods.json](https://public.dhe.ibm.com/ibmdl/export/pub/software/websphere/messaging/mqadv/ibm-mq-rdqm-kmods.json),
  `lastUpdated` `2026-10-09T09:55:53+00:00`) has no RHEL 10 / `6.12` kernel.
  Neither has a change log, and the JSON has no per-entry dates.
- **[data]** IBM matches kmods to kernels more loosely than one-per-kernel. The
  page says "Earlier kernel versions (within the supported range of RHEL
  releases supported by the IBM MQ version) have been confirmed to be
  compatible unless otherwise stated" and that "the included modver utility can
  be used to confirm which supplied kernel module is appropriate for use with
  the current kernel level." In the JSON, the `…_687.5.1` kmod is validated for
  kernels `687.12.1` through `687.56.1`, and the RHEL 8.9 kmod is reused for the
  RHEL 8.10 kernel. IBM never uses the words "kernel family" or "kABI".
- **[data]** The lab confirmed the family match empirically: `modver -m` on the
  real 10.0.0.0 and 10.0.0.5 tarballs maps the RHEL 9.8 GA kernel
  `5.14.0-687.5.3.el9_8` to the `_687.5.1` kmod
  ([#1405](https://github.com/logical-minds-foundry/mq-resiliency-lab-for-linux/issues/1405),
  [#1408](https://github.com/logical-minds-foundry/mq-resiliency-lab-for-linux/issues/1408)).
- **[judgment]** The "RHEL 9.8 has no kmod" blocker in #1396 was the lab's own
  exact-release glob, not IBM's support posture. The kmod paradigm is still
  confusing — IBM documents a kernel, not a RHEL minor — but it is less strict
  than it first looked.

### 3.2 Cadence by RHEL release

- **[data]** First RDQM kmod per RHEL release. Evidence is the earliest of: the
  MQ image that shipped it, the fix pack carrying its APAR, or a dated
  kernel-modules page snapshot on the
  [Wayback Machine](https://web.archive.org/web/2024*/https://www.ibm.com/support/pages/node/1087143).
  MQ release dates are from IBM's fix lists
  ([9.2 LTS](https://www.ibm.com/support/pages/fix-list-ibm-mq-version-92-lts),
  [9.3 LTS](https://www.ibm.com/support/pages/fix-list-ibm-mq-version-93-lts),
  [9.3.x CD](https://www.ibm.com/support/pages/fix-list-ibm-mq-version-93x-continuous-delivery),
  [9.4 LTS](https://www.ibm.com/support/pages/fix-list-ibm-mq-version-94-lts),
  [9.4.x CD](https://www.ibm.com/support/pages/fix-list-ibm-mq-version-94x-continuous-delivery)).
  RHEL GA dates are from
  [Red Hat](https://access.redhat.com/articles/red-hat-enterprise-linux-release-dates).

  | RHEL (GA) | First RDQM kmod (evidence) | Days after GA |
  |---|---|---|
  | **8.0** (2019-05-07) | MQ 9.2.0.0 image, 2020-07-23 (first RHEL 8 support, at 8.2) | 443 |
  | 8.3 (2020-11-03) | 9.2.1 CD image, 2020-12-03 | 30 |
  | 8.4 (2021-05-18) | page: "not currently supported" (19 May 2021); 9.2.3 CD, 2021-07-22 | 65 |
  | 8.6 (2022-05-10) | 9.3.0.0, 2022-06-23; IT40940 in 9.2.0.6 | 44 |
  | 8.7 (2022-11-09) | IT42445 in 9.2.0.7, 2022-12-01 | 22 |
  | 8.8 (2023-05-16) | IT43770 in 9.3.3.0, 2023-06-20 | 35 |
  | 8.9 (2023-11-14) | [IT44926](https://www.ibm.com/support/pages/apar/IT44926), 2023-12-14 | 30 |
  | 8.10 (2024-05-22) | 8.9 kmod reused; IT46256 `modver` update in 9.3.0.20, 2024-06-26 | ≤ 35 |
  | **9.0** (2022-05-17) | 9.3.1 CD, 2022-10-20 (first RHEL 9 support) | 156 |
  | 9.1 (2022-11-15) | 9.2.0.7 image, 2022-12-01 | 16 |
  | 9.2 (2023-05-10) | IT43724 in 9.3.3.0, 2023-06-20 | 41 |
  | 9.3 (2023-11-07) | IT44926, 2023-12-14 | 37 |
  | 9.4 (2024-04-30) | page modified 11 May 2024 lists `427.16.1` | ≤ 11 |
  | 9.5 (2024-11-12) | [IT47245](https://www.ibm.com/support/pages/apar/IT47245), 2024-12-17 | 35 |
  | **10.0** (2025-05-20) | none yet; MQ 10.0.1 announced for 2026-10-13 (§3.3) | 511 (planned) |

  RHEL 8.5 and 9.6–9.8 are omitted: their first-kmod dates can only be bounded
  from the MQ level IBM tested against, not read from a dated source. For
  RHEL 9.8, the 10.0.0.0 tarball (GA 2026-06-16) already ships a `687.5.1`
  kmod. APAR pages render with JavaScript, so the APAR dates above are the fix
  pack dates, not the APAR dates.
- **[judgment]** Two different cadences:
  - **New RHEL minor:** since 2022 a validated kmod has typically appeared
    **2 to 6 weeks** after RHEL GA. The one clear slip was RHEL 8.4.
  - **New RHEL major:** much slower — **443** days for RHEL 8, **156** for
    RHEL 9, and **511** (planned) for RHEL 10.

  So RDQM's RHEL 10 delay is in line with IBM's history for a new major. It is
  not, on its own, a deprecation signal. Expect RHEL 10.x minors to follow in
  weeks once a 10.0.1 baseline exists, and do not expect 10.0.0.x LTS levels to
  gain RHEL 10 RDQM.

### 3.3 RHEL 10: announced for MQ 10.0.1

- **[data]** IBM announcement **AD26-0859**, "IBM MQ 10.0.1 delivers new AI
  Agent capabilities, expands quantum-safe security, and delivers MQTT 5.0
  support", published 6 October 2026
  ([announcement](https://www.ibm.com/docs/en/announcements/mq-1001)):
  - "RDQM RHEL 10 support: Replicated Data Queue Manager (RDQM) is updated to
    support Red Hat Enterprise Linux 10."
  - Planned availability: "IBM MQ for Multiplatforms — 13 October 2026".
  - "The MQ 10.0.1 modification level release is a follow-on offering to the
    MQ 10.0 LTS release" (a Continuous Delivery level).

  The page renders with JavaScript, so neither `WebFetch` nor
  `tools/ibm_doc_cache.py` can read it. The quotes come from the rendered text
  the human pasted on 2026-10-09, recorded in
  [#1405](https://github.com/logical-minds-foundry/mq-resiliency-lab-for-linux/issues/1405).
  Re-check in a browser.
- **[data]** Every 10.0.0.x source still shows RHEL 10 RDQM as unsupported. The
  SPCR deliverable chain has only `10.0` and `10.0.0.5` (no 10.0.1 deliverable
  yet), and both list RDQM under `serverUnsupportedComps` for RHEL 10, with
  footnote Linux_7: "RHEL 10 is not currently supported and MQ does not include
  a DRBD kmod for this operating system release"
  ([chain](https://www.ibm.com/software/reports/compatibility/clarity-reports/report/json/getDeliverableChain?deliverableId=FA94B1F6888342969E7F9B505149F032)).
  The SPCR detail is in [`os-version-support-matrix.md`](os-version-support-matrix.md)
  §2.2, which #1405 is updating for 10.0.1.
- **[judgment]** The announcement undercuts the original premise of this task.
  A product IBM is retiring does not get a new RHEL major. What remains to
  verify once 10.0.1 ships (#1405): that the 10.0.1 Developer tarball carries an
  `el10/` kmod set, and that `modver` selects one for the lab's RHEL 10.2 GA
  kernel `6.12.0-211.7.3.el10_2`.

## 4. Ecosystem signals

### 4.1 LINBIT (DRBD upstream)

- **[data]** LINBIT's support matrix lists DRBD 9.1.x, 9.2.x and 9.3.x on RHEL
  10; the 9.3.x row reads "4.12.14 – 7.1 | 8, 9, 10 | 20.04 – 26.04" (kernel
  range, RHEL, Ubuntu) ([LINBIT](https://linbit.com/solutions-rfq/)).
- **[data]** LINBIT's kernel list covers RHEL 10.0, 10.1 and 10.2 kernels, for
  example "RHEL 10.2 6.12.0-211.64.1 kmod-drbd-9.3.4_6.12.0_211.7.1-1.x86_64"
  ([kernels.drbd.io](https://kernels.drbd.io/)). ELRepo publishes
  `kmod-drbd9x-9.3.4-1.el10_2` (2026-09-16)
  ([ELRepo el10](https://elrepo.org/linux/elrepo/el10/x86_64/RPMS/)).
- **[data]** DRBD 9 is still out of tree: "The current version of the DRBD
  kernel module in the mainline Linux kernel is 8.4.11"
  ([LINBIT blog, 2026-04-27](https://linbit.com/blog/working-to-put-drbd-9-in-the-mainline-linux-kernel/)).
- **[judgment]** DRBD 9 on RHEL 10 has been available upstream for months, so
  the RDQM RHEL 10 gap was IBM's validation and packaging, not the DRBD
  ecosystem. DRBD 9 stays an out-of-tree kmod that must match the kernel — the
  structural cost RDQM carries and Native HA does not.

### 4.2 Red Hat

- **[data]** Pacemaker is a first-class RHEL 10 add-on: Red Hat publishes
  *Configuring and managing high availability clusters* for RHEL 10
  ([docs](https://docs.redhat.com/en/documentation/red_hat_enterprise_linux/10/html-single/configuring_and_managing_high_availability_clusters/index)),
  and RHEL 10.1 rebased to Pacemaker 3.0.1
  ([RHEL-86085](https://issues.redhat.com/browse/RHEL-86085)).
- **[data]** Red Hat does not ship or support DRBD: "DRBD is supported by
  LINBIT for usage in Red Hat Enterprise Linux environments", and the kernel
  drivers "are provided by LINBIT"
  ([Red Hat KB 32085](https://access.redhat.com/solutions/32085), tagged RHEL
  7/8/9).
- **[judgment]** On RHEL, RDQM is the only path where one vendor (IBM) owns the
  whole Pacemaker + DRBD + MQ stack. That single ownership is RDQM's real value
  over a self-built cluster.

### 4.3 Ubuntu

- **[data]** Ubuntu's own DRBD how-to uses `drbd-utils` and the kernel's
  in-tree module
  ([Ubuntu Server docs](https://documentation.ubuntu.com/server/how-to/high-availability/install-drbd/)).
  The in-tree module is DRBD 8.4 (see
  [`os-version-support-matrix.md`](os-version-support-matrix.md) §3.3).
- **[data]** Ubuntu curates only a base set of resource agents ("IPaddr2 iscsi
  iSCSILogicalUnit iSCSITarget LVM-activate systemd"); pcs is "the recommended
  and supported tool from Ubuntu 23.04"
  ([Ubuntu Server docs](https://documentation.ubuntu.com/server/explanation/high-availability/pacemaker-resource-agents/)).
- **No data:** a Canonical statement on DRBD 9.

### 4.4 Community and customer-facing guidance

- **[data]** No independent practitioner (IBM Champion, MQ community, or
  conference deck) comparing RDQM with Native HA, or steering new deployments,
  was found. The steering found is IBM's own (§2).

## 5. Justifying `pcmk-ubuntu`

### 5.1 What it is, and what IBM says about it

- **[data]** `pcmk-ubuntu` is a community Pacemaker cluster (`pcmk-a1..3`)
  around an ordinary MQ queue manager on a shared SAN LUN (`san-a`, iSCSI). Its
  DR peer in Data Center B fronts `san-b`, whose LUN is DRBD-replicated from
  `san-a` over the WAN
  ([Architecture](https://github.com/logical-minds-foundry/mq-resiliency-lab-for-linux/blob/develop/docs/site/docs/architecture/index.md)).
  It is not RDQM, and RDQM is "only supported on RHEL x86-64"
  ([`os-version-support-matrix.md`](os-version-support-matrix.md) §3.2).
- **[data]** IBM documents this class of design as an **external HA
  solution**: "You can use IBM MQ with a high availability (HA) cluster on AIX
  and Linux platforms", with the condition that "A queue manager that is to be
  used in an HA cluster must have its data and logs on disks that are shared
  between the nodes in the cluster", and "Refer to the HA cluster documentation
  particular to your environment"
  ([HA clusters on AIX and Linux, MQ 10.0](https://www.ibm.com/docs/SSYHRD_10.0.0/configure/q017830_.html)).
- **[data]** IBM's HA/DR overview warns that external HA solutions "fail over
  more slowly than an HA mechanism internal to IBM MQ", and "require additional
  product purchase and deployment, and non-trivial additional skills"
  ([source](https://www.ibm.com/docs/en/ibm-mq/10.0.x?topic=configuring-high-availability-disaster-recovery)).
- **No data:** any IBM statement supporting DRBD for MQ outside RDQM.

### 5.2 Position (judgment)

- **[judgment]** `pcmk-ubuntu` is **not an IBM-supported HA product** the way
  RDQM is. IBM supports the queue manager on Ubuntu (the MQ server cell in the
  support matrix) and describes shared-disk HA clusters generically; the
  cluster itself, and the DRBD replication under the DR SAN, are owned by
  whoever builds them. The lab presents it honestly as that: the
  **open-source equivalent design** — the same Pacemaker and DRBD building
  blocks RDQM packages, assembled by hand on a platform RDQM does not cover.
- **[judgment]** Its value as a **counter-option** is that it shows what RDQM's
  single-vendor packaging buys you, and what owning it yourself costs. The
  human has shown such a design can match or beat RDQM, but it demands an
  ownership most teams will not take on. That contrast is useful to anyone
  weighing Native HA against the older patterns, so it stays while its
  building blocks remain supportable: the MQ server on its Ubuntu release,
  plus Ubuntu's Pacemaker. Its HA leg (a shared-disk cluster) is the model IBM
  documents for external HA.
- **[judgment]** Because no IBM HA product backs it, "while IBM supports it"
  means, for `pcmk-ubuntu`, while IBM supports the MQ queue manager on the
  stack's Ubuntu release and documents external HA clusters. Both hold today.

## 6. Options considered

| Option | Cost | Verdict |
|---|---|---|
| **A. Keep the secondary stacks, with per-stack RHEL minor pins** | Two RHEL 9 base boxes, against the box-naming rule; more bake and validation surface | **Not now.** The need came from the exact-match kmod glob (§3.1); `modver` removes it. Revisit only if a real per-minor RDQM gap appears. |
| **B. Freeze them at current versions** | The stacks drift behind IBM's supported levels; the lab demonstrates stale configs | **Rejected.** Contradicts "we don't drop what IBM still supports" — a frozen stack is a slow drop. |
| **C. Deprecate and remove them (deprecation epic)** | Lose the comparison the lab exists to show | **Rejected.** IBM has not deprecated RDQM (§2.2) and just added RHEL 10 (§3.3). |
| **D. Keep as "legacy reference" with a not-strategic banner** | Small docs cost | **Partly adopted, reworded.** "Legacy" overstates it for a fully supported product. The lab instead states Native HA's strategic status plainly (§7). |
| **E. Keep as-is, as completeness counter-options while IBM supports them** | Ongoing maintenance of two secondary stacks | **Chosen** (§1). |

- **[judgment]** The decision hinges on one trigger, owned by IBM: when IBM
  deprecates or stabilizes RDQM (or stops supporting the MQ server on the
  Ubuntu release `pcmk-ubuntu` uses), the lab opens a deprecation epic for the
  affected stack. Watch the MQ *Deprecations, stabilizations, and removals*
  page (§2.2), the SPCR, and the kernel-modules page (§3.1).

## 7. The lab's framing duty

The lab makes Native HA's strategic status explicit wherever users meet the HA
options:

- The docs site's **Home** page and the **Architecture** page state that Native
  HA is the strategic choice for new HA deployments, and that RDQM and
  Pacemaker/DRBD are counter-options kept while IBM supports them.
- The docs-site **Reference** page links this note.
- The full docs review in
  [#1263](https://github.com/logical-minds-foundry/mq-resiliency-lab-for-linux/issues/1263)
  carries the framing through the remaining pages (for example, the
  Architecture page still calls the RDQM arm "the headline topology").

## 8. Open items

- Verify the 10.0.1 Developer tarball's `el10/` kmods and the `modver` pick for
  the RHEL 10.2 GA kernel, then update the support matrix (#1405).
- Re-decide the RHEL 9.6 → 9.8 re-pin now that `modver` selects the kmod
  (#1396, #1408).
- Record this outcome in the #280 retrospective §5
  ([.github#283](https://github.com/logical-minds-foundry/.github/issues/283)).
