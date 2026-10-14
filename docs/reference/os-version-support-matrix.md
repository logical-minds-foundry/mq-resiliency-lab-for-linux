# OS-version support matrix: Ubuntu 26.04 and RHEL 10 for IBM MQ 10.0

> Scope: IBM's support status for **IBM MQ 10.0 server, Native HA and RDQM** on
> the two OS majors the multi-version OS-axis epic adds: **Ubuntu 26.04 LTS** and
> **RHEL 10**. Also covered: the RHEL 10 point release to pin, and whether Ubuntu
> 26.04 ships the cluster/SAN packages the lab's Ubuntu roles install. This is
> spike **S2** (spec §5) and plan task **T0a** of epic
> [logical-minds-foundry/.github#280](https://github.com/logical-minds-foundry/.github/issues/280)
> (issue [#1271](https://github.com/logical-minds-foundry/mq-resiliency-lab-for-linux/issues/1271)).
> Tasks T8, T9, T10 and T11 consume it. T0b ([#1272](https://github.com/logical-minds-foundry/mq-resiliency-lab-for-linux/issues/1272))
> added the live 26.04 package data (§3.3), ARM64 support (§5) and the
> Ubuntu support-lag rule (§6).
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
> `lastModified: 2026-09-28 10:26:14 EDT`. The RDQM × RHEL 10 cell was revised
> on 2026-10-10 (issue
> [#1405](https://github.com/logical-minds-foundry/mq-resiliency-lab-for-linux/issues/1405))
> after IBM announced RDQM support for RHEL 10 in MQ 10.0.1. See §2.4.
> On 2026-10-13 the lab re-pinned to MQ **10.0.1.0**
> ([#1409](https://github.com/logical-minds-foundry/mq-resiliency-lab-for-linux/issues/1409)),
> and §2.4 records the "Verify when 10.0.1 ships" results.
>
> **Where the open items went (2026-10-11).** Epic #280 closed on what it shipped.
> The items that wait on IBM moved to the follow-on epic
> [logical-minds-foundry/.github#315](https://github.com/logical-minds-foundry/.github/issues/315):
> the MQ 10.0.1 re-pin ([#1409](https://github.com/logical-minds-foundry/mq-resiliency-lab-for-linux/issues/1409)),
> `rdqm-rhel` on RHEL 10 ([#1440](https://github.com/logical-minds-foundry/mq-resiliency-lab-for-linux/issues/1440)),
> and plan task T9, the SPCR-gated move of the Ubuntu stack defaults and `infra_mq`
> to Ubuntu 26 ([#1285](https://github.com/logical-minds-foundry/mq-resiliency-lab-for-linux/issues/1285)).
> Where this document says "T9", read #1285.

## 1. The matrix

| OS | IBM MQ 10.0 server | Native HA | RDQM |
|---|---|---|---|
| **RHEL 10** (x86-64) | **supported** [data, S-1] | **supported** [data, S-1] | **supported from MQ 10.0.1** (CD, GA 2026-10-13); **not in any 10.0.0.x** [data, S-5; S-1 footnote (7); §2.4]. The 10.0.1.0 tarball ships `el10` kmods, and `modver` maps the RHEL 10.2 GA kernel to one [data, §2.4]. The SPCR has no 10.0.1 row yet. In the lab, `rdqm-rhel` offers `rhel:10` as **selectable** (default stays `rhel:9`), from [#1440](https://github.com/logical-minds-foundry/mq-resiliency-lab-for-linux/issues/1440) [§2.4, §4]. |
| **Ubuntu 26.04 LTS** | **unsupported** (not listed) [data, S-1; see §3] | **unsupported** (not listed) [data, S-1; see §3] | **unsupported** [data, S-1 footnotes (1)/(3)] |

Source key (full URLs in §8):

- **S-1**: IBM Software Product Compatibility Report (SPCR) for IBM MQ 10.0,
  operating-system data (deliverable `FA94B1F6888342969E7F9B505149F032`).
- **S-2**: Red Hat, *Red Hat Enterprise Linux Release Dates*.
- **S-3**: IBM MQ 10.0 docs, *Hardware and software requirements on Linux
  systems* (cached via `tools/ibm_doc_cache.py`).
- **S-4**: Launchpad API and packages.ubuntu.com for `resolute` (26.04).
- **S-5**: IBM announcement AD26-0859, *IBM MQ 10.0.1* (2026-10-06).
- **S-6**: IBM MQ RDQM kernel modules: the support page and its JSON feed.

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
  current RHEL 9 point-release pin (`9.6`, `os.rhel.9.point` in `lab/versions.yaml`) sits at that floor. (S-1)
- **[judgment]** This settles the open item in the #1095 note (§2, §8): RHEL 10 is a
  listed, supported base-QM platform for MQ 10.0, and Native HA is listed with it.

### 2.2 RDQM: unsupported in MQ 10.0.0.x

> Superseded for MQ 10.0.1 by §2.4. Everything below still holds for every
> 10.0.0.x level, including 10.0.0.5, the lab's `lab/mq-version` pin until the
> 10.0.1.0 re-pin on 2026-10-13.

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
  ([ibm.biz/mqrdqmkernelmods](https://ibm.biz/mqrdqmkernelmods); human-fetch, §7).

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
- **[data]** T0b found all eight x86-64-v3 marker flags (`abm avx2 bmi1 bmi2
  f16c fma movbe xsave`) in a KVM `host-passthrough` guest on the cloud x86_64
  host. TCG was not measured. See
  [`2026-10-os-axis-spike.md`](../reports/2026-10-os-axis-spike.md) §1.

### 2.4 RDQM: supported from MQ 10.0.1 (CD), not in any 10.0.0.x

> Added by [#1405](https://github.com/logical-minds-foundry/mq-resiliency-lab-for-linux/issues/1405),
> 2026-10-10. "Re-verified 2026-10-10" means the source was fetched again on
> that date and still says what is quoted.

**Summary.** IBM has announced RDQM support for RHEL 10 in **MQ 10.0.1**, a
Continuous Delivery modification-level release that becomes available on
**2026-10-13**. No 10.0.0.x level supports it: the SPCR, the kernel-modules
page and feed, and the lab's 10.0.0.5 Developer tarball all still show RHEL 10
RDQM as unsupported, with no `el10` kmods. 10.0.1 shipped on 2026-10-13 and the
lab re-pinned to 10.0.1.0 the same day. Its Developer tarball carries an `el10`
RDQM prereq set, and IBM's `modver` maps the lab's RHEL 10.2 GA kernel to an
`el10` kmod (checks 1–2 below pass).
[#1440](https://github.com/logical-minds-foundry/mq-resiliency-lab-for-linux/issues/1440)
makes `rhel:10` selectable for `rdqm-rhel`; the default stays `rhel:9` (see
"Decision (2026-10-13, #1440)" at the end of this section).

**Release and edition**

- **[data]** IBM announcement AD26-0859, "IBM MQ 10.0.1 delivers new AI Agent
  capabilities, expands quantum-safe security, and delivers MQTT 5.0 support",
  published 2026-10-06, says: "RDQM RHEL 10 support: Replicated Data Queue
  Manager (RDQM) is updated to support Red Hat Enterprise Linux 10." It gives
  planned availability as "IBM MQ for Multiplatforms — 13 October 2026", the
  same date for MQ Advanced and the non-production editions. It describes 10.0.1
  as "a follow-on offering to the MQ 10.0 LTS release", that is, a CD
  modification level. (S-5)
  - Retrieval: the page is rendered by JavaScript. `WebFetch`,
    `tools/ibm_doc_cache.py` (no `?topic=` slug) and `curl` with a browser
    user-agent (HTTP 403 on 2026-10-10) all fail to read it. The quotes come
    from the rendered page, which the human pasted into #1405 on 2026-10-09.
    **Not re-verified 2026-10-10**; it is human-fetch item 5 (§7).
- **[data]** The SPCR deliverable chain for MQ 10.0 lists only `10.0` and its
  fix pack `10.0.0.5`. There is no 10.0.1 deliverable yet. Re-verified
  2026-10-10. (S-1)
- **[data]** The MQ 10.0 SPCR (`lastModified` 2026-09-28) and the 10.0.0.5 SPCR
  (`lastModified` 2026-10-01) both list RDQM under `serverUnsupportedComps` for
  RHEL 10 x86-64, with footnote (7), "RHEL 10 is not currently supported and MQ
  does not include a DRBD kmod for this operating system release". Re-verified
  2026-10-10. (S-1)
- **[data]** The *Downloading IBM MQ 10.0 CD* page still reads "The latest IBM
  MQ CD release is IBM MQ 10.0.0.5". The public Developer download directory
  holds 10.0.0.0 and 10.0.0.5 tarballs only. Re-verified 2026-10-10. (S-7)
- **[judgment]** The announcement says the non-production editions ship on the
  same date. That points to a 10.0.1 MQ Advanced for Developers LinuxX64
  tarball, which is the edition the lab uses. Whether that tarball carries the
  `el10` RDQM prereqs is not established until it can be listed (see "Verify
  when 10.0.1 ships" below).

**Kernel modules**

- **[data] (local)** The cached
  `10.0.0.5-IBM-MQ-Advanced-for-Developers-LinuxX64.tar.gz` has
  `MQServer/Advanced/RDQM/PreReqs/{el8,el9}/` only, each with `kmod-drbd-9/`
  (including IBM's `modver`), `pacemaker-2/` and `drbd-utils-9/`. It has no
  `el10/` directory and no `6.12.0` kmod; the newest el9 kmod is
  `kmod-drbd-9.3.2_5.14.0_687.5.1`. Re-listed read-only 2026-10-10.
- **[data]** The RDQM kernel-modules support page (modified 2026-10-07) has
  tables for RHEL 7, 8 and 9 only. Its JSON feed (`lastUpdated`
  2026-10-09T09:55:53+00:00) has no `6.12.0` kernel level and no `el10` string.
  Its newest kmod bundle key is `10.0.0` (`10.0.0-IBM-MQ-RDQM-Kmods`), covering
  `RHEL8` and `RHEL9` only. Re-verified 2026-10-10. (S-6)
- **[data] (lab)** The RHEL 10.2 DVD's GA kernel is `6.12.0-211.7.3.el10_2`,
  from `kernel-core` on the DVD (#1405). Red Hat's release-dates table gives
  `6.12.0-211.7.1.el10_2` for 10.2 GA (§2.3, S-2). The lab's guests are
  offline and install only from the DVD, so this is the kernel RDQM must
  match.
- **[data] (lab)** `rdqm-install` picks the kmod with IBM's `modver` helper,
  which matches by **kernel family**, not exact release (#1408). On RHEL 9 it
  maps the 9.8 GA kernel `5.14.0-687.5.3.el9_8` to the `_687.5.1` kmod (#1405
  research comment).
- **[judgment] Kernel caveat (2026-10-10; answered by the 2026-10-13
  results below).** IBM had not yet published which RHEL 10 kernel(s) the 10.0.1 kmods target. The lab's 10.2 GA kernel is covered only if
  10.0.1 ships an `el10` kmod in the `6.12.0-211` family that `modver` selects
  for `6.12.0-211.7.3.el10_2`. IBM's RHEL 9 history makes that likely but not
  certain. It shipped a kmod for the 9.6 GA family (`570.12.1`) but listed 9.6
  GA kernels under "known compatibility issues". Until then, this is an
  expectation, not a fact.

**Verify when 10.0.1 ships** (from 2026-10-13; the MQ 10.0.1 re-pin is
[#1409](https://github.com/logical-minds-foundry/mq-resiliency-lab-for-linux/issues/1409))

1. The 10.0.1 Developer LinuxX64 tarball has
   `MQServer/Advanced/RDQM/PreReqs/el10/` with `kmod-drbd-*`, `pacemaker-*` and
   `drbd-utils-*` subdirectories. Record the exact directory names, which the
   follow-on `vars/RedHat-10.yml` needs.
2. `modver` in query mode selects an `el10` kmod for
   `6.12.0-211.7.3.el10_2`, and that kmod's kernel family is `211`.
3. The kernel-modules page and feed add a RHEL 10 table and a `10.0.1` bundle
   key. Check the page's "known compatibility issues" table for a RHEL 10 row
   that covers `211.7.x`.
4. The SPCR adds a 10.0.1 deliverable whose RHEL 10 x86-64 row moves RDQM to
   `serverSupportedComps`. Cite its deliverable ID and `lastModified`.

**Results (2026-10-13, [#1409](https://github.com/logical-minds-foundry/mq-resiliency-lab-for-linux/issues/1409))**

- **[data] Release.** The *Downloading IBM MQ 10.0 CD* page (modified
  2026-10-13) reads "Latest CD Release: IBM MQ 10.0.1", with defect fixes
  until 12 October 2027. The public Developer directory holds all three
  `10.0.1.0-IBM-MQ-Advanced-for-Developers-{LinuxX64,UbuntuLinuxX64,UbuntuLinuxARM64}.tar.gz`
  tarballs, dated 2026-10-13. A `10.0.1-…` file name returns HTTP 404, so the pin
  string is `10.0.1.0`. (S-7)
- **[data] (local) Media.** `scripts/fetch-mq.sh` on the 10.0.1.0 pin
  downloaded `10.0.1.0-IBM-MQ-Advanced-for-Developers-LinuxX64.tar.gz`
  (597,696,780 bytes, sha256 `f0744d4e1f8bfa33277fcaa4af0b8c9d86142e8e76a46c54d9591bf753b93304`)
  and `…-UbuntuLinuxARM64.tar.gz` (548,269,450 bytes, sha256
  `62746769ea5b7af87f930c6d544720aee9af6a820978ccd73ad368ad0b5695e2`) and
  recorded both checksums. A second run verified them (`sha256sum -c`: OK).
  These are the lab's own first-download checksums; IBM publishes none in that
  directory to compare against. The UbuntuLinuxX64 tarball was not fetched,
  because `fetch-mq.sh` fetches only the host-arch Ubuntu flavour and this
  host is aarch64.
- **[data] (local) Check 1 passes.** The 10.0.1.0 LinuxX64 tarball has
  `MQServer/Advanced/RDQM/PreReqs/el10/` with three subdirectories:
  - `el10/kmod-drbd-9/`: `modver` plus three kmods,
    `kmod-drbd-9.3.3+ptf.28.g6c10a1e64888_6.12.0_55.9.1-1.x86_64.rpm` (RHEL
    10.0 family), `…_6.12.0_124.8.1-1.x86_64.rpm` (10.1) and
    `…_6.12.0_211.7.1-1.x86_64.rpm` (10.2).
  - `el10/pacemaker-3/`: **Pacemaker 3** (`pacemaker-3.0.1.linbit-1.el10`,
    `corosync-3.1.9.linbit-2.0.el10`, `crmsh-4.6.2.linbit-1.0.117.721d6d2a.el10`,
    `resource-agents-4.17.0.linbit-3.0.36.c8cff50d.el10`, and their libraries).
    The el8 and el9 sets use `pacemaker-2/`.
  - `el10/drbd-utils-9/`: `drbd-utils-9.33.1-1.el10` and its `drbd-*`
    noarch companions.

  The `el9` set changed only in its kmod names, from `kmod-drbd-9.3.2_<kernel>`
  to `kmod-drbd-9.3.3+ptf.28.g6c10a1e64888_<kernel>`, for the same twelve
  kernels. `el9/pacemaker-2/` and `el9/drbd-utils-9/` are unchanged from
  10.0.0.5, so `vars/RedHat-9.yml` needs no change.
- **[data] (local) Check 2 passes.** `el10/kmod-drbd-9/modver` is a bash script
  that maps kernel families with a `case` on the kernel release (`6.12.0-55.*`,
  `6.12.0-124.*`, `6.12.0-211.*`). Given a kernel argument it reads nothing from
  the host, so running it on this non-RHEL box gives the same answer as on a
  RHEL guest. `modver -m 6.12.0-211.7.3.el10_2.x86_64` printed
  `kmod-drbd-9.3.3+ptf.28.g6c10a1e64888_6.12.0_211.7.1-1.x86_64.rpm` then
  `10.0.0.0`, exit 0. The selected kmod is in the `211` family. The `el9`
  `modver` from the same tarball still rejects that kernel ("Unsupported
  kernel release.", exit 1), so RHEL 10 needs the `el10` directory.
- **[data] modver and the kmod page disagree on the minimum MQ level.** The
  `el10` `modver -m` prints `10.0.0.0` as the minimum MQ level, but the
  kernel-modules page and feed give `minVrmf` `10.0.1.0` for the RHEL 10 kmod.
  The role's parse only requires modver's level to be no newer than the pin,
  so either value passes on the 10.0.1.0 pin. **[judgment]** The page is the
  authoritative figure: no 10.0.0.x tarball ships an `el10` kmod.
- **[data] Check 3 passes.** The kernel-modules page (modified 2026-10-13) has
  a "RHEL 10 kernel versions" table: kernel `6.12.0-211.65.1`, validated IBM MQ
  version `10.0.1.0`, kmod `kmod-drbd-9.3.3+ptf.28.g6c10a1e64888_6.12.0_211.7.1-1`,
  minimum MQ `10.0.1.0`. Its "known compatibility issues" table has no RHEL 10
  row (RHEL 9 is still `5.14.0-570.12.1 -> 5.14.0-570.15.1`). The JSON feed
  (`lastUpdated` 2026-10-13T08:27:01+00:00) adds three RHEL 10 kernel levels,
  `6.12.0-55.109.1` (kmod `…_6.12.0_55.9.1-1`), `6.12.0-211.64.1` and
  `6.12.0-211.65.1` (both kmod `…_6.12.0_211.7.1-1`), all under the `10.0.x`
  key with `minVrmf` `10.0.1.0`. The bundle key is `10.0.x`
  (`10.0-CD-IBM-MQ-RDQM-Kmods`), not `10.0.1`. (S-6)
- **[data] Check 4 is still open.** The SPCR deliverable chain still lists only
  `10.0` (`lastModified` 2026-09-28) and `10.0.0.5` (`lastModified`
  2026-10-01). Both still list RDQM under `serverUnsupportedComps` for RHEL 10
  x86-64. Re-verified 2026-10-13. (S-1)
- **[data] IBM Docs lag.** The MQ 10.0.x *Hardware and software requirements
  on Linux* and *Installing RDQM* topics (re-fetched 2026-10-13 with
  `tools/ibm_doc_cache.py`, content path `SSYHRD_10.0.0`) still list RDQM
  prerequisites for RHEL 8 and RHEL 9 only. (S-3)
- **[judgment]** The media gate for
  [#1440](https://github.com/logical-minds-foundry/mq-resiliency-lab-for-linux/issues/1440)
  holds: there is an `el10` kmod set, and IBM's own selector covers the lab's
  RHEL 10.2 GA kernel. #1440's `vars/RedHat-10.yml` should use
  `el10/kmod-drbd-9`, `el10/pacemaker-3` and `el10/drbd-utils-9`. Pacemaker 3
  is a new major for the lab's RDQM arm, so #1440 must prove that the RHEL
  10.2 DVD supplies its OS dependencies offline. Cite the SPCR row once a
  10.0.1 deliverable appears.

**Decision (2026-10-10): not yet.**

- **[judgment]** `rdqm-rhel` stays `supported: [rhel:9]`, default `rhel:9`, on
  the 10.0.0.5 pin. On 10.0.0.5, RDQM × RHEL 10 is IBM-unsupported and the
  tarball has no `el10` kmods, so there is nothing to build against. Enabling
  `rdqm-rhel@10` depends on re-pinning to 10.0.1
  ([#1409](https://github.com/logical-minds-foundry/mq-resiliency-lab-for-linux/issues/1409))
  and on checks 1–2 above. That work moves to the follow-on epic for the 10.0.1
  re-pin ([.github#315](https://github.com/logical-minds-foundry/.github/issues/315),
  task [#1440](https://github.com/logical-minds-foundry/mq-resiliency-lab-for-linux/issues/1440)),
  which adds `rhel:10` to the catalog, an `rdqm-install`
  `vars/RedHat-10.yml`, an `mq-rdqm-rhel10` bake and a validation row.
- **[judgment]** This follows the lab's position on RDQM: the lab supports RDQM
  for as long as IBM does. RDQM and Pacemaker/DRBD remain counter-options for
  completeness alongside Native HA, the strategic HA technology.

**Update (2026-10-13): unblocked, not yet enabled.**

- **[judgment]** The lab now pins 10.0.1.0, and checks 1–2 pass (see the
  results above), so nothing blocks
  [#1440](https://github.com/logical-minds-foundry/mq-resiliency-lab-for-linux/issues/1440).
  The re-pin does not change the catalog: `rdqm-rhel` stays
  `supported: [rhel:9]`, default `rhel:9`, until #1440 adds `rhel:10` and
  proves it with a cold-rebuild validation row.

**Decision (2026-10-13, [#1440](https://github.com/logical-minds-foundry/mq-resiliency-lab-for-linux/issues/1440)): selectable.**

- **[judgment]** `rdqm-rhel` is now `supported: [rhel:9, rhel:10]`, default
  `rhel:9`. `rhel:10` needs an x86-64-v3 host, as for `nativeha-rhel-crr`. The
  default stays on RHEL 9 until a human decides otherwise after the validation
  row below passes. The generated box is `mq-rdqm-rhel10` (role `mq-rdqm`,
  bake `bake-mq-rdqm.yml`, the same playbook as RHEL 9).
- **[data] (local)** `rdqm-install`'s `vars/RedHat-10.yml` uses
  `el10/kmod-drbd-9`, `el10/pacemaker-3` and `el10/drbd-utils-9` (check 1).
  Its vendored known-issue list is empty, because the kernel-modules page has
  no RHEL 10 row in its "known compatibility issues" table (re-fetched
  2026-10-13: RHEL7 and RHEL8 "None", RHEL9 `5.14.0-570.12.1 -> 5.14.0-570.15.1`).
  (S-6)
- **[data] (local) The RHEL 10.2 DVD satisfies the el10 install set offline.**
  Read-only check on 2026-10-13: the RPM headers of everything the role's two
  `dnf install` steps hand to dnf (the nine `MQSeries*` server rpms, all of
  `el10/pacemaker-3/` and `el10/drbd-utils-9/`, the `_6.12.0_211.7.1` kmod
  and `MQSeriesRDQM`, 31 rpms) were resolved against the DVD's `BaseOS` and
  `AppStream` repodata (`primary.xml` provides and `filelists.xml` paths,
  x86_64 and noarch only, 5,630 packages; `.treeinfo` "Red Hat Enterprise
  Linux 10.2"). Every `Requires` resolves to the install set itself or to the
  DVD, versioned requires included. The DVD packages this pulls in (248, by
  transitive closure over their own `Requires`) also resolve with nothing
  missing. Two results matter for Pacemaker 3:
  - The kmod's `kernel(<symbol>) = <hash>` kABI requires all match the DVD's
    `kernel-core-6.12.0-211.7.3.el10_2`.
  - `el10/drbd-utils-9/` ships no `ktls-utils` rpm (el8 and el9 do), but
    `MQSeriesRDQM` requires `ktls-utils >= 0.11`. The DVD's BaseOS supplies
    `ktls-utils-1.2.1-3.el10`, so the step-2 `dnf install` pulls it in.
    Other DVD packages the bundle needs include `python3-lxml`, `python3-pyyaml`,
    `python3-dateutil` and `python3-packaging` (crmsh), `perl-TimeDate` and
    `psmisc` (pacemaker-cli), `net-snmp-libs` and `libxslt` (corosync),
    `net-tools` and `nfs-utils` (resource-agents), and `selinux-policy`
    `42.1.18` (drbd-selinux needs `>= 40.13.26`).
- **[data] (local)** The DVD's AppStream also carries Red Hat builds of
  `corosynclib` (3.1.10), `libqb`, `pacemaker-libs`, `pacemaker-cluster-libs`
  and `pacemaker-schemas` (3.0.1-5). The role installs the LINBIT rpms as
  local files, so dnf takes those (and IBM's pre-install check, below,
  requires the LINBIT builds). The base box's kickstart installs only
  `@^minimal-environment`, `openssh-server` and `lvm2`, so none of the Red Hat
  builds are already present. `pacemaker-libs` conflicts with `sbd`, which the
  role never installs.
- **[judgment] Limits of that check.** It is a static resolution, not a dnf
  transaction: it does not run scriptlets, weigh conflicts beyond the ones
  listed, or pick between alternative providers the way dnf would. It shows
  that nothing is missing from the DVD. The bake
  ([#1442](https://github.com/logical-minds-foundry/mq-resiliency-lab-for-linux/issues/1442))
  is the real dnf transaction.
- **[data] (local) Pacemaker 3 touch points.** The lab drives RDQM through
  IBM's own commands (`rdqmadm`, `rdqmstatus`, `rdqmdr`, `crtmqm -sx`). Its only
  direct Pacemaker calls are `crm_mon --one-shot --output-as=xml` (the RDQM
  state collector) and `pacemakerd --version` (version gathering). The
  collector uses the current `--output-as=xml` flag, not the removed
  `--as-xml`, and the elements it reads (`nodes/node`, `resources//resource`,
  `node_history`) are still in the 3.0.1 `crm_mon` schemas. Its role map
  already accepts both `Master`/`Slave` and `Promoted`/`Unpromoted`. The
  3.0.1 `pacemaker-libs` scriptlet still creates the `haclient` group, which
  `rdqm-install` adds `mqm` to. On RHEL 10, `MQSeriesRDQM-10.0.1`'s pre-install
  scriptlet checks for exactly this set: `pacemaker*` 3.0.1, `crmsh` 4.6.2,
  `corosync` 3.1.9, `libknet1` 1.30, `libqb` 2.0.8, `resource-agents` 4.17.0
  and `python3-pacemaker` 3.0.1 (in place of el9's `python3-parallax`). It
  also fails unless each one is a LINBIT build (`linbit` in the rpm version).
  So the Red Hat builds on the DVD would be refused even if dnf picked them,
  and the role's local-file install of the LINBIT rpms is what IBM expects.
  Statically, no role or script needed a Pacemaker 3 change beyond
  `pacemaker_dir`. The first live run found one more change, described next.
- **[data] crmsh needs an undeclared `distutils` on RHEL 10 (#1484).** The
  first live `rdqm-rhel@10` provision (validation #1475, 2026-10-13) failed in
  `rdqmadm -c`, with AMQ3890E then AMQ3875E. `RDQM.0.LOG` showed it polling
  `crm status bynode`, and every call failed with
  `No module named 'distutils'`. The bundled
  `crmsh-4.6.2.linbit-1.0.117.721d6d2a.el10` imports `distutils` (in
  `crmsh/utils.py` and `crmsh/ra.py`), which RHEL 10's python3 (3.12) no
  longer has, and the rpm declares no dependency that provides it.
  Corosync, Pacemaker and the `modver`-chosen DRBD kmod were all healthy.
  `rdqm-install` now installs `python3-setuptools` (its `distutils` shim) from
  the DVD on el10 through `rdqm_os_extra_packages`. It also checks that
  `crm --version` succeeds right after the RDQM install, so a missing
  dependency fails there, at install time. The upstream report is tracked in
  [.github#337](https://github.com/logical-minds-foundry/.github/issues/337).
- **[data] RHEL 10's SELinux policy blocks RDQM's DRBD handlers (#1486).**
  The lab's single RHEL kickstart has run SELinux **enforcing** on RHEL 9 and
  10 since it was written. With #1484 in place, `rdqm-rhel@10` provisioned,
  but RDQM never recorded its initial sync. On both sites `rdqmstatus` showed
  DR "Waiting for initial connection", and `rdqmadm -s` was refused with
  AMQ6526E. DRBD itself was healthy throughout: `Connected`, `Established`,
  `UpToDate`.
  - **Cause:** DRBD runs RDQM's handlers (`/var/mqm/rdqm/<hook>` →
    `/opt/mqm/bin/rdqmhandler`) through the kernel's usermode helper, and every
    one exited 255. The AVCs show that `kernel_t` may not `execute_no_trans`
    `drbdadm` (`drbd_exec_t`) or `/bin/sh` (`shell_exec_t`). With that allowed,
    the handler lands in the confined `kernel_generic_helper_t`, where it needs
    97 allow rules (221 permissions) across its lifecycle.
  - **Not the DRBD module:** IBM ships the same `drbd-selinux-9.33.1` for el9 and
    el10, and the two compiled modules reference the same types.
  - **[judgment]** The change is RHEL 10's base policy (`selinux-policy`
    42.1.18) confining kernel usermode helpers that RHEL 9's policy let run.
    This is inferred from RHEL 9 RDQM working under the same enforcing
    kickstart.
  - **Hang hazard:** a handler blocked part-way can hang, wedging the DR resource
    in `Disconnecting`.
  - **The fix:** `rdqm-install` loads a small, generic CIL module,
    `files/rdqm_drbd_helper.cil`. It lets the kernel run `drbdadm` and its shell
    in place, and gives `rdqmhandler` its own entrypoint type,
    `rdqm_handler_exec_t`. Executing the handler transitions into
    `unconfined_service_t`, so only IBM's handler leaves confinement.
  - **Proven under full enforcing:** every handler exits 0; HA failover works
    (RDQMAPP moved to another node in about 15 s); DR cutover (18.3 s) and
    failback (18.8 s) work; and no AVC is logged on any of the six nodes.
  - **Upstream:** the module is offered to IBM through
    [.github#337](https://github.com/logical-minds-foundry/.github/issues/337).
- **[judgment] Still to prove, live.** The `mq-rdqm-rhel10` bake (#1442) and
  the cold-rebuild row `rdqm-rhel@10` in the MQ 10.0.1 validation matrix
  ([#1443](https://github.com/logical-minds-foundry/mq-resiliency-lab-for-linux/issues/1443))
  are human-operated and have **not run yet**. The row must:
  - bring up `rdqm-rhel` with `os: rhel:10` from a cold rebuild in one pass;
  - show the install-time kmod choice in the bake log (the `modver` task
    output);
  - prove RDQM HA failover and DR;
  - capture a live Pacemaker 3 `crm_mon` XML and `rdqmstatus -m` against the
    collector's fixtures.

  Until then `rdqm-rhel@10` is selectable but unproven. Under the epic's gate
  policy, a failure there that affects only RDQM or Pacemaker is narrowed or
  deferred and never blocks Native HA.

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
`ansible/roles/drbd-san/tasks/install.yml` and
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
  base modules package instead. `drbd-san` installs the kernel-modules package
  named in its per-version vars file (since #1278, `vars/Ubuntu-24.yml` names
  `linux-modules-extra-{{ ansible_kernel }}`), so T8 adds a `vars/Ubuntu-26.yml`
  that names `linux-modules-<kver>`, which the box already carries. `pacemaker` moves a major version (2.1 → 3.0) and `pcs` moves
  0.11 → 0.12. Expect role fix-ups there too. T8/V2 must prove this; it is not
  established here.
- **[data] Live guest check (T0b, [#1272](https://github.com/logical-minds-foundry/mq-resiliency-lab-for-linux/issues/1272)).**
  In a booted `cloud-image/ubuntu-26.04` `20260927.0.0` guest (Ubuntu
  26.04.1 LTS, kernel `7.0.0-34-generic`), after `apt-get update`,
  `apt-cache policy` gave the same candidates on arm64
  ([#1290](https://github.com/logical-minds-foundry/mq-resiliency-lab-for-linux/issues/1290))
  and amd64
  ([#1291](https://github.com/logical-minds-foundry/mq-resiliency-lab-for-linux/issues/1291)).
  They agree with the archive metadata above:

  | Package | Installed | Candidate (arm64 and amd64) |
  |---|---|---|
  | `pacemaker` | (none) | `3.0.1-1ubuntu2` |
  | `pcs` | (none) | `0.12.1-2ubuntu3` |
  | `fence-agents-virsh` | (none) | `4.17.0-1ubuntu1` |
  | `drbd-utils` | (none) | `9.22.0-1.2build1` |
  | `targetcli-fb` | (none) | `1:3.0.1-0.1build1` |
  | `linux-modules-extra-7.0.0-34-generic` | no output (unknown to apt) | no output (unknown to apt) |

  The absence of `linux-modules-extra` is deliberate on Canonical's side: the
  kernel team deprecated it from the 6.15 development kernel (25.10 cycle), and
  all modules now ship in `linux-modules`
  ([Ubuntu Community Hub, 2025-07-28](https://discourse.ubuntu.com/t/kernel-development-release-cadence-and-deprecation-of-linux-modules-extra/65176)).
  An offline `modinfo` of the amd64 `linux-modules-7.0.0-34-generic` package
  shows in-tree DRBD **8.4.11** and the LIO target modules. A load check inside a
  booted guest, on both architectures, is left to T6/T8. Full detail:
  [`2026-10-os-axis-spike.md`](../reports/2026-10-os-axis-spike.md) §2.

## 4. Defaults decision (spec §4.1)

Rule (spec §4.1): **a stack's default may point at a version only if that version
is cited as IBM-`supported`.** An unsupported version still ships as
selectable lab-only. It carries `ibm_support: { status: unsupported, source: <url> }`,
and the resolver warns when it is selected.

| Stack | Spec goal default | Gating cell | Decision |
|---|---|---|---|
| `nativeha-ubuntu` | `ubuntu:26` | Ubuntu 26.04 × Native HA = unsupported (not listed) | **Default stays `ubuntu:24`.** `ubuntu:26` ships lab-only with `ibm_support: unsupported`. T9 does not flip. |
| `pcmk-ubuntu` | `ubuntu:26` | Ubuntu 26.04 × MQ server = unsupported (not listed) | **Default stays `ubuntu:24`.** `ubuntu:26` ships lab-only with `ibm_support: unsupported`. T9 does not flip. |
| `nativeha-rhel-crr` | `rhel:10` | RHEL 10 × Native HA = supported | **Flipped to `rhel:10`** (T11, [#1289](https://github.com/logical-minds-foundry/mq-resiliency-lab-for-linux/issues/1289)) after V3 proved the build ([#1288](https://github.com/logical-minds-foundry/mq-resiliency-lab-for-linux/issues/1288): cold full-DR bootstrap in one pass). Pin point `10.2`. A host without x86-64-v3 is refused and pointed at `os: rhel:9`. |
| `rdqm-rhel` | `rhel:9` | RHEL 10 × RDQM = unsupported in 10.0.0.x; supported from 10.0.1 (§2.4) | **Stays `rhel:9`; `rhel:10` is selectable** ([#1440](https://github.com/logical-minds-foundry/mq-resiliency-lab-for-linux/issues/1440)) on the 10.0.1.0 pin ([#1409](https://github.com/logical-minds-foundry/mq-resiliency-lab-for-linux/issues/1409)), which passes the §2.4 media checks. `rhel:10` needs an x86-64-v3 host. The default flips only on a human decision after the cold-rebuild row in [#1443](https://github.com/logical-minds-foundry/mq-resiliency-lab-for-linux/issues/1443) passes. |

- **[judgment]** For both Ubuntu stacks, T9's "if IBM-supported" condition is
  **not met** today. T9 should re-check S-1 when it runs. If IBM has added an
  Ubuntu 26.04 row that lists the MQ Queue Manager (and Native HA, for
  `nativeha-ubuntu`), the flip becomes permitted. Cite the new row when that
  happens. §6.1 gives the rule and the method for checking.
- **[judgment] (flag for the epic)** §4.1 gates *stack* defaults only.
  `infra: ubuntu:26` (spec goal 2) moves the shared Ubuntu MQ commons
  (`svc-sim`, `app-client`, `mon-probe`) to 26.04. Those nodes run the MQ server
  and client (the `mq-client-ubuntu24` box bakes them in, per `lab/topology.yaml`), so
  MQ would run on an OS IBM does not list. §4.1 neither covers nor forbids this.
  The epic should decide it explicitly before T8 moves infra to 26.
  **Resolved** ([.github#285](https://github.com/logical-minds-foundry/.github/issues/285)):
  the catalog splits the shared nodes. `infra:` (`ubuntu:26`) covers only the
  nodes that run no MQ (`infra`, `obs`, `san`). A separate `infra_mq:` key covers
  the MQ commons, stays `ubuntu:24`, and is support-gated like a stack default,
  so MQ never runs on 26.04 by default.
- **[data] (side observation, outside this epic's scope)** The MQ 10.0 SPCR lists
  only `x86-64`, `IBM Z and IBM LinuxONE` and `POWER System - Little Endian`
  hardware for its Linux rows. There is no ARM64 row. The lab's arm64 (Apple
  Silicon) Ubuntu builds therefore run MQ on a hardware platform the SPCR does not
  list, on 24.04 as well as on 26.04. (S-1) §5 covers what IBM does offer on
  ARM64.

## 5. ARM64 (aarch64) support

> Added by T0b
> ([#1272](https://github.com/logical-minds-foundry/mq-resiliency-lab-for-linux/issues/1272))
> as follow-up research. Retrieved 2026-10-03. The IBM Support pages below were
> fetched with a browser user-agent, outside `tools/ibm_doc_cache.py`; re-check
> them in a browser.

**Summary.** IBM MQ on Linux ARM64 (aarch64) is available only as IBM MQ
Advanced for Developers: the `UbuntuLinuxARM64` deb tarball since 9.4.1, and a
non-install ARM64 image for building your own development container since
9.4.0. Both server and client are included. IBM licenses it as Non-Production
and Non-Warranted, and describes it as "not suitable for production use …
experimental … with no formal IBM support". MQTT and AMQP are unavailable.
There is no production ARM64 eImage, no ARM64 redistributable client and no
prebuilt ARM64 container, so ARM64 does not appear in the SPCR. Supported
production Linux architectures for MQ 10.0 are x86-64, POWER LE and IBM Z. The
lab's ARM64 (Apple Silicon) stacks run natively under the Developer licence:
representative, but unsupported. Production-support-dependent results, such as
RDQM ("only supported on RHEL x86-64"), must be confirmed on x86-64.

The summary combines the claims below. Each is tagged on its own.

- **[data]** The IBM Docs MQ 10.0 downloads page lists "Linux ARM64 (deb)" under
  Development only, and the ARM64 non-install image "For development
  environments".
  <https://www.ibm.com/docs/en/ibm-mq/10.0.x?topic=information-mq-downloads>
- **[data]** The "Downloading IBM MQ 10.0 LTS" production eImages are Windows,
  Linux LE Power/Z/x86-64 and Ubuntu LE Power/Z/x86-64. None is ARM64.
  <https://www.ibm.com/support/pages/downloading-ibm-mq-100-lts>
- **[data]** IBM Developer tutorial "IBM MQ on ARM64" (2026-09-10): "not
  suitable for production use … an experimental offering that is provided as is
  with no formal IBM support".
  <https://developer.ibm.com/tutorials/mq-connect-app-queue-manager-arm64/>
- **[data]** IBM Community blog (R. Coppen, 2024-11-21): new in 9.4.1, ARM64
  Ubuntu `.deb` packages ship as part of MQ Advanced for Developers; MQTT and
  AMQP are not available.
  <https://community.ibm.com/community/user/blogs/richard-coppen/2024/11/21/ibm-mq-developer-edition-now-available-for-arm64-u>
- **[data]** Redistributable clients are "provided for Linux® x86-64 and Windows
  64-bit platforms" only.
  <https://www.ibm.com/docs/en/ibm-mq/10.0.x?topic=overview-redistributable-mq-clients>
- **[data]** The prebuilt Developer container image is published for
  amd64/s390x/ppc64le and is "not supported"
  (<https://www.ibm.com/docs/SSYHRD_10.0.0/container/ctr_support_dev.html>). The
  mq-container build docs say the devserver image builds on amd64 and arm64
  (Apple Silicon)
  (<https://github.com/ibm-messaging/mq-container/blob/master/docs/building.md>).
- **[data]** The MQ 10.0 SPCR's Linux rows list only x86-64, IBM Z and POWER LE
  hardware. (S-1; see §4)
- **[data] (local)** The lab's cached
  `10.0.0.0-IBM-MQ-Advanced-for-Developers-UbuntuLinuxARM64.tar.gz` contains
  `ibmmq-server_10.0.0.0_arm64.deb` plus the runtime, client, SDK, AMS, web,
  Java, JRE, GSKit and FT packages. It has no `ibmmq-xr` or `ibmmq-amqp` deb. Its
  `MQServer/licenses/LI_en.txt` reads "IBM MQ Advanced for Developers
  (Non-Warranted) 10.0 … Non-Production", and `MQServer/READMES/RPi_README.txt`
  says "development purposes only … 'as is' and no support".
- **[data] (local)** The lab runs the ARM64 build natively: boxes are baked per
  host architecture (`*-aarch64.box`), and `platforms.py` selects
  `driver: kvm` whenever the guest architecture equals the host's, so there is no
  emulation.
- **[judgment]** The SPCR omits ARM64 because it lists only supported production
  platforms, and ARM64 is Developer-only.

## 6. When a new Ubuntu LTS becomes IBM-supported

### 6.1 The rule for our gate

- **[judgment] Rule.** For the spec §4.1 defaults gate, a new Ubuntu version
  counts as IBM-`supported` only when a **"Ubuntu \<ver\> LTS" row appears in
  the SPCR for an IBM MQ 10.0.x deliverable** and that row lists the components
  the stack needs (`MQ Queue Manager`, plus `Native HA` for `nativeha-ubuntu`).
  Announcements, Ansible fixes or roadmap talk do not count. Cite the
  deliverable ID and its `lastModified` when the row appears.
- **Method (checkable).** Two public SPCR JSON endpoints, fetched with a browser
  user-agent as in S-1 (§8):
  1. `getDeliverableChain?deliverableId=<id>` names the deliverable and lists its
     fix-pack children. Start from the MQ 10.0 deliverable
     `FA94B1F6888342969E7F9B505149F032`:
     <https://www.ibm.com/software/reports/compatibility/clarity-reports/report/json/getDeliverableChain?deliverableId=FA94B1F6888342969E7F9B505149F032>
  2. `getTSROsSupportSummary?deliverableId=<id>` returns the OS rows for one
     deliverable. Check `osSupportDetails[].os` for the new Ubuntu row, and
     `.serverSupportedComps` for the components. Run it for 10.0 and for each
     newer 10.0.x child the chain lists.
- **[data] Status on 2026-10-03.** The MQ 10.0 SPCR
  (`FA94B1F6888342969E7F9B505149F032`, `lastModified` 2026-09-28) lists only
  "Ubuntu 24.04 LTS" for Ubuntu, on x86-64, POWER LE and Z
  (<https://www.ibm.com/software/reports/compatibility/clarity-reports/report/json/getTSROsSupportSummary?deliverableId=FA94B1F6888342969E7F9B505149F032>).
  The MQ 10.0.0.5 SPCR (`9CA6A0D6BAD147BEBB45A74CAF2CC1B5`, `lastModified`
  2026-10-01) adds SLES 16 at product minimum 10.0.0.5, and Ubuntu is still
  24.04 only
  (<https://www.ibm.com/software/reports/compatibility/clarity-reports/report/json/getTSROsSupportSummary?deliverableId=9CA6A0D6BAD147BEBB45A74CAF2CC1B5>).
- **[data]** The "System Requirements for IBM MQ 10.0" page (modified
  2026-06-16) says nothing about Ubuntu 26.04 or planned support
  (<https://www.ibm.com/support/pages/system-requirements-ibm-mq-100>).
  `ibm-messaging/mq-ansible` PR #145 ("…fix Ubuntu 26.04 compatibility",
  2026-09-09) was closed unmerged; it is an Ansible fix, not a support statement
  (<https://github.com/ibm-messaging/mq-ansible/pull/145>). No IBM announcement,
  roadmap, Ideas entry, blog or release note about MQ on 26.04 was found.

### 6.2 Historical lag from Ubuntu LTS GA to MQ support

- **[data]** First MQ release whose SPCR lists each Ubuntu LTS, from the SPCR
  `productMinimum` chain (`getDeliverableChain`) and IBM fix-list dates. Ubuntu
  GA dates are from <https://changelogs.ubuntu.com/meta-release>.

  | Ubuntu LTS (GA) | First MQ release supporting it (date) | Lag |
  |---|---|---|
  | 20.04 (2020-04-23) | 9.2.0.2 LTS (2021-03-31) | 342 days |
  | 22.04 (2022-04-21) | 9.3.1 CD (2022-10-20); 9.2.0.7 LTS (2022-12-01); 9.3.0.2 LTS (2022-12-21) | 182 / 224 / 244 days |
  | 24.04 (2024-04-25) | 9.3.0.25 LTS (2024-10-29); 9.4.2 CD and 9.4.0.10 LTS (2025-02-27) | 187 / 308 days |
  | 26.04 (2026-04-23) | none yet (as of 2026-10-03) | over 163 days |

  Supporting detail:
  - The MQ 9.3 "What's new in LTS" page says 9.3.0 Fix Pack 2 adds support for
    Ubuntu 22.04:
    <https://www.ibm.com/docs/SSFKSJ_9.3.0/overview/mq_newchgd_lts.html>
  - Ubuntu 24.04 was absent from MQ 9.4.0 (2024-06-18) and 9.4.1 CD
    (2024-10-24). SPCR deliverable IDs: 9.4 `367442DE910A46B0A02768947525804E`,
    9.4.1 `46C32ED25B164C8D8487D9E2044859F7`, 9.3.1
    `30EEDDE07E1011EC93C02AA4DE91E682`, 9.3.0.25
    `D1FF007C04994AF08D4C94AC178E64B9`, 9.2.0.7
    `BF12FB907EB711EC93C02AA4DE91E682`.
  - Fix lists:
    <https://www.ibm.com/support/pages/fix-list-ibm-mq-version-92-lts> (and the
    `-93-lts`, `-94-lts`, `-93x-continuous-delivery` and
    `-94x-continuous-delivery` pages).
- **[judgment] Estimate, not a date.** IBM has historically added a new Ubuntu
  LTS 6 to 11 months after GA. For 26.04 that points to late October 2026
  through March 2027, most likely in the next 10.0.x CD release or fix pack after
  10.0.0.5. IBM has published no date. Apply §6.1 rather than this estimate.

## 7. Human-fetch items

Neither `WebFetch` nor `tools/ibm_doc_cache.py` can retrieve these. Open each in
a browser to confirm or extend the data above. Nothing here paraphrases them.

1. **SPCR, browser view** (the human-readable rendering of S-1; confirm the RHEL 10
   and Ubuntu rows match §2–§3):
   <https://www.ibm.com/software/reports/compatibility/clarity-reports/report/html/softwareReqsForProduct?deliverableId=FA94B1F6888342969E7F9B505149F032&osPlatforms=Linux&duComponentIds=S008>
2. **System Requirements for IBM MQ 10.0** (IBM Support page):
   <https://www.ibm.com/support/pages/system-requirements-ibm-mq-100>
3. **IBM MQ RDQM kernel modules** (watch for a RHEL 10 / kernel `6.12` entry):
   <https://www.ibm.com/support/pages/node/1087143> (linked from SPCR footnote (7)),
   also reachable as <https://ibm.biz/mqrdqmkernelmods>. Since 2026-10-09 a
   browser user-agent `curl` of this page returns the tables, so §2.4 cites it
   directly. Keep the browser check for the rendered view.
4. **IBM MQ support for SELinux** (linked from every SPCR Linux footnote):
   <https://www.ibm.com/support/pages/node/261161>
5. **IBM announcement AD26-0859, MQ 10.0.1** (S-5; JavaScript-rendered, HTTP
   403 to `curl` and `WebFetch`). Confirm the RDQM RHEL 10 line and the
   2026-10-13 date quoted in §2.4:
   <https://www.ibm.com/docs/en/announcements/mq-1001>
6. **10.0.1 results (2026-10-13, #1409).** These were fetched with a browser
   user-agent `curl`. Confirm in a browser:
   - the kernel-modules page's "RHEL 10 kernel versions" table and its "known
     compatibility issues" table (no RHEL 10 row):
     <https://www.ibm.com/support/pages/node/1087143>;
   - "Latest CD Release: IBM MQ 10.0.1" on
     <https://www.ibm.com/support/pages/downloading-ibm-mq-100-cd>;
   - the "Fix list for IBM MQ 10.0.x Continuous Delivery" page, which had not
     been updated for 10.0.1 (modified 2026-09-10):
     <https://www.ibm.com/support/pages/fix-list-ibm-mq-100x-continuous-delivery>
     (also <https://www.ibm.com/support/pages/node/7276511>).
7. **#1440 known-issue copy (2026-10-13).** `rdqm-install`'s
   `vars/RedHat-10.yml` vendors an empty RHEL 10 known-issue list, from a
   browser user-agent `curl` of the kernel-modules page. Confirm in a browser
   that its "Kernel versions with known compatibility issues" table still has
   no RHEL 10 row: <https://www.ibm.com/support/pages/node/1087143>.

## 8. Sources

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
- **S-5: IBM announcement AD26-0859**, "IBM MQ 10.0.1 delivers new AI Agent
  capabilities, expands quantum-safe security, and delivers MQTT 5.0 support"
  (2026-10-06): <https://www.ibm.com/docs/en/announcements/mq-1001>. The text
  was pasted from the rendered page by the human (#1405, 2026-10-09); see §7
  item 5.
- **S-6: IBM MQ RDQM kernel modules.** Support page:
  <https://www.ibm.com/support/pages/node/1087143>. JSON feed:
  <https://public.dhe.ibm.com/ibmdl/export/pub/software/websphere/messaging/mqadv/ibm-mq-rdqm-kmods.json>
  (keys `kernelLevels`, `latestKmodBundleFixIds`,
  `latestKnownCompatibleKernelLevels`, `lastUpdated`). Both were fetched on
  2026-10-10 and again on 2026-10-13 with a browser user-agent.
- **S-7: MQ 10.0 CD releases.**
  <https://www.ibm.com/support/pages/downloading-ibm-mq-100-cd> and the public
  Developer download directory
  <https://public.dhe.ibm.com/ibmdl/export/pub/software/websphere/messaging/mqadv/>,
  both fetched on 2026-10-10 and again on 2026-10-13 with a browser user-agent.
- **Prior art in this repo:**
  [`mq10-rhel9-vs-rhel10-and-rdqm-support.md`](mq10-rhel9-vs-rhel10-and-rdqm-support.md)
  (#1095/#1096). IBM's
  [`ibm-messaging/rdqm-kmod-queries`](https://github.com/ibm-messaging/rdqm-kmod-queries)
  automates the kernel-modules check that §2.2 says to repeat.
