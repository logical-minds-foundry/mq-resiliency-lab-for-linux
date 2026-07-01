# Shared Business-B svc counterparty (`SVCQM`) Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Replace the broken per-stack `{short}SVC` counterparty model with a single shared `SVCQM` queue manager whose request path stays independent per stack, fixing the `MQRC 2058` svc-exporter crash-loop (#446).

**Architecture:** One `SVCQM` on `svc-sim:1414` (one listener, one cert, one exporter), but each app stack gets its own request queue `{SHORT}.SVC.REQUEST` on `SVCQM` serviced by its own `mq-svc-responder@` instance. Inter-QM channel pairs stay per app QM (`{APP}.SVCQM` / `SVCQM.{APP}`); replies route by `MQMD.ReplyToQMgr` (per-app transmission queue), so a shared request QM never misroutes a reply. See the design: `docs/specs/2026-07-01-shared-svc-counterparty-design.md`.

**Tech Stack:** Python 3 (`src/mqlab`, pytest), Ansible + Jinja2 (`ansible/`), IBM MQ MQSC, systemd, Prometheus/Grafana JSON boards, `lab/topology.yaml`.

## Global Constraints

- **Do NOT run the live lab from this box.** Implementation + the cold-rebuild acceptance run on the macOS/live-lab side per #446. Here, only the Python/YAML/Jinja edits + `vrg-validate` are exercised.
- **Worktree:** all edits happen in `/.worktrees/issue-446-shared-svc-qm/` on branch `feature/446-shared-svc-qm`. Use absolute worktree paths; `cd` into the worktree for Bash.
- **Git:** commit with `vrg-commit --type <type> --scope <scope> --message <msg>`; stage with `vrg-git add`. Raw `git`/`gh` are denied — use `vrg-git`/`vrg-gh`.
- **Validation gate:** the only acceptance command is `vrg-container-run -- vrg-validate`. Run it before every commit. The TDD inner loop may run a single test with `vrg-container-run -- uv run pytest <path>::<test> -v`.
- **Naming (verbatim):** svc short token `SVC`; svc QM name `SVCQM` (= `{svc.short}QM`); per-stack request queue `{SHORT}.SVC.REQUEST` (e.g. `PCMK.SVC.REQUEST`); channel pair `{APP}.SVCQM` / `SVCQM.{APP}`. No hardcoded QM literals outside the `svc:` block and the `short` tokens (#351).
- **Shared svc exporter port:** `9158` (reused from pcmk's freed `exporter_svc_port`).
- **Fail-loud:** no swallowed exceptions, no silent skips. A topology missing the `svc:` block is an error, not a default.

---

### Task 1: Topology `svc:` block + shared svc identity in `stacks.py`

Adds the single source of svc truth and rewires `QmConfig` to read it. Everything downstream depends on this task.

**Files:**
- Modify: `lab/topology.yaml` (add top-level `svc:` block; remove per-stack `qm.svc_conn` and `alloc.exporter_svc_port`)
- Modify: `src/mqlab/stacks.py` (QmConfig: add `short` field + `req_queue` property; add `_svc_identity`; rewire `_qm_from_stack` + `lab_stacks`)
- Test: `tests/test_stacks.py`, `tests/test_topology_integrity.py`

**Interfaces:**
- Produces: `QmConfig.short: str`, `QmConfig.req_queue -> str` (`"{short}.SVC.REQUEST"` or `""`); `stacks._svc_identity(topo: dict) -> tuple[str, str]` returning `(svc_qm_name, svc_conn)`; `QmConfig.qm_svc == "SVCQM"` and `QmConfig.svc_conn == "10.60.0.50"` for every real stack.
- Consumes: `lab/topology.yaml` top-level key `svc: {short, conn, listener_port, exporter_port}`.

- [ ] **Step 1: Write the failing test** — add to `tests/test_stacks.py`:

```python
def test_qmconfig_req_queue_derives_from_short() -> None:
    qm = QmConfig(name="PCMKAPP", short="PCMK", svc="SVCQM", svc_conn="10.60.0.50")
    assert qm.qm_svc == "SVCQM"
    assert qm.chl_to_svc == "PCMKAPP.SVCQM"
    assert qm.chl_to_app == "SVCQM.PCMKAPP"
    assert qm.req_queue == "PCMK.SVC.REQUEST"


def test_qmconfig_req_queue_empty_without_short() -> None:
    assert QmConfig(name="PCMKAPP").req_queue == ""
```

- [ ] **Step 2: Run to verify it fails**

Run: `cd /.worktrees/issue-446-shared-svc-qm && vrg-container-run -- uv run pytest tests/test_stacks.py::test_qmconfig_req_queue_derives_from_short -v`
Expected: FAIL — `QmConfig.__init__() got an unexpected keyword argument 'short'`.

- [ ] **Step 3: Add the `short` field + `req_queue` property** to `QmConfig` in `src/mqlab/stacks.py` (after the `svc` field and existing properties):

```python
    name: str
    short: str = ""
    vip: str = ""
    vip_ext: str = ""
    svc_conn: str | None = None
    svc: str = ""
```

```python
    @property
    def req_queue(self) -> str:
        """This stack's own request queue on the shared SVCQM (1b independence)."""
        return f"{self.short}.SVC.REQUEST" if self.short else ""
```

- [ ] **Step 4: Run to verify the QmConfig tests pass**

Run: `cd /.worktrees/issue-446-shared-svc-qm && vrg-container-run -- uv run pytest tests/test_stacks.py::test_qmconfig_req_queue_derives_from_short tests/test_stacks.py::test_qmconfig_req_queue_empty_without_short -v`
Expected: PASS (2 passed).

- [ ] **Step 5: Write the failing shared-identity test** — add to `tests/test_stacks.py`:

```python
def test_svc_identity_reads_the_svc_block() -> None:
    from mqlab.stacks import _svc_identity

    topo = {"svc": {"short": "SVC", "conn": "10.60.0.50"}}
    assert _svc_identity(topo) == ("SVCQM", "10.60.0.50")


def test_svc_identity_fail_loud_without_block() -> None:
    from mqlab.stacks import _svc_identity

    with pytest.raises(ValueError, match="svc"):
        _svc_identity({"svc": {}})
```

Add `import pytest` at the top of `tests/test_stacks.py` if not already present.

- [ ] **Step 6: Run to verify it fails**

Run: `cd /.worktrees/issue-446-shared-svc-qm && vrg-container-run -- uv run pytest tests/test_stacks.py::test_svc_identity_reads_the_svc_block -v`
Expected: FAIL — `cannot import name '_svc_identity'`.

- [ ] **Step 7: Implement `_svc_identity` and rewire `_qm_from_stack` + `lab_stacks`** in `src/mqlab/stacks.py`:

```python
def _svc_identity(topo: dict[str, Any]) -> tuple[str, str]:
    """The shared Business-B counterparty identity from the top-level `svc:` block:
    (SVC QM name, its CONNAME). Fail loud — svc is required, never defaulted."""
    svc = topo.get("svc") or {}
    short = svc.get("short")
    conn = svc.get("conn")
    if not short or not conn:
        raise ValueError("topology `svc:` block must declare `short` and `conn`")
    return f"{short}QM", str(conn)


def _qm_from_stack(
    short: str, qm_cfg: dict[str, Any], svc_name: str, svc_conn: str
) -> QmConfig:
    """Build a QmConfig from a stack's short + qm: sub-block and the SHARED svc
    identity. The app QM name derives from short (<short>APP); the counterparty is
    the single SVCQM (name + conn threaded in), not a per-stack {short}SVC (#446)."""
    return QmConfig(
        name=f"{short}APP",
        short=short,
        vip=qm_cfg.get("vip", ""),
        vip_ext=qm_cfg.get("vip_ext", ""),
        svc_conn=svc_conn,
        svc=svc_name,
    )
```

In `lab_stacks()`, read the svc identity once and pass it into each `_qm_from_stack` call:

```python
def lab_stacks() -> dict[str, Stack]:
    data = _topology()
    svc_name, svc_conn = _svc_identity(data)
    result: dict[str, Stack] = {}
    for name, cfg in (data.get("stacks") or {}).items():
        cfg = cfg or {}
        short = cfg["short"]
        result[name] = Stack(
            name=name,
            mechanism=cfg["mechanism"],
            os=cfg["os"],
            short=short,
            verbs=dict(cfg.get("verbs") or {}),
            cluster_group=cfg.get("cluster_group") or None,
            groups=list(cfg.get("groups") or []),
            qm=_qm_from_stack(short, dict(cfg.get("qm") or {}), svc_name, svc_conn),
            provision=cfg.get("provision"),
            secrets=list(cfg.get("secrets") or []),
            alloc=dict(cfg.get("alloc") or {}),
        )
    return result
```

- [ ] **Step 8: Add the `svc:` block to `lab/topology.yaml`** as a top-level key (sibling of `stacks:` / `commons:`):

```yaml
# svc: — the single shared Business-B counterparty (#446). One SVCQM on svc-sim;
# each app stack owns its own {SHORT}.SVC.REQUEST queue + responder for independence.
svc:
  short: SVC              # → QM name SVCQM (parallels {short}APP)
  conn: 10.60.0.50        # inter-business WAN CONNAME (was per-stack qm.svc_conn)
  listener_port: 1414
  exporter_port: 9158     # the one shared svc exporter port
```

- [ ] **Step 9: Remove the now-redundant per-stack svc fields** in `lab/topology.yaml`:
  - Delete `svc_conn: 10.60.0.50` from each stack's `qm:` block (pcmk-ubuntu, rdqm-rhel, nativeha-rhel, nativeha-ubuntu).
  - Delete `exporter_svc_port: <n>` from each stack's `alloc:` block (all four).

- [ ] **Step 10: Update the topology-derived tests** to the shared identity.
  In `tests/test_stacks.py`, update `test_qm_names_derive_from_short` (and the fixture YAML string) — add a `svc:` block to the fixture and change svc expectations:

```python
    # fixture YAML: add at top level (dedent to match the existing string)
    "svc: { short: SVC, conn: 10.60.0.50, listener_port: 1414, exporter_port: 9158 }\n"
```

```python
    assert stacks["pcmk-ubuntu"].qm.qm_svc == "SVCQM"
    assert stacks["rdqm-rhel"].qm.qm_svc == "SVCQM"
```

  Also drop `svc_conn:` / `exporter_svc_port:` lines from the fixture stacks (they now live in the `svc:` block).
  In `tests/test_topology_integrity.py`, update `test_pcmk_stack_composed` and `test_rdqm_stack_composed`:

```python
    assert dist.qm.qm_app == "PCMKAPP" and dist.qm.qm_svc == "SVCQM"  # shared (#446)
    assert dist.qm.chl_to_svc == "PCMKAPP.SVCQM"
    assert dist.qm.svc_conn == "10.60.0.50"  # from the shared svc: block
    assert dist.qm.req_queue == "PCMK.SVC.REQUEST"
```

- [ ] **Step 11: Run the full validation gate**

Run: `cd /.worktrees/issue-446-shared-svc-qm && vrg-container-run -- vrg-validate`
Expected: PASS. (If `test_stacks`/`test_topology_integrity` still reference `PCMKSVC`, fix those assertions — they are exactly the per-stack literals this task removes.)

- [ ] **Step 12: Commit**

```bash
cd /.worktrees/issue-446-shared-svc-qm
vrg-git add lab/topology.yaml src/mqlab/stacks.py tests/test_stacks.py tests/test_topology_integrity.py
vrg-commit --type feat --scope svc --message "add shared SVCQM identity (svc: block + QmConfig.short/req_queue) (#446)"
```

---

### Task 2: Exporter emits one `SVCQM` instance, decoupled from per-stack svc port

The core #446 fix: stop emitting a per-stack `{short}SVC` exporter target against a shared address; emit exactly one `SVCQM` instance from the `svc:` block, and decouple app emission from `svc_port`.

**Files:**
- Modify: `src/mqlab/scrape.py` (`mq_exporter_instances`; delete `SVC_EXPORTER_CONN`)
- Test: `tests/test_scrape.py`

**Interfaces:**
- Consumes: `QmConfig`/topology from Task 1; `topo["svc"] = {short, conn, exporter_port}`.
- Produces: `mq_exporter_instances(topo)` returns per-stack **app** instances (guarded on `app_port` alone) **plus exactly one** svc instance `{"qm": "SVCQM", "role": "svc", "stack": "commons", "conn": "10.60.0.50(1414)", "port": 9158, ...}`.

- [ ] **Step 1: Rewrite the fixture + failing test** in `tests/test_scrape.py`. Add a `svc:` block to `MQ_TOPO` and drop `exporter_svc_port` from the stacks:

```python
    "svc": {"short": "SVC", "conn": "10.60.0.50", "exporter_port": 9158},
    "stacks": {
        "pcmk-ubuntu": {
            "short": "PCMK",
            "qm": {"vip": "10.10.1.200"},
            "alloc": {"exporter_app_port": 9157},
        },
        "nha-x": {
            "short": "NHAX",
            "cluster_group": "nha_x_a",
            "qm": {},
            "alloc": {"exporter_app_port": 9163},
        },
        "reserved": {"short": "RSVD", "qm": {}, "alloc": {}},  # no app port -> skipped
    },
```

Replace `test_mq_exporter_instances_one_app_and_svc_pair_per_stack` with:

```python
def test_mq_exporter_instances_app_per_stack_plus_one_shared_svc():
    from mqlab.scrape import mq_exporter_instances

    insts = mq_exporter_instances(MQ_TOPO)
    # two app instances (reserved skipped) + exactly one shared svc instance
    assert [(i["qm"], i["role"], i["port"]) for i in insts] == [
        ("PCMKAPP", "app", 9157),
        ("NHAXAPP", "app", 9163),
        ("SVCQM", "svc", 9158),
    ]
    svc = [i for i in insts if i["role"] == "svc"]
    assert len(svc) == 1
    assert svc[0]["stack"] == "commons"
    assert all(i["channel"] == "MON.SVRCONN" for i in insts)


def test_svc_exporter_conn_matches_its_qm_name_446_regression():
    """The svc instance's conn must resolve to the QM it names — the #446 bug was
    name (NHAUSVC) vs address (svc-sim answering as another QM) inconsistency."""
    from mqlab.scrape import mq_exporter_instances

    svc = next(i for i in mq_exporter_instances(MQ_TOPO) if i["role"] == "svc")
    assert svc["qm"] == "SVCQM"
    assert svc["conn"] == "10.60.0.50(1414)"
```

Update `test_mq_exporter_app_conn_is_vip_or_native_ha_instance_list` — the svc conn assertion becomes the single `SVCQM` entry:

```python
    assert by_qm["PCMKAPP"]["conn"] == "10.10.1.200(1414)"
    assert by_qm["SVCQM"]["conn"] == "10.60.0.50(1414)"
    assert by_qm["NHAXAPP"]["conn"] == "10.10.1.11(1414),10.10.1.12(1414),10.10.1.13(1414)"
```

For the fail-loud fixtures (`_ports = {...}` at lines ~151/164/177), drop `"exporter_svc_port": 2` — they only need `exporter_app_port`.

- [ ] **Step 2: Run to verify it fails**

Run: `cd /.worktrees/issue-446-shared-svc-qm && vrg-container-run -- uv run pytest tests/test_scrape.py::test_mq_exporter_instances_app_per_stack_plus_one_shared_svc -v`
Expected: FAIL — current code emits a per-stack `PCMKSVC`/`NHAXSVC` and skips stacks lacking `svc_port`.

- [ ] **Step 3: Restructure `mq_exporter_instances`** in `src/mqlab/scrape.py`. Delete the `SVC_EXPORTER_CONN` constant (line ~34). Replace the loop body so the per-stack loop emits app-only (guarded on `app_port`), then append one svc instance from the `svc:` block:

```python
def _svc_exporter_instance(topo: dict[str, Any]) -> dict[str, Any]:
    """The single shared svc exporter target (#446): one SVCQM on svc-sim, scraped
    once, labelled `commons`. Conn/name/port come from the top-level `svc:` block."""
    svc = topo.get("svc") or {}
    short = svc.get("short")
    conn = svc.get("conn")
    port = svc.get("exporter_port")
    if not short or not conn or not port:
        raise ScrapeError("topology `svc:` block must declare short, conn, exporter_port")
    qm = f"{short}QM"
    return {
        "instance": qm.lower(),
        "stack": "commons",
        "role": "svc",
        "qm": qm,
        "conn": f"{conn}({MQ_LISTENER_PORT})",
        "channel": MON_CHANNEL,
        "port": port,
    }


def mq_exporter_instances(topo: dict[str, Any]) -> list[dict[str, Any]]:
    """Per-stack APP exporters (on each stack's alloc port) + ONE shared SVCQM svc
    exporter. App emission is guarded on app_port ALONE — svc is no longer per-stack,
    so a stack without an svc port must NOT lose its app exporter (#446)."""
    out: list[dict[str, Any]] = []
    for name, cfg in (topo.get("stacks") or {}).items():
        cfg = cfg or {}
        short = cfg.get("short")
        app_port = (cfg.get("alloc") or {}).get("exporter_app_port")
        if not short or not app_port:
            continue  # reserved / not observable
        qm_app = f"{short}APP"
        out.append(
            {
                "instance": qm_app.lower(),
                "stack": name,
                "role": "app",
                "qm": qm_app,
                "conn": _app_qm_conn(topo, name, cfg),
                "channel": MON_CHANNEL,
                "port": app_port,
            }
        )
    out.append(_svc_exporter_instance(topo))
    return out
```

Delete the now-unused comment block above `SVC_EXPORTER_CONN` (lines ~32-34) referencing the per-stack svc conn.

- [ ] **Step 4: Run the scrape tests**

Run: `cd /.worktrees/issue-446-shared-svc-qm && vrg-container-run -- uv run pytest tests/test_scrape.py -v`
Expected: PASS (all, including the two new tests). Fix any residual per-stack-svc assertions in the file.

- [ ] **Step 5: Full gate + commit**

```bash
cd /.worktrees/issue-446-shared-svc-qm
vrg-container-run -- vrg-validate
vrg-git add src/mqlab/scrape.py tests/test_scrape.py
vrg-commit --type fix --scope observability --message "svc exporter: one shared SVCQM target, decouple app emission from svc port (#446)"
```

---

### Task 3: Thread the per-stack request queue + define it on `SVCQM`

Adds the `svc_req_queue` extra-var (Python, testable) and makes the their-side MQSC define each stack's own `{SHORT}.SVC.REQUEST` on the shared `SVCQM`.

**Files:**
- Modify: `src/mqlab/phases.py` (`_qm_extra_vars`)
- Modify: `src/mqlab/cli.py` (the qm extra-vars block ~L1115-1135)
- Modify: `ansible/roles/mq-inter-qm/templates/their-side.mqsc.j2`
- Test: `tests/test_cli_qm.py` (or the test that asserts `_qm_extra_vars`; locate with grep below)

**Interfaces:**
- Consumes: `QmConfig.req_queue` (Task 1).
- Produces: provision/obs plays receive `-e svc_req_queue={SHORT}.SVC.REQUEST`; `their-side.mqsc.j2` defines `QLOCAL({{ svc_req_queue }})` on `SVCQM`.

- [ ] **Step 1: Locate the extra-vars test**

Run: `cd /.worktrees/issue-446-shared-svc-qm && grep -rn "chl_to_svc\|qm_svc=\|_qm_extra_vars\|svc_req_queue" tests/`
Expected: identifies the test asserting the extra-var list (e.g. `tests/test_cli_qm.py`). Use that file in Step 2.

- [ ] **Step 2: Write the failing test** — in the test file found above, assert the new extra-var is threaded (adapt to the existing test's shape):

```python
def test_qm_extra_vars_include_per_stack_request_queue():
    from mqlab.phases import _qm_extra_vars
    from mqlab.stacks import lab_stacks

    ev = _qm_extra_vars(lab_stacks()["pcmk-ubuntu"])
    assert "svc_req_queue=PCMK.SVC.REQUEST" in ev
    assert "qm_svc=SVCQM" in ev
```

- [ ] **Step 3: Run to verify it fails**

Run: `cd /.worktrees/issue-446-shared-svc-qm && vrg-container-run -- uv run pytest <that test file>::test_qm_extra_vars_include_per_stack_request_queue -v`
Expected: FAIL — `svc_req_queue=...` not present.

- [ ] **Step 4: Thread the extra-var** in `src/mqlab/phases.py` `_qm_extra_vars` (append to the returned list):

```python
        "-e",
        f"chl_to_app={qm.chl_to_app}",
        "-e",
        f"svc_req_queue={qm.req_queue}",
    ]
```

And in `src/mqlab/cli.py` (the `ansible-playbook` args block ~L1128-1133), add alongside `chl_to_app`:

```python
                    "-e",
                    f"chl_to_app={qm.chl_to_app}",
                    "-e",
                    f"svc_req_queue={qm.req_queue}",
```

- [ ] **Step 5: Run the test to verify it passes**

Run: `cd /.worktrees/issue-446-shared-svc-qm && vrg-container-run -- uv run pytest <that test file>::test_qm_extra_vars_include_per_stack_request_queue -v`
Expected: PASS.

- [ ] **Step 6: Make the their-side MQSC define the per-stack request queue.** In `ansible/roles/mq-inter-qm/templates/their-side.mqsc.j2`, change the fixed request queue (line 6) to the per-stack queue:

```jinja
DEFINE QLOCAL({{ svc_req_queue }}) DEFPSIST(YES) REPLACE
```

(The channel pair, XMITQ-back `QLOCAL({{ our_qm }})`, SDR `{{ qmgr_name }}.{{ our_qm }}`, and RCVR `{{ our_qm }}.{{ qmgr_name }}` lines are already per-`our_qm`/`qmgr_name` and need no change — `qmgr_name` is now `SVCQM`, `our_qm` is the app QM.)

- [ ] **Step 7: Full gate + commit**

```bash
cd /.worktrees/issue-446-shared-svc-qm
vrg-container-run -- vrg-validate
vrg-git add src/mqlab/phases.py src/mqlab/cli.py ansible/roles/mq-inter-qm/templates/their-side.mqsc.j2 tests/
vrg-commit --type feat --scope svc --message "thread per-stack svc_req_queue and define {SHORT}.SVC.REQUEST on SVCQM (#446)"
```

---

### Task 4: Templated `mq-svc-responder@` unit (per-stack responder)

Give each stack an independent responder reading its own request queue, and fix the fixed-name last-writer-wins collision by construction.

**Files:**
- Modify: `ansible/roles/mq-inter-qm/templates/mq-svc-responder.service.j2` → rename to `mq-svc-responder@.service.j2`
- Modify: `ansible/roles/mq-inter-qm/tasks/main.yml` (the "responder systemd unit" + "enable + start" tasks, ~L79-88)

**Interfaces:**
- Consumes: `svc_req_queue`, `qmgr_name` (= `SVCQM`) from Task 3.
- Produces: a systemd template unit `mq-svc-responder@.service`; each stack enables `mq-svc-responder@{{ svc_req_queue }}.service` (`%i` = the request queue).

- [ ] **Step 1: Convert the unit to a template unit.** Create `ansible/roles/mq-inter-qm/templates/mq-svc-responder@.service.j2` with `%i` as the request queue (delete the old non-`@` template):

```ini
[Unit]
Description=SVC service responder for %i (client-mode localhost; QM-to-QM #147/#180)
After=mq-{{ qmgr_name }}.service
Requires=mq-{{ qmgr_name }}.service

[Service]
Type=simple
User=mqm
Group=mqm
Environment=LD_LIBRARY_PATH=/opt/mqm/lib64
ExecStart=/var/mqm/rvenv/bin/python /var/mqm/svc_responder.py --qm {{ qmgr_name }} --in-queue %i --channel SVC.SVRCONN --conn "localhost(1414)" --keyrepo /var/mqm/ssl/svc-responder/key --certlabel svc-responder
Restart=always
RestartSec=3

[Install]
WantedBy=multi-user.target
```

- [ ] **Step 2: Point the tasks at the template unit** in `ansible/roles/mq-inter-qm/tasks/main.yml`. Change the "responder systemd unit" task's `src`/`dest`:

```yaml
- name: responder systemd unit (template)
  ansible.builtin.template:
    src: mq-svc-responder@.service.j2
    dest: /etc/systemd/system/mq-svc-responder@.service
    mode: "0644"
  become: true
```

And the "enable + start" task to the per-stack instance:

```yaml
- name: enable + start the responder for this stack's request queue
  ansible.builtin.systemd:
    name: "mq-svc-responder@{{ svc_req_queue }}.service"
    enabled: true
    state: started
    daemon_reload: true
  become: true
```

- [ ] **Step 3: Validate (ansible-lint + templating are the only checks possible off-box)**

Run: `cd /.worktrees/issue-446-shared-svc-qm && vrg-container-run -- vrg-validate`
Expected: PASS. (Runtime start of `mq-svc-responder@` units is proven in Task 8's cold rebuild.)

- [ ] **Step 4: Commit**

```bash
cd /.worktrees/issue-446-shared-svc-qm
vrg-git add ansible/roles/mq-inter-qm/templates/ ansible/roles/mq-inter-qm/tasks/main.yml
vrg-commit --type feat --scope svc --message "per-stack mq-svc-responder@ instance reading {SHORT}.SVC.REQUEST (#446)"
```

---

### Task 5: App-side MQSC — point each stack's `QREMOTE` at its own request queue

Each stack's app QM keeps opening the local `SVC.REQUEST` alias; only its `RNAME` (the remote queue on `SVCQM`) becomes per-stack, and `RQMNAME`/`XMITQ` resolve to `SVCQM` (already threaded as `qm_svc`).

**Files (the four per-mechanism app-side MQSC sources):**
- Modify: `ansible/site-nativeha.yml` (inline MQSC, ~L69)
- Modify: `ansible/site-nativeha-ubuntu.yml` (inline MQSC, ~L59)
- Modify: `ansible/roles/mq-pcmk-qmgr/templates/inter-qm.mqsc.j2` (pcmk our-side)
- Modify: `ansible/roles/mq-rdqm-qmgr/...` or `rdqm-qm-create.sh` (rdqm our-side — locate in Step 1)

**Interfaces:**
- Consumes: `svc_req_queue` (Task 3), `qm_svc` (= `SVCQM`, already threaded).
- Produces: each app QM has `QREMOTE(SVC.REQUEST) RNAME({{ svc_req_queue }}) RQMNAME(SVCQM) XMITQ(SVCQM)`.

- [ ] **Step 1: Locate every app-side `QREMOTE(SVC.REQUEST)`**

Run: `cd /.worktrees/issue-446-shared-svc-qm && grep -rn "QREMOTE(SVC.REQUEST)" ansible/`
Expected: the four sources above (nativeha-rhel inline, nativeha-ubuntu inline, pcmk `inter-qm.mqsc.j2`, rdqm script/template). Note the exact variable each uses for the svc QM name (`qm_svc_name` inline; `qm_svc` in the pcmk/rdqm templates — confirm from the grep).

- [ ] **Step 2: Change the `RNAME` to the per-stack queue in each source.** For the two Native HA inline blocks (`site-nativeha.yml` ~L69, `site-nativeha-ubuntu.yml` ~L59), change:

```
DEFINE QREMOTE(SVC.REQUEST) RNAME(SVC.REQUEST) RQMNAME({{ qm_svc_name }}) XMITQ({{ qm_svc_name }}) REPLACE
```

to:

```
DEFINE QREMOTE(SVC.REQUEST) RNAME({{ svc_req_queue }}) RQMNAME({{ qm_svc_name }}) XMITQ({{ qm_svc_name }}) REPLACE
```

For the pcmk template (`inter-qm.mqsc.j2`) and the rdqm source, apply the identical transformation using whatever svc-name variable that file uses (confirmed in Step 1): only `RNAME(SVC.REQUEST)` → `RNAME({{ svc_req_queue }})`. Leave the local `QREMOTE(SVC.REQUEST)` alias name unchanged — the app requester opens `SVC.REQUEST` and must keep finding it.

- [ ] **Step 3: Confirm the rdqm path threads `svc_req_queue`.** If the rdqm our-side is `rdqm-qm-create.sh` invoked with positional args (not the `-e` extra-vars), pass `svc_req_queue` through the same mechanism the script already uses for `qm_svc`/`svc_conn` (trace from `ansible/site-rdqm.yml`). If it is a template consuming play vars, no extra wiring is needed. Make the request queue reach it the same way the svc QM name already does.

- [ ] **Step 4: Validate**

Run: `cd /.worktrees/issue-446-shared-svc-qm && vrg-container-run -- vrg-validate`
Expected: PASS.

- [ ] **Step 5: Commit**

```bash
cd /.worktrees/issue-446-shared-svc-qm
vrg-git add ansible/
vrg-commit --type feat --scope svc --message "app-side QREMOTE RNAME points at per-stack {SHORT}.SVC.REQUEST on SVCQM (#446)"
```

---

### Task 6: Messaging board — per-stack `SVC.REQUEST` depth tile

Restore per-stack round-trip observability: the svc-depth tile must query the stack's own `{SHORT}.SVC.REQUEST`, not a shared `SVC.REQUEST`.

**Files:**
- Modify: `src/mqlab/messagingboard.py` (the `_flow_strip` svc-depth tile + `render_messaging_board` call site ~L329)
- Test: `tests/test_messagingboard.py`

**Interfaces:**
- Consumes: `QmConfig.req_queue` (Task 1).
- Produces: the messaging board's svc-depth `expr` is `ibmmq_queue_depth{qmgr="SVCQM",queue="{SHORT}.SVC.REQUEST"}`.

- [ ] **Step 1: Write the failing test** — add to `tests/test_messagingboard.py` (adapt to the file's existing board-introspection helpers):

```python
def test_svc_request_depth_tile_uses_per_stack_queue():
    from mqlab.messagingboard import build_messaging_board

    board = build_messaging_board("pcmk-ubuntu", "PCMKAPP", "SVCQM", req_queue="PCMK.SVC.REQUEST")
    exprs = _all_target_exprs(board)  # existing helper; else walk panels->targets->expr
    assert any('queue="PCMK.SVC.REQUEST"' in e and 'qmgr="SVCQM"' in e for e in exprs)
    assert not any('queue="SVC.REQUEST"' in e and 'PCMK' not in e for e in exprs)
```

- [ ] **Step 2: Run to verify it fails**

Run: `cd /.worktrees/issue-446-shared-svc-qm && vrg-container-run -- uv run pytest tests/test_messagingboard.py::test_svc_request_depth_tile_uses_per_stack_queue -v`
Expected: FAIL — the tile currently uses the constant `_SVC_REQUEST = "SVC.REQUEST"`.

- [ ] **Step 3: Thread the request queue into the board.** In `src/mqlab/messagingboard.py`, give `build_messaging_board` and `_flow_strip` a `req_queue` parameter and use it for the svc-depth tile in `_flow_strip` (replace the `_SVC_REQUEST` use on line ~124):

```python
        ("svc-sim: " + req_queue, _queue_depth_expr(svc_qm, req_queue), None, None),
```

Signature changes:

```python
def _flow_strip(ds_uid: str, app_qm: str, svc_qm: str, req_queue: str, y: int) -> list[dict[str, Any]]:
    ...
def build_messaging_board(
    stack_name: str, app_qm: str, svc_qm: str, req_queue: str,
    ds_uid: str = "prometheus", loki_uid: str = "loki",
) -> dict[str, Any]:
    ...
        *_flow_strip(ds_uid, app_qm, svc_qm, req_queue, y=7),
```

At the render call site (~L329) pass `stack.qm.req_queue`:

```python
        board = build_messaging_board(stack.name, stack.qm.qm_app, stack.qm.qm_svc, stack.qm.req_queue)
```

- [ ] **Step 4: Run the board tests**

Run: `cd /.worktrees/issue-446-shared-svc-qm && vrg-container-run -- uv run pytest tests/test_messagingboard.py -v`
Expected: PASS. Update any existing test that constructed `build_messaging_board(...)` without `req_queue`.

- [ ] **Step 5: Full gate + commit**

```bash
cd /.worktrees/issue-446-shared-svc-qm
vrg-container-run -- vrg-validate
vrg-git add src/mqlab/messagingboard.py tests/test_messagingboard.py
vrg-commit --type feat --scope cockpit --message "messaging board svc-depth tile keys per-stack {SHORT}.SVC.REQUEST (#446)"
```

---

### Task 7: Certs collapse to one `SVCQM`; retire the legacy `dashboard.py` PCMKSVC

`cli.py`'s cert dedup already collapses svc CNs — with Task 1 it yields one `SVCQM` CN automatically; lock it with a test. Retire the stale `PCMKSVC` clusterboard reference.

**Files:**
- Modify: `src/mqlab/dashboard.py` (~L126-128 legacy `PCMKSVC`)
- Test: `tests/test_cli_pki.py` (cert entities), `tests/test_dashboard.py`

**Interfaces:**
- Consumes: `QmConfig.qm_svc == "SVCQM"` (Task 1).
- Produces: exactly one `svc-org` CN (`SVCQM`) in the PKI entity list; no `PCMKSVC` literal in `dashboard.py`.

- [ ] **Step 1: Write the failing cert test** — add to `tests/test_cli_pki.py` (adapt to how it invokes `_render_pki_entities`):

```python
def test_pki_has_exactly_one_svc_org_cn_svcqm():
    from mqlab.cli import _render_pki_entities
    import json

    entities = json.loads(_render_pki_entities().read_text())
    svc_cns = [e["cn"] for e in entities if e.get("org") == "svc-org"]
    assert svc_cns == ["SVCQM"]
```

- [ ] **Step 2: Run to verify it fails** (before the code change it lists four `{short}SVC` CNs)

Run: `cd /.worktrees/issue-446-shared-svc-qm && vrg-container-run -- uv run pytest tests/test_cli_pki.py::test_pki_has_exactly_one_svc_org_cn_svcqm -v`
Expected: On a tree with Task 1 applied this may already PASS (the dedup + shared name collapse it). If so, keep the test as a regression lock and note it in the commit. If it FAILS listing `PCMKSVC`/`RDQMSVC`/…, Task 1 is not fully applied — reconcile before proceeding.

- [ ] **Step 3: Retire the legacy `PCMKSVC` in `src/mqlab/dashboard.py`** (~L126-128). Locate it:

Run: `cd /.worktrees/issue-446-shared-svc-qm && grep -n "PCMKSVC\|PCMKAPP.PCMKSVC\|PCMKSVC.PCMKAPP" src/mqlab/dashboard.py`

Replace the hardcoded `"PCMKSVC"` and its channel literals (`"PCMKSVC.PCMKAPP"`, `"PCMKAPP.PCMKSVC"`) with the shared names driven off the stack the board is built for — `"SVCQM"`, `f"{app_qm}.SVCQM"`, `f"SVCQM.{app_qm}"`. If `dashboard.py`'s clusterboard is already superseded by `messagingboard.py` (per the extraction roadmap), instead delete the dead `PCMKSVC` sample rows and update `tests/test_dashboard.py` to match. Choose deletion only if no rendered board consumes them (confirm with grep of the call sites).

- [ ] **Step 4: Run the dashboard + pki tests**

Run: `cd /.worktrees/issue-446-shared-svc-qm && vrg-container-run -- uv run pytest tests/test_dashboard.py tests/test_cli_pki.py -v`
Expected: PASS.

- [ ] **Step 5: Full gate + commit**

```bash
cd /.worktrees/issue-446-shared-svc-qm
vrg-container-run -- vrg-validate
vrg-git add src/mqlab/dashboard.py tests/test_dashboard.py tests/test_cli_pki.py
vrg-commit --type fix --scope cockpit --message "one SVCQM cert CN; retire legacy PCMKSVC dashboard reference (#446)"
```

---

### Task 8: Cold-rebuild acceptance (live-lab side)

Not code — the acceptance gate. Runs on the macOS/live-lab side per #446. Lint-green is not "done" for a provisioning-touching change (repo cold-rebuild acceptance gate).

**Files:** none (operational validation).

- [ ] **Step 1: Full VM cold rebuild.** `vrg-vm rebuild logical-minds-foundry/mq-resiliency-lab-for-linux --identity vergil-user`, then bring up **≥2 stacks** (e.g. `nativeha-rhel` + `pcmk-ubuntu`) through the normal provision flow.

- [ ] **Step 2: Assert one shared QM.** On `svc-sim`: `dspmq` shows exactly one QM, `SVCQM`, and it owns `:1414`. No `{short}SVC` QMs exist.

- [ ] **Step 3: Assert per-stack independence.** For each running stack: its `mq-svc-responder@{SHORT}.SVC.REQUEST` unit is `active`, `{SHORT}.SVC.REQUEST` exists on `SVCQM`, and an app request round-trips to `APP.REPLY` on that stack's app QM. Stopping one stack's responder must NOT break the other stack's round-trip.

- [ ] **Step 4: Assert the exporter is healthy.** `mq-exporter-svcqm.service` (the single svc exporter) is `active` with no `MQRC 2058`; the `ibmmq` scrape target for `SVCQM` is `up` in Prometheus.

- [ ] **Step 5: Assert the boards.** Each running stack's messaging board shows its SVC tiles live, and its `SVC.REQUEST`-depth tile reflects **that stack's** `{SHORT}.SVC.REQUEST` (not a summed shared queue).

- [ ] **Step 6: Close out.** Record the cold-rebuild result on the PR; only then is the change accepted.

---

## Self-Review

**Spec coverage** (design §1–§8 → task):
- §1 topology `svc:` block + remove per-stack svc fields → Task 1.
- §2 `stacks.py` svc identity + `req_queue` → Task 1.
- §3 provisioning: create SVCQM once (existing `mq-qmgr`, unchanged), per-stack request queue → Task 3; per-stack responder → Task 4; app-side MQSC → Task 5; DR/CRR inherits unchanged (no task — asserted in Task 8 Step 3).
- §4 exporter one SVCQM instance + decoupled emission → Task 2.
- §5 boards: svc-depth per-stack queue → Task 6; retire `dashboard.py` PCMKSVC → Task 7.
- §6 certs collapse to one SVCQM → Task 7.
- §7 tests: #446 regression + skip-guard + per-stack queue + one-cert → Tasks 2, 6, 7 (and updated fixtures in Task 1).
- §8 validation + cold rebuild → every task's `vrg-validate` + Task 8.

**Placeholder scan:** no "TBD/TODO"; the two "locate in Step 1" steps (rdqm our-side path, extra-vars test file) are explicit grep discovery, not deferred work, because the exact file differs by mechanism and must be confirmed on the tree — each includes the concrete transformation to apply once located.

**Type consistency:** `req_queue`/`svc_req_queue` = `{SHORT}.SVC.REQUEST` used identically in Tasks 1/3/4/5/6; `SVCQM` (= `{svc.short}QM`) used identically in Tasks 1/2/3/6/7; `build_messaging_board(..., req_queue)` signature defined in Task 6 matches its call site in the same task; exporter instance dict keys (`qm/role/stack/conn/port/channel/instance`) match the existing `render_mq_scrape_targets`/`render_mq_exporters` consumers.

**Known discovery points (honest, not placeholders):** Task 3 Step 1 and Task 5 Step 1/3 require a grep to pin the exact rdqm our-side file and the extra-vars test module, because those differ by mechanism and the plan must not guess a path it hasn't verified. The transformation to apply is fully specified in each case.
