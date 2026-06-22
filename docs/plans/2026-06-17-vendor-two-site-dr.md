# Two-site Vendor (SVC) DR Model — Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Model the SVC/service side as a two-site DR pair and add a deliberate, operator-run failover utility so we can validate that our infrastructure survives both our own DR and the vendor's DR.

**Architecture:** A new combined lab setup unions our full 2-site DR topology with a two-site vendor pair (same QM name, cold standby). Vendor DR is **not** automatic: because the vendor's DR site is a separate same-name QM instance with stale channel sequence numbers, a `RESET CHANNEL` is always required, so failover is an integration-tested Python utility over `pymqrest` (the admin REST wrapper) that the operator runs when the vendor declares DR. A 2×2 validation matrix proves zero message loss.

**Tech Stack:** Python 3.12, Typer (`mqlab` CLI), `pymqrest` (MQ administrative REST), Ansible (provisioning), Vagrant/libvirt (lab VMs), pytest (`uv run pytest`).

**Spec:** `docs/specs/2026-06-17-vendor-two-site-dr-design.md`

## Global Constraints

- **Single-active invariant:** at most one vendor site's listener is reachable at any instant. The sim enforces stop-before-start.
- **`RESET CHANNEL` is always required** on a vendor failover (stale sequence numbers on the separate same-name DR instance). Never skip it.
- **Fail loud, never mask:** no swallowed exceptions, no silent fallbacks; verification failures exit non-zero with the actual state.
- **Admin REST only + network probes** for the operational utility — it touches *our* QM via `pymqrest` and TCP-probes vendor endpoints; it never assumes vendor admin access. (The *sim* may drive vendor QMs because the lab owns them.)
- **Creds:** `MQWEB_ADMIN_USER` / `MQWEB_ADMIN_PASSWORD`, runtime-injected, never committed.
- **Vendor address block:** `10.60.0.40–.49`. `svc-sim-a = 10.60.0.40`, `svc-sim-b = 10.60.0.41`.
- **Validation:** `vrg-container-run -- vrg-validate` is the only gate. Watch the known gotchas: `StrEnum`/UP042, ruff magic-trailing-comma, **100% branch coverage**, `uv run pytest`, never mask exit codes.
- **Acceptance:** lab bring-up change → accepted only after a full cold rebuild of the combined setup proves bring-up one-pass and the 2×2 matrix passes at zero loss. Pacemaker arm first, then RDQM.
- **Commits:** `vrg-commit --type <type> --scope vendor-dr --message <msg>` (this branch: `feature/237-vendor-dr-model`).

---

## File Structure

| File | Responsibility |
|---|---|
| `docs/reports/2026-06-17-vendor-dr-seqreset-spike.md` | **Create.** Spike findings: RESET-CHANNEL behavior + enumerated manual-step list (gates Task 3). |
| `lab/topology.yaml` | **Modify.** Renumber `svc-sim` → `svc-sim-a` (.40); add `svc-sim-b` (.41); `svc` group; new `distributed-pcmk-dr` setup. |
| `ansible/site-distributed-dr.yml` | **Create.** Combined provisioning: compose DR (`site-pcmk-dr.yml`) + distributed (`site-distributed-shared.yml`); provision `QMSVC` identically on both vendor sites; stop site-B listener. |
| `ansible/roles/mq-inter-qm/...` | **Modify.** Parameterize the vendor target / both-site provisioning; request channel CONNAME default = vendor primary. |
| `src/mqlab/mqadmin.py` | **Create.** Thin, typed `pymqrest` adapter implementing the `ChannelAdmin` protocol used by the utility (isolates REST calls for testability). |
| `src/mqlab/vendor_dr.py` | **Create.** Pure failover/sim/status logic against the `ChannelAdmin` protocol (no I/O); the testable core. |
| `src/mqlab/cli.py` | **Modify.** Add the `vendor` Typer sub-app (`failover`, `sim-dr`, `status`). |
| `tests/test_vendor_dr.py` | **Create.** Unit tests for the failover/sim/status logic with a fake `ChannelAdmin`. |
| `tests/test_cli_vendor.py` | **Create.** Unit tests for the `vendor` CLI wiring. |
| `tests/integration/test_vendor_2x2.py` | **Create.** Lab-gated 2×2 matrix integration tests (run against the cold-rebuilt combined setup). |

