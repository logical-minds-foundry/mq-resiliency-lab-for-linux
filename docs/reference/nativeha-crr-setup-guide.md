# IBM MQ Native HA + Cross-Region Replication (CRR) — Manual Setup Guide

> Scope: IBM **MQ 9.4** Advanced for Developers on **RHEL 9.6**. A single queue
> manager, **QMNATIVE**, running as a **Native HA group of three** instances in
> each of two regions, with **Cross-Region Replication (CRR)** linking the two
> groups and the replication link secured with **TLS**. This is the "3+3" shape:
> a synchronous three-node HA group in the live region, asynchronously replicated
> to a standby three-node group in the recovery region.
>
> This is a **bare-hand walkthrough** — every step is the manual equivalent of
> what the lab automation performs, so the setup is understandable and
> supportable without it. The lab's example host names and addresses appear
> throughout; **substitute your own.** Section 8 maps each manual step to the
> automation that performs it.
>
> TLS specifics — which certificates are required, the naming conventions, and
> the keystore lifecycle — are covered in the companion
> [Native HA / CRR TLS reference](nativeha-crr-tls-guide.md). This guide shows
> *where* TLS plugs into the build; that guide explains the certificates
> themselves.
>
> Sources: `ansible/roles/mq-nativeha/tasks/{main,tls,crr}.yml`,
> `ansible/site-nativeha.yml`, `ansible/site-nativeha-dr.yml`,
> `ansible/roles/lab-pki`, `docs/reference/lab-gotchas.md`,
> `src/mqlab/nativehastate.py`.

## 1. Topology

QMNATIVE exists as **six instances of one queue manager** — three per region.
All six share the same queue-manager name, the same configuration, and (for TLS)
the same identity. Only one instance is *active* (running the queue manager) at a
time; the rest are in-sync replicas ready to take over.

| Region | Role | Instances | HA replication NIC (`net-hb`) | Data NIC | CRR NIC (`net-wan`) |
|---|---|---|---|---|---|
| Region A | **Live** group | `nha-rhel-a1`, `a2`, `a3` | `172.16.1.91–93` | `10.10.1.91–93` | `10.99.0.91–93` |
| Region B | **Recovery** group | `nha-rhel-b1`, `b2`, `b3` | `172.16.2.91–93` | `10.10.2.91–93` | `10.99.0.94–96` |

Two replication paths run on **dedicated NICs**, each on its own port:

| Path | Scope | NIC | Port |
|---|---|---|---|
| **Native HA** (synchronous, intra-region) | within one 3-node group | `net-hb` | **9414** |
| **CRR** (asynchronous, cross-region, TLS) | Live group → Recovery group | `net-wan` | **9415** |

Native HA inside a group keeps the three local instances in lock-step and
elects a leader on failure. CRR ships that group's recovered data across the WAN
to the standby group; it is asynchronous by necessity (the recovery region is
far enough away that synchronous replication is not viable) and **secured with
TLS** end to end.

## 2. Prerequisites (every instance)

| Item | Requirement |
|---|---|
| OS | RHEL 9.6 x86_64 |
| IBM MQ | **9.4.4 CD or later** — the off-container Native HA floor. Verify with `dspmqver -b -f 2`. |
| Entitlement | A licence that unlocks Native HA **and** CRR on Linux (the developer licence does). |
| Three NICs | `net-hb` (HA replication), data (the QM listener, port 1414), and `net-wan` (CRR). |
| Name resolution | `/etc/hosts` entries for all instance names in the local group (no DNS needed). |
| `libicu` (TLS only) | `runmqakm` needs the OS ICU runtime — **`dnf install libicu`** before any keystore operation (see §9). |
| PKI material (TLS only) | The QMNATIVE PKCS#12 keystore + its password — see the [TLS reference](nativeha-crr-tls-guide.md). |

Do the install and queue-manager creation on **all six** instances. Form the
Live group first, then the Recovery group; they are configured identically until
the CRR step (§6) distinguishes them.

## 3. Form a local HA group (plaintext)

