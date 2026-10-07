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
(`pymqi==1.12.13`). pymqi ships only as an sdist and compiles against the host's MQ
SDK, so guests install it from the hash-locked sdist with the `sdist-build` group
(`setuptools`, `wheel`, and `packaging` transitively). Every module imports pymqi
**lazily**, inside the functions that call the MQI. Tests run **without** the extra
and use a fake `pymqi` module, because the dev VM has no MQ SDK.

## Entry points

| Command | Module | Purpose |
|---|---|---|
| `mq-app-requester` | `mqrc.app_requester` | steady request/reply load + round-trip textfile |
| `mq-bench` | `mqrc.bench_client` | Native HA commit-latency benchmark (JSONL record); Native HA only (#1380) |
| `mq-svc-responder` | `mqrc.svc_responder` | counterparty responder: reply with the request's correlation id |
| `mq-authz-probe` | `mqrc.authz_probe` | induced-denial authorization probe |
| `mq-dlq-probe` | `mqrc.dlq_probe` | dead-letter queue inspector |
| `mq-reconnect-probe` | `mqrc.reconnect_probe` | does client auto-reconnect ride a QM restart? |
| `mq-dr-flow` | `mqrc.dr_flow` | app-side DR flow generator (app ledger) |
| `mq-dr-responder` | `mqrc.dr_responder` | SVC-side Watcher responder (Watcher ledger) |
| `mq-dr-baseline` | `mqrc.dr_baseline` | no-fault self-correctness check (RPO 0) |
| `mq-dr-forced` | `mqrc.dr_forced` | forced cross-site DR loss attribution (RPO > 0) |
| `mq-resiliency-clients-selfcheck` | `mqrc.selfcheck` | import every module, assert CPython 3.14, prove the real pymqi when the `mqi` extra is installed |

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
