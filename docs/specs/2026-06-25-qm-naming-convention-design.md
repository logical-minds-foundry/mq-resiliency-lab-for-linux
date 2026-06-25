# Queue-Manager Naming Convention — Design

> **Status:** approved design, ready for planning.
> **Date:** 2026-06-25
> **Author:** Phillip Moore (with Claude)
> **Issue:** #351
> **Lands first** — before the CLI namespace rationalization (#350), which
> depends on the per-stack distinct QM names established here.
> **Related:** #345 (globally-unique per-QM ports); #313 (cockpit QM
> de-hardcode); reconcile with `2026-06-17-vendor-two-site-dr-design.md`
> (service-site DR pair).

---

## 1. Problem

Queue-manager names are inconsistent, verbose, and hardcoded across the repo:

- Names carry a redundant `QM` prefix (`QMPCMK`, `QMSVC`, `QMNATIVE`,
  `QMRDQM`) — "Q in the Qname," wasting two characters of a tight budget (MQ
  channel names are capped at 20 chars, so a `QMa.QMb` channel leaves ≈9–10
  chars per QM).
- The service-side counterparty is a single shared `QMSVC`, which can't be
  told apart per stack from outside the lab, and can't be failed over
  per-stack.
- The names are **hardcoded in ~250 places** (ansible ×13 files + many roles,
  tests ×10, `lab/scripts` ×9, `src/mqlab`, `clients`, `content`), only
  partly deriving from the topology's `qm.name`. (The cockpit dashboards were
  already de-hardcoded in #313.)

