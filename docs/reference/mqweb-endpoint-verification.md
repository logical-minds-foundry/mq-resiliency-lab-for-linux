# mqweb endpoint verification runbook (epic `.github#341`, task #1505)

Acceptance for epic `.github#341` (spec §6): on every stack and both sites, mqweb
serves the MQ Console and the REST API on the queue manager's **data-plane** address,
refuses anonymous access, enforces the three role logins, serves the org-CA `mqweb`
certificate over TLS 1.3, and is found again by `mqlab console open` after a failover
or a cross-site cutover. This is a **live-lab** procedure. Run it on the dev VM against
a freshly cold-rebuilt lab and record the results in the tables below. The live-lab
validation task #1505 runs it; automating it is the job of the live-lab validation
framework (epic #38).

> Fill the **Result** cells as you go (`✅` / `❌` + a note). Do not pre-populate.

## Prerequisites

- A **cold-rebuilt** lab with the stacks under test and the commons up (the
  cold-rebuild acceptance gate; a stale VM does not count).
- mqweb running on every QM node, which is the default: on a QM node,
  `systemctl is-active mqweb.service` → `active`.
- The lab PKI in the state bucket (`mqlab pki ensure`). `mqlab console open` checks the
  app-org CA with it.
- The probes run on the dev VM, which reaches the data plane (`10.10.x.x`), `net-ext`
  (`10.60.0.x`) and the management plane (`10.50.0.x`).

### Logins

mqweb has three role logins (spec §2.2). By lab convention each password equals the
username. They are lab-only sample logins, not secrets.

| Login | mqweb role | Expected behaviour |
|---|---|---|
| `mqadmin` | `MQWebAdmin` | reads and writes |
| `mqreadonly` | `MQWebAdminRO` | reads; mqweb refuses its writes (HTTP 403) |
| `mqoperator` | `MQWebUser` | reads; the queue manager refuses its writes (`MQRC 2035`) |

### REST conventions

The lab uses REST API **`v3`** (`REST_API_VERSION=v3`, from
[`mqweb-spike-findings.md`](mqweb-spike-findings.md) §5). Every write needs the CSRF
header `ibm-mq-rest-csrf-token` (any value). Without it mqweb answers HTTP 400
`MQWB0100E: The CSRF header 'ibm-mq-rest-csrf-token' was omitted from the request.`

The commands below use three shell variables. Set them from Step 0 for each stack and
site:

```bash
EP=https://10.10.1.11:9443          # the live instance Step 0 found
QM=NHAUAPP                          # that stack's queue manager
REST=$EP/ibmmq/rest/v3/admin
```

`curl -k` skips certificate checks because the `mqweb` certificate names `mqweb`, not
the address. Step 3 checks the certificate itself.

## Step 0 — find the live instance

```bash
mqlab console open <stack>            # site A
mqlab console open <stack> --site b   # site B, once the stack is live there
```

`console open` probes the stack's candidate endpoints from topology, logged in as
`mqreadonly`, and picks the one whose queue manager reports `running`. It then points
the `mqlab-console-forward` forwarder (the VM's `127.0.0.1:19443`) at it and prints the
live instance, the workstation URLs and the logins. Observed on `nativeha-ubuntu`:

```text
MQ Console for nativeha-ubuntu (queue manager NHAUAPP, site a): live instance https://10.10.1.11:9443
Forwarder mqlab-console-forward: this VM's 127.0.0.1:19443 -> 10.10.1.11:9443
```

If no candidate is running at that site (for example, site B before a cutover), it
fails loud and lists each candidate's state:

```text
mqlab console open: NHAUAPP: no candidate reports 'running' (https://10.10.2.11:9443=replica [member], https://10.10.2.12:9443=recoveryGroupLeader [member], https://10.10.2.13:9443=replica [member])
```

The candidates, derived from topology:

| Stack | QM | Site-A endpoint | Site-B endpoint |
|---|---|---|---|
| `pcmk-ubuntu` (VIP) | PCMKAPP | `https://10.10.1.200:9443` | `https://10.10.2.200:9443` |
| `rdqm-rhel` (VIP) | RDQMAPP | `https://10.10.1.100:9443` | `https://10.10.2.100:9443` |
| `pcmk-drbd-ubuntu` (VIP) | PDRBDAPP | `https://10.10.1.170:9443` | `https://10.10.2.170:9443` |
| `nativeha-ubuntu` (active instance) | NHAUAPP | `https://10.10.1.{11,12,13}:9443` | `https://10.10.2.{11,12,13}:9443` |
| `nativeha-rhel-crr` (active instance) | NHARCAPP | `https://10.10.1.{91,92,93}:9443` | `https://10.10.2.{91,92,93}:9443` |
| `svc-sim` (counterparty, `net-ext`) | SVCQM | `https://10.60.0.50:9443` | — |

`pcmk-drbd-ubuntu` runs mqweb on every node of both sites (#1428, epic `.github#310`).
Its site-B VIP is up only while site B is the DR primary, after a `mqlab dr cutover`.

> **Replicas answer too.** A non-active Native HA instance's mqweb is up. The collection
> query `GET $REST/qmgr` returns the QM with `"state": "replica"` (or
> `"recoveryGroupLeader"` on the CRR recovery-group leader), not an error. The named
> query `GET $REST/qmgr/$QM` on a replica returns HTTP 500 `MQWB0004E` with reason 2543
> (`MQRC_STANDBY_Q_MGR`). `console open` uses the collection query, so it skips replicas
> without error.

Steps 1–3 run against the live instance at site A. After the Step 5 cutover, run them
again at site B, starting from `mqlab console open <stack> --site b`. svc-sim has only
its `net-ext` endpoint.

## Step 1 — no anonymous access

```bash
# expect: 401
curl -sk -o /dev/null -w '%{http_code}\n' $REST/qmgr
# expect: 302 https://<live>:9443/ibmmq/console/login.html
curl -sk -o /dev/null -w '%{http_code} %{redirect_url}\n' $EP/ibmmq/console/
```

The REST refusal reads `MQWB0104E: The REST API request to 'v3/admin/qmgr' is not
authenticated.` Opening the Console with no login lands on the login page.

| Stack (site) | REST 401? | Console redirects to login? | Result |
|---|---|---|---|
| `pcmk-ubuntu` (A) | | | ☐ |
| `pcmk-ubuntu` (B) | | | ☐ |
| `rdqm-rhel` (A) | | | ☐ |
| `rdqm-rhel` (B) | | | ☐ |
| `nativeha-ubuntu` (A) | | | ☐ |
| `nativeha-ubuntu` (B) | | | ☐ |
| `nativeha-rhel-crr` (A) | | | ☐ |
| `nativeha-rhel-crr` (B) | | | ☐ |
| `svc-sim` (`net-ext`) | | | ☐ |

## Step 2 — per-role allow and deny

### Through REST

Every login can read:

```bash
for u in mqadmin mqreadonly mqoperator; do
  curl -sk -u $u:$u -o /dev/null -w "$u %{http_code}\n" $REST/qmgr/$QM   # expect 200
done
```

Each login then attempts the same write. Run `mqadmin` last, so the earlier refusals
are not a side effect of an existing queue:

```bash
DEFINE='{"type":"runCommand","parameters":{"command":"DEFINE QLOCAL(ACCEPT.TEST) REPLACE"}}'
for u in mqreadonly mqoperator mqadmin; do
  echo "== $u"
  curl -sk -u $u:$u -H 'ibm-mq-rest-csrf-token: x' -H 'Content-Type: application/json' \
    -d "$DEFINE" -w '\nHTTP %{http_code}\n' $REST/action/qmgr/$QM/mqsc
done
```

Expected, as observed on `nativeha-ubuntu` and svc-sim:

| Login | HTTP | Response | Refused by |
|---|---|---|---|
| `mqreadonly` | 403 | `MQWB0113E: User 'mqreadonly' does not have a suitable role. The 'MQWebAdminRO' role permits only read-only operations.` | mqweb |
| `mqoperator` | 200 | `"completionCode": 2, "reasonCode": 2035, "text": ["AMQ8135E: Not authorized."]`, `"overallReasonCode": 3008` | the queue manager |
| `mqadmin` | 200 | `"completionCode": 0, "reasonCode": 0, "text": ["AMQ8006I: IBM MQ queue created."]` | — |

The `mqoperator` refusal comes back as HTTP 200: mqweb ran the command, and the queue
manager refused it. Read the `reasonCode`, not the HTTP status.

Remove the test queue:

```bash
curl -sk -u mqadmin:mqadmin -H 'ibm-mq-rest-csrf-token: x' -H 'Content-Type: application/json' \
  -d '{"type":"runCommand","parameters":{"command":"DELETE QLOCAL(ACCEPT.TEST)"}}' \
  $REST/action/qmgr/$QM/mqsc                                  # expect AMQ8007I: IBM MQ queue deleted.
```

### The authority event in Loki

The `mqoperator` refusal raises a queue-manager authority event. The event collector
ships it to Loki with the label `unit="mq-events"` (the collector's syslog tag; see
`ansible/roles/alloy/templates/config.alloy.j2`). Query Loki on the obs node:

```bash
curl -sG http://10.50.0.2:3100/loki/api/v1/query_range \
  --data-urlencode 'query={unit="mq-events"} | json | eventReason_value="2035" | eventData_userIdentifier="mqoperator"' \
  --data-urlencode "start=$(date -u -d '-15 min' +%s)000000000" | python3 -m json.tool
```

The same query works in Grafana **Explore** with the Loki data source. Expect one
stream per refusing node, with `host` set to that node, and an event like:

```text
"eventType": {"name": "Queue Mgr Event", "value": 44},
"eventReason": {"name": "Not Authorized", "value": 2035},
"eventData": {"queueMgrName": "NHAUAPP", "reasonQualifier": "Cmd Not Authorized",
              "command": "Copy Queue", "userIdentifier": "mqoperator"}
```

The event names the PCF command (`Copy Queue`) that mqweb sent for the `DEFINE`, not the
MQSC text.

### Through the Console

Once per login, on the live instance: open the Console through the forward (the URL
`console open` printed), log in, and try to create a local queue. Expect `mqadmin` to
succeed, `mqreadonly` to be refused, and `mqoperator` to be refused with a "not
authorized" error from the queue manager. Delete any queue created.

| Stack (site) | `mqadmin` writes? | `mqreadonly` 403? | `mqoperator` 2035? | 2035 event in Loki? | Console UI matches? | Result |
|---|---|---|---|---|---|---|
| `pcmk-ubuntu` (A) | | | | | | ☐ |
| `pcmk-ubuntu` (B) | | | | | | ☐ |
| `rdqm-rhel` (A) | | | | | | ☐ |
| `rdqm-rhel` (B) | | | | | | ☐ |
| `nativeha-ubuntu` (A) | | | | | | ☐ |
| `nativeha-ubuntu` (B) | | | | | | ☐ |
| `nativeha-rhel-crr` (A) | | | | | | ☐ |
| `nativeha-rhel-crr` (B) | | | | | | ☐ |
| `svc-sim` (`net-ext`) | | | | | | ☐ |

## Step 3 — TLS

```bash
CA=$(mqlab build path state)/secrets/pki/ca/app-org/ca.crt
openssl s_client -connect ${EP#https://} -CAfile "$CA" </dev/null 2>/dev/null | grep -E '^New|Verify return'
openssl s_client -connect ${EP#https://} </dev/null 2>/dev/null | openssl x509 -noout -subject -issuer
```

Expected, as observed on `nativeha-ubuntu` and svc-sim:

```text
New, TLSv1.3, Cipher is TLS_AES_256_GCM_SHA384
Verify return code: 0 (ok)
subject=O = app-org, OU = ops, CN = mqweb
issuer=O = app-org, CN = app-org Root CA
```

| Stack (site) | `CN=mqweb`? | Issuer app-org CA, verifies? | TLSv1.3? | Result |
|---|---|---|---|---|
| `pcmk-ubuntu` (A) | | | | ☐ |
| `pcmk-ubuntu` (B) | | | | ☐ |
| `rdqm-rhel` (A) | | | | ☐ |
| `rdqm-rhel` (B) | | | | ☐ |
| `nativeha-ubuntu` (A) | | | | ☐ |
| `nativeha-ubuntu` (B) | | | | ☐ |
| `nativeha-rhel-crr` (A) | | | | ☐ |
| `nativeha-rhel-crr` (B) | | | | ☐ |
| `svc-sim` (`net-ext`) | | | | ☐ |

## Step 4 — discovery after an in-site failover

The forward points at one instance and does not follow the queue manager. After each
failover, re-run `mqlab console open <stack>` and confirm where it lands.

**VIP stacks** (`pcmk-ubuntu`, `rdqm-rhel`). Move the QM to another node in site A. The
VIP follows the QM, so `console open` lands on the **same** VIP and the Console works.

The node commands run in a shell on the guest, opened with `mqlab vm ssh <guest>`.

```bash
mqlab console open pcmk-ubuntu                        # note the live endpoint
# pcmk, on a cluster node (mqlab vm ssh pcmk-a1): move the resource group
sudo pcs resource move mq_group pcmk-a2               # afterwards: sudo pcs resource clear mq_group
# rdqm, on the current primary (mqlab vm ssh rdqm-a1): end the QM with reconnect;
# RDQM restarts it on another node
sudo su - mqm -c '/opt/mqm/bin/endmqm -r RDQMAPP'
# back on the dev VM:
mqlab console open pcmk-ubuntu                        # same VIP; log in to the Console
```

**Native HA** (`nativeha-ubuntu`, `nativeha-rhel-crr`). Stop the active instance's
monitor, so another instance in the Live group takes over. `console open` lands on the
**new** active instance.

```bash
mqlab console open nativeha-ubuntu                    # note the live instance, e.g. 10.10.1.11 = nha-ubuntu-a1
# on that instance (mqlab vm ssh nha-ubuntu-a1):
sudo systemctl stop mqmonitor@NHAUAPP
# back on the dev VM:
mqlab console open nativeha-ubuntu                    # expect a different site-A instance
# on nha-ubuntu-a1 again, so it rejoins as a replica:
sudo systemctl start mqmonitor@NHAUAPP
```

The `nativeha-rhel-crr` equivalent uses `nha-rhel-crr-a<n>` (`10.10.1.9<n>`) and
`mqmonitor@NHARCAPP`.

| Stack | QM moved? | `console open` lands on | Console works? | Result |
|---|---|---|---|---|
| `pcmk-ubuntu` | | same VIP? | | ☐ |
| `rdqm-rhel` | | same VIP? | | ☐ |
| `nativeha-ubuntu` | | new active instance? | | ☐ |
| `nativeha-rhel-crr` | | new active instance? | | ☐ |

## Step 5 — discovery after a cross-site cutover, with fail-back

Cut the stack over to site B, confirm `console open --site b` lands on the site-B live
endpoint, run Steps 1–3 at site B, then fail back and confirm site A.

```bash
mqlab dr cutover  <stack>           # site A -> B
mqlab console open <stack> --site b # VIP stacks: the site-B VIP; Native HA: the site-B active instance
# ... Steps 1-3 at site B ...
mqlab dr failback <stack>           # site B -> A
mqlab console open <stack>          # back on site A
```

`mqlab dr` runs each stack's declared DR verb from `lab/topology.yaml`. For the Native
HA arms that verb is the CRR switchover playbook (`ansible/site-nativeha-switchover.yml`
for `nativeha-rhel-crr`, `ansible/site-nativeha-ubuntu-switchover.yml` for
`nativeha-ubuntu`). The hand-run form is `ansible-playbook <playbook> -e target_live=b`
to cut over and `-e target_live=a` to fail back. `pcmk-ubuntu` declares no DR verb; cut
it over with `lab/scripts/pcmk-dr-cutover.sh a2b` and fail back with `b2a`.

| Stack | `--site b` lands on the site-B live endpoint? | Steps 1–3 pass at site B? | Site A after fail-back? | Result |
|---|---|---|---|---|
| `pcmk-ubuntu` | | | | ☐ |
| `rdqm-rhel` | | | | ☐ |
| `nativeha-ubuntu` | | | | ☐ |
| `nativeha-rhel-crr` | | | | ☐ |

## Step 6 — no REST leftovers

The pre-epic REST tooling is gone:

```bash
mqlab rest                          # expect: No such command 'rest'.
grep -rn pymqrest pyproject.toml    # expect no output
```

| Check | Result |
|---|---|
| `mqlab rest` → "No such command" | ☐ |
| No `pymqrest` in `pyproject.toml` | ☐ |

## Step 7 — the deferred browser Console checks

The spike deferred these to a browser (spec §3.1;
[`mqweb-spike-findings.md`](mqweb-spike-findings.md) §4.5). Do them in a browser on
macOS/arm64 and on cloud x86, through the forward `console open` sets up, logged in as
`mqadmin` / `mqadmin`. Save screenshots in the temp bucket
(`$(mqlab build path temp)/`) and record what you see in the findings document.

- **Native HA and replication views.** On the active instance (`nativeha-ubuntu`,
  `nativeha-rhel-crr`): do the queue manager's Native HA and replication status views
  render?
- **A replica.** Point the forward at a replica by hand (the forwarder follows only the
  active instance) and record what its Console shows: an error, a read-only view, or a
  "standby" banner. On the spike, the Console listed the queue manager with its Native HA
  role but would not open it.
- **Unauthenticated.** A fresh browser session opening `/ibmmq/console/` lands on the
  login page (the spike saw raw `MQWB2010E` JSON there while anonymous access was on).

| Check | macOS/arm64 | Cloud x86 | Result |
|---|---|---|---|
| Native HA / replication views on the active instance | | | ☐ |
| What a replica shows | | | ☐ |
| Unauthenticated browser lands on the login page | | | ☐ |

## Sign-off

- [ ] No anonymous access on any live endpoint, both sites (Step 1).
- [ ] Per-role allow/deny through REST and the Console, with the `mqoperator` authority
      event in Loki, on every stack including svc-sim (Step 2).
- [ ] TLS: `CN=mqweb`, issued by the app-org CA, TLS 1.3 (Step 3).
- [ ] Discovery follows an in-site failover (Step 4).
- [ ] Discovery follows a cross-site cutover and fail-back (Step 5).
- [ ] No REST leftovers (Step 6).
- [ ] Browser Console checks recorded on macOS/arm64 and cloud x86 (Step 7).
- [ ] Cold-rebuild basis confirmed.

**Run by:** ______  **Date:** ______  **Lab build:** ______

Record SUCCESS or FAILURE per row in #1505's result comment.
