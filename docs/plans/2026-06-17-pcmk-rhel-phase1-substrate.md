# pcmk-rhel Phase 1 — RHEL Substrate Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Stand up the open-source Corosync/Pacemaker + iSCSI-SAN substrate on RHEL 9.6 — a 3-node HA cluster with STONITH and a shared LUN — as the `pcmk-rhel` arm's first build increment, with no RDQM and no MQ yet.

**Architecture:** Reuse the Ubuntu arm's four substrate roles unchanged in orchestration (`pcmk-cluster`, `pcmk-stonith`, `iscsi-target`, `iscsi-initiator`), factoring only their package-install surface into an `ansible_os_family`-selected adapter (`tasks/install-Debian.yml` / `tasks/install-RedHat.yml`). A new `pcmk-rhel` arm, a `pcmk_san_rhel_ha` setup, new RHEL nodes/groups, and a `site-pcmk-rhel.yml` playbook wire it together. Parity is structural: both arms run the same orchestration code.

**Tech Stack:** Ansible (ansible-core, no Galaxy collections), Corosync/Pacemaker/pcs (RHEL High Availability Add-On), LIO/targetcli + open-iscsi, Vagrant/libvirt (RHEL x86_64 under TCG), `vrg-validate` for CI lint.

## Global Constraints

