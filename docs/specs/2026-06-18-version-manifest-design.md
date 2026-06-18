# Version Manifest — Design

**Date:** 2026-06-18
**Issue:** #266
**Status:** Design (brainstormed; pending plan)

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
  in role defaults today; this folds it into the manifest's purview.

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
manifests/<arm>/default.yaml
        │  (drive)
        ▼
   mqlab orchestrator ──► Ansible vars overlay (version pins)
        │              ──► Vagrant box / box_version (OS + kernel)
        │              ──► build/mq/<tarball> selection (fail loud if absent)
        │              ──► config_digest (manifest hashed in)
        ▼
   provisioned guests
        │  (discover, post-provision)
        ▼
   runreport.read_versions() ──► report metadata: { manifest id, versions{...}, commit, config_digest }
```

## 4. The manifest

**Location & selection.** `manifests/<arm>/default.yaml` is the only file to start.
Named alternates (`manifests/<arm>/repro-case-X.yaml`) are added later with zero new
machinery: selection resolves `--manifest <name>` → `manifests/<arm>/<name>.yaml`,
defaulting to `default`. Per-arm because pcmk-ubuntu and rdqm-rhel have genuinely
different stacks.

**Shape (Scope B, priority-ordered, drivable-only):**

```yaml
# manifests/distributed-pcmk-ubuntu/default.yaml
mq:
  version: "9.4.5.0"                 # selects the build/mq/ tarball
os:
  box: cloud-image/ubuntu-24.04
  box_version: "20260518.0.0"        # one pin → OS *and* kernel layer
observability:
  prometheus: "2.53.2"
  node_exporter: "1.8.2"
  loki: "<pinned>"
  alloy: "<pinned>"
  grafana: "<pinned>"                # promote from apt-float to a pin
  mq_metric_samples_ref: "v5.x.y"    # was 'master'
```

**Drivable-only by construction.** Every key is something we can actually set.
Derived versions are deliberately *absent*:

- **RHEL HA stack** (pacemaker / drbd / kmod-drbd) ships *inside* the MQ Advanced
  package → fixed by `mq.version` → discovered, not pinned.
- **drbd kmod** follows the kernel → fixed by `os.box_version` → discovered.
- **Ubuntu HA apt packages** (pacemaker / corosync / pcs) are floatable today;
  promote into the manifest later by step-function if/when latest bites.

## 5. Drive wiring (the blend)

`mqlab` loads the selected manifest and:

1. **renders a generated vars overlay** (extra-vars, or a `build/`-side group_vars
   file) that sets the version vars the roles already consume — `prometheus_version`,
   `node_exporter_version`, `loki_version`, `alloy_version`, `mq_exporter_ref`, … —
   so **roles barely change**; the manifest just becomes their single source;
2. **passes `box` / `box_version` to Vagrant**, pinning the OS + kernel layer
   (today the box version floats — this adds `config.vm.box_version`);
3. **maps `mq.version` → the expected `build/mq/` tarball**, failing loud if that
   tarball is absent (no silent fallback to "whatever is there");
4. **includes the manifest file in the set hashed for `config_digest`**, binding the
   report to it.

Promoting a currently-floating dependency (grafana, an apt package) under the
manifest means adding a version pin where it floats today (an apt version spec, a
pinned download URL) — done per-key as each is brought in.

## 6. Discover — capturing the actual stack

Extend `runreport.read_versions()` (today only `vagrant` + `ansible` on the
controller) to capture the real stack from the **running guests** — the only place
the truth lives — as a query pass `mqlab` runs after `provision` / `qm create`.

- **Mechanism:** an Ansible gather play (reuses the rendered inventory) collects
  per-component versions; `mqlab` aggregates them into the `versions` dict. Keep the
  existing **injectable-reader contract** (`capture_metadata`'s readers; real impls
  shell out, tests inject fakes) and the **"readers MUST raise on failure — a field
  that silently became '' would corrupt the corpus"** rule.
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
  - the discovery readers via the injected-fake pattern already in
    `capture_metadata` (fakes in tests, shell-out in real);
  - the **fail-loud-on-missing-tarball** path.
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
