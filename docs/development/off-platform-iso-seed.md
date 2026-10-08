# Seeding the RHEL DVDs onto an off-platform lab VM

The lab build expects each RHEL DVD ISO the catalog names (the `iso:` value of every
`os.rhel.<major>` entry in [`lab/versions.yaml`](../../lab/versions.yaml), ~12.7 GB
each) under `build/state/`. They are gitignored and entitlement-gated, so they never
arrive via clone; the operator supplies them once.

On a local Lima dev VM this is automatic: `build/` is host-mounted, so an ISO you
dropped in `build/state/` on the host is already visible in the VM. On an
**off-platform (GCP) VM** it is not: `build/` lives on the VM's **persistent
volume**, which is not populated from the host. So after `vrg-vm create` for an
off-platform VM, the ISOs must be pushed onto its `build/state/` once. They then
persist until that volume is destroyed, a true one-off per volume. A RHEL major added
to the catalog later needs one more push.

## How

From the host (macOS), in this repo:

```bash
./scripts/push-rhel-iso.sh --catalog                   # every DVD the catalog names
./scripts/push-rhel-iso.sh --iso <file> [--iso <file>] # or an explicit set
```

`--catalog` pushes every `os.rhel.<major>.iso` in the catalog (#1395), read without
`mqlab` or PyYAML by `lab/scripts/rhel-catalog-isos.sh`, so re-running it after a
catalog change pushes just the new DVD. Each `<file>` is a DVD filename the lab
expects, an `iso:` value from the catalog (#1274). The script looks the VM up once,
then copies each `build/state/<file>` straight to the VM's `build/state/<file>` over
the private VM's IAP tunnel (`gcloud compute scp --tunnel-through-iap`). It is
idempotent per ISO: one the VM already holds as a same-size copy is skipped. A
failed ISO does not stop the rest; the run exits non-zero naming every ISO that
failed.

The ISO source resolves the same way `lab/scripts/stage-rhel-iso.sh` does
(git-common-dir), so both scripts agree on where an ISO lives. To push a download
that is not yet in `build/state/`, point `MQLAB_RHEL_ISO` at it and name its target
with a single `--iso <file>`. The override names one file, so the script refuses it
with `--catalog` or more than one `--iso`.

Once they land, `lab/scripts/stage-rhel-iso.sh` (run inside the VM by bootstrap, which
passes each `--iso <file>`; `--catalog` stages them all by hand) takes them from
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
- **Instance name is an opaque, generated `vrg-<hash>`** that changes on
  `vrg-vm rebuild`. The script resolves the instance by its Vergil labels (org,
  repo, identity; #329), so it keeps working across rebuilds without edits.
- **No resume.** `gcloud compute scp` does not resume a partial transfer; if the
  ~12.7 GB copy drops, re-run it (the idempotent size check skips an ISO only once
  it is fully there, so a `--catalog` re-run redoes just the dropped one). For a
  resumable transfer, tunnel with `gcloud compute start-iap-tunnel` and `rsync --partial` over the local port.
- **Lands as `ubuntu`**, which owns the persistent volume, so there are no
  permission issues writing into `build/state/`.
