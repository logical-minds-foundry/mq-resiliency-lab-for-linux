# Plan 1 — Network & two-VIP foundation — Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.
>
> **Issue:** #146 (sub-issue of epic #145). **Spec:** `docs/specs/2026-06-13-distributed-mq-architecture-design.md` §7–§8.

**Goal:** Give our HA/DR queue manager a second, partner-facing floating VIP on a dedicated inter-business WAN segment (`net-ext`), so a later DTCC counterparty can reach it across both HA (in-site VIP float) and DR (cross-site VIP change).

**Architecture:** Add an isolated libvirt network `net-ext` (10.60.0.0/24); attach the Pacemaker nodes (both sites) to it; manage a second `IPaddr2` resource `mq_vip_ext` in the existing `mq_group` (ordered `mq_fs → mq_vip → mq_vip_ext → mq_qm`), sourced from a new per-setup `vip_ext`; and extend the DR cutover script to bring the partner VIP up at the target site. The internal-app data-plane VIP is unchanged.

**Tech Stack:** libvirt network XML, `lab/topology.yaml`, Ruby Vagrantfile (generic NIC iteration — no change needed), Python 3.12 (`mqlab` setups/cli/dashboard), Pacemaker `pcs`, bash, pytest.

---

## Scope & boundaries

**In scope:** the `net-ext` network; `net-ext` NICs on `pcmk-a1..3` / `pcmk-b1..3`; `mq_vip_ext` in the role's resource group; `vip_ext` on the `pcmk_san_ha` setup's QM config, plumbed through `QmConfig` → `mqlab qm` → the playbook; the partner VIP in `pcmk-dr-cutover.sh`; the `dashboard.py` `NET_SECTIONS` update the exact-match test forces.

**Out of scope (deferred):**
- **DTCC VM's `net-ext` NIC + dropping `net-dtcc`** → Plan 2 (repurposing `dtcc-sim` into the DTCC service VM; dropping `net-dtcc` now would break the still-live `standalone` setup, retired in Plan 3).
- All MQSC / channels / QMDTCC → Plans 2–3.

**Verified facts (this branch):**
- Networks auto-discover from `lab/networks/net-*.xml` via `netsel.lab_net_names()`; a new XML is picked up with no registration.
- `dashboard.py` `NET_SECTIONS` must **exactly equal** `lab_net_names()` — enforced by `tests/test_topology_integrity.py::test_network_sections_cover_exactly_the_declared_networks`. Adding `net-ext.xml` **fails** that test until `NET_SECTIONS` includes `net-ext`.
- `lab/Vagrantfile` iterates `spec.fetch("nics", {})` generically (`:libvirt__network_name => net`) — new NICs need no Vagrantfile change.
- `inventory.render_inventory` validates only: every host has a `net-mgmt` IP, and groups/setups reference defined names. It does **not** couple NICs to declared networks.
- `setups.QmConfig` is a frozen dataclass `{name, vip}`, parsed from `cfg["qm"]`. `cli.py:828-831` passes `-e qm_name=… -e qm_vip=…` to the QM playbook. Only `pcmk_san_ha` declares a `qm:` block today.
- The role's group (mq-pcmk-qmgr/tasks/main.yml) is `mq_fs → mq_vip(ip={{ qm_vip }}) → mq_qm`. `pcmk-dr-cutover.sh` hardcodes one `TO_VIP` per direction and recreates the group at the target site.

## File structure

- **Create:** `lab/networks/net-ext.xml` — the inter-business WAN (isolated, 10.60.0.0/24).
- **Modify:** `lab/topology.yaml` — `net-ext` NICs on `pcmk-a1..3` / `pcmk-b1..3`; `vip_ext: 10.60.0.10` on `pcmk_san_ha.qm`.
- **Modify:** `src/mqlab/setups.py` — `QmConfig.vip_ext`.
- **Modify:** `src/mqlab/cli.py` — pass `-e qm_vip_ext=…`.
- **Modify:** `src/mqlab/dashboard.py` — add `net-ext` to `NET_SECTIONS`.
- **Modify:** `ansible/roles/mq-pcmk-qmgr/tasks/main.yml` — `mq_vip_ext` resource; reorder `mq_qm --after mq_vip_ext`.
- **Modify:** `lab/scripts/pcmk-dr-cutover.sh` — per-site `TO_VIP_EXT`; create `mq_vip_ext` in the group rebuild.
- **Tests:** `tests/test_setups.py`, `tests/test_topology_integrity.py`, `tests/test_cli_qm.py`, `tests/test_dashboard.py`.

