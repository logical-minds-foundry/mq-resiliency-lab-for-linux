# The Lab Methodology

## 1. Thesis — build it to understand it

To understand a technology, build the whole thing from the ground up.

Not read about it. Not take the vendor's course. **Get the product, stand up a
bespoke, reproducible, throwaway mini-enterprise around it, build something real on
it, and find out how it actually works.** The same instinct applies everywhere: to
learn a programming language, write code in it that doesn't work and then make it
work; to learn an infrastructure stack, install it, automate it, and break it.

This matters more for infrastructure than for applications, and the asymmetry is the
whole reason the method exists. Application work is easy to prototype: you live in
user space, you can mock every dependency, and a unit-test harness proves the code
basically works without any special privilege or distributed infrastructure.
Infrastructure gives you none of that. To build tooling that installs, upgrades,
configures, and *survives the failure of* a vendor product, you need an environment
that **simulates the real thing** — the network, the storage, the security services,
the failover — because those are exactly the parts you cannot mock.

A **lab**, in this method, is that environment: a complete, code-defined, disposable
simulation of the slice of an enterprise a technology actually lives in.

## 2. The Reproducibility Theorem

Everything in this method descends from one principle, so it comes first.

> **The Reproducibility Theorem.** Confidence that a system can be reproduced decays
> exponentially with the time since it was last reproduced from scratch — and the
> decay accelerates the lower down the stack the system sits.

Stated as a mathematical expression, for those who prefer it:

> `R(t) ≈ e^(−t / τ)` — reproducibility confidence `R` falls off exponentially with
> the elapsed time `t` since the last from-scratch rebuild, governed by a trust
> half-life `τ` that **shrinks the further down the stack you go**. Foundational
> infrastructure has the smallest `τ`; it rots fastest.

The everyday tell is emotional. Log into a box that has not rebooted in a year and
your first thought is *"I hope it comes back."* In corporate environments that fear
is usually justified. The old badge of honor — a pet machine with two years of
uptime — is now an anti-pattern, not a trophy. Booting a host doesn't just test the
host; it tests the host's **interaction with an environment that has moved on around
it** — DNS resolution, the security services, file services, everything it silently
depends on. Keep the box frozen while the world evolves and its reproducibility rots
invisibly. Patch it a hundred times over eighteen months without rebooting and, when
it finally won't come back, you have a bisection nightmare — on, of all things, a
node that was supposed to be highly available.

The sharp edge, and the reason this drives the whole method: **being able to rebuild
the individual components is not the same as being able to rebuild the whole thing
from the ground up.** If you have never done the latter, you almost certainly can't.
Most enterprises have never truly bootstrapped — their "bootstrap" was an *accretion*
of live tweaks, the running integral of every change ever made, which is precisely
why a real enterprise is effectively impossible to reproduce. We accept that for
production because of its complexity. But when the goal is to *understand or build* a
technology, the lab must be the thing the enterprise never was: **bootstrappable from
scratch, on demand.**

## 3. The invariants that follow

Three non-negotiable properties fall directly out of the theorem.

**Ephemeral, data-less, fearlessly disposable.** The lab holds no precious data,
ever. Putting real data into it and backing that data up is *misuse by design*. You
must be able to blow the entire lab away without a second thought — and you should do
so often (the aspiration is an automated weekly rebuild, to force the discipline).
Rebuilding the whole thing is where the real lessons live: bootstrapping the fleet
forces you to solve startup ordering, timing, and race conditions across stacks.
Those particular problems don't necessarily export to the real world easily — but you
solve them in the lab anyway, because solving them is how you prove the bootstrap is
real. Practically,
the fleet is nested inside a single logical VM: one container for the whole thing,
and one that runs as happily on a cloud instance as on a laptop. Disposability also
sets the security posture. The **instrumented component** — the thing under study,
the queue managers and the apps on top — is secured properly. But the **automation's
own secrets** are generated dynamically into an ephemeral, world-readable build
directory, because the lab is isolated and disposable and a breach is answered by
rebuilding it. Nothing inside is important; everything is reproducible.

