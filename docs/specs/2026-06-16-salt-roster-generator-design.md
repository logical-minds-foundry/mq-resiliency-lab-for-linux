# salt-ssh roster generator + mqlab/Salt integration — design

- **Issue:** #206
- **Date:** 2026-06-16
- **Status:** design
- **Follows:** #205 (Ansible→Salt evaluation) — retires that report's "roster
  generation" paper-only risk without lab ownership.

## 1. Purpose

The lab generates an Ansible inventory from `lab/topology.yaml`
(`src/mqlab/inventory.py` → `build/inventory.ini`). A Salt migration needs the
same projection into a **salt-ssh roster**. This builds that generator — a pure,
unit-tested sibling to `inventory.py` — and specifies (design-only) the rest of
the `mqlab`↔Salt integration so the picture is whole.

**Buildable this round:** `roster.py` + a `mqlab vm roster` CLI command + tests.
Everything else (the salt-ssh invocation, the salt config, the `vergil.toml`
Salt-repo addition) is **design-only** — it either has no live consumer until the
migration proper or cannot be validated without a VM rebuild.

## 2. Background — what we mirror

`src/mqlab/inventory.py` is the model. It exposes a trio:

- `render_inventory(topo: dict) -> str` — pure projection, topology → INI text.
- `inventory_path() -> Path` — `build/inventory.ini` (gitignored).
- `lab_inventory() -> str` — reads `lab/topology.yaml`, calls `render_inventory`.

It is **fail-loud** via `InventoryError` (undefined host in a group; host missing
its `net-mgmt` IP; setup referencing an undefined group) and emits a deterministic
INI: `[group]` sections (atomic role×site groups), `[setup:children]` blocks, and
a trailing `[all:vars]` carrying the shared Vagrant insecure key (#101),
`ansible_user=vagrant`, host-key bypass, and the Python interpreter.

`scrape.py` is a second instance of the same trio pattern (topology → JSON). Both
parse `lab/topology.yaml` inline; there is no shared `topology.py`, and this design
does **not** introduce one (out of scope).

Topology facts the roster depends on: every node has a mandatory `net-mgmt`
(`10.50.0.0/24`) NIC — the management/SSH plane; hosts are hyphen-named
(`pcmk-a1`), groups underscore-named (`pcmk_a`); `groups` are atomic (role×site),
`setups` compose groups via their `groups:` list.

## 3. `roster.py` — the generator

A sibling trio in `src/mqlab/roster.py`:

- `render_roster(topo: dict) -> str` — pure function, topology → roster YAML text.
- `roster_path() -> Path` — `build/salt/roster` (gitignored; establishes the
  future `salt/` config home, parallel to `ansible/`).
- `lab_roster() -> str` — reads `lab/topology.yaml`, calls `render_roster`.
- `RosterError` — fail-loud, mirroring `InventoryError`.

No refactor of `inventory.py`; match the existing inline-parse style.

### 3.1 Output format

Per-target, keyed by hostname (the salt-ssh minion id). Connection details are
carried per-target (a roster's nature — the per-host mirror of inventory's
`[all:vars]`); group/setup membership rides as **grains**:

```yaml
# salt-ssh roster — generated from lab/topology.yaml. Do not edit by hand.
pcmk-a1:
  host: 10.50.0.51
  user: vagrant
  priv: ~/.vagrant.d/insecure_private_key
  sudo: true
  grains:
    roster_groups: [pcmk_a]
    roster_setups: [pcmk_san_ha]
```

- `host` = the node's `net-mgmt` IP. `user`/`priv`/`sudo` are constant across the
  fleet (the #101 insecure key; `sudo: true` = the Ansible `become` equivalent),
  repeated per target because the roster is per-target.
- Grain keys are **prefixed** `roster_groups` / `roster_setups` to avoid colliding
  with any built-in Salt grain.
- `roster_groups` = every atomic group the host belongs to. `roster_setups` =
  every setup whose `groups:` transitively includes the host.

**Deterministic ordering** (exact-string tests depend on it): targets in topology
`nodes` order; `roster_groups` in `groups`-declaration order; `roster_setups` in
`setups`-declaration order. Rendered via `yaml.safe_dump(data, sort_keys=False)`
over an insertion-ordered structure, with a leading comment line prepended.

### 3.2 Error handling (fail-loud)

`RosterError` is raised — never a silent skip — on:

1. a group referencing a host absent from `nodes`;
2. a host missing its `net-mgmt` IP;
3. a setup referencing an undefined group.

These mirror `InventoryError`'s cases so both generators fail identically on the
same malformed topology.

## 4. CLI command

`mqlab vm roster` — mirrors the existing `mqlab vm inventory`: calls `lab_roster()`,
writes `roster_path()` (creating `build/salt/` if absent), and echoes the rendered
roster. Same Typer/`Deps` wiring as the inventory command.

## 5. Testing

`tests/test_roster.py`, mirroring `test_inventory.py`:

- inline `TOPO` fixture (a minimal multi-group, multi-setup topology);
- one exact-string assertion on `render_roster(TOPO)` output;
- `pytest.raises(RosterError, match=...)` for each of the three fail-loud cases;
- grain correctness: a host in two groups and one setup lists both groups (in
  declaration order) and the setup.

Plus a CLI test for `mqlab vm roster` using `CliRunner` + the `RecordingRunner`
fake (per `tests/test_cli_vm.py`), asserting it writes `build/salt/roster` and
echoes content. `vrg-validate` green, including the 100% branch-coverage gate.

## 6. Design-only — the rest of the integration

Specified for completeness; **not built this round**.

- **`mqlab`→`salt-ssh` invocation.** A `Command` mirroring the `ansible-playbook`
  one, run from a future `salt/` dir, invoking `salt-ssh` **by bare name via
  `$PATH`** (per #165, no `uv run`):
  `salt-ssh -c <saltdir> --roster-file build/salt/roster -G 'roster_groups:<grp>' state.apply <state>`.
  Secrets inject as env vars exactly as today.
  **Open verification:** that salt-ssh honors roster-defined grains for `-G`
  targeting. To be confirmed when the invocation is built (a disposable-VM
  spot-check, the #205 pattern). Documented fallback if not: emit a `nodegroups`
  mapping into the salt config and target via `-N` (evaluation approach B). The
  generator's grain output is correct either way; only the targeting flag changes.
- **Salt config.** `file_roots` (states base), the host-key bypass that is the
  roster's global analogue of `ansible_ssh_common_args`, and a `salt/` states dir
  paralleling `ansible/`.
- **`vergil.toml`.** Add the Broadcom Salt repo to `[vm.vergil-user]`
  (`apt_repos`) + the `salt`/`salt-ssh` package — the #205 §6 tooling finding.
  Deferred because it cannot be validated without a VM rebuild (cold-rebuild
  acceptance gate).

## 7. Scope boundaries

- **In:** `src/mqlab/roster.py`, the `mqlab vm roster` command, their tests.
- **Out:** the invocation builder; the salt config; `vergil.toml`; any SLS
  porting; a shared `topology.py` extraction; anything touching a VM or the lab.