## Addressing

| Endpoint | Address | Network |
|---|---|---|
| Partner VIP — site A | `10.60.0.10` | `net-ext`, floats on `pcmk_a` |
| Partner VIP — site B | `10.60.0.20` | `net-ext`, floats on `pcmk_b` |
| `pcmk-a1..3` net-ext NIC | `10.60.0.51/52/53` | `net-ext` |
| `pcmk-b1..3` net-ext NIC | `10.60.0.61/62/63` | `net-ext` |
| (Plan 2) DTCC service VM | `10.60.0.50` | `net-ext` |

---

### Task 1: Declare the `net-ext` network + update `NET_SECTIONS` (TDD via the exact-match test)

**Files:**
- Create: `lab/networks/net-ext.xml`
- Modify: `src/mqlab/dashboard.py` (`NET_SECTIONS`)
- Test: `tests/test_topology_integrity.py` (existing exact-match test is the guard)

- [ ] **Step 1: Create the network XML** (mirrors `net-wan.xml`)

```xml
<!-- lab/networks/net-ext.xml — inter-business WAN ("the internet") between
     Business A (our HA/DR QM) and Business B (DTCC). Isolated: no <forward>,
     no DHCP. Traffic-shaping (tc netem) is a future enhancement (#145 §7.3). -->
<network>
  <name>net-ext</name>
  <bridge name="virbr-ext"/>
  <ip address="10.60.0.1" netmask="255.255.255.0"/>
</network>
```

- [ ] **Step 2: Run the guard test — expect FAIL** (`net-ext` declared but not in `NET_SECTIONS`)

Run: `uv run pytest tests/test_topology_integrity.py::test_network_sections_cover_exactly_the_declared_networks -q`
Expected: FAIL — `NET_SECTIONS must match lab/networks/net-*.xml`.

- [ ] **Step 3: Add `net-ext` to `NET_SECTIONS`** in `src/mqlab/dashboard.py` — group it with the cross-site/WAN section (read the literal and place `net-ext` alongside `net-wan`; if `net-wan` has its own section, add `net-ext` to it; otherwise add a WAN section consistent with the surrounding style).

- [ ] **Step 4: Run the guard test — expect PASS**

Run: `uv run pytest tests/test_topology_integrity.py::test_network_sections_cover_exactly_the_declared_networks -q`
Expected: PASS.

- [ ] **Step 5: Commit**

```bash
vrg-git add lab/networks/net-ext.xml src/mqlab/dashboard.py
vrg-commit --type feat --scope lab --message "add net-ext inter-business WAN network (#146)"
```

### Task 2: Attach the Pacemaker nodes to `net-ext` in topology

**Files:**
- Modify: `lab/topology.yaml` (`pcmk-a1..3`, `pcmk-b1..3` nics)
- Test: `tests/test_topology_integrity.py` (new assertion)

- [ ] **Step 1: Write the failing integrity test**

Add to `tests/test_topology_integrity.py`:

```python
def test_pcmk_nodes_attach_to_net_ext():
    import yaml
    from mqlab.paths import repo_root

    topo = yaml.safe_load((repo_root() / "lab" / "topology.yaml").read_text())
    nodes = topo["nodes"]
    expected = {
        "pcmk-a1": "10.60.0.51", "pcmk-a2": "10.60.0.52", "pcmk-a3": "10.60.0.53",
        "pcmk-b1": "10.60.0.61", "pcmk-b2": "10.60.0.62", "pcmk-b3": "10.60.0.63",
    }
    for host, ip in expected.items():
        assert nodes[host]["nics"].get("net-ext") == ip, f"{host} missing net-ext {ip}"
```

- [ ] **Step 2: Run it — expect FAIL** (`KeyError`/`None` — NICs not added yet)

Run: `uv run pytest tests/test_topology_integrity.py::test_pcmk_nodes_attach_to_net_ext -q`
Expected: FAIL.

