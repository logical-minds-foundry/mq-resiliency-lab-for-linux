# OS-version support matrix: Ubuntu 26.04 and RHEL 10 for IBM MQ 10.0

> Scope: IBM's support status for **IBM MQ 10.0 server, Native HA and RDQM** on
> the two OS majors the multi-version OS-axis epic adds: **Ubuntu 26.04 LTS** and
> **RHEL 10**. Also covered: the RHEL 10 point release to pin, and whether Ubuntu
> 26.04 ships the cluster/SAN packages the lab's Ubuntu roles install. This is
> spike **S2** (spec §5) and plan task **T0a** of epic
> [logical-minds-foundry/.github#280](https://github.com/logical-minds-foundry/.github/issues/280)
> (issue [#1271](https://github.com/logical-minds-foundry/mq-resiliency-lab-for-linux/issues/1271)).
> Tasks T8, T9, T10 and T11 consume it.
>
> Builds on [`mq10-rhel9-vs-rhel10-and-rdqm-support.md`](mq10-rhel9-vs-rhel10-and-rdqm-support.md)
> (#1095/#1096). That note's open item in §2 and §8 (the RHEL 10 "tested" status in
> the SPCR) is answered in §2 below. Nothing here contradicts it.
>
> Convention: each claim is tagged **[data]** (what a cited source actually says)
> or **[judgment]** (our reasoning on top). Support statuses are load-bearing:
> they gate stack defaults (spec §4.1). Verify them against the linked sources
> before relying on them.
>
> Retrieved: 2026-10-03. The IBM SPCR data below carries IBM's own
> `lastModified: 2026-09-28 10:26:14 EDT`.

## 1. The matrix

| OS | IBM MQ 10.0 server | Native HA | RDQM |
|---|---|---|---|
| **RHEL 10** (x86-64) | **supported** [data, S-1] | **supported** [data, S-1] | **unsupported** [data, S-1 footnote (7); S-3] |
| **Ubuntu 26.04 LTS** | **unsupported** (not listed) [data, S-1; see §3] | **unsupported** (not listed) [data, S-1; see §3] | **unsupported** [data, S-1 footnotes (1)/(3)] |

Source key (full URLs in §6):

- **S-1**: IBM Software Product Compatibility Report (SPCR) for IBM MQ 10.0,
  operating-system data (deliverable `FA94B1F6888342969E7F9B505149F032`).
- **S-2**: Red Hat, *Red Hat Enterprise Linux Release Dates*.
- **S-3**: IBM MQ 10.0 docs, *Hardware and software requirements on Linux
  systems* (cached via `tools/ibm_doc_cache.py`).
- **S-4**: Launchpad API and packages.ubuntu.com for `resolute` (26.04).

No cell is `unknown`. The two Ubuntu 26.04 MQ/Native HA cells are **absence-based**:
IBM lists Ubuntu 24.04 LTS and no later Ubuntu release. §3 explains why we read
that as `unsupported` and not as `unknown`.

## 2. RHEL 10

### 2.1 MQ 10.0 server and Native HA: supported

- **[data]** The MQ 10.0 SPCR has a row for `Red Hat Enterprise Linux (RHEL) 10`
  on `x86-64` (also IBM Z and POWER LE). Its fields are `osMinimum: "Base"` and
  `productMinimum: "10.0"`. `serverSupportedComps` lists `MQ Queue Manager`,
  `MQ Advanced` and `Native HA`, among others. (S-1)
- **[data]** For RHEL 10 x86-64, the per-release table marks both `MQ Queue Manager`
  and `Native HA` as `isSupported: "true"` for `Base`, `10.1`, `10.2` and
  `Future Fix Packs`. (S-1)
- **[data]** For comparison, RHEL 9 x86-64 is supported from `9.6` (`osMinimum: "9.6"`)
  through `9.8`. Releases `Base` through `9.5` are `isSupported: "false"`. The lab's
  current `rhel/9.6` pin sits at that floor. (S-1)
- **[judgment]** This settles the open item in the #1095 note (§2, §8): RHEL 10 is a
  listed, supported base-QM platform for MQ 10.0, and Native HA is listed with it.

### 2.2 RDQM: unsupported

- **[data]** The RHEL 10 SPCR row lists `Replicated Data Queue Manager (RDQM)` under
  `serverUnsupportedComps` on every architecture. Footnote (7) on that row reads
  verbatim: "RHEL 10 is not currently supported and MQ does not include a DRBD
  kmod for this operating system release". (S-1)
- **[data]** The MQ 10.0 Linux requirements page lists RDQM (Pacemaker)
  prerequisites for "supported levels of RHEL 8 (Pacemaker 2)" and "RHEL 9
  (Pacemaker 2)" only. It has no RHEL 10 list. (S-3)
- **[judgment]** This agrees with the #1095 note's finding that RDQM has no
  validated DRBD kernel module for RHEL 10. That note inferred it from the
  kernel-modules list. SPCR footnote (7) is now an explicit IBM statement. The word
  "currently" leaves room for a future kmod. Re-check the kernel-modules list
  ([ibm.biz/mqrdqmkernelmods](https://ibm.biz/mqrdqmkernelmods); human-fetch, §5).

### 2.3 The RHEL 10 point release to pin: **10.2**

- **[data]** Red Hat lists RHEL 10.2 GA as 2026-05-19 (kernel
  `6.12.0-211.7.1.el10_2`, `redhat-release` erratum `RHBA-2026:18131`). Earlier
  releases: 10.1 GA 2025-11-11, 10.0 GA 2025-05-20. No later RHEL 10 minor is
  listed. (S-2)
- **[data]** IBM's SPCR names RHEL 10 `Base`, `10.1` and `10.2` as supported for
  the MQ 10.0 queue manager and Native HA. (S-1)
- **[judgment]** **Pin `10.2`.** It is the latest GA minor that Red Hat ships, and
  IBM names it explicitly. The catalog entry (spec §4.1) becomes
  `rhel: 10: { point: "10.2", iso: rhel-10.2-x86_64-dvd.iso, requires: [x86-64-v3] }`.
  The ISO filename follows the existing `rhel-9.6-x86_64-dvd.iso` pattern. That
  filename is a lab naming convention, not a Red Hat-published name. Confirm it
  against the actual download when the human stages the DVD (D3).
- **[data / constraint]** RHEL 10 requires x86-64-v3. The #1095 note cites
  [Red Hat solution 7066628](https://access.redhat.com/solutions/7066628) for this.
  Exposure under the lab host's KVM is spike S3 (T0b,
  [#1272](https://github.com/logical-minds-foundry/mq-resiliency-lab-for-linux/issues/1272)).

## 3. Ubuntu 26.04 LTS

### 3.1 MQ 10.0 server and Native HA: unsupported (not listed)

- **[data]** Ubuntu 26.04 ("resolute") was released 2026-04-23. Launchpad reports
  `version: 26.04`, `status: Current Stable Release`. (S-4)
- **[data]** The MQ 10.0 SPCR's Linux rows are RHEL 8, RHEL 9, RHEL 10, SLES 15 and
  **Ubuntu 24.04 LTS**. There is **no Ubuntu 26.04 row** (and no other Ubuntu
  release), as of IBM's `lastModified` of 2026-09-28. (S-1)
- **[judgment]** We record this as `unsupported`, not `unknown`. The SPCR is
  IBM's authoritative, enumerated list of supported operating systems for the
  product, and the data was fetched five months after 26.04 GA. An absent OS is
  not a supported OS. It is still an absence, though, not a negative statement:
  IBM may add a 26.04 row in a later SPCR revision. Re-check S-1 before T9.
- **[data]** For reference, the Ubuntu 24.04 LTS row lists `MQ Queue Manager` and
  `Native HA` as supported on x86-64, IBM Z and POWER LE (`osMinimum: "Base"`). (S-1)

### 3.2 RDQM: unsupported

- **[data]** Linux footnotes (1) and (3) in the MQ 10.0 SPCR state "RDQM is only
  supported on RHEL x86-64". The SPCR marks these footnotes `appliedToAll`. The Ubuntu 24.04 row lists RDQM under
  `serverUnsupportedComps`. (S-1)
- **[judgment]** RDQM is unsupported on every Ubuntu release, so also on 26.04.
  The lab has no Ubuntu RDQM stack. `pcmk-ubuntu` is a community Pacemaker cluster
  around an ordinary MQ queue manager, not RDQM, so its IBM gate is the **MQ
  server** cell, not this one.

### 3.3 Package availability on Ubuntu 26.04 (resolute)

The lab's Ubuntu cluster/SAN roles install these packages:
`ansible/roles/pcmk-cluster/tasks/install-Debian.yml`,
`ansible/roles/drbd-san/tasks/main.yml` and
`ansible/roles/iscsi-target/tasks/install-Debian.yml`.

- **[data]** Published binaries from the Launchpad primary archive, as of
  2026-10-03 (S-4). Each was checked on both `amd64` and `arm64`, with identical
  results. Ubuntu 24.04 (`noble`) is shown for comparison:

  | Binary package | resolute (26.04) version | Component / pocket | noble (24.04) version |
  |---|---|---|---|
  | `pacemaker` | `3.0.1-1ubuntu2` | main / Release | `2.1.6-5ubuntu2` |
  | `pcs` | `0.12.1-2ubuntu3` | main / Release | `0.11.7-1ubuntu1` |
  | `fence-agents-virsh` | `4.17.0-1ubuntu1` (`4.17.0-1ubuntu1.1` in Proposed) | main / Release | `4.12.1-2~exp1ubuntu4` |
  | `drbd-utils` | `9.22.0-1.2build1` | main / Release | `9.22.0-1build1` |
  | `targetcli-fb` | `1:3.0.1-0.1build1` | main / Release | `1:2.1.53-1ubuntu3` |
  | `corosync` | `3.1.9-2ubuntu3` | main / Release | not checked |
  | `resource-agents-base` | `1:4.17.0-1ubuntu2.1` | main / Updates | not checked |
  | `resource-agents-extra` | `1:4.17.0-1ubuntu2.1` | **universe** / Updates | not checked |
  | `linux-modules-extra-<kver>-generic` | **not published** | n/a | published (e.g. `linux-modules-extra-6.8.0-146-generic`) |

- **[data]** The resolute GA kernel (`linux-generic`) is `7.0.0-14.14` (Release).
  The current one is `7.0.0-38.38` (Updates). Launchpad reports
  `linux-modules-extra-7.0.0-14-generic` and `linux-modules-extra-7.0.0-38-generic`
  as not published on either architecture. (S-4)
- **[data]** On resolute, `drbd.ko.zst`, `iscsi_target_mod.ko.zst` and the
  `target_core_*.ko.zst` modules ship in the **base `linux-modules-<kver>-generic`**
  package. The packages.ubuntu.com file lists for `linux-modules-7.0.0-14-generic`
  (amd64) and `linux-modules-7.0.0-38-generic` (amd64 and arm64) contain
  `/usr/lib/modules/<kver>/kernel/drivers/block/drbd/drbd.ko.zst`. On noble,
  `drbd.ko.zst` is in `linux-modules-extra-6.8.0-146-generic`. (S-4)
- **[judgment] (consequence for T8)** Every package the lab names is available
  on 26.04 except one. The **`linux-modules-extra-*` package does not exist on
  26.04**, and the DRBD and LIO target modules it supplied on 24.04 are in the
  base modules package instead. `drbd-san` caches and installs
  `linux-modules-extra-{{ ansible_kernel }}`, so it needs a per-version fix-up in
  T8: install nothing extra on 26, or install `linux-modules-<kver>` if the box
  lacks it. `pacemaker` moves a major version (2.1 → 3.0) and `pcs` moves
  0.11 → 0.12. Expect role fix-ups there too. T8/V2 must prove this; it is not
  established here.
- **Pending T0b ([#1272](https://github.com/logical-minds-foundry/mq-resiliency-lab-for-linux/issues/1272)):**
  live `apt-cache policy <pkg>` output from a 26.04 guest (plan T0a step 2).
  T0b pastes it into its report and into this section. Until then the table above
  rests on archive metadata, not on a booted guest's apt sources.

## 4. Defaults decision (spec §4.1)

Rule (spec §4.1): **a stack's default may point at a version only if that version
is cited as IBM-`supported`.** An unsupported version still ships as
selectable lab-only. It carries `ibm_support: { status: unsupported, source: <url> }`,
and the resolver warns when it is selected.

| Stack | Spec goal default | Gating cell | Decision |
|---|---|---|---|
| `nativeha-ubuntu` | `ubuntu:26` | Ubuntu 26.04 × Native HA = unsupported (not listed) | **Default stays `ubuntu:24`.** `ubuntu:26` ships lab-only with `ibm_support: unsupported`. T9 does not flip. |
| `pcmk-ubuntu` | `ubuntu:26` | Ubuntu 26.04 × MQ server = unsupported (not listed) | **Default stays `ubuntu:24`.** `ubuntu:26` ships lab-only with `ibm_support: unsupported`. T9 does not flip. |
| `nativeha-rhel-crr` | `rhel:10` | RHEL 10 × Native HA = supported | **Flip to `rhel:10` is permitted** (T11), once V3 proves the build. Pin point `10.2`. |
| `rdqm-rhel` | `rhel:9` | RHEL 10 × RDQM = unsupported | **Stays `rhel:9`**, as the spec already says. `rhel:10` is not in its supported list. |

- **[judgment]** For both Ubuntu stacks, T9's "if IBM-supported" condition is
  **not met** today. T9 should re-check S-1 when it runs. If IBM has added an
  Ubuntu 26.04 row that lists the MQ Queue Manager (and Native HA, for
  `nativeha-ubuntu`), the flip becomes permitted. Cite the new row when that
  happens.
- **[judgment] (flag for the epic)** §4.1 gates *stack* defaults only.
  `infra: ubuntu:26` (spec goal 2) moves the shared Ubuntu MQ commons
  (`svc-sim`, `app-client`, `mon-probe`) to 26.04. Those nodes run the MQ server
  and client (the `mq-ubuntu2404` box bakes them in, per `lab/topology.yaml`), so
  MQ would run on an OS IBM does not list. §4.1 neither covers nor forbids this.
  The epic should decide it explicitly before T8 moves infra to 26.
- **[data] (side observation, outside this epic's scope)** The MQ 10.0 SPCR lists
  only `x86-64`, `IBM Z and IBM LinuxONE` and `POWER System - Little Endian`
  hardware for its Linux rows. There is no ARM64 row. The lab's arm64 (Apple
  Silicon) Ubuntu builds therefore run MQ on a hardware platform the SPCR does not
  list, on 24.04 as well as on 26.04. (S-1)

## 5. Human-fetch items

Neither `WebFetch` nor `tools/ibm_doc_cache.py` can retrieve these. Open each in
a browser to confirm or extend the data above. Nothing here paraphrases them.

1. **SPCR, browser view** (the human-readable rendering of S-1; confirm the RHEL 10
   and Ubuntu rows match §2–§3):
   <https://www.ibm.com/software/reports/compatibility/clarity-reports/report/html/softwareReqsForProduct?deliverableId=FA94B1F6888342969E7F9B505149F032&osPlatforms=Linux&duComponentIds=S008>
2. **System Requirements for IBM MQ 10.0** (IBM Support page):
   <https://www.ibm.com/support/pages/system-requirements-ibm-mq-100>
3. **IBM MQ RDQM kernel modules** (watch for a RHEL 10 / kernel `6.12` entry):
   <https://www.ibm.com/support/pages/node/1087143> (linked from SPCR footnote (7)),
   also reachable as <https://ibm.biz/mqrdqmkernelmods>.
4. **IBM MQ support for SELinux** (linked from every SPCR Linux footnote):
   <https://www.ibm.com/support/pages/node/261161>

## 6. Sources

- **S-1: IBM SPCR, MQ 10.0, operating systems.** The SPCR page is a JavaScript
  app. Its data comes from a public JSON endpoint behind the page, and we fetched
  that endpoint directly on 2026-10-03 with a browser user-agent:
  <https://www.ibm.com/software/reports/compatibility/clarity-reports/report/json/getTSROsSupportSummary?deliverableId=FA94B1F6888342969E7F9B505149F032&osPlatforms=Linux&duComponentIds=spcrAllValues&mandatoryCapIds=spcrAllValues&optionalCapIds=spcrAllValues>.
  The deliverable chain
  (<https://www.ibm.com/software/reports/compatibility/clarity-reports/report/json/getDeliverableChain?deliverableId=FA94B1F6888342969E7F9B505149F032>)
  names it `IBM MQ` version `10.0`, with fix pack `10.0.0.5` as a child. The
  relevant fields are `osSupportDetails[].os`, `.hardware`, `.osMinimum`,
  `.serverSupportedComps`, `.serverUnsupportedComps`, `.footnotes[]` and
  `.osServicePackDetails[]`. To reproduce:

  ```bash
  curl -sS -A 'Mozilla/5.0 (X11; Linux x86_64; rv:128.0) Gecko/20100101 Firefox/128.0' \
    -H 'Accept: application/json' \
    'https://www.ibm.com/software/reports/compatibility/clarity-reports/report/json/getTSROsSupportSummary?deliverableId=FA94B1F6888342969E7F9B505149F032&osPlatforms=Linux&duComponentIds=spcrAllValues&mandatoryCapIds=spcrAllValues&optionalCapIds=spcrAllValues'
  ```

  Human-fetch item 1 is the browser rendering of the same data.
- **S-2: Red Hat Enterprise Linux Release Dates:**
  <https://access.redhat.com/articles/red-hat-enterprise-linux-release-dates>
  (RHEL 10 table: release, GA date, errata date, `redhat-release` erratum, kernel).
- **S-3: IBM MQ 10.0, Hardware and software requirements on Linux systems:**
  <https://www.ibm.com/docs/en/ibm-mq/10.0.x?topic=linux-hardware-software-requirements-systems>
  (fetched with `python3 tools/ibm_doc_cache.py`; cached `content.txt`, content
  path `SSYHRD_10.0.0`; see the "RDQM (replicated data queue manager)" section).
- **S-4: Ubuntu archive metadata:**
  - Series: <https://api.launchpad.net/1.0/ubuntu/resolute>.
  - Published binaries, one query per package and architecture. Example:
    <https://api.launchpad.net/1.0/ubuntu/+archive/primary?ws.op=getPublishedBinaries&binary_name=pacemaker&exact_match=true&status=Published&distro_arch_series=https%3A%2F%2Fapi.launchpad.net%2F1.0%2Fubuntu%2Fresolute%2Famd64>.
  - Package pages: <https://packages.ubuntu.com/resolute/pacemaker>,
    [`pcs`](https://packages.ubuntu.com/resolute/pcs),
    [`fence-agents-virsh`](https://packages.ubuntu.com/resolute/fence-agents-virsh),
    [`drbd-utils`](https://packages.ubuntu.com/resolute/drbd-utils),
    [`targetcli-fb`](https://packages.ubuntu.com/resolute/targetcli-fb).
  - Module file lists:
    <https://packages.ubuntu.com/resolute-updates/amd64/linux-modules-7.0.0-38-generic/filelist>,
    <https://packages.ubuntu.com/resolute-updates/arm64/linux-modules-7.0.0-38-generic/filelist>,
    <https://packages.ubuntu.com/resolute/amd64/linux-modules-7.0.0-14-generic/filelist>,
    and, for the noble comparison,
    <https://packages.ubuntu.com/noble-updates/amd64/linux-modules-extra-6.8.0-146-generic/filelist>.
- **Prior art in this repo:**
  [`mq10-rhel9-vs-rhel10-and-rdqm-support.md`](mq10-rhel9-vs-rhel10-and-rdqm-support.md)
  (#1095/#1096). IBM's
  [`ibm-messaging/rdqm-kmod-queries`](https://github.com/ibm-messaging/rdqm-kmod-queries)
  automates the kernel-modules check that §2.2 says to repeat.
