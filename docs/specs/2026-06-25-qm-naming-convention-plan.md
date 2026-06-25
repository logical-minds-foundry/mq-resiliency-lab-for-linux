# QM Naming Convention — Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Rename the lab's queue managers to a short, per-stack, role-tagged convention (`<short>APP`/`<short>SVC`) behind a single source of truth, de-hardcoding ~250 references so the rename is a one-line-per-stack config change.

**Architecture:** Two phases. **Phase 1** wires every reference to derive from `QmConfig` (Python) / threaded ansible vars / script args, with the source still holding the *old* names — a behavior-preserving refactor proven byte-identical by a cold rebuild. **Phase 2** flips the source to the `short`-derived names and adds a guard test. The app QM stays per-stack on the cluster; the svc QM is per-stack-named, created in provision (`mq-inter-qm`); channels are a bidirectional pair derived from `qm_app`+`qm_svc`.

**Tech Stack:** Python 3.14 (Typer CLI, frozen dataclasses), Ansible (Jinja2 `.mqsc.j2` templates), Bash (`lab/scripts`), pymqi clients, pytest. Spec: `docs/specs/2026-06-25-qm-naming-convention-design.md`.

## Global Constraints

- **Validation is `vrg-container-run -- vrg-validate` ONLY.** Never run individual linters. (CLAUDE.md)
- **100% branch coverage** — `pytest --cov=src --cov-branch --cov-fail-under=100`. Every new Python line needs a covering test.
- **Commit with `vrg-commit`** (conventional commits): `vrg-commit --type <type> --scope <scope> --message <msg> [--body <body>]`. No raw `git commit`. No GitHub auto-close keywords in bodies (`close/fix/resolve`) — use `Ref #N`.
- **Git/GitHub via `vrg-git` / `vrg-gh`** wrappers only.
- **All work in the worktree** `.worktrees/issue-351-qm-naming/` on branch `feature/351-qm-naming`. Use absolute paths or `cd` into it.
- **Ruff + mypy + ty clean**; StrEnum members need explicit values where UP042 applies; ruff magic-trailing-comma formatting; don't mask subprocess exit codes. (memory: validation-gotchas)
- **No XML authored**; MQSC and Jinja stay as-is in form. (memory: no-xml-unless-required)
- **`short` tokens:** `PCMK` (pcmk-ubuntu), `RDQM` (rdqm-rhel), `NHAR` (nativeha-rhel), `NHAU` (nativeha-ubuntu, reserved). Derived: `qm_app="{short}APP"`, `qm_svc="{short}SVC"`, `chl_to_svc="{short}APP.{short}SVC"`, `chl_to_app="{short}SVC.{short}APP"`. Channel ≤ 20 chars.
- **`*.SVRCONN` channels** (`APP.SVRCONN`, `MON.SVRCONN`, `SVC.SVRCONN`) are function-named — **never** renamed.

---

## File Structure

**Phase 1 (de-hardcode; names unchanged):**
- `src/mqlab/setups.py` — `QmConfig` gains `svc` field + derived `qm_app`/`qm_svc`/`chl_to_svc`/`chl_to_app` properties.
- `src/mqlab/cli.py` — `_qm_playbook` (and the provision/obs paths) thread the derived QM vars to ansible.
- `ansible/roles/mq-pcmk-qmgr/templates/inter-qm.mqsc.j2`, `ansible/roles/mq-inter-qm/templates/their-side.mqsc.j2` — literal `QMSVC` → `{{ qm_svc }}`; channel literals → `{{ chl_to_svc }}`/`{{ chl_to_app }}`.
- `ansible/roles/mq-exporter/defaults/main.yml`, `ansible/site-obs.yml`, `ansible/site-distributed.yml` (+ rdqm/nativeha distributed playbooks) — exporter QM/channels + `our_qm` from threaded vars.
- `ansible/vars/pki-entities.yml` → rendered from topology (`src/mqlab/cli.py` render step) instead of static literals.
- `lab/scripts/*.sh` — failover scripts take QM name as `$1`/env, defaulted from a single `mqlab` lookup.
- `clients/app_requester.py` (+ `dr_responder.py`, `svc_responder.py`, `dr_flow.py`) — `--qm` default sourced from config, not a literal.
- `tests/*` — assertions read the derived values.