- [ ] **Step 3: Add `net-ext` to each pcmk node's `nics`** in `lab/topology.yaml` (append to the existing `nics:` map; `pcmk-a1` gains `net-ext: 10.60.0.51`, … `pcmk-b3` gains `net-ext: 10.60.0.63`).

- [ ] **Step 4: Run it — expect PASS**

Run: `uv run pytest tests/test_topology_integrity.py::test_pcmk_nodes_attach_to_net_ext -q`
Expected: PASS.

- [ ] **Step 5: Commit**

```bash
vrg-git add lab/topology.yaml tests/test_topology_integrity.py
vrg-commit --type feat --scope lab --message "attach pcmk nodes (both sites) to net-ext (#146)"
```

### Task 3: Thread `vip_ext` through `QmConfig` → `mqlab qm` → playbook

**Files:**
- Modify: `src/mqlab/setups.py` (`QmConfig`)
- Modify: `lab/topology.yaml` (`pcmk_san_ha.qm.vip_ext`)
- Modify: `src/mqlab/cli.py` (extra `-e`)
- Test: `tests/test_setups.py`, `tests/test_cli_qm.py`

- [ ] **Step 1: Write failing setups test**

Add to `tests/test_setups.py`:

```python
def test_qm_config_carries_partner_vip():
    from mqlab.setups import lab_setups

    qm = lab_setups()["pcmk_san_ha"].qm
    assert qm is not None
    assert qm.vip == "10.10.1.200"
    assert qm.vip_ext == "10.60.0.10"
```

- [ ] **Step 2: Run it — expect FAIL** (`AttributeError: vip_ext` / parse KeyError)

Run: `uv run pytest tests/test_setups.py::test_qm_config_carries_partner_vip -q`
Expected: FAIL.

- [ ] **Step 3: Add `vip_ext` to `QmConfig` and parse it**

In `src/mqlab/setups.py`, add the field to the frozen dataclass:

```python
@dataclass(frozen=True)
class QmConfig:
    """A setup's queue-manager identity (#109): the QM name, its internal data-plane
    VIP, and its partner-facing (net-ext) VIP."""

    name: str
    vip: str
    vip_ext: str
```

and in `lab_setups()` update the parse:

```python
qm=QmConfig(
    name=cfg["qm"]["name"],
    vip=cfg["qm"]["vip"],
    vip_ext=cfg["qm"]["vip_ext"],
) if cfg.get("qm") else None,
```

- [ ] **Step 4: Add `vip_ext` to the topology QM block**

In `lab/topology.yaml`, the `pcmk_san_ha` setup:

```yaml
    qm: { name: QMPCMK, vip: 10.10.1.200, vip_ext: 10.60.0.10 }
```

- [ ] **Step 5: Run setups test — expect PASS**

Run: `uv run pytest tests/test_setups.py::test_qm_config_carries_partner_vip -q`
Expected: PASS.