Bring up one three-node Native HA group at a time. The steps below are for the
Region A (Live) group; repeat verbatim for Region B (Recovery), substituting its
host names and `net-hb-b` addresses.

### 3.1 Create the Native HA instance (on each node)

Run on each node, using **that node's own name** as the instance name:

```bash
# On nha-rhel-a1 (repeat on a2, a3 with their own names)
crtmqm -lr nha-rhel-a1 -lf 8192 -lp 10 -ls 10 -p 1414 QMNATIVE
```

- `-lr <name>` — create a Native HA (log-replicated) instance; `<name>` is this
  instance's name, conventionally the host name.
- `-lf 8192 -lp 10 -ls 10` — log file pages, primary and secondary log files.
- `-p 1414` — the queue-manager listener port.
- `QMNATIVE` — the queue-manager name (identical on all instances).

### 3.2 Declare the group's instances in `qm.ini` (on each node)

Add the **same** `NativeHAInstance` block — one stanza per group member — to
`/var/mqm/qmgrs/QMNATIVE/qm.ini` on every node. Replication runs on the
dedicated `net-hb` NIC, port **9414**:

```ini
NativeHAInstance:
   Name=nha-rhel-a1
   ReplicationAddress=172.16.1.91(9414)
NativeHAInstance:
   Name=nha-rhel-a2
   ReplicationAddress=172.16.1.92(9414)
NativeHAInstance:
   Name=nha-rhel-a3
   ReplicationAddress=172.16.1.93(9414)
```

### 3.3 Start each instance under systemd

Native HA instances are **not** started with `strmqm`/`endmqm`. They run under
the `mqmonitor@` systemd template (a liveness monitor that restarts a failed
instance), shipped in the MQ samples:

```bash
ln -s /opt/mqm/samp/mqmonitor@.service /etc/systemd/system/mqmonitor@.service
systemctl daemon-reload
systemctl enable --now mqmonitor@QMNATIVE
```

> **Do not `endmqm` a Native HA instance** — systemd would simply restart it.
> Lifecycle is managed through the `mqmonitor@QMNATIVE` unit.

### 3.4 Verify the group formed

```bash
su - mqm -c "dspmq -m QMNATIVE -o nativeha -x"
```

A healthy three-node group reports `QUORUM(3/3)`, with one instance
`ROLE(Active)` (running the queue manager), the other two `ROLE(Replica)`, and
every instance `INSYNC(yes) HASTATUS(Normal)`.

At this point the group replicates in **plaintext** on `net-hb`. TLS is added
next, before CRR.

## 4. Add TLS to the keystore (on each node)

TLS secures the **CRR** link (§6). Configure it on every instance of both groups
before enabling CRR. The certificates and their naming are detailed in the
[TLS reference](nativeha-crr-tls-guide.md); the on-host mechanics are:

```bash
# 1. ICU runtime for runmqakm (see §9)
dnf install -y libicu

# 2. The per-QM ssl directory
install -d -o mqm -g mqm -m 0750 /var/mqm/qmgrs/QMNATIVE/ssl

# 3. Place the QMNATIVE PKCS#12 keystore (from the PKI provider)
install -o mqm -g mqm -m 0640 QMNATIVE.p12 \
    /var/mqm/qmgrs/QMNATIVE/ssl/QMNATIVE.p12

# 4. Stash the keystore password so the replication process can open it
su - mqm -c "/opt/mqm/bin/runmqakm -keydb -stashpw -type pkcs12 \
    -db /var/mqm/qmgrs/QMNATIVE/ssl/QMNATIVE.p12 -pw '<KEYSTORE_PASSWORD>'"
# -> creates /var/mqm/qmgrs/QMNATIVE/ssl/QMNATIVE.sth
```

