# RDQM DR/HA Cold-Build Defect — the no-SSH "secondaries-first" fallback never reaches `UpToDate`

> Scope: IBM **MQ 9.4.5** Advanced for Developers on **RHEL 9.6**, RDQM **3+3
> HA/DR** (two 3-node HA groups, cross-site DR). RDQM bundles and manages the
> DRBD + Pacemaker stack itself — you configure RDQM, not DRBD/Pacemaker directly.
>
> Lab IPs below are placeholders — **substitute your site's addresses.**
>
> Sources: issue #559 (root-cause triage), `docs/reports/2026-06-19-rdqm-hadr-automation-findings.md`,
> `lab/scripts/rdqm-qm-create.sh`, `ansible/roles/rdqm-ssh-access`, and the cached
> IBM 9.4 docs under `build/refs/ibm-docs/ibm-mq/9.4.x/` (RDQM DR/HA worked example).
> Harvested from the closed epic .github#45 ("Restore RDQM build functionality").

## TL;DR

On a **cold** RDQM DR/HA build, IBM's *documented no-SSH fallback* — hand-rolling
a **secondaries-first** create order (`crtmqm -sxs` on the secondaries, then
`crtmqm -sx` on the primary) — **does not work for a DR/HA queue manager.**
Creating the secondaries `Inconsistent` first forces a full multi-GB initial DRBD
resync that does not complete inside `crtmqm`'s create window, so no node reaches
`UpToDate` and the stacked DR resource cannot be promoted. The create rolls back.

The fix is **not** a bigger timeout — it is to stop using the fallback: provision
`mqm` passwordless SSH + sudo and use IBM's **coordinated** flow, one
`crtmqm -sx` per site that SSHes to its peers and auto-creates the secondaries
live (which skips the full initial resync). This is what `rdqm-qm-create.sh` does
today.

## Symptoms — two faces of one bug

The failure signature depends on whether replication TLS (`crtmqm -re`) is on. This
initially looked like a TLS bug; it is not — TLS only changes *where* the same
underlying defect surfaces.

| Path | Error | Where it dies |
|---|---|---|
| **TLS replication (`-re`, default)** | `AMQ3879E: Resource 'rdqmapp' not connected to 'rdqm-a2' after '10' seconds` → `AMQ3812E` | ~10 s, at the HA link connect-wait |
| **plaintext (no `-re`)** | `AMQ3817E` / `drbdadm primary --force --stacked rdqmapp.dr ... rc 17` → `State change failed: (-2) Need access to UpToDate data` | ~60 s, at the DR stacked-resource promote |

Both roll the queue manager back and instruct `dltmqm RDQMAPP` on the secondaries.

## Root cause

The fresh, empty replicated device is **not** skipping its initial sync — it runs a
full resync of the whole device, and `crtmqm`'s create window closes before it
finishes. DRBD kernel log during the create (from a secondary):

```
drbd rdqmapp: ... role( Primary ) disk( UpToDate )                       # primary forced Primary/UpToDate
drbd rdqmapp/0 rdqm-a1: Began resync as SyncTarget (will sync 3145500 KB [786375 bits set])   # full ~3 GB sync
drbd rdqmapp rdqm-a1: conn( Connected -> NetworkFailure )                # torn down before the sync completes
```

- With **TLS**, the slower handshake path trips the fixed **10 s** HA connect-wait
  (`AMQ3879E`) before the sync even becomes the problem.
- With **plaintext**, the create gets past connect and runs the full sync, but still
  tears down before completion — so the DR stacked resource (`rdqmapp.dr`) has no
  `UpToDate` data to promote (`AMQ3817E`, `-2 Need access to UpToDate data`).
- Post-failure DRBD state confirms it: the secondaries are left `disk:Inconsistent`,
  with **no** node `UpToDate`.

### Why the fallback triggers it

The manual **secondaries-first** order creates the secondaries `Inconsistent`
*before* the primary exists. When the primary then appears, the secondaries require
a **full** resync from it (there is no fresh-device initial-sync skip), which is the
multi-GB sync that does not complete in time. IBM's coordinated flow avoids this by
having the primary auto-create the secondaries in one operation.

### Why TLS is a red herring

