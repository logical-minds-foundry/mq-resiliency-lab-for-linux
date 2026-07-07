# FQDN migration inventory (DNS epic, Task C1 · #479)

Where the lab uses raw IP addresses in its **MQ connection surface**, and the
disposition of each: migrate to a generated FQDN, or stay a literal IP with a
stated reason. This is the deliverable of C1 and the worklist for **C2 (#480)**.

Guiding rule (epic #21 doctrine): **err toward minimal dependency.** Names go on
the app↔service axis (where realism and the security guides need them); the
HA/replication fabric and the observability/Watcher plane stay on literal IPs so
they never depend on the breakable DNS. The generated names are those `mqlab dns
render` produces (`<host>-<plane>.<zone>`, `<short>-vip.client.com`).

## Migrate → FQDN

All of these are MQ `CONNAME`s on the **data** (`net-data-a/b`) or **inter-business**
(`net-ext`) planes. In the site playbooks they are threaded as vars, so C2 changes
the var *value*, not scattered literals.

| Surface | Location | Current (IP) | Proposed FQDN |
|---|---|---|---|
| Cross-org CONNAME — our QM → the SVC counterparty | `svc_conn` in `site-*.yml`; `svc.conn` in `topology.yaml` | `10.60.0.50` | `svc-sim-ext.service.com` |
| Our-side ext CONNAME — SVC → our Native-HA QM (3 instances) | `our_conn` in `site-nativeha*.yml` | `10.60.0.11-13(1414)` | `nha-ubuntu-a{1,2,3}-ext.client.com(1414)` (per arm) |
| App data-plane CONNAME — Native-HA arms (3 instances) | `app_conn` in `site-nativeha*.yml` → `app-requester` role | `10.10.1.11-13(1414)` | `nha-ubuntu-a{1,2,3}-data-a.client.com(1414)` (per arm) |
| App data-plane CONNAME — VIP arms (pcmk/rdqm) | `app_conn` (the stack VIP) | `10.10.1.200` / `10.10.1.100` | `pcmk-vip.client.com` / `rdqm-vip.client.com` |
| App default/fallback CONNAME (site-A + site-B VIP) | `DEFAULT_CONN` in `clients/app_requester.py` | `10.10.1.200(1414),10.10.2.200(1414)` | `pcmk-vip.client.com(1414),`**`pcmk-vip-b.client.com`**`(1414)` — ⚠ see gap 1 |
| DR-flow client CONNAME | `clients/dr_flow.py` | (1 addr) | → FQDN (confirm target during C2) |
| Partner-facing ext CONNAME (pcmk) | `our_conn` (pcmk/rdqm arms) | `10.60.0.10` (`vip_ext`) | `pcmk-vip-ext.client.com` |

Note: the VIP itself is still **bound** by IP (Pacemaker `IPaddr2` / `rdqmint`
floating IP) — only the CONNAME *to* the VIP becomes a name.

## mqweb admin REST/Console endpoint — data-plane infrastructure (#39)

The `mqweb` admin REST/Console endpoint (`9443/HTTPS`, one per QM node) is a
**data-plane infrastructure** surface — the control surface co-located with the QM,
part of what the lab *instruments*, **not** part of the Watcher/observability plane
observing it. It had drifted toward being treated as a management-plane service
(undefined address, never classified here); epic #39 corrects that. Its canonical
published address is on the data plane:

| Stack | Published endpoint | Proposed FQDN |
|---|---|---|
| pcmk / RDQM | the QM's data-plane VIP `:9443`, **each site** (`vip` / `vip_b`) | `pcmk-vip-a/-b.client.com:9443` · `rdqm-vip-a/-b.client.com:9443` |
| Native HA (no VIP) | the **active** instance's `net-data` node IP `:9443`, runtime-resolved (`dspmq -o nativeha`) | `nha-{ubuntu,rhel}-a{1,2,3}-data-a.client.com:9443` (candidate set) |
| `svc-sim` (counterparty) | its `net-ext` address `:9443`, administered **lab-only** | `svc-sim-ext.service.com:9443` — outside "our data plane"; a real estate would not administer the counterparty's mqweb |

mqweb binds `httpHost=*` (so it also answers on `net-mgmt`), but the address the lab
**publishes and uses** is the data-plane one above. Enforcing this at the network
layer — refusing `9443` on mgmt — is deferred to the firewall / plane-enforcement
follow-on epic. The topology-derived source of truth for these endpoints is
`mqlab rest render` (epic #39, task #537).

## Stay literal IP (with reason)

| Surface | Location | Reason |
|---|---|---|
| Heartbeat `172.16.1-2.x` | `_*-cluster-ha.yml` `hb:`; Native HA `ReplicationAddress` | HA/replication fabric — must survive a DNS outage (minimal-dependency); the fault suite severs this plane |
| Cross-site WAN replication `10.99.0.x` | `_*-dr-replication.yml` `peer_wan_addrs` | CRR/DR replication fabric |
| SAN / DRBD / iSCSI `10.40.x` | `drbd-san`, iscsi roles | storage-replication fabric |
| Observability | `mq-exporter`, `alloy` defaults; Prometheus scrape targets; mgmt `10.50.0.x` | the Watcher/observability plane is deliberately IP — admitted-unrealistic instrumentation (epic doctrine) |
| Node addressing | `lab/topology.yaml` `nics:` | source of truth — the IPs are the definition FQDNs are *generated from*, not references to migrate |
| VIP bindings | Pacemaker `IPaddr2`, `rdqmint -f` | the floating IP is an interface binding, not a name lookup |

Admin readability: where a fabric IP is kept, the host still gets its lab FQDN in
`/etc/hosts` (B4), so an operator reading the config sees a name alongside the IP.

## Gaps / dependencies for C2

1. **Site-B VIP has no generated FQDN.** `bind.py` `_vips()` emits `<short>-vip`
   only from the site-A `qm.vip` (and `<short>-vip-ext` from `vip_ext`); the
   site-B DR VIP (`10.10.2.200`, `10.10.2.100`) and site-B instance addresses are
   absent from the zones. Any DR CONNAME that names the site-B VIP
   (`app_requester.py` `DEFAULT_CONN`, DR flows) needs a site-B name first —
   e.g. add `<short>-vip-b.client.com` to the topology + generator. Small
   B-style follow-up; do it before (or as the first step of) C2.
2. **Per-arm confirmation.** `our_conn`/`app_conn` are per-arm literals across all
   four arms (nha-ubuntu, nha-rhel, rdqm, pcmk); confirm each swapped FQDN matches
   a name the generator actually emits (spot-checked live for nha-ubuntu: `dig`
   resolves `nha-ubuntu-a2-data-a.client.com` → `10.10.1.12`).
3. **`REVDNS` stays `ENABLED`** (QM default; already the case) — hostname
   CONNAMEs are forward-resolved and hostname CHLAUTH rules keep working. No QM
   attribute change is part of the sweep.

## 50/50s (decide case-by-case in C2, default least-dependency)

- **mgmt-plane admin references**: leave IP (Watcher), unless a specific one is
  read by a human often enough that a name earns its keep.
- **Native-HA `our_conn` (net-ext)**: names improve log/debug legibility across
  the org boundary (the security-guide payoff) — migrate.
