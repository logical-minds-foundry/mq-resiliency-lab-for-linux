# Seeding the RHEL DVD onto an off-platform lab VM

The lab build expects the RHEL 9.6 DVD ISO at `build/state/rhel-9.6-x86_64-dvd.iso`
(~12.7 GB). It is gitignored and entitlement-gated, so it never arrives via clone —
the operator supplies it once.

On a local Lima dev VM this is automatic: `build/` is host-mounted, so the ISO you
dropped in `build/state/` on the host is already visible in the VM. On an
**off-platform (GCP) VM** it is not: `build/` lives on the VM's **persistent
volume**, which is not populated from the host. So after `vrg-vm create` for an
off-platform VM, the ISO must be pushed onto its `build/state/` once. It then
persists until that volume is destroyed — a true one-off per volume.

## How

From the host (macOS), in this repo:

```bash
./scripts/push-rhel-iso.sh
```

It copies `build/state/rhel-9.6-x86_64-dvd.iso` straight to the VM's
`build/state/` over the private VM's IAP tunnel
(`gcloud compute scp --tunnel-through-iap`). The ISO source resolves the same way
`lab/scripts/stage-rhel-iso.sh` does (git-common-dir, with `MQLAB_RHEL_ISO` as an
override), so both scripts agree on where the ISO lives. The script is idempotent:
a re-run no-ops if the VM already holds a same-size copy.

Once it lands, `lab/scripts/stage-rhel-iso.sh` (run inside the VM) takes it from
`build/state/` into the libvirt pool as usual.

## Why not a GCS bucket

The VM is private (no public IP) and has no `gcloud`/`gsutil` installed. Routing
the ISO through a Cloud Storage bucket would mean installing the Cloud SDK *and*
wiring service-account credentials on the VM — a credential rabbit hole not worth
it for a one-off seed. Pushing directly over IAP keeps all auth host-side (the
operator's existing `gcloud` login) and needs nothing on the VM beyond stock
sshd / git / coreutils.

## Notes and gotchas

- **Run it on the host**, not inside the VM. `gcloud` auth is host-local; the VM
  has no Cloud SDK.
- **Instance name carries a generated suffix** that changes on `vrg-vm rebuild`.
  The script resolves the instance by name-match (`mq-resiliency-lab`), so it keeps
  working across rebuilds without edits.
- **No resume.** `gcloud compute scp` does not resume a partial transfer; if the
  ~12.7 GB copy drops, re-run it (the idempotent size check skips it only once it
  is fully there). For a resumable transfer, tunnel with
  `gcloud compute start-iap-tunnel` and `rsync --partial` over the local port.
- **Lands as `ubuntu`**, which owns the persistent volume, so there are no
  permission issues writing into `build/state/`.