- [ ] **Step 6: Write failing cli test** — assert the playbook gets `qm_vip_ext`. Find the existing qm-create test in `tests/test_cli_qm.py` that asserts on `qm_vip=…`, and add a sibling assertion that the rendered command args contain `qm_vip_ext=10.60.0.10`. (Match the existing test's harness/fake exactly — reuse its setup.)

- [ ] **Step 7: Run it — expect FAIL**

Run: `uv run pytest tests/test_cli_qm.py -q -k vip_ext`
Expected: FAIL.

- [ ] **Step 8: Pass `qm_vip_ext` in `cli.py`** — after the `qm_vip` arg (lines ~830-831):

```python
                    "-e",
                    f"qm_vip={qm.vip}",
                    "-e",
                    f"qm_vip_ext={qm.vip_ext}",
```

- [ ] **Step 9: Run cli test — expect PASS**

Run: `uv run pytest tests/test_cli_qm.py -q`
Expected: PASS.

- [ ] **Step 10: Commit**

```bash
vrg-git add src/mqlab/setups.py src/mqlab/cli.py lab/topology.yaml tests/test_setups.py tests/test_cli_qm.py
vrg-commit --type feat --scope mqlab --message "thread partner VIP (vip_ext) through QmConfig and qm create (#146)"
```

### Task 4: Add `mq_vip_ext` to the Pacemaker resource group (role)

**Files:**
- Modify: `ansible/roles/mq-pcmk-qmgr/tasks/main.yml`

> Infra (Ansible) — no pytest. Verified by the human running the lab (Task 6).

- [ ] **Step 1: Insert the `mq_vip_ext` resource after `mq_vip`** and reorder `mq_qm`:

```yaml
- name: mq_vip_ext resource (partner-facing floating IP on net-ext)
  ansible.builtin.shell: >
    pcs resource status mq_vip_ext >/dev/null 2>&1 ||
    pcs resource create mq_vip_ext ocf:heartbeat:IPaddr2 ip={{ qm_vip_ext }} cidr_netmask=24
    --group mq_group --after mq_vip
  run_once: true
  changed_when: true

- name: mq_qm resource (the queue manager)
  ansible.builtin.shell: >
    pcs resource status mq_qm >/dev/null 2>&1 ||
    pcs resource create mq_qm systemd:mq-{{ qm_name }} --group mq_group --after mq_vip_ext
  run_once: true
  changed_when: true
```

(Replace the existing `mq_qm` task's `--after mq_vip` with `--after mq_vip_ext`.)

- [ ] **Step 2: Lint-only check** — `vrg-container-run -- vrg-validate` (ansible-lint/yaml gates). Expected: green. Behavioural check is Task 6.

- [ ] **Step 3: Commit**

```bash
vrg-git add ansible/roles/mq-pcmk-qmgr/tasks/main.yml
vrg-commit --type feat --scope pcmk --message "add partner VIP (mq_vip_ext) to the QM resource group (#146)"
```

### Task 5: Carry the partner VIP through the DR cutover

**Files:**
- Modify: `lab/scripts/pcmk-dr-cutover.sh`

- [ ] **Step 1: Add per-direction `TO_VIP_EXT`** alongside `TO_VIP` (a2b → `10.60.0.20`; b2a → `10.60.0.10`).

- [ ] **Step 2: Create `mq_vip_ext` in the group rebuild** — insert into the `pcs resource create` sequence, after `mq_vip` and before `mq_qm`, reordering `mq_qm --after mq_vip_ext`:

```bash
    pcs resource create mq_vip ocf:heartbeat:IPaddr2 ip=${TO_VIP} cidr_netmask=24 --group mq_group --after mq_fs
    pcs resource create mq_vip_ext ocf:heartbeat:IPaddr2 ip=${TO_VIP_EXT} cidr_netmask=24 --group mq_group --after mq_vip
    pcs resource create mq_qm systemd:mq-QMPCMK --group mq_group --after mq_vip_ext
```

- [ ] **Step 3: Update the completion echo** to report both VIPs (`VIP $TO_VIP / ext $TO_VIP_EXT`).

- [ ] **Step 4: `shellcheck` via validate** — `vrg-container-run -- vrg-validate`. Expected: green.

- [ ] **Step 5: Commit**

```bash
vrg-git add lab/scripts/pcmk-dr-cutover.sh
vrg-commit --type feat --scope pcmk --message "bring the partner VIP across on DR cutover (#146)"
```

### Task 6: Full validation + human lab verification

- [ ] **Step 1: Full dev-loop gate** — `vrg-container-run -- vrg-validate`. Expected: green (ruff/format/mypy/pytest 100% branch/markdownlint/ansible-lint/shellcheck).

- [ ] **Step 2: Human lab verification** (operator-run; the dev gate cannot prove runtime VIP behaviour):
  - `mqlab vm up pcmk_san_ha` + `mqlab qm create pcmk_san_ha` → `pcs status` shows `mq_group` = `mq_fs → mq_vip(10.10.1.200) → mq_vip_ext(10.60.0.10) → mq_qm`, all Started.
  - Confirm `10.60.0.10` answers on `net-ext` from another net-ext host.
  - HA: move/standby the active node → **both** VIPs follow together.
  - DR: `pcmk-dr-cutover.sh a2b` → site B hosts `mq_qm` with `mq_vip(10.10.2.200)` **and** `mq_vip_ext(10.60.0.20)` Started.

## Acceptance (this plan)

- Spec §13.4 (partial, pre-DTCC): after a DR cutover **both** VIPs are live at the target site (DTCC-reconnect half lands in Plan 2/4).
- `vrg-validate` green; `net-ext` declared and covered by `NET_SECTIONS`; `pcmk` nodes on `net-ext`; `mq_vip_ext` in the group and in the cutover path.
