# Static, topology-derived Ansible inventory + unified grouping namespace

**Issue:** #101
**Date:** 2026-06-10
**Status:** Design — awaiting review
**Depends-on / blocks:** blocks #102 (`mqlab vm provision <setup>`)

## 1. Problem

Two coupled problems, discovered while zooming out across the configs:

1. **The inventory is built dynamically at provision time.** `ansible/inventory.sh`
   scrapes `vagrant status` (which hosts are running) and `vagrant ssh-config`
   (each host's SSH address, port, per-machine key). The inventory is therefore a
   runtime artifact — inconsistent with the rest of the lab, where the Vagrantfile,
   the libvirt networks, and `mqlab vm status` are all pure functions of the static
   `lab/topology.yaml`.

2. **The grouping namespaces have drifted.** The playbooks target *role × site*
   groups (`pcmk_a`, `san_hosts`, `rdqm_a`, `qm_hosts`, …) derived from
   `inventory.sh`'s name-pattern matching, while `topology.yaml` defines an
   orthogonal set of *setup* groups (`pcmk-san-ha`, `rdqm-dr`, …). The same
   architecture is described by two unrelated vocabularies. We own the whole
   stack; there is no reason for them to diverge.

## 2. Goals / non-goals

**Goals**

- The Ansible inventory is a **deterministic render of `topology.yaml`** — no
  runtime `vagrant` queries. "Static" means *rendered from the single source*,
  not a second hand-maintained file.
- **One grouping namespace** shared by topology, the playbooks, and mqlab.
- Pin the SSH transport so the inventory is a pure function of topology.
- Keep the lab faithful: management traffic stays off the modeled
  data/heartbeat/SAN planes.

**Non-goals**

- `mqlab vm provision` itself (that is #102; it consumes this work).
- Making a single shared playbook (e.g. `site-rdqm.yml`, used by both `rdqm_ha`
  and `rdqm_dr`) scope itself to a chosen setup. See §9 — flagged for #102.
- Any change to role *logic*; only group **names**/targets and connection wiring.

## 3. The unified grouping model

### 3.1 Convention

- **Group names use underscores** (`san_a`, `pcmk_a`, `pcmk_san_ha`).
- **Host names keep hyphens** (`pcmk-a1`, `san-a`).

This is the only way to get true namespace identity: Ansible group names cannot
contain hyphens, so setups must be underscore-named to be inventory groups. The
identical string is then the topology setup, the mqlab selector, and the Ansible
group — no translation layer. (Open decision — see §13.)

### 3.2 Two levels

`topology.yaml` gains a `groups:` block of **atomic role × site groups** (each
host listed exactly once), and `setups:` become **compositions of groups**
(never raw host lists). The renderer emits atomic groups as `[group]` and setups
as Ansible `[setup:children]` parent groups.

```yaml
groups:                       # atomic role × site groups — the shared namespace
  san_a:  [san-a]
  san_b:  [san-b]
  pcmk_a: [pcmk-a1, pcmk-a2, pcmk-a3]
  pcmk_b: [pcmk-b1, pcmk-b2, pcmk-b3]
  rdqm_a: [rdqm-a1, rdqm-a2, rdqm-a3]
  rdqm_b: [rdqm-b1, rdqm-b2, rdqm-b3]
  qm:     [qm-main]
  dtcc:   [dtcc-sim]
  client: [app-client]

setups:                       # compositions of groups (provision unchanged)
  pcmk_san_ha: { groups: [san_a, pcmk_a],                provision: ansible/site-pcmk.yml }
  pcmk_san_dr: { groups: [san_a, san_b, pcmk_a, pcmk_b], provision: ansible/site-pcmk-dr.yml }
  rdqm_ha:     { groups: [rdqm_a],                       provision: ansible/site-rdqm.yml }
  rdqm_dr:     { groups: [rdqm_a, rdqm_b],               provision: ansible/site-rdqm.yml }
  standalone:  { groups: [qm, dtcc, client],             provision: ansible/site.yml }
```

`description:` is retained on each setup (used by `mqlab vm status`).

### 3.3 Renders to

```ini
[pcmk_a]
pcmk-a1 ansible_host=10.50.0.51
pcmk-a2 ansible_host=10.50.0.52
pcmk-a3 ansible_host=10.50.0.53
[san_a]
san-a ansible_host=10.50.0.5
... (every atomic group, every host) ...
[pcmk_san_dr:children]
san_a
san_b
pcmk_a
pcmk_b
... (every setup as a :children parent) ...
[all:vars]
ansible_user=vagrant
ansible_ssh_private_key_file=<shared key path, §5.3>
ansible_ssh_common_args=-o StrictHostKeyChecking=no
ansible_python_interpreter=/usr/bin/python3
```

The inventory lists **all** topology hosts, not just running ones: it is the
full declarative map. Liveness ("is it actually up?") stays in the verb
pre-flight (#99 / #102), never in the inventory.

## 4. Member resolution

A setup's members = the union of its groups' members, in declared order,
de-duplicated. `mqlab`'s setup selector (`mqlab vm up pcmk_san_ha`) resolves
through this. Atomic-group order within a setup is preserved (SAN groups first,
matching the documented bring-up order).

## 5. Transport pinning

### 5.1 New `net-mgmt` network

`lab/networks/net-mgmt.xml` — host-only, no `<forward>`, no DHCP, host IP
`10.50.0.1/24` — same shape as the other lab networks. Rationale: real
deployments run a dedicated management network; routing Ansible over the modeled
data/hb/san planes would pollute the model (the meaningful-lab principle). The
host gets a bridge on `10.50.0.0/24` and reaches every guest's mgmt IP directly.

### 5.2 Per-node management NIC

Each node gains one `net-mgmt` NIC with an explicit static IP, declared in its
`nics:` block (consistent with every other IP in topology). The last octet
mirrors each host's existing per-node numbering for predictability:

| host | mgmt IP | host | mgmt IP |
|------|---------|------|---------|
| qm-main | 10.50.0.10 | san-a | 10.50.0.5 |
| dtcc-sim | 10.50.0.50 | pcmk-a1/2/3 | 10.50.0.51/52/53 |
| app-client | 10.50.0.60 | san-b | 10.50.0.6 |
| rdqm-a1/2/3 | 10.50.0.31/32/33 | pcmk-b1/2/3 | 10.50.0.61/62/63 |
| rdqm-b1/2/3 | 10.50.0.41/42/43 | | |

### 5.3 Shared SSH key

`config.ssh.insert_key = false` in `lab/Vagrantfile` → all guests use Vagrant's
well-known insecure keypair instead of a per-machine generated key. The inventory
`[all:vars]` then carries one constant `ansible_ssh_private_key_file` (the
insecure key path, e.g. `~/.vagrant.d/insecure_private_key` — the **exact** path
is confirmed by the spike, §11). `ansible_user=vagrant`; host-key checking is
already off in `ansible.cfg`.

## 6. The renderer

### 6.1 `src/mqlab/inventory.py`

A pure function `render_inventory(topology: dict) -> str` returning INI text.
Fully unit-tested (100% branch, like the rest of mqlab). Emits, in order: each
atomic group with `host ansible_host=<mgmt-ip>` lines; each setup as a
`[setup:children]` parent; the `[all:vars]` block. No I/O, no `vagrant`, no
network — a deterministic projection of the parsed topology.

### 6.2 `mqlab vm inventory` command

Renders to `build/inventory.ini` and echoes it (treatment-A: the operator can
*see* the exact inventory before provisioning — transparency). Reuses the
existing renderer/transcript machinery. `ansible.cfg` already points at
`build/inventory.ini`.

### 6.3 Retire `ansible/inventory.sh`

Deleted. Any script that calls it (e.g. `lab/scripts/dr-provision.sh`) switches
to `mqlab vm inventory`. (#102's `vm provision` calls the same renderer as step
one; #101 ships the standalone command.)

## 7. Playbook `hosts:` rewrites

| file | before | after |
|------|--------|-------|
| site-pcmk.yml | `pcmk_a:san_hosts` | `pcmk_san_ha` |
| site-pcmk.yml | `san-a` | `san_a` |
| site-pcmk-dr.yml | `pcmk_a:pcmk_b:san_hosts` | `pcmk_san_dr` |
| site-pcmk-dr.yml | `san-a` / `san-b` | `san_a` / `san_b` |
| site.yml | `all` | `standalone` |
| site.yml | `qm_hosts` / `client_hosts` | `qm` / `client` |
| site.yml | `qm-main` / `dtcc-sim` | `qm` / `dtcc` |
| site.yml | `dtcc-sim:app-client` | `dtcc:client` |

`pcmk_a`, `pcmk_b`, `rdqm_a`, `rdqm_b` are unchanged (already underscore role
groups). Sub-setup cuts (`pcmk_a` alone, etc.) stay — the two-level model
preserves them. **`site-rdqm.yml` needs no `hosts:` change** — all its targets
(`rdqm_a`, `rdqm_b`, `rdqm_a:rdqm_b`) are already valid atomic groups; the
union `rdqm_a:rdqm_b` is intentionally *not* renamed to `rdqm_dr`, because that
playbook serves both `rdqm_ha` and `rdqm_dr` and should not claim one setup's
name.

**Role-internal references.** Roles/templates may reference group names in Jinja
(`groups['san_hosts']`, `hostvars`, etc.). The implementation greps
`ansible/roles/` for the old names (`san_hosts`, `qm_hosts`, `client_hosts`, and
any bare-host group assumptions) and updates them. This is correctness-critical —
a missed reference fails loud at playbook time, not silently.

## 8. mqlab internals

- `setups.py`: `Setup` carries `groups: list[str]` (not raw `members`);
  `setup_members(name)` flattens groups → hosts via the new `groups:` block.
  A `lab_groups()` accessor reads the `groups:` section.
- `fleet.py`: `setups_of(guest)` and the status join resolve membership through
  groups.
- `guestsel.py`: setup-name resolution unchanged in shape (a token may be a
  setup, a regex, or `all`); setups are now underscore-named.

## 9. Data flow

```
lab/topology.yaml ──render_inventory()──▶ build/inventory.ini ──▶ ansible-playbook
       │                                                              ▲
       └── nodes (mgmt IPs) · groups · setups · [all:vars] ──────────┘
```

Single source (topology) → deterministic render → the file Ansible already reads.

## 10. Error handling (fail loud)

- **Node missing a `net-mgmt` IP** → the renderer raises (no host can be
  addressed without one). Never emit a host with no `ansible_host`.
- **Setup references an undefined group**, or a **group references an undefined
  host** → raise with the offending name. Topology integrity is checked at render.
- **Unknown setup/group on the CLI** → mqlab's existing exit-2 "no match"
  message, in mqlab's own voice.

No silent skips or empty-string fallbacks (repo policy: a swallowed failure here
becomes an Ansible run against the wrong/empty host set).

## 11. Spike (de-risk before the full build)

Before committing to the transport-pinning premise, prove the path end to end:

1. Set `config.ssh.insert_key = false`; rebuild **one** guest with a `net-mgmt`
   NIC at its static IP.
2. Hand-write a 1-host inventory (`<host> ansible_host=10.50.0.N`, the shared
   key, `ansible_user=vagrant`).
3. `ansible <host> -m ping` succeeds.

Go = build the rest. No-go (shared key or static-mgmt SSH doesn't work) = the
transport approach is re-examined before any further code. The spike result is
recorded in `docs/reports/`.

## 12. Testing

- `tests/test_inventory.py`: topology fixture → expected INI (atomic groups,
  `:children` parents, `[all:vars]`, per-host `ansible_host`); error cases from
  §10.
- Updated `setups.py` / `fleet.py` / `guestsel.py` tests for the groups model.
- `mqlab vm inventory` command test (writes + echoes; CommandRunner seam).
- `vrg-container-run -- vrg-validate` green at 100% branch coverage.

## 13. Open decision for review

**Setup rename to underscore form** (`pcmk-san-ha` → `pcmk_san_ha`, etc.). This
is what buys true namespace identity (§3.1), but it changes the setup tokens you
type (`mqlab vm up pcmk_san_ha`) and any existing references in docs/scripts. The
alternative — keep hyphenated setups and have the renderer map hyphen→underscore
for the Ansible group name only — preserves the CLI spelling but reintroduces the
two-vocabulary seam this issue set out to remove. **Recommendation: rename.**
Confirm at review.

## 14. Scope boundary

**#101 delivers:** the `groups:` / `setups:` schema; the `net-mgmt` network + the
per-node mgmt NIC; `config.ssh.insert_key = false`; `src/mqlab/inventory.py` +
`mqlab vm inventory`; the playbook `hosts:` rewrites + role-reference updates;
the mqlab internals (§8); the spike. Requires a guest **rebuild** to pick up the
mgmt NIC and shared key.

**#102 consumes it:** `mqlab vm provision <setup>` drops any inventory
regeneration and runs the setup's playbook against the static inventory, with a
state-aware pre-flight for liveness, and addresses the shared-playbook scoping
(§2 non-goal).