**Phase 2 (flip):**
- `lab/topology.yaml` — `arms` gain `short`; setups' `qm` derives names from it.
- `src/mqlab/setups.py` — `QmConfig`/arm wiring derives `name`/`svc` from `short`.
- `ansible/vars/pki-entities.yml` (rendered) — new CNs.
- `tests/test_qm_naming_guard.py` — the retired-name deny-list guard.

---

## PHASE 1 — De-hardcode with names unchanged

### Task 1: `QmConfig` derived QM identity

**Files:**
- Modify: `src/mqlab/setups.py:22-40` (`QmConfig`), `:63-85` (`lab_setups`)
- Test: `tests/test_setups.py`

**Interfaces:**
- Produces: `QmConfig.svc: str` (default `"QMSVC"`), and read-only properties `QmConfig.qm_app -> str` (== `name`), `QmConfig.qm_svc -> str` (== `svc`), `QmConfig.chl_to_svc -> str` (== `f"{name}.{svc}"`), `QmConfig.chl_to_app -> str` (== `f"{svc}.{name}"`).

- [ ] **Step 1: Write the failing test**

```python
# tests/test_setups.py
from mqlab.setups import QmConfig

def test_qmconfig_derives_app_svc_and_channel_pair():
    qm = QmConfig(name="QMPCMK", vip="10.10.1.200", vip_ext="10.60.0.10")
    assert qm.svc == "QMSVC"               # default
    assert qm.qm_app == "QMPCMK"
    assert qm.qm_svc == "QMSVC"
    assert qm.chl_to_svc == "QMPCMK.QMSVC"
    assert qm.chl_to_app == "QMSVC.QMPCMK"

def test_qmconfig_svc_override():
    qm = QmConfig(name="QMRDQM", svc="QMSVC")
    assert qm.chl_to_svc == "QMRDQM.QMSVC"
    assert qm.chl_to_app == "QMSVC.QMRDQM"
```

- [ ] **Step 2: Run to verify it fails**

Run: `cd .worktrees/issue-351-qm-naming && uv run pytest tests/test_setups.py -k qmconfig_derives -v`
Expected: FAIL — `QmConfig` has no `svc` / `qm_app`.

- [ ] **Step 3: Implement on `QmConfig`**

```python
# src/mqlab/setups.py — inside QmConfig (keep frozen=True)
    name: str
    vip: str = ""
    vip_ext: str = ""
    svc_conn: str | None = None
    svc: str = "QMSVC"

    @property
    def qm_app(self) -> str:
        return self.name

    @property
    def qm_svc(self) -> str:
        return self.svc

    @property
    def chl_to_svc(self) -> str:
        return f"{self.name}.{self.svc}"

    @property
    def chl_to_app(self) -> str:
        return f"{self.svc}.{self.name}"
```

And load `svc` in `lab_setups()` (the `QmConfig(...)` construction, ~line 75):

```python
            qm=QmConfig(
                name=cfg["qm"]["name"],
                vip=cfg["qm"].get("vip", ""),
                vip_ext=cfg["qm"].get("vip_ext", ""),
                svc_conn=cfg["qm"].get("svc_conn"),
                svc=cfg["qm"].get("svc", "QMSVC"),
            )
```

- [ ] **Step 4: Run to verify it passes**

Run: `cd .worktrees/issue-351-qm-naming && uv run pytest tests/test_setups.py -v`
Expected: PASS (existing `QmConfig` equality tests still pass — `svc` defaults to `"QMSVC"`).

- [ ] **Step 5: Commit**

```bash
cd .worktrees/issue-351-qm-naming
vrg-git add src/mqlab/setups.py tests/test_setups.py
vrg-commit --type feat --scope qm-naming --message "derive qm_app/qm_svc/channel pair on QmConfig" --body "Add svc field (default QMSVC) and read-only qm_app/qm_svc/chl_to_svc/chl_to_app properties; behavior-preserving (names unchanged). Ref #351."
```

---

### Task 2: Thread the derived QM vars into the ansible QM plays

**Files:**
- Modify: `src/mqlab/cli.py:1326-1369` (`_qm_playbook`)
- Test: `tests/test_cli_qm.py:67-88`

