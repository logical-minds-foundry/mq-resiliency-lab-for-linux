# Connection TLS — pcmk-ubuntu Arm (Stage 1) — Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Apply mutual TLS + cert authentication to every `pcmk-ubuntu` MQ connection (QM↔QM channels, SVRCONN clients, mqweb/REST) using the merged lab-pki keystores, to unblock NativeHA/CRR.

**Architecture:** Extend the existing Ansible roles (`mq-pcmk-qmgr`, `mq-inter-qm`, `mq-client`, `mq-exporter`, `mqweb`) to (a) distribute each entity's lab-pki PKCS#12 keystore to its host, (b) wire `SSLKEYR`/`KEYRPWD` on each QM, and (c) add `SSLCIPH`/`SSLCAUTH(REQUIRED)`/`SSLPEER` to every channel/SVRCONN and TLS to the pymqi clients + mqweb. Internal authorization is untouched (Stage 2 / #249).

**Tech Stack:** IBM MQ 9.4.5, MQSC, Ansible (`ansible.builtin` only — no galaxy beyond `community.crypto` already in `requirements.yml`), pymqi (MQI client), Liberty (mqweb), the lab-pki provider (`mqlab pki`).

**Spec:** `docs/specs/2026-06-17-connection-tls-pcmk-ubuntu-design.md` (#250).

## Global Constraints

- **TLS 1.3 only** — `SSLCIPH(ANY_TLS13_OR_HIGHER)` on every channel; `sslProtocol="TLSv1.3"` for mqweb. No TLS 1.2 fallback. (Pin the GOV1683-24 suite, e.g. `TLS_AES_256_GCM_SHA384`, only if the load gate demands a concrete spec.)
- **Mutual TLS** — `SSLCAUTH(REQUIRED)` + `SSLPEER` on every channel/SVRCONN; clients present certs.
- **Keystores come from lab-pki** — `build/secrets/pki/entities/<cn>/<cn>.p12`; the in-house `SSLPEER` matches **`O=client-org, OU=clearing-service`** (partial-DN), DTCC side matches **`O=dtcc-org`**.
- **`KEYRPWD` set once + persisted** — sourced from `lab/scripts/lab-secret.sh pki-keyrpwd-<cn>`, set via `ALTER QMGR`, persisted in the QMGR config; QMPCMK's keystore lives on `/mqshared` so it follows the QM on failover. Never committed.
- **No internal authorization** — leave `MCAUSER('mqm')`, `CHLAUTH(DISABLED)`, `CONNAUTH(' ')` (Stage 2 / #249).
- **Validation is functional** — `vrg-validate` has no ansible-lint; the running arm is the gate. Cold-rebuild acceptance gate applies.
- **Arm-agnostic channel MQSC** — author the channel-TLS attrs as a shared snippet/var, not hardcoded per-arm (the #212 seam), so `rdqm-rhel` is a keystore-wiring delta later.

---

## Task 1: Add the DTCC responder entity to the PKI inventory

The SVC.SVRCONN service responder is a SVRCONN **client** and needs a client cert. Add a `dtcc-org` responder entity (spec §10).

**Files:**
- Modify: `ansible/vars/pki-entities.yml`

**Interfaces:**
- Produces: keystore `build/secrets/pki/entities/dtcc-responder/dtcc-responder.p12` (personal `O=dtcc-org, CN=dtcc-responder` + the dtcc-org CA) after `mqlab pki ensure`.

- [ ] **Step 1: Add the entity**

In `ansible/vars/pki-entities.yml`, append to `pki_entities`:

```yaml
  - { cn: dtcc-responder, org: dtcc-org, ou: clearing-service, kind: personal, trust: [client-org] }
```

- [ ] **Step 2: Re-run the provider, confirm the keystore**

Run:
```bash
uv run mqlab pki issue dtcc-responder
ls build/secrets/pki/entities/dtcc-responder/dtcc-responder.p12
```
Expected: the `.p12` exists.

- [ ] **Step 3: Commit**

```bash
vrg-git add ansible/vars/pki-entities.yml
vrg-commit --type feat --scope tls --message "PKI: add dtcc-responder client entity (#250)"
```

---

## Task 2: Keystore-distribution role

A small role that copies each entity's PKCS#12 (and its `KEYRPWD`) from the controller's `build/secrets/pki/` to the host that uses it. Shared-LUN-aware for QMPCMK.

**Files:**
- Create: `ansible/roles/pki-distribute/tasks/main.yml`
- Create: `ansible/roles/pki-distribute/defaults/main.yml`

**Interfaces:**
- Consumes: `pki_entity` (the CN to place), `pki_dest_dir` (where on the target host), `pki_owner` (file owner, default `mqm`).
- Produces: `<pki_dest_dir>/<cn>.p12` (mode 0600) + the password available as fact `pki_keyrpwd` (from `lab-secret.sh`, not logged).

- [ ] **Step 1: Defaults**

`ansible/roles/pki-distribute/defaults/main.yml`:
```yaml
---
pki_src_dir: "{{ repo_root }}/build/secrets/pki/entities"
pki_owner: mqm
```

- [ ] **Step 2: Tasks — copy keystore + source the password**

`ansible/roles/pki-distribute/tasks/main.yml`:
```yaml
---
- name: "ensure dest dir for {{ pki_entity }}"
  ansible.builtin.file:
    path: "{{ pki_dest_dir }}"
    state: directory
    owner: "{{ pki_owner }}"
    group: "{{ pki_owner }}"
    mode: "0700"
  become: true

- name: "copy {{ pki_entity }} keystore to the host"
  ansible.builtin.copy:
    src: "{{ pki_src_dir }}/{{ pki_entity }}/{{ pki_entity }}.p12"
    dest: "{{ pki_dest_dir }}/{{ pki_entity }}.p12"
    owner: "{{ pki_owner }}"
    group: "{{ pki_owner }}"
    mode: "0600"
  become: true

- name: "source the {{ pki_entity }} keystore password (lab-secret.sh)"
  ansible.builtin.command: "{{ repo_root }}/lab/scripts/lab-secret.sh pki-keyrpwd-{{ pki_entity | lower }}"
  delegate_to: localhost
  become: false
  register: pki_keyrpwd
  changed_when: false
  no_log: true
```

- [ ] **Step 3: Validate (syntax + idempotency on a host)**

Run: `vrg-container-run -- vrg-validate` (yamllint/markdownlint pass; no ansible-lint).
Then functionally on a live setup later (Task 3) — the role has no standalone test (infra).

- [ ] **Step 4: Commit**

```bash
vrg-git add ansible/roles/pki-distribute
vrg-commit --type feat --scope tls --message "pki-distribute role: place keystores on hosts (#250)"
```

---

## Task 3: QMPCMK keystore + the keystore-load gate (FIRST live-MQ proof)

Wire QMPCMK's `SSLKEYR`/`KEYRPWD` (keystore on `/mqshared`) and **prove GSKit loads the lab-pki PKCS#12** before any channel TLS. This is the spec §11 step-0 gate and discharges PKI Task 8.

**Files:**
- Modify: `ansible/roles/mq-pcmk-qmgr/tasks/main.yml` (the creation block, near the `apply MQSC config` task)

**Interfaces:**
- Consumes: `pki-distribute` (places `/mqshared/ssl/QMPCMK.p12`), `qm_name` (= `QMPCMK`).
- Produces: QMPCMK with `SSLKEYR('/mqshared/ssl/QMPCMK')` + persisted `KEYRPWD`.

- [ ] **Step 1: Distribute QMPCMK's keystore to the LUN (creation node)**

In `mq-pcmk-qmgr/tasks/main.yml`, inside the first-time creation block (after `mount the shared LUN`, before `apply MQSC config`), add a role include / tasks that place the keystore under `/mqshared/ssl`:
```yaml
    - name: place QMPCMK keystore on the shared LUN
      ansible.builtin.include_role:
        name: pki-distribute
      vars:
        pki_entity: "{{ qm_name }}"
        pki_dest_dir: /mqshared/ssl
      run_once: true
```

- [ ] **Step 2: Set SSLKEYR/KEYRPWD in the creation MQSC**

In the `apply MQSC config` `printf | runmqsc` block, add (before the `endmqm -w`):
```
ALTER QMGR SSLKEYR('"'"'/mqshared/ssl/{{ qm_name }}'"'"') KEYRPWD('"'"'{{ pki_keyrpwd.stdout }}'"'"')\n
REFRESH SECURITY TYPE(SSL)\n
```
(`pki_keyrpwd.stdout` is registered by the Step-1 include; `no_log` keeps it out of output.)

- [ ] **Step 3: GATE — confirm GSKit loads the keystore (functional, blocking)**

Bring up just the SANs + pcmk nodes for the setup and run QM create through this point, then on the creation node:
```bash
su mqm -c 'echo "REFRESH SECURITY TYPE(SSL)" | runmqsc QMPCMK'
su mqm -c 'cat /var/mqm/qmgrs/QMPCMK/errors/AMQERR01.LOG' | grep -iE "AMQ9657|AMQ9633|keystore|SSL" | tail
```
Expected: `REFRESH SECURITY` returns success; **no** `AMQ9657`/`AMQ9633` keystore-parse error. The `<stem>.p12` naming convention is confirmed here.
**If it fails (GSKit rejects the encoding):** re-encode with `runmqktool` (PKI fallback) — `runmqktool -keystore /mqshared/ssl/QMPCMK.p12 ...` to a GSKit-compatible PKCS#12 — and re-run. Do **not** proceed to channels until this is green.

- [ ] **Step 4: Commit**

```bash
vrg-git add ansible/roles/mq-pcmk-qmgr/tasks/main.yml
vrg-commit --type feat --scope tls --message "QMPCMK: SSLKEYR/KEYRPWD on shared LUN + keystore-load gate (#250)"
```

---

## Task 4: Channel TLS — the four QM↔QM channels (shared MQSC snippet)

Add `SSLCIPH`/`SSLCAUTH`/`SSLPEER` to both ends of both channels, authored as a shared, arm-agnostic var so rdqm reuses it.

**Files:**
- Create: `ansible/group_vars/all/tls.yml` (the shared channel-TLS attrs)
- Modify: `ansible/roles/mq-pcmk-qmgr/templates/inter-qm.mqsc.j2` (our-side channels)
- Modify: `ansible/roles/mq-inter-qm/templates/their-side.mqsc.j2` (their-side channels)
- Modify: `ansible/roles/mq-inter-qm/tasks/main.yml` (QMDTCC keystore + KEYRPWD, like Task 3)

**Interfaces:**
- Consumes: the keystores (Task 2/3); `tls_cipher`, `tls_peer_inhouse`, `tls_peer_dtcc` (from `group_vars/all/tls.yml`).
- Produces: the four channels with TLS attrs; QMDTCC with `SSLKEYR`.

- [ ] **Step 1: Shared TLS vars**

`ansible/group_vars/all/tls.yml`:
```yaml
---
# Shared channel-TLS attributes — identical across arms (#212 seam).
tls_cipher: "ANY_TLS13_OR_HIGHER"
tls_peer_inhouse: "O=client-org,OU=clearing-service" # what DTCC validates
tls_peer_dtcc: "O=dtcc-org"                            # what the in-house QM validates
```

- [ ] **Step 2: Our-side channel TLS (QMPCMK)**

In `mq-pcmk-qmgr/templates/inter-qm.mqsc.j2`, add `SSLCIPH`/`SSLCAUTH`/`SSLPEER` to the SDR and RCVR:
```
DEFINE CHANNEL({{ qm_name }}.QMDTCC) CHLTYPE(SDR) TRPTYPE(TCP) CONNAME('{{ dtcc_conn }}(1414)') XMITQ(QMDTCC) SSLCIPH({{ tls_cipher }}) SSLPEER('{{ tls_peer_dtcc }}') SHORTRTY(10) SHORTTMR(5) LONGRTY(999999999) LONGTMR(20) REPLACE
DEFINE CHANNEL(QMDTCC.{{ qm_name }}) CHLTYPE(RCVR) TRPTYPE(TCP) SSLCIPH({{ tls_cipher }}) SSLCAUTH(REQUIRED) SSLPEER('{{ tls_peer_dtcc }}') REPLACE
```

- [ ] **Step 3: Their-side channel TLS (QMDTCC) + QMDTCC keystore**

In `mq-inter-qm/templates/their-side.mqsc.j2`, add to the SDR/RCVR (and `SVC.SVRCONN` in Task 5):
```
DEFINE CHANNEL({{ qmgr_name }}.{{ our_qm }}) CHLTYPE(SDR) TRPTYPE(TCP) CONNAME('{{ our_conn }}') XMITQ({{ our_qm }}) SSLCIPH({{ tls_cipher }}) SSLPEER('{{ tls_peer_inhouse }}') SHORTRTY(10) SHORTTMR(5) LONGRTY(999999999) LONGTMR(20) REPLACE
DEFINE CHANNEL({{ our_qm }}.{{ qmgr_name }}) CHLTYPE(RCVR) TRPTYPE(TCP) SSLCIPH({{ tls_cipher }}) SSLCAUTH(REQUIRED) SSLPEER('{{ tls_peer_inhouse }}') REPLACE
```
In `mq-inter-qm/tasks/main.yml`, before applying the their-side MQSC, distribute QMDTCC's keystore (reuse `pki-distribute`, `pki_entity: QMDTCC`, `pki_dest_dir: /var/mqm/qmgrs/QMDTCC/ssl`) and set `ALTER QMGR SSLKEYR(...) KEYRPWD(...) ; REFRESH SECURITY TYPE(SSL)` on QMDTCC (same pattern as Task 3).

- [ ] **Step 4: GATE — channels go RUNNING over TLS**

Bring the distributed setup up; on QMPCMK:
```bash
su mqm -c 'echo "DIS CHSTATUS(QMPCMK.QMDTCC) SSLPEER SSLCIPH" | runmqsc QMPCMK'
```
Expected: STATUS(RUNNING), `SSLCIPH(ANY_TLS13_OR_HIGHER)`, `SSLPEER` shows the DTCC DN. Repeat for `QMDTCC.QMPCMK`. A plaintext SDR (no SSLCIPH) is rejected.
**Confirms the [pushback §8] question:** `SSLCAUTH`/`SSLPEER` enforce with `CHLAUTH(DISABLED)`. If they don't, add a minimal `SET CHLAUTH(...) TYPE(SSLPEERMAP) SSLPEER(...) USERSRC(CHANNEL)` + `ALTER QMGR CHLAUTH(ENABLED)` (a sliver of Stage 2) — record it.

- [ ] **Step 5: Commit**

```bash
vrg-git add ansible/group_vars/all/tls.yml ansible/roles/mq-pcmk-qmgr/templates/inter-qm.mqsc.j2 ansible/roles/mq-inter-qm
vrg-commit --type feat --scope tls --message "QM-to-QM channel TLS (TLS1.3, mutual, partial-DN SSLPEER) (#250)"
```

---

## Task 5: SVRCONN client TLS — app-client + service responder

TLS the `APP.SVRCONN` (app-client → QMPCMK) and `SVC.SVRCONN` (responder → QMDTCC), and the pymqi clients that use them.

**Files:**
- Modify: `ansible/roles/mq-pcmk-qmgr/tasks/main.yml` (`APP.SVRCONN` def — add TLS)
- Modify: `ansible/roles/mq-inter-qm/templates/their-side.mqsc.j2` (`SVC.SVRCONN` def — add TLS)
- Modify: `ansible/roles/mq-client/tasks/main.yml` (distribute `app-client.p12`)
- Modify: `clients/app_requester.py`, `clients/service_responder.py` (pymqi TLS)

**Interfaces:**
- Consumes: client keystores (`app-client.p12`, `dtcc-responder.p12`) on their hosts (Task 2).
- Produces: TLS'd SVRCONNs + TLS pymqi connections.

- [ ] **Step 1: TLS the SVRCONN defs**

`APP.SVRCONN` (in `mq-pcmk-qmgr` base MQSC) and `SVC.SVRCONN` (their-side): add `SSLCIPH({{ tls_cipher }}) SSLCAUTH(REQUIRED) SSLPEER('{{ tls_peer_inhouse }}')` (app-client and the responder both present `O=client-org`/`O=dtcc-org` certs — set `SSLPEER` to the matching org; the responder is `O=dtcc-org`).

- [ ] **Step 2: Distribute client keystores**

In `mq-client/tasks/main.yml`, include `pki-distribute` (`pki_entity: app-client`, `pki_dest_dir: /home/vagrant/ssl`, `pki_owner: vagrant`). The responder host gets `dtcc-responder` via `mq-inter-qm`.

- [ ] **Step 3: pymqi TLS on the connect calls**

In `clients/app_requester.py` and `clients/service_responder.py`, add the cipher + key repository to the connect:
```python
    cd.SSLCipherSpec = b"ANY_TLS13_OR_HIGHER"
    sco = pymqi.SCO()
    sco.KeyRepository = args.keyrepo.encode()   # stem, e.g. /home/vagrant/ssl/app-client
    qmgr.connect_with_options(args.qm, cd=cd, sco=sco, opts=pymqi.CMQC.MQCNO_RECONNECT)
```
Add an `--keyrepo` arg. **Verify at build:** the PKCS#12 client key-repo password is supplied (MQI clients with PKCS#12 have the no-stash issue) — via `MQS_KEYSTORE_CONF`/`sco.KeyRepoPassword` or an env stash; confirm the working mechanism in the functional run and record it.

- [ ] **Step 4: GATE — app flow works over TLS end-to-end**

Drive the app flow (the existing run path):
```bash
uv run mqlab run distributed-pcmk-ubuntu --seconds 20
```
Expected: requests/replies flow; `DIS CHSTATUS(APP.SVRCONN)` shows TLS; a non-TLS client is rejected (2393 `MQRC_SSL_INITIALIZATION_ERROR`).

- [ ] **Step 5: Commit**

```bash
vrg-git add ansible/roles/mq-pcmk-qmgr/tasks/main.yml ansible/roles/mq-inter-qm ansible/roles/mq-client clients/app_requester.py clients/service_responder.py
vrg-commit --type feat --scope tls --message "SVRCONN client TLS — app-client + responder (#250)"
```

---

## Task 6: Exporter SVRCONN TLS

TLS the `mq_prometheus` exporter's SVRCONN connection.

**Files:**
- Modify: `ansible/roles/mq-exporter/tasks/main.yml` + `defaults/main.yml` (read first; mirror the app-client pattern)

**Interfaces:**
- Consumes: `mq_prometheus.p12` on the exporter host (Task 2).
- Produces: TLS'd exporter connection.

- [ ] **Step 1: Read the exporter role + its connection config**

Run: `cat ansible/roles/mq-exporter/tasks/main.yml ansible/roles/mq-exporter/defaults/main.yml ansible/roles/mq-exporter/templates/*` — find how the exporter sets its channel/QM/connection (the mq-metric-samples config: `ibmmq.connectionName`, `ibmmq.channel`, and the TLS keys `ibmmq.sslKeyRepository`/`ibmmq.sslCipherSpec`).

- [ ] **Step 2: Add TLS to the exporter config**

Add to the exporter's config (the mq-metric-samples `mq_prometheus` ini/yaml): `ibmmq.sslKeyRepository = <stem>`, `ibmmq.sslCipherSpec = ANY_TLS13_OR_HIGHER`. Distribute `mq_prometheus.p12` via `pki-distribute`. TLS the exporter's SVRCONN def (the one it connects on) with `SSLCIPH`/`SSLCAUTH`/`SSLPEER('O=client-org')`.

- [ ] **Step 3: GATE — exporter scrapes over TLS**

Confirm the exporter connects and Prometheus has fresh `ibmmq_*` metrics (the obs stack), over a TLS channel (`DIS CHSTATUS` for the exporter channel shows SSLCIPH).

- [ ] **Step 4: Commit**

```bash
vrg-git add ansible/roles/mq-exporter
vrg-commit --type feat --scope tls --message "exporter SVRCONN TLS (#250)"
```

---

## Task 7: mqweb TLS — org-CA server cert + REST client trust flip

Replace mqweb's default self-signed cert with the `mqweb`-entity cert, and flip the REST clients to trust the org CA — **in one change**.

**Files:**
- Modify: `ansible/roles/mqweb/tasks/main.yml` (distribute `mqweb.p12` per node)
- Modify: `ansible/roles/mqweb/templates/mqwebuser.xml.j2` (keyStore/ssl/sslDefault)
- Modify: wherever `pymqrest`/exporter set `verify_tls` (grep first)

**Interfaces:**
- Consumes: `mqweb.p12` (per node), the org CA bundle (for clients).
- Produces: mqweb serving the org-CA cert; REST clients trusting the org CA.

- [ ] **Step 1: Distribute the mqweb keystore per node**

In `mqweb/tasks/main.yml`, include `pki-distribute` (`pki_entity: mqweb`, `pki_dest_dir: /var/mqm/web/installations/Installation1/servers/mqweb/resources/security`). Source its `KEYRPWD`.

- [ ] **Step 2: Configure mqwebuser.xml for the custom cert**

In `mqwebuser.xml.j2`, replace `<sslDefault sslRef="mqDefaultSSLConfig"/>` with:
```xml
  <keyStore id="mqwebKeyStore" location="resources/security/mqweb.p12" type="PKCS12" password="{{ mqweb_keyrpwd }}"/>
  <ssl id="mqwebSSL" keyStoreRef="mqwebKeyStore" serverKeyAlias="mqweb" sslProtocol="TLSv1.3"/>
  <sslDefault sslRef="mqwebSSL"/>
```
(`mqweb_keyrpwd` from `lab-secret.sh pki-keyrpwd-mqweb`, `no_log`; `serverKeyAlias` = the cert friendlyName `mqweb`.) Then `strmqweb` restart (the role's `systemctl start mqweb --no-block` picks up on restart — add an explicit `systemctl restart mqweb --no-block`).

- [ ] **Step 3: Flip the REST clients to CA-trust (same change)**

Grep `verify_tls=False` and the `pymqrest(...)` / exporter REST construction; replace `verify_tls=False` with the org CA bundle path (`verify=<ca-bundle>` or the client's trust). Distribute the `client-org` CA bundle to those hosts.

- [ ] **Step 4: GATE — REST over CA-trusted TLS**

```bash
uv run mqlab pki list   # sanity
# from a REST client host:
curl --cacert <ca-bundle> https://<mqweb-host>:9443/ibmmq/rest/v3/ -u <user>:<pw>
```
Expected: TLS handshake succeeds against the org-CA cert (no `verify=False`); `pymqrest`/exporter reach mqweb.

- [ ] **Step 5: Commit**

```bash
vrg-git add ansible/roles/mqweb <rest-client-files>
vrg-commit --type feat --scope tls --message "mqweb org-CA cert + REST client trust flip (#250)"
```

---

## Task 8: Full functional validation + failover gate (spec §11)

**Files:** none (acceptance).

- [ ] **Step 1: Clean bring-up, all TLS**

```bash
mqlab vm destroy distributed-pcmk-ubuntu
mqlab vm create distributed-pcmk-ubuntu && mqlab vm provision distributed-pcmk-ubuntu
mqlab qm create distributed-pcmk-ubuntu
```
Expected: one-pass; all four QM↔QM channels RUNNING over TLS (Task 4 gate), app flow works (Task 5 gate), exporter + mqweb over TLS (Tasks 6–7).

- [ ] **Step 2: Unattended-failover gate (the HA-with-TLS proof)**

Trigger a Pacemaker failover and confirm QMPCMK comes back TLS'd with **no** manual step:
```bash
ssh pcmk-a1 'sudo pcs resource move mq_group pcmk-a2'
# after it lands on pcmk-a2:
ssh pcmk-a2 "su mqm -c 'echo \"DIS QMSTATUS\" | runmqsc QMPCMK'"   # RUNNING
ssh pcmk-a2 "su mqm -c 'echo \"DIS CHSTATUS(QMPCMK.QMDTCC) SSLCIPH\" | runmqsc QMPCMK'"   # RUNNING + TLS
```
Expected: QMPCMK reads its `/mqshared` keystore with the persisted `KEYRPWD` and channels resume over TLS — **no human supplied a password.** This proves [pushback 1].

- [ ] **Step 3: Cold-rebuild acceptance gate**

(Human runs the VM rebuild — agent can't.) After `vrg-vm rebuild` + `uv sync` + `ansible-galaxy collection install -r ansible/requirements.yml -p build/ansible_collections` + `mqlab pki ensure`, run Step 1 from clean and confirm one-pass TLS bring-up.

- [ ] **Step 4: Record results in the PR**

Note: channels RUNNING over TLS 1.3, app flow green, failover-TLS green, cold-rebuild one-pass, and whether `SSLCAUTH`/`SSLPEER` enforced with `CHLAUTH` disabled (or the minimal SSLPEERMAP fallback was needed).

---

## Self-Review

**Spec coverage:** §3 inventory → Tasks 3–7; §4 recipe → Tasks 3–7; §5 cipher (TLS 1.3) → Global Constraints + every channel/client task; §6 SSLPEER partial-DN → `group_vars/all/tls.yml` (Task 4) + per-channel; §7 keystore distribution + LUN + KEYRPWD-persisted → Tasks 2, 3; §8 CHLAUTH/CONNAUTH disabled + confirm → Task 4 step 4; §9 roles + mqweb mechanism + arm-agnostic → Tasks 4–7; §10 responder entity → Task 1; §11 validation incl. step-0 load gate + failover → Task 3 step 3, Task 8. **Pushback resolutions:** [1] failover gate (Task 8); [2] load gate first (Task 3); [3] mqweb Liberty (Task 7); [4] shared TLS var (Task 4); [5] `.p12` stem (Task 3 gate); [6] client distribution (Task 2, 5).

**Out-of-plan (by design):** Stage 2 internal authorization (#249); other arms; cert expiry/rotation.

**Verify-at-build items (gated, not bluffed):** the GSKit keystore-load + exact `SSLKEYR` `.p12` convention (Task 3 gate, `runmqktool` fallback); `SSLCAUTH`/`SSLPEER`-with-`CHLAUTH`-disabled (Task 4 gate, `SSLPEERMAP` fallback); the pymqi PKCS#12 client key-repo password mechanism (Task 5); the exporter's exact TLS config keys (Task 6 step 1). Each has a concrete verification + fallback, per the spec's deliberate gating.

**Placeholder scan:** none — the `<rest-client-files>` / `args.keyrepo` style tokens are real parameters resolved by their task's grep/arg step, not TODOs.
