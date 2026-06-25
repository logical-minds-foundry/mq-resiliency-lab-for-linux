# Native HA / CRR TLS Reference — QMNATIVE

> Scope: the **TLS/SSL requirements for securing the Cross-Region Replication
> (CRR) link** of the QMNATIVE Native HA queue manager. This is the companion to
> the [Native HA + CRR setup guide](nativeha-crr-setup-guide.md): that guide
> shows *where* TLS plugs into the build; this one specifies *which* certificates
> are required, *how they are named*, and *how the keystore is produced and
> deployed*.
>
> Scoped to **QMNATIVE only** — the Native HA / CRR arm. It is deliberately not a
> lab-wide certificate inventory.
>
> The lab's example names and paths appear throughout; **substitute your own.**
>
> Sources: `ansible/roles/lab-pki/tasks/{entity,ca}.yml`,
> `ansible/roles/lab-pki/defaults/main.yml`, `ansible/vars/pki-entities.yml`,
> `ansible/roles/mq-nativeha/tasks/{tls,crr}.yml`,
> `docs/reference/lab-gotchas.md`.

## 1. What TLS secures here

CRR replicates the live region's queue-manager data across the WAN to the
standby region (on `net-wan`, port 9415). That link carries the full message and
state stream between regions, so **it is secured with TLS** — encrypted in
transit and mutually authenticated at both ends.

The intra-region Native HA replication (within a single three-node group, on
`net-hb`, port 9414) is a separate path; this reference is about the
**cross-region** CRR link, which is the one carrying traffic across a trust
boundary.

The cipher is configured as **`CipherSpec=ANY_TLS12`** — both ends negotiate any
mutually supported TLS 1.2 cipher. In the lab this consistently negotiates
**`ECDHE_RSA_AES_256_GCM_SHA384`** (forward-secret, AES-256-GCM).

## 2. Required certificates

CRR mutual TLS between the two QMNATIVE groups needs exactly two pieces of PKI
material, both delivered inside one PKCS#12 keystore:

| Item | Subject / identity | Purpose | Source |
|---|---|---|---|
| **QMNATIVE personal certificate** (+ private key) | `CN=QMNATIVE, OU=messaging, O=app-org` | The identity each end **presents** on the CRR link | Issued by the app-org CA; RSA 4096-bit, ~730-day validity |
| **app-org Root CA certificate** | `CN=app-org Root CA, O=app-org` | The signer each end **trusts** to validate the peer's certificate | The app-org root CA |

That is the entire CRR trust requirement. Because the link is mutually
authenticated and both ends are the same queue manager (§3), the certificate a
side *presents* and the certificate it *validates* are issued by the same CA — so
trusting the single **app-org Root CA** is sufficient for the peer end of CRR.

> In the lab the QMNATIVE keystore also carries the **svc-org** CA, because the
> same queue manager additionally runs cross-org application channels to a
> service partner. That trust is **not** part of CRR — for the CRR link, only the
> app-org material above is in play.

## 3. The shared-identity model

All six instances — three in the live region, three in the recovery region — are
instances of the **one queue manager QMNATIVE**. They share the queue-manager
name and they share the **same TLS identity**: the *same* `QMNATIVE.p12`
keystore, presenting the *same* `CN=QMNATIVE` certificate, is deployed to every
instance.

This makes the CRR handshake **QMNATIVE authenticating QMNATIVE**: each end
presents the QMNATIVE certificate and validates the peer's QMNATIVE certificate
against the shared app-org CA. There is no separate "live" or "recovery"
certificate — the regions are distinguished by their `qm.ini` group role, not by
their identity. One certificate, one keystore, deployed six times.

## 4. Naming conventions

The PKI follows a single, regular scheme so that certificates never need
reissuing as the topology grows:

| Convention | Value for QMNATIVE |
|---|---|
| **DN scheme** | `O=<org>, OU=<service>, CN=<entity>` |
| **Distinguished name** | `O=app-org, OU=messaging, CN=QMNATIVE` |
| **Certificate label** | `QMNATIVE` — the label **equals the CN** (the PKCS#12 `friendly_name`), and is what `CertificateLabel` in `qm.ini` references |
| **Keystore file** | `QMNATIVE.p12` |
| **Password stash file** | `QMNATIVE.sth` |
| **`KeyRepository` stem** | `…/QMNATIVE` — the path **without** extension; GSKit appends `.p12` / `.sth` |

Two conventions matter most in practice:

- **Certificate label = CN = keystore friendly name.** A queue manager finds its
  personal certificate inside the keystore by label; keeping the label identical
  to the CN (and to the PKCS#12 friendly name) removes a whole class of
  "certificate not found" misconfigurations.
- **`O` and `OU` are stable; `CN` identifies the entity.** Messaging queue
  managers share `O=app-org, OU=messaging` and are distinguished only by `CN`.
  This lets a single peer rule match on `O`/`OU` while each queue manager keeps a
  distinct `CN` — the standard partial-DN onboarding pattern.

### On-host layout

The keystore lands in the queue manager's own `ssl` directory on every instance:

```text
/var/mqm/qmgrs/QMNATIVE/ssl/
├── QMNATIVE.p12      # PKCS#12 keystore: QMNATIVE cert + key + trusted CA(s)
└── QMNATIVE.sth      # stashed keystore password (created by runmqakm)
```

And is referenced from `qm.ini` by stem:

```ini
NativeHALocalInstance:
   CipherSpec=ANY_TLS12
   CertificateLabel=QMNATIVE
   KeyRepository=/var/mqm/qmgrs/QMNATIVE/ssl/QMNATIVE
```

## 5. Keystore lifecycle

### 5.1 Issue (PKI provider, once per certificate)

The QMNATIVE keystore is produced by the lab's certificate-authority provider on
the controller — never by hand:

1. Generate the QMNATIVE private key (RSA 4096-bit).
2. Build a CSR with `CN=QMNATIVE, OU=messaging, O=app-org`.
3. Sign it with the **app-org** root CA → the QMNATIVE personal certificate
   (~730-day validity).
4. Assemble a PKCS#12 keystore (`friendly_name=QMNATIVE`) containing the
   certificate, its private key, and the CA trust chain.

**Encoding — pin it.** The keystore is exported with the **`compatibility2022`**
PKCS#12 encoding. OpenSSL 3.x's modern defaults (AES-256-CBC + SHA-256 MAC) can
be rejected by IBM's GSKit; the compatibility encoding is verified to load
cleanly under GSKit (`runmqakm`). This is the single most likely first-build
blocker if left to defaults.

The issued keystore lives, gitignored, on the controller at:

```text
build/state/secrets/pki/entities/QMNATIVE/QMNATIVE.p12
```

### 5.2 Distribute (every instance)

Copy `QMNATIVE.p12` to `/var/mqm/qmgrs/QMNATIVE/ssl/QMNATIVE.p12` on all six
instances (owner `mqm:mqm`, mode `0640`). The same file goes to every instance —
this is the shared identity of §3.

### 5.3 Stash the password — the key Native HA TLS step

The Native HA replication process opens the keystore via the `KeyRepository`
stem and needs the password from a **stash file**. Create it with `runmqakm`:

```bash
su - mqm -c "/opt/mqm/bin/runmqakm -keydb -stashpw -type pkcs12 \
    -db /var/mqm/qmgrs/QMNATIVE/ssl/QMNATIVE.p12 -pw '<KEYSTORE_PASSWORD>'"
# -> /var/mqm/qmgrs/QMNATIVE/ssl/QMNATIVE.sth
```

> **This is where Native HA TLS differs from channel TLS.** A queue manager's
> *channel* TLS references its keystore through the `SSLKEYR` attribute and takes
> the password from the **`KEYRPWD`** queue-manager attribute — **no stash file**
> (PKCS#12 has no stash in that path). The **Native HA replication** path instead
> reads the keystore at the process level via `KeyRepository` and **requires the
> `.sth` stash**. Same PKCS#12 keystore; two different password mechanisms.
> Miss the stash and replication TLS will not establish.

`runmqakm` depends on the OS ICU runtime — install **`libicu`** first, or the
stash step fails with a `libicuio` `dlopen` error (see the setup guide's
troubleshooting section).

### 5.4 Reference (`qm.ini`)

Point `NativeHALocalInstance` at the keystore stem and certificate label (§4),
restart `mqmonitor@QMNATIVE`, and the CRR link comes up over TLS.

### 5.5 Renewal

Certificates are issued with ~730-day validity. Renewal/rotation is an operator
task (reissue from the provider, redistribute, re-stash, restart) — plan for it
before the validity window lapses; nothing here rotates automatically.

## 6. Quick checklist

- [ ] `libicu` installed on every instance.
- [ ] `QMNATIVE.p12` present in `/var/mqm/qmgrs/QMNATIVE/ssl/` on all six
      instances (same file, `mqm:mqm`, `0640`).
- [ ] `QMNATIVE.sth` stash created next to it via `runmqakm -stashpw`.
- [ ] `qm.ini` `NativeHALocalInstance` has `CipherSpec=ANY_TLS12`,
      `CertificateLabel=QMNATIVE`, `KeyRepository=…/QMNATIVE` (stem, no extension).
- [ ] Keystore exported with a GSKit-readable encoding (`compatibility2022`).
- [ ] After restart, `dspmq -m QMNATIVE -o nativeha -g` shows the Recovery group
      `CONNGRP(yes) INSYNC(yes)` — the TLS CRR link is up.