When multiple stacks run concurrently (#350), you need to see at a glance —
from outside the lab — which QM belongs to which stack and which side (app vs
service) it is. That requires a short, per-stack, role-tagged convention and a
single source the whole repo derives from.

## 2. Goals & Non-Goals

**Goals**

1. A short, per-stack, externally-legible QM naming convention.
2. **One source of truth** per stack; every reference derives from it.
3. De-hardcode the ~250 references so the rename is a byproduct and future
   renames are free.
4. A regression guard so hardcoded names can't creep back.

**Non-goals (handled elsewhere)**

- Creating multiple co-resident service QM instances (per-stack svc
  *multiplication*) — that's #350's concurrency work; this spec only
  establishes the names and renames the one existing svc QM.
- The service-site DR pair — `2026-06-17-vendor-two-site-dr-design.md`. It
  composes for free (a DR replica keeps the same QM name on the partner node).
- The CLI/stack-model collapse — #350.

## 3. Naming Convention + Single Source

**One value per stack drives everything: a short token.** Each stack declares
a `short`; both QM names and the channel derive from it:

```
short → qm_app = "{short}APP"   qm_svc = "{short}SVC"   channel = "{short}APP.{short}SVC"
```

| stack | `short` | app-side QM | svc-side QM | channel | len |
|---|---|---|---|---|---|
| pcmk-ubuntu | `PCMK` | `PCMKAPP` | `PCMKSVC` | `PCMKAPP.PCMKSVC` | 15 |
| rdqm-rhel | `RDQM` | `RDQMAPP` | `RDQMSVC` | `RDQMAPP.RDQMSVC` | 15 |
| nativeha-rhel | `NHAR` | `NHARAPP` | `NHARSVC` | `NHARAPP.NHARSVC` | 15 |
| nativeha-ubuntu | `NHAU` | `NHAUAPP` | `NHAUSVC` | (reserved) | — |

`short` is OS-disambiguated only where a mechanism spans OSes (`NHAR`/`NHAU`);
`PCMK`/`RDQM` stay clean. All comfortably under the 20-char channel limit.

- **app-side QM** = the SUT / HADR cluster QM (the enterprise app's QM). The
  star QM is renamed too, so the pair reads cleanly from outside.
- **svc-side QM** = the service counterparty.

**Source of truth** is keyed by stack/arm (the four canonical stacks) and
lives in `topology.yaml`, so it survives #350's `arms`+`setups`→`stacks`
merge untouched.

**Old → new rename:**

| old | new | note |
|---|---|---|
| `QMPCMK` | `PCMKAPP` | SUT QM — already per-stack, clean rename |
| `QMRDQM` | `RDQMAPP` | SUT QM |
| `QMNATIVE` | `NHARAPP` | SUT QM |
| `QMSVC` | `<short>SVC` | one svc QM today → its stack's svc name; #350 multiplies |

## 4. De-hardcode Model

Follows the repo's existing render-then-consume pattern (`inventory.ini`,
`reach-peers.json`, `node.json`):

- **Python (`src/mqlab`):** the stack model exposes derived properties —
  `stack.short` → `stack.qm_app` / `stack.qm_svc` / `stack.channel`. Hardcoded
  sites read these.
- **Ansible:** `mqlab` resolves the target stack's QM identity and passes it
  to the playbook (extra-vars or a generated per-run vars file, like the
  rendered inventory). Roles/templates reference `{{ qm_app }}` /
  `{{ qm_svc }}` / `{{ channel }}` instead of literals — covering the ansible
  roles, exporter, PKI, mqweb, inter-qm, and `ansible/vars`.
- **Scripts (`lab/scripts`):** the HA/DR failover scripts take QM names as
  arguments from `mqlab` (which reads topology), not hardcoded.
- **Clients (`clients/`):** connection configs templated from the same source.
- **Tests:** fixtures derive from `short` (or assert against the resolver), so
  a name change can't silently break them.

**Regression guard:** a `vrg-validate` check (grep gate) fails if a literal
`QM<NAME>` string reappears outside the single source — so the de-hardcode
can't quietly rot back.

## 5. Scope Boundaries & Interactions

- **Per-stack svc multiplication → #350.** This spec names the per-stack svc
  QMs and renames the single existing one; creating multiple co-resident svc
  QMs is #350's concurrency work, which reuses these names.
- **Service-site DR pair → vendor-two-site-DR design.** Composes for free: a
  DR replica keeps the same QM name on the partner node, so `<short>SVC`
  needs no extra naming. The same-host collision concern does **not** apply
  across a primary/DR pair (different hosts).
- **CLI/stack collapse → #350.**
- **Co-location collision (validated by a spike during #350):** distinct
  per-stack names (this convention) avoid the MQ rule against two same-named
  QMs on one host. The spike also confirms a shared `app-org` keystore +
  `MON.SVRCONN` SSLPEER pinning holds across N co-resident QMs.

## 6. Migration

Cold-rebuild, **no in-place** — same philosophy as #350. QM names live in the
QM data directories, so renaming *is* recreating the QMs; the cold rebuild
does that cleanly, and the current half-built lab is torn down regardless.

## 7. Testing & Acceptance

- **Unit:** the derivation (`short` → names/channel) and the resolver `mqlab`
  passes to ansible/scripts.
- **Validation:** `vrg-validate` green, including the new no-hardcoded-QM-name
  gate.
- **Cold-rebuild acceptance:** rebuild the one currently-implementable stack
  (`pcmk-ubuntu`) and confirm `PCMKAPP`/`PCMKSVC`, the `PCMKAPP.PCMKSVC`
  channel, the exporter, and the dashboard all come up clean under the new
  names.

## 8. Rough Implementation Sequence

1. Add `short` + derived QM identity to the stack/arm source of truth in
   `topology.yaml`; expose `qm_app`/`qm_svc`/`channel` on the Python model.
2. De-hardcode `src/mqlab` and the `mqlab`→ansible/scripts resolver path.
3. De-hardcode the ansible roles/templates/vars, exporter, PKI, mqweb,
   inter-qm to `{{ qm_app }}`/`{{ qm_svc }}`/`{{ channel }}`.
4. De-hardcode `lab/scripts` (failover) + `clients` + test fixtures.
5. Add the `vrg-validate` no-hardcoded-QM-name gate.
6. Cold-rebuild `pcmk-ubuntu`; confirm names/channel/exporter/dashboard.
