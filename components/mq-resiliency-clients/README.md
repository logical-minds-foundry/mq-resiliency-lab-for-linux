# mq-resiliency-clients

The lab's IBM MQ reference clients and the DR measurement framework, packaged as one
standalone component (import package `mqrc`). It holds the always-on request/reply
load, the counterparty responder, the Native HA benchmark client, the authorization
and dead-letter probes, the reconnect diagnostic, and the DR flow generator, Watcher
responder and loss analyzers, with the pure-Python `mqrc.dr` ledger, classifier and
report core that they share.

It follows the guest component contract in [`../README.md`](../README.md). Design:
epic [logical-minds-foundry/.github#294](https://github.com/logical-minds-foundry/.github/issues/294)
(spec §5.5).

## Dependencies

There are no base dependencies. The MQI binding is the **`mqi` extra**
(`ibmmq==2.1.1`, IBM's maintained binding). x86_64 guests install its hash-locked
abi3 wheel. Upstream ships no Linux aarch64 wheel, so aarch64 guests compile the
hash-locked sdist against the host's MQ SDK, with the `sdist-build` group
(`setuptools`) pre-installed as its build backend. Every module does a **lazy**
`import ibmmq`, inside the functions that call the MQI. Tests run **without** the
extra and use a fake `ibmmq` module, because the dev VM has no MQ SDK.

A fake cannot catch a name the real binding lacks, because the fake is written from
the same belief as the code (#1478). So `mqrc.ibmmq_refs` reads every `ibmmq.*` name
and class member the package uses off its own source, and two checks hold it to the
**real** ibmmq: `tests/test_ibmmq_refs.py` against the sdist pinned in `uv.lock`
(fetched and hash-verified; set `MQRC_IBMMQ_SDIST` to a local copy; a failed fetch
fails, never skips), and the selfcheck against the compiled binding on every install.

## Client connections (a temporary workaround)

Every client connects through one helper, `mqrc.mqconn.connect_client`, which always
adds `MQCNO_CLIENT_BINDING` to the caller's connect options (#1480).
`tests/test_mqconn.py` fails if any module connects another way.

- **[data]** ibmmq 2.1.1's `connect_with_options()` packs the MQCNO before it adds
  the automatic client binding for a supplied CD, so the flag never reaches MQCONNX.
  On a guest with the full MQ server install (`svc-sim`, `app-client`, `mon-probe`),
  the MQI then defaults to local bindings and the connect fails: 2058, or 2046 with
  `MQCNO_RECONNECT`. Setting `MQ_CONNECT_MODE=CLIENT` did not force client mode on the
  lab guests. The reasoning and the code references are in `src/mqrc/mqconn.py`.
- Reported upstream as
  [ibm-messaging/mq-mqi-python#29](https://github.com/ibm-messaging/mq-mqi-python/issues/29).
- **[judgment]** The forced flag is a workaround, not a design choice. At the next
  MQ upgrade,
  [#1545](https://github.com/logical-minds-foundry/mq-resiliency-lab-for-linux/issues/1545)
  checks whether upstream has fixed it, and removes the flag if so.

## Putting messages: a fresh MQMD per put

`MsgId` is an input/output field on MQPUT: the queue manager writes the MsgId it
generates back into the MD you pass. A client that reuses one MD across a put loop
therefore resends that MsgId on every later put, and all of its messages share one
MsgId (#1549). Every put site builds a fresh `ibmmq.MD(...)` and also sets
`MQPMO_NEW_MSG_ID`; follow the same pattern in a new client.

## Entry points

| Command | Module | Purpose |
|---|---|---|
| `mq-app-requester` | `mqrc.app_requester` | steady request/reply load + round-trip textfile |
| `mq-bench` | `mqrc.bench_client` | Native HA commit-latency benchmark (JSONL record); Native HA only (#1380) |
| `mq-svc-responder` | `mqrc.svc_responder` | counterparty responder: reply with the request's correlation id |
| `mq-authz-probe` | `mqrc.authz_probe` | induced-denial authorization probe |
| `mq-dlq-probe` | `mqrc.dlq_probe` | dead-letter queue inspector |
| `mq-reconnect-probe` | `mqrc.reconnect_probe` | does client auto-reconnect ride a QM restart? It connects over plaintext `APP.SVRCONN`, which the lab's TLS-only channels refuse, so it is not run against the current stacks |
| `mq-dr-flow` | `mqrc.dr_flow` | app-side DR flow generator (app ledger); takes only its own replies, matched on a per-run CorrelId, and exits non-zero if either thread fails |
| `mq-dr-responder` | `mqrc.dr_responder` | SVC-side Watcher responder (Watcher ledger); copies the request CorrelId onto the reply and dead-letters a message with no DRv1 body |
| `mq-dr-baseline` | `mqrc.dr_baseline` | no-fault self-correctness check (RPO 0) |
| `mq-dr-forced` | `mqrc.dr_forced` | forced cross-site DR loss attribution (RPO > 0) |
| `mq-resiliency-clients-selfcheck` | `mqrc.selfcheck` | import every module, assert CPython 3.14, prove the real ibmmq binding when the `mqi` extra is installed, and that it has every name and class member the package uses |

`mq-bench` is a **Native HA** persistent-commit benchmark: it measures the commit cost of
Native HA replication (async CRR vs strict IRR) by PUTting persistent messages to
`APP.REPLY` on a Native HA app QM (`NHARCAPP` / `NHARIAPP`). It is not a benchmark for
RDQM or Pacemaker stacks, so the deployer renders its `/home/vagrant/mq-bench` wrapper
only when it provisions a Native HA stack. When a run produces no commits, it reports
the MQ reason codes it saw. It says "QM unreachable" only when every failure was a
connection failure; for example, 2035 `MQRC_NOT_AUTHORIZED` is reported as a refusal.

## Units and configuration

The static units in `systemd/` install to `/usr/lib/systemd/system/`. Site values come
from deployer-rendered environment files under
`/etc/opt/logical-minds-foundry/mq-resiliency-clients/`:

| Unit | Env file | Keys |
|---|---|---|
| `mq-app-requester.service` | `mq-app-requester.env` | `QM`, `CONN`, `CHANNEL`, `RATE`, `MSG_SIZE`, `TEXTFILE`, `TLS_ARGS` (empty, or `--keyrepo … --certlabel …`) |
| `mq-svc-responder@.service` (instance = in-queue) | `mq-svc-responder.env` | `QM`, `CHANNEL`, `CONN`, `KEYREPO`, `CERTLABEL` |

The responder must start after its SVC QM's own unit. That unit's name is site-specific,
and systemd never expands environment variables in `[Unit]`, so the deployer supplies
the ordering as a drop-in, `/etc/systemd/system/mq-svc-responder@.service.d/qm.conf`
(`After=` and `Requires=mq-<QM>.service`).

## Development

```bash
uv --directory components/mq-resiliency-clients run --frozen pytest
uv --directory components/mq-resiliency-clients run ruff check
mqlab component build mq-resiliency-clients   # the pinned-runtime gate + staged artifact
```