With `tlshd` logging enabled (see #558 — silent `tlshd` at `loglevel=0` originally
hid this), the TLS handshakes all **succeed** (TLS 1.3, mutual auth, valid certs)
and the secondaries connect and replicate over TLS. TLS is not broken; it just makes
the HA link slow enough to fail the 10 s connect-wait *before* the deeper sync
problem is reached.

## Resolution (current state)

1. **Provision `mqm` passwordless SSH + sudo** on the HA nodes — the
   `rdqm-ssh-access` role (#560). It also **moves `mqm`'s home to `/home/mqm`**:
   the default `/var/mqm` is SELinux `var_t` + group-writable, which breaks `sshd`
   `StrictModes`. The role runs after `rdqm-install` and **before** `mqweb` (mqweb
   runs as `mqm` and would block the `usermod` home move), and is removed after the
   create via its `tasks_from: remove`.
2. **Use IBM's coordinated create** — one `crtmqm -sx` per site, run **as `mqm`**.
   `crtmqm` self-sudos for its privileged work and SSHes to that site's HA peers as
   `mqm` over the replication subnet (`172.16.x`) to auto-create the secondaries
   ("*Secondary queue manager created on rdqm-a2/a3*"). No manual `-sxs`. The DR
   flags go on the command line:

   ```bash
   # Site A = DR primary (auto-creates a2/a3); site B = DR secondary (auto-creates b2/b3).
   crtmqm -fs 3072M -sx -rr p -rl <A_WAN_IPs> -ri <B_WAN_IPs> -rp 7001 [-re] QMRDQM   # on rdqm-a1, as mqm
   crtmqm -fs 3072M -sx -rr s -rl <B_WAN_IPs> -ri <A_WAN_IPs> -rp 7001 [-re] QMRDQM   # on rdqm-b1, as mqm
   ```

   (`-rr p/s` = DR role; `-rl` local-site DR IPs; `-ri` remote-site DR IPs;
   `-rp` DR port; `-re` replication TLS. Flags verified against the cached IBM
   worked example, not reconstructed.)

Because the primary auto-creates the secondaries in one coordinated operation, the
device comes up `UpToDate` without a full initial resync, and the `AMQ3879E` /
`AMQ3817E` class disappears. See `lab/scripts/rdqm-qm-create.sh` for the live
implementation.

## Diagnosing / reproducing

- **Repro:** cold-rebuild the `rdqm-rhel` stack and run the QM create with the
  secondaries-first fallback; observe `AMQ3879E` (TLS) at ~10 s.
- **Bisect:** re-run the same `crtmqm` **without `-re`** → `AMQ3817E ... Need access
  to UpToDate data` at ~60 s. A *different* error without TLS proves TLS is not the
  cause.
- **Confirm:** `drbdsetup status --verbose` shows all nodes `Inconsistent`; the
  kernel log shows a full multi-GB `Began resync as SyncTarget` that is torn down
  before finishing.
- **Prerequisite for diagnosis:** `tlshd` logging must be on (#558), or the TLS
  handshake success is invisible and the investigation derails onto TLS.

## The IBM-side takeaway

IBM documents the manual secondaries-first order as the no-SSH fallback for creating
RDQM queue managers, but for **DR/HA** managers that fallback pushes the fresh device
into a full initial resync that `crtmqm` does not wait out — so the documented
fallback cannot produce a healthy DR/HA QM on a cold build. The supported path for
DR/HA is the SSH-based coordinated create. Provision `mqm` SSH+sudo and use it; treat
the secondaries-first fallback as **HA-only**, not DR/HA.

## References

- Issue #559 — full root-cause triage (the evidence above).
- `docs/reports/2026-06-19-rdqm-hadr-automation-findings.md` — the automation that
  landed the coordinated create + DR flags.
- `docs/reports/2026-06-06-phase-c-rdqm-findings.md` — the by-hand Phase-C HA+DR proof.
- `docs/reference/rdqm-ha-cheatsheet.md` — HA-group setup (the non-DR case).
- Issues #560 (`mqm` SSH-access role), #561 (coordinated create), #558 (`tlshd`
  logging), #545 (replication TLS). Epic .github#45 (closed).
