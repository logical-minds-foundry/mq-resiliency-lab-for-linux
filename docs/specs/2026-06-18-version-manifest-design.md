# Version Manifest — Design

**Date:** 2026-06-18
**Issue:** #266
**Status:** Design (brainstormed + pushback-reviewed; pending plan)

## 1. Why this exists

Lab arm builds float to "latest and greatest" for most of the guest stack. That
keeps the lab fresh but makes a build **non-reproducible**: we cannot reliably
stand up a *specific* historical stack on demand. We need that for the work this
lab exists to support:

- **Reproduce problems on the exact stack a real environment runs** — the central
  motivation. A support case on a particular MQ + HA + kernel/OS combination should
  be reproducible here, not approximated.
- **Self-describing test results.** Every published test / integration-test result
  must be prefixed with the configuration it ran against — explicitly including the
  manifest of software versions. A result without its stack coordinates is weak
  evidence.
- **Upgrade-procedure testing.** A primary lab workload is developing the tooling
  for ongoing operations — patching, upgrades, triage, debugging. None of that is
  meaningful without asserting versions: an upgrade test is "from version X to
  version Y," which presupposes we can pin X.

If we cannot assert versions, the lab is weak going forward. This design adds the
ability to assert the versions we care about and to capture the rest.

## 2. Scope & non-goals

**In scope (the controllable subset — Scope "B"):**

- **System-Under-Test (priority 1), in priority order:** IBM MQ → the HA stack →
  kernel / OS. This is the central thing the lab instruments.
- **Observability (priority 2):** the obs stack (prometheus, node_exporter, loki,
  alloy, grafana, the mq-metric-samples exporter). Already pinned-as-step-functions
  in role defaults today; this folds it into the manifest's purview. The obs stack is
  a **shared singleton** (`mqlab obs up` serves every running arm), so it lives in a
  **separate shared manifest**, not the per-arm files (§4).

**Model — drive what we can, discover the rest (no verify engine):**

- We **drive** the versions we can actually choose, and **discover** (capture)
  everything else. There is deliberately **no assert/verify/reconcile engine**: a
  *correctly* pinned release is a static artifact, so its bundled/derived
  dependencies are fixed by definition. If a pin ever produced a different bundle,
  the pin was wrong — not something to paper over with per-dependency verification.

**Out of scope / deferred:**

- **Everything else floats** (toolchain, un-promoted apt packages, …) and is
  captured into the report but not asserted. The pin-list grows incrementally
  ("by step function") as floating-latest causes pain.
- **No verify/reconcile engine.**
- **Upgrade from/to** is deferred but *accommodated* by the structure (§8); V1 is a
  single-stack manifest.
- **Other arms** get a manifest as they are built; V1 ships the arms that exist.

## 3. Architecture — two facets, one orchestrator

`mqlab` is the orchestrator. There are two facets and no verify engine:

- **Drive.** `manifests/<arm>/<name>.yaml` pins the drivable versions. `mqlab` reads
  the selected manifest (default `default.yaml`) and injects it three ways: version
  vars → Ansible (overriding existing role defaults), `box` / `box_version` →
  Vagrant, `mq.version` → tarball selection. The manifest is **also folded into the
  config set hashed for `config_digest`**, so a run report is cryptographically
  bound to the exact pinned stack.
