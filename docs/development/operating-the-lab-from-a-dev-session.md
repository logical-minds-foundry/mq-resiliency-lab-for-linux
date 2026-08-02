# Operating the lab from a dev session — access runbook

**Purpose.** A concise, repeatable path for reaching the lab's queue managers from
a development session, so no future session repeats the access-discovery
confusion. Everything below is **inspection / `DISPLAY` / non-destructive browse /
probe-put** — the safe operations a dev session needs to look at what the lab is
doing.

**Scope note.** This describes *operational* access for development and
inspection. The lab's own lifecycle (create/bootstrap/teardown/HA drills) is
driven through `mqlab` (below); a client that merely *uses* the lab should confine
itself to its own objects and treat the lab's objects as read-only — inspect with
`DISPLAY`/browse, don't `ALTER` what you don't own.

---

## 1. The VMs are libvirt guests under `qemu:///system`

The single most common confusion: the lab VMs are **not** under the default
libvirt *session* URI — they are **system** libvirt guests. List them with the
system connection explicitly:

```sh
virsh -c qemu:///system list --all
```

(With the default `qemu:///session` you will see nothing and wrongly conclude the
lab is down.)

Don't drive the guests through raw `virsh` for lifecycle — operate them through
the lab's own CLI, **`mqlab`**, in this repo's virtualenv:

```sh
.venv/bin/mqlab vm inventory   # the node map (names ↔ IPs ↔ roles)
.venv/bin/mqlab vm roster      # roster view of the nodes
.venv/bin/mqlab status         # per-stack phase / queue-manager status
.venv/bin/mqlab bootstrap      # bring a stack up
.venv/bin/mqlab teardown       # take a stack down
```

---

## 2. The node map and SSH

Nodes sit on **`10.50.0.0/24`** (render the authoritative map with
`mqlab vm inventory`). A representative Native-HA + counterparty slice:

| Node | IP | Role |
|---|---|---|
| a1 | `10.50.0.11` | Native-HA app QM `NHAUAPP` node |
| a2 | `10.50.0.12` | Native-HA app QM `NHAUAPP` node |
| a3 | `10.50.0.13` | Native-HA app QM `NHAUAPP` node |
| svc-sim | `10.50.0.50` | Service/counterparty QM `SVCQM` |
| app-client | `10.50.0.60` | MQ client host (Python venv, TLS keystore) |

SSH in as **`vagrant`** with the Vagrant insecure key; `sudo` is enabled.
**Host keys churn on every rebuild**, so a normal `ssh` trips
`REMOTE HOST IDENTIFICATION HAS CHANGED` — for these throwaway lab guests, disable
host-key checking:

```sh
ssh -i ~/.vagrant.d/insecure_private_key \
    -o UserKnownHostsFile=/dev/null -o StrictHostKeyChecking=no \
    vagrant@10.50.0.11
```

---

## 3. Which node hosts an active Native-HA QM

A Native-HA queue manager (e.g. `NHAUAPP`) is spread across a1/a2/a3 — only
**one** node is active at a time. Find it with `dspmq` (run as any node's
`vagrant`, with `sudo`):

```sh
ssh … vagrant@10.50.0.11 'sudo dspmq -o nativeha -x'
```

Try a1/a2/a3 until one reports the QM **running** as the active instance. A
single-instance QM such as `SVCQM` lives on its own node (svc-sim, `10.50.0.50`).

---

## 4. Admin and browsing — run as the `mqm` user

MQ admin must run as **`mqm`**. Pipe `runmqsc` scripts over SSH, and browse queues
non-destructively with `amqsbcg`:

```sh
# DISPLAY queues (inspection only — don't ALTER objects you don't own)
printf "DISPLAY QLOCAL(*) CURDEPTH\n" | ssh … vagrant@<node-ip> 'sudo -u mqm runmqsc NHAUAPP'

# Browse a queue non-destructively — full MQMD dump, one field per line
ssh … vagrant@<node-ip> 'sudo -u mqm /opt/mqm/samp/bin/amqsbcg <QUEUE> <QM>'
```

`amqsbcg` is the workhorse for message-fidelity inspection: it browses without
consuming and prints every MQMD field on its own labelled line, so an
original-vs-copy field compare (e.g. a message on one QM vs its cross-QM copy on
another) is a straightforward diff. Match related messages by `MsgId` (the
labelled `MsgId : X'…'` line is unique per message).