**Nothing by hand.** I have a deliberate aversion to doing anything manually, because
the manual command is only ever a *step toward the automated, reproducible
component.* A one-off by hand is a component you have chosen not to be able to
reproduce. Everything is code-defined and glass-box.

**AI builds the automation; AI does not run the live system.** This is where my use
of AI diverges hard from the industry's drift, and the distinction is load-bearing.
"Automating with AI," to me, means using AI as a **development-engineering assistant
to produce deterministic software that implements the automation.** The AI is *not*
the automation — it is the tool that builds it. Even in the lab, nothing is done by
hand *including by the AI agents*: their output is committed, reproducible code, not
live improvisation. I am happy to use AI to **build** a system meant to last; I am
not willing to put AI in the **booting and running** of a live 24/7 system. An
agentic tool operating against live automation must make no change that isn't
human-approved and human-audited. Aggregate log analysis, hunting for signal in the
noise, *reporting* what it finds — excellent, do that. Acting on the live system —
that line I cross last, because the output of this method must be **deterministic and
reproducible**, and an AI-in-the-loop runtime is neither.

## 4. Why the old way couldn't do this

Throughout my career I have worked *inside* the enterprises that **consume** this
infrastructure, not the vendors that build and sell it — and those are very different
kinds of company. A vendor that *makes* infrastructure software has manufacturing-like
processes around packaging it for installation; the enterprises that merely consume it
are left to work out bootstrapping on their own, usually with poor tools.

Historically the tool on offer was the **shared corporate lab**, and it was
pathetic. You borrowed other teams' *lab-quality* — not production-quality — DNS,
Kerberos, or file services, and found them left in half-baked, unreproducible states
by whoever used them last. You lost days to other people's bespoke mess. At best you
could *manually play with* a vendor product to get a rough feel for it; no serious
infrastructure or software engineering ever happened there. Dev environments were
better — a near-production, managed NAS you could actually build on — but you rarely
got to test the thing that matters most: what happens when that NAS *fails.*

Desktop virtualization changed the economics completely. Roughly twenty years ago,
VirtualBox and then Vagrant made it practical to stop waiting on the network, storage,
and security teams — for whom my request was always the lowest priority — and instead
**build the corporate environment myself**: real networks, a fleet of real machines,
the core services, all on my own machine. The lab stopped being a place you
borrowed and became a thing you *authored*.

**A note on proprietary products.** I lean toward free, open-source tools wherever I
can, and much of the lab is built from them. But the products most *worth* building a
lab for are often the **proprietary** ones — MQ among them (free only as a developer
instance). Precisely because a vendor holds its testing close, a proprietary
product's failure modes are the least openly documented; open-source software, by its
nature, exposes far more. Building the lab is how you develop that missing knowledge
yourself.

## 5. What the lab is for — the evolution

The method matured through three uses, each building on the last.

1. **Reproduce and understand.** The original purpose: stand a technology up,
   reproduce it reliably, and learn how it actually behaves — including a scaled-down
   multi-data-center topology with simulated core services, so the behavior you study
   is the real thing's, not a mock's.
2. **Integration-test environment.** Infrastructure integration tests are painful on
   real infrastructure and straightforward on *simulated* (not mocked) infrastructure.
   A lab can be spun up on demand to run long, edge-case-heavy integration suites
   against real-but-virtual services, and it can reproduce conditions bare metal
   can't hand you easily — slow networks, partitions, injected failures. The same lab
   doubles as an application-developer test environment and a CI/CD substrate.
