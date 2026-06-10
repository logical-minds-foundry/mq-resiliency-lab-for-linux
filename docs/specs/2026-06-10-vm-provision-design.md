# mqlab vm provision <setup> — design

**Issue:** #102
**Date:** 2026-06-10
**Status:** Design — awaiting review
**Depends-on:** #101 (static topology-derived inventory) — merged

## 1. Goal

Add `mqlab vm provision <setup>` — the **provisioning** layer of the vm namespace.
The existing verbs cover existence (`create`/`destroy`) and power (`up`/`down`);
provisioning — running a setup's Ansible playbook to configure the cluster — is
the third, genuinely **one-way** concern (you configure a cluster; you don't
"un-provision" it, you tear it down). This is the Ansible run an operator goes
looking for; today it is hand-run via `lab/scripts/*.sh`.

## 2. Command shape

`mqlab vm provision <setup>` — the argument is a **setup name only**, not a
guest / regex / `all`. A playbook configures a whole cluster holistically (e.g.
Pacemaker needs all its nodes), so a per-guest or regex target is meaningless. A
bad argument errors in mqlab's own voice, pointing at `mqlab vm status` (which
lists the setups).

## 3. Flow

1. **Resolve** the setup via `lab_setups()`. Unknown name → mqlab error (exit 2)
   naming `mqlab vm status`. A setup with no `provision:` playbook → mqlab error
   (exit 2): `<setup> has no provision playbook`.
2. **Probe** member state — reuse #99's `_probe_states` / `classify`. Members come
   from `setup_members(<setup>)`. If **any** member is not `RUNNING`, emit an
   advisory note listing the not-running members and `run mqlab vm up <setup>
   first`, then **exit 0 without touching Ansible** (a guided no-op, consistent
   with the #99 advisory model — `vm up` itself chains the guidance to `vm
   create` for absent members).
3. **Secrets / env** (declarative, fail-loud):
   - For each name in the setup's `secrets:` → run `lab/scripts/lab-secret.sh
     <name>`, **capture its stdout silently**, and set
     `os.environ[<NAME>.upper()]` (e.g. `pcmk_hacluster_password` →
     `PCMK_HACLUSTER_PASSWORD`). The Ansible subprocess inherits it.
   - For each var in the setup's `requires_env:` → if it is absent or empty in
     `os.environ`, **fail loud** (exit 2): `<setup> requires <VAR> — export it
     before provisioning`. No silent empty-credential runs.
4. **Render the inventory** (inline): write `build/inventory.ini` from topology
   via the #101 renderer — a Python call, not a subprocess — echoing a one-line
   note that it was written. This guarantees Ansible runs against a current
   static inventory.
5. **Run the playbook** (the one orchestrated, streamed/teed `CommandStep`):
   `uv run ansible-playbook <basename(provision)>` with cwd `ansible/`, streamed
   verbatim through `run_steps`. `ansible.cfg` already points at
   `../build/inventory.ini`.

One-way by design: no `vm deprovision`; teardown is `vm down` / `vm destroy`.
Ansible's own idempotency makes re-running `vm provision` safe (inherits the #99
"run twice, converges" property).

## 4. Per-setup declarations (topology.yaml)

Setups gain two optional keys (both default empty):

```yaml
pcmk_san_ha:
  groups: [san_a, pcmk_a]
  provision: ansible/site-pcmk.yml
  secrets: [pcmk_hacluster_password]        # auto-generated via lab-secret.sh
pcmk_san_dr:
  groups: [san_a, san_b, pcmk_a, pcmk_b]
  provision: ansible/site-pcmk-dr.yml
  secrets: [pcmk_hacluster_password]
rdqm_ha:   { groups: [rdqm_a],         provision: ansible/site-rdqm.yml }
rdqm_dr:   { groups: [rdqm_a, rdqm_b], provision: ansible/site-rdqm.yml }
standalone:
  groups: [qm, dtcc, client]
  provision: ansible/site.yml
  requires_env: [MQWEB_ADMIN_USER, MQWEB_ADMIN_PASSWORD]
```

Derivation (traced from the playbooks' roles): `PCMK_HACLUSTER_PASSWORD` is used
only by the `pcmk-cluster` role (→ the two pcmk setups); `mqweb_admin` only by
the `mq-qmgr` role, which only `site.yml` runs (→ standalone). The rdqm cluster
playbooks need neither.

## 5. Secret hygiene

Sourcing a secret captures the value **silently**: the command line
(`bash lab/scripts/lab-secret.sh pcmk_hacluster_password`) may be echoed for
transparency, but its **output value is never rendered to screen nor written to
the transcript** (`build/runs/*.log`). A dedicated silent capture (a sink that
appends to a local buffer only) is used, distinct from the normal echo-and-tee
sink. This keeps secrets out of logs while preserving the "show the command"
contract.

## 6. Components

- **`src/mqlab/setups.py`** — `Setup` gains `secrets: list[str]` and
  `requires_env: list[str]` (default `[]`), parsed in `lab_setups()`.
- **`src/mqlab/cli.py`** — `vm_provision(setup)` command + a `_provision`
  flow/helper that does resolve → probe → preflight → source secrets → check env
  → render inventory → run the playbook step. Reuses `_probe_states`,
  `classify`, `_resolve_or_exit`, `_execute`-style orchestration, and the
  `lab_inventory`/`inventory_path` helpers from #101.
- **A silent secret-source helper** — runs `lab-secret.sh <name>` via the
  CommandRunner with a capture-only sink; returns the value.

## 7. Error handling (fail-loud)

| condition | behavior |
|-----------|----------|
| unknown setup | mqlab error, exit 2, name `mqlab vm status` |
| setup has no `provision:` | mqlab error, exit 2 |
| a member not `RUNNING` | advisory note + `mqlab vm up <setup>`, exit 0 |
| missing `requires_env` var | mqlab error, exit 2, name the var |
| playbook fails | `StepFailedError` → propagate ansible's exit code; ansible's own error already streamed verbatim |

No swallowed failures, no empty-secret fallbacks.

## 8. Testing

- `setups.py`: `Setup.secrets` / `requires_env` parse (present + defaulted-empty).
- provision flow (CommandRunner fake, no live lab):
  - members not all running → advisory note, **no** action commands run.
  - all running, secret setup → `lab-secret.sh` invoked, `os.environ` set, then
    inventory rendered + `ansible-playbook` step runs.
  - missing `requires_env` → exit 2, no `ansible-playbook` run.
  - **secret hygiene**: the captured secret value appears **neither** in the
    rendered output buffer **nor** in the transcript.
  - unknown setup / no-provision setup → exit 2.
- `vrg-container-run -- vrg-validate` green at 100% branch.

## 9. Scope

**In:** the `vm provision` verb; `secrets:` / `requires_env:` schema + the five
setups' declarations; secret sourcing + env validation; the inventory-render +
playbook steps; tests.

**Out:** changing the playbooks or roles; the `*-qm-create.sh` post-provision
scripts (they remain the QM-creation step after the cluster infra is provisioned);
a `deprovision` verb (provisioning is one-way).