**Interfaces:**
- Consumes: `QmConfig.qm_app/qm_svc/chl_to_svc/chl_to_app` from Task 1.
- Produces: the QM plays receive extra-vars `qm_app`, `qm_svc`, `chl_to_svc`, `chl_to_app` (in addition to the existing `qm_name`/`qm_vip`/`qm_vip_ext`/`svc_conn`).

- [ ] **Step 1: Update the failing test** (extend the existing assertion)

```python
# tests/test_cli_qm.py — in test_qm_create_runs_playbook_with_qm_extra_vars
    assert play.argv == [
        "ansible-playbook",
        "site-pcmk-qm.yml",
        "-e", "qm_name=QMPCMK",
        "-e", "qm_vip=10.10.1.200",
        "-e", "qm_vip_ext=10.60.0.10",
        "-e", "qm_app=QMPCMK",
        "-e", "qm_svc=QMSVC",
        "-e", "chl_to_svc=QMPCMK.QMSVC",
        "-e", "chl_to_app=QMSVC.QMPCMK",
    ]
```

- [ ] **Step 2: Run to verify it fails**

Run: `cd .worktrees/issue-351-qm-naming && uv run pytest tests/test_cli_qm.py -k extra_vars -v`
Expected: FAIL — new `-e` pairs absent.

- [ ] **Step 3: Add the extra-vars in `_qm_playbook`**

```python
# src/mqlab/cli.py — inside the Command argv list in _qm_playbook, after qm_vip_ext
                    "-e", f"qm_vip_ext={qm.vip_ext}",
                    "-e", f"qm_app={qm.qm_app}",
                    "-e", f"qm_svc={qm.qm_svc}",
                    "-e", f"chl_to_svc={qm.chl_to_svc}",
                    "-e", f"chl_to_app={qm.chl_to_app}",
                    *(["-e", f"svc_conn={qm.svc_conn}"] if qm.svc_conn else []),
```

- [ ] **Step 4: Run to verify it passes**

Run: `cd .worktrees/issue-351-qm-naming && uv run pytest tests/test_cli_qm.py -v`
Expected: PASS.

- [ ] **Step 5: Commit**

```bash
cd .worktrees/issue-351-qm-naming
vrg-git add src/mqlab/cli.py tests/test_cli_qm.py
vrg-commit --type feat --scope qm-naming --message "thread qm_app/qm_svc/channel pair into QM plays" --body "Ref #351."
```

---

### Task 3: De-hardcode the inter-QM MQSC templates

**Files:**
- Modify: `ansible/roles/mq-pcmk-qmgr/templates/inter-qm.mqsc.j2`, `ansible/roles/mq-inter-qm/templates/their-side.mqsc.j2`

**Interfaces:**
- Consumes: ansible vars `qm_app`, `qm_svc`, `chl_to_svc`, `chl_to_app` (Task 2). Note: `mq-pcmk-qmgr` already receives `qm_name` (== `qm_app`); add `qm_svc`/`chl_*` to its var pass-through if the role is invoked outside `_qm_playbook` — verify the role's caller passes them (the `qm-create` play does).

No unit test (Jinja templates render under ansible; verified by the Task 7 cold rebuild). These edits replace literals 1:1 with the equivalent vars, so the rendered output is **identical** while names are unchanged.

- [ ] **Step 1: Edit `inter-qm.mqsc.j2`** — replace literal `QMSVC` with `{{ qm_svc }}` and channel literals with the derived pair:

