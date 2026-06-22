# Phase B — Standalone QM & End-to-End Message Path Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use
> superpowers:subagent-driven-development (recommended) or
> superpowers:executing-plans to implement this plan task-by-task. Steps use
> checkbox (`- [ ]`) syntax for tracking.

**Goal:** One solid standalone queue manager (Ubuntu arm64) brought up by the
Ansible plane with its REST API enabled, configured declaratively via
`pymqrest`, exchanging FFH-pattern trade messages with a `svc-sim` queue
manager over real sender/receiver channels — proven by an end-to-end test
that survives a node reboot.

**Architecture:** Three new arm64 VM nodes join `lab/topology.yaml`
(`qm-main`, `svc-sim`, `app-client`). The bring-up plane is Ansible over
the vagrant management net (roles: `mq-install`, `mq-qmgr`, `mq-client`);
the boundary is "QM + REST online" (design §8.1); above it, all object
content is `pymqrest` `ensure_*` calls from the dev VM against each node's
REST endpoint. The trade path is native MQI (`pymqi`): a requester on
`app-client` (client connection) and a responder on `svc-sim` (bindings).

**Tech stack:** IBM MQ Advanced for Developers **9.4.5.0** arm64 Ubuntu
.debs (no-charge), Ansible (uv-managed), `pymqrest` 1.2.2 (PyPI),
`pymqi` (on nodes only), pytest for the pure-Python units.

**Design references:** spec §8 (two planes, layers L0–L4), §9 (SVC sim
model), §10-B. **Spec §5 amendment (decided 2026-06-06):** fixtures run as
**VM nodes via the same Ansible roles**, not containers — `icr.io`
publishes no arm64 MQ image (verified against the registry manifest), and
the VM route reuses the L1 role twice instead of adding container-build
infrastructure.

---

## Contents

