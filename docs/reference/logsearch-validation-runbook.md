# Validating the logsearch tier (cold-rebuild + snapshot round-trip)

A repeatable procedure to prove the `logsearch` tier (epic
`logical-minds-foundry/.github#149`) builds from nothing and operates end-to-end
on a live lab: a one-pass cold rebuild, fleet-wide log ingestion, full-text
search + aggregation, and host-durable snapshot/restore. It is the operational
`validation` task #835. Architecture background is in
[`box-model.md`](../development/box-model.md) and the site docs
(`docs/site/docs/architecture/index.md`).

## What it proves

| # | Scenario | Expected |
|---|---|---|
| **L1** | Box bake | `mqlab box build logsearch-ubuntu2404` bakes an inert fat box (OpenSearch + Dashboards + Data Prepper + node-exporter + alloy install halves), one-pass, no manual fixups. |
| **L2** | Cold bring-up | `mqlab commons up` renders topology, brings up the `logsearch` node (mgmt plane `10.50.0.4`) and runs `site-logsearch.yml` — OpenSearch, Data Prepper and Dashboards start; the fan-out gate is rendered. |
| **L3** | Health | `mqlab logsearch status` → `cluster: green` on a single node (`number_of_replicas:0`, so green is honest not luck) and a `disk:` line. Dashboards reachable via `mqlab logsearch open`. |
| **L4** | Ingestion | Fleet-wide fan-out flows: every node's alloy writes the same corpus Loki receives to OpenSearch via Data Prepper (`:21892`). Daily index `logs-YYYY.MM.DD`, doc count **rising**. |
| **L5** | Search + aggregate | A full-text `match` query returns hits; an events-per-hour `date_histogram` (bucketed by host) returns non-empty. |
| **L6** | Snapshot round-trip | `mqlab logsearch snapshot` captures `logs-*` and fetches the tarball to `build/state/logsearch/`; after `destroy logsearch` + re-provision, restore-on-bring-up brings the corpus back. |
| **L7** | Empty-store tradeoff | A destroy with **no** host snapshot re-provisions to a clean empty store (a logged no-op, not a failure). |
| **L8** | Loud-not-silent | `status` reports disk-used and would surface a read-only / flood-stage-full store loudly (`read_only_allow_delete` index block), failing rather than lying. |

## Prerequisites

- Run from a dev session with lab access (see
  [`operating-the-lab-from-a-dev-session.md`](../development/operating-the-lab-from-a-dev-session.md)).
- Drive `mqlab` from the **host venv** so the bake's `ansible-playbook` resolves:
  prepend `.venv-host/bin` to `PATH` (a bare `.venv/bin/mqlab` has no ansible and
  the bake dies `ansible-playbook: command not found`).
- The tier is baked from the merged roles; the box bake is the authoritative way
  to get there (a `dspmqver`-style drift check is not enough).

## Step 1 — cold rebuild (L1, L2)

```bash
export PATH="$PWD/.venv-host/bin:$PATH"
mqlab box build logsearch-ubuntu2404 obs-ubuntu2404 mq-ubuntu2404 infra-ubuntu2404
mqlab commons up
```

Box bake is a **separate step before** the bring-up — `commons up` does **not**
bake boxes; it renders topology (via `_prepare_lab`, which also writes
`build/work/lab/topology.resolved.yaml` the Vagrantfile needs) and `vagrant up`s
the registered boxes. Bake every box the commons needs (`obs`, `mq-ubuntu2404`
for svc/app/probe, `infra-ubuntu2404` for the DNS pair, `logsearch`) or the
bring-up fails `Couldn't open file lab/<box>` when vagrant can't find one.

## Step 2 — baseline (L3, L4, L5)

```bash
mqlab logsearch status          # cluster: green (healthy); disk: NN% used
mqlab logsearch open            # prints the Dashboards + Discover URLs
IP=10.50.0.4
curl -s "http://$IP:9200/logs-*/_count"                 # rising between calls
curl -s "http://$IP:9200/logs-*/_search" -H 'Content-Type: application/json' \
  -d '{"query":{"match":{"body":"server"}}}'            # full-text hits
curl -s "http://$IP:9200/logs-*/_search" -H 'Content-Type: application/json' \
  -d '{"size":0,"aggs":{"per_hour":{"date_histogram":{"field":"time","fixed_interval":"1h"},
       "aggs":{"by_host":{"terms":{"field":"log.attributes.host.keyword"}}}}}}'
```