3. **AI-assisted develop-then-factor-out (current).** With aggressive AI tooling, I
   build a solution in the lab **fast and deliberately rough**. The code is still
   **reproducible** — it has to be; it is compiled or installed as part of building
   the lab, and if it weren't reproducible neither would the lab be. What I lower is
   the **engineering bar**, not the reproducibility one: in the lab the code is
   allowed to be quick-and-dirty, embedded straight in the Ansible playbooks and
   ad-hoc scripts, without the full rigor — unit and integration tests, audits and
   security scans, packaging as a proper product — that a standalone tool earns.
   Getting the architecture to produce the result I want, reliably, is the proof of
   concept. Then I look back and **identify the point solutions that turn out to be
   genuinely generic**, and **factor those out** into **standalone, reusable
   components** — which *do* go through the full rigor (Vergil-managed, tested,
   audited, published under the aggressive controls a real product gets), and which
   the lab then consumes in place of the rough version. The CLI-tool metric
   **collectors**, generic data-source-agnostic **dashboards**, and **MQ-event
   handling** are all examples. Each factor-out compounds the practice and makes the
   *next* lab cheaper to build.

## 6. Base labs and satellite labs

A mature lab layers. A **base lab** owns a reproducible, highly-available instance of
a foundational technology — here, the MQ **3+3 HA/DR** queue-manager topology, chosen
because any solution worth testing must survive HA failover and be understood against
its DR risk. A **satellite lab** is a *separate repository* that **depends on** the
base lab, carries **its own Ansible and CLI** for whatever it is testing, and
**automates its own customizations to the base** — while the base lab remains
entirely ignorant of it. That one-directional ignorance is the point: the base takes
full responsibility for reproducing the foundation on whichever stack you choose, and
the satellite rides on top without the base needing to know it exists.

(The relationship earlier tempted the name "parasite," but that misrepresents it —
it is a clean layering/dependency model, not a parasitic one. In practice it is all
one practitioner, so the "separate team" independence is somewhat notional; the
repository boundary is what keeps the concern separate.)

You can layer indefinitely. The satellites I have in mind for MQ are a
**streaming-queue / history** capability and a **protocol gateway** — a generic
trading back-office gateway that lets applications speak one protocol and have it
translated to MQ. The same model generalizes: a future **Postgres HA/DR** base lab
with its own satellites, and so on.

## 7. The lab as a teaching instrument

There is a second-order use I have only recently started exploiting: the lab as a way
to **transfer expertise** — a direct answer to the AI **de-skilling** problem (experts
who lean on AI and let their hands-on knowledge atrophy) and the **no-skilling**
problem (people who never build the skill at all).

My own mitigation came first: have the AI **teach me what it built.** I will sit with
an agent and say "walk me through how we installed and configured this HA queue
manager, and every way it can fail," then spend hours going back and forth against the
live system, the commands, and the code. AI is remarkably good at this.

The generalization is a **live-fire training ground**, which I intend to build into
the lab's CLI. The AI **injects a fault** — "I've broken something; find it" — and the
learner triages from the dashboard and the low-level tools, with everything they do
recorded at the terminal. The AI gives **hints, not answers** ("can't find the flames?
there's smoke over here — trace it"), and when the exercise ends it **evaluates what
the learner did and gives feedback.** This deliberately manufactures the "scar tissue"
that used to take years of production outages to accumulate — the tacit knowledge of
where a system bends and breaks — which no longer scales the old way. When I build an
HA system for a client whose own staff must support it, this is how they learn it
without waiting for it to page them at 3 a.m.

## 8. The first published instance — the MQ Resiliency Lab

The MQ Resiliency Lab is the current, and first *published*, instance of this method.
It stands up a reproducible **3+3 HA/DR** queue-manager topology across the HA
mechanisms, an end-to-end request/reply application, a full observability plane, and
disaster-recovery drills — driven originally by a generic **trading back-office
gateway** resiliency problem. It is deliberately ephemeral and data-less; it
bootstraps from scratch with one command; and it is already spinning off its first
factored-out components.

It is not the point. It is **one instance of a repeatable practice** — the first
published lab of what I expect to be several, and the reusable components they shed
along the way. The method is the deliverable; MQ is simply where I aimed it first.
