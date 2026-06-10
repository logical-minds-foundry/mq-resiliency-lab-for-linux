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
   first`, then **exit 3 ("precondition not met") without touching Ansible**.
   This is *not* a #99-style satisfied no-op: the goal (a provisioned cluster) is
   not achieved, so a 0 here would be a misleading success for automation. Exit 3
   is distinct from the `2` input/usage errors below — a lab-*state* problem
   (fix with `mqlab vm up`), not a bad-input problem.
3. **Secrets** (declarative; all auto-generated). The lab is a disposable
   illusion, so mqlab invents *every* secret — nothing is operator-supplied. For
   each name in the setup's `secrets:` → run `lab/scripts/lab-secret.sh <name>`
   (auto-generates + persists in gitignored `build/secrets/` on first use,
   returns the same value forever after), capture its stdout (echoed like any
   step — §5), and inject it as `<NAME>.upper()` (e.g. `pcmk_hacluster_password` →
   `PCMK_HACLUSTER_PASSWORD`) onto the playbook subprocess via `Command.env`
   (merged over `os.environ` for that child only — no global mutation). There is
   no missing-secret case — `lab-secret.sh` always returns a value.
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

Setups gain one optional key, `secrets:` (default empty) — the lab secrets that
setup's playbook needs, each auto-generated and injected:

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
  secrets: [mqweb_admin_password]
```

Derivation (traced from the playbooks' roles): `PCMK_HACLUSTER_PASSWORD` is used
only by the `pcmk-cluster` role (→ the two pcmk setups); `mqweb_admin_password`
only by the `mq-qmgr` role, which only `site.yml` runs (→ standalone). The rdqm
cluster playbooks need neither.

The MQWeb **username** is not a secret, so it is *not* auto-generated: it becomes
a lab constant `mqweb_admin_user: mqadmin` in `ansible/group_vars/all.yml`
(replacing the `lookup('env', …)` + "export before ansible-playbook" comment).
Only the password is a `secrets:` entry; mqlab injects it as
`MQWEB_ADMIN_PASSWORD`, which `group_vars` continues to read via `lookup('env',
…)`. To retrieve the generated console password later:
`lab/scripts/lab-secret.sh mqweb_admin_password`.

## 5. Secret handling — no hiding needed

These secrets are auto-generated and carry **no security weight**: the lab is a
disposable illusion (power it off, throw it away, it stops existing). So we make
no effort to hide their values. `_source_secret` echoes and tees the
`lab-secret.sh` run exactly like any other step (`_probe_states`), and simply
captures the printed value to inject it. (Anything touching *real* credentials
would be done with grade-A care; this deliberately isn't that.)

## 6. Components

- **`src/mqlab/setups.py`** — `Setup` gains `secrets: list[str]` (default `[]`),
  parsed in `lab_setups()`.
- **`src/mqlab/runner.py`** — `Command` gains optional `env`, merged over
  `os.environ` for the child subprocess (so a secret rides only the playbook).
- **`src/mqlab/cli.py`** — `vm_provision(setup)` command + a `_provision`
  flow that does resolve → probe → preflight → source secrets → render
  inventory → run the playbook step. Reuses `_probe_states`, `classify`,
  `run_steps`, and the `lab_inventory`/`inventory_path` helpers from #101.
- **A secret-source helper (`_source_secret`)** — runs `lab-secret.sh <name>`,
  echoing + teeing like any step (§5), and returns the captured value.
- **`ansible/group_vars/all.yml`** — `mqweb_admin_user` becomes the constant
  `mqadmin` (it is not a secret); `mqweb_admin_password` stays a `lookup('env',
  'MQWEB_ADMIN_PASSWORD')`, which mqlab now supplies from the generated secret.
  The "export … before ansible-playbook" comment is replaced accordingly.

## 7. Error handling (fail-loud)

| condition | exit | class |
|-----------|------|-------|
| unknown setup | 2 | input/usage — name `mqlab vm status` |
| setup has no `provision:` | 2 | input/usage |
| a member not `RUNNING` | 3 | lab-state precondition — advisory note + `mqlab vm up <setup>` |
| playbook fails | ansible's code | `StepFailedError` → propagate; ansible's error already streamed verbatim |
| provisioned OK | 0 | success |

Exit-code convention: **2** = you gave wrong/incomplete input; **3** = the lab
isn't in a provisionable state yet; **playbook's own code** = Ansible ran and
failed. No swallowed failures, no empty-secret fallbacks.

## 8. Testing

- `setups.py`: `Setup.secrets` parse (present + defaulted-empty).
- `runner.py`: `Command.env` passed to the child, merged over `os.environ`.
- provision flow (CommandRunner fake, no live lab):
  - members not all running → advisory note, **no** action commands run, exit 3.
  - all running, secret setup → `lab-secret.sh` invoked, the value injected as
    `Command.env` on the playbook step, then inventory rendered + playbook runs.
  - all running, no-secret setup (rdqm) → no `lab-secret.sh`, playbook `env` is None.
  - lab-secret failure → exit 2; playbook failure → ansible's exit code.
  - unknown setup / no-provision setup → exit 2.
- `vrg-container-run -- vrg-validate` green at 100% branch.

## 9. Scope

**In:** the `vm provision` verb; the `secrets:` schema + the setups'
declarations; auto secret sourcing/injection; the inventory-render + playbook
steps; the one `group_vars/all.yml` change (mqweb username constant); tests.

**Out:** changing the playbooks or other roles; the `*-qm-create.sh`
post-provision scripts (they remain the QM-creation step after the cluster infra
is provisioned); a `deprovision` verb (provisioning is one-way).