```jinja2
DEFINE QREMOTE(SVC.REQUEST) RNAME(SVC.REQUEST) RQMNAME({{ qm_svc }}) XMITQ({{ qm_svc }}) REPLACE
DEFINE QLOCAL({{ qm_svc }}) USAGE(XMITQ) TRIGGER TRIGTYPE(FIRST) INITQ(SYSTEM.CHANNEL.INITQ) TRIGDATA({{ chl_to_svc }}) REPLACE
DEFINE CHANNEL({{ chl_to_svc }}) CHLTYPE(SDR) TRPTYPE(TCP) CONNAME('{{ svc_conn }}(1414)') XMITQ({{ qm_svc }}) SSLCIPH({{ tls_cipher }}) SSLPEER('{{ tls_peer_svc }}') SHORTRTY(10) SHORTTMR(5) LONGRTY(999999999) LONGTMR(20) REPLACE
DEFINE CHANNEL({{ chl_to_app }}) CHLTYPE(RCVR) TRPTYPE(TCP) SSLCIPH({{ tls_cipher }}) SSLCAUTH(REQUIRED) SSLPEER('{{ tls_peer_svc }}') REPLACE
```
(`{{ qm_name }}` references already exist and == `qm_app`; leave them or switch to `{{ qm_app }}` for consistency — keep `{{ qm_name }}` to avoid churn since the role var is `qm_name`.)

- [ ] **Step 2: Edit `their-side.mqsc.j2`** — it already uses `{{ our_qm }}`/`{{ qmgr_name }}` (no literal QM names except the SVRCONN channels, which stay). Confirm the channel `DEFINE`s use `{{ our_qm }}.{{ qmgr_name }}` form (they do) — **no change needed beyond confirming**; the `SVC.SVRCONN`/`MON.SVRCONN` lines are out of scope.

- [ ] **Step 3: Static render check** — confirm no stray literal `QMSVC` remains in the edited template:

Run: `cd .worktrees/issue-351-qm-naming && grep -n 'QMSVC' ansible/roles/mq-pcmk-qmgr/templates/inter-qm.mqsc.j2`
Expected: no output (all replaced by `{{ qm_svc }}`).

- [ ] **Step 4: Commit**

```bash
cd .worktrees/issue-351-qm-naming
vrg-git add ansible/roles/mq-pcmk-qmgr/templates/inter-qm.mqsc.j2 ansible/roles/mq-inter-qm/templates/their-side.mqsc.j2
vrg-commit --type refactor --scope qm-naming --message "derive inter-QM MQSC from qm_svc/channel vars" --body "1:1 var substitution; rendered output unchanged. Ref #351."
```

---

### Task 4: De-hardcode the exporter + obs/distributed playbooks

**Files:**
- Modify: `ansible/roles/mq-exporter/defaults/main.yml`, `ansible/site-obs.yml`, `ansible/site-distributed.yml`, `ansible/site-rdqm-distributed.yml`, `ansible/site-nativeha.yml`

**Interfaces:**
- Consumes: `setup_dict.qm.*` (site-obs already reads `setup_dict.qm.svc_conn`), threaded `qm_app`/`qm_svc`/`chl_*`.

No Python unit test (ansible vars; cold-rebuild verifies). These keep the *old* names because the source (`QmConfig`) still yields them.

- [ ] **Step 1: `mq-exporter/defaults/main.yml`** — make the QM-derived defaults reference vars instead of literals. Change the monitored-channels literal to derive:

```yaml
mq_exporter_qm: "{{ qm_app | default('QMPCMK') }}"
mq_exporter_monitored_queues: "HA.TEST,{{ qm_svc | default('QMSVC') }},APP.REPLY"
mq_exporter_monitored_channels: "APP.SVRCONN,{{ chl_to_svc | default('QMPCMK.QMSVC') }},{{ chl_to_app | default('QMSVC.QMPCMK') }}"
```

- [ ] **Step 2: `site-obs.yml`** — the QMSVC exporter block: replace literal `QMPCMK`/`QMSVC` in `mq_exporter_monitored_*` with the threaded vars (`setup_dict.qm.qm_app`, `setup_dict.qm.qm_svc`, and the channel pair). Pass `mq_exporter_qm: "{{ setup_dict.qm.qm_svc }}"` for the svc exporter; its monitored channels become `"SVC.SVRCONN,MON.SVRCONN,{{ setup_dict.qm.chl_to_svc }},{{ setup_dict.qm.chl_to_app }}"`.

- [ ] **Step 3: `site-distributed.yml`** (and the rdqm/nativeha distributed playbooks) — `our_qm: QMPCMK` becomes `our_qm: "{{ setup_dict.qm.qm_app }}"`. Confirm `setup_dict` is in scope for these playbooks (it is — site-obs already uses it; thread it the same way).

- [ ] **Step 4: Confirm no literal QM names remain in these files**

