# Lab Gotchas Playbook (libvirt / Vagrant / Pacemaker)

> Symptom → cause → fix, from direct experience. The Pacemaker entries extend
> the Phase D findings; the libvirt/Vagrant/worktree ones are new from #67.

## Contents

- [Vagrant leaves orphaned extra-disk volumes](#vagrant-leaves-orphaned-extra-disk-volumes)
- [Concurrent same-box boot trips a Vagrant machine lock](#concurrent-same-box-boot-trips-a-vagrant-machine-lock)
- [macOS bootstrap stops before booting: "huge-page reservation SHORT"](#macos-bootstrap-stops-before-booting-huge-page-reservation-short)
- [Fences and failovers when the host is CPU-starved](#fences-and-failovers-when-the-host-is-cpu-starved)
- [Can't live-attach a NIC ("No more available PCI slots")](#cant-live-attach-a-nic-no-more-available-pci-slots)
- [Driving the lab from a worktree](#driving-the-lab-from-a-worktree)
- [build/ artifacts missing in a worktree](#build-artifacts-missing-in-a-worktree)
- [Ansible silently does nothing (empty inventory)](#ansible-silently-does-nothing-empty-inventory)
- [Pacemaker operational settings that matter](#pacemaker-operational-settings-that-matter)
- [RDQM DR cold-create: crtmqm mkfs I/O error on the DR device](#rdqm-dr-cold-create-crtmqm-mkfs-io-error-on-the-dr-device)
- [RDQM bake: kmod WARNING or "Unsupported kernel release"](#rdqm-bake-kmod-warning-or-unsupported-kernel-release)
- [Bake fails "No space left on device" unpacking into /tmp](#bake-fails-no-space-left-on-device-unpacking-into-tmp)
- [RDQM crtmqm fails AMQ3817E: "no STONITH resources have been defined"](#rdqm-crtmqm-fails-amq3817e-no-stonith-resources-have-been-defined)
- [nic-config fails: a lab NIC "did not keep its hardening"](#nic-config-fails-a-lab-nic-did-not-keep-its-hardening)

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
`nha-rhel-crr-*` on `mq-nativeha-rhel9`, or the three `mq-client-ubuntu26` commons) race
on staging that box's base volume into the libvirt pool the first time it's
used. The concurrent clones trip Vagrant's per-machine lock. Once the volume is
staged, later clones from it are fine.

**Fix (built in, #859).** The vms phase detects a batch whose guests share a box
and issues that batch `vagrant up --no-parallel` — serial *within the batch*, so
the first boot stages the shared volume before the next clones it. Batches whose
guests all boot **distinct** boxes keep the parallel default (distinct volumes
don't contend), preserving the `boot_batch` speed dial (#638). If you ever drive
`vagrant up` by hand across same-box nodes, add `--no-parallel` yourself.

## macOS bootstrap stops before booting: "huge-page reservation SHORT"

**Symptom.** On the macOS dev VM, `mqlab bootstrap` (or `mqlab commons up`)
fails in its `reserve huge pages` step, before any `vagrant up`, with
`huge-page reservation SHORT: needed <N> free 2 MiB pages … got <M> of <N>`.

**Cause.** The `macos` profile backs every guest's RAM with 2 MiB huge pages
(#1241), because 4 KiB backing made arm64 nested guests' page faults hundreds of
times slower under a memory-churning load (#1240). A huge-page-backed guest
can't boot without free huge pages, so `mqlab` reserves them first, about
22 GiB for `nativeha-ubuntu --no-dr`. If the Vergil VM's RAM is too
fragmented or in use, the kernel can't assemble that many even after the
built-in `drop_caches` and `compact_memory` retry. `mqlab` never falls back to
4 KiB backing.

**Fix.** Free Vergil VM memory (stop other workloads, or tear down a stack you
don't need) and re-run `bootstrap`. Check the pool with
`grep HugePages_ /proc/meminfo`. If a failed run left pages reserved with no
guests running, release them from `ansible/` with
`ansible-playbook host-hugepages.yml -c local -i localhost, -e hugepages_release=true`.
Details: [`perf-and-staging.md` §5.1](../development/perf-and-staging.md#51-huge-page-backed-guest-ram-macos).

## Fences and failovers when the host is CPU-starved

**Symptom.** A healthy, idle stack starts misbehaving on its own, with nothing
driving it:

- Pacemaker fences nodes. Corosync logs `Corosync main process was not scheduled
  for N ms` and `token timed out`.
- Native HA fails over. Replicas log `AMQ3247W` ("active instance … has not sent
  anything"), and the active instance logs slow log writes (`AMQ6729W`) and
  `AMQ6176W`.
- Guests go briefly unreachable (`No route to host`).

It hits both sites and the commons at once, including guests doing nothing.

**Cause.** The host is out of CPU, and the nested lab guests stop being scheduled
for seconds at a time (#1602). Corosync and Native HA then correctly conclude that
a peer is dead. Nothing is wrong with the lab itself. On the macOS dev VM this
happens when the Mac's cores are oversubscribed. In #1602 two VMs were running,
`vergil-user` with 12 vCPUs and the lab VM with 24, on 18 cores, and the Mac sat
at 100% CPU. Every nested guest's exit goes through macOS, so the nested guests
froze for 3–163 s while the Lima VM itself only slowed down. Guest kernels log
`watchdog: BUG: soft lockup - CPU#n stuck for Ns!`, often on `swapper` (the idle
task), and `rcu: INFO: rcu_preempt detected stalls`. A guest whose idle CPUs are
"stuck" is not overloaded; it isn't being run at all.

This is an accepted constraint of running the whole lab on one workstation, not a
defect to tune away. Longer corosync or Native HA timeouts would only hide it,
because no sane timeout survives a 20–160 s freeze.

**Confirm.** `mqlab vm starvation` reads every running guest's kernel log for
those two signatures and fails loud, naming the guests and the window. Narrow it
to the incident with `--since` (journalctl syntax, for example
`--since "2026-10-17 11:00"` or `--since "30 min ago"`). The kernel reports a soft
lockup only after about 20 s, so the check catches the severe freezes. Shorter ones
(corosync's 3.65 s token is enough to fence) show only as the corosync and
`AMQ3247W` warnings above, which usually come alongside severe ones.

**Fix.** Free host CPU. On macOS, stop or idle the other VMs and heavy apps while
a lab stack is up. Then re-check a fresh window with
`mqlab vm starvation --since "10 min ago"`. The lab recovers by itself: fenced
nodes rejoin, Native HA resyncs. Re-run or resume whatever was interrupted
(`mqlab bootstrap <stack> --from <phase>`). Treat a result taken during
starvation, such as a timing, a failover count or a validation run, as void
rather than as a lab finding.

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

## RDQM bake: kmod WARNING or "Unsupported kernel release"

**Symptom.** Either of two messages from `rdqm-install` while baking `mq-rdqm-rhel9`
(or running the whole role on a fresh RHEL box):

- a `WARNING: kernel 5.14.0-570.12.1.el9_6.x86_64 is in IBM's RDQM range of kernel
  versions with known compatibility issues (5.14.0-570.12.1 -> 5.14.0-570.15.1)` line,
  after which the install carries on; or
- the task "ask IBM's modver which drbd kmod fits the running kernel" fails with
  `Unsupported kernel release.`, and nothing is installed.

**Cause.** The role asks IBM's `modver` helper, shipped in the MQ media's
`Advanced/RDQM/PreReqs/el<N>/kmod-drbd-9/` directory, which `kmod-drbd` rpm fits the
running kernel. That is the step IBM's
[10.0 RDQM install doc](https://www.ibm.com/docs/en/ibm-mq/10.0.x?topic=multiplatforms-installing-rdqm-replicated-data-queue-managers)
describes (#1408). `modver` matches by kernel family, so a kernel with no same-release
kmod still gets one (`5.14.0-687.5.3` gets `_687.5.1`). A kernel outside every family
the media knows gets "Unsupported kernel release." and exit 1; the role fails there, with
no fallback.

The WARNING is separate. `modver` does not know about IBM's
[known-compatibility-issue table](https://www.ibm.com/support/pages/node/1087143),
which lists RHEL 9 kernels `5.14.0-570.12.1 -> 5.14.0-570.15.1` as "known to require a
new kernel module". The lab's RHEL 9.6 GA kernel, `570.12.1`, is in that range, so every
bake from the 9.6 DVD prints the WARNING. The role checks a dated copy of the table
(`ansible/roles/rdqm-install/vars/RedHat-9.yml`), because the guest is offline and IBM
publishes the table only on that HTML page; the `ibm-mq-rdqm-kmods.json` feed has no
known-issue field.

**Fix.** For the WARNING on the 9.6 GA kernel: none needed. The lab has run RDQM on it
since Phase C. Treat it as a reason to move the box to a newer kernel (a newer DVD, then
a rebake), not to suppress it. For "Unsupported kernel release.": the MQ media is older
than the kernel. Use an MQ level (or IBM's Fix Central kmod bundle) whose `modver` knows
that kernel family, or bake from a supported RHEL point release. When IBM's table
changes, refresh `rdqm_kernel_known_issues` and its `_as_of` date in the vars file.

**RHEL 10.** `mq-rdqm-rhel10` uses the same mechanism with the `el10` media, which
only MQ 10.0.1 and later ship. Its dated table copy
(`ansible/roles/rdqm-install/vars/RedHat-10.yml`, as of 2026-10-13) is empty, because
IBM's known-issue table has no RHEL 10 row. On MQ 10.0.0.x, a RHEL 10 bake has no
`el10` kmods to pick from.

## Bake fails "No space left on device" unpacking into /tmp

**Symptom.** An Ubuntu 26.04 bake (first seen on `obs-ubuntu26`) fails at a download
or unpack task, for example `prometheus : download + unpack prometheus`, with
`tar: ... Cannot write: No space left on device`, while `/` still has gigabytes free.

**Cause.** Ubuntu 26.04 mounts `/tmp` as tmpfs sized at 50% of RAM. `tmp.mount` ships
enabled (`What=tmpfs`, `Options=...,size=50%%,...`), so a 1.9 GB build VM gets a 974M
`/tmp`. Check with `findmnt /tmp` and `df -h /tmp`. The roles used to stage there, and
node_exporter, logcli, loki, prometheus and alloy filled it before the multi-GB MQ
tarball was even copied. Ubuntu 24.04 and RHEL keep `/tmp` on disk, so this never
showed on them.

**Fix.** Do not mask `tmp.mount`: the tmpfs default stays. Since #1417 every role
stages large artifacts under `lab_stage_dir` (`/var/tmp/lab-staging`, on disk), made at
the point of use by `ansible/tasks/lab-stage-dir.yml`, and removes them once installed.
If this error comes back, a new task is staging in `/tmp`. `tests/test_stage_off_tmpfs.py`
should already have failed on it; move the task to `{{ lab_stage_dir }}`, not onto the
allow-list. See
[box-bake-manifest.md](../development/box-bake-manifest.md#staging-large-artifacts-go-to-lab_stage_dir-never-tmp-1417).

## RDQM crtmqm fails AMQ3817E: "no STONITH resources have been defined"

**Symptom.** An RDQM provision fails at "create the RDQM queue manager". `crtmqm` on
one site reports AMQ3817E (`crm configure load update ... failed`, with "Resource
start-up disabled since no STONITH resources have been defined"), then AMQ3813E. The
failure is intermittent: the same row can pass on a re-run. It is not OS-specific
(first seen on `rdqm-rhel@9` in #1475).

**Cause.** RDQM does a site's cluster-wide setup, `stonith-enabled=false` included,
in whichever `rdqmadm -c` holds the `rdqm-lock` cluster attribute. From
`RDQM.0.LOG`, the lock is a query, an update, then a read-back that only checks the
attribute exists. Two nodes that start `rdqmadm -c` within milliseconds both "hold" it,
and both skip the setup (#1488). The role used to run the step on all three nodes at
once. Check with
`crm_attribute --type crm_config --name stonith-enabled --query` on each node: a site
that hit this has no value set (rc 105).

**Fix.** Since #1488 the `rdqm-ha` role runs `rdqmadm -c` with `throttle: 1`, one node
at a time, and then fails loud unless every node reads `stonith-enabled` as `false`. If
the new check fails, RDQM's own setup was skipped: tear the stack down and bootstrap
again. Do not set the property by hand and carry on. When you run RDQM by hand, run
`rdqmadm -c` on one node at a time (see the
[RDQM cheat sheet](rdqm-ha-cheatsheet.md#2-form-the-rdqm-ha-group-all-3-nodes)).

## nic-config fails: a lab NIC "did not keep its hardening"

**Symptom.** Provisioning fails in `lab/scripts/nic-config.sh` with nmcli's own error
from `nmcli connection modify`, or with
`nic-config: <conn> did not keep its hardening (autoconnect-priority=…, autoconnect-retries=…)`.
Before #1491 the same fault was silent. On `rdqm-rhel@10` nodes, the journal showed
NetworkManager `failed rename ... Permission denied` lines, with a matching
`avc: denied { unlink }`, and the profiles kept `autoconnect-priority -100`.

**Cause.** Vagrant writes the lab NIC profiles during `vagrant up`, and on the nodes
seen they carried the SELinux label `user_tmp_t`, not NetworkManager's own
`NetworkManager_etc_rw_t`. Under enforcing, NetworkManager cannot replace a
`user_tmp_t` file, so every `nmcli connection modify` failed. The old script ran the
modify with `|| true`, so the #866 hardening was missing with nothing reported.
(Judgment: the label fits a file written under `/tmp` and moved into place.)

**Fix.** Since #1491 `nic-config.sh` runs `restorecon` on the NetworkManager profile
directories before it hardens anything, when SELinux is enabled. It lets a failed modify
fail the step, and reads back `autoconnect-priority` and `autoconnect-retries`. If it
fails anyway, check the profiles' labels with `ls -Z` and the journal for AVCs.

## Channels to SVCQM stall for minutes after an unplanned HA failover

**Symptom.** After an app QM fails over because its node hung (in the lab: `virsh
suspend` on the HA Primary), the QM runs on the new node within about 27 s, but every
app round-trip fails with `MQRC_NO_MSG_AVAILABLE` (2033) for about 6 minutes. SVCQM's
`AMQERR01.LOG` shows `AMQ9514E: Channel '<APPQM>.SVCQM' is in use` every ~25 s, and later
`AMQ9259E … select() [TIMEOUT] 360 seconds`. `SVCQM.<APPQM>` stays `RUNNING` to the dead
node while replies pile up on its transmission queue. A planned move or a process kill
does not show this, because it resets the TCP connections.

**Cause.** A hung node sends no TCP reset, so SVCQM keeps both channel instances to the
dead node until the receive timeout ends them: HBINT + 60 s, or 360 s at the default
HBINT(300) (2 × HBINT below 60 s). Meanwhile:
- The new active's sender comes from another node's address. With MQ's defaults
  (`AdoptNewMCA=NO`, `AdoptNewMCACheck=NAME,ADDRESS,QM`) it cannot take over the stale
  receiver, so it is refused.
- The reply sender only times out once it is waiting on its partner, so it recovers about
  2 × HBINT after the first reply it tries to send.

**Fix (#1557).**
- The `mq-qmgr` role sets SVCQM's qm.ini to `AdoptNewMCA=ALL`, `AdoptNewMCACheck=NAME,QM`
  and `KeepAlive=Yes`, and restarts the QM when they change. That is IBM's suggested set,
  with `ADDRESS` dropped because it cannot match after a move to another host. The
  channels' mutual TLS and `SSLPEER` are the protection IBM names for that.
- Every inter-QM `SDR` and `RCVR` sets `HBINT(10)` (`inter_qm_hbint`) on both ends, since
  MQ uses the larger value.

Measured on `rdqm-rhel@10`: the new sender adopted the stale receiver about 1 s after the
QM restart. The reply sender was back on the new node about 40 s after the freeze, and
e2e x20 run straight after the failover got 19/20, with only the first request lost.
