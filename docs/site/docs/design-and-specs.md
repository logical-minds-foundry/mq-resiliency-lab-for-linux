# Design & Specs

The published site documents *what the lab is and how to run it*. The design
specs and findings reports below are the *why* — the engineering record of
how it was built. They live in-repo under `docs/specs/`, `docs/plans/`, and
`docs/reports/`.

## Design specs

- **[MQ cluster lab design](https://github.com/logical-minds-foundry/mq-cluster-tooling/blob/develop/docs/specs/2026-06-03-mq-cluster-lab-design.md)**
  — the authoritative design; the lab is built out in its §10 phases (A→G).
- **[DR/HA validation framework design](https://github.com/logical-minds-foundry/mq-cluster-tooling/blob/develop/docs/specs/2026-06-08-dr-ha-validation-framework-design.md)**
  — how disaster-recovery and high-availability behavior is validated.
- **[Docs site + architecture design](https://github.com/logical-minds-foundry/mq-cluster-tooling/blob/develop/docs/specs/2026-06-08-docs-site-and-architecture-design.md)**
  — the design behind this documentation site.

## Findings reports

- **[Phase A provider spike](https://github.com/logical-minds-foundry/mq-cluster-tooling/blob/develop/docs/reports/2026-06-06-phase-a-provider-spike.md)**
  — the virtualization-provider findings (UEFI/arm64, CPU mode, the
  weak-host-model test-design lesson).
- **[Phase C RDQM findings](https://github.com/logical-minds-foundry/mq-cluster-tooling/blob/develop/docs/reports/2026-06-06-phase-c-rdqm-findings.md)**
  — RDQM cluster formation and fault-drill results.
- **[Phase D Pacemaker findings](https://github.com/logical-minds-foundry/mq-cluster-tooling/blob/develop/docs/reports/2026-06-07-phase-d-pacemaker-findings.md)**
  — the Pacemaker/SAN arm findings.
- **[MQ diagnostic logging in JSON — a research study](https://github.com/logical-minds-foundry/mq-cluster-tooling/blob/develop/docs/reports/2026-06-19-mq-json-logging-research.md)**
  — every way MQ 9.4 emits JSON-format diagnostic logs (server/system/client +
  mqweb), the full text-log surface, and the JSON-only ingestion decision (#282).

## Related tooling

- **Operations &amp; observability (#32)** — health checks and a single
  lab-status verb for the running stacks (planned; this page will link the
  Operations section once it lands).