Run: `cd .worktrees/issue-351-qm-naming && grep -rn 'QMPCMK\|QMSVC\|QMNATIVE\|QMRDQM' ansible/roles/mq-exporter/defaults/main.yml ansible/site-obs.yml ansible/site-distributed.yml`
Expected: only `default('QM...')` fallbacks remain (acceptable — they reproduce old names if a var is unset).

- [ ] **Step 5: Commit**

```bash
cd .worktrees/issue-351-qm-naming
vrg-git add ansible/roles/mq-exporter/defaults/main.yml ansible/site-obs.yml ansible/site-distributed.yml ansible/site-rdqm-distributed.yml ansible/site-nativeha.yml
vrg-commit --type refactor --scope qm-naming --message "derive exporter + distributed QM refs from config" --body "Ref #351."
```

---

### Task 5: Render `pki-entities.yml` from topology

**Files:**
- Modify: `src/mqlab/cli.py` (add `_render_pki_entities()` near `_render_reach_peers` ~line 521), `ansible/vars/pki-entities.yml` → consume the rendered file or keep as a rendered artifact under `build/work/`
- Test: `tests/test_cli_pki.py` (new) or extend `tests/test_cli_obs.py`

**Interfaces:**
- Produces: `_render_pki_entities() -> Path` writes `build/work/pki/entities.json` with one entry per stack's `qm_app` (org `app-org`) and `qm_svc` (org `svc-org`), plus the fixed non-QM entities (`app-client`, `mq_prometheus`, `mqweb`, `pymqrest`, `svc-responder`).

- [ ] **Step 1: Write the failing test**

```python
# tests/test_cli_pki.py
from mqlab import cli

def test_render_pki_entities_includes_derived_qm_cns(monkeypatch, tmp_path):
    monkeypatch.setenv("MQLAB_REPO_ROOT", str(tmp_path))
    _seed(tmp_path)  # seeds topology.yaml; reuse the helper from test_setups
    path = cli._render_pki_entities()
    data = json.loads(path.read_text())
    cns = {e["cn"]: e["org"] for e in data}
    assert cns["QMPCMK"] == "app-org"
    assert cns["QMSVC"] == "svc-org"
```

- [ ] **Step 2: Run to verify it fails**

Run: `cd .worktrees/issue-351-qm-naming && uv run pytest tests/test_cli_pki.py -v`
Expected: FAIL — `_render_pki_entities` undefined.

- [ ] **Step 3: Implement `_render_pki_entities()`** deriving QM CNs from `lab_setups()` (dedupe `qm_app`/`qm_svc` across setups), writing the fixed entities + the derived ones. Point the PKI role/playbook at the rendered file (replace the static `ansible/vars/pki-entities.yml` literals with a load of the rendered artifact, or have `mqlab` write it before the PKI play).

- [ ] **Step 4: Run to verify it passes**

Run: `cd .worktrees/issue-351-qm-naming && uv run pytest tests/test_cli_pki.py -v`
Expected: PASS.

- [ ] **Step 5: Commit**

```bash
cd .worktrees/issue-351-qm-naming
vrg-git add src/mqlab/cli.py tests/test_cli_pki.py ansible/vars/pki-entities.yml
vrg-commit --type refactor --scope qm-naming --message "render PKI entities' QM CNs from topology" --body "Ref #351."
```

---

### Task 6: De-hardcode `lab/scripts` + `clients`

**Files:**
- Modify: `lab/scripts/pcmk-dr-cutover.sh:56`, `lab/scripts/pcmk-qm-create.sh`, `lab/scripts/nativeha-fault-suite.sh:9`, `lab/scripts/rdqm-dr-cutover.sh` (already arg-defaulted), `lab/scripts/e2e-test.sh`, `lab/scripts/dr-run.sh`
- Modify: `clients/app_requester.py:29`, `clients/dr_responder.py`, `clients/dr_flow.py`

**Interfaces:**
- Consumes: a single `mqlab` lookup for a setup's `qm_app` (add a tiny read-only command, e.g. `mqlab qm name <setup>` printing `qm.qm_app`, OR have scripts accept the name as `$1`). Recommended: scripts take the QM name as their first positional arg, and their callers (`e2e-test.sh`, `dr-run.sh`, the `mqlab qm` verbs) pass `$(mqlab ...)`-resolved names — no literal in the script body.