- [Entry gate](#entry-gate)
- [File structure](#file-structure)
  - [Task 1: Python toolchain enters the repo (closes #9's loose end)](#task-1-python-toolchain-enters-the-repo-closes-9s-loose-end)
  - [Task 2: MQ acquisition (no IBM binaries in git)](#task-2-mq-acquisition-no-ibm-binaries-in-git)
  - [Task 3: Phase B nodes in the topology](#task-3-phase-b-nodes-in-the-topology)
  - [Task 4: Ansible plane — scaffolding + L1 install role](#task-4-ansible-plane--scaffolding--l1-install-role)
  - [Task 5: QM creation + REST enablement + boot services](#task-5-qm-creation--rest-enablement--boot-services)
  - [Task 6: Content plane — declarative objects via pymqrest](#task-6-content-plane--declarative-objects-via-pymqrest)
  - [Task 7: The message path — requester + responder](#task-7-the-message-path--requester--responder)
  - [Task 8: End-to-end test + reboot survival (the Phase B proof)](#task-8-end-to-end-test--reboot-survival-the-phase-b-proof)
  - [Task 9: Convergence proof + handoff](#task-9-convergence-proof--handoff)
- [Deliberately deferred](#deliberately-deferred)
- [Self-review notes](#self-review-notes)

## Entry gate

```bash
ls /dev/kvm && virsh -c qemu:///system list >/dev/null && echo gate-ok
virsh -c qemu:///system pool-list | grep -q 'default.*active' || echo "POOL MISSING - create per Phase A Task 1"
cd lab && vagrant validate
```

Plus: internet access for the one-time MQ tar download (~1 GB), and the
#24 fix merged (`cpu_mode = "maximum"` in `lab/Vagrantfile`).

## File structure

```text
pyproject.toml                 # Python enters the repo (uv-managed)
uv.lock
src/mqlab/__init__.py          # package home for lab tooling
src/mqlab/header.py               # FFH-pattern header codec (pure python)
src/mqlab/apply.py             # pymqrest declarative applier CLI
tests/test_header.py              # TDD'd codec tests (run in CI)
scripts/fetch-mq.sh            # download+verify MQ tar into build/mq/
ansible/
  ansible.cfg
  inventory.sh                 # vagrant ssh-config -> INI inventory
  site.yml                     # maps groups to roles
  group_vars/all.yml
  roles/mq-install/tasks/main.yml      # L1a: debs, license, mqm limits
  roles/mq-qmgr/tasks/main.yml         # L1b: crtmqm, listener, mqweb, systemd
  roles/mq-qmgr/templates/qm.service.j2
  roles/mq-qmgr/templates/mqweb.service.j2
  roles/mq-qmgr/templates/mqwebuser.xml.j2
  roles/mq-client/tasks/main.yml       # client/sdk debs + pymqi venv
content/qm-main.yaml           # declarative MQ objects (content plane)
content/svc-sim.yaml
clients/epn_requester.py       # pymqi client-mode requester (app-client)
clients/epn_responder.py       # pymqi bindings-mode responder (svc-sim)
lab/topology.yaml              # +3 phase-b nodes (modify)
lab/scripts/e2e-test.sh        # the proof
vergil.toml                    # primary-language returns (modify)
.github/workflows/ci.yml       # python matrix returns (modify; HUMAN PUSH)
```

Never committed: `build/mq/**` (IBM binaries), any mqweb credentials
(runtime env `MQWEB_ADMIN_USER`/`MQWEB_ADMIN_PASSWORD`), keystores.

---

### Task 1: Python toolchain enters the repo (closes #9's loose end)

**Files:** Create `pyproject.toml`, `src/mqlab/__init__.py`,
`tests/test_header.py` (first test), `src/mqlab/header.py`; Modify `vergil.toml`,
`.github/workflows/ci.yml`.

- [ ] **Step 1: Write the failing codec test** (the FFH header is spec
  §9.1's fixed-format, blank-padded, left-justified fields)

```python
# tests/test_header.py
from mqlab.header import pack_header, parse_header, validate_header, ACK_OK, ACK_BAD_HEADER, ACK_STALE_DATE

def test_pack_header_fixed_width():
    h = pack_header(password="pw", sender="APP01", receiver="SVC", session_date="20260606")
    assert len(h) == 32
    assert h == "pw      APP01  SVC 20260606"

def test_roundtrip():
    h = pack_header(password="pw", sender="APP01", receiver="SVC", session_date="20260606")
    f = parse_header(h + "PAYLOAD")
    assert (f.sender, f.receiver, f.session_date, f.payload) == ("APP01", "SVC", "20260606", "PAYLOAD")

def test_validate_ack_codes():
    good = pack_header(password="pw", sender="APP01", receiver="SVC", session_date="20260606")
    assert validate_header(good, today="20260606") == ACK_OK
    assert validate_header("short", today="20260606") == ACK_BAD_HEADER
    stale = pack_header(password="pw", sender="APP01", receiver="SVC", session_date="20250101")
    assert validate_header(stale, today="20260606") == ACK_STALE_DATE
```

- [ ] **Step 2: `pyproject.toml` + scaffold, run test to see it fail**

```toml
[project]
name = "mqlab"
version = "0.0.0"
requires-python = ">=3.12"
dependencies = ["pymqrest>=1.2,<2", "ansible-core>=2.16", "pyyaml>=6"]

[dependency-groups]
dev = ["pytest>=8"]

[build-system]
requires = ["hatchling"]
build-backend = "hatchling.build"

[tool.hatch.build.targets.wheel]
packages = ["src/mqlab"]

[tool.pytest.ini_options]
testpaths = ["tests"]
```

Run: `uv sync && uv run pytest` — expected: FAIL (no `mqlab.header`).

- [ ] **Step 3: Implement `src/mqlab/header.py` minimally**

```python
"""FFH-pattern fixed-format header (spec 9.1): blank-padded, left-justified
8-char fields - Password, Sender, Receiver, BusDate - then payload. Field
layouts are per-service and arrive at onboarding; this models the PATTERN."""
from dataclasses import dataclass

FIELD = 8
HEADER_LEN = 4 * FIELD
ACK_OK = "0000"
ACK_BAD_HEADER = "9001"
ACK_STALE_DATE = "9002"

@dataclass
class Header:
    password: str
    sender: str
    receiver: str
    session_date: str
    payload: str

def pack_header(*, password: str, sender: str, receiver: str, session_date: str) -> str:
    for name, v in (("password", password), ("sender", sender),
                    ("receiver", receiver), ("session_date", session_date)):
        if len(v) > FIELD:
            raise ValueError(f"{name} exceeds {FIELD} chars")
    return f"{password:<8}{sender:<8}{receiver:<8}{session_date:<8}"

def parse_header(msg: str) -> Header:
    if len(msg) < HEADER_LEN:
        raise ValueError("message shorter than header")
    return Header(msg[0:8].rstrip(), msg[8:16].rstrip(),
                  msg[16:24].rstrip(), msg[24:32].rstrip(), msg[32:])

def validate_header(msg: str, *, today: str) -> str:
    try:
        h = parse_header(msg)
    except ValueError:
        return ACK_BAD_HEADER
    if not (h.password and h.sender and h.receiver):
        return ACK_BAD_HEADER
    if h.session_date != today:
        return ACK_STALE_DATE
    return ACK_OK
```

Run: `uv run pytest` — expected: 3 passed.

- [ ] **Step 4: Restore the language declaration**

In `vergil.toml`: replace the `# primary-language deliberately unset…`
comment with `primary-language = "python"`, and set
`versions = ["3.12"]`. In `.github/workflows/ci.yml`: restore the
pre-#9 python shape (git history, commit `8bbc00d`'s parent has it) but
with `versions: '["3.12"]'`, `container-tag: '3.12'`,
`container-suffix: python`, and re-add the `audit`/`test` jobs.
**Note: workflow-file changes cannot be pushed by the agent identity —
this branch lands via the human's `vrg-submit-pr`, which is the normal
flow anyway.**

- [ ] **Step 5: Full validation + commit**

```bash
vrg-container-run -- vrg-validate     # now includes uv sync + pytest
vrg-git add pyproject.toml uv.lock src/ tests/ vergil.toml .github/
vrg-commit --type feat --scope tooling \
  --message "python toolchain + FFH header codec (#<N>)" \
  --body "pyproject/uv with pymqrest+ansible; TDD'd FFH fixed-format codec; primary-language restored per #9. Ref #<N>"
```

### Task 2: MQ acquisition (no IBM binaries in git)

**Files:** Create `scripts/fetch-mq.sh`; Modify `.gitignore` (only if
`build/*` does not already cover `build/mq/` — it does; verify).

- [ ] **Step 1: Write the fetch script**

```bash
#!/usr/bin/env bash
# scripts/fetch-mq.sh - download IBM MQ Advanced for Developers (no-charge)
# arm64 Ubuntu debs into gitignored build/mq/. Never commit these binaries.
set -euo pipefail
VER="9.4.5.0"
TAR="${VER}-IBM-MQ-Advanced-for-Developers-UbuntuLinuxARM64.tar.gz"
URL="https://public.dhe.ibm.com/ibmdl/export/pub/software/websphere/messaging/mqadv/${TAR}"
DEST="$(cd "$(dirname "$0")/.." && pwd)/build/mq"
mkdir -p "$DEST"
if [ ! -f "$DEST/$TAR" ]; then
  curl -fL --retry 3 -o "$DEST/$TAR.part" "$URL"
  mv "$DEST/$TAR.part" "$DEST/$TAR"
fi
# Record the checksum on first download; verify on every later run.
if [ -f "$DEST/$TAR.sha256" ]; then
  (cd "$DEST" && sha256sum -c "$TAR.sha256")
else
  (cd "$DEST" && sha256sum "$TAR" > "$TAR.sha256")
  echo "recorded checksum: $(cat "$DEST/$TAR.sha256")"
fi
tar -tzf "$DEST/$TAR" | head -5
echo "ok: $DEST/$TAR"
```

- [ ] **Step 2: Run it, confirm contents are Ubuntu .debs, commit**

Run: `chmod +x scripts/fetch-mq.sh && scripts/fetch-mq.sh`
Expected: tar listing shows `ibmmq-*.deb` entries (runtime, server, sdk,
client, web, …). If the tar layout differs (e.g. nested `MQServer/`),
record the actual layout — the `mq-install` role consumes it in Task 4.

```bash
vrg-git add scripts/fetch-mq.sh
vrg-commit --type feat --scope lab --message "MQ 9.4.5.0 arm64 fetch script (#<N>)" \
  --body "No-charge Developers edition tar into gitignored build/mq with pinned version and recorded sha256. Ref #<N>"
```

### Task 3: Phase B nodes in the topology

**Files:** Modify `lab/topology.yaml`.

- [ ] **Step 1: Add three nodes (memory: MQ needs headroom)**

```yaml
  # --- Phase B: standalone QM + fixtures (spec 10-B; 5-amendment: VMs) ---
  qm-main:
    cpus: 2
    memory: 2048
    nics: { net-client: 10.30.0.10, net-svc: 10.20.0.10 }
  svc-sim:
    cpus: 2
    memory: 2048
    nics: { net-svc: 10.20.0.50 }
  app-client:
    nics: { net-client: 10.30.0.60 }
```

- [ ] **Step 2: Boot and verify**

```bash
lab/scripts/net-up.sh
cd lab && vagrant up qm-main svc-sim app-client --provider=libvirt
vagrant ssh qm-main -c 'ip -br addr | grep -c 10\.'   # expect 2 topology IPs
```

- [ ] **Step 3: Commit** (`feat(lab): phase-b nodes qm-main/svc-sim/app-client`)

### Task 4: Ansible plane — scaffolding + L1 install role

**Files:** Create `ansible/ansible.cfg`, `ansible/inventory.sh`,
`ansible/site.yml`, `ansible/group_vars/all.yml`,
`ansible/roles/mq-install/tasks/main.yml`,
`ansible/roles/mq-client/tasks/main.yml`.

- [ ] **Step 1: Inventory generator from vagrant**

```bash
#!/usr/bin/env bash
# ansible/inventory.sh - render an INI inventory from vagrant ssh-config.
# Usage: ansible/inventory.sh > build/inventory.ini
set -euo pipefail
cd "$(dirname "$0")/../lab"
{
  echo "[qm_hosts]"; echo "qm-main"; echo "svc-sim"
  echo "[client_hosts]"; echo "app-client"
  echo "[all:vars]"
  echo "ansible_user=vagrant"
  echo "ansible_python_interpreter=/usr/bin/python3"
} > ../build/inventory.ini
for h in qm-main svc-sim app-client; do
  cfg=$(vagrant ssh-config "$h")
  hn=$(awk '/HostName/{print $2}' <<<"$cfg")
  port=$(awk '/Port/{print $2}' <<<"$cfg")
  key=$(awk '/IdentityFile/{print $2}' <<<"$cfg")
  sed -i "s|^${h}\$|${h} ansible_host=${hn} ansible_port=${port} ansible_ssh_private_key_file=${key} ansible_ssh_common_args='-o StrictHostKeyChecking=no'|" ../build/inventory.ini
done
echo "wrote build/inventory.ini" >&2
```

- [ ] **Step 2: `ansible.cfg` + ping**

```ini
# ansible/ansible.cfg
[defaults]
inventory = ../build/inventory.ini
host_key_checking = False
retry_files_enabled = False
```

Run: `ansible/inventory.sh && cd ansible && ansible -m ping all`
Expected: three `SUCCESS` pongs.

- [ ] **Step 3: `mq-install` role** (consumes the Task-2 tar; L0 prep is
  folded in — it is small at dev scale)

```yaml
# ansible/roles/mq-install/tasks/main.yml
# L1a: unpack the (host-fetched, rsynced) MQ debs and install server set.
- name: copy MQ tar to node
  ansible.builtin.copy:
    src: "{{ playbook_dir }}/../build/mq/9.4.5.0-IBM-MQ-Advanced-for-Developers-UbuntuLinuxARM64.tar.gz"
    dest: /tmp/mqadv.tar.gz
- name: unpack
  ansible.builtin.unarchive:
    src: /tmp/mqadv.tar.gz
    dest: /tmp
    remote_src: true
    creates: /tmp/MQServer
- name: accept developer licence
  ansible.builtin.command: /tmp/MQServer/mqlicense.sh -accept
  args: { creates: /var/mqm }     # licence acceptance precedes install
- name: install server debs
  ansible.builtin.shell: |
    set -e
    cd /tmp/MQServer
    apt-get install -y ./ibmmq-runtime_*.deb ./ibmmq-server_*.deb \
      ./ibmmq-java_*.deb ./ibmmq-jre_*.deb ./ibmmq-gskit_*.deb \
      ./ibmmq-web_*.deb ./ibmmq-msg-*.deb
  args: { creates: /opt/mqm/bin/crtmqm }
  become: true
- name: mqm ulimits
  ansible.builtin.copy:
    dest: /etc/security/limits.d/99-mqm.conf
    content: |
      mqm soft nofile 10240
      mqm hard nofile 10240
  become: true
```

*(Verify the unpacked directory name and deb list against the Task-2 tar
listing on first run — `creates:` guards make re-runs convergent. If the
tar root is not `MQServer`, adjust both `creates:` and paths once,
here only.)*

- [ ] **Step 4: `mq-client` role** (app-client: client libs + pymqi venv)

```yaml
# ansible/roles/mq-client/tasks/main.yml
- name: copy MQ tar to node
  ansible.builtin.copy:
    src: "{{ playbook_dir }}/../build/mq/9.4.5.0-IBM-MQ-Advanced-for-Developers-UbuntuLinuxARM64.tar.gz"
    dest: /tmp/mqadv.tar.gz
- name: unpack
  ansible.builtin.unarchive: { src: /tmp/mqadv.tar.gz, dest: /tmp, remote_src: true, creates: /tmp/MQServer }
- name: accept developer licence
  ansible.builtin.command: /tmp/MQServer/mqlicense.sh -accept
  args: { creates: /var/mqm }
- name: install client + sdk debs and build deps
  ansible.builtin.shell: |
    set -e
    apt-get update
    apt-get install -y python3-venv python3-dev gcc
    cd /tmp/MQServer
    apt-get install -y ./ibmmq-runtime_*.deb ./ibmmq-client_*.deb ./ibmmq-sdk_*.deb ./ibmmq-gskit_*.deb
  args: { creates: /opt/mqm/inc/cmqc.h }
  become: true
- name: pymqi venv
  ansible.builtin.shell: |
    set -e
    python3 -m venv /home/vagrant/mqvenv
    /home/vagrant/mqvenv/bin/pip install pymqi
  args: { creates: /home/vagrant/mqvenv/bin/python }
```

- [ ] **Step 5: `site.yml`, run it, commit**

```yaml
# ansible/site.yml
- hosts: qm_hosts
  roles: [mq-install]
- hosts: client_hosts
  roles: [mq-client]
```

Run: `cd ansible && ansible-playbook site.yml`
Expected: ok/changed across all three, zero failed; re-run: zero changed
(idempotence). `vagrant ssh qm-main -c '/opt/mqm/bin/dspmqver'` shows
9.4.5.0. Commit (`feat(ansible): L1 install roles`).

### Task 5: QM creation + REST enablement + boot services

**Files:** Create `ansible/roles/mq-qmgr/tasks/main.yml` and the three
templates; Modify `ansible/site.yml`, `ansible/group_vars/all.yml`.

- [ ] **Step 1: Role variables** (`group_vars/all.yml`)

```yaml
# Per-host overrides in site.yml. Web admin creds are runtime-injected:
# export MQWEB_ADMIN_USER / MQWEB_ADMIN_PASSWORD before ansible-playbook.
mqweb_admin_user: "{{ lookup('env', 'MQWEB_ADMIN_USER') }}"
mqweb_admin_password: "{{ lookup('env', 'MQWEB_ADMIN_PASSWORD') }}"
```

- [ ] **Step 2: The qmgr role**

```yaml
# ansible/roles/mq-qmgr/tasks/main.yml
# L1b: create the QM, listener via MQSC at create-time, enable mqweb REST,
# and install boot services so reboot brings everything back (spec 8.3).
- name: create queue manager
  ansible.builtin.command: /opt/mqm/bin/crtmqm -u SYSTEM.DEAD.LETTER.QUEUE {{ qmgr_name }}
  args: { creates: "/var/mqm/qmgrs/{{ qmgr_name }}" }
  become: true
  become_user: mqm
- name: qm systemd unit
  ansible.builtin.template: { src: qm.service.j2, dest: "/etc/systemd/system/mq-{{ qmgr_name }}.service" }
  become: true
- name: mqweb systemd unit
  ansible.builtin.template: { src: mqweb.service.j2, dest: /etc/systemd/system/mqweb.service }
  become: true
- name: mqweb basic registry (creds from env, never committed)
  ansible.builtin.template:
    src: mqwebuser.xml.j2
    dest: /var/mqm/web/installations/Installation1/servers/mqweb/mqwebuser.xml
    owner: mqm
  become: true
  no_log: true
- name: bind mqweb to all interfaces
  ansible.builtin.command: "/opt/mqm/bin/setmqweb properties -k httpHost -v '*'"
  become: true
  become_user: mqm
  register: setmqweb
  changed_when: setmqweb.rc == 0
  failed_when: false   # idempotent property set; first run may precede server start
- name: enable + start services
  ansible.builtin.systemd:
    name: "{{ item }}"
    enabled: true
    state: started
    daemon_reload: true
  loop: ["mq-{{ qmgr_name }}.service", "mqweb.service"]
  become: true
- name: define + start listener via MQSC (idempotent)
  ansible.builtin.shell: |
    echo "DEFINE LISTENER(L1414) TRPTYPE(TCP) PORT(1414) CONTROL(QMGR) REPLACE
    START LISTENER(L1414)" | /opt/mqm/bin/runmqsc {{ qmgr_name }}
  become: true
  become_user: mqm
  register: lsr
  changed_when: "'AMQ8626' not in lsr.stdout"
  failed_when: lsr.rc not in [0, 10]   # 10 = MQSC warnings (already started)
```

```ini
# ansible/roles/mq-qmgr/templates/qm.service.j2
[Unit]
Description=IBM MQ queue manager {{ qmgr_name }}
After=network-online.target
[Service]
Type=forking
User=mqm
ExecStart=/opt/mqm/bin/strmqm {{ qmgr_name }}
ExecStop=/opt/mqm/bin/endmqm -w {{ qmgr_name }}
TimeoutStartSec=300
[Install]
WantedBy=multi-user.target
```

```ini
# ansible/roles/mq-qmgr/templates/mqweb.service.j2
[Unit]
Description=IBM MQ web/REST server
After=network-online.target
[Service]
Type=forking
User=mqm
ExecStart=/opt/mqm/bin/strmqweb
ExecStop=/opt/mqm/bin/endmqweb
TimeoutStartSec=300
[Install]
WantedBy=multi-user.target
```

```xml
<!-- ansible/roles/mq-qmgr/templates/mqwebuser.xml.j2
     Dev-convenience basic registry (security out of scope, spec 1).
     Credentials arrive from the environment at deploy time. -->
<server>
  <featureManager><feature>appSecurity-2.0</feature><feature>basicAuthenticationMQ-1.0</feature></featureManager>
  <enterpriseApplication id="com.ibm.mq.rest">
    <application-bnd>
      <security-role name="MQWebAdmin">
        <user name="{{ mqweb_admin_user }}" realm="defaultRealm"/>
      </security-role>
    </application-bnd>
  </enterpriseApplication>
  <basicRegistry id="basic" realm="defaultRealm">
    <user name="{{ mqweb_admin_user }}" password="{{ mqweb_admin_password }}"/>
  </basicRegistry>
  <variable name="httpHost" value="*"/>
  <sslDefault sslRef="mqDefaultSSLConfig"/>
</server>
```

- [ ] **Step 3: Wire into site.yml with per-host QM names**

```yaml
- hosts: qm-main
  vars: { qmgr_name: QMAIN }
  roles: [mq-qmgr]
- hosts: svc-sim
  vars: { qmgr_name: QMSVC }
  roles: [mq-qmgr]
```

- [ ] **Step 4: Run, then verify REST from the dev VM (the §8.1 boundary)**

```bash
export MQWEB_ADMIN_USER=mqadmin MQWEB_ADMIN_PASSWORD=$(openssl rand -hex 12)
cd ansible && ansible-playbook site.yml
curl -sk -u "$MQWEB_ADMIN_USER:$MQWEB_ADMIN_PASSWORD" \
  https://10.30.0.10:9443/ibmmq/rest/v2/admin/qmgr | head -3
curl -sk -u "$MQWEB_ADMIN_USER:$MQWEB_ADMIN_PASSWORD" \
  https://10.20.0.50:9443/ibmmq/rest/v2/admin/qmgr | head -3
```

Expected: JSON listing `QMAIN` / `QMSVC` running. *(The mqwebuser.xml
template is validated here; if the 9.4.5 sample schema differs, reconcile
against `/opt/mqm/web/mq/samp/configuration/basic_registry.xml` on the
node — one file, one place.)*

- [ ] **Step 5: Commit** (`feat(ansible): QM + REST + boot services role`)

### Task 6: Content plane — declarative objects via pymqrest

**Files:** Create `src/mqlab/apply.py`, `content/qm-main.yaml`,
`content/svc-sim.yaml`, `tests/test_apply.py`.

- [ ] **Step 1: Confirm the exact `ensure_*` surface** (verification, not
  guesswork — the README documents the pattern but not every name)

```bash
uv run python -c "import pymqrest, inspect; \
print([m for m in dir(pymqrest.MQRESTSession) if m.startswith('ensure_')])"
```

Record the names; the applier's `KIND_TO_METHOD` map (below) uses them.

- [ ] **Step 2: Declarative definitions — the §9.1 distributed-queuing
  shape** (reciprocal SDR/RCVR channels, xmitqs, remote qdefs)

```yaml
# content/qm-main.yaml — objects on QMAIN
qmgr: QMAIN
objects:
  - { kind: local_queue,        name: APP.REPLY }
  - { kind: local_queue,        name: QMSVC,            attrs: { usage: transmission } }
  - { kind: remote_queue,       name: SVC.REQUEST,
      attrs: { remote_qmgr: QMSVC, remote_queue: SVC.REQUEST, transmission_queue: QMSVC } }
  - { kind: sender_channel,     name: QMAIN.QMSVC,
      attrs: { connection_name: "10.20.0.50(1414)", transmission_queue: QMSVC } }
  - { kind: receiver_channel,   name: QMSVC.QMAIN }
  - { kind: server_conn_channel, name: APP.SVRCONN }
```

```yaml
# content/svc-sim.yaml — objects on QMSVC
qmgr: QMSVC
objects:
  - { kind: local_queue,        name: SVC.REQUEST }
  - { kind: local_queue,        name: QMAIN,            attrs: { usage: transmission } }
  - { kind: remote_queue,       name: APP.REPLY,
      attrs: { remote_qmgr: QMAIN, remote_queue: APP.REPLY, transmission_queue: QMAIN } }
  - { kind: sender_channel,     name: QMSVC.QMAIN,
      attrs: { connection_name: "10.20.0.10(1414)", transmission_queue: QMAIN } }
  - { kind: receiver_channel,   name: QMAIN.QMSVC }
```

- [ ] **Step 3: The applier** (attribute names mapped per the Step-1
  introspection + pymqrest docs; the structure is fixed, the map is thin)

```python
# src/mqlab/apply.py
"""Apply declarative MQ object definitions through pymqrest ensure_*.
Usage: python -m mqlab.apply content/qm-main.yaml https://10.30.0.10:9443
Credentials from MQWEB_ADMIN_USER / MQWEB_ADMIN_PASSWORD (never committed)."""
import os, sys, yaml
from pymqrest import MQRESTSession, BasicAuth   # adjust import per Step 1

KIND_TO_METHOD = {
    # filled from the Step-1 introspection, e.g.:
    # "local_queue": "ensure_local_queue", "remote_queue": "ensure_remote_queue", ...
}

def main() -> int:
    spec_path, base_url = sys.argv[1], sys.argv[2]
    spec = yaml.safe_load(open(spec_path))
    session = MQRESTSession(
        rest_base_url=f"{base_url}/ibmmq/rest/v2",
        qmgr_name=spec["qmgr"],
        credentials=BasicAuth(os.environ["MQWEB_ADMIN_USER"],
                              os.environ["MQWEB_ADMIN_PASSWORD"]),
        verify_tls=False,   # lab self-signed; security out of scope (spec 1)
    )
    rc = 0
    for obj in spec["objects"]:
        method = getattr(session, KIND_TO_METHOD[obj["kind"]])
        result = method(name=obj["name"], **obj.get("attrs", {}))
        print(f"{spec['qmgr']} {obj['kind']:<20} {obj['name']:<20} {result.action.name}")
    return rc

if __name__ == "__main__":
    sys.exit(main())
```

`tests/test_apply.py`: unit-test `KIND_TO_METHOD` covers every `kind`
used in `content/*.yaml` (pure file parse — no live QM in CI).

- [ ] **Step 4: Apply to both QMs; prove convergence and the channels**

```bash
python -m mqlab.apply content/qm-main.yaml  https://10.30.0.10:9443
python -m mqlab.apply content/svc-sim.yaml https://10.20.0.50:9443
# second run: every line UNCHANGED (the pymqrest drift-detection showcase)
# then start senders once (runmqsc via ansible ad-hoc or REST) and check:
cd ansible && ansible qm-main -b --become-user=mqm -m shell \
  -a 'echo "START CHANNEL(QMAIN.QMSVC)" | /opt/mqm/bin/runmqsc QMAIN'
ansible svc-sim -b --become-user=mqm -m shell \
  -a 'echo "START CHANNEL(QMSVC.QMAIN)" | /opt/mqm/bin/runmqsc QMSVC'
```

Expected: both sender channels reach RUNNING (verify via pymqrest channel
status or `DIS CHSTATUS(*)`). Commit (`feat(content): declarative QM
objects via pymqrest`).

### Task 7: The message path — requester + responder

**Files:** Create `clients/epn_requester.py`, `clients/epn_responder.py`;
Modify `ansible/site.yml` (deploy clients + `src/mqlab/header.py` to nodes).

- [ ] **Step 1: Responder (bindings mode, runs on svc-sim)**

```python
# clients/epn_responder.py — SVC-side: get SVC.REQUEST, validate the
# FFH header, reply with ACK code via APP.REPLY. Bindings mode (local QM).
import sys, datetime, pymqi
from header import validate_header, parse_header, pack_header, ACK_OK

def main(count: int) -> int:
    qmgr = pymqi.connect("QMSVC")          # bindings: no channel/conninfo
    qin = pymqi.Queue(qmgr, "SVC.REQUEST")
    qout = pymqi.Queue(qmgr, "APP.REPLY")
    today = datetime.date.today().strftime("%Y%m%d")
    gmo = pymqi.GMO(Options=pymqi.CMQC.MQGMO_WAIT, WaitInterval=30_000)
    for _ in range(count):
        msg = qin.get(None, pymqi.MD(), gmo).decode()
        ack = validate_header(msg, today=today)
        h = parse_header(msg) if ack == ACK_OK else None
        seq = h.payload if h else "?"
        reply = pack_header(password="pw", sender="SVC",
                            receiver="APP01", session_date=today) + f"{ack}:{seq}"
        qout.put(reply.encode())
    qmgr.disconnect()
    return 0

if __name__ == "__main__":
    sys.exit(main(int(sys.argv[1])))
```

- [ ] **Step 2: Requester (client mode from app-client via APP.SVRCONN)**

```python
# clients/epn_requester.py — app-side: put N trades to SVC.REQUEST on
# QMAIN over a client connection, then get N ACKs from APP.REPLY.
import sys, datetime, pymqi
from header import pack_header, parse_header

def main(count: int) -> int:
    today = datetime.date.today().strftime("%Y%m%d")
    cd = pymqi.CD(ChannelName=b"APP.SVRCONN",
                  ConnectionName=b"10.30.0.10(1414)",
                  TransportType=pymqi.CMQC.MQXPT_TCP)
    qmgr = pymqi.QueueManager(None)
    qmgr.connect_with_options("QMAIN", cd=cd)
    qreq = pymqi.Queue(qmgr, "SVC.REQUEST")
    qrep = pymqi.Queue(qmgr, "APP.REPLY")
    for i in range(count):
        qreq.put((pack_header(password="pw", sender="APP01",
                              receiver="SVC", session_date=today) + f"MSG-{i:04d}").encode())
    gmo = pymqi.GMO(Options=pymqi.CMQC.MQGMO_WAIT, WaitInterval=60_000)
    acks = 0
    for _ in range(count):
        reply = qrep.get(None, pymqi.MD(), gmo).decode()
        body = parse_header(reply).payload
        code, seq = body.split(":", 1)
        print(f"ack {seq}: {code}")
        if code == "0000":
            acks += 1
    qmgr.disconnect()
    print(f"{acks}/{count} clean ACKs")
    return 0 if acks == count else 1

if __name__ == "__main__":
    sys.exit(main(int(sys.argv[1])))
```

*(Client-mode MQCONN with relaxed CHLAUTH is a dev-edition convenience —
if QMAIN rejects the connection, disable CHLAUTH for the lab:
`ALTER QMGR CHLAUTH(DISABLED)` + `ALTER QMGR CONNAUTH(' ')` +
`REFRESH SECURITY`, via the content plane or runmqsc. Security is
explicitly out of scope, spec §1.)*

- [ ] **Step 3: Deploy via ansible** — add a `copy` task pushing
  `clients/*.py` + `src/mqlab/header.py` (as `header.py`) to `svc-sim` and
  `app-client` home dirs; re-run `site.yml`.

- [ ] **Step 4: Manual first pass**

```bash
cd lab
vagrant ssh svc-sim  -c '~/mqvenv/bin/python ~/epn_responder.py 3' &
sleep 2
vagrant ssh app-client -c '~/mqvenv/bin/python ~/epn_requester.py 3'
```

Expected: `3/3 clean ACKs`, requester exit 0. Commit
(`feat(clients): FFH requester/responder over the trade path`).

### Task 8: End-to-end test + reboot survival (the Phase B proof)

**Files:** Create `lab/scripts/e2e-test.sh`.

- [ ] **Step 1: The orchestrated test**

```bash
#!/usr/bin/env bash
# lab/scripts/e2e-test.sh — N trades through app-client -> QMAIN ->
# channel -> QMSVC -> responder -> back. Exits non-zero unless every
# trade round-trips with a clean ACK.
set -euo pipefail
N="${1:-5}"
cd "$(dirname "$0")/.."
vagrant ssh svc-sim -c "~/mqvenv/bin/python ~/epn_responder.py $N" &
RESP=$!
sleep 3
vagrant ssh app-client -c "~/mqvenv/bin/python ~/epn_requester.py $N"
RC=$?
wait "$RESP"
exit "$RC"
```

Run: `chmod +x lab/scripts/e2e-test.sh && lab/scripts/e2e-test.sh 5`
Expected: `5/5 clean ACKs`, exit 0.

- [ ] **Step 2: Reboot survival (spec §8.3 boot-services requirement)**

```bash
cd lab && vagrant reload qm-main
# wait for SSH, then services must be up WITHOUT manual action:
vagrant ssh qm-main -c 'systemctl is-active mq-QMAIN.service mqweb.service'
lab/scripts/e2e-test.sh 3
```

Expected: both `active`; `3/3 clean ACKs`. This is the §8.3 "comes up
correctly on reboot" requirement, tested.

- [ ] **Step 3: Commit** (`feat(lab): e2e trade-path test incl. reboot survival`)

### Task 9: Convergence proof + handoff

- [ ] **Step 1: Full re-run is a no-op** — `ansible-playbook site.yml`
  (zero changed), both `apply` runs (all `UNCHANGED`), e2e green.
- [ ] **Step 2: `vrg-container-run -- vrg-validate`** — green, now with
  the Python gates.
- [ ] **Step 3: Final commit; write `.vergil/pr-template.yml`** (issue
  `<N>`, note the human push requirement from Task 1's workflow change).

---

## Deliberately deferred

- TLS on channels / REST hardening — out of scope (spec §1); those security standards
  onboarding-ready TLS config is a later, requirements-driven task.
- SVC multi-queue inbound delivery modeling (spec §9.1) — Phase C/DR
  testing territory.
- Guest service-surface minimization — with Phase C's cluster nodes.
- `pymqrest` MIT relicense — tracked for Phase F packaging (spec §11).

## Self-review notes

- **Spec coverage:** §10-B fully — bring-up plane (Tasks 4–5), REST
  boundary (Task 5 Step 4 curl), content plane via `ensure_*` (Task 6),
  fixtures + native-MQI path (Task 7), e2e validation (Task 8). §8.3 boot
  services explicitly tested (Task 8 Step 2). §5 amendment recorded in the
  header. #9's Python-restoration note closed by Task 1.
- **Honest unknowns are verification steps, not placeholders:** the tar's
  internal layout (Task 2/4), the exact `ensure_*` method names (Task 6
  Step 1 introspection), the 9.4.5 mqwebuser.xml schema (Task 5 Step 4),
  CHLAUTH behavior (Task 7 note) — each has a named, single-file
  reconciliation point.
- **Consistency:** QM names (QMAIN/QMSVC), channel names (QMAIN.QMSVC /
  QMSVC.QMAIN), queue names (SVC.REQUEST/APP.REPLY, xmitqs named for
  the remote QM), IPs (10.30.0.10, 10.20.0.50, 10.30.0.60) match across
  topology, content YAML, clients, and curl checks.
- **Secrets:** IBM binaries stay in gitignored `build/mq/`; mqweb creds
  are env-injected with `no_log: true`; nothing sensitive lands in git.
