# Reference

Quick-lookup reference for the lab: the node/network **topology**, the queue
managers' published **REST endpoints**, the **`mqlab` CLI verb map**, and a
curated pointer to the in-repo **gotchas**. Everything here is **as-built** and
derives from a single source of truth — `lab/topology.yaml` — so it stays in
lockstep with the lab. For the narrative walk-through of how these pieces fit
together, see [Architecture](../architecture/index.md); to drive the running
lab, see [Operate & Observe](../operate/index.md).

The lab pins IBM **MQ 9.4** (currently `9.4.5.0`, see
[`ansible/group_vars/all/versions.yml`](https://github.com/logical-minds-foundry/mq-resiliency-lab-for-linux/blob/develop/ansible/group_vars/all/versions.yml)).

## Topology

The lab shape — guest VMs, networks, role×site groups, and the stacks that
compose them — is declared in
[`lab/topology.yaml`](https://github.com/logical-minds-foundry/mq-resiliency-lab-for-linux/blob/develop/lab/topology.yaml),
the single source of truth. The Ansible inventory, the REST endpoint map, and
the observability targets are all rendered from it — never hand-maintained.
Render the live inventory with `mqlab vm inventory`.

### Stacks

Four HA/DR stacks, each a full **3+3** deployment (a 3-node group in Data
Center A with an asynchronous DR/CRR relationship to a matching 3-node group in
Data Center B). Queue-manager names derive from each stack's `short` token
(`<short>APP` / `<short>SVC`), so no QM literal is hardcoded.

| Stack | Mechanism | OS | `short` | Site-A groups | Site-B groups | QM VIP (A / B) |
|---|---|---|---|---|---|---|
| `pcmk-ubuntu` | Pacemaker/SAN (shared-storage HA) | Ubuntu | `PCMK` | `san_a`, `pcmk_a` | `san_b`, `pcmk_b` | `10.10.1.200` / `10.10.2.200` |
| `rdqm-rhel` | RDQM (replicated-storage HA) | RHEL | `RDQM` | `rdqm_a` | `rdqm_b` | `10.10.1.100` / `10.10.2.100` |
| `nativeha-rhel` | Native HA (log-replicated HA) | RHEL | `NHAR` | `nha_rhel_a` | `nha_rhel_b` | per-instance (no VIP) |
| `nativeha-ubuntu` | Native HA (log-replicated HA) | Ubuntu | `NHAU` | `nha_ubuntu_a` | `nha_ubuntu_b` | per-instance (no VIP) |

Native HA has no floating VIP — clients reach the active instance directly (see
[REST endpoints](#rest-endpoints) below). A single shared **counterparty**
(`SVCQM` on `svc-sim`, `10.60.0.50`) sits across the inter-business WAN; each
stack owns its own `{SHORT}.SVC.REQUEST` queue and responder.

### Networks

Every node carries a `net-mgmt` NIC; the data / heartbeat / WAN / SAN / external
planes are attached per role. The management plane is deliberately non-transit
("the Watcher").

| Network | Subnet | Role |
|---|---|---|
| `net-mgmt` | `10.50.0.0/24` | Management plane — Ansible control, observation; non-transit |
| `net-data-a` | `10.10.1.0/24` | Site-A data plane (client ↔ QM) |
| `net-data-b` | `10.10.2.0/24` | Site-B data plane (client ↔ QM) |
| `net-hb-a` | `172.16.1.0/24` | Site-A heartbeat / replication |
| `net-hb-b` | `172.16.2.0/24` | Site-B heartbeat / replication |
| `net-wan` | `10.99.0.0/24` | Cross-site WAN (DR/CRR replication) |
| `net-ext` | `10.60.0.0/24` | Inter-business WAN to the `svc-sim` counterparty |
| `net-san-a` | `10.40.1.0/24` | Site-A SAN (Pacemaker/iSCSI) |
| `net-san-b` | `10.40.2.0/24` | Site-B SAN (Pacemaker/iSCSI) |

The guest fleet is the four 3+3 stacks (24 nodes), two SAN targets (`san-a`,
`san-b`), and six shared commons (`obs`, `mon-probe`, `svc-sim`, `app-client`,
`infra-client`, `infra-svc`). An **optional** seventh mgmt-plane node — `logsearch`
(`net-mgmt` `10.50.0.4`), the single-node OpenSearch log-search tier (see
[Architecture](../architecture/index.md#the-log-search-tier-full-text-over-the-log-corpus-logsearch))
— joins the fleet when the topology carries it. See `lab/topology.yaml` for the
authoritative per-node NIC and resource allocation.

## REST endpoints

Each queue manager's admin REST API / Console (Liberty `mqweb`, HTTPS on
**`9443`**) is a published endpoint derived from the topology. Render the
current map (both sites) to `build/work/rest/endpoints.json` with:

```bash
mqlab rest render
```

Each record is `{stack, kind, endpoints}`, where `kind` is one of:

- **`vip`** — the stack's floating HA VIP (`pcmk-ubuntu`, `rdqm-rhel`); one URL
  per site.
- **`active-instance`** — every instance's URL (Native HA stacks, which have no
  VIP); the client resolves the active one.
- **`counterparty`** — the shared `svc-sim` endpoint on `net-ext`.

| Stack | Kind | Site-A endpoint(s) | Site-B endpoint(s) |
|---|---|---|---|
| `pcmk-ubuntu` | `vip` | `https://10.10.1.200:9443` | `https://10.10.2.200:9443` |
| `rdqm-rhel` | `vip` | `https://10.10.1.100:9443` | `https://10.10.2.100:9443` |
| `nativeha-rhel` | `active-instance` | `10.10.1.91-93:9443` | `10.10.2.91-93:9443` |
| `nativeha-ubuntu` | `active-instance` | `10.10.1.11-13:9443` | `10.10.2.11-13:9443` |
| `svc-sim` | `counterparty` | `https://10.60.0.50:9443` | — |

The administrative REST credentials (the `MQWEB_ADMIN_*` login) are
runtime-injected, never committed. For the acceptance procedure that proves each
endpoint is live, see the
[mqweb endpoint verification runbook](https://github.com/logical-minds-foundry/mq-resiliency-lab-for-linux/blob/develop/docs/reference/mqweb-endpoint-verification.md).

## `mqlab` CLI verb map

`mqlab` is the operator orchestrator (a thin Typer veneer over Ansible and the
lab tooling). Run any command with `--help` for its full options. Top-level
commands:

| Command | What it does |
|---|---|
| `mqlab bootstrap` | Bring up a whole stack in one command: net → vms → provision → observe (add `--no-dr` for a lighter HA-site-only bring-up) |
| `mqlab teardown` | Destroy a stack's VMs; shared commons only when the last stack is down (or `--commons`) |
| `mqlab status` | Show phase completion (net/vms/provision/observe) for a stack or all stacks |
| `mqlab parity` | Print the cross-arm capability matrix (which verbs each arm supports) |
| `mqlab doctor` | Check this host can run the lab (arch, KVM, required tools) |

Command groups:

| Group | Subcommands | Purpose |
|---|---|---|
| `mqlab qm` | `create` · `destroy` · `up` · `down` · `status` | MQ queue-manager per-stack HA lifecycle (stack-dispatched) |
| `mqlab dr` | `cutover` · `failback` | Cross-site DR: cut a stack's live QM over to its DR peer (`cutover`, A→B) and back (`failback`, B→A) |
| `mqlab vm` | `inventory` · `roster` · `ssh` | Guest VM maps (Ansible inventory / salt-ssh roster) + interactive shell |
| `mqlab obs` | `targets` · `dashboard` · `net-state` · `reach-peers` · `open` | Observability renders (Prometheus targets, Grafana dashboard) + reach the Watcher |
| `mqlab rest` | `render` | Render the published mqweb REST endpoints (both sites) |
| `mqlab dns` | `render` | Render BIND zone files + `named.conf` + host resolver facts |
| `mqlab pki` | `ensure` · `issue` · `list` | Lab PKI / TLS certificate provider (org CAs, entity certs, keystores) |
| `mqlab commons` | `up` · `status` · `down` | Shared commons VMs (obs + probe + svc + app) — and, when present, the `logsearch` tier — independently of any stack |
| `mqlab logsearch` | `status` · `open` · `snapshot` · `restore` | Operate the optional log-search tier (single-node OpenSearch + Dashboards): cluster health, Dashboards URL, host-durable snapshot/restore |
| `mqlab box` | `status` · `build` · `rebuild` · `clean` · `gc` | Baked-box fleet lifecycle |
| `mqlab build` | `path` · `ensure` · `clean` · `status` · `migrate` | `build/` bucket lifecycle (cache/state/work/temp) |

## Gotchas & in-repo reference

Hard-won, symptom-first reference notes live in the repo under `docs/reference/`
(outside the docs site). Curated pointers:

- **[Lab gotchas playbook](https://github.com/logical-minds-foundry/mq-resiliency-lab-for-linux/blob/develop/docs/reference/lab-gotchas.md)**
  — symptom → cause → fix for libvirt / Vagrant / Pacemaker.
- **[Lab bootstrap runbook](https://github.com/logical-minds-foundry/mq-resiliency-lab-for-linux/blob/develop/docs/reference/lab-bootstrap.md)**
  — cold-start bring-up from a freshly-rebuilt VM.
- **[DRBD operations reference](https://github.com/logical-minds-foundry/mq-resiliency-lab-for-linux/blob/develop/docs/reference/drbd-operations.md)**
  — the Pacemaker/SAN replication layer, symptom-first.
- **[RDQM 3-node HA cheat sheet](https://github.com/logical-minds-foundry/mq-resiliency-lab-for-linux/blob/develop/docs/reference/rdqm-ha-cheatsheet.md)**
  and the
  **[RDQM DR/HA cold-build defect](https://github.com/logical-minds-foundry/mq-resiliency-lab-for-linux/blob/develop/docs/reference/rdqm-drha-no-ssh-fallback-defect.md)**.
- **[Native HA + CRR manual setup](https://github.com/logical-minds-foundry/mq-resiliency-lab-for-linux/blob/develop/docs/reference/nativeha-crr-setup-guide.md)**
  and its
  **[CRR TLS reference](https://github.com/logical-minds-foundry/mq-resiliency-lab-for-linux/blob/develop/docs/reference/nativeha-crr-tls-guide.md)**.
- **[MQ TLS coverage](https://github.com/logical-minds-foundry/mq-resiliency-lab-for-linux/blob/develop/docs/reference/tls-coverage.md)**
  — the lab-wide no-plaintext invariant, every path and its TLS state.
- **[mqweb endpoint verification](https://github.com/logical-minds-foundry/mq-resiliency-lab-for-linux/blob/develop/docs/reference/mqweb-endpoint-verification.md)**
  — acceptance that every stack's REST endpoint is live.
- **[Event-monitor wrapper validation](https://github.com/logical-minds-foundry/mq-resiliency-lab-for-linux/blob/develop/docs/reference/event-monitor-wrapper-validation.md)**
  — proving the self-healing `amqsevt` wrapper.
- **[Grafana image rendering](https://github.com/logical-minds-foundry/mq-resiliency-lab-for-linux/blob/develop/docs/reference/grafana-image-rendering.md)**
  — server-side PNG/PDF capture.
- **[DNS/FQDN inventory](https://github.com/logical-minds-foundry/mq-resiliency-lab-for-linux/blob/develop/docs/reference/dns-fqdn-inventory.md)**
  — where the MQ connection surface uses raw IPs vs FQDNs.