> **The stash file is the key TLS detail for Native HA.** The replication
> process reads the keystore via the `KeyRepository` stem and needs the password
> from a **`.sth` stash file** — unlike a queue manager's *channel* TLS, where
> `SSLKEYR` points at the PKCS#12 and the password comes from the `KEYRPWD`
> queue-manager attribute (no stash). Same keystore, two different password
> mechanisms. See the [TLS reference](nativeha-crr-tls-guide.md#5-keystore-lifecycle).

Then declare the TLS settings in `qm.ini` (on each node):

```ini
NativeHALocalInstance:
   CipherSpec=ANY_TLS12
   CertificateLabel=QMNATIVE
   KeyRepository=/var/mqm/qmgrs/QMNATIVE/ssl/QMNATIVE
```

- `CipherSpec=ANY_TLS12` — negotiate any supported TLS 1.2 cipher (in the lab
  this lands on `ECDHE_RSA_AES_256_GCM_SHA384`).
- `CertificateLabel=QMNATIVE` — the label of the personal certificate inside the
  keystore (= the certificate CN; see the TLS reference).
- `KeyRepository=…/QMNATIVE` — the keystore **stem**, no extension; GSKit appends
  `.p12` and `.sth`.

## 5. (Recovery group) Form the second group

Repeat §3 and §4 on the Region B nodes (`nha-rhel-b1–b3`), using the `net-hb-b`
addresses (`172.16.2.91–93`) in the `NativeHAInstance` stanzas. The same queue
manager name (`QMNATIVE`) and the same keystore are used — the Recovery group is
a second, independent three-node group of the *same* queue manager.

## 6. Enable Cross-Region Replication (TLS)

CRR turns the two independent HA groups into a live/recovery pair. On **every
instance of both groups**, extend `NativeHALocalInstance` with the group's
identity and add a `NativeHARecoveryGroup` stanza pointing at the *peer* group
over `net-wan`, port **9415**.

### 6.1 Live group (Region A) — replicate to Recovery

Replace the TLS-only `NativeHALocalInstance` from §4 with the full stanza, and
add the recovery-group pointer:

```ini
NativeHALocalInstance:
   CipherSpec=ANY_TLS12
   CertificateLabel=QMNATIVE
   KeyRepository=/var/mqm/qmgrs/QMNATIVE/ssl/QMNATIVE
   GroupName=Live
   GroupRole=live
   GroupLocalAddress=(9415)

NativeHARecoveryGroup:
   GroupName=Recovery
   ReplicationAddress=10.99.0.94(9415),10.99.0.95(9415),10.99.0.96(9415)
   Enabled=Yes
```

### 6.2 Recovery group (Region B) — receive from Live

```ini
NativeHALocalInstance:
   CipherSpec=ANY_TLS12
   CertificateLabel=QMNATIVE
   KeyRepository=/var/mqm/qmgrs/QMNATIVE/ssl/QMNATIVE
   GroupName=Recovery
   GroupRole=Recovery
   GroupLocalAddress=(9415)

NativeHARecoveryGroup:
   GroupName=Live
   ReplicationAddress=10.99.0.91(9415),10.99.0.92(9415),10.99.0.93(9415)
   Enabled=No
```

Notes on the fields:

- `GroupName` / `GroupRole` — each group names itself and declares its role
  (`live` vs `Recovery`). The `NativeHARecoveryGroup` stanza names the **peer**.
- `GroupLocalAddress=(9415)` — the local CRR listen port on `net-wan`.
- `Enabled=Yes` on the Live group means "replicate outward to Recovery";
  `Enabled=No` on the Recovery group means it receives but does not push back.
  This asymmetry is what makes one side live and the other standby.
- Because both groups are the *same* queue manager presenting the *same* TLS
  identity, the CRR link is QMNATIVE authenticating QMNATIVE — see the
  [TLS reference](nativeha-crr-tls-guide.md#3-the-shared-identity-model).

### 6.3 Restart and verify

Restart the monitor on every instance to apply TLS + CRR — **Recovery group
first, then Live**:

```bash
systemctl restart mqmonitor@QMNATIVE
```

Verify the cross-region link:

```bash
su - mqm -c "dspmq -m QMNATIVE -o nativeha -g"
```

Both groups report `GRPROLE` and `GRSTATUS`. The **Recovery** group line carries
the cross-region facets: `CONNGRP(yes)` (connected to the peer), `INSYNC(yes)`
(caught up), and `BACKLOG(<n>)` (a message count, not seconds). A healthy link
shows the Recovery group connected and in sync with a small, draining backlog.

> **Role reporting nuance.** In `-o nativeha -x`, the Live group's leader reports
> `ROLE(Active)` (it is running the queue manager); the Recovery group's leader
> reports `ROLE(Leader)` (it is applying CRR replication). Both are healthy —
> `ROLE(Leader)` on the standby side is **not** a fault.

## 7. Switchover and failback

Promoting the Recovery group to live (and later returning) is a **manual,
operator-driven** change: on every instance of both groups, edit the `GroupRole`
in `NativeHALocalInstance` (and flip `Enabled` on the `NativeHARecoveryGroup`
stanzas so the new live side replicates outward), then restart
`mqmonitor@QMNATIVE`. There is no automatic cross-region failover — CRR is a
deliberate, declared cutover.

In the lab this manual procedure is wrapped by `mqlab dr cutover` and
`mqlab dr failback`, which perform exactly the `qm.ini` role edit + restart
described above across all instances.

## 8. How the lab automates this

Every manual step above is performed by the `mq-nativeha` Ansible role, driven by
the `site-nativeha*` playbooks. The mapping:

| Manual step | Automation |
|---|---|
| §2 install + MQ-level assert | `mq-nativeha/tasks/main.yml` (+ `install-RedHat.yml`) |
| §3.1 `crtmqm -lr` | `mq-nativeha/tasks/main.yml` ("create the Native HA instance") |
| §3.2 `NativeHAInstance` stanzas | `mq-nativeha/tasks/main.yml` ("append the NativeHAInstance peer set") |
| §3.3 `mqmonitor@` systemd | `mq-nativeha/tasks/main.yml` (link + enable the unit) |
| §3 form Live group | `ansible/site-nativeha.yml` |
| §4 TLS keystore + stash + stanza | `mq-nativeha/tasks/tls.yml` |
| §5 form Recovery group | `ansible/site-nativeha-dr.yml` (host group `nha_rhel_b`) |
| §6 CRR group stanzas | `mq-nativeha/tasks/crr.yml` |
| §6.3 ordered restart | `ansible/site-nativeha-dr.yml` (final play) |
| §7 switchover / failback | `mqlab dr cutover` / `mqlab dr failback` |

The HA-group formation in `main.yml` is shared **verbatim** across RHEL and
Ubuntu — that thin OS-adapter boundary is one of the things the lab is built to
demonstrate.

## 9. Troubleshooting

### `runmqakm` fails with `libicuio` — install `libicu`

**Symptom.** `runmqakm` (and `runmqckm`) fail immediately with
`Failed to dlopen ICU library. Attempted versions from libicuio.so.100 to
libicuio.so.49`. No keystore can be created or stashed, so all TLS work is
blocked. Plaintext Native HA/CRR and base QM operations are unaffected, so this
stays invisible until the TLS step.

**Cause.** `runmqakm` `dlopen`s the **system** `libicuio.so.*` at runtime (not a
link-time dependency, so `ldd` shows nothing), and a minimal RHEL 9.6 install
does **not** pull in `libicu` (it is not a hard MQ RPM dependency). GSKit's own
libraries are present; the missing piece is the OS ICU.

**Fix.** `dnf install libicu`. RHEL 9 ships ICU 67
(`/usr/lib64/libicuio.so.67`), inside `runmqakm`'s accepted 49–100 range, so it
works immediately. The TLS step (§4) installs `libicu` first for this reason.

### Replication won't establish over TLS

Confirm, on each node: the `.p12` **and** the `.sth` stash file both exist in
`/var/mqm/qmgrs/QMNATIVE/ssl/` and are owned by `mqm`; the `KeyRepository` stem
matches the file name without extension; and the keystore uses a GSKit-readable
PKCS#12 encoding (the lab pins `compatibility2022` — see the TLS reference).
Verified working in the lab: replication negotiates
`ECDHE_RSA_AES_256_GCM_SHA384` once the stash is in place.