- **Discover.** After provisioning, `mqlab` queries the *running guests* for what
  actually got installed, and `runreport` stamps every result with that complete
  set. Derived/bundled versions (RHEL's HA stack, the drbd kmod) surface here
  automatically — discovered, never asserted.

```
manifests/<arm>/default.yaml          (SUT: MQ / HA / OS — per arm)
manifests/_shared/observability.yaml  (obs stack — read by `mqlab obs up`)
        │  (drive — selection pinned into lab state at `create`, auto-threaded)
        ▼
   mqlab orchestrator ──► Ansible vars overlay (version pins)
        │              ──► Vagrant box / box_version (OS + kernel)
        │              ──► build/mq/<tarball>: acquire (cache → download → fail-if-fetch-fails) + verify .sha256
        │              ──► config_digest (manifest(s) hashed in)
        ▼
   provisioned guests
        │  (discover, post-provision; topology-aware, per component)
        ▼
   runreport.read_versions() ──► report metadata: { manifest id, versions{...}, commit, config_digest }
```

## 4. The manifest

**Location & selection — two namespaces, because the topology has two lifetimes:**

- **Per-arm SUT manifest** — `manifests/<arm>/default.yaml` (MQ / HA / OS). Per-arm
  because pcmk-ubuntu and rdqm-rhel have genuinely different stacks. `default.yaml` is
  the only file to start; named alternates (`manifests/<arm>/repro-case-X.yaml`) are
  added later with zero new machinery — selection resolves `--manifest <name>` →
  `manifests/<arm>/<name>.yaml`, defaulting to `default`.
- **Shared obs manifest** — `manifests/_shared/observability.yaml`. The obs stack is a
  singleton (`mqlab obs up` serves all arms and has no per-arm context), so its pins
  live here and are read by `obs up`, not duplicated per-arm.

**Selection is pinned into lab state at `create` and auto-threaded.** A build is a
sequence — `vm create` → `vm provision` → `qm create` — so the chosen manifest is
recorded into lab state (alongside the existing `clusterstate` / snapshot state) at
`create` and read automatically by the later commands; `--manifest` is supplied once.
Changing it mid-lifecycle is an **explicit error** ("this lab was created against
`repro-945`; destroy + recreate to switch") — never a silent, mismatched stack.

**Shape (Scope B, priority-ordered, drivable-only):**

```yaml
# manifests/distributed-pcmk-ubuntu/default.yaml  — per-arm SUT (MQ / HA / OS)
mq:
  version: "9.4.5.0"                 # (version × the arm's arch) → build/mq/ tarball
os:
  box: cloud-image/ubuntu-24.04
  box_version: "20260518.0.0"        # one pin → OS *and* kernel layer
# HA: derived (RHEL bundled in MQ; Ubuntu apt floats today) → discovered, not pinned
```

```yaml
# manifests/_shared/observability.yaml  — shared obs stack (read by `mqlab obs up`)
prometheus: "2.53.2"
node_exporter: "1.8.2"
loki: "<pinned>"
alloy: "<pinned>"
grafana: "<pinned>"                  # promote from apt-float to a pin
mq_metric_samples_ref: "v5.x.y"      # was 'master' (closes the #172 TODO)
```

**Drivable-only by construction.** Every key is something we can actually set.
Derived versions are deliberately *absent*:

- **RHEL HA stack** (pacemaker / drbd / kmod-drbd) ships *inside* the MQ Advanced
  package → fixed by `mq.version` → discovered, not pinned.
- **drbd kmod** follows the kernel → fixed by `os.box_version` → discovered.
- **Ubuntu HA apt packages** (pacemaker / corosync / pcs) are floatable today;
  promote into the manifest later by step-function if/when latest bites.

## 5. Drive wiring (the blend)

`mqlab` loads the selected manifest(s) and drives each consumer. The obs OSS
components already have `_version` vars to override; the **priority-1 items are not
parameterized today**, so the wiring is real work, not a one-line override:

| Manifest key | Consumer today | Change to make it manifest-driven |
|---|---|---|
| `prometheus` / `loki` / `alloy` / `node_exporter` | role `_version` vars exist | overlay the vars (trivial override) |
| `mq_metric_samples_ref` | `mq_exporter_ref` default | overlay the var (closes #172) |
| `grafana` | apt repo, **no** version var → floats | add apt **version pinning** (real change) |
| `mq.version` | tarball name (version **+ arch**) hardcoded in `mq-install` / `mq-client` / `rdqm-install` | template the filename from `mq.version` × the arm's arch; **acquire** it (§5.1) |
| `os.box_version` | Vagrantfile sets `node.vm.box`, **no** `box_version` | thread `box_version` through `topology.boxes` → Vagrantfile |
| (all) | `mqlab` has **no** `--extra-vars` threading; `ansible-playbook` runs at several `cli.py` sites | add the vars-overlay injection consistently at each site |

Then `mqlab`:

1. **renders a generated vars overlay** (extra-vars or a `build/`-side group_vars
   file) setting the version vars — applied at the arm provision (SUT manifest) and at
   `obs up` (shared obs manifest);
2. **threads `box` / `box_version`** into the topology the Vagrantfile reads, pinning
   OS + kernel;
3. **acquires the MQ tarball** — §5.1;
4. **includes the manifest file(s) in the set hashed for `config_digest`**, binding
   the report to the exact pinned stack.

### 5.1 MQ artifact acquisition (cache → download → fail-only-if-unfetchable)

A pinned `(version × arch)` is a byte-identical, immutable artifact, so it is safely
cacheable. The MQ **Advanced for Developers** tarball is freely downloadable for
**every** arch we use, so `mqlab` resolves `mq.version` to a present tarball by:

- **cache hit** → use the local copy (fast, offline);
- **miss** → download it (and populate the cache if one is configured); **fail loud
  only if the download itself fails**, naming the artifact and where to place it.

The genuinely un-fetchable artifact is the **RHEL OS box** (it needs a Red Hat
developer login) — that is the *OS box* layer's concern (Vagrant + the general cache,
#269), **not** MQ acquisition, which therefore has **no RHEL special-case**.

On acquire, **verify the sibling `.sha256`** (the repo already keeps one next to the
MQ tarball). This is *integrity*, not version-reconciliation — it catches a corrupt or
mislabeled artifact at the cheapest point without reintroducing a verify engine.

The general, cross-cutting artifact cache (boxes + OSS releases too, with a
configurable cache dir and dynamic-download fallback) is tracked separately as
**#269**; this spec owns only the minimal MQ acquire-or-download.

## 6. Discover — capturing the actual stack

Extend `runreport.read_versions()` (today only `vagrant` + `ansible` on the
controller) to capture the real stack from the **running guests** — the only place
the truth lives — as a query pass `mqlab` runs after `provision` / `qm create`.

- **Mechanism:** a **topology-aware** Ansible gather play. Each component is probed
  **only on the group/host it lives on** (the topology already encodes the groups — QM
  nodes, `obs_box`, `probe`, the pcmk nodes), so absence-by-design is never queried.
  `mqlab` aggregates results into the `versions` dict. Keep the existing
  **injectable-reader contract** (`capture_metadata`'s readers; real impls shell out,
  tests inject fakes) and the **"readers MUST raise on failure — a field that silently
  became '' would corrupt the corpus"** rule. That rule applies to a genuine query
  failure on a host where the component is *expected*; an expected absence is not a
  failure (it is simply not probed there) — so the two cases never get conflated.
- **Captured (per relevant guest):**
  - **MQ:** `dspmqver -b`
  - **HA:** `pacemakerd --version`, `corosync -v`, `drbdadm --version` /
    `modinfo drbd` (RHEL-bundled and Ubuntu in-tree both surface)
  - **OS / kernel:** `uname -r`, `/etc/os-release`, the Vagrant box version (from
    `.vagrant/.../box_meta`)
  - **Obs:** prometheus / loki / alloy / node_exporter `--version`,
    `grafana-server -v`, the mq-metric-samples build (binary version + the git sha
    it was built from)

Derived/bundled versions appear automatically: discovered, not pinned.

## 7. Reporting integration

`runreport` already stamps `setup`, `commit`, `config_digest`, `versions`,
`timestamp` and prefixes results with `Versions: …`. This design:

- **fills `versions`** with the real stack (§6);
- **ensures the manifest is in `config_digest`** (§5.4);
- **adds the manifest identity** (`<arm>/<name>`) to the report metadata.

So every published result carries both *"we asked for this stack"* (the manifest)
and *"we got this stack"* (the discovered versions) — self-describing and
reproducible. The existing bundle + `index.jsonl` corpus carries forward unchanged,
now with version coordinates.

## 8. What floats / deferred

- **Floats (captured, not pinned):** everything outside the manifest — toolchain,
  Ubuntu HA apt packages for now, any un-promoted apt bits. Still *discovered* into
  the report.
- **No verify/reconcile engine** (the §2 razor).
- **Upgrade from/to — deferred, accommodated.** The named-manifest structure means
  upgrade testing is later expressed as "provision with manifest A → drive an upgrade
  toward manifest B" — a sequencing layer on top, not a schema change.
- **Per-arm rollout:** ship `default.yaml` for the arms that exist (pcmk-ubuntu,
  rdqm-rhel); others as built.

## 9. Testing

- **Unit (pure, no lab):**
  - manifest loading + vars-overlay rendering (manifest → expected ansible vars /
    box pin / tarball path);
  - the topology-aware discovery readers via the injected-fake pattern already in
    `capture_metadata` (fakes in tests, shell-out in real), incl. expected-absence vs
    query-failure;
  - the **MQ acquisition** paths: cache-hit, download-on-miss, `.sha256` mismatch
    (fail loud), and the un-fetchable-RHEL fail-loud-with-guidance path;
  - manifest-selection persistence: recorded at `create`, read by later commands,
    explicit error on a mid-lifecycle change.
  - Repo's 100%-branch-coverage bar holds for the Python pieces.
- **Functional gate (lab):** provision an arm with its `default.yaml`, then assert
  the *discovered* versions match the manifest's drivable pins (MQ, box, obs
  components) — proof the drive actually drove. Fits the cold-bring-up gate.

## 10. Future (out of scope here)

- Named alternate manifests for specific repro stacks.
- The upgrade from/to sequencing layer.
- Promoting Ubuntu HA apt packages (and other floaters) into the manifest by
  step-function as needed.
- Manifests for additional arms as they are built.
- The general local artifact cache (#269) this spec's MQ acquisition is a seed of.

## 11. Pushback resolutions (2026-06-18)

1. **Shared obs vs per-arm manifests** — obs is a singleton served by `mqlab obs up`;
   split the namespace into per-arm SUT (`manifests/<arm>/`) + a shared
   `manifests/_shared/observability.yaml` (§4).
2. **"Roles barely change" understated the wiring** — replaced with the real
   per-consumer table; MQ tarball, `box_version`, grafana apt-pin, and `mqlab`
   extra-vars threading are genuine work (§5).
3. **MQ "fail loud if absent" was wrong** — MQ Advanced for Developers is freely
   downloadable (every arch); acquisition is cache → download → fail-if-fetch-fails,
   with `.sha256` verified on acquire (§5.1). The RHEL-box-needs-login caveat belongs
   to the OS box layer, not MQ. The general artifact cache spun out as **#269**.
4. **Manifest selection wasn't threaded across the build lifecycle** — pinned into lab
   state at `create`, auto-threaded, mid-lifecycle change is an explicit error (§4).
5. **Discovery needed host-targeting** — made topology-aware, with expected-absence
   distinguished from query-failure (§6).
6. **`(version × arch)` → tarball** made explicit; pinning `mq_metric_samples_ref`
   **closes the #172 TODO** (§4, §5).
