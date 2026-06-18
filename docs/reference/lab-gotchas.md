# Lab Gotchas Playbook (libvirt / Vagrant / Pacemaker)

> Symptom → cause → fix, from direct experience. The Pacemaker entries extend
> the Phase D findings; the libvirt/Vagrant/worktree ones are new from #67.

## Contents

- [Vagrant leaves orphaned extra-disk volumes](#vagrant-leaves-orphaned-extra-disk-volumes)
- [Can't live-attach a NIC ("No more available PCI slots")](#cant-live-attach-a-nic-no-more-available-pci-slots)
- [Driving the lab from a worktree](#driving-the-lab-from-a-worktree)
- [build/ artifacts missing in a worktree](#build-artifacts-missing-in-a-worktree)
- [Ansible silently does nothing (empty inventory)](#ansible-silently-does-nothing-empty-inventory)
- [Pacemaker operational settings that matter](#pacemaker-operational-settings-that-matter)

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

## GSKit ships as un-extracted tarballs — TLS tooling fails until initialized

**Symptom.** `runmqakm` (and `runmqckm`) fail immediately with
`Failed to dlopen ICU library. Attempted versions from libicuio.so.100 to
libicuio.so.49`; `runmqckm` may not even exist on disk. Creating a TLS keystore
(`runmqakm -keydb -create`) is impossible. `setmqenv -s` does **not** fix it
(it sets an empty `LD_LIBRARY_PATH`).

**Cause.** IBM MQ 9.4.5 ships **GSKit 9** as **tarballs** the base rpm install
does not unpack: `MQSeriesGSKit` lays down only `/opt/mqm/gskit9/gskssl32.tar.gz`
and `gskssl64.tar.gz` — there is no extracted lib tree, so no `libicuio`. Base
queue-manager operations (`crtmqm`, `strmqm`, `dspmq`, plaintext Native HA /
plaintext CRR) don't touch GSKit, so the gap is invisible until you do anything
TLS (channel certs, Native HA CRR over TLS, `mqweb`).

**Fix.** Initialize GSKit before any TLS work — the supported trigger is a
GSKit-using MQ operation / the GSKit install step, not a manual `tar -x`. Treat
"extract/initialize GSKit" as an explicit provisioning task in any role that
configures TLS (it is a named task in the Native HA Phase-3 plan), and verify with
`runmqakm -version` succeeding before issuing certs. Discovered in the #246
Phase-0 spike (the entitlement probe deliberately ran plaintext to sidestep it).
