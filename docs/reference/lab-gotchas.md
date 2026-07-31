# Lab Gotchas Playbook (libvirt / Vagrant / Pacemaker)

> Symptom → cause → fix, from direct experience. The Pacemaker entries extend
> the Phase D findings; the libvirt/Vagrant/worktree ones are new from #67.

## Contents

- [Vagrant leaves orphaned extra-disk volumes](#vagrant-leaves-orphaned-extra-disk-volumes)
- [Concurrent same-box boot trips a Vagrant machine lock](#concurrent-same-box-boot-trips-a-vagrant-machine-lock)
- [Can't live-attach a NIC ("No more available PCI slots")](#cant-live-attach-a-nic-no-more-available-pci-slots)
- [Driving the lab from a worktree](#driving-the-lab-from-a-worktree)
- [build/ artifacts missing in a worktree](#build-artifacts-missing-in-a-worktree)
- [Ansible silently does nothing (empty inventory)](#ansible-silently-does-nothing-empty-inventory)
- [Pacemaker operational settings that matter](#pacemaker-operational-settings-that-matter)
- [RDQM DR cold-create: crtmqm mkfs I/O error on the DR device](#rdqm-dr-cold-create-crtmqm-mkfs-io-error-on-the-dr-device)

## Vagrant leaves orphaned extra-disk volumes

**Symptom.** `vagrant up san-a` fails: `storage volume 'lab_san-a-vdb.qcow2'
exists already`. Then after deleting that, `Volume for domain is already created.
Please run 'vagrant destroy' first`.

**Cause.** `vagrant destroy` removes the main domain disk but **not** the
`extra_disk` qcow2 (the SAN's `/dev/vdb`), and a half-failed `up` leaves the main
volume too.

**Fix.** Clear every `lab_<node>*` volume, then up:
```sh
vagrant destroy -f san-a
for v in $(virsh -c qemu:///system vol-list --pool default | awk '/lab_san-a/{print $1}'); do
  virsh -c qemu:///system vol-delete "$v" --pool default
done
vagrant up san-a
```
Generalize the `awk` filter to `lab_(san-|pcmk-|app-client)` for a full arm wipe.

## Concurrent same-box boot trips a Vagrant machine lock

**Symptom.** A `vagrant up` of several nodes at once fails on *one* of them (a
different node each run) with: `Vagrant can't use the requested machine because
it is locked! ... another Vagrant process is currently reading or modifying the
machine`. The nodes themselves are fine — the *batch* fails, so a resume limps
forward one batch at a time and never completes a cold rebuild in one pass.

**Cause.** Nodes that boot from the **same** baked box (e.g. the six
`nha-rhel-*` on `mq-nativeha-rhel9`, or the three `mq-ubuntu2404` commons) race
on staging that box's base volume into the libvirt pool the first time it's
used. The concurrent clones trip Vagrant's per-machine lock. Once the volume is
staged, later clones from it are fine.

**Fix (built in, #859).** The vms phase detects a batch whose guests share a box
and issues that batch `vagrant up --no-parallel` — serial *within the batch*, so
the first boot stages the shared volume before the next clones it. Batches whose
guests all boot **distinct** boxes keep the parallel default (distinct volumes
don't contend), preserving the `boot_batch` speed dial (#638). If you ever drive
`vagrant up` by hand across same-box nodes, add `--no-parallel` yourself.

## Can't live-attach a NIC ("No more available PCI slots")

**Symptom.** Adding a network to a running VM fails with no free PCI slots.

**Cause.** The guest was created with a fixed PCI layout; you can't hot-add.

**Fix.** Add the NIC in `topology.yaml` and `vagrant destroy + up` that node —
it's rebuilt with all NICs. (This is why giving `app-client` a `net-data-b` NIC
to reach the site-B VIP meant recreating it.)

## Driving the lab from a worktree

The lab's Vagrant state lives in `<checkout>/lab/.vagrant`, so by default only
the checkout that created the VMs can drive them. But:

**The domains are portable.** The libvirt domain prefix is the Vagrantfile
directory **basename** (`lab`) — identical in `main/lab` and every
`.worktrees/<x>/lab`. Copy the state and a worktree drives the *same* running
domains:
```sh
cp -a main/lab/.vagrant <worktree>/lab/.vagrant
cd <worktree>/lab && vagrant status     # lists the running VMs
```
**Benign warning:** `This machine used to live in <main> ... but it's now at
<worktree>` — vagrant just updated its path reference; proceed.

**Caveat.** Don't operate the same lab from *both* checkouts at once. (The proper
fix — one shared, discoverable state location — is #69.)

## build/ artifacts missing in a worktree

**Symptom.** Provisioning from a worktree fails: `Could not find or access
'<worktree>/ansible/../build/fence_key'` (also the MQ install tarball). One
missing file cascades: `pcmk-stonith` fails → the host is marked failed →
`mq-install` is skipped → no `mqm` user → QM-create's `chown mqm` fails.

**Cause.** `build/` is host-mounted into the main checkout and **gitignored**, so
it's never in a worktree; ansible resolves these paths relative to the worktree.

**Fix (stopgap).** Symlink — gitignored, so it does **not** dirty develop:
```sh
ln -s ../../build <worktree>/build
```
**Restore before committing** (the symlink replaces the tracked `build/.gitkeep`):
```sh
rm <worktree>/build && (cd <worktree> && vrg-git checkout -- build/.gitkeep)
```
Proper fix: #69 (one shared lab-state location covering vagrant state + `build/`).

## Ansible silently does nothing (empty inventory)

**Symptom.** A scripted cutover prints every step and "complete", but nothing
changed (no LUN, QM not running on the peer).

**Cause.** Two compounding bugs: (1) the script masked every command with
`|| true` and printed success unconditionally; (2) the worktree's
`build/inventory.ini` was never generated, so ansible had **zero target hosts** —
every `run <group> ...` was a no-op.

**Fix.** Regenerate the inventory from the operating checkout, and make scripts
fail loud:
```sh
bash ansible/inventory.sh        # reads `vagrant status` from ../lab -> ../build/inventory.ini
```
Verify an **end state** (e.g. `dspmq` shows `STATUS(Running)` on the peer) and
exit non-zero otherwise — never trust per-step `|| true`.

## Pacemaker operational settings that matter

| Setting | Why (symptom it prevents) |
|---|---|
| `resource-stickiness=1000` | without it the group auto-fails-back to a recovered node; that controlled move-back runs `endmqm` and **disconnects reconnected clients** |
| `no-quorum-policy=suicide` | makes a quorum-loss (heartbeat sever) an **abrupt self-fence** instead of a graceful `endmqm -w` — so clients ride it via auto-reconnect like a crash |
| `op monitor ... OCF_CHECK_LEVEL=20 on-fail=fence` on `mq_fs` | a real read/write probe catches a **dead SAN**; the default mount-table check is a ~12-minute blind spot (an idle QM generates no I/O to notice the dead disk) |
| planned move = location constraint → verify → remove | this `pcs` version's `resource move` porcelain raced our migrations and **reverted** them |
| `fence_virsh` needs `LIBVIRT_DEFAULT_URI=qemu:///system` | STONITH SSHes to the hypervisor (`build/fence_key`); without the URI the fence exec fails |

## `runmqakm` fails (`libicuio`) on a minimal RHEL box — install `libicu`

**Symptom.** `runmqakm` (and `runmqckm`) fail immediately with `Failed to dlopen
ICU library. Attempted versions from libicuio.so.100 to libicuio.so.49`, at any
`LD_LIBRARY_PATH`. So no TLS keystore / stash via the MQ tools, and (because most
arms' channel TLS and Native HA CRR-over-TLS need a stashed keystore) all TLS
work is blocked. Base QM ops (`crtmqm`/`strmqm`/`dspmq`, plaintext Native HA/CRR)
don't touch GSKit, so it's invisible until TLS.

**Cause.** `runmqakm` dlopen's the **system** `libicuio.so.*` at runtime (not a
link dep, so `ldd` shows nothing) — and the minimal kickstart RHEL 9.6 box does
**not** install `libicu` (it's not a hard MQ rpm dependency). GSKit 9's own libs
*are* present (`/opt/mqm/gskit9/lib64`); the missing piece is the OS ICU. (Earlier
readings — "unextracted tarballs", "use .p12 to avoid runmqakm" — were wrong; this
is the real root cause.)

**Fix — `dnf install libicu`.** RHEL 9 ships ICU 67 (`/usr/lib64/libicuio.so.67`),
squarely in runmqakm's accepted 49–100 range, so installing it makes `runmqakm`
work immediately. It's in the DVD BaseOS repo (offline). The Native HA TLS role
(`mq-nativeha/tasks/tls.yml`) installs `libicu` before any keystore op.

**Then TLS works the proper way:** deploy the `lab-pki` PKCS#12 (`.p12`, OpenSSL,
`compatibility2022` encoding), stash its password with the now-working
`runmqakm -keydb -stashpw -type pkcs12`, and set `NativeHALocalInstance`
`CipherSpec`/`CertificateLabel`/`KeyRepository`. **Verified (#246 Phase 3):** the
site-A Native HA group re-formed `QUORUM(3/3) INSYNC` with replication negotiating
`ECDHE_RSA_AES_256_GCM_SHA384` — TLS replication via a CA-signed lab-pki cert.

## RDQM DR cold-create: crtmqm mkfs I/O error on the DR device

**Symptom.** A cold `crtmqm` of the DR/HA queue manager aborts while formatting the
DR device — `mkfs.ext4` on `/dev/drbd/by-res/<qm>.dr/0` fails with I/O errors, and
crtmqm rolls its own create back:
```
AMQ3817E: Replicated data subsystem call '/sbin/mkfs.ext4 /dev/drbd/by-res/rdqmapp.dr/0' failed with return code '1'.
Warning: could not read block 0: Input/output error
mkfs.ext4: Input/output error while writing out and closing file system
AMQ3812E: Failed to create replicated data queue manager
Secondary queue manager deleted on 'rdqm-a3'.   ← crtmqm rolls the create back
```
`crtmqm` exits `rc=71`; the provision fails at `site-rdqm.yml` → "create the RDQM
queue manager". Non-deterministic / load-sensitive.

**Cause.** The coordinated create formats the DR DRBD device before the DR
replication link (site-A ↔ site-B) is *stably* Connected/UpToDate. Under this
stack's resource envelope (the rdqm arm's own ~20 vCPU footprint on an 8-core
host), DRBD heartbeats time out in the format window: `dmesg` shows
`rdqmapp.dr ... conn( Disconnecting → StandAlone )` and the resource losing quorum.
`mkfs` "could not read block 0: Input/output error" is DRBD **fencing I/O because
the DR device lost quorum**, not a bad block. It is transient: a manual
`bootstrap --from provision` resume moments later succeeds because the link has
settled by then.

**Fix.** A bounded in-line retry around the DR `crtmqm` create, not a DRBD-config
rewrite. `run_crtmqm_retry` in `lab/scripts/rdqm-qm-create.sh` wraps **both** DR
site creates (the `-rr p` primary on rdqm-a1 and the `-rr s` secondary on rdqm-b1):
it retries **only** the transient DR-device signature (`Input/output error`, the
AMQ3817E mkfs subsystem-call failure, or AMQ3812E alongside a StandAlone/
Disconnecting DR-link drop) with a 20s backoff to let the link settle, up to 3
attempts, and **fails loud** on any other error or after exhaustion — a genuine
crtmqm fault must never be swallowed (#583). crtmqm self-rolls-back the failed
attempt, so each retry starts clean; no manual pre-clean is needed. The HA-only
create is not wrapped — with no DR link (`-reh`, no `.dr` device) it cannot hit
this class. Mirrors the `run_mqsc_retry` idiom (#657) in the same script. See #768;
amplified by host pressure (#120).