- [ ] **Step 1: `nativeha-fault-suite.sh`** — replace `QM=QMNATIVE` with `QM="${1:?usage: nativeha-fault-suite.sh <qm-name> ...}"` (or env `QM="${QM:?}"`). Update its caller (`dr-run.sh`/`e2e-test.sh`) to pass the resolved name.

- [ ] **Step 2: `pcmk-dr-cutover.sh:56`** — replace `systemd:mq-QMPCMK` with `systemd:mq-${QM:?}` and add `QM="${1:?usage: pcmk-dr-cutover.sh <qm-name>}"` near the top. Update callers.

- [ ] **Step 3: `clients/app_requester.py:29`** — change `ap.add_argument("--qm", default="QMPCMK")` to `ap.add_argument("--qm", default=os.environ.get("MQLAB_QM", "QMPCMK"))` so the default is overridable from the harness without a literal in the flow. Apply the same to `dr_responder.py` / `dr_flow.py` where a literal default exists.

- [ ] **Step 4: Confirm no bare literals remain in script/client bodies**

Run: `cd .worktrees/issue-351-qm-naming && grep -rn 'QMPCMK\|QMNATIVE\|QMRDQM' lab/scripts/ clients/ | grep -v 'default=os.environ'`
Expected: only fallback defaults remain.

- [ ] **Step 5: Commit**

```bash
cd .worktrees/issue-351-qm-naming
vrg-git add lab/scripts/ clients/
vrg-commit --type refactor --scope qm-naming --message "parameterize QM name in failover scripts + clients" --body "Ref #351."
```

---

### Task 7: Phase-1 acceptance — cold rebuild proves byte-identical

**Files:** none (verification task).

- [ ] **Step 1: Full validation**

Run: `cd .worktrees/issue-351-qm-naming && vrg-container-run -- vrg-validate`
Expected: green (lint/type/test/audit; 100% coverage).

- [ ] **Step 2: Human-run cold rebuild of `pcmk-ubuntu`** (lab op — hand to the human per the human-operates-the-lab boundary). Bring up `distributed-pcmk-ubuntu`; confirm the QM is `QMPCMK`, the channels are `QMPCMK.QMSVC` / `QMSVC.QMPCMK`, the exporter scrapes, and the dashboard populates — **identical to before**. This proves the refactor changed no behavior.

- [ ] **Step 3: Commit a checkpoint note** (if any doc/CHANGELOG update): `vrg-commit --type docs --scope qm-naming --message "phase 1 de-hardcode complete (names unchanged)" --body "Ref #351."`

---

## PHASE 2 — Flip to the new names

### Task 8: Introduce `short` and derive names from it

**Files:**
- Modify: `lab/topology.yaml` (`arms` gain `short`; setups' `qm.name`/`qm.svc` removed where derived), `src/mqlab/setups.py` (resolve `name`/`svc` from the arm's `short`)
- Test: `tests/test_setups.py`

**Interfaces:**
- Consumes: arm `short` from topology.
- Produces: for `pcmk-ubuntu`, `QmConfig.qm_app == "PCMKAPP"`, `qm_svc == "PCMKSVC"`, `chl_to_svc == "PCMKAPP.PCMKSVC"`.

- [ ] **Step 1: Write the failing test**

```python
# tests/test_setups.py
def test_short_drives_qm_names(monkeypatch, tmp_path):
    monkeypatch.setenv("MQLAB_REPO_ROOT", str(tmp_path))
    _seed(tmp_path)  # seed must include arm short: PCMK
    qm = lab_setups()["distributed-pcmk-ubuntu"].qm
    assert qm.qm_app == "PCMKAPP"
    assert qm.qm_svc == "PCMKSVC"
    assert qm.chl_to_svc == "PCMKAPP.PCMKSVC"
    assert qm.chl_to_app == "PCMKSVC.PCMKAPP"
```

- [ ] **Step 2: Run to verify it fails**

Run: `cd .worktrees/issue-351-qm-naming && uv run pytest tests/test_setups.py -k short_drives -v`
Expected: FAIL — names still `QMPCMK`.

