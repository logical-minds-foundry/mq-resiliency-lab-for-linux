# Ansible vs Salt — ecosystem health, and the decision to park Salt

- **Issue:** #528
- **Date:** 2026-07-07
- **Companion to:** #197 / `docs/reports/2026-06-16-ansible-to-salt-evaluation.md`
- **Status:** final — research complete, **decision recorded: Salt is parked
  (lab stays Ansible-only)**.
- **Method:** multi-agent deep-research harness — 5 search angles, 21 sources
  fetched, 87 candidate claims, top 25 adversarially fact-checked with 3-vote
  verification (**25/25 confirmed, 0 refuted**). Maintenance-health facts are
  snapshotted **2026-07-06** and will drift; re-check before acting on them.

Every load-bearing claim below is tagged **[data]** (what a cited source
actually says) or **[judgment]** (our reasoning on top of it), per the project's
sources-are-load-bearing standard. Citations are inline; the full source list is
§8.

## Contents

1. [Bottom line & decision](#1-bottom-line--decision)
2. [How this relates to the #197 evaluation](#2-how-this-relates-to-the-197-evaluation)
3. [Feature-for-feature comparison](#3-feature-for-feature-comparison)
4. [Ecosystem tooling deep-dive](#4-ecosystem-tooling-deep-dive)
5. [Applying it to the lab (MQ + Pacemaker/Corosync + DRBD)](#5-applying-it-to-the-lab-mq--pacemakercorosync--drbd)
6. [The decision and its rationale](#6-the-decision-and-its-rationale)
7. [Caveats & what this does not rest on](#7-caveats--what-this-does-not-rest-on)
8. [Sources](#8-sources)

---

## 1. Bottom line & decision

Ansible and Salt are both mature configuration-management/orchestration tools, and
on raw architecture neither is "wrong." But on the axis this study prioritized —
the **health of the tooling and reusable-code / testing ecosystems** — they have
diverged sharply. **[data]** Ansible's Molecule test harness, `ansible-lint`, and
Galaxy are actively maintained through 2026; Salt's counterparts are largely
stalled — `salt-lint`'s last release was v0.9.2 in Feb 2023 (Snyk rates it
*Inactive*), `kitchen-salt`'s last release was Jan 2022, and flagship
`saltstack-formulas` repos sat 14+ months untouched as of the July 2026 snapshot.

**[judgment]** An ecosystem whose dedicated linter and standard test harness have
gone years without a release reads as one in decline. On a greenfield tooling
choice today, that signal alone is disqualifying — it would take Salt off the
shortlist before any feature comparison began.

**Decision — park Salt; the lab stays Ansible-only.** On pure technical merit for
this lab, Ansible is the correct tool with no real contest (§5). The only
remaining argument for doing a Salt port *in the lab* was employer-fidelity /
skills value — and that argument has since dissolved: the maintainer now works in
an environment that uses Salt directly, so the skills exposure is obtained there,
and none of that employer Salt work is reusable back into this lab. With the
fidelity rationale gone and the ecosystem signal pointing hard at Ansible, Salt is
**on hold, not implemented**. This supersedes the #197 recommendation (which was
GO on the content port, defer the HA class). See §6.

## 2. How this relates to the #197 evaluation

This report does **not** re-litigate #197 — it answers a different question, and
the two sit side by side:

| | #197 (2026-06-16) | This report (2026-07-07) |
|---|---|---|
| **Question** | *Can* we port the lab's own Ansible content to SLS, and what does it cost? | How *alive* are the Ansible and Salt ecosystems as a whole? |
| **Axis** | Internal — our footprint (~2,260 lines, 21 roles) → SLS translation tax | External — community formulas/roles, linting, testing frameworks, project vitality |
| **Method** | Paper analysis + one `prometheus` role spike (RUN, idempotent) | Multi-source web research, adversarially fact-checked |
| **Finding** | Content ports 1:1 for the most part; cost concentrates in the `changed_when`→`unless` idempotency tax and unsampled HA coordination | Salt's tooling/testing/linting ecosystem is materially staler than Ansible's; a current community Pacemaker role exists on the Ansible side |
| **Recommendation** | GO on content port, defer master/minion + HA | Park Salt (see §6) |

#197 established that the port is *feasible and not that expensive on the
salt-ssh baseline*. It touched ecosystem health only once — noting Salt had left
Ubuntu 24.04's repos post-VMware and now ships from the Broadcom repo (#197 §6).
This report is the deliberate follow-up on that thread, and it is the piece that
tips the decision from "GO, deferred" to "hold."

## 3. Feature-for-feature comparison

**[data]** unless noted. Sources: Salt architecture & ZeroMQ transport docs
(primary), Salt Reactor docs (primary), Red Hat's *Ansible vs Salt* (secondary),
phoenixNAP (secondary).

| Dimension | Ansible | Salt |
|---|---|---|
| **Agent model** | Agentless — push over OpenSSH; `ansible-pull` as an alternative; no persistent daemon (needs only a Python interpreter on the node, except the `raw` module) | Agent-based **by default** — `salt-master` + `salt-minion` daemons. Also supports agentless `salt-ssh` and masterless `salt-call --local` |
| **Transport / bus** | On-demand SSH fan-out; no standing connection | **ZeroMQ** pub/sub over persistent TCP (default); jobs published on **4505**, results returned on **4506**. TCP/Tornado is an alternative transport |
| **Push vs. pull** | Push on demand (or pull via `ansible-pull`) | Push-oriented, near-immediate remote execution: master publishes, minions (holding an outbound persistent connection) execute |
| **Event-driven** | None out of the box | **Reactor** — triggers actions on events matched by tag on the event bus. *Note:* Reactor SLS deliberately does **not** support requisites/ordering/`onlyif`/`unless`; non-trivial reactions delegate to the **Orchestrate** system |
| **Config model** | Task model — playbooks/roles (tasks, handlers, files, vars) | State model — SLS + formulas (states, files, vars) |
| **Templating** | Jinja2 | Jinja2 (plus other renderers) |
| **Facts / targeting** | Inventory + facts | Grains + bus targeting |
| **Scale posture** | Simple model; SSH fan-out bottlenecks at very large fleets | ZeroMQ bus built for high-speed messaging to large minion fleets — a genuine strength |
| **Orchestration** | Playbook ordering; AWX/`ansible-runner` control plane | Native Orchestrate runner + Reactor for reactive flows |

**[judgment] Fair framing.** The honest contrast is "agentless vs.
agent-**by-default**," not "agentless vs. agent-only" — Salt can run agentless
(`salt-ssh`) or masterless. And Ansible's "no software required" is imprecise:
managed nodes need a Python interpreter. The Reactor is Salt's one clear native
edge and is genuinely relevant to HA/DR (reacting to cluster events in
near-real-time) — but its deliberate limitations mean anything non-trivial gets
delegated to Orchestrate, so it is less "free HA automation" than it first looks.

## 4. Ecosystem tooling deep-dive

This is the core of the study and where the decision is actually made.

### 4.1 Reusable code — Galaxy roles/collections vs. Salt formulas

- **[data]** Structurally analogous: Salt's own docs state formulas are "similar
  to Chef Cookbooks or Ansible Roles."
- **[data]** Distribution differs. Ansible **Galaxy** is a searchable registry
  with download metrics. Salt's community library is the **`saltstack-formulas`
  GitHub org** — a flat set of **~356 repos** (GitHub API, 2026-07-06). That count
  includes org meta/template repos (`.github`, `template-formula`), so it slightly
  overstates distinct provisioning formulas.
- **[data]** Maintenance is uneven and slowing. Flagship formulas —
  `users-formula`, `zabbix-formula`, `tomcat-formula` (last updated Apr 7, 2025),
  `jenkins-formula` (May 6, 2025) — sat ~14–15 months untouched at the July 2026
  snapshot. Some core infra formulas (`template-formula`, `nginx-formula`) saw
  activity as recently as Dec 2025, so the org is not dead.
- **[judgment]** Stale ≠ broken — complete, stable formulas legitimately need
  fewer commits — but named application formulas untouched for over a year is a
  real velocity signal, and the registry-vs-flat-GitHub-org asymmetry makes Salt's
  library harder to assess and trust than Galaxy's.

> ⚠️ **Two different orgs — do not conflate.** `saltstack-formulas` (the mainstream
> community formulas) is **not** `salt-formulas` (the legacy Mirantis / tcp-cloud
> reclass lineage at `github.com/salt-formulas`). They have different, and
> differently-maintained, testing stacks — see §4.3.

### 4.2 Linting — `ansible-lint` vs. `salt-lint` (the sharpest asymmetry)

- **[data]** `salt-lint` is the Salt analogue to `ansible-lint` — literally a
  Warpnet fork/adaptation that checks SLS files for best practices. Its latest
  release is **v0.9.2, Feb 9, 2023** (~3.5 years stale as of July 2026); Snyk
  rates its maintenance status **"Inactive"**, using the exact language "could be
  considered as a discontinued project, or that which receives low attention." No
  2024–2026 releases.
- **[data]** By direct contrast, `ansible-lint` shows active development through
  mid-2026 (e.g. v26.6.0 in June 2026) with a steady multi-release cadence, plus
  `yamllint` alongside it.
- **[judgment]** This is the single cleanest tooling gap in the whole comparison:
  Ansible's linting story is alive; Salt's dedicated linter is abandoned.

### 4.3 Testing — Molecule vs. Salt's fragmented story

- **[data]** **Molecule** is the de-facto standard Ansible role test harness — full
  lifecycle (create → converge → **idempotence** → verify → destroy), pluggable
  drivers (Docker/Podman/Vagrant/cloud), now living under the official Ansible org.
  *(It is not bundled with `ansible-core`, so not a literal default — but it is the
  community-standard harness.)* The built-in **idempotence** stage matters
  enormously for config management.
- **[data]** Salt has no single equivalent. The testing story is fragmented across
  less-maintained pieces:
  - **`kitchen-salt`** (Test Kitchen provisioner: generates minion configs, builds
    pillars from `.kitchen.yml`, runs `salt-call`) — last gem v0.7.1/0.7.2, **Jan
    2022** (~4.5 years stale). The mainstream `saltstack-formulas`
    `template-formula` uses this official `kitchen-salt`.
  - The **legacy** `salt-formulas` org uses a *forked* `kitchen-salt` + **InSpec**
    + a `tests/run_tests.sh` shell smoke test (`salt-call state.show_sls`). This is
    niche and minimally maintained — it is **not** "the" Salt testing story.
- **[judgment]** There is no Salt tool with Molecule's cohesion, active
  maintenance, and community adoption. This is the second major tooling asymmetry.
- **[not established]** `pytest-salt-factories` exists (it is how Salt tests
  *itself*), but the fetch of its docs came back low-signal in this run. This
  report makes **no** claim about its maturity as an end-user *formula*-testing
  tool — treat it as an open question, not as absent.

### 4.4 Debugging / development tooling (roughly at parity; Salt holds up)

| Ansible | Salt |
|---|---|
| `--check` (check mode) + `--diff` | `test=True` (dry-run states) |
| `-vvv` verbosity | `-l debug`, `salt-call -l debug` |
| `ansible-console`, `ansible-navigator` | `salt-call --local` (masterless local debug), `state.show_sls` |
| — | `salt-run`, and the **event bus** (`salt-run state.event`) for live observation |

**[judgment]** This is the one category where Salt is not behind: masterless
`salt-call --local` and the live event bus are genuinely nice for interactive
debugging.

### 4.5 Project health post-Broadcom

- **[data]** Salt reached **Broadcom via the VMware acquisition** (closed Nov 22,
  2023) and now survives as a **component of VMware-by-Broadcom products**. In
  **June 2024** the Salt Project launched a **"Community Core Maintainer" pilot**
  to "strengthen and secure the future of Salt Project by increasing the
  involvement and contributions of select members of the community" (contact:
  `saltproject.pdl@broadcom.com`).
- **[judgment]** Read charitably, the pilot augments the core team; read alongside
  the observed satellite-tooling staleness (§4.1–4.3), it signals increased
  reliance on volunteer maintainers. Either way it is consistent with — not a
  counter to — the stale-tooling picture.

## 5. Applying it to the lab (MQ + Pacemaker/Corosync + DRBD)

The lab orchestrates **IBM MQ + Pacemaker/Corosync + DRBD** with Ansible today.

1. **[data]** A current, breadth-matched Ansible role already exists for the
   hardest piece: `OndrejHome/ansible.ha-cluster-pacemaker` covers cluster
   creation, node auth, corosync transport (heartbeat/RRP/knet, up to 8 links),
   pacemaker-remote nodes, order/colocation/location constraints, and multiple
   stonith fencing agents. Most recent commits **Nov 5, 2025** (RHEL 10 + Debian
   13); distro matrix through RHEL 10 / Fedora 43 / AlmaLinux 10. *(Caveats:
   modest popularity — 42 stars; "mature" rests on longevity/breadth — 215 commits,
   wide distro support — not adoption metrics; the Debian variant omits
   stonith/firewall config.)* There is also a LINBIT-published Ansible path for
   DRBD + Pacemaker.
2. **[judgment]** The tooling gap hits *exactly* this workload. Stateful HA/DR
   clustering is where idempotence testing and dry-run verification matter most —
   Molecule's idempotence stage + `--check`/`--diff` + a maintained linter are the
   safety net a resiliency lab depends on. Salt's stale `kitchen-salt` and
   abandoned `salt-lint` remove exactly that net.
3. **[judgment]** Dual-support is recurring cost for little benefit. Maintaining
   MQ/Pacemaker/DRBD logic in *both* SLS and playbooks means two test suites (one
   on a stale harness), two linting stories (one dead), double the drift surface,
   and a second toolchain wired into `vrg-validate`. The one documented org that
   runs Salt + Ansible together (RIPE NCC) did so to *manage a migration* — a
   transition strategy, keeping one primary — not a steady state to aspire to.

**[judgment] When Salt would deserve a fresh look:** if the lab specifically
wanted the event bus + Reactor to drive reactive DR automation across a large
fleet. That is Salt's real architectural edge — but (a) Reactor's limitations push
non-trivial logic into Orchestrate, and (b) for a bounded-node teaching lab the
always-on bus is more machinery than the problem needs. That would justify a
focused *spike*, not a conversion or dual-support.

## 6. The decision and its rationale

**Park Salt. The lab remains Ansible-only. Salt is on hold, not implemented.**

Three factors, in order of weight:

1. **[judgment] Ecosystem health tilts hard to Ansible for this workload** (§4,
   §5). The stale Salt tooling is not a footnote — for a resiliency lab it removes
   the testing/linting safety net precisely where it is most needed.
2. **[judgment] The employer-fidelity rationale has dissolved.** The main
   non-technical argument for a lab Salt port was skills/employer fidelity. The
   maintainer now works in an environment that uses Salt directly, so that
   exposure is obtained there — and that employer Salt work is not reusable back
   into this lab. The rationale that partly justified #197's "GO" no longer holds.
3. **[data → judgment] On pure technical merit Ansible was already the cleaner
   fit**; #197 showed the port is feasible but pays an idempotency-translation tax
   and leaves the HA class unsampled. Nothing here argues Salt is *unusable* — only
   that, absent the fidelity driver and against a declining tooling ecosystem, the
   port is not worth the cost.

This supersedes #197's GO-on-content recommendation. #197 remains valid as the
record of *what a port would cost*; this report is the record of *why we are not
paying it*.

## 7. Caveats & what this does not rest on

- **[data] Time-sensitive.** All maintenance-health facts are snapshotted
  **2026-07-06**. If the Community Core Maintainer pilot revives Salt's tooling,
  re-check before treating "stale" as current.
- **[judgment] The strongest data** is the tooling-staleness set (PyPI, RubyGems,
  GitHub, Snyk) and the current Ansible Pacemaker role. Those are directly
  verified against primary sources.
- **[judgment] The dual-support / migration-cost conclusion is inference**, not a
  sourced case study of a dual-tool codebase. It reasons from ecosystem health +
  the RIPE NCC transition pattern.
- **[judgment] Absence-of-evidence gap.** The research established that Ansible
  *has* a current Pacemaker role and that Salt's general formula/testing/linting
  tooling is stale — it did **not** run an exhaustive search proving the *absence*
  of a current Salt Pacemaker/DRBD formula. "None surfaced" ≠ "none exists." A
  targeted search would close this if the decision is ever reopened.
- **[not established] `pytest-salt-factories`** maturity as a formula-testing tool
  is unverified (§4.3) — a genuine blind spot, not a negative finding.

## 8. Sources

Primary sources carry the load; blogs/secondary are corroborated by primary docs.

**Architecture / features**
- Salt system architecture — `saltstack.github.io/docs-saltproject-io/.../salt_system_architecture.html` (primary)
- Salt ZeroMQ transport — `docs.saltproject.io/en/latest/topics/transports/zeromq.html` (primary)
- Salt Reactor — `docs.saltproject.io/en/latest/topics/reactor/index.html` (primary)
- Red Hat, *Ansible vs Salt* — `redhat.com/en/topics/automation/ansible-vs-salt` (secondary)
- phoenixNAP, *SaltStack vs. Ansible* — `phoenixnap.com/kb/saltstack-vs-ansible` (secondary)

**Linting**
- `github.com/warpnet/salt-lint` (primary) · `snyk.io/advisor/python/salt-lint` (secondary) · `pypi.org/project/salt-lint/` (primary) · `github.com/ansible/ansible-lint/releases` (primary)

**Testing**
- `github.com/saltstack/kitchen-salt` (primary) · `rubygems.org/gems/kitchen-salt` (primary) · Molecule docs, `docs.ansible.com/projects/molecule` (primary) · `salt-formulas.readthedocs.io/.../testing-formulas.html` (primary, legacy org)

**Reusable code / library**
- `github.com/orgs/saltstack-formulas/repositories` (primary) · `api.github.com/orgs/saltstack-formulas` (primary) · Salt formula conventions, `docs.saltproject.io/.../conventions/formulas.html` (primary)

**Project health post-Broadcom**
- Salt blog, *Community maintainers wanted* — `saltproject.io/blog/community-maintainers-wanted/` (primary) · `github.com/saltstack/salt/discussions/67028` (forum, contested signals)

**Lab-specific**
- `github.com/OndrejHome/ansible.ha-cluster-pacemaker` (primary) · LINBIT, DRBD+Pacemaker via Ansible — `linbit.com/en/deploy-drbd-pacemaker-cluster-using-ansible/` (vendor blog) · RIPE NCC dual-tool migration — `labs.ripe.net/.../how-were-migrating-our-configuration-management-system/` (blog)
