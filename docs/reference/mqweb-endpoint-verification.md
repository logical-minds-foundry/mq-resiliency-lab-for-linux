# mqweb endpoint verification runbook (epic #39, task #539)

Acceptance for epic #39: every stack's **published mqweb REST endpoint** is
reachable and server-authenticated on its **data-plane** address, on **both
sites**, and **survives failover**. This is a **live-lab** procedure — run it on
the box against a freshly cold-rebuilt lab and record the results in the tables
below. It is deliberately manual for now; automating it (inducing a condition and
asserting the observable) is the job of the live-lab validation framework
(epic #38).

> Fill the **Result** cells as you go (`✅` / `❌` + a note). Do not pre-populate.

## Prerequisites

- A **cold-rebuilt** lab with the stacks provisioned (the cold-rebuild acceptance
  gate — a stale VM does not count).
- mqweb running on every instance, including Native HA (#538): on a QM node,
  `systemctl is-active mqweb.service` → `active`.
- `mqlab rest render` available (#537) and `rdqm-rhel` `vip_b` declared (#540).
- REST admin creds in the environment: `MQWEB_ADMIN_USER`, `MQWEB_ADMIN_PASSWORD`
  (runtime-injected, never committed).
- Probes run from a host with **data-plane** reachability to the target addresses.

## Step 0 — render the canonical endpoints

```bash
mqlab rest render      # writes build/work/rest/endpoints.json; echoes per-site endpoints
```

Confirm the output matches the table below (topology-derived; addresses shown for
convenience). The QM names are `<short>APP` (PCMKAPP, RDQMAPP, NHAUAPP, NHARAPP)
and `SVCQM` for the counterparty.

| Stack | QM | Site-A endpoint | Site-B endpoint |
|---|---|---|---|
| `pcmk-ubuntu` (VIP) | PCMKAPP | `https://10.10.1.200:9443` | `https://10.10.2.200:9443` |
| `rdqm-rhel` (VIP) | RDQMAPP | `https://10.10.1.100:9443` | `https://10.10.2.100:9443` |
| `nativeha-ubuntu` (active instance) | NHAUAPP | `https://10.10.1.{11,12,13}:9443` | `https://10.10.2.{11,12,13}:9443` |
| `nativeha-rhel` (active instance) | NHARAPP | `https://10.10.1.{91,92,93}:9443` | `https://10.10.2.{91,92,93}:9443` |
| `svc-sim` (counterparty, net-ext) | SVCQM | `https://10.60.0.50:9443` | — |

## Step 1 — probe each live endpoint (TLS + auth)

For each **live** endpoint, confirm the TLS server cert is the org-CA `mqweb` cert
and that an authenticated REST GET returns the QM:

```bash
# TLS server identity (expect subject CN=mqweb, issuer = the client-org CA):
openssl s_client -connect 10.10.1.200:9443 </dev/null 2>/dev/null | openssl x509 -noout -subject -issuer
# Authenticated REST GET (expect the QM name + RUNNING state):
curl -sk -u "$MQWEB_ADMIN_USER:$MQWEB_ADMIN_PASSWORD" \
  https://10.10.1.200:9443/ibmmq/rest/v2/admin/qmgr | python3 -m json.tool
```

For Native HA, first resolve the active instance, then probe its node IP:

```bash
ssh nha-ubuntu-a1 "su - mqm -c '/opt/mqm/bin/dspmq -m NHAUAPP -o nativeha -x'"  # find ROLE(Active) INSTANCE(...)
curl -sk -u "$MQWEB_ADMIN_USER:$MQWEB_ADMIN_PASSWORD" https://<active-node-data-ip>:9443/ibmmq/rest/v2/admin/qmgr
```

| Stack (live site) | TLS cert = mqweb? | REST GET returns QM? | Result |
|---|---|---|---|
| `pcmk-ubuntu` (site A `10.10.1.200`) | | | ☐ |
| `rdqm-rhel` (site A `10.10.1.100`) | | | ☐ |
| `nativeha-ubuntu` (active in site A) | | | ☐ |
| `nativeha-rhel` (active in site A) | | | ☐ |
| `svc-sim` (`10.60.0.50`) | | | ☐ |

## Step 2 — HA failover (within site A)

Move the QM to another node in the same site and re-probe the **same VIP** (it
follows the QM; the endpoint address does not change).

```bash
# pcmk: move the resource group, then clear the constraint
ssh pcmk-a1 "sudo pcs resource move mq_group pcmk-a2"    # then: pcs resource clear mq_group
# rdqm: fail the HA primary over (endmqm on the current primary; RDQM re-elects)
ssh rdqm-a1 "su - mqm -c '/opt/mqm/bin/endmqm -r RDQMAPP'"
# re-probe the SAME VIP:
curl -sk -u "$MQWEB_ADMIN_USER:$MQWEB_ADMIN_PASSWORD" https://10.10.1.200:9443/ibmmq/rest/v2/admin/qmgr | head
```

| Stack | QM moved to new node? | Same-VIP REST still answers? | Result |
|---|---|---|---|
| `pcmk-ubuntu` | | | ☐ |
| `rdqm-rhel` | | | ☐ |

## Step 3 — cross-site DR cutover (site A → B → A)

Cut the QM over to site B and confirm the **site-B** published endpoint answers;
fail back and confirm site A. This is the check that motivated the #540 fix — the
site-B VIP the renderer publishes must match what the cutover binds.

```bash
lab/scripts/rdqm-dr-cutover.sh a2b            # QM live at site B; binds 10.10.2.100 (from topology)
curl -sk -u "$MQWEB_ADMIN_USER:$MQWEB_ADMIN_PASSWORD" https://10.10.2.100:9443/ibmmq/rest/v2/admin/qmgr | head
lab/scripts/rdqm-dr-cutover.sh b2a            # fail back to site A
curl -sk -u "$MQWEB_ADMIN_USER:$MQWEB_ADMIN_PASSWORD" https://10.10.1.100:9443/ibmmq/rest/v2/admin/qmgr | head
# pcmk equivalent: lab/scripts/pcmk-dr-cutover.sh (site-B VIP 10.10.2.200)
```

| Stack | Site-B endpoint answers after a2b? | Matches `mqlab rest render` site-B? | Site-A answers after b2a? | Result |
|---|---|---|---|---|
| `rdqm-rhel` | | | | ☐ |
| `pcmk-ubuntu` | | | | ☐ |

## Step 4 — Native HA cross-site switchover (site A → B → A)

Native HA has no floating VIP; its published surface is the per-site **candidate
list** of instance data-plane addresses the renderer emits (Step 0). The switchover
flips the Live↔Recovery group role across sites — the Native HA analog of Step 3.
After it, the **site-B** candidate list answers and the newly-active instance's
address must be one of `mqlab rest render`'s site-B addresses for that stack. The
`site-nativeha-*-switchover.yml` playbooks drive it and **require `-e target_live=a|b`**
(`b` = cutover A→B, `a` = fail back B→A).

```bash
# rhel arm (NHARAPP) shown; ubuntu arm: site-nativeha-ubuntu-switchover.yml + NHAUAPP
ansible-playbook ansible/site-nativeha-switchover.yml -e target_live=b   # cutover A -> B
# resolve the new active instance in site B, then probe its data-plane IP:
ssh nha-rhel-b1 "su - mqm -c '/opt/mqm/bin/dspmq -m NHARAPP -o nativeha -x'"   # find ROLE(Active) INSTANCE(...)
curl -sk -u "$MQWEB_ADMIN_USER:$MQWEB_ADMIN_PASSWORD" https://<site-B-active-ip>:9443/ibmmq/rest/v2/admin/qmgr | head
ansible-playbook ansible/site-nativeha-switchover.yml -e target_live=a   # fail back B -> A
```

> **Replicas answer too.** Non-active instances' mqweb is up and REST returns the QM
> with `"state":"replica"` (not an error, not unreachable). Resolution "follows the
> active" by walking the rendered candidate list for the instance reporting
> `"state":"running"` — the others reporting `"replica"` is expected, not a failure.

| Stack | Site-B active answers after A→B? | Matches `mqlab rest render` site-B? | Site-A active answers after B→A? | Result |
|---|---|---|---|---|
| `nativeha-ubuntu` (NHAUAPP) | | | | ☐ |
| `nativeha-rhel` (NHARAPP) | | | | ☐ |

## Sign-off

- [ ] All Step 1 endpoints reachable + authenticated (both sites where applicable).
- [ ] HA failover: REST follows the VIP (Step 2).
- [ ] DR cutover: site-B endpoint answers and matches the renderer (Step 3).
- [ ] Native HA cross-site switchover: site-B candidate answers, matches the renderer, resolution follows the active (Step 4).
- [ ] Cold-rebuild basis confirmed.

**Run by:** ______  **Date:** ______  **Lab build:** ______

When all boxes are ✅, epic #39's verification acceptance is met; close #539 citing
this record. Automating this procedure is tracked under epic #38.
