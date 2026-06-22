# Changelog

All notable changes to this project will be documented in this file.

The format is based on [Keep a Changelog](https://keepachangelog.com/)
and this project adheres to [Semantic Versioning](https://semver.org/).

## [1.0.0] - 2026-06-09

### Bug fixes

- keep build/.gitkeep tracked (ignore build/* contents, not the dir)
- drop apt vagrant + HashiCorp repo from VM spec (#8)
- remove premature Python language assumption (#9)
- add missing Vergil 2.1 baseline entries to .gitignore (#11)
- enable nested virtualization and add libarchive-tools (#14)
- add UEFI guest appware packages (#20)
- TCG x86 root cause was CPU model, not speed - use cpu_mode maximum (#24)
- grant actions: read and disable SARIF upload on this private repo

### CI

- add CI and CD workflows

### Chores

- add vergil.toml profile and README table of contents
- add VERSION, .claude settings + guard hook, fold workflow rules into CLAUDE.md
- ignore python bytecode; drop committed pycache (#27)

### Documentation

- ground DR mandate and SVC MQ connectivity model in public references
- add Appendix C — MQ version strategy (9.4 baseline, 9->10 gap analysis, upgrade window)
- correct go-live framing — 2026-06-15 is the contract start, not a production go-live
- confirm RDQM runs on VMs (no bare-metal requirement) in section 6.1
- capture scale boundary condition (§1) and RDQM/RHEL-vs-Ubuntu tradeoff ledger (§2.7)
- add symmetric peer-sites design constraint (§4.7) and planned role-rotation test (§3.1)
- add table of contents with anchor links to design doc
- note SVC-constrained role rotation in §4.7, orthogonal to the 3+3 requirement
- expand §7 — choose Vagrant for the lab harness, surface Apple-Silicon provider bind
- make nested vagrant-libvirt the leading §7.2 harness hypothesis
- rework §7.4 — develop inside one large persistent dev+lab VM, collapsing the macOS/Linux boundary
- flesh out §8 (the tooling) — the actual product
- close §6 placeholder — state where Ubuntu/RHEL diverge (L0 only)
- fold pymqrest + dev-environment prior art into the design
- pushback fixes 1-3 — REST/HA architecture, security scope, reframe deliverable
- pushback fix 4 — local emulation by intent, functional-correctness scope, VM production stance
- pushback fix 5 — build both arms RDQM-first, complexity asymmetry as a finding, elevate storage-SPOF argument
- pushback fix 6 — sharpen 9.4-baseline rationale, 10.0 strictly forward-looking
- pushback fix 7 — content-plane credential hygiene (runtime-injected, never committed)
- move design spec and diagrams to docs/specs/
- add Phase-0 dev-environment bootstrap plan (Vergil profile + virgilize + build VM)
- add mkdocs docs-site scaffold
- reconcile contributor docs with v2.1 vm-profile schema
- Phase A implementation plan - virtualization harness (#15)
- Phase B implementation plan - standalone QM and end-to-end message path (#23)
- Phase C implementation plan - RDQM arm (#29)
- Phase D implementation plan - Ubuntu Pacemaker/SAN arm (#37) (#38)
- add tables of contents to all plans and reports; regenerate spec TOC (#41) (#42)
- DR/HA validation framework spec and implementation plans (#46)
- align DR framework plans with repo conventions (StrEnum, uv run pytest) (#52)
- capture message-path bring-up + operator-artifact resolution design (#59)
- docs site + architecture documentation design (issue #33)
- implementation plan for the docs site + architecture page (issue #33)
- revise plan from Task 1 spike: real build recipe + dormant Releases wiring

### Features

- add vergil-user VM profile and migrate to vergil v2.1
- multi-site network fabric: seven severable libvirt nets (#19)
- declarative topology + parameterized multi-machine Vagrantfile (#19)
- provider spike: KVM arm64 + severability proven; TCG x86 capped by plugin IP-wait (#19)
- 3+3 skeleton boots; connectivity-matrix smoke test green (#19)
- harness reproducibility proven; spike scaffolding removed (#19)
- python toolchain + FFH header codec; language restored (#27)
- phase-b topology nodes: qm-main, svc-sim, app-client (#27)
- MQ 9.4.5.0 arm64 fetch script (#27)
- bring-up plane: install roles + QM/REST/boot services (#27)
- declarative QM objects via pymqrest ensure_* (#27)
- FFH trade path live: 3/3 clean ACKs end-to-end (#27)
- e2e trade test green incl. unattended reboot survival (#27)
- Phase B convergence proven; apply CLI fully covered (#27)
- RHEL 9.6 kickstart box build + six RDQM topology nodes (#31)
- RDQM install + HA formation roles: site-A cluster online (#31)
- QMRDQM live: replicated QM + floating IP serving VIP traffic (#31)
- fault suite vs the HA group: 4/4 drills passed, RPO 0 throughout (#31)
- 3+3 DR proven: rdqmdr cutover and failback, RPO 0 both directions (#31)
- Phase D build - Ubuntu Pacemaker/SAN arm with cross-site DR (#39) (#40)
- DR/HA validation framework core instrument (Plan 1) (#48)
- DR framework wire format, snapshot parsing, scenario catalog (Plan 2 pure foundations) (#50)
- cache the RHEL box on host-durable repo-root build/ (survives base-VM rebuilds) (#58)
