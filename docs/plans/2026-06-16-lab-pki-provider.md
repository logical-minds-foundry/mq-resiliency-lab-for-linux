# Lab PKI / TLS Certificate Provider — Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Build a reproducible, idempotent two-organization certificate-authority provider for the lab that issues per-entity PKCS#12 key repositories, driven by an `mqlab pki` command group.

**Architecture:** An Ansible role (`lab-pki`) using the `community.crypto` collection builds two independent org CAs (`client-org`, `dtcc-org`), issues each entity's key + cert, and assembles per-entity PKCS#12 keystores (personal cert + key + the signer certs that entity must trust) under `build/secrets/pki/`. A `connection=local` playbook (`site-pki.yml`) runs it on the controller; the `mqlab pki` Typer group wraps the playbook following the established `mqlab qm` pattern. Keystore passwords come from the existing `lab/scripts/lab-secret.sh` (generate-persist-inject). Certificate expiry/rotation is **out of scope** (spec §8.2).

**Tech Stack:** Ansible (`ansible-core` + `community.crypto`), `cryptography` (Python), Typer/Rich (`mqlab` CLI), pytest, IBM MQ PKCS#12 key repositories (`SSLKEYR`/`KEYRPWD`).

**Spec:** `docs/specs/2026-06-16-lab-pki-design.md` (issue #201).

---

## File Structure

**Phase 0 — galaxy provisioning (spec §3 / pushback [1]):**
- Modify `pyproject.toml` — add `cryptography` to `dependencies`.
- Create `ansible/requirements.yml` — declare `community.crypto`.
- Modify `ansible/ansible.cfg` — `collections_path`.
- Modify `docs/reference/lab-bootstrap.md` — galaxy install prereq after `uv sync`.

**Phase 1 — the CA provider role:**
- Create `ansible/vars/pki-entities.yml` — the orgs + entity inventory (DNs, trust sets).
- Create `ansible/roles/lab-pki/defaults/main.yml` — paths, validity, encryption level.
- Create `ansible/roles/lab-pki/tasks/main.yml` — orchestration (CAs, then entities).
- Create `ansible/roles/lab-pki/tasks/ca.yml` — one org CA.
- Create `ansible/roles/lab-pki/tasks/entity.yml` — one entity (key, CSR, sign, PKCS#12).
- Create `ansible/site-pki.yml` — `connection=local` playbook.

**Phase 2 — the `mqlab pki` CLI (TDD):**
- Modify `src/mqlab/cli.py` — `pki_app` Typer group (`ensure`, `issue`, `list`).
- Create `tests/test_cli_pki.py` — mirrors `tests/test_cli_qm.py`.

**Phase 3 — verification gates:**
- PKCS#12 ↔ MQ load check (pushback [2]) — blocking, with `runmqktool` fallback.
- Cold-rebuild acceptance gate (spec §3, §11).

Each task ends green under `vrg-container-run -- vrg-validate` and a `vrg-commit`.

---

## Phase 0 — Galaxy provisioning foundation

### Task 1: Provision `community.crypto` reproducibly

**Files:**
- Modify: `pyproject.toml` (line 7, `dependencies`)
- Create: `ansible/requirements.yml`
- Modify: `ansible/ansible.cfg`
- Modify: `docs/reference/lab-bootstrap.md` (Prerequisites section)

- [ ] **Step 1: Add `cryptography` to the project dependencies**

In `pyproject.toml`, change the `dependencies` line to add `cryptography` (the `community.crypto` module backend; installed by `uv sync`):

```toml
dependencies = ["pymqrest>=1.2,<2", "ansible-core>=2.16", "pyyaml>=6", "typer>=0.12", "rich>=13", "cryptography>=42"]
```

- [ ] **Step 2: Declare the collection**

Create `ansible/requirements.yml`:

```yaml
---
# The lab's FIRST galaxy collection (otherwise ansible.builtin only — #156).
# Provisioned declaratively + installed at bootstrap into the ansible.cfg
# collections_path, never hand-installed. See docs/specs/2026-06-16-lab-pki-design.md §3.
collections:
  - name: community.crypto
    version: ">=2.20.0"
```

- [ ] **Step 3: Point ansible at a repo-relative collections path**

Modify `ansible/ansible.cfg` so the installed collection is found (path is under the gitignored, restart-surviving `build/`; reinstalled on rebuild by Step 5):

```ini
[defaults]
inventory = ../build/inventory.ini
host_key_checking = False
retry_files_enabled = False
collections_path = ../build/ansible_collections
```

- [ ] **Step 4: Run validation**

Run: `vrg-container-run -- vrg-validate`
Expected: PASS (pyproject + cfg are well-formed; no behavior change yet).

- [ ] **Step 5: Document the bootstrap install step**

In `docs/reference/lab-bootstrap.md`, under "Prerequisites (once per VM)", immediately after the `uv sync` block, add:

````markdown
Install the lab's Ansible collection (the only one — `community.crypto`, for the
PKI provider). Declarative + reproducible; reinstalls cleanly on a fresh VM:

```bash
ansible-galaxy collection install -r ansible/requirements.yml -p build/ansible_collections
```
````

- [ ] **Step 6: Commit**

```bash
vrg-git add pyproject.toml ansible/requirements.yml ansible/ansible.cfg docs/reference/lab-bootstrap.md
vrg-commit --type build --scope pki --message "provision community.crypto reproducibly for the PKI provider (#201)" --body "Add cryptography to project deps; declare community.crypto in ansible/requirements.yml; set ansible.cfg collections_path; document the ansible-galaxy bootstrap step. The lab's first galaxy collection, provisioned declaratively (never hand-installed) per spec 3 / pushback 1."
```

---

## Phase 1 — The CA provider role

### Task 2: The entity inventory (orgs + DNs + trust sets)

**Files:**
- Create: `ansible/vars/pki-entities.yml`

- [ ] **Step 1: Write the inventory**

Create `ansible/vars/pki-entities.yml`. `org` selects the issuing CA; `trust` lists the CA names whose signer certs go into this entity's keystore (own org always implied; cross-org peers add the other CA). The in-house QMs share `O`/`OU` with distinct `CN`s (spec §5, partial-DN identity).

```yaml
---
# Two independent org CAs (spec §4). Each CA: name -> subject.
pki_cas:
  client-org:
    common_name: "client-org Root CA"
    organization_name: "client-org"
  dtcc-org:
    common_name: "dtcc-org Root CA"
    organization_name: "dtcc-org"

# Entities. cn = certificate CN; org = issuing CA; ou = organizational unit;
# trust = extra CA names this entity must trust (beyond its own org CA);
# kind = personal (gets key+cert+keystore) | trust_only (gets a CA bundle only).
pki_entities:
  - { cn: QMPCMK,        org: client-org, ou: clearing-service, kind: personal,   trust: [dtcc-org] }
  - { cn: QMRDQM,        org: client-org, ou: clearing-service, kind: personal,   trust: [dtcc-org] }
  - { cn: app-client,    org: client-org, ou: apps,             kind: personal,   trust: [] }
  - { cn: mq_prometheus, org: client-org, ou: ops,              kind: personal,   trust: [] }
  - { cn: mqweb,         org: client-org, ou: ops,              kind: personal,   trust: [] }
  - { cn: pymqrest,      org: client-org, ou: ops,              kind: trust_only, trust: [] }
  - { cn: QMDTCC,        org: dtcc-org,   ou: clearing-service, kind: personal,   trust: [client-org] }
```

- [ ] **Step 2: Validate YAML loads**

Run: `vrg-container-run -- vrg-validate`
Expected: PASS (ansible-lint/yamllint accept the vars file).

- [ ] **Step 3: Commit**

```bash
vrg-git add ansible/vars/pki-entities.yml
vrg-commit --type feat --scope pki --message "add the two-org PKI entity inventory (#201)" --body "Declares the client-org and dtcc-org CAs and the base-lab entities with their DNs (O/OU/CN), issuing CA, trust sets, and personal-vs-trust-only kind. In-house QMs share O/OU with distinct CNs (partial-DN identity, spec 5)."
```

### Task 3: Role defaults + CA creation

**Files:**
- Create: `ansible/roles/lab-pki/defaults/main.yml`
- Create: `ansible/roles/lab-pki/tasks/ca.yml`

- [ ] **Step 1: Defaults**

Create `ansible/roles/lab-pki/defaults/main.yml`:

```yaml
---
# All PKI material lives under build/ (gitignored; survives restart, regenerated
# on rebuild — spec §7). repo_root is passed by the playbook.
pki_root: "{{ repo_root }}/build/secrets/pki"
pki_ca_dir: "{{ pki_root }}/ca"
pki_entity_dir: "{{ pki_root }}/entities"
pki_key_size: 4096
pki_validity_days: 730            # 1–2 yr; expiry mgmt deferred (spec §8.2)
pki_pkcs12_encryption: compatibility2022   # GSKit-readable encoding (pushback [2])
pki_only: ""                      # when set to a CN, issue just that entity
```

- [ ] **Step 2: CA task file**

Create `ansible/roles/lab-pki/tasks/ca.yml` (included per-CA with `ca_name` / `ca` set):

```yaml
---
- name: "ensure CA dir for {{ ca_name }}"
  ansible.builtin.file:
    path: "{{ pki_ca_dir }}/{{ ca_name }}"
    state: directory
    mode: "0700"

- name: "{{ ca_name }}: CA private key"
  community.crypto.openssl_privatekey:
    path: "{{ pki_ca_dir }}/{{ ca_name }}/ca.key"
    size: "{{ pki_key_size }}"
    type: RSA
    mode: "0600"

- name: "{{ ca_name }}: CA CSR (self-signing input)"
  community.crypto.openssl_csr:
    path: "{{ pki_ca_dir }}/{{ ca_name }}/ca.csr"
    privatekey_path: "{{ pki_ca_dir }}/{{ ca_name }}/ca.key"
    common_name: "{{ ca.common_name }}"
    organization_name: "{{ ca.organization_name }}"
    basic_constraints: ["CA:TRUE"]
    basic_constraints_critical: true
    key_usage: ["keyCertSign", "cRLSign"]
    key_usage_critical: true

- name: "{{ ca_name }}: self-signed CA certificate"
  community.crypto.x509_certificate:
    path: "{{ pki_ca_dir }}/{{ ca_name }}/ca.crt"
    csr_path: "{{ pki_ca_dir }}/{{ ca_name }}/ca.csr"
    privatekey_path: "{{ pki_ca_dir }}/{{ ca_name }}/ca.key"
    provider: selfsigned
    selfsigned_not_after: "+{{ pki_validity_days }}d"
    mode: "0644"
```

- [ ] **Step 3: Commit**

```bash
vrg-git add ansible/roles/lab-pki/defaults/main.yml ansible/roles/lab-pki/tasks/ca.yml
vrg-commit --type feat --scope pki --message "lab-pki role: defaults + org CA creation (#201)" --body "Idempotent community.crypto tasks to create a self-signed org CA (key + CA:TRUE cert) under build/secrets/pki/ca/<org>. Defaults pin key size, 730d validity (expiry mgmt deferred), and compatibility2022 PKCS#12 encoding."
```

### Task 4: Entity issuance + PKCS#12 assembly

**Files:**
- Create: `ansible/roles/lab-pki/tasks/entity.yml`

- [ ] **Step 1: Entity task file**

Create `ansible/roles/lab-pki/tasks/entity.yml` (included per-entity with `e` set to one inventory entry). `trust_only` entities get a CA bundle, not a keypair (spec §5, pushback [5b]). The keystore password comes from `lab-secret.sh` (persisted, reused). `other_certificates` carries the trust chain (own CA + cross-org CAs).

```yaml
---
- name: "ensure entity dir for {{ e.cn }}"
  ansible.builtin.file:
    path: "{{ pki_entity_dir }}/{{ e.cn }}"
    state: directory
    mode: "0700"

# trust set = own org CA cert + any cross-org CA certs this entity must trust.
- name: "{{ e.cn }}: compute trust bundle paths"
  ansible.builtin.set_fact:
    pki_trust_paths: >-
      {{ ([e.org] + (e.trust | default([])))
         | map('regex_replace', '^(.*)$', pki_ca_dir + '/\1/ca.crt') | list }}

- name: "{{ e.cn }}: write CA trust bundle (PEM)"
  ansible.builtin.assemble:
    src: "{{ pki_ca_dir }}"
    regexp: '^({{ ([e.org] + (e.trust | default([]))) | join("|") }})/ca\.crt$'
    dest: "{{ pki_entity_dir }}/{{ e.cn }}/trust-bundle.pem"
    mode: "0644"

# --- personal entities: key + CSR + signed cert + PKCS#12 keystore ---
- name: "{{ e.cn }}: private key"
  community.crypto.openssl_privatekey:
    path: "{{ pki_entity_dir }}/{{ e.cn }}/{{ e.cn }}.key"
    size: "{{ pki_key_size }}"
    type: RSA
    mode: "0600"
  when: e.kind == 'personal'

- name: "{{ e.cn }}: CSR"
  community.crypto.openssl_csr:
    path: "{{ pki_entity_dir }}/{{ e.cn }}/{{ e.cn }}.csr"
    privatekey_path: "{{ pki_entity_dir }}/{{ e.cn }}/{{ e.cn }}.key"
    common_name: "{{ e.cn }}"
    organization_name: "{{ pki_cas[e.org].organization_name }}"
    organizational_unit_name: "{{ e.ou }}"
  when: e.kind == 'personal'

- name: "{{ e.cn }}: certificate signed by {{ e.org }} CA"
  community.crypto.x509_certificate:
    path: "{{ pki_entity_dir }}/{{ e.cn }}/{{ e.cn }}.crt"
    csr_path: "{{ pki_entity_dir }}/{{ e.cn }}/{{ e.cn }}.csr"
    provider: ownca
    ownca_path: "{{ pki_ca_dir }}/{{ e.org }}/ca.crt"
    ownca_privatekey_path: "{{ pki_ca_dir }}/{{ e.org }}/ca.key"
    ownca_not_after: "+{{ pki_validity_days }}d"
    mode: "0644"
  when: e.kind == 'personal'

- name: "{{ e.cn }}: keystore password (persisted via lab-secret.sh)"
  ansible.builtin.command: "{{ repo_root }}/lab/scripts/lab-secret.sh pki-keyrpwd-{{ e.cn | lower }}"
  register: pki_keyrpwd
  changed_when: false
  when: e.kind == 'personal'

- name: "{{ e.cn }}: PKCS#12 key repository (cert + key + trust chain)"
  community.crypto.openssl_pkcs12:
    action: export
    path: "{{ pki_entity_dir }}/{{ e.cn }}/{{ e.cn }}.p12"
    friendly_name: "{{ e.cn }}"
    privatekey_path: "{{ pki_entity_dir }}/{{ e.cn }}/{{ e.cn }}.key"
    certificate_path: "{{ pki_entity_dir }}/{{ e.cn }}/{{ e.cn }}.crt"
    other_certificates: "{{ pki_trust_paths }}"
    passphrase: "{{ pki_keyrpwd.stdout }}"
    encryption_level: "{{ pki_pkcs12_encryption }}"
    mode: "0600"
  when: e.kind == 'personal'
```

- [ ] **Step 2: Validate**

Run: `vrg-container-run -- vrg-validate`
Expected: PASS (ansible-lint accepts the role; FQCN modules, no bare shell).

- [ ] **Step 3: Commit**

```bash
vrg-git add ansible/roles/lab-pki/tasks/entity.yml
vrg-commit --type feat --scope pki --message "lab-pki role: entity issuance + PKCS#12 assembly (#201)" --body "Per-entity: key + CSR + ownca-signed cert + a PKCS#12 keystore (compatibility2022 encoding) carrying the personal cert and the org-CA trust chain (own + cross-org signers). trust_only entities get a PEM CA bundle, no keypair. Keystore password via lab-secret.sh (persisted/reused)."
```

### Task 5: Orchestration + the `site-pki.yml` playbook

**Files:**
- Create: `ansible/roles/lab-pki/tasks/main.yml`
- Create: `ansible/site-pki.yml`

- [ ] **Step 1: Role orchestration**

Create `ansible/roles/lab-pki/tasks/main.yml` — build every CA, then issue entities (honoring the `pki_only` filter for single-entity runs):

```yaml
---
- name: "create org CAs"
  ansible.builtin.include_tasks: ca.yml
  loop: "{{ pki_cas | dict2items }}"
  loop_control:
    loop_var: ca_item
  vars:
    ca_name: "{{ ca_item.key }}"
    ca: "{{ ca_item.value }}"

- name: "issue entities"
  ansible.builtin.include_tasks: entity.yml
  loop: "{{ pki_entities | selectattr('cn', 'equalto', pki_only) | list if pki_only else pki_entities }}"
  loop_control:
    loop_var: e
    label: "{{ e.cn }}"
```

- [ ] **Step 2: The playbook**

Create `ansible/site-pki.yml` — runs on the controller (`connection: local`); the provider writes files into the host-mounted `build/` tree:

```yaml
---
# The lab PKI provider. Runs locally on the controller (no managed hosts): it
# generates CA + entity material under build/secrets/pki/. Mirrors host-obs.yml's
# local-connection shape. repo_root is resolved from the playbook dir.
- name: lab PKI provider
  hosts: localhost
  connection: local
  gather_facts: false
  vars:
    repo_root: "{{ playbook_dir }}/.."
  vars_files:
    - vars/pki-entities.yml
  roles:
    - lab-pki
```

- [ ] **Step 3: Functional check — provider runs idempotently**

Run (from the worktree, on a VM with the collection installed):
```bash
ansible-playbook ansible/site-pki.yml -c local -i localhost,
ansible-playbook ansible/site-pki.yml -c local -i localhost,   # second run
```
Expected: first run `changed`, **second run `ok=… changed=0`** (idempotent); `build/secrets/pki/ca/{client-org,dtcc-org}/ca.crt` and `build/secrets/pki/entities/QMPCMK/QMPCMK.p12` exist.

- [ ] **Step 4: Validate + commit**

Run: `vrg-container-run -- vrg-validate`
```bash
vrg-git add ansible/roles/lab-pki/tasks/main.yml ansible/site-pki.yml
vrg-commit --type feat --scope pki --message "lab-pki orchestration + site-pki.yml provider playbook (#201)" --body "main.yml builds every org CA then issues entities (with a pki_only single-entity filter). site-pki.yml runs the role connection=local so the provider generates material under build/secrets/pki/. Verified idempotent on a second run."
```

---

## Phase 2 — The `mqlab pki` CLI (TDD)

### Task 6: `mqlab pki` group — `ensure` (failing test first)

**Files:**
- Test: `tests/test_cli_pki.py`
- Modify: `src/mqlab/cli.py`

- [ ] **Step 1: Write the failing test**

Create `tests/test_cli_pki.py` (mirrors `tests/test_cli_qm.py`'s harness — `RecordingRunner`, monkeypatched `build_deps`):

```python
from __future__ import annotations

import io

from rich.console import Console
from typer.testing import CliRunner

from mqlab import cli
from mqlab.render import Renderer
from mqlab.transcript import Transcript, transcript_path
from tests.fakes import RecordingRunner, ScriptedResult


class _NoPause:
    def wait(self) -> None:
        return None


def _deps(runner):
    return cli.Deps(
        runner=runner,
        renderer=Renderer(Console(file=io.StringIO(), force_terminal=False, width=80)),
        transcript=Transcript(transcript_path("pki", "20260616T000000Z")),
        pauser=_NoPause(),
    )


def _seed(monkeypatch, tmp_path):
    monkeypatch.setenv("MQLAB_REPO_ROOT", str(tmp_path))
    (tmp_path / "lab").mkdir(parents=True)
    (tmp_path / "lab" / "topology.yaml").write_text("nodes: {}\ngroups: {}\nsetups: {}\n")


def test_pki_ensure_runs_provider_playbook_locally(monkeypatch, tmp_path):
    _seed(monkeypatch, tmp_path)
    runner = RecordingRunner(results=[ScriptedResult([])])
    monkeypatch.setattr(cli, "build_deps", lambda verb, ts: _deps(runner))
    result = CliRunner().invoke(cli.app, ["pki", "ensure"])
    assert result.exit_code == 0
    play = runner.recorded[-1]
    assert play.argv == ["ansible-playbook", "site-pki.yml", "-c", "local", "-i", "localhost,"]
    assert str(play.cwd).endswith("/ansible")
```

- [ ] **Step 2: Run the test — verify it fails**

Run: `uv run pytest tests/test_cli_pki.py::test_pki_ensure_runs_provider_playbook_locally -v`
Expected: FAIL — `No such command 'pki'` (exit code 2 from Typer, assertion on argv unreached).

- [ ] **Step 3: Add the `pki` group + `ensure` command**

In `src/mqlab/cli.py`, after the `qm_app` registration block (near line 183), add:

```python
pki_app = typer.Typer(help="lab PKI / TLS certificate provider", no_args_is_help=True)
app.add_typer(pki_app, name="pki")

_PKI_PLAYBOOK = ["ansible-playbook", "site-pki.yml", "-c", "local", "-i", "localhost,"]


@pki_app.command("ensure")
def pki_ensure(step: _StepFlag = False) -> None:
    """Create/ensure both org CAs and every entity's certs + PKCS#12 keystores."""
    cmd = Command([*_PKI_PLAYBOOK], cwd=repo_root() / "ansible")  # noqa: S607
    _execute("pki-ensure", [CommandStep("pki ensure", cmd)], step_mode=step)
```

- [ ] **Step 4: Run the test — verify it passes**

Run: `uv run pytest tests/test_cli_pki.py::test_pki_ensure_runs_provider_playbook_locally -v`
Expected: PASS.

- [ ] **Step 5: Commit**

```bash
vrg-git add tests/test_cli_pki.py src/mqlab/cli.py
vrg-commit --type feat --scope pki --message "mqlab pki ensure — run the provider playbook (#201)" --body "Adds the pki Typer group and 'ensure', which runs site-pki.yml connection=local from ansible/. TDD: failing test first, then minimal wiring, mirroring the qm command tests."
```

### Task 7: `mqlab pki issue <entity>` and `list`

**Files:**
- Test: `tests/test_cli_pki.py`
- Modify: `src/mqlab/cli.py`

- [ ] **Step 1: Write the failing tests**

Append to `tests/test_cli_pki.py`:

```python
def test_pki_issue_passes_pki_only_extra_var(monkeypatch, tmp_path):
    _seed(monkeypatch, tmp_path)
    runner = RecordingRunner(results=[ScriptedResult([])])
    monkeypatch.setattr(cli, "build_deps", lambda verb, ts: _deps(runner))
    result = CliRunner().invoke(cli.app, ["pki", "issue", "QMPCMK"])
    assert result.exit_code == 0
    assert runner.recorded[-1].argv == [
        "ansible-playbook", "site-pki.yml", "-c", "local", "-i", "localhost,",
        "-e", "pki_only=QMPCMK",
    ]


def test_pki_list_prints_entities_from_vars(monkeypatch, tmp_path):
    _seed(monkeypatch, tmp_path)
    (tmp_path / "ansible" / "vars").mkdir(parents=True)
    (tmp_path / "ansible" / "vars" / "pki-entities.yml").write_text(
        "pki_cas: {}\npki_entities:\n  - {cn: QMPCMK, org: client-org, ou: clearing-service, kind: personal, trust: [dtcc-org]}\n"
    )
    result = CliRunner().invoke(cli.app, ["pki", "list"])
    assert result.exit_code == 0
    assert "QMPCMK" in result.output
    assert "client-org" in result.output
```

- [ ] **Step 2: Run the tests — verify they fail**

Run: `uv run pytest tests/test_cli_pki.py -v -k "issue or list"`
Expected: FAIL — `No such command 'issue'` / `'list'`.

- [ ] **Step 3: Implement `issue` and `list`**

In `src/mqlab/cli.py`, after `pki_ensure`, add:

```python
@pki_app.command("issue")
def pki_issue(entity: str, step: _StepFlag = False) -> None:
    """Issue (or re-issue) one entity's cert + keystore — runs the provider for just that CN."""
    cmd = Command([*_PKI_PLAYBOOK, "-e", f"pki_only={entity}"], cwd=repo_root() / "ansible")  # noqa: S607
    _execute("pki-issue", [CommandStep(f"pki issue {entity}", cmd)], step_mode=step)


@pki_app.command("list")
def pki_list() -> None:
    """List the PKI entity inventory (org, OU, kind) from ansible/vars/pki-entities.yml."""
    import yaml as _yaml

    deps = build_deps("pki-list", datetime.now(tz=UTC).strftime("%Y%m%dT%H%M%SZ"))
    try:
        data = _yaml.safe_load((repo_root() / "ansible" / "vars" / "pki-entities.yml").read_text())
        for e in data.get("pki_entities", []):
            line = f"{e['cn']:<14} org={e['org']:<11} ou={e.get('ou', '-'):<16} {e['kind']}"
            deps.renderer.output(line)
            deps.transcript.write(line)
    finally:
        deps.transcript.close()
```

- [ ] **Step 4: Run the tests — verify they pass**

Run: `uv run pytest tests/test_cli_pki.py -v`
Expected: PASS (all three tests).

- [ ] **Step 5: Commit**

```bash
vrg-git add tests/test_cli_pki.py src/mqlab/cli.py
vrg-commit --type feat --scope pki --message "mqlab pki issue + list (#201)" --body "issue <entity> runs the provider with -e pki_only=<cn> for a single entity; list prints the entity inventory from ansible/vars/pki-entities.yml. TDD with failing tests first."
```

---

## Phase 3 — Verification gates

### Task 8: PKCS#12 ↔ MQ load check (blocking — pushback [2])

This is the spec's blocking first-build interop check: does a `community.crypto` PKCS#12 (`compatibility2022`) actually load in the licensed MQ? Run it **before** trusting the provider for the downstream channel-security work.

**Files:** none (verification); on failure, modify `ansible/roles/lab-pki/defaults/main.yml` and/or `ansible/roles/lab-pki/tasks/entity.yml`.

- [ ] **Step 1: Generate one keystore**

Run: `ansible-playbook ansible/site-pki.yml -c local -i localhost, -e pki_only=QMPCMK`
Expected: `build/secrets/pki/entities/QMPCMK/QMPCMK.p12` exists.

- [ ] **Step 2: Load it into a queue manager's key repository**

On an MQ host/QM, set the repository and start a TLS listener that reads it:
```bash
KEYRPWD=$(lab/scripts/lab-secret.sh pki-keyrpwd-qmpcmk)
# copy QMPCMK.p12 to the QM host, then:
printf "ALTER QMGR SSLKEYR('/var/mqm/qmgrs/QMPCMK/ssl/key') KEYRPWD('%s')\nREFRESH SECURITY TYPE(SSL)\n" "$KEYRPWD" \
  | su mqm -c '/opt/mqm/bin/runmqsc QMPCMK'
```
Expected: `REFRESH SECURITY TYPE(SSL)` returns success and the QM error log shows **no** key-repository/GSKit parse error (e.g. no `AMQ9633`/`AMQ9657` keystore errors).

- [ ] **Step 3: Decide**

- PASS → the `compatibility2022` encoding is MQ-readable; the provider is good. Record the result in the PR.
- FAIL (GSKit rejects the encoding) → apply the **fallback** (spec §6): keep `community.crypto` for the PEM key/cert, but assemble the keystore with MQ's `runmqktool` instead of `openssl_pkcs12`. Replace the PKCS#12 task in `entity.yml` with a `runmqktool` import step, and note the one-tool detour in the PR. Re-run Steps 1–2.

- [ ] **Step 4: Commit (only if the fallback changed files)**

```bash
vrg-git add ansible/roles/lab-pki/tasks/entity.yml ansible/roles/lab-pki/defaults/main.yml
vrg-commit --type fix --scope pki --message "PKCS#12 encoding: MQ-load-verified (or runmqktool fallback) (#201)" --body "Pushback [2] first-build interop check: <PASS: compatibility2022 loads in MQ | FALLBACK: assemble keystore via runmqktool>. <details>."
```

### Task 9: Cold-rebuild acceptance gate (spec §3, §11)

The lab's standard: lint-green is not done — it must come up one-pass on a freshly-rebuilt VM (the #156 class of failure). This proves the galaxy provisioning (Task 1) and the provider survive a rebuild.

**Files:** none (acceptance); on failure, fix the offending task and re-run.

- [ ] **Step 1: Cold rebuild + bootstrap**

(Human runs the VM rebuild — agent cannot.) Suggest to the human:
```
! vrg-vm rebuild logical-minds-foundry/mq-cluster-tooling --identity vergil-user
```
Then in a fresh session, from the repo root:
```bash
uv sync
ansible-galaxy collection install -r ansible/requirements.yml -p build/ansible_collections
```
Expected: collection installs into `build/ansible_collections/community/crypto`.

- [ ] **Step 2: Run the provider from clean**

Run: `rm -rf build/secrets/pki && ansible-playbook ansible/site-pki.yml -c local -i localhost,`
Expected: completes one-pass; both CA dirs and all personal-entity `.p12` files exist; a second run is `changed=0`.

- [ ] **Step 3: Full validation**

Run: `vrg-container-run -- vrg-validate`
Expected: PASS.

- [ ] **Step 4: Record the gate result in the PR**

Note in the PR description that the cold-rebuild gate passed (date, that `community.crypto` installed and the provider ran one-pass), per spec §3/§11.

---

## Self-Review

**Spec coverage** (against `docs/specs/2026-06-16-lab-pki-design.md`):
- §3 provider (community.crypto, reproducible) → Tasks 1, 3, 4, 5.
- §4 two-org CAs + signer exchange → Task 3 (CAs), Task 4 (`other_certificates` trust chain incl. cross-org).
- §5 entity inventory + partial-DN identity + personal-vs-trust-only → Task 2, Task 4 (`kind`).
- §6 PKCS#12 end-to-end + encoding pin + load test + runmqktool fallback → Task 4 (`encryption_level`), Task 8.
- §7 secrets/ephemerality (build/, lab-secret.sh) → Task 3/4 (paths, `KEYRPWD` via `lab-secret.sh`).
- §8.1 lifecycle (ensure/add/issue) → Tasks 5, 6, 7 (`ensure`, `issue`, idempotent re-run = add-entity).
- §8.2 expiry/rotation deferred → out of scope (no task), as designed.
- §9 CLI surface → Tasks 6, 7. Per §9, QM-side keystore distribution + `SSLKEYR`/`KEYRPWD` wiring is the first **downstream** step (not this plan); Task 8 exercises it only to load-test one keystore.
- §10 GOV1683-24 fidelity → Task 3 defaults pin `pki_key_size: 4096`; signature digest is `community.crypto`'s SHA-256 default (representative). Cipher/TLS-version specifics are deferred downstream per §10.
- §3/§11 cold-rebuild gate + collection provisioning → Tasks 1, 9.
- Pushback [1] galaxy provisioning → Task 1; [2] encoding → Tasks 4, 8; [3] partial-DN → Task 2; [4] mqweb coupling → out of this plan (downstream, flagged in spec §9); [5] runmqsc/trust-only → Task 2 (`trust_only`), Task 8.

**Out-of-plan (by design):** QM-side keystore distribution + `SSLKEYR`/`KEYRPWD` wiring (the first downstream step, extending the `mq-qmgr`/`mq-pcmk-qmgr` roles), channel `SSLCIPH`/`CHLAUTH`/`SSLPEER` wiring, mqweb TLS adoption + the `verify_tls=False` flip, and OpenShift/CRR cert extension — all downstream of this plan.

**Placeholder scan:** none — every code/config step shows exact content; Task 8's branch is a real decision with concrete commands, not a TODO.

**Type/name consistency:** `pki_only`, `pki_entities`, `pki_cas`, `pki_ca_dir`, `pki_entity_dir`, `_PKI_PLAYBOOK`, and the `ensure`/`issue`/`list` verbs are used identically across role, playbook, CLI, and tests.
