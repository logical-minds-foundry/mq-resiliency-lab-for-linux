# Boot-layer flakiness diagnosis — IP-lease timeouts & DNS `no route to host`

- **Date:** 2026-09-20
- **Epic:** `logical-minds-foundry/.github#249` (macOS/arm64 startup reliability)
- **Task:** #1164, plan Task 6 (diagnose) → Task 7 (fix)
- **Spec grounding:** `epics/249-startup-reliability/spec.md` §4.4 (boot & DNS layer)
- **Method:** static code analysis + the symptoms recorded in the epic spec. **No
  live boot was run for this note** — the lab is human-operated (repo doctrine), so
  no failure rate is measured here. The reproducibility proof (≥3 clean cold boots,
  then VAL-A's 5/5) is deferred to the human / VAL-A (#1154). This diagnosis
  attributes each failure mode to a concrete code path so the Task-7 fix is
  evidence-driven, not a guess.

Throughout, **Data** = what the code / spec actually says (checkable at the cited
`file:line`); **Judgment** = the reasoning built on top of it.

---

## 1. Symptoms (from the spec)

The epic spec §1 / §4.4 records two independent, intermittent boot-layer failures of
a cold `mqlab bootstrap nativeha-ubuntu --no-dr` on the Apple-silicon host:

1. **VM boot IP-lease timeout** — `Fog::Errors::TimeoutError` during the concurrent
   batch boot (the `vms` phase).
2. **infra-client DNS `no route to host`** — a dependent races the DNS node.

Neither is a resource shortage (spec §1: the macOS host has more aggregate resources
than the x86 cloud, which boots cleanly); the constraint is the nested-virt tax
compounded by concurrency, and — for these two — the absence of any boot-layer
retry or DNS-first ordering.

---

## 2. Failure mode A — the IP-lease `Fog::Errors::TimeoutError`

### 2.1 What times out (Data)

- The lab's modeled NICs are **DHCP-disabled**: every `private_network` is declared
  `:libvirt__dhcp_enabled => false` (`lab/Vagrantfile:66`). So the timeout is **not**
  a lab-NIC lease.
- The error originates in `vagrant-libvirt` / `fog-libvirt` waiting on the **default
  management-network DHCP lease** during `vagrant up` (spec §4.4). That base
  management NIC (Vagrant's own SSH channel) is the one NIC that *does* DHCP, and its
  lease acquisition is what `Fog` blocks on.
- On macOS/arm64 the guests run under **TCG** (emulated), not KVM: `platforms.py`
  resolves a non-native guest to `driver="qemu"`, `cpu_mode=CPU_TCG`, and a
  `boot_timeout` of `TCG_BOOT_TIMEOUT = 1800` s (`platforms.py:28,106`). TCG boots are
  slow, so the window in which a guest is mid-boot and its management lease is
  outstanding is long.
- The only throttle today is the batch size: `boot_batch: 4` (`lab/topology.yaml:135`),
  applied by `_boot_batch` / `_batch_guests` (`phases.py` vms section) — one
  `vagrant up <batch>` per contiguous group of ≤ 4 guests. A batch whose guests share
  a baked box volume is additionally forced serial with `--no-parallel`
  (`_batch_shares_box`, `phases.py`), but a batch of *distinct*-box guests still boots
  all 4 in parallel.

### 2.2 Why it fires, and why it aborts the whole run (Data + Judgment)

- **Data:** there is **no boot/lease retry anywhere**. The orchestrator fails loud on
  the first non-zero `vagrant up` exit (`orchestrator.py` `run_steps` → `StepFailedError`;
  the module docstring is explicit: "Fail loud on any non-zero exit"). A single
  transient lease timeout therefore aborts the entire bootstrap with no re-attempt.
- **Judgment:** four heavy TCG guests booting at once each contend for the host and
  each hold a management-NIC lease request open; under that contention `fog-libvirt`'s
  bounded lease wait can lapse for one guest while its siblings are still coming up.
  The failure is **transient** — a re-run of the same `vagrant up <batch>` (which is
  idempotent: already-up guests are skipped) almost always clears it. The current
  fail-loud-on-first-exit contract turns a transient, self-clearing condition into a
  hard bootstrap abort. This is the single highest-leverage fix.

---

## 3. Failure mode B — the DNS `no route to host` race

### 3.1 The resolver topology (Data)

- Every guest's **sole** resolver is its org's infra node: `host-resolver`
  (`ansible/roles/host-resolver/tasks/main.yml`) points each guest's systemd-resolved
  (Ubuntu) / NetworkManager global-dns (RHEL) at the infra node and leaves only the
  guest's **own** FQDN in `/etc/hosts` as a fallback ("self lab FQDN in /etc/hosts",
  same file). There is no secondary resolver.
- The DNS nodes are `infra-client` (our estate, `client.com`) and `infra-svc` (the
  mock `service.com`), both on platform `infra-ubuntu2404` and both in the `infra`
  group (`lab/topology.yaml:178-189`, groups `:382`). `infra-client` is a **heavy
  multi-NIC guest** — 4 NICs: net-mgmt + net-data-a + net-data-b + net-ext
  (`topology.yaml:181-182`).

### 3.2 The ordering defect (Data)

- The boot order is `all_vms` = stack **members first, then commons**
  (`phases.py` `all_vms`), and the commons order is
  `[obs_box, probe, svc, app, infra]` (`lab/topology.yaml:503`). `_commons_members`
  walks the groups in that listed order (`phases.py`), so **infra is appended at the
  very tail** — after every stack member *and* after obs/probe/svc/app.
- Net effect: `infra-client`, the resolver every other guest depends on, is the
  **last** thing to boot. Its dependents boot before it.

### 3.3 What actually breaks, and when (Judgment)

The lab zones and resolver drop-ins are configured in the **provision** phase, by
`ansible/site-dns.yml` (run before the stack's own provision playbook,
`phases.py` `_provision_build_steps`), not during the `vms` phase. So at the point
guests are configured to *use* infra, all VMs — including infra — are already booted.
The `no route to host` therefore is not "a guest queried DNS before infra existed at
all"; it is the narrower **settle race**:

- `site-dns.yml` opens with `wait_for_connection` against the `infra` hosts, then a
  BIND configure, then `host-resolver` on `all` (`site-dns.yml:12-72`). If
  `infra-client`, booting **last** and carrying **4 NICs**, is still settling its
  net-mgmt interface when that first infra-facing task reaches it, the connection to
  it is refused / unroutable → `no route to host`. The existing mitigations are all
  **provision-phase, Ansible-level** retries (`site-dns.yml:12-53`
  `wait_for_connection` + `is-system-running`), which help but still start the clock
  the moment provision begins — giving the last-booted, heaviest guest the *least*
  time to settle.

So the diagnosis distinguishes the two candidate causes the plan lists:

- **(a) a dependent resolving before infra's BIND is serving** — largely covered
  already: BIND configure runs inside the provision phase after the infra
  `wait_for_connection`, so a dependent's resolver is only pointed at infra after
  BIND is up. This is **not** the primary residual failure.
- **(b) a still-settling multi-NIC `infra-client` unreachable** — the primary
  residual failure: infra boots last and heaviest, so its multi-NIC settle overlaps
  the earliest provision-phase touch. **This is what infra-first ordering fixes.**

---

## 4. Prescribed fix set (Task 7)

Evidence maps each lever to the failure mode it addresses. Two of the three levers
are prescribed; the third is deliberately **not** taken.

### 4.1 Bounded boot-retry around `vagrant up` — **take** (addresses mode A)

- **Rationale (Judgment):** the lease timeout is transient and self-clearing, and
  `vagrant up` is idempotent, so a bounded retry with backoff converts a hard abort
  into a brief re-attempt. It must stay **bounded and fail-loud after the cap** so it
  never masks a genuine boot/config error (spec §8 risk).
- **Placement (Data doctrine):** the retry **loop** lives in the orchestrator /
  command runner, **not** the pure `phases.py` (spec §3 principle 7;
  `phases.py:7-15` docstring). `phases.py` only *declares* the policy as data on the
  boot step (like its existing `ensure` tuples).
- **Parity:** on the healthy x86 cloud the first attempt succeeds, so retry never
  fires — added resilience, zero behaviour change (spec §4 parity requirement).

### 4.2 Infra-first boot ordering — **take** (addresses mode B, cause b)

- **Rationale (Judgment):** boot the `infra` group **first** so `infra-client` is up,
  and has had the **longest** possible time to settle its 4 NICs, before any
  dependent reaches it in the provision phase. This is the boot-layer complement to
  the existing provision-phase `wait_for_connection` guards — it shrinks the settle
  race at the source instead of only waiting it out.
- **Preserves HADR order:** the reordering is a **stable partition** — infra nodes to
  the front, every other guest keeps its authoritative `all_vms` order (SANs first,
  site-A before site-B). Nothing else is reshuffled.
- **Scope:** expressed as a boot-order-only helper used by the `vms` phase; the other
  `all_vms` consumers (provision/observe `--limit`, box-ensure) are order-independent
  set enumerations and are left calling `all_vms`.

### 4.3 Re-size `boot_batch` — **do NOT take** (no evidence it helps without cost)

- **Rationale (Judgment):** the batch dial is already conservative at 4
  (`topology.yaml:135`), and same-box batches already serialize (`_batch_shares_box`).
  With a bounded boot-retry absorbing the transient lease timeout (§4.1), there is no
  evidence a smaller batch is needed, and shrinking it costs wall-clock on **every**
  boot including the already-reliable x86 cloud (parity risk, spec §8). Changing it
  now would be a guess, not evidence-driven. Leave `boot_batch: 4` as-is; revisit only
  if VAL-A shows the lease pressure persists *after* the retry lands.

---

## 5. Acceptance & deferral

- **Task-7 acceptance:** ≥3 cold boots reach end-of-`vms` with no IP-lease/DNS boot
  failures. This is a **live** proof the human runs (lab is human-operated) and is
  deferred to VAL-A (#1154). The code fixes here are proven by **unit tests** of the
  retry/backoff and the ordering logic (`tests/test_orchestrator.py`,
  `tests/test_phases.py`); `vrg-validate` green is necessary but not sufficient
  (spec §7 — lint-green ≠ done).
- **No live failure rate is asserted in this note** — deliberately, per the
  no-fabrication rule.
