# Lab bring-up — captured run (DRAFT, feeds the site documentation)

> Working capture written *as the bring-up is executed* (2026-06-08, issue #53).
> Not the definitive doc — the site-docs session owns that. This exists to
> (a) record exactly what was required and what was run, and (b) surface where
> today's script-heavy flow should reduce to a few straightforward, reusable
> commands.
>
> **Operational note:** the lab's large artifacts (MQ tars, RHEL ISO) live in the
> *main worktree's* `build/` (a per-checkout, gitignored dir — NOT shared into
> feature worktrees). So the bring-up commands below are run from the main
> worktree; this capture doc is authored in the #53 worktree and merged via its PR.

## Contents

- [1. Required data artifacts](#1-required-data-artifacts-must-exist-in-build-before-any-bring-up)
- [2. Prerequisites verified this run (2026-06-08)](#2-prerequisites-verified-this-run-2026-06-08)
- [3. Bring-up log — minimal message path](#3-bring-up-log--minimal-message-path-phase-b-qm-main--svc-sim--app-client)
- [4. Tooling-improvement notes](#4-tooling-improvement-notes-reduce-scripts--a-few-reusable-commands)

## 1. Required data artifacts (must exist in `build/` before any bring-up)

Large/licensed binaries, **not** in git. The bring-up fails loudly without them.
Capturing provenance is the priority of this draft.

| Artifact | `build/` path | Size | Auto-fetch? | Source / archive |
|---|---|---|---|---|
| MQ 9.4.5 Ubuntu (host arch) | `mq/9.4.5.0-…-UbuntuLinux{ARM64\|X64}.tar.gz` | ~467 MB | **Yes** | `scripts/fetch-mq.sh` picks the suffix from `uname -m` (`ARM64` on the Mac dev path, `X64` on an x86 host) — native-preferred (#276). IBM developer CDN, no auth. SHA256 recorded first fetch, verified after. |
| MQ 9.4.5 x86-64 (RHEL/RDQM) | `mq/9.4.5.0-IBM-MQ-Advanced-for-Developers-LinuxX64.tar.gz` | 520 MB | **Yes** | `scripts/fetch-mq.sh` fetches this on **every** host (RHEL is x86_64-only). Same CDN, no auth. |
| RHEL 9.6 DVD ISO (Phase C box build) | operator-supplied (see §1.1) | 12 GB | **NO** | **Not downloadable by script** (licensed media). The **only** artifact an operator must acquire by hand; located via the artifact-resolution mechanism (§1.1). Implementation tracked in #54. |

**Rule captured:** if an artifact *can* be fetched, a `scripts/` helper should
fetch + checksum it into `build/`. If it *cannot* (licensing), it is
**operator-supplied** and located via the resolution mechanism below — the shared
code knows *how to look*, the operator's local config says *where it is*.

### 1.1 Locating operator-supplied artifacts (the RHEL ISO)

A machine-specific path can't live in a committed file (`vergil.toml` is shared,
so a per-machine path would leak into git and break other operators). Instead, a
small resolver finds the artifact by a fixed precedence (highest first):

1. **`MQLAB_RHEL_ISO` env var** — CI / one-off override.
2. **`~/.config/mq-resiliency-lab-for-linux/config.toml` → `[artifacts] rhel_9_6_iso`** —
   the durable per-operator setting (XDG, same pattern as Vergil's identity
   config). Example:

   ```toml
   # ~/.config/mq-resiliency-lab-for-linux/config.toml
   [artifacts]
   rhel_9_6_iso = "~/dev/software/rhel-9.6-x86_64-dvd.iso"
   ```

3. **`build/rhel-9.6-x86_64-dvd.iso`** — zero-config drop-in fallback (the
   "just copy it into `build/`" path that works today).
4. **None found → fail loud**, naming the artifact, the places checked, and the
   one-line config to set.

So an operator drops the ISO wherever they keep large media, adds one line to
their local config, and the shared tooling (box build + preflight) finds it — no
repo edits, nothing committed, portable across machines. Resolver + wiring are
tracked in **#54**.

## 2. Prerequisites verified this run (2026-06-08)

- `/dev/kvm` present (nested virt enabled).
- `vagrant-libvirt` 0.12.2 installed.
- libvirt `default` storage pool active + autostart.
- Both MQ tars + the RHEL ISO present in `build/` (§1).
- IBM CDN reachable.
- ⚠️ Host `.venv` was stale (`uv run` warned: interpreter `.venv/bin/python3` →
  non-existent). Fix: `uv sync` at repo root before running ansible.

## 3. Bring-up log — minimal message path (Phase B: qm-main + svc-sim + app-client)

Smallest functional system; prerequisite for the DR framework's first live
milestone (Plan 2 Tasks 4–7).

| # | Step | Command | Result |
|---|---|---|---|
| 0 | Fix host venv | `uv sync` (repo root) | ✅ host `.venv` was stale (interpreter gone); `uv sync` rebuilt it → `ansible-playbook core 2.21.0`. |
| 1 | Networks | `lab/scripts/net-up.sh` | ✅ defined+started 10 libvirt nets (default + net-client/data-a/data-b/svc/hb-a/hb-b/san-a/san-b/wan); all active + autostart. |
| 2 | Boot trio | `cd lab && vagrant up qm-main svc-sim app-client --provider=libvirt` | ✅ 3 running, ~2 min (KVM arm64, box `cloud-image/ubuntu-24.04` already present). Static IPs correct: qm-main 10.30.0.10+10.20.0.10, svc-sim 10.20.0.50, app-client 10.30.0.60. **node-a1 NOT required** (the old quickstart over-specified it). |
| 3 | Inventory | `ansible/inventory.sh` | ✅ wrote `build/inventory.ini` (3 hosts: qm_hosts=qm-main,svc-sim; client_hosts=app-client). Harmless `[fog][WARNING] Unrecognized arguments: libvirt_ip_command` noise. `ansible.cfg` already points `inventory = ../build/inventory.ini`. |
| 4 | Provision | `MQWEB_ADMIN_USER/PASSWORD` env + `cd ansible && ansible-playbook site.yml` | ⏳ running (background). Creds generated and saved to `build/mqweb.env` (gitignored) for reuse by steps 5+. |
| 5 | QM objects | `source build/mqweb.env && python -m mqlab.apply content/qm-main.yaml https://10.30.0.10:9443` (and `content/svc-sim.yaml https://10.20.0.50:9443`) | ✅ 6 objects CREATED each (QMAIN: APP.REPLY, QMSVC xmit, SVC.REQUEST remote, QMAIN.QMSVC/QMSVC.QMAIN/APP.SVRCONN; QMSVC mirror + SVC.SVRCONN). `mqlab.apply` reads `MQWEB_ADMIN_USER/PASSWORD` from env. **Legacy Phase-B single-QM path** (`10.30.0.10`/`10.20.0.x` predate the #351 four-stack model; superseded by Ansible `runmqsc`; the canonical REST address now comes from `mqlab rest render`, epic #39). |
| 6 | Start channels | `cd ansible && ansible qm-main -b --become-user=mqm -m shell -a 'echo "START CHANNEL(QMAIN.QMSVC)" \| runmqsc QMAIN'` (+ QMSVC.QMAIN on svc-sim) | ✅ `AMQ8018I: Start IBM MQ channel accepted` both sides. |
| 7 | E2E proof | `lab/scripts/e2e-test.sh 5` | ✅ **5/5 clean ACKs, exit 0.** Message path live: app-client → QMAIN → channel → QMSVC → responder → reply. |

**Result: the Phase B message path is GREEN.** Total wall-clock from cold (all
artifacts present): ~12 min, the bulk being MQ install in step 4. This is the
substrate the DR framework's first live milestone (Plan 2 Tasks 4–7) builds on.

## 4. Tooling-improvement notes (reduce scripts → a few reusable commands)

Goal: replace the net-up → vagrant up → inventory → playbook → apply →
start-channels → e2e chain with a single reusable verb (e.g.
`make lab-up-messagepath` or an `mqlab lab up messagepath` CLI).

- **One verb, not seven.** The message-path bring-up is 7 manual steps across
  3 directories (repo root, `lab/`, `ansible/`) with env-var setup in the middle.
  Collapse to a single reusable command, e.g. `make lab-up-messagepath` or
  `mqlab lab up messagepath`, that runs: preflight (artifacts + `/dev/kvm` + pool)
  → net-up → vagrant up → inventory → playbook → apply → start-channels → e2e,
  and prints one PASS/FAIL.
- **Preflight check is missing.** There's no single command that verifies the
  required `build/` artifacts (§1), `/dev/kvm`, the libvirt pool, and the box
  before booting. Add `scripts/lab-preflight.sh` (or a CLI subcommand) that fails
  loud naming the missing artifact + where to get it (esp. the RHEL ISO).
- **Stale host `.venv` is a recurring trap.** First action of any lab session
  should be `uv sync`; fold it into the preflight/verb.
- **Credential handling is ad-hoc.** MQWEB user/pass are generated inline and
  must be threaded to both the playbook and `mqlab.apply`. Standardize on
  `build/mqweb.env` (gitignored) written by the bring-up verb and sourced by every
  downstream step.
- **`inventory.sh` emits `[fog]` warning noise** (`Unrecognized arguments:
  libvirt_ip_command`) — cosmetic, but worth silencing so real warnings stand out.
- **Channel-start should be declarative.** Step 6 hand-runs `runmqsc START
  CHANNEL` via ansible; this belongs in the `mqlab.apply` content spec (an
  ensure-channel-started), removing a manual step.
