# Ansible → Salt migration evaluation — implementation plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Conduct the evaluation defined in `docs/specs/2026-06-16-ansible-to-salt-evaluation-design.md` and produce a go/no-go recommendation plus a person-hours effort estimate at `docs/reports/2026-06-16-ansible-to-salt-evaluation.md`.

**Architecture:** This is an **evaluation**, not a code feature, so the rhythm is *define success criterion → produce artifact → verify against it → commit* rather than red/green TDD. Three artifacts get built: (1) a paper **translation matrix** of every Ansible idiom → Salt equivalent with confidence + person-hours weight; (2) one **throwaway `salt-ssh` spike** porting the `prometheus` role to an SLS formula, run to a clean idempotent apply to calibrate the estimate and surface real frictions; (3) the **report** that folds matrix + spike results into a scored recommendation. The single most important honesty constraint (from the spec's pushback review): the thin spike validates the low-risk idioms only, so the report must tag each matrix cell *spike-validated* vs *paper-only* and band the estimate accordingly.

**Tech Stack:** Salt (`salt-ssh`, SLS states, Jinja, grains), the existing Ansible footprint under `ansible/`, Markdown deliverables. All work on branch `feature/197-ansible-salt-eval` in worktree `.worktrees/issue-197-ansible-salt-eval/`. Git via `vrg-git`/`vrg-commit`; the spike's working files live in the gitignored `build/` tree so they are never merged.

**Conventions for every task below:**
- All commands run from inside the worktree: `cd /Users/pmoore/dev/projects/logical-minds-foundry/mq-cluster-tooling/.worktrees/issue-197-ansible-salt-eval` first.
- Commit only with `vrg-commit --type <type> --scope salt --message <msg> [--body <body>]`.
- The report path is referred to throughout as `REPORT = docs/reports/2026-06-16-ansible-to-salt-evaluation.md`.
- The spike workspace is `SPIKE = build/salt-spike/` (gitignored — confirm with `git check-ignore build/` before writing there).

---

## Phase 0 — Control-node tooling prerequisite (spec §4.0)

### Task 1: Provision `salt-ssh` as a scoped throwaway and capture its cost

**Files:**
- Create: `build/salt-spike/TOOLING.md` (throwaway notes; gitignored)

- [ ] **Step 1: Confirm `build/` is gitignored (so the spike never merges)**

Run: `git check-ignore build/ && echo IGNORED`
Expected: prints `build/` then `IGNORED`. If it does not print `IGNORED`, STOP and resolve — the spike must not land in git.

- [ ] **Step 2: Install `salt-ssh` as an isolated throwaway tool**

Do **not** `apt install` into the live VM and do **not** add Salt to `vergil.toml` (that is the *costed-but-not-built* production path, spec §6). Use an isolated `uv` tool install:

```bash
uv tool install salt        # provides salt-ssh on $PATH via uv's shim
salt-ssh --version          # verify; record the exact version printed
```

If `uv tool install salt` fails (Salt has heavy C deps), fall back to a dedicated venv:

```bash
uv venv build/salt-spike/.venv
build/salt-spike/.venv/bin/python -m pip install salt
build/salt-spike/.venv/bin/salt-ssh --version
```

- [ ] **Step 3: Record the tooling cost line for the report**

Write `build/salt-spike/TOOLING.md` capturing: the install method that worked, the `salt-ssh --version`, install time (wall-clock), on-disk footprint (`du -sh` of the tool/venv), and a one-paragraph note on what *production* adoption would take — adding Salt to the `[vm.vergil-user]` profile in `vergil.toml` and invoking `salt-ssh` by bare name via `$PATH` from `mqlab`, mirroring how Ansible is carried today (#165). This becomes the report's §4.0 cost line in Task 9.

- [ ] **Step 4: No commit** — everything here is in gitignored `build/`. Verify nothing is staged:

Run: `git status --short`
Expected: empty (no tracked changes).

---

## Phase 1 — Translation matrix (spec §4.1)

### Task 2: Create the report skeleton with the full paper matrix

**Files:**
- Create: `docs/reports/2026-06-16-ansible-to-salt-evaluation.md`

- [ ] **Step 1: Write the report skeleton with all section headers and the matrix**

Create `REPORT` with the structure below. The matrix lists **every** idiom from the footprint (spec §2/§4.1); fill the *Equivalent* column from the spec's draft mappings; set every *Coverage* cell to `paper-only` for now (Task 8 promotes the spike-touched ones); leave *Weight (hrs)* as your first paper estimate using the 1/3/5 ordinal (1 ≈ trivial 1:1 swap, 3 ≈ rework+test, 5 ≈ hard re-modelling).

```markdown
# Ansible → Salt migration evaluation — report

- **Issue:** #197
- **Date:** 2026-06-16
- **Spec:** docs/specs/2026-06-16-ansible-to-salt-evaluation-design.md
- **Status:** draft

## 1. Summary & recommendation
<!-- filled in Task 11 -->

## 2. Footprint under evaluation
<!-- 9 playbooks, 21 roles, 21 templates, ~2,260 lines, zero collections (spec §2) -->

## 3. Translation matrix

| Ansible idiom | Count | Salt equivalent | Confidence | Weight (hrs) | Coverage |
|---|---|---|---|---|---|
| `apt`/`dnf` (cross-distro) | … | `pkg.installed` + grain targeting | … | … | paper-only |
| `systemd` | … | `service.running` | … | … | paper-only |
| `template` (.j2) | … | `file.managed` + `template: jinja` | … | … | paper-only |
| `copy` | … | `file.managed` | … | … | paper-only |
| `file` | … | `file.directory`/`file.symlink` | … | … | paper-only |
| `lineinfile`/`blockinfile` | … | `file.line`/`file.blockreplace` | … | … | paper-only |
| `unarchive`+`creates` | … | `archive.extracted` | … | … | paper-only |
| `shell`/`command`+`changed_when` | … | `cmd.run`+`onlyif`/`unless` | … | … | paper-only |
| handlers/`notify` | … | `mod_watch`+`watch`/`listen` | … | … | paper-only |
| `include_role`/`import_playbook` | … | `include`/orchestrate | … | … | paper-only |
| `run_once`+`register` coordination | … | orchestrate/mine/imperative | … | … | paper-only |
| `inventory.ini` (from `inventory.py`) | 1 | salt-ssh roster | … | … | paper-only |
| `group_vars/all.yml` | 1 | pillar | … | … | paper-only |
| `ansible.cfg` | 1 | `Saltfile`/master config | … | … | paper-only |
| `mqlab`→`ansible-playbook` | 1 | `mqlab`→`salt-ssh` | … | … | paper-only |

## 4. Spike: prometheus role → SLS
<!-- filled in Task 8 -->

## 5. Control-node tooling cost
<!-- filled in Task 9 -->

## 6. Risk register & confidence bands
<!-- filled in Task 10 -->

## 7. Effort estimate & go/no-go
<!-- filled in Task 11 -->
```

- [ ] **Step 2: Populate the `Count` column from the live tree**

Run these and fill the counts:

```bash
grep -rho 'ansible\.builtin\.[a-z_]*' ansible/ | sort | uniq -c | sort -rn
```

Expected: a frequency table (template/copy/systemd/command/shell/file/unarchive/lineinfile/user/etc.). Transcribe the relevant counts into the matrix.

- [ ] **Step 3: Verify no placeholders remain in the matrix rows**

Run: `grep -n '…' docs/reports/2026-06-16-ansible-to-salt-evaluation.md`
Expected: matches only in the `## 4/5/6/7` "filled in later" comment sections, **not** inside the Task-2 matrix rows. Every matrix cell except `Coverage` must hold a real value.

- [ ] **Step 4: Commit**

```bash
vrg-git add docs/reports/2026-06-16-ansible-to-salt-evaluation.md
vrg-commit --type docs --scope salt \
  --message "Report skeleton + paper translation matrix (#197)" \
  --body "Every footprint idiom mapped to its Salt equivalent with first-pass person-hours weights; all cells paper-only pending the spike."
```

---

## Phase 2 — The throwaway salt-ssh spike (spec §4.2)

### Task 3: Stand up the spike workspace and prove salt-ssh connectivity

**Files:**
- Create: `build/salt-spike/etc/master` (file_roots config; gitignored)
- Create: `build/salt-spike/roster` (gitignored)

- [ ] **Step 1: Choose the lightest sufficient target**

Default to **localhost on the dev VM** (the `prometheus` role has no MQ/cluster dependency; the dev VM is ephemeral so mutating it is fine — rebuild to reset). Confirm the target can do what the role needs:

```bash
command -v systemctl && sudo -n true && echo "OK: systemd + passwordless sudo"
```

Expected: `OK: systemd + passwordless sudo`. If this fails (no systemd or sudo on the dev VM), STOP and switch to a scratch lab VM per spec §4.2 — **the human brings that VM up** (lab-ops boundary); resume once you have its IP + SSH user.

- [ ] **Step 2: Ensure key-based SSH to the target**

For localhost:

```bash
test -f ~/.ssh/id_ed25519 || ssh-keygen -t ed25519 -N '' -f ~/.ssh/id_ed25519
ssh-keygen -y -f ~/.ssh/id_ed25519 >> ~/.ssh/authorized_keys
ssh -o StrictHostKeyChecking=accept-new -i ~/.ssh/id_ed25519 "$USER@127.0.0.1" true && echo SSH_OK
```

Expected: `SSH_OK`.

- [ ] **Step 3: Write the salt-ssh config + roster**

`build/salt-spike/etc/master`:

```yaml
file_roots:
  base:
    - /Users/pmoore/dev/projects/logical-minds-foundry/mq-cluster-tooling/.worktrees/issue-197-ansible-salt-eval/build/salt-spike/srv/salt
```

`build/salt-spike/roster` (replace `<USER>` with `$USER`):

```yaml
spike:
  host: 127.0.0.1
  user: <USER>
  priv: /home/<USER>/.ssh/id_ed25519
  sudo: true
```

- [ ] **Step 4: Prove connectivity**

```bash
salt-ssh -c build/salt-spike/etc --roster-file build/salt-spike/roster -i 'spike' test.ping
```

Expected: `spike:` then `True`. If you hit the salt "thin" deploy prompt, accept it. Record any first-run friction in `build/salt-spike/FINDINGS.md` (create it now).

- [ ] **Step 5: No commit** (all under gitignored `build/`). Verify: `git status --short` is empty.

### Task 4: Port the `prometheus` role to an SLS formula (starting point — expect to fix)

**Files:**
- Read: `ansible/roles/prometheus/{tasks,handlers,defaults}/main.yml`, `ansible/roles/prometheus/templates/prometheus.yml.j2`, `ansible/roles/prometheus/files/lab.rules.yml`
- Create: `build/salt-spike/srv/salt/prometheus/init.sls` (gitignored)
- Create: `build/salt-spike/srv/salt/prometheus/files/{prometheus.yml.jinja,lab.rules.yml,node.json}` (gitignored)

- [ ] **Step 1: Read the source role**

Run: `sed -n '1,200p' ansible/roles/prometheus/tasks/main.yml ansible/roles/prometheus/defaults/main.yml ansible/roles/prometheus/handlers/main.yml`
Note the eight tasks (user → unarchive → copy binary → dirs → template → copy rules → copy targets → systemd unit → enable/start) and the `restart prometheus` handler.

- [ ] **Step 2: Write the starting-point `init.sls`**

This is a faithful translation, **not** a finished answer — several cells are deliberately where Salt diverges from Ansible (flagged inline). Drive it to convergence in Task 5, logging each fix as a finding.

```jinja
# build/salt-spike/srv/salt/prometheus/init.sls   (THROWAWAY SPIKE)
{% set version = '2.53.2' %}
{% set arch = 'arm64' if grains['cpuarch'] == 'aarch64' else 'amd64' %}
{% set pkg = 'prometheus-' ~ version ~ '.linux-' ~ arch %}
{% set url = 'https://github.com/prometheus/prometheus/releases/download/v' ~ version ~ '/' ~ pkg ~ '.tar.gz' %}

prometheus_user:
  user.present:
    - name: prometheus
    - system: True
    - shell: /usr/sbin/nologin
    - createhome: False

# FRICTION TO CONFIRM: Ansible unarchive needs no checksum; Salt archive.extracted
# wants source_hash for a remote source. skip_verify bypasses it — log this as a finding.
prometheus_unpack:
  archive.extracted:
    - name: /tmp/{{ pkg }}
    - source: {{ url }}
    - skip_verify: True
    - enforce_toplevel: False
    - if_missing: /tmp/{{ pkg }}/prometheus

# FRICTION TO CONFIRM: Ansible copy remote_src=true copies a file already ON the target.
# Salt file.managed sources come from salt:// or remote URLs, not arbitrary minion paths.
# Use file.copy (the state) for an on-target copy — log this divergence.
prometheus_binary:
  file.copy:
    - name: /usr/local/bin/prometheus
    - source: /tmp/{{ pkg }}/prometheus
    - mode: '0755'
    - force: True
    - require:
      - archive: prometheus_unpack

prometheus_dirs:
  file.directory:
    - names:
      - /etc/prometheus
      - /etc/prometheus/targets
      - /etc/prometheus/rules
      - /var/lib/prometheus
    - user: prometheus
    - group: prometheus
    - mode: '0755'
    - require:
      - user: prometheus_user

prometheus_config:
  file.managed:
    - name: /etc/prometheus/prometheus.yml
    - source: salt://prometheus/files/prometheus.yml.jinja
    - template: jinja
    - mode: '0644'
    - require:
      - file: prometheus_dirs

prometheus_rules:
  file.managed:
    - name: /etc/prometheus/rules/lab.rules.yml
    - source: salt://prometheus/files/lab.rules.yml
    - user: prometheus
    - group: prometheus
    - mode: '0644'
    - require:
      - file: prometheus_dirs

prometheus_targets:
  file.managed:
    - name: /etc/prometheus/targets/node.json
    - source: salt://prometheus/files/node.json
    - user: prometheus
    - group: prometheus
    - mode: '0644'
    - require:
      - file: prometheus_dirs

prometheus_unit:
  file.managed:
    - name: /etc/systemd/system/prometheus.service
    - mode: '0644'
    - contents: |
        [Unit]
        Description=Prometheus
        After=network-online.target
        Wants=network-online.target

        [Service]
        User=prometheus
        Group=prometheus
        ExecStart=/usr/local/bin/prometheus --config.file=/etc/prometheus/prometheus.yml --storage.tsdb.path=/var/lib/prometheus --storage.tsdb.retention.time=24h --web.listen-address=:9090
        Restart=on-failure

        [Install]
        WantedBy=multi-user.target

# FRICTION TO CONFIRM: Ansible 'notify: restart prometheus' → Salt watch/mod_watch.
# service.running with watch requisites restarts on config/unit change. Log the
# semantic difference (Salt watch is declarative requisite, not a queued handler).
prometheus_service:
  service.running:
    - name: prometheus
    - enable: True
    - watch:
      - file: prometheus_config
      - file: prometheus_rules
      - file: prometheus_targets
      - file: prometheus_unit
    - require:
      - file: prometheus_binary
```

- [ ] **Step 3: Port the Jinja template and stub the topology files**

Read `ansible/roles/prometheus/templates/prometheus.yml.j2`. Copy it to `build/salt-spike/srv/salt/prometheus/files/prometheus.yml.jinja` and translate Ansible-isms to Salt — the ones to watch and log as Jinja-parity findings:
- `ansible_architecture` / `ansible_*` facts → `grains['...']`
- `groups[...]` / `hostvars[...]` (inventory) → not available under salt-ssh the same way; hardcode a stub value for the spike and log it
- `| default(x)` → works in Salt too; `map(attribute=...)` → may need rework

Copy `ansible/roles/prometheus/files/lab.rules.yml` verbatim to the spike files dir. Stub the targets file the role expects from `build/prometheus/targets/node.json`:

```bash
mkdir -p build/salt-spike/srv/salt/prometheus/files
printf '[{"labels":{"job":"node"},"targets":["127.0.0.1:9100"]}]\n' \
  > build/salt-spike/srv/salt/prometheus/files/node.json
```

- [ ] **Step 4: No commit** (gitignored). `git status --short` empty.

### Task 5: Drive the spike to a successful apply (measure wall-clock; log frictions)

**Files:**
- Modify: `build/salt-spike/srv/salt/prometheus/init.sls` (as needed to converge)
- Append: `build/salt-spike/FINDINGS.md`

- [ ] **Step 1: Note the start time**

Run: `date +%s | tee build/salt-spike/.t0`

- [ ] **Step 2: Apply the state**

```bash
salt-ssh -c build/salt-spike/etc --roster-file build/salt-spike/roster -i 'spike' state.apply prometheus
```

- [ ] **Step 3: Iterate to a clean run**

For each failing state, fix `init.sls` (or the template/files) and re-run Step 2. Every fix you make — `source_hash`/`skip_verify`, `file.copy` vs `file.managed`, `watch` vs `notify`, any Jinja change — gets one line in `build/salt-spike/FINDINGS.md` with the idiom name and the fix. Continue until output shows `Failed: 0` and Prometheus is up:

```bash
sudo systemctl is-active prometheus && curl -fsS localhost:9090/-/ready && echo READY
```

Expected: `active` then `Prometheus Server is Ready.` then `READY`.

- [ ] **Step 4: Record elapsed wall-clock**

```bash
echo "spike wall-clock seconds: $(( $(date +%s) - $(cat build/salt-spike/.t0) ))" >> build/salt-spike/FINDINGS.md
```

- [ ] **Step 5: No commit** (gitignored).

### Task 6: Verify the idempotency success criterion (the spike's "test")

**Files:**
- Append: `build/salt-spike/FINDINGS.md`

- [ ] **Step 1: Re-apply unchanged and capture the change count**

```bash
salt-ssh -c build/salt-spike/etc --roster-file build/salt-spike/roster -i 'spike' \
  state.apply prometheus 2>&1 | tee build/salt-spike/second-apply.txt
grep -E 'Succeeded:|Changed:|Failed:' build/salt-spike/second-apply.txt
```

- [ ] **Step 2: Assert a clean no-op**

Expected: `Failed: 0` **and** `Changed: 0` (or `Changed: 0` with `unchanged` on every state). This is the spec's success criterion — an idempotent second apply.

If `Changed:` is non-zero, a state is non-idempotent (common cause: `file.copy` without `preserve`/proper mode, or a `cmd.run` lacking `unless`). Fix it, re-run Task 5 Step 2 once to settle, then repeat this step. Log the non-idempotency cause as a finding (it is direct evidence about the `changed_when`→`onlyif`/`unless` risk).

- [ ] **Step 3: Record the idempotency verdict** in `build/salt-spike/FINDINGS.md` (pass + the final `Changed:` line).

- [ ] **Step 4: No commit** (gitignored).

### Task 7: Consolidate spike findings

**Files:**
- Finalize: `build/salt-spike/FINDINGS.md`

- [ ] **Step 1: Structure the findings**

Ensure `FINDINGS.md` contains, in order: (a) wall-clock person-hours for the port, (b) the idempotency verdict, (c) a per-idiom friction list (each with the Ansible idiom, the Salt fix, and a 1/3/5 difficulty), (d) which idioms the spike actually exercised — `user`, `archive.extracted`, on-target copy, `file.directory`, `file.managed`+`template: jinja`, `watch`/`service.running`, and **arch grain** (note: this validates the *arch* grain but NOT the `apt`/`dnf` *os_family* split — that stays paper-only).

- [ ] **Step 2: No commit** — findings stay in gitignored `build/`; they are transcribed into the tracked report in Phase 3.

---

## Phase 3 — Report: fold matrix + spike into a scored recommendation (spec §4.3, §5, §4.0)

### Task 8: Write the spike section and promote spike-validated matrix cells

**Files:**
- Modify: `docs/reports/2026-06-16-ansible-to-salt-evaluation.md` (§3 matrix, §4 spike)

- [ ] **Step 1: Write §4 (spike)** from `build/salt-spike/FINDINGS.md`: what was ported, the idempotency result, the friction list, and the measured wall-clock.

- [ ] **Step 2: Promote the validated cells in §3**: for every idiom the spike exercised (per Task 7a/d), change `Coverage` from `paper-only` to `spike-validated` and adjust its `Weight (hrs)` to the spike-calibrated value. Leave cluster-coordination, cross-distro `apt`/`dnf`, and inter-host Jinja (`groups`/`hostvars`) as `paper-only`.

- [ ] **Step 3: Verify the coverage split is honest**

Run: `grep -c 'spike-validated' docs/reports/2026-06-16-ansible-to-salt-evaluation.md; grep -c 'paper-only' docs/reports/2026-06-16-ansible-to-salt-evaluation.md`
Expected: both non-zero — the spike validated some cells, and the high-risk cells remain paper-only.

- [ ] **Step 4: Commit**

```bash
vrg-git add docs/reports/2026-06-16-ansible-to-salt-evaluation.md
vrg-commit --type docs --scope salt \
  --message "Fold prometheus spike results into matrix (#197)" \
  --body "Spike section written; spike-exercised idioms promoted to spike-validated with calibrated weights; high-risk cells remain paper-only."
```

### Task 9: Write the control-node tooling cost (§5)

**Files:**
- Modify: `docs/reports/2026-06-16-ansible-to-salt-evaluation.md` (§5)

- [ ] **Step 1: Transcribe** `build/salt-spike/TOOLING.md` (Task 1) into report §5: install method, version, footprint, and the production-adoption paragraph (Salt into `[vm.vergil-user]`, bare-name `$PATH` from `mqlab`).

- [ ] **Step 2: Commit**

```bash
vrg-git add docs/reports/2026-06-16-ansible-to-salt-evaluation.md
vrg-commit --type docs --scope salt --message "Control-node tooling cost line (#197)"
```

### Task 10: Write the risk register with confidence bands (§6)

**Files:**
- Modify: `docs/reports/2026-06-16-ansible-to-salt-evaluation.md` (§6)

- [ ] **Step 1: Write §6** from spec §5, each risk carrying a confidence band. Mark the two widest-variance, paper-only risks explicitly:
  - `run_once`+`register` cluster coordination (pcs/DRBD/iSCSI) — not exercised by the spike; paper estimate, wide variance; name the candidate Salt pattern (orchestrate runner / mine / retained `cmd.run`).
  - Cross-distro `apt`/`dnf` → `pkg.installed` os_family split — the single-arch spike under-sampled it; paper-only.
  Plus: Jinja inter-host parity (`groups`/`hostvars`), roster generation from `topology.yaml`, `mqlab`+cold-rebuild integration.

- [ ] **Step 2: Commit**

```bash
vrg-git add docs/reports/2026-06-16-ansible-to-salt-evaluation.md
vrg-commit --type docs --scope salt --message "Risk register + confidence bands (#197)"
```

### Task 11: Compute the estimate and apply the go/no-go rubric (§7 + §1)

**Files:**
- Modify: `docs/reports/2026-06-16-ansible-to-salt-evaluation.md` (§7, then §1)

- [ ] **Step 1: Roll up the estimate in §7**

Group the 21 roles into role-classes (e.g. *simple-provisioning* like `prometheus`/`node-exporter`/`grafana`/`loki`/`alloy`; *MQ-install/config*; *HA-coordination* like `pcmk-*`/`drbd-san`/`iscsi-*`/`rdqm-*`/`mq-pcmk-qmgr`). For each class, multiply representative per-role hours (spike-calibrated for simple-provisioning; paper for the rest) by the role count to get a **person-hours range**. Attach a confidence band per class; HA-coordination gets the widest. Sum to a total range.

- [ ] **Step 2: Apply the rubric in §7**

State the chosen person-hours **bar** (call this out as the human's decision input), then evaluate per spec §4.3: **Go** if total ≤ bar and no unresolved red hotspot; **Defer** if the only blocker is an unvalidated hotspot (name the targeted follow-up spike); **No-go** otherwise. Weigh the Axis-1 (authoring) cost against the Axis-2 (salt-ssh keeps footprint near-zero) and the benefit side (employer-fidelity, skills).

- [ ] **Step 3: Write §1 summary** — the one-paragraph recommendation (go/defer/no-go), the headline person-hours range with its band, and the single biggest caveat (HA-coordination effort is paper-only).

- [ ] **Step 4: Commit**

```bash
vrg-git add docs/reports/2026-06-16-ansible-to-salt-evaluation.md
vrg-commit --type docs --scope salt \
  --message "Effort estimate, go/no-go rubric, and summary (#197)" \
  --body "Per-role-class person-hours ranges with confidence bands; rubric applied; §1 recommendation written."
```

### Task 12: Self-review against the spec, validate, and finalize

**Files:**
- Modify: `docs/reports/2026-06-16-ansible-to-salt-evaluation.md` (status → final)

- [ ] **Step 1: Check report against spec deliverables**

Confirm the report delivers every spec output: go/no-go recommendation (§1), person-hours estimate with bands (§7), spike-validated/paper-only tagging on every matrix cell (§3), control-node cost (§5), risk register (§6). Fix any gap inline.

- [ ] **Step 2: Scan for leftover placeholders**

Run: `grep -nE '…|<!-- filled|TBD|TODO' docs/reports/2026-06-16-ansible-to-salt-evaluation.md`
Expected: no matches. Fix any that remain.

- [ ] **Step 3: Confirm the spike never leaked into git**

Run: `git status --short && git ls-files build/ | head`
Expected: clean working tree and **no** `build/` files tracked.

- [ ] **Step 4: Run the repo validation**

Run: `vrg-container-run -- vrg-validate`
Expected: passes. If it flags the new Markdown (e.g. link/format lint), fix and re-run until green.

- [ ] **Step 5: Flip status and commit**

Set the §status line to `final`, then:

```bash
vrg-git add docs/reports/2026-06-16-ansible-to-salt-evaluation.md
vrg-commit --type docs --scope salt \
  --message "Finalize Ansible→Salt evaluation report (#197)" \
  --body "Self-reviewed against the spec; vrg-validate green; spike confirmed absent from git."
```

- [ ] **Step 6: Open the PR into develop** (per repo workflow)

```bash
vrg-gh pr create --base develop --head feature/197-ansible-salt-eval \
  --title "Ansible→Salt migration evaluation (#197)" \
  --body "Closes #197. Methodology spec + plan + evaluation report (go/no-go + person-hours estimate). Salt spike was throwaway (gitignored build/), never merged into lab automation."
```

If `vrg-gh pr create` is denied for the agent, hand the exact command to the human to run via `! …`.

---

## Self-review (author's check against the spec)

- **Spec coverage:** §1 purpose → Tasks 11/12; §2 footprint → Task 2; §3 two-axis framing → Task 11 Step 2 (Axis-1 cost vs Axis-2 footprint); §4.0 tooling → Tasks 1/9; §4.1 matrix → Tasks 2/8; §4.2 spike → Tasks 3–7; §4.3 confidence tagging + rubric → Tasks 8/10/11; §5 risk register → Task 10; §6 scope (throwaway, gitignored, no profile build) → Tasks 1/3/12 Step 3; §7 outputs → Task 12. No uncovered requirement.
- **Placeholder scan:** the only `…` / `<!-- filled -->` tokens are *inside report artifacts* and each is explicitly resolved by a later task with a verifying `grep`; no plan step defers its own content.
- **Type/name consistency:** `REPORT`, `SPIKE`, the SLS state IDs (`prometheus_user`/`_unpack`/`_binary`/`_dirs`/`_config`/`_rules`/`_targets`/`_unit`/`_service`), the roster id `spike`, and the config dir `build/salt-spike/etc` are used identically across all tasks.