To isolate one message for a clean compare, `CLEAR QLOCAL(<queue>)` a landing
queue that is safe to empty, put one probe, then browse — but note `CLEAR` fails
with `AMQ8148E` while any handle (e.g. a browsing collector) holds the queue open.

---

## 5. Custom MQMD (report options, groups, properties) — pymqi on the app-client

`amqsput` puts a plain message but **cannot** set report options, build a grouped
MQMD-v2 message, or attach named properties. For those, put with **pymqi** on the
**app-client (`10.50.0.60`)**, client-connecting over the **`APP.SVRCONN`**
channel with **mutual TLS**. The app-client carries the Python venv and the TLS
keystore:

- Python: `/home/vagrant/mqvenv/bin/python`
- Key repository: `/home/vagrant/ssl/key` (stem — MQ appends `.kdb`)
- Client certificate label: `app-client`
- Channel `APP.SVRCONN`, cipher `ANY_TLS13_OR_HIGHER`
- Connection name: all Native-HA nodes comma-separated, so the client reaches
  whichever is active — `10.50.0.11(1414),10.50.0.12(1414),10.50.0.13(1414)`

### Minimal pymqi connect + put

```python
# run with /home/vagrant/mqvenv/bin/python, on app-client (10.50.0.60)
import pymqi
from pymqi import CMQC as C

cd = pymqi.CD(ChannelName=b"APP.SVRCONN",
              ConnectionName=b"10.50.0.11(1414),10.50.0.12(1414),10.50.0.13(1414)",
              TransportType=C.MQXPT_TCP)
cd.ChannelType   = C.MQCHT_CLNTCONN
cd.SSLCipherSpec = b"ANY_TLS13_OR_HIGHER"
sco = pymqi.SCO(KeyRepository=b"/home/vagrant/ssl/key")   # -> /home/vagrant/ssl/key.kdb
sco.CertificateLabel = b"app-client"

qmgr = pymqi.QueueManager(None)
qmgr.connect_with_options(b"NHAUAPP", cd=cd, sco=sco)

md = pymqi.MD()
# report options:  md.Report = C.MQRO_COA | C.MQRO_COD; md.ReplyToQ/ReplyToQMgr
# grouped (v2):     md.Version = C.MQMD_VERSION_2; md.GroupId/MsgSeqNumber/MsgFlags
# named properties: md.Format = C.MQFMT_RF_HEADER_2 and prepend an MQRFH2 <usr> folder
pmo = pymqi.PMO(Options=C.MQPMO_NO_SYNCPOINT | C.MQPMO_NEW_MSG_ID)
q = pymqi.Queue(qmgr, b"<TARGET.QUEUE>", C.MQOO_OUTPUT)
q.put(b"PROBE-PAYLOAD", md, pmo)
print("MsgId:", md.MsgId.hex())     # correlate against copies / reports later
q.close(); qmgr.disconnect()
```

For a report-options probe, browse the `ReplyToQ` afterwards: a generated report
is `MsgType 4` (`MQMT_REPORT`), its `CorrelId` equals the original `MsgId`, and
`PutApplName` names the **generating** queue manager (so you can tell which QM
produced a COA/COD). `Feedback 259 = MQFB_COA`, `260 = MQFB_COD`.

---

## 6. Quick reference

| Need | Command |
|---|---|
| See the lab guests | `virsh -c qemu:///system list --all` |
| Node map / status | `.venv/bin/mqlab vm inventory` · `mqlab status` |
| SSH to a node | `ssh -i ~/.vagrant.d/insecure_private_key -o UserKnownHostsFile=/dev/null -o StrictHostKeyChecking=no vagrant@10.50.0.<n>` |
| Find active Native-HA node | `sudo dspmq -o nativeha -x` on a1/a2/a3 |
| DISPLAY queues | `printf "DISPLAY QLOCAL(*)\n" \| sudo -u mqm runmqsc <QM>` |
| Browse a queue (MQMD dump) | `sudo -u mqm /opt/mqm/samp/bin/amqsbcg <QUEUE> <QM>` |
| Custom MQMD / reports / groups / props | pymqi on app-client (`10.50.0.60`), `APP.SVRCONN` mutual TLS |

Addresses and the Vagrant insecure key here are lab-internal and not
client-identifiable; keep it that way.