Ingestion resumes only once Data Prepper is up; on first bring-up alloy ships the
journald backlog, so the count climbs fast then tracks live events.

## Step 3 — snapshot round-trip (L6, L7)

```bash
mqlab logsearch snapshot        # -> build/state/logsearch/snap-<utc>.tar.gz
# fresh node:
( cd lab && vagrant destroy -f logsearch )   # see gotcha below if it refuses
mqlab commons up                # re-provision; restore-on-bring-up restores logs-*
curl -s "http://10.50.0.4:9200/logs-*/_count"            # corpus present again
```

For L7, clear the host store (`rm build/state/logsearch/*.tar.gz`) before the
destroy: re-provision then logs `starting with an empty store` and comes up clean.

## Step 4 — loud-not-silent (L8)

`mqlab logsearch status` always prints the `disk:` line; if OpenSearch has flipped
any index to `read_only_allow_delete` at the flood-stage watermark, `status`
reports it loudly and exits non-zero. (Exercised in unit tests;
`read_only_indices` is the authoritative full signal, the disk line is advisory.)

## Gotchas surfaced by this validation

- **Drive the bake from `.venv-host`** — `.venv/bin/mqlab` lacks `ansible-playbook`
  (exit 127 mid-bake).
- **`vagrant destroy logsearch` can refuse** with a vagrant-libvirt state desync
  (`Name 'lab_logsearch' … already taken` on a *destroy*, when vagrant's machine
  id file is gone but the domain still runs). Force via libvirt and re-provision:
  `virsh -c qemu:///system destroy lab_logsearch && virsh -c qemu:///system undefine lab_logsearch --remove-all-storage`.
- **Snapshot names must be lowercase** and the fs-repo transport needs `--become`
  (both fixed in #962) — see the run record.
- **Don't take ad-hoc full snapshots into the fs repo.** The snapshot tarball
  captures the whole repo dir, and restore-on-bring-up restores the lexical-latest
  snapshot; a manual full/system-index snapshot (or a non-`snap-<utc>` name) will
  be picked over the CLI's `logs-*` snapshots and can collide with the fresh
  cluster's system indices on restore. The designed flow only ever creates
  `logs-*`, timestamp-named snapshots, which restore cleanly.

## Run record — 2026-08-07

**Outcome: SUCCESS (after fixes).** The cold rebuild did its job as the epic's
proof: nothing had ever baked and operated the tier end-to-end, and it surfaced
three blocking bugs, all fixed. With the fixes applied the full tier builds and
operates — L1–L8 all pass.

Bugs surfaced and fixed:

| Issue | Bug | Fix |
|---|---|---|
| #952 | `build-fatbox.sh` + `_manifest-hash.sh` rejected `logsearch-ubuntu2404` (the shell allowlists had drifted from the Python FLEET) — box un-bakeable | added the box to both shell `case` arms + usage; regression tests guard the FLEET↔shell drift |
| #960 | `opensearch-dashboards-plugin remove` refused to run as root during the (root) bake | `--allow-root` on the plugin remove |
| #962 | `mqlab logsearch snapshot` generated an uppercase (invalid) name → 400; the fs-repo transport ran ansible without `--become` so it couldn't read the `opensearch:opensearch 0750` repo | lowercase the name; `--become` on all four transport builders |

Evidence (10.50.0.4): `status` → `cluster: green (healthy)`, `disk: 31% used
(5.7gb/18.3gb)`; `logs-2026.08.07` doc count observed rising 99 → 2468 → 6001;
`match "server"` → hits; `date_histogram` by host → `obs` and `mon-probe` both
ingesting; `mqlab logsearch snapshot` → `snap-20260807t153535z.tar.gz`
(`indices=[logs-2026.08.07]`) in `build/state/logsearch/`; destroy + re-provision
restored `logs-2026.08.07` (restore task `failed=0`, index green with data);
empty-store destroy came up clean (logged no-op).