- [ ] **Step 3: Add `short` to arms in `lab/topology.yaml`** and resolve in `lab_setups()`:

```yaml
# lab/topology.yaml — arms
  pcmk-ubuntu:
    mechanism: pacemaker-san
    cluster_group: pcmk_a
    short: PCMK
    verbs: { ... }
  rdqm-rhel:    { ..., short: RDQM }
  nativeha-rhel:{ ..., short: NHAR }
```

```python
# src/mqlab/setups.py — in lab_setups(), when building QmConfig, resolve from the arm's short
            arm_cfg = (data.get("arms") or {}).get(cfg.get("arm") or "", {})
            short = arm_cfg.get("short")
            qm_name = f"{short}APP" if short else cfg["qm"]["name"]
            qm_svc = f"{short}SVC" if short else cfg["qm"].get("svc", "QMSVC")
            qm=QmConfig(name=qm_name, ..., svc=qm_svc)
```

Remove the now-derived `name`/`svc` from each surviving setup's `qm:` block (keep `vip`/`vip_ext`/`svc_conn`). Drop the doomed setups' QM names too (they're deleted in #350; leaving them is harmless but de-hardcode them for the guard test).

- [ ] **Step 4: Run to verify it passes** — and update the Phase-1 tests that asserted `QMPCMK`/`QMSVC` literals to the new names (`PCMKAPP`/`PCMKSVC`), including `test_cli_qm.py` and `test_setups.py`.

Run: `cd .worktrees/issue-351-qm-naming && uv run pytest tests/ -v`
Expected: PASS after updating literal assertions to new names.

- [ ] **Step 5: Commit**

```bash
cd .worktrees/issue-351-qm-naming
vrg-git add lab/topology.yaml src/mqlab/setups.py tests/
vrg-commit --type feat --scope qm-naming --message "flip QM names to short-derived <short>APP/<short>SVC" --body "PCMK/RDQM/NHAR short tokens drive qm_app/qm_svc/channel pair; drops the QM prefix. Ref #351."
```

---

### Task 9: Update PKI CNs + any remaining new-name touchpoints

**Files:**
- Modify: the rendered PKI entities (Task 5 now yields `PCMKAPP`/`PCMKSVC`/`RDQMAPP`/… automatically), `ansible/vars/pki-entities.yml` static fallbacks if any remain
- Test: extend `tests/test_cli_pki.py`

- [ ] **Step 1: Update the PKI test to new CNs**

```python
# tests/test_cli_pki.py
    assert cns["PCMKAPP"] == "app-org"
    assert cns["PCMKSVC"] == "svc-org"
```

- [ ] **Step 2: Run to verify it fails then passes** — Task 5's renderer derives from `QmConfig.qm_app/qm_svc`, so once Task 8 flips the source, the render yields new CNs. Confirm:

Run: `cd .worktrees/issue-351-qm-naming && uv run pytest tests/test_cli_pki.py -v`
Expected: PASS (the renderer already derives; just the assertion changed).

- [ ] **Step 3: Sweep any residual literals** the var-threading didn't reach (comments/log strings that name the QM, e.g. site-obs.yml header comments `QMPCMK on :9157`). Update comments to the new names for accuracy.

- [ ] **Step 4: Commit**

```bash
cd .worktrees/issue-351-qm-naming
vrg-git add tests/test_cli_pki.py ansible/
vrg-commit --type refactor --scope qm-naming --message "PKI CNs + comments to new QM names" --body "Ref #351."
```

---

### Task 10: The retired-name deny-list guard test

**Files:**
- Create: `tests/test_qm_naming_guard.py`

**Interfaces:**
- Consumes: nothing. Walks repo code paths and asserts no retired literal.

- [ ] **Step 1: Write the guard test**

```python
# tests/test_qm_naming_guard.py
import re
from pathlib import Path

REPO = Path(__file__).resolve().parents[1]
CODE_DIRS = ["ansible", "src", "lab/scripts", "clients", "tests"]
RETIRED = re.compile(r"\bQM(PCMK|SVC|NATIVE|RDQM)\b")
# The source of truth may legitimately mention them only as fallbacks; allowlist none in code.
ALLOWLIST = {
    REPO / "tests" / "test_qm_naming_guard.py",  # this file names them by definition
}

def _code_files():
    for d in CODE_DIRS:
        for p in (REPO / d).rglob("*"):
            if p.is_file() and p.suffix in {".py", ".yml", ".yaml", ".j2", ".sh", ".mqsc"}:
                yield p

def test_no_retired_qm_name_literals_in_code():
    offenders = []
    for p in _code_files():
        if p in ALLOWLIST:
            continue
        for i, line in enumerate(p.read_text(errors="ignore").splitlines(), 1):
            if RETIRED.search(line):
                offenders.append(f"{p.relative_to(REPO)}:{i}: {line.strip()}")
    assert not offenders, "retired QM-name literals found:\n" + "\n".join(offenders)
```

- [ ] **Step 2: Run it** — it now enumerates every remaining literal (Phase-1 `default('QM...')` fallbacks, stray comments). Fix each by deriving or removing, until clean.

Run: `cd .worktrees/issue-351-qm-naming && uv run pytest tests/test_qm_naming_guard.py -v`
Expected: first run lists offenders → remove them → PASS. (Remove the Phase-1 `| default('QM...')` fallbacks now that the source always supplies the value.)

- [ ] **Step 3: Confirm `docs/` and `CHANGELOG` are excluded** — they're not in `CODE_DIRS`, so historical references (and these specs) don't trip the gate.

- [ ] **Step 4: Commit**

```bash
cd .worktrees/issue-351-qm-naming
vrg-git add tests/test_qm_naming_guard.py ansible/ src/ lab/scripts/ clients/
vrg-commit --type test --scope qm-naming --message "add retired-QM-name deny-list guard; remove fallbacks" --body "Code-path-scoped; docs/CHANGELOG excluded. Ref #351."
```

---

### Task 11: Phase-2 acceptance — cold rebuild under new names

**Files:** none (verification).

- [ ] **Step 1: Full validation**

Run: `cd .worktrees/issue-351-qm-naming && vrg-container-run -- vrg-validate`
Expected: green, including `test_qm_naming_guard.py`.

- [ ] **Step 2: Human-run cold rebuild of `pcmk-ubuntu`** — confirm the QM is `PCMKAPP`, svc QM `PCMKSVC`, channels `PCMKAPP.PCMKSVC` / `PCMKSVC.PCMKAPP`, exporter scrapes, dashboard populates, and TLS handshakes succeed (the PKI CNs flipped too). This is the rename's proof.

- [ ] **Step 3: Final commit / PR prep**

```bash
cd .worktrees/issue-351-qm-naming
vrg-commit --type docs --scope qm-naming --message "phase 2 rename complete (PCMKAPP/PCMKSVC)" --body "Ref #351."
```

---

## Self-Review

**Spec coverage:** §3 naming/source → Tasks 1, 8; §3 channel pair → Tasks 1–3; §4 de-hardcode (python/ansible/scripts/clients) → Tasks 1–6; §4 regression gate → Task 10; §5 svc-per-stack-in-provision → Tasks 3–4 (their-side/inter-qm in provision); §6 migration two-phase → Phase 1 / Phase 2 split; §7 testing (unit, validate, per-phase cold rebuild) → Tasks 1/8 unit, Tasks 7/11 cold rebuild, Task 10 gate. No gaps.

**Placeholder scan:** every code step shows real code/edits; mechanical bulk edits (Tasks 3–6) cite exact files + the literal→var substitution. No "TBD"/"add error handling".

**Type consistency:** `qm_app`/`qm_svc`/`chl_to_svc`/`chl_to_app` used identically across Tasks 1→2→3→4→5→8→9. The guard regex `QM(PCMK|SVC|NATIVE|RDQM)` matches the four retired names exactly.

**Note for the implementer:** Tasks 3–6 have no Python unit coverage (ansible/bash/templates aren't under `--cov=src`); they are proven by the Task 7 cold rebuild. Keep Phase-1 edits behavior-preserving — if a cold rebuild in Task 7 differs from baseline, a de-hardcode wired the wrong var. Do not start Phase 2 until Task 7 is byte-identical.
