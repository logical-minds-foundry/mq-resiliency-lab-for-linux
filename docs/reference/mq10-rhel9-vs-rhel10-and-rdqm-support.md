# IBM MQ 10.0 on RHEL 9 vs RHEL 10 — and the state of RDQM

> Scope: whether moving the lab's base OS from RHEL 9 to the latest stable
> RHEL 10 buys anything **MQ-specific** now that the lab is rebaselined to IBM
> MQ 10.0 (#1085), and what IBM's **documented** support posture is for RDQM
> under MQ 10.0 — on RHEL 10 in particular. Written to inform the lab's OS and
> HA-strategy decisions, not to prescribe them.
>
> Out of scope: Native HA CRR/IRR design (separate brainstorm). This doc only
> touches CRR/IRR where it bears on the RDQM-vs-Native-HA strategic picture. For
> the arm the lab actually ships (CRR) and the one it evaluated and **deferred**
> (IRR — a 1 Live + 1 Recovery strict-sync topology that is DR *without* HA, not a
> peer of the six-instance CRR arm), see
> [`docs/reports/2026-09-15-mq10-nativeha-irr-setup-facts-spike.md`](../reports/2026-09-15-mq10-nativeha-irr-setup-facts-spike.md).
>
> Convention: each claim is tagged **[data]** (what a cited source actually
> says) or **[judgment]** (our reasoning on top). Numbers and support claims are
> load-bearing — verify against the linked sources before betting on them.
>
> Sources: IBM MQ 10.0 documentation and system requirements, the IBM MQ RDQM
> kernel-modules list, and Red Hat's RHEL 10 documentation and lifecycle policy,
> all cited inline and dated (as of 2026-09-15).

## 1. Bottom line

- **[judgment]** There is **no IBM-published, MQ-specific advantage** to running
  MQ 10.0 on RHEL 10 rather than RHEL 9. The real advantages are generic OS
  modernization (support-lifecycle runway, newer kernel), not anything MQ
  exploits.
- **[data]** RDQM is **fully supported** in MQ 10.0 — it is absent from the MQ
  10.0 deprecations/stabilizations/removals list — but IBM documents **Native HA
  as the "primary" and "preferred"** Linux HA solution, explicitly on the
  grounds that RDQM depends on the OS kernel.
- **[data]** RDQM has **no validated DRBD kernel module for RHEL 10** (kernel
  6.12). IBM ships validated modules for RHEL 7/8/9 only, and publishes no
  statement committing RDQM to RHEL 10 (nor one ruling it out).
- **[judgment]** Therefore: **do not blanket-migrate the base OS to RHEL 10
  while the RDQM arm is in scope** — RDQMs will not start without a matching
  DRBD module. Native HA is kernel-independent and is unaffected by the RHEL
  9-vs-10 question.

## 2. Does MQ 10.0 support RHEL 10 as a base OS?

- **[data]** MQ 10.0 LTS is generally available from 16 June 2026 (19 June for
  z/OS). ([Introducing IBM MQ v10.0](https://www.ibm.com/new/announcements/introducing-ibm-mq-v10-0))
- **[data]** The MQ 10.0 system-requirements page states defect support "is
  available for Linux environments that are fully compatible, both source and
  binary, with Red Hat Enterprise Linux 9 or 10" — but adds "IBM MQ has not been
  tested in such compatible environments." That sentence is about RHEL-*compatible*
  rebuilds (Rocky/Alma/Oracle), not RHEL itself.
  ([System Requirements for IBM MQ 10.0](https://www.ibm.com/support/pages/system-requirements-ibm-mq-100))
- **[judgment / open item]** The enumerated *tested* base-QM platform list lives
  in the JavaScript-rendered Software Product Compatibility Report, which could
  not be captured by our doc tooling. RHEL 9 and RHEL 10 as tested base-QM
  platforms is the realistic expectation, but **confirm the RHEL 10 "tested"
  status by opening the SPCR in a browser** before treating it as fact:
  [MQ 10.0 detailed system requirements (Linux)](https://www.ibm.com/software/reports/compatibility/clarity-reports/report/html/softwareReqsForProduct?deliverableId=FA94B1F6888342969E7F9B505149F032&osPlatforms=Linux&duComponentIds=S008).

## 3. RDQM on RHEL 10 — the load-bearing constraint for this lab

- **[data]** RDQM depends on a DRBD **kernel module** that IBM validates per RHEL
  kernel and ships in the MQ image / via Fix Central. IBM: "If you upgrade to a
  newer RHEL kernel … you might need to upgrade the RDQM kernel module … Failure
  to upgrade the RDQM kernel module … results in a failure to start the
  Replicated Data Queue Managers."
  ([RDQM kernel modules](https://www.ibm.com/support/pages/ibm-mq-replicated-data-queue-manager-kernel-modules) ·
  [ibm.biz/mqrdqmkernelmods](https://ibm.biz/mqrdqmkernelmods))
- **[data]** As of 2026-09-15 the validated-module list covers **RHEL 7, 8, and 9
  only**. Latest validated combinations:

  | RHEL | Latest validated kernel | MQ range | DRBD |
  |------|-------------------------|----------|------|
  | 8 | `4.18.0-553.158.1` | 9.2.0 – 10.0.0 | 9.2.15 – 9.3.1 |
  | 9 | `5.14.0-687.46.1` | 9.2.0 – 10.0.0 | 9.2.15 – 9.3.1 |
  | 10 (kernel 6.12) | **not listed** | — | — |

- **[data]** IBM's mechanism is to ship new modules per new kernel *as needed*,
  via point APARs (e.g. [IT47245](https://www.ibm.com/support/pages/apar/IT47245),
  [IT44926](https://www.ibm.com/support/pages/apar/IT44926)). No RHEL 10 APAR or
  module exists yet.
- **[judgment]** Absence of a RHEL 10 module today is **not** evidence of intent
  to drop RDQM — it is equally consistent with "RHEL 10 validation simply hasn't
  happened yet" (RHEL 10 is ~16 months old; MQ 10.0 LTS GA'd June 2026). But IBM
  has made **no commitment** that RDQM will ever be validated on RHEL 10. Plan as
  if RDQM-on-RHEL-10 is unavailable and uncommitted.

## 4. Is RDQM being deprecated or dropped in MQ 10.0?

- **[data]** No. RDQM (and DRBD, and Pacemaker) appear **nowhere** in the MQ 10.0
  "Deprecations, stabilizations, and removals" list.
  ([Deprecations, stabilizations, removals in MQ 10.0](https://www.ibm.com/docs/en/ibm-mq/10.0.x?topic=information-deprecations-stabilizations-removals-in-mq))
- **[data]** The MQ 10.0 HA/DR configuration page states, verbatim
  ([Configuring high availability and disaster recovery, MQ 10.0](https://www.ibm.com/docs/en/ibm-mq/10.0.x?topic=configuring-high-availability-disaster-recovery)):
  - "The primary high availability option is known as 'native HA' … It is the
    preferred HA solution because it has no dependencies on external components
    such as shared storage or operating system features."
  - "Since IBM MQ 9.4.4, Native HA has been made available on Linux and is the
    preferred solution to RDQM in most circumstances because it does not have
    dependencies on the underlying operating system kernel. **However, RDQM
    continues to be fully supported.**"
- **[judgment]** IBM's direction is unambiguous: Native HA is the strategic,
  kernel-independent Linux HA solution, now extended from containers to VM/bare
  metal (9.4.4). That is a clear **de-emphasis** of RDQM — but it is **not** a
  deprecation, stabilization, or end-of-support statement. RDQM remains fully
  supported today. The lab's Native-HA-first posture aligns with IBM's stated
  strategic direction.

## 5. Security: MQ's post-quantum TLS comes from MQ, not the OS

This is the crux of "can MQ 10 take advantage of RHEL 10's features?"

- **[data]** MQ 10.0 adds "Support for FIPS 203 Quantum Safe ML-KEM key shares
  for TLS" and upgrades its bundled crypto library to **GSKit 9**.
  ([What's new in MQ 10.0.0](https://www.ibm.com/docs/en/ibm-mq/10.0.x?topic=mq-whats-new-changed-in-1000))
- **[data]** On Linux, MQ channel TLS uses the **bundled GSKit**, not the OS
  OpenSSL. ([MQ Linux hardware/software requirements](https://www.ibm.com/docs/pl/ibm-mq/9.3.x?topic=linux-hardware-software-requirements-systems))
- **[data]** RHEL 10's post-quantum story is at the **OS OpenSSL 3.5 /
  crypto-policy** layer, enabled by default from RHEL 10.1 for apps that use
  OpenSSL/GnuTLS/NSS/Go.
  ([PQC in RHEL 10.1](https://www.redhat.com/en/blog/whats-new-post-quantum-cryptography-rhel-101) ·
  [PQC in RHEL 10](https://www.redhat.com/en/blog/post-quantum-cryptography-red-hat-enterprise-linux-10))
- **[judgment]** MQ's queue-manager and client TLS goes through GSKit 9, so it
  does **not** inherit RHEL 10's OpenSSL PQC — and does not need to, because
  GSKit 9 gives MQ its own ML-KEM quantum-safe TLS **on RHEL 9 as well**. RHEL
  10's default PQC still hardens **non-MQ** host traffic (SSH, host TLS, other
  services), but that is a host-hardening benefit, not an MQ-channel one. So the
  headline RHEL 10 security feature is largely redundant for MQ itself.

## 6. Generic RHEL 10 advantages (real, but not MQ-specific)

- **[data]** Support runway: RHEL 9 Full Support ends 31 May 2027 and
  Maintenance Support ends 31 May 2032; RHEL 10 (GA 20 May 2025) runs its
  lifecycle to ~2035. ([Red Hat Enterprise Linux Life Cycle](https://access.redhat.com/support/policy/updates/errata))
- **[data]** RHEL 10 ships kernel **6.12** (RHEL 9 is 5.14), with newer hardware
  enablement, image mode (bootc), and refreshed userspace.
  ([RHEL 10 release notes](https://docs.redhat.com/en/documentation/red_hat_enterprise_linux/10/html-single/10.0_release_notes/index) ·
  [RHEL 10 datasheet](https://www.redhat.com/en/resources/new-in-enterprise-linux-10-datasheet))
- **[data / constraint]** RHEL 10 raises the ISA baseline to **x86-64-v3**;
  x86-64-v1/v2 CPUs are unsupported and may not boot RHEL 10.
  ([x86-64-v3 required by RHEL 10](https://access.redhat.com/solutions/7066628))
- **[judgment]** These are real but generic. IBM publishes no claim that MQ 10
  exploits a specific RHEL 10 kernel/feature for measurable gain, and MQ ships as
  prebuilt binaries targeting a baseline covering both RHEL 9 and 10 — so MQ
  itself won't run faster on RHEL 10 from the OS toolchain. The support-runway
  argument is the most defensible reason to prefer RHEL 10 for a lab meant to
  demonstrate currency and longevity. The x86-64-v3 baseline is a non-issue for a
  modern VM host but worth a one-line check against the lab's hardware.

## 7. Recommendation for the lab

1. **[judgment]** Do not do a blanket RHEL 9→10 base bump while RDQM is in
   scope — it breaks the RDQM arm for no MQ-specific gain.
2. **[judgment]** If RHEL 10 exposure is wanted now, the clean split is: run the
   **kernel-module-free Native HA arms on RHEL 10** and **keep the RDQM arm on
   RHEL 9** — weighed against the cost of a heterogeneous lab.
3. **[judgment]** Track the RDQM-on-RHEL-10 gap as the gating item; automate the
   check via [`ibm-messaging/rdqm-kmod-queries`](https://github.com/ibm-messaging/rdqm-kmod-queries)
   against [ibm.biz/mqrdqmkernelmods](https://ibm.biz/mqrdqmkernelmods). A `6.12.x`
   RHEL 10 entry is the signal that a full base-OS move becomes viable.
4. **[judgment]** Do not cite RHEL 10 PQC as an MQ security win — MQ's
   quantum-safe TLS is GSKit 9, delivered by MQ 10 on RHEL 9 too.

## 8. Open items to verify

- Confirm the **base-QM RHEL 10 "tested" status** in the MQ 10.0 SPCR (JS-rendered;
  open in a browser). See §2.
- Confirm whether IBM later publishes a **RHEL 10 RDQM kernel module** — re-check
  the kernel-modules list periodically. See §3.