- **No Galaxy collections.** ansible-core only; a hand-installed collection vanished on VM rebuild (#156). Use `ansible.builtin.*` modules and `shell`/`command`.
- **Offline, unregistered RHEL guests.** Guests get base OS packages from the attached install DVD (`file:///media/rhel/{BaseOS,AppStream}`), exactly as `rdqm-install` does. No RHSM registration in the lab.
- **Idempotent + fail-loud.** Every provisioning step guards on observed state and fails loud on real errors (the repo's hard-won pattern — #67, #151, #158, #160). No swallowed failures.
- **Cold-rebuild acceptance gate.** The increment is "done" only after a full cold VM rebuild provisions it one-pass (memory: cold-rebuild acceptance gate). Lab bring-up is human-operated (memory: human operates the lab).
- **MQ version when it lands (Phase 2):** IBM MQ 9.4.5.0, RHEL `LinuxX64` RPM build — out of scope for Phase 1.
- **Platform:** RHEL 9.6 (`rhel/9.6-x86_64`), x86_64 / TCG — matches the RDQM arm, per the design's clean-axis decision.

---

## Phase roadmap (this plan = Phase 1)

1. **Substrate (this plan):** Corosync/Pacemaker + STONITH + iSCSI SAN on RHEL 9.6. Deliverable: `pcmk_san_rhel_ha` forms a 3-node cluster with STONITH enabled and the shared LUN logged in on all nodes.
2. **MQ:** OS-adapter `mq-install` for the RHEL RPM path (templated off `rdqm-install`, RDQM steps dropped); run QMPCMK as the `systemd:` Pacemaker resource; single-VIP intra-site failover.
3. **Multi-VIP / DMZ:** `distributed-pcmk-rhel` setup with data + partner VIPs (the #223 RDQM blocker, lifted).
4. **DR:** OS-adapter `drbd-san` (ELRepo `kmod-drbd`, offline-delivered), cross-site cutover/failback, and the add-DR-after-HA refutation.

The full-parity acceptance bar (spec §8) governs when the *arm* is done; this plan delivers Phase 1 only.

---

## Decisions to confirm before execution

These are genuine choices the plan bakes in with reasoned defaults. Confirm or redirect before running.

- **D1 — HA Add-On package delivery (BLOCKING).** The HA Add-On (`pacemaker`, `corosync`, `pcs`, `resource-agents`, `fence-agents-*`) is **not** on the RHEL binary DVD's BaseOS/AppStream. **Default in this plan:** host-fetch the HA Add-On RPMs once into the gitignored `build/rhel-ha/` directory and install them from a `file://` repo on the guest — mirroring how `mq-install`/`rdqm-install` deliver host-fetched artifacts to offline guests (Task 5). This requires obtaining the RPMs once on a machine with a RHEL HA Add-On entitlement (or an equivalent EL9 HighAvailability mirror). **Confirm:** does the lab host have an entitlement to fetch these, or do you want a different source? This is the spec §7 "HA Add-On availability/entitlement" risk made concrete.
- **D2 — New RHEL SAN/cluster nodes & IP plan.** Phase 1 adds one RHEL iSCSI target VM (`san-a-rhel`) and three RHEL cluster nodes (`pcmk-rhel-a1..3`) on a new `net-san-rhel-a` plane, following the existing `.3x` RDQM numbering and the Ubuntu arm's SAN/hb/data layout (Task 6). The concrete IPs are specified in Task 6; confirm they don't collide with anything planned.

---

## File structure

**Roles (refactor — add adapter task files, edit `main.yml` to dispatch):**
- `ansible/roles/pcmk-cluster/tasks/main.yml` — replace the `apt` install task with an `include_tasks` dispatch.
- `ansible/roles/pcmk-cluster/tasks/install-Debian.yml` — *create* (the existing apt task, moved).
- `ansible/roles/pcmk-cluster/tasks/install-RedHat.yml` — *create* (dnf + HA Add-On).
- Same three-file shape for `pcmk-stonith`, `iscsi-target`, `iscsi-initiator`.

**New role (offline repo setup, RHEL-only):**
- `ansible/roles/rhel-ha-repo/tasks/main.yml` — *create* (DVD repo + host-fetched HA Add-On `file://` repo).

**Playbook:**
- `ansible/site-pcmk-rhel.yml` — *create* (substrate-only: repo → target → initiator → cluster → stonith; no MQ in Phase 1).

**Topology:**
- `lab/topology.yaml` — add RHEL SAN/cluster nodes, the `san_a_rhel`/`pcmk_rhel_a` groups, the `pcmk-rhel` arm, and the `pcmk_san_rhel_ha` setup.

**No new Python.** `vrg-validate` lints Python/docs only; for Ansible the gate is `ansible-playbook --syntax-check` plus the cold-rebuild provision.

---

## A note on the "test" cycle for this plan

There is no Ansible unit-test framework in this repo (CI runs ruff/pytest/mkdocs on Python and docs only). For each task the red/green cycle is adapted:
- **"Failing" check:** `ansible-playbook --syntax-check ansible/site-pcmk-rhel.yml` (catches YAML/task errors) and, where stated, `grep`-level assertions that the adapter dispatch is wired.
- **"Green" check:** syntax-check passes; and at increment end, the human-operated cold-rebuild provision reaches the asserted substrate state.
- Lab-provisioning verification (the `vagrant`/`mqlab vm` runs) is **human-operated** and called out as such.

---

### Task 1: OS-adapter seam for `pcmk-cluster`

**Files:**
- Modify: `ansible/roles/pcmk-cluster/tasks/main.yml:4-8`
- Create: `ansible/roles/pcmk-cluster/tasks/install-Debian.yml`
- Create: `ansible/roles/pcmk-cluster/tasks/install-RedHat.yml`

**Interfaces:**
- Consumes: nothing new. The role's existing vars (`pcmk_site_nodes`, `pcmk_cluster_name`, `PCMK_HACLUSTER_PASSWORD`) are unchanged.
- Produces: an installed cluster stack (`pacemaker`, `corosync`, `pcs`, resource agents) on both OS families, with `pcsd` startable. Later tasks in `main.yml` (unchanged) consume it.

- [ ] **Step 1: Create the Debian adapter (move the existing apt task verbatim)**

Create `ansible/roles/pcmk-cluster/tasks/install-Debian.yml`:

```yaml
# Cluster stack on Debian/Ubuntu (the Ubuntu arm's original package set).
- name: install cluster stack (apt)
  ansible.builtin.apt:
    name: [pacemaker, corosync, pcs, resource-agents-base, resource-agents-extra]
    update_cache: true
  become: true
```

- [ ] **Step 2: Create the RedHat adapter**

Create `ansible/roles/pcmk-cluster/tasks/install-RedHat.yml`. The HA Add-On repo is established by the `rhel-ha-repo` role (Task 5) before this runs.

```yaml
# Cluster stack on RHEL: the High Availability Add-On. resource-agents is a
# single package on EL9 (not split base/extra). fence-agents-all pulls the
# fence_virsh agent the pcmk-stonith role uses. Repo set up by rhel-ha-repo.
- name: install cluster stack (dnf, HA Add-On)
  ansible.builtin.dnf:
    name: [pacemaker, corosync, pcs, resource-agents, fence-agents-all]
    state: present
  become: true
```

- [ ] **Step 3: Replace the install task in `main.yml` with an OS-family dispatch**

In `ansible/roles/pcmk-cluster/tasks/main.yml`, replace lines 4-8 (the `- name: install cluster stack` apt task) with:

```yaml
- name: install cluster stack (OS-adapter)
  ansible.builtin.include_tasks: "install-{{ ansible_os_family }}.yml"
```

Leave the rest of `main.yml` (lines 10-62) unchanged.

- [ ] **Step 4: Syntax-check**

Run: `cd /Users/pmoore/dev/projects/logical-minds-foundry/mq-cluster-tooling/.worktrees/issue-238-pcmk-rhel-arm && ansible-playbook --syntax-check ansible/site-pcmk.yml`
Expected: PASS (the Ubuntu playbook still resolves the role; `include_tasks` is valid). If `ansible` is not on PATH, run inside the container: `vrg-container-run -- ansible-playbook --syntax-check ansible/site-pcmk.yml`.

- [ ] **Step 5: Assert the dispatch is wired (gather-facts uses `ansible_os_family`)**

Run: `grep -n "include_tasks" ansible/roles/pcmk-cluster/tasks/main.yml && ls ansible/roles/pcmk-cluster/tasks/`
Expected: `main.yml` shows the include; both `install-Debian.yml` and `install-RedHat.yml` exist.

- [ ] **Step 6: Commit**

```bash
cd /Users/pmoore/dev/projects/logical-minds-foundry/mq-cluster-tooling/.worktrees/issue-238-pcmk-rhel-arm
vrg-git add ansible/roles/pcmk-cluster/
vrg-commit --type refactor --scope pcmk --message "pcmk-cluster: OS-adapter install seam (Debian/RedHat)" --body "Refs #238."
```

---

### Task 2: OS-adapter seam for `pcmk-stonith`

**Files:**
- Modify: `ansible/roles/pcmk-stonith/tasks/main.yml:4-8`
- Create: `ansible/roles/pcmk-stonith/tasks/install-Debian.yml`
- Create: `ansible/roles/pcmk-stonith/tasks/install-RedHat.yml`

**Interfaces:**
- Consumes: `fence_hypervisor_ip`, `fence_hypervisor_user`, `pcmk_site_nodes`, the `build/fence_key` (unchanged).
- Produces: `fence_virsh` agent present; STONITH primitives created (unchanged logic in `main.yml:18-35`).

- [ ] **Step 1: Create the Debian adapter**

Create `ansible/roles/pcmk-stonith/tasks/install-Debian.yml`:

```yaml
# noble ships fence_virsh in fence-agents-virsh.
- name: fence agent package (apt)
  ansible.builtin.apt:
    name: fence-agents-virsh
    update_cache: false
  become: true
```

- [ ] **Step 2: Create the RedHat adapter**

Create `ansible/roles/pcmk-stonith/tasks/install-RedHat.yml`. On EL9 the fence agents ship in `fence-agents-virsh` (pulled in by `fence-agents-all` from Task 1, but installed explicitly here so the role is self-sufficient).

```yaml
# EL9 ships fence_virsh in fence-agents-virsh (HA Add-On).
- name: fence agent package (dnf)
  ansible.builtin.dnf:
    name: fence-agents-virsh
    state: present
  become: true
```

- [ ] **Step 3: Replace the install task in `main.yml` with the dispatch**

In `ansible/roles/pcmk-stonith/tasks/main.yml`, replace lines 4-8 (the `- name: fence agent package` apt task) with:

```yaml
- name: fence agent package (OS-adapter)
  ansible.builtin.include_tasks: "install-{{ ansible_os_family }}.yml"
```

Leave lines 10-36 unchanged.

- [ ] **Step 4: Syntax-check**

Run: `vrg-container-run -- ansible-playbook --syntax-check ansible/site-pcmk.yml`
Expected: PASS.

- [ ] **Step 5: Assert the dispatch is wired**

Run: `grep -n "include_tasks" ansible/roles/pcmk-stonith/tasks/main.yml && ls ansible/roles/pcmk-stonith/tasks/`
Expected: include present; both adapter files exist.

- [ ] **Step 6: Commit**

```bash
vrg-git add ansible/roles/pcmk-stonith/
vrg-commit --type refactor --scope pcmk --message "pcmk-stonith: OS-adapter install seam (Debian/RedHat)" --body "Refs #238."
```

---

### Task 3: OS-adapter seam for `iscsi-target`

**Files:**
- Modify: `ansible/roles/iscsi-target/tasks/main.yml:5-9` and the service name at `:33-37`
- Create: `ansible/roles/iscsi-target/tasks/install-Debian.yml`
- Create: `ansible/roles/iscsi-target/tasks/install-RedHat.yml`

**Interfaces:**
- Consumes: `san_backing_dev`, `san_target_iqn`, `san_initiator_iqns`, `san_portal_ip` (unchanged).
- Produces: `targetcli` present and a configured LIO target with one block backstore `mqlun`. The `targetcli` config logic (`main.yml:11-31`) is OS-agnostic and unchanged.

- [ ] **Step 1: Create the Debian adapter**

Create `ansible/roles/iscsi-target/tasks/install-Debian.yml`:

```yaml
- name: install targetcli (apt)
  ansible.builtin.apt:
    name: targetcli-fb
    update_cache: true
  become: true
```

- [ ] **Step 2: Create the RedHat adapter**

Create `ansible/roles/iscsi-target/tasks/install-RedHat.yml`. On EL9 the package is `targetcli` (from AppStream — available on the DVD).

```yaml
- name: install targetcli (dnf)
  ansible.builtin.dnf:
    name: targetcli
    state: present
  become: true
```

- [ ] **Step 3: Replace the install task with the dispatch**

In `ansible/roles/iscsi-target/tasks/main.yml`, replace lines 5-9 (the `- name: install targetcli` apt task) with:

```yaml
- name: install targetcli (OS-adapter)
  ansible.builtin.include_tasks: "install-{{ ansible_os_family }}.yml"
```

- [ ] **Step 4: Make the boot-restore service name OS-aware**

The Debian package enables `rtslib-fb-targetctl`; EL9 uses `target.service`. Replace the task at `main.yml:33-37` with:

```yaml
- name: target service enabled (restores config at boot)
  ansible.builtin.systemd:
    name: "{{ 'target' if ansible_os_family == 'RedHat' else 'rtslib-fb-targetctl' }}"
    enabled: true
  become: true
```

- [ ] **Step 5: Syntax-check**

Run: `vrg-container-run -- ansible-playbook --syntax-check ansible/site-pcmk.yml`
Expected: PASS.

- [ ] **Step 6: Assert the dispatch and service conditional are wired**

Run: `grep -nE "include_tasks|target.*if ansible_os_family" ansible/roles/iscsi-target/tasks/main.yml && ls ansible/roles/iscsi-target/tasks/`
Expected: include present; service-name conditional present; both adapter files exist.

- [ ] **Step 7: Commit**

```bash
vrg-git add ansible/roles/iscsi-target/
vrg-commit --type refactor --scope iscsi --message "iscsi-target: OS-adapter install seam + EL9 target.service" --body "Refs #238."
```

---

### Task 4: OS-adapter seam for `iscsi-initiator`

**Files:**
- Modify: `ansible/roles/iscsi-initiator/tasks/main.yml:3-7`
- Create: `ansible/roles/iscsi-initiator/tasks/install-Debian.yml`
- Create: `ansible/roles/iscsi-initiator/tasks/install-RedHat.yml`

**Interfaces:**
- Consumes: `san_target_iqn`, `san_portal_ip` (unchanged); IQN logic at `main.yml:12-43` is OS-agnostic.
- Produces: `open-iscsi`/`iscsi-initiator-utils` present; a stable IQN; session logged in to the target.

- [ ] **Step 1: Create the Debian adapter**

Create `ansible/roles/iscsi-initiator/tasks/install-Debian.yml`:

```yaml
- name: install open-iscsi (apt)
  ansible.builtin.apt:
    name: open-iscsi
    update_cache: true
  become: true
```

- [ ] **Step 2: Create the RedHat adapter**

Create `ansible/roles/iscsi-initiator/tasks/install-RedHat.yml`. On EL9 the initiator package is `iscsi-initiator-utils` (BaseOS — on the DVD). The `iscsid` service name is the same on both families, so `main.yml:19-24` needs no change.

```yaml
- name: install iscsi-initiator-utils (dnf)
  ansible.builtin.dnf:
    name: iscsi-initiator-utils
    state: present
  become: true
```

- [ ] **Step 3: Replace the install task with the dispatch**

In `ansible/roles/iscsi-initiator/tasks/main.yml`, replace lines 3-7 (the `- name: install open-iscsi` apt task) with:

```yaml
- name: install iscsi initiator (OS-adapter)
  ansible.builtin.include_tasks: "install-{{ ansible_os_family }}.yml"
```

- [ ] **Step 4: Syntax-check**

Run: `vrg-container-run -- ansible-playbook --syntax-check ansible/site-pcmk.yml`
Expected: PASS.

- [ ] **Step 5: Assert the dispatch is wired**

Run: `grep -n "include_tasks" ansible/roles/iscsi-initiator/tasks/main.yml && ls ansible/roles/iscsi-initiator/tasks/`
Expected: include present; both adapter files exist.

- [ ] **Step 6: Commit**

```bash
vrg-git add ansible/roles/iscsi-initiator/
vrg-commit --type refactor --scope iscsi --message "iscsi-initiator: OS-adapter install seam (Debian/RedHat)" --body "Refs #238."
```

---

### Task 5: `rhel-ha-repo` role — offline DVD + HA Add-On repos (resolves D1)

**Files:**
- Create: `ansible/roles/rhel-ha-repo/tasks/main.yml`

**Interfaces:**
- Consumes: nothing (host-fetched RPMs live under `build/rhel-ha/`, gitignored, copied to the guest).
- Produces: `dnf` repos on the guest for BaseOS, AppStream (DVD) and HighAvailability (host-fetched), so Tasks 1–2's `dnf` installs resolve offline. Runs before the cluster/stonith roles in `site-pcmk-rhel.yml`.

> **D1 default (confirm before running):** the HA Add-On RPMs are fetched once on a host with an EL9 HA entitlement into `build/rhel-ha/` (gitignored). The role copies that directory to the guest and serves it as a `file://` repo with `createrepo` metadata. If you prefer guest RHSM registration or another mirror, adjust this role only — nothing else in the plan changes.

- [ ] **Step 1: Create the role**

Create `ansible/roles/rhel-ha-repo/tasks/main.yml`:

```yaml
# Offline package sources for the RHEL pcmk substrate. BaseOS+AppStream come
# from the attached install DVD (same as rdqm-install). The HA Add-On is NOT
# on the binary DVD, so its RPMs are host-fetched into build/rhel-ha/ and
# served from a local file:// repo. No RHSM registration (lab guests are
# offline). See plan D1.
- name: mount the install DVD (fstab + mount; ansible-core has no mount module)
  ansible.builtin.shell: |
    set -e
    mkdir -p /media/rhel
    grep -q '/media/rhel' /etc/fstab || echo '/dev/sr0 /media/rhel iso9660 ro 0 0' >> /etc/fstab
    mountpoint -q /media/rhel || mount /media/rhel
  become: true
  changed_when: false

- name: dnf repos from the DVD (BaseOS + AppStream)
  ansible.builtin.copy:
    dest: /etc/yum.repos.d/rhel-dvd.repo
    content: |
      [dvd-baseos]
      name=RHEL 9.6 DVD BaseOS
      baseurl=file:///media/rhel/BaseOS
      enabled=1
      gpgcheck=0
      [dvd-appstream]
      name=RHEL 9.6 DVD AppStream
      baseurl=file:///media/rhel/AppStream
      enabled=1
      gpgcheck=0
  become: true

- name: copy host-fetched HA Add-On RPMs to the guest
  ansible.builtin.copy:
    src: "{{ playbook_dir }}/../build/rhel-ha/"
    dest: /opt/rhel-ha/
  become: true

- name: createrepo for the local HA Add-On repo
  ansible.builtin.command: createrepo_c /opt/rhel-ha
  args:
    creates: /opt/rhel-ha/repodata/repomd.xml
  become: true

- name: dnf repo for the local HA Add-On
  ansible.builtin.copy:
    dest: /etc/yum.repos.d/rhel-ha-local.repo
    content: |
      [ha-local]
      name=RHEL 9.6 HA Add-On (host-fetched, local)
      baseurl=file:///opt/rhel-ha
      enabled=1
      gpgcheck=0
  become: true
```

> `createrepo_c` is in AppStream (on the DVD), so it resolves from the DVD repo set up immediately above.

- [ ] **Step 2: Document the host-fetch prerequisite**

Append to the role a `README.md` so the human knows what to stage:

Create `ansible/roles/rhel-ha-repo/README.md`:

```markdown
# rhel-ha-repo

Offline package sources for the RHEL pcmk substrate.

## One-time host prerequisite (D1)
On a machine with an EL9 High Availability entitlement, download the HA Add-On
RPMs and their deps into `build/rhel-ha/` (gitignored), e.g.:

    dnf download --resolve --downloaddir=build/rhel-ha \
      pacemaker corosync pcs resource-agents fence-agents-all fence-agents-virsh

`build/` is host-mounted and gitignored; nothing here enters git.
```

- [ ] **Step 3: Confirm `build/` is gitignored**

Run: `grep -nE "^/?build/?$|^build/" .gitignore || echo "MISSING build/ in .gitignore"`
Expected: a match for `build/`. If MISSING, add `build/` to `.gitignore` in this step and commit it with the role.

- [ ] **Step 4: Commit**

```bash
vrg-git add ansible/roles/rhel-ha-repo/
vrg-commit --type feat --scope rhel --message "rhel-ha-repo: offline DVD + host-fetched HA Add-On repos" --body "Refs #238."
```

---

### Task 6: `topology.yaml` — RHEL SAN/cluster nodes, groups, arm, setup (resolves D2)

**Files:**
- Modify: `lab/topology.yaml` (nodes block ~`:48-101`, `groups:` `:127-139`, `arms:` `:148-170`, `setups:` `:172-216`)

**Interfaces:**
- Consumes: the existing `rhel96-x86_64` box (`:16-19`).
- Produces: groups `san_a_rhel` and `pcmk_rhel_a`; arm `pcmk-rhel`; setup `pcmk_san_rhel_ha` referencing `ansible/site-pcmk-rhel.yml` (Task 7).

> **D2 IP plan (confirm):** new `.7x` host octet on the RHEL data/hb planes and a new `net-san-rhel-a` (`10.40.3.0/24`) SAN plane, mirroring the Ubuntu arm's `san-a`/`pcmk-a*` shape. Net-mgmt uses `10.50.0.7x` (unused today).

- [ ] **Step 1: Add the RHEL SAN target + cluster nodes**

In `lab/topology.yaml`, under `nodes:` (after the Pacemaker arm block, ~line 101), add:

```yaml
  # --- pcmk-rhel arm: OSS Pacemaker/SAN substrate on RHEL 9.6 (issue #238) ---
  san-a-rhel:
    platform: rhel96-x86_64
    extra_disk: 8
    nics: { net-mgmt: 10.50.0.70, net-san-rhel-a: 10.40.3.5, net-wan: 10.99.0.70 }
  pcmk-rhel-a1:
    platform: rhel96-x86_64
    cpus: 2
    memory: 2048
    nics: { net-mgmt: 10.50.0.71, net-data-a: 10.10.1.71, net-hb-a: 172.16.1.71, net-san-rhel-a: 10.40.3.71, net-wan: 10.99.0.71, net-ext: 10.60.0.71 }
  pcmk-rhel-a2:
    platform: rhel96-x86_64
    cpus: 2
    memory: 2048
    nics: { net-mgmt: 10.50.0.72, net-data-a: 10.10.1.72, net-hb-a: 172.16.1.72, net-san-rhel-a: 10.40.3.72, net-wan: 10.99.0.72, net-ext: 10.60.0.72 }
  pcmk-rhel-a3:
    platform: rhel96-x86_64
    cpus: 2
    memory: 2048
    nics: { net-mgmt: 10.50.0.73, net-data-a: 10.10.1.73, net-hb-a: 172.16.1.73, net-san-rhel-a: 10.40.3.73, net-wan: 10.99.0.73, net-ext: 10.60.0.73 }
```

- [ ] **Step 2: Add the groups**

Under `groups:` (after `pcmk_b`, ~line 131) add:

```yaml
  san_a_rhel:  [san-a-rhel]
  pcmk_rhel_a: [pcmk-rhel-a1, pcmk-rhel-a2, pcmk-rhel-a3]
```

- [ ] **Step 3: Add the `pcmk-rhel` arm**

Under `arms:` (after the `rdqm-rhel` block, ~line 170) add. Verbs are identical to `pcmk-ubuntu` — the QM is a Pacemaker `mq_group` either way (this is the structural-parity proof); the QM-create playbook is RHEL-specific (lands in Phase 2):

```yaml
  pcmk-rhel:
    mechanism: pacemaker-san
    cluster_group: pcmk_rhel_a
    verbs:
      qm-create:  { playbook: site-pcmk-rhel-qm.yml }
      qm-destroy: { playbook: site-pcmk-rhel-qm-down.yml }
      qm-up:      { pcs: "resource enable mq_group" }
      qm-down:    { pcs: "resource disable mq_group" }
      qm-status:  { pcs: "status resources" }
```

> The `site-pcmk-rhel-qm*.yml` playbooks are Phase 2 deliverables; named here so the arm registry is complete. Phase 1 does not invoke the QM verbs.

- [ ] **Step 4: Add the `pcmk_san_rhel_ha` setup**

Under `setups:` (after `pcmk_san_ha`, ~line 179) add:

```yaml
  pcmk_san_rhel_ha:
    description: RHEL Pacemaker/Corosync + iSCSI SAN — site-A 3-node HA (issue #238, substrate only in Phase 1)
    arm: pcmk-rhel
    groups: [san_a_rhel, pcmk_rhel_a]
    provision: ansible/site-pcmk-rhel.yml
    secrets: [pcmk_hacluster_password, mqweb_admin_password]
    qm: { name: QMPCMK, vip: 10.10.1.201, vip_ext: 10.60.0.11 }
```

> `vip`/`vip_ext` are declared now for Phase 2/3 use; Phase 1 provisioning does not create them.

- [ ] **Step 5: Verify topology parses and the inventory renders**

Run: `vrg-container-run -- python -c "import yaml,sys; yaml.safe_load(open('lab/topology.yaml')); print('topology OK')"`
Expected: `topology OK`. Then, if the inventory renderer is available: `mqlab vm inventory pcmk_san_rhel_ha` (human-operated) lists `san-a-rhel` and `pcmk-rhel-a1..3`.

- [ ] **Step 6: Commit**

```bash
vrg-git add lab/topology.yaml
vrg-commit --type feat --scope lab --message "topology: pcmk-rhel arm, RHEL SAN/cluster nodes, pcmk_san_rhel_ha setup" --body "Refs #238."
```

---

### Task 7: `site-pcmk-rhel.yml` playbook (substrate-only)

**Files:**
- Create: `ansible/site-pcmk-rhel.yml`

**Interfaces:**
- Consumes: groups `san_a_rhel`, `pcmk_rhel_a`; the four adapter-ized roles; the `rhel-ha-repo` role; the fence keypair logic.
- Produces: a provisioned RHEL substrate — cluster formed, STONITH enabled, LUN logged in. Mirrors `site-pcmk.yml` but targets the RHEL groups, runs `rhel-ha-repo` first, uses `dnf` for the `acl` package, and **omits `mq-install`/`mqweb`** (Phase 2).

- [ ] **Step 1: Create the playbook**

Create `ansible/site-pcmk-rhel.yml`. This mirrors `site-pcmk.yml:1-135` with RHEL group targets, the repo role prepended, and the MQ plays removed:

```yaml
# The pcmk-rhel arm (issue #238), Phase 1: OSS Corosync/Pacemaker + iSCSI SAN
# substrate on RHEL 9.6. Mirrors site-pcmk.yml but targets the RHEL groups,
# sets up offline repos first, and stops at the substrate (no MQ until Phase 2).
# Run: ansible-playbook site-pcmk-rhel.yml [--limit ...]

# Fence keypair + hypervisor authorization (STONITH prerequisite) — identical
# to site-pcmk.yml; the key is OS-agnostic and shared across arms.
- name: fence keypair + hypervisor authorization (STONITH prerequisite)
  hosts: localhost
  connection: local
  gather_facts: false
  vars:
    fence_key: "{{ playbook_dir }}/../build/fence_key"
  tasks:
    - name: generate the fence keypair once (gitignored, under build/)
      ansible.builtin.command:
        cmd: ssh-keygen -t ed25519 -N "" -C "fence_virsh@lab" -f "{{ fence_key }}"
        creates: "{{ fence_key }}"
    - name: ensure the fence user's ~/.ssh exists
      ansible.builtin.file:
        path: ~/.ssh
        state: directory
        mode: "0700"
    - name: authorize the fence public key for fence_virsh on the hypervisor
      ansible.builtin.lineinfile:
        path: ~/.ssh/authorized_keys
        line: "{{ lookup('file', fence_key + '.pub') }}"
        create: true
        mode: "0600"
        state: present

# Cold-boot guard (#151): wait for every host to accept SSH before any work.
- name: wait for all pcmk-rhel hosts to be SSH-ready (cold-boot guard)
  hosts: pcmk_san_rhel_ha
  gather_facts: false
  tasks:
    - name: wait for connection
      ansible.builtin.wait_for_connection:
        timeout: 180

# Offline package sources (DVD BaseOS/AppStream + host-fetched HA Add-On).
- hosts: pcmk_san_rhel_ha
  roles: [rhel-ha-repo]

- hosts: pcmk_san_rhel_ha
  become: true
  tasks:
    - name: acl package for unprivileged become (dnf)
      ansible.builtin.dnf:
        name: acl
        state: present

- hosts: san_a_rhel
  vars:
    san_backing_dev: /dev/vdb
    san_target_iqn: iqn.2026-06.lab.mq:san-a-rhel.lun0
    san_portal_ip: 10.40.3.5
    san_initiator_iqns:
      - iqn.2026-06.lab.mq:pcmk-rhel-a1
      - iqn.2026-06.lab.mq:pcmk-rhel-a2
      - iqn.2026-06.lab.mq:pcmk-rhel-a3
  roles: [iscsi-target]

- hosts: pcmk_rhel_a
  vars:
    san_target_iqn: iqn.2026-06.lab.mq:san-a-rhel.lun0
    san_portal_ip: 10.40.3.5
  roles: [iscsi-initiator]
  tasks:
    - name: one-time fs prep - format the shared LUN (run once)
      ansible.builtin.shell: |
        set -e
        dev=/dev/disk/by-path/ip-{{ san_portal_ip }}:3260-iscsi-{{ san_target_iqn }}-lun-0
        if blkid "$dev" 2>/dev/null | grep -q MQSHARED; then
          echo already-formatted
          exit 0
        fi
        mkfs.xfs -L MQSHARED "$dev"
        echo formatted
      args:
        executable: /bin/bash
      become: true
      run_once: true
      register: lun_mkfs
      changed_when: "'formatted' in lun_mkfs.stdout_lines"
    - name: refresh the iSCSI block view so MQSHARED resolves on every node
      ansible.builtin.shell: |
        set -e
        iscsiadm -m session --rescan
        udevadm trigger --action=change --subsystem-match=block
        udevadm settle
        test -e /dev/disk/by-label/MQSHARED
      args:
        executable: /bin/bash
      become: true
      changed_when: false

- hosts: pcmk_rhel_a
  vars: &pcmk_rhel_a_vars
    pcmk_cluster_name: mqpcmkrhel
    pcmk_site_nodes:
      - { name: pcmk-rhel-a1, hb: 172.16.1.71 }
      - { name: pcmk-rhel-a2, hb: 172.16.1.72 }
      - { name: pcmk-rhel-a3, hb: 172.16.1.73 }
    fence_hypervisor_ip: 192.168.121.1
    fence_hypervisor_user: pmoore
  roles: [pcmk-cluster]

- hosts: pcmk_rhel_a
  vars: *pcmk_rhel_a_vars
  roles: [pcmk-stonith]
```

> The STONITH `plug=lab_{{ n.name }}` (pcmk-stonith `main.yml:26`) requires the libvirt domains to be named `lab_pcmk-rhel-a1` etc. Confirm the Vagrantfile's domain-naming matches the existing `lab_<host>` convention for the new nodes (it derives from the node name, so this should hold) — verify during the first provision.

- [ ] **Step 2: Syntax-check the new playbook**

Run: `vrg-container-run -- ansible-playbook --syntax-check ansible/site-pcmk-rhel.yml`
Expected: PASS.

- [ ] **Step 3: Commit**

```bash
vrg-git add ansible/site-pcmk-rhel.yml
vrg-commit --type feat --scope lab --message "site-pcmk-rhel: Phase 1 RHEL substrate playbook (no MQ yet)" --body "Refs #238."
```

---

### Task 8: Cold-rebuild acceptance + capability-matrix wiring

**Files:**
- (No code) Human-operated lab bring-up; record outcomes in the parity run-report corpus.

**Interfaces:**
- Consumes: everything from Tasks 1–7.
- Produces: a green Phase-1 substrate run report for `pcmk_san_rhel_ha`.

- [ ] **Step 1: Stage the D1 HA Add-On RPMs (human)**

On a host with an EL9 HA entitlement, populate `build/rhel-ha/` per `ansible/roles/rhel-ha-repo/README.md`. Confirm the files are present:
Run: `ls build/rhel-ha/*.rpm | head`
Expected: pacemaker/corosync/pcs/resource-agents/fence-agents RPMs present.

- [ ] **Step 2: Cold rebuild the setup (human-operated lab op)**

This is the acceptance gate (it cannot run in the agent environment). Suggest the human run, via `! `:
```
! mqlab vm create pcmk_san_rhel_ha && mqlab vm provision pcmk_san_rhel_ha
```
Expected: provision completes one-pass with no failed tasks.

- [ ] **Step 3: Assert the substrate state (human-operated)**

On `pcmk-rhel-a1`:
```
! pcs status            # 3 nodes Online: pcmk-rhel-a1 a2 a3; stonith-enabled
! pcs stonith status    # fence_pcmk-rhel-a1..3 Started
```
On every `pcmk_rhel_a` node:
```
! test -e /dev/disk/by-label/MQSHARED && echo LUN-OK
```
Expected: three nodes online, three STONITH primitives started, `LUN-OK` on all nodes.

- [ ] **Step 4: Record the run report**

Add a `pcmk-rhel` Phase-1 entry to the parity run-report corpus (the `(setup × config × commit) → outcomes` mapping from #189), capturing the commit SHA and the asserted outcomes. Mark only the substrate rows (cluster-forms, stonith, shared-LUN) green; HA-failover / DR / distributed remain `not_yet` until later phases.

- [ ] **Step 5: Open the PR**

```bash
cd /Users/pmoore/dev/projects/logical-minds-foundry/mq-cluster-tooling/.worktrees/issue-238-pcmk-rhel-arm
vrg-git push -u origin feature/238-pcmk-rhel-arm
vrg-submit-pr   # or: vrg-gh pr create --base develop --fill
```
Expected: PR opened into `develop`, linked to #238.

---

## Self-Review

**1. Spec coverage (Phase 1 scope only):**
- Spec §3.1 arm registration → Task 6. ✅
- Spec §3.2 shared-role + OS-adapter seam → Tasks 1–4 (the four substrate roles; `mq-install` is Phase 2, `drbd-san` is Phase 4 — explicitly deferred). ✅
- Spec §2 substrate sourcing (HA Add-On) → Tasks 1 + 5. ELRepo DRBD is Phase 4 (not in the HA substrate). ✅
- Spec §6 sequencing step 1 ("substrate up on RHEL 9.6; cluster forms") → Task 8. ✅ (Step "DRBD syncs" belongs to Phase 4; the HA setup has no DRBD — noted in the roadmap.)
- Spec §7 risk "HA Add-On availability/entitlement" → D1 + Task 5. ✅
- Spec §7 risk "STONITH under TCG / #135 key" → Task 7 Step 1 reuses the fence-key authorization. ✅
- Spec §8 acceptance (cold-rebuild, run-report corpus) → Task 8. ✅ (Full three-way parity spans Phases 2–4.)

**2. Placeholder scan:** No "TBD"/"handle errors"/"similar to Task N". Every code step shows the actual YAML; D1/D2 are explicit decisions with defaults, not placeholders. ✅

**3. Type/name consistency:** `install-{{ ansible_os_family }}.yml` → files named `install-Debian.yml` / `install-RedHat.yml` in every role (matches Ansible's `ansible_os_family` values `Debian`/`RedHat`). Group names `san_a_rhel` / `pcmk_rhel_a`, arm `pcmk-rhel`, setup `pcmk_san_rhel_ha`, playbook `site-pcmk-rhel.yml`, target IQN `iqn.2026-06.lab.mq:san-a-rhel.lun0`, portal `10.40.3.5` — used consistently across Tasks 6–8. ✅

**Open items intentionally deferred:** MQ-on-RHEL RPM install (Phase 2), multi-VIP/DMZ (Phase 3), DRBD/ELRepo + DR (Phase 4). Each gets its own plan.
