# mq-resiliency-observability

Stdlib-only, non-MQI collectors for IBM MQ HA/DR state. Each one probes the cluster's
own command-line tools (`crm_mon`, `drbdsetup`, `dspmq`, `rdqmstatus`, `df`/`ls`),
never the MQI, and renders a node_exporter textfile. It supplements the stock MQ
Prometheus exporter with HA/DR facts the exporter cannot see.

Import package: `mqro`. Runtime: CPython 3.14 (`requires-python = "==3.14.*"`).
No third-party runtime dependencies.

## Entry points

| Entry point | Module | Writes |
|---|---|---|
| `lab-cluster-state --role {cluster,storage}` | `mqro.clusterstate` | `lab_cluster_state.prom` (Pacemaker + DRBD) |
| `lab-nativeha-state --qm <QM>` | `mqro.nativehastate` | `lab_nativeha_state.prom` (Native HA / CRR) |
| `lab-loglifecycle-state --qm <QM>` | `mqro.loglifecycle` | `lab_loglifecycle_state.prom` (Native HA log health) |
| `lab-rdqm-state --qm <QM>` | `mqro.rdqmstate` | `lab_rdqm_state.prom` (RDQM HA / DR / DRBD) |
| `mq-resiliency-observability-selfcheck` | `mqro.selfcheck` | stdout: imports every module, asserts CPython 3.14, prints the installed version |

Textfiles land in `/var/lib/node_exporter/textfile/` by default (`--out` overrides).

## systemd units and configuration keys

The units in `systemd/` are static and install to `/usr/lib/systemd/system/`. Each
`.service` reads its site values from a deployer-owned environment file at
`/etc/opt/logical-minds-foundry/mq-resiliency-observability/<unit>.env`:

| Unit | Environment file | Keys |
|---|---|---|
| `lab-cluster-state.{service,timer}` | `lab-cluster-state.env` | `ROLE` (`cluster` or `storage`) |
| `lab-nativeha-state.{service,timer}` | `lab-nativeha-state.env` | `QM` (runs both `lab-nativeha-state` and `lab-loglifecycle-state`) |
| `lab-rdqm-state.{service,timer}` | `lab-rdqm-state.env` | `QM` |

Every timer fires every 5 seconds.

## Development

```bash
uv lock
uv run pytest
uv run ruff check && uv run ruff format --check
```