---

## Task 1: Sequence-reset spike (gates the utility design)

**Files:**
- Create: `docs/reports/2026-06-17-vendor-dr-seqreset-spike.md`

**Interfaces:**
- Produces: the authoritative **manual-step list** the utility encapsulates (minimum: stop → repoint → reset → resolve-in-doubt → start → verify), and confirmation that `RESET CHANNEL` on our sender restores flow to a fresh same-name DR instance. Task 3 consumes this list verbatim.

This task is investigation + lab measurement, not TDD. It must complete before Task 3.

- [ ] **Step 1: Verify RESET-CHANNEL semantics against IBM 9.4 docs**

Use the IBM-docs canonical fetch path (browser UA → `oldUrl` → `/docs/api/v1/content/<oldUrl>`; pin to 9.4; cache under `build/refs/ibm-docs/`). Confirm: (a) sender/receiver sequence-number persistence and resync on `START CHANNEL`; (b) the `AMQ9526` mismatch condition; (c) that `RESET CHANNEL` on the sender (we cannot reset the vendor's receiver) is sufficient to recover, and under what conditions vendor-side coordination is still required. Record exact doc URLs.

- [ ] **Step 2: Reproduce empirically on a scratch lab**

On a running lab, point `QMPCMK.QMSVC` at a *fresh* same-name QM instance and start the channel; confirm it stalls on a sequence error. Then run `RESET CHANNEL(QMPCMK.QMSVC)` + `START CHANNEL` and confirm flow resumes with no message loss (persistent messages on the xmitq drain). Capture the `runmqsc`/REST transcript.

- [ ] **Step 3: Enumerate the manual-step list and write the report**

Document the confirmed step sequence (including in-doubt handling via `RESOLVE CHANNEL`), the data-vs-judgment split (cite doc URLs), and any vendor-coordination edge cases. This list is the contract for Task 3.

- [ ] **Step 4: Commit**

```bash
vrg-commit --type docs --scope vendor-dr --message "sequence-reset spike: confirm RESET CHANNEL recovery + step list (#237)"
```

---

## Task 2: Topology — vendor pair + combined setup

**Files:**
- Modify: `lab/topology.yaml`

**Interfaces:**
- Produces: nodes `svc-sim-a` (`net-ext: 10.60.0.40`) and `svc-sim-b` (`net-ext: 10.60.0.41`); group `svc: [svc-sim-a, svc-sim-b]`; setup `distributed-pcmk-dr` with groups `[san_a, san_b, pcmk_a, pcmk_b, svc, app]` and `provision: ansible/site-distributed-dr.yml`. Consumed by Tasks 3–6 and the integration tests.

- [ ] **Step 1: Write the failing topology-integrity test**

Add to `tests/test_topology_integrity.py`:

```python
def test_vendor_pair_and_combined_setup():
    topo = load_topology()  # existing helper in this test module
    nodes = topo["nodes"]
    assert nodes["svc-sim-a"]["nics"]["net-ext"] == "10.60.0.40"
    assert nodes["svc-sim-b"]["nics"]["net-ext"] == "10.60.0.41"
    assert "svc-sim" not in nodes  # renamed, not left behind
    assert topo["groups"]["svc"] == ["svc-sim-a", "svc-sim-b"]
    setup = topo["setups"]["distributed-pcmk-dr"]
    assert setup["groups"] == ["san_a", "san_b", "pcmk_a", "pcmk_b", "svc", "app"]
    assert setup["provision"] == "ansible/site-distributed-dr.yml"
```

- [ ] **Step 2: Run it to confirm it fails**

Run: `uv run pytest tests/test_topology_integrity.py::test_vendor_pair_and_combined_setup -v`
Expected: FAIL (KeyError on `svc-sim-a`).

- [ ] **Step 3: Edit `lab/topology.yaml`**

Rename the `svc-sim` node block to `svc-sim-a` and set `net-ext: 10.60.0.40`; add a `svc-sim-b` mirror at `10.60.0.41` (same `net-mgmt` pattern, e.g. `10.50.0.55`/`.56` — pick free mgmt IPs and assert them in the test too if you pin them). Update `svc: [svc-sim-a, svc-sim-b]`. Add the `distributed-pcmk-dr` setup:

```yaml
  distributed-pcmk-dr:
    description: Distributed MQ + full DR (Pacemaker) — our 2-site DR ⇄ two-site vendor DR (#237)
    arm: pcmk-ubuntu
    groups: [san_a, san_b, pcmk_a, pcmk_b, svc, app]
    provision: ansible/site-distributed-dr.yml
    secrets: [pcmk_hacluster_password, mqweb_admin_password]
    qm: { name: QMPCMK, vip: 10.10.1.200, vip_ext: 10.60.0.10, svc_conn: 10.60.0.40 }
```

- [ ] **Step 4: Reference sweep for the renumber**

Update every `10.60.0.50` / `svc-sim` reference: `svc_conn` in `distributed-pcmk-ubuntu` and `distributed-rdqm-rhel` (→ `10.60.0.40`), the `topology.yaml` `svc-sim` comments (#147/#182), the their-side channel CONNAME source, and `docs/reference/lab-bootstrap.md`. Grep to confirm none remain:

Run: `grep -rn "10.60.0.50\|svc-sim\b" lab/ ansible/ docs/reference/`
Expected: only intentional historical/doc mentions remain.

- [ ] **Step 5: Run the test + validate**

Run: `uv run pytest tests/test_topology_integrity.py -v` → PASS.
Run: `vrg-container-run -- vrg-validate` → green.

- [ ] **Step 6: Commit**

```bash
vrg-commit --type feat --scope vendor-dr --message "topology: two-site vendor pair (.40/.41) + distributed-pcmk-dr setup (#237)"
```

---

## Task 3: `ChannelAdmin` protocol + `pymqrest` adapter

**Files:**
- Create: `src/mqlab/mqadmin.py`
- Test: `tests/test_vendor_dr.py` (fake lives here, shared by Tasks 3–5)

**Interfaces:**
- Produces: a `ChannelAdmin` Protocol and a `PymqrestChannelAdmin` implementation. The utility (Task 4) depends only on the protocol, so it is unit-testable with a fake.

```python
# Protocol (the only surface the utility logic sees)
class ChannelAdmin(Protocol):
    def chstatus(self, channel: str) -> ChannelStatus: ...      # RUNNING/RETRYING/STOPPED/INACTIVE + seqno
    def stop_channel(self, channel: str) -> None: ...           # quiesce
    def set_conname(self, channel: str, conname: str) -> None: ...  # ensure_channel CONNAME=
    def reset_channel(self, channel: str) -> None: ...
    def resolve_channel(self, channel: str) -> None: ...        # no-op if not in-doubt
    def start_channel(self, channel: str) -> None: ...          # sync
    def ping_channel(self, channel: str) -> bool: ...
    def xmitq_depth(self, queue: str) -> int: ...
    def start_listener(self, name: str) -> None: ...            # sim only
    def stop_listener(self, name: str) -> None: ...             # sim only
    def listener_running(self, name: str) -> bool: ...          # sim/status
```

- [ ] **Step 1: Write the failing test for the adapter's status mapping**

In `tests/test_vendor_dr.py`:

```python
from mqlab.mqadmin import ChannelStatus, parse_chstatus

def test_parse_chstatus_running():
    raw = {"status": "RUNNING", "currentSequenceNumber": 42}
    st = parse_chstatus(raw)
    assert st.running is True
    assert st.seqno == 42
```

- [ ] **Step 2: Run it — expect failure**

Run: `uv run pytest tests/test_vendor_dr.py::test_parse_chstatus_running -v`
Expected: FAIL (no `mqadmin`).

- [ ] **Step 3: Implement `mqadmin.py`**

Define `ChannelStatus` (a frozen dataclass: `running: bool`, `state: str`, `seqno: int | None`), `parse_chstatus(raw) -> ChannelStatus`, the `ChannelAdmin` Protocol above, and `PymqrestChannelAdmin` wrapping `MQRESTSession` (mirror `src/mqlab/apply.py` for session construction: `rest_base_url=f"{base}/ibmmq/rest/v2"`, `BasicAuth(MQWEB_ADMIN_USER, MQWEB_ADMIN_PASSWORD)`, `verify_tls=False`). Map protocol methods to `display_chstatus` / `stop_channel_sync` / `ensure_channel` / `reset_channel` / `resolve_channel` / `start_channel_sync` / `ping_channel` / `display_qstatus` / `start_listener_sync` / `stop_listener_sync` / `display_lsstatus`. Confirm each `pymqrest` signature against the installed package while implementing.

- [ ] **Step 4: Run the test — expect pass**

Run: `uv run pytest tests/test_vendor_dr.py::test_parse_chstatus_running -v` → PASS.

- [ ] **Step 5: Commit**

```bash
vrg-commit --type feat --scope vendor-dr --message "mqadmin: ChannelAdmin protocol + pymqrest adapter (#237)"
```

---

## Task 4: Failover utility (the deliverable)

**Files:**
- Create: `src/mqlab/vendor_dr.py`
- Test: `tests/test_vendor_dr.py`

**Interfaces:**
- Consumes: `ChannelAdmin` (Task 3); the spike step list (Task 1).
- Produces: `failover(admin: ChannelAdmin, *, channel: str, xmitq: str, current_conn: str, target_conn: str, current_reachable: Callable[[], bool], target_reachable: Callable[[], bool], force: bool=False) -> FailoverResult`. `FailoverResult` is a frozen dataclass `(switched: bool, steps: list[str], verified: bool)`. Raises `VendorDRError` (fail-loud) on guard violation or verify failure. Consumed by the CLI (Task 6).

- [ ] **Step 1: Write the failing happy-path test**

```python
from mqlab.vendor_dr import failover, VendorDRError

def test_failover_runs_ordered_steps_and_resets(fake_admin):
    # fake_admin records calls; ping/chstatus return RUNNING after start
    res = failover(
        fake_admin, channel="QMPCMK.QMSVC", xmitq="QMSVC",
        current_conn="10.60.0.40(1414)", target_conn="10.60.0.41(1414)",
        current_reachable=lambda: False,  # primary down
        target_reachable=lambda: True,    # DR up
    )
    assert res.switched and res.verified
    assert fake_admin.calls == [
        ("stop_channel", "QMPCMK.QMSVC"),
        ("set_conname", "QMPCMK.QMSVC", "10.60.0.41(1414)"),
        ("reset_channel", "QMPCMK.QMSVC"),       # MANDATORY
        ("resolve_channel", "QMPCMK.QMSVC"),
        ("start_channel", "QMPCMK.QMSVC"),
    ]
```

- [ ] **Step 2: Write the failing guard tests**

```python
def test_failover_refuses_when_primary_still_reachable(fake_admin):
    with pytest.raises(VendorDRError, match="primary still reachable"):
        failover(fake_admin, channel="C", xmitq="Q",
                 current_conn="a", target_conn="b",
                 current_reachable=lambda: True, target_reachable=lambda: True)

def test_failover_refuses_when_target_unreachable(fake_admin):
    with pytest.raises(VendorDRError, match="target .* not reachable"):
        failover(fake_admin, channel="C", xmitq="Q",
                 current_conn="a", target_conn="b",
                 current_reachable=lambda: False, target_reachable=lambda: False)

def test_force_overrides_guards(fake_admin):
    res = failover(fake_admin, channel="C", xmitq="Q",
                   current_conn="a", target_conn="b",
                   current_reachable=lambda: True, target_reachable=lambda: True,
                   force=True)
    assert res.switched

def test_idempotent_noop_when_already_on_target(fake_admin):
    fake_admin.set_running("C", conname="b")  # already there + RUNNING
    res = failover(fake_admin, channel="C", xmitq="Q",
                   current_conn="a", target_conn="b",
                   current_reachable=lambda: False, target_reachable=lambda: True)
    assert res.switched is False and fake_admin.calls == []

def test_verify_failure_raises_fail_loud(fake_admin):
    fake_admin.make_ping_fail("C")
    with pytest.raises(VendorDRError, match="verify"):
        failover(fake_admin, channel="C", xmitq="Q",
                 current_conn="a", target_conn="b",
                 current_reachable=lambda: False, target_reachable=lambda: True)
```

- [ ] **Step 3: Run them — expect failures**

Run: `uv run pytest tests/test_vendor_dr.py -k failover -v`
Expected: FAIL (no `vendor_dr`).

- [ ] **Step 4: Implement `failover` in `vendor_dr.py`**

Pre-flight (c1 idempotency via `chstatus`; c2 `current_reachable()` must be False; c3 `target_reachable()` must be True; bypass c2/c3 if `force`). Switch in exact order: `stop_channel` → `set_conname(target)` → `reset_channel` → `resolve_channel` → `start_channel`. Verify: `chstatus().running` and `ping_channel()` and `xmitq_depth` observed (record). Raise `VendorDRError` with a specific message on any guard/verify failure; never mask. Return `FailoverResult` recording the steps taken.

- [ ] **Step 5: Run all failover tests — expect pass**

Run: `uv run pytest tests/test_vendor_dr.py -k failover -v` → PASS.

- [ ] **Step 6: Commit**

```bash
vrg-commit --type feat --scope vendor-dr --message "vendor_dr: guarded failover utility with mandatory RESET CHANNEL (#237)"
```

---

## Task 5: Vendor-side sim + status logic

**Files:**
- Modify: `src/mqlab/vendor_dr.py`
- Test: `tests/test_vendor_dr.py`

**Interfaces:**
- Consumes: `ChannelAdmin` (Task 3).
- Produces: `sim_dr(admin, *, current_listener: str, target_listener: str) -> None` (stop-before-start, fail-loud) and `vendor_status(admin, *, site_listeners: dict[str,str], channel: str) -> VendorStatus` where `VendorStatus(active_sites: list[str], channel_conn: str, invariant_ok: bool)`. Consumed by the CLI (Task 6).

- [ ] **Step 1: Write the failing sim test (stop-before-start)**

```python
from mqlab.vendor_dr import sim_dr, vendor_status

def test_sim_dr_stops_current_before_starting_target(fake_admin):
    fake_admin.set_listener("LSR.A", running=True)
    fake_admin.set_listener("LSR.B", running=False)
    sim_dr(fake_admin, current_listener="LSR.A", target_listener="LSR.B")
    assert fake_admin.calls == [
        ("stop_listener", "LSR.A"),
        ("start_listener", "LSR.B"),
    ]

def test_vendor_status_flags_invariant_violation(fake_admin):
    fake_admin.set_listener("LSR.A", running=True)
    fake_admin.set_listener("LSR.B", running=True)  # both up == violation
    st = vendor_status(fake_admin, site_listeners={"a": "LSR.A", "b": "LSR.B"}, channel="C")
    assert st.invariant_ok is False
    assert sorted(st.active_sites) == ["a", "b"]
```

- [ ] **Step 2: Run — expect failure**

Run: `uv run pytest tests/test_vendor_dr.py -k "sim_dr or vendor_status" -v` → FAIL.

- [ ] **Step 3: Implement `sim_dr` + `vendor_status`**

`sim_dr`: `stop_listener(current)` → confirm `not listener_running(current)` → `start_listener(target)` → confirm `listener_running(target)`; raise `VendorDRError` if either confirmation fails. `vendor_status`: read `listener_running` for each site, set `active_sites`, `invariant_ok = len(active_sites) <= 1`, `channel_conn` from `chstatus`/config.

- [ ] **Step 4: Run — expect pass**

Run: `uv run pytest tests/test_vendor_dr.py -k "sim_dr or vendor_status" -v` → PASS.

- [ ] **Step 5: Commit**

```bash
vrg-commit --type feat --scope vendor-dr --message "vendor_dr: cold-standby sim + invariant status (#237)"
```

---

## Task 6: `mqlab vendor` CLI

**Files:**
- Modify: `src/mqlab/cli.py`
- Test: `tests/test_cli_vendor.py`

**Interfaces:**
- Consumes: `failover`, `sim_dr`, `vendor_status` (Tasks 4–5); `PymqrestChannelAdmin` (Task 3); the setup's QM/vendor addressing (Task 2 topology).
- Produces: `mqlab vendor failover --to {a|b} [--force]`, `mqlab vendor sim-dr --to {a|b}`, `mqlab vendor status` — three Typer commands on a `vendor` sub-app.

- [ ] **Step 1: Write the failing CLI test**

```python
from typer.testing import CliRunner
from mqlab.cli import app

def test_vendor_failover_invokes_utility(monkeypatch):
    called = {}
    monkeypatch.setattr("mqlab.cli.failover",
                        lambda *a, **k: called.setdefault("ok", True))
    # patch admin construction + reachability probes to avoid I/O
    ...
    result = CliRunner().invoke(app, ["vendor", "failover", "--to", "b",
                                      "distributed-pcmk-dr"])
    assert result.exit_code == 0 and called["ok"]
```

- [ ] **Step 2: Run — expect failure**

Run: `uv run pytest tests/test_cli_vendor.py -v` → FAIL (no `vendor` command).

- [ ] **Step 3: Implement the `vendor` sub-app**

Follow the existing `qm_app` pattern in `cli.py`: build a `vendor_app = typer.Typer()`, register on `app`. Each command resolves the setup (reuse `_setup_qm_or_exit`-style validation), constructs `PymqrestChannelAdmin` for the relevant QM (our QM for `failover`/`status`; the vendor QMs for `sim-dr`), supplies TCP-probe callables for `current_reachable`/`target_reachable`, and calls the matching `vendor_dr` function. Map `VendorDRError` → `typer.Exit(code=1)` with the message echoed to stderr (fail-loud).

- [ ] **Step 4: Run — expect pass**

Run: `uv run pytest tests/test_cli_vendor.py -v` → PASS.

- [ ] **Step 5: Full validate (coverage gate)**

Run: `vrg-container-run -- vrg-validate`
Expected: green, including 100% branch coverage on `vendor_dr.py` / `mqadmin.py` / the new CLI (add tests for any uncovered branch).

- [ ] **Step 6: Commit**

```bash
vrg-commit --type feat --scope vendor-dr --message "cli: mqlab vendor failover/sim-dr/status (#237)"
```

---

## Task 7: Combined provisioning playbook

**Files:**
- Create: `ansible/site-distributed-dr.yml`
- Modify: `ansible/roles/mq-inter-qm/...` (parameterize both-vendor-site provisioning + request CONNAME default)

**Interfaces:**
- Consumes: the `distributed-pcmk-dr` setup groups (Task 2).
- Produces: a provisioned combined lab — our 2-site DR (`QMPCMK`) + identical `QMSVC` on `svc-sim-a` and `svc-sim-b`, site-B listener stopped, request channel `QMPCMK.QMSVC` CONNAME defaulting to the vendor primary (`10.60.0.40`).

- [ ] **Step 1: Write `site-distributed-dr.yml`**

Compose the existing plays: `import_playbook: site-pcmk-dr.yml` (our DR substrate) then `import_playbook: site-distributed-shared.yml` (SVC/app/channels), extended so the SVC plays run against the `svc` group (both sites) and explicitly **stop the site-B listener** at the end (cold standby). Inject `mqweb_admin_password` where the inter-QM/mqweb roles require it.

- [ ] **Step 2: Ansible syntax check**

Run: `vrg-container-run -- ansible-playbook ansible/site-distributed-dr.yml --syntax-check`
Expected: no errors.

- [ ] **Step 3: Add an assertion play / molecule-style check (if present) or a topology-derived inventory test**

Confirm the rendered inventory includes both vendor hosts and the request CONNAME is the vendor primary. Reuse the existing inventory-render test pattern.

- [ ] **Step 4: Validate + commit**

Run: `vrg-container-run -- vrg-validate` → green.

```bash
vrg-commit --type feat --scope vendor-dr --message "ansible: combined distributed-pcmk-dr provisioning + cold-standby vendor B (#237)"
```

---

## Task 8: 2×2 integration validation (lab-gated)

**Files:**
- Create: `tests/integration/test_vendor_2x2.py`

**Interfaces:**
- Consumes: a cold-rebuilt `distributed-pcmk-dr` lab; `mqlab vendor *` and `mqlab dr *` commands; `app_requester` traffic.
- Produces: the acceptance evidence (zero-loss across the 2×2 + invariant held).

These tests are **human-operated / lab-gated** (marked `@pytest.mark.integration`, skipped in `vrg-validate`'s unit run). They are the real proof per the acceptance gate.

- [ ] **Step 1: Write the matrix test skeleton**

One test per transition (① baseline, ①→② our DR, ①→③ vendor DR, →④ both DR, failbacks). Each: start `app_requester` traffic, fire the trigger (`mqlab dr failover` and/or `mqlab vendor sim-dr` + `mqlab vendor failover --to b`), then assert every request received its reply (zero loss), `mqlab vendor status` shows `invariant_ok`, and the xmitq drained to 0.

- [ ] **Step 2: Cold rebuild + run the matrix (human-operated)**

Human runs: rebuild the VM, `mqlab` bring-up of `distributed-pcmk-dr`, then `uv run pytest tests/integration/test_vendor_2x2.py -m integration -v`. Capture wall-clock and results; record any sequence-reset edge surfaced (cross-check against the Task 1 spike).

- [ ] **Step 3: Record acceptance + commit**

Append results to the spike/acceptance report; commit the tests.

```bash
vrg-commit --type test --scope vendor-dr --message "integration: 2x2 vendor/our DR zero-loss matrix (#237)"
```

- [ ] **Step 4: Repeat on the RDQM arm**

Add the analogous combined RDQM setup + run (per the spec's "Pacemaker first, then RDQM"). Fold RDQM-specific addressing (per-node CONNAME list to us) into the inter-QM parameters.

---

## Self-Review

**Spec coverage:**
- §3 reset-always / §5.2 / §10 load-bearing → Task 1 (spike), Task 4 (mandatory `reset_channel`).
- §4 topology + renumber + dedicated block → Task 2.
- §5.1 addressing (reply list / request single utility-managed) → Tasks 2, 7.
- §5.3 utility algorithm (guard → stop → repoint → reset → resolve → start → verify) → Task 4.
- §5.4 2×2 → Task 8.
- §6 alternatives → recorded in spec (no task; rejected).
- §7 sim + status + REST-only operational boundary → Tasks 3, 5, 6.
- §8.1 combined setup → Tasks 2, 7; §8.4 cold-rebuild acceptance → Task 8.
- §9 deferred (fault injection, channel-exit, vendor-profile seam) → intentionally **not** tasked.
- §12 coordination → noted; reference sweep in Task 2.

**Placeholder scan:** integration tests (Task 8) are deliberately skeletal because they require the live lab; all unit tasks carry concrete test + impl code. No "TBD" in buildable code.

**Type consistency:** `ChannelAdmin` protocol method names are used identically across Tasks 3–6; `failover`/`sim_dr`/`vendor_status` signatures match between `vendor_dr.py` (Tasks 4–5) and the CLI (Task 6); `FailoverResult`/`VendorStatus`/`ChannelStatus` dataclasses defined once.

**Note:** Tasks 1 and 8 are the load-bearing, lab-dependent gates; Tasks 2–7 are unit-testable and CI-gated. Per the project's deferral, execution waits until after the security work.
