# Topology-consumer audit — x86 host portability (#276)

Per design §5.1: making `fleet.lab_guests()` host-aware (it now folds in
`default_platform(facts)`) means its consumers inherit host-awareness. This audit
classifies every module that reads `lab/topology.yaml` and/or `lab_guests()` and
records a verdict. **Result: no consumer required a fix** — each is either
name-only or already-correctly host-resolved.

| Module | Reads | Verdict |
|--------|-------|---------|
| `arms` | nodes/arms by name | **name-only** — no arch/platform/box reads |
| `cli` | orchestration | **name-only** — gating added in Task 8; no arch reads |
| `dashboard` | node names for targets | **name-only** — its `"defaults"` keys are Grafana JSON `fieldConfig`, not topology defaults |
| `fleet` | `lab_guests` | **arch-aware (injection point)** — applies `default_platform(facts)`; host-resolved |
| `guestsel` | node names | **name-only** |
| `inventory` | groups/setups, mgmt IPs | **name-only** |
| `netstate` | network names | **name-only** |
| `parity` | setups/arms | **name-only** |
| `roster` | node names + conn data | **name-only** — embeds mgmt IP + user, not platform/arch |
| `scrape` | node names/IPs | **name-only** |
| `setups` | setups/groups | **name-only** |
| `vmstatus` | `lab_guests()` | **arch-aware** — shows per-guest platform; inherits the host-resolved default |
| `manifest` (#266) | `lab_guests` via `setup_platforms`; `boxes` in `box_version_pins` | **arch-aware** — `setup_platforms(setup, facts)` is facts-threaded; `box_version_pins` keys by the box **name** (`cloud-image/ubuntu-24.04`), so the new `ubuntu2404-x86_64` platform automatically receives the pin alongside `ubuntu2404-arm64` |
| `artifact` (#266) | `setup_platforms` | **arch-aware** — `ensure_mq_tarballs(..., facts)` threads facts through to `setup_platforms` |

## Determinism note

`lab_guests()` and `setup_platforms()` default `facts` to `probe()` but accept an
injected `HostFacts`, so the unit suite and x86 CI runners stay deterministic
(tests inject explicit facts rather than reading the real host). Confirmed: the
full suite passes at 100% branch coverage independent of host arch.
