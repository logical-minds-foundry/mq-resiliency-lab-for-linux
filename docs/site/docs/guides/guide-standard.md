# MQ Configuration Guides — authoring standard

How to write a guide in this section. Every guide follows this standard so the
set reads as one coherent family: a reader learns the shape once, then navigates
any guide by reflex. To start a new guide, copy [`_template.md`](_template.md).

## What a guide is

A guide is a **generic, product-level IBM MQ how-to** — one coherent area of
configuration (JSON logging, TCP tuning, security, …), written so anyone running
IBM MQ can read and apply it, with **no dependency on this lab's code**.

Three properties define it:

- **Generic to the product.** No lab hostnames, IPs, site values, or code. Where
  a value must appear it is an illustrative default or a `<placeholder>`, never a
  live secret.
- **Recommendation-bearing.** Where more than one approach exists, the guide
  ranks them strongest → weakest and names each option's compromise. It
  recommends the best technical choice and shows the evidence; the reader owns the
  tradeoff. See [Ranking alternatives](#ranking-alternatives).
- **Living.** Front matter records the date the guide was last validated in the
  lab. Guides are revisited as the lab and the product move on.

## The content boundary (scan-safe)

Guides are meant to be shared into environments that scan for code. To stay
shareable, a guide contains **declarative configuration only**.

### Allowed

- `qm.ini` / `mqs.ini` / `mqclient.ini` stanza blocks
- MQSC `DEFINE` / `ALTER` statements
- `setmqaut` authority statements
- Parameter → value tables
- Vendor-mandated configuration markup shown as an opaque blob where the product
  requires it (e.g. the mqweb Liberty `<logging>` element) — consumed because MQ
  mandates it, never authored by preference

### Not allowed

- Shell, Python, Ansible, or any imperative script
- `runmqsc` pipelines or other command-orchestration snippets
- Verification or procedure written as runnable commands

Describe *procedures* in prose ("add a `DiagnosticMessages` stanza with
`Service=Syslog` …"; "confirm each error-log line parses as one JSON object").
Show the *configuration artifact* verbatim. A stanza is far less likely to trip a
scan than a script, and prose survives a copy-paste review.

This is the default. It may be widened per explicit external approval — but the
template holds this line until then.

## The two-layer structure

The essence up front, the depth behind it — visibly separated, so a reader sees
the forest before the trees.

- **Numbered sections (1–6) are the how-to.** Target two to three pages. Someone
  who only wants it working reads these and stops.
- **Lettered appendices (A–N) are supporting material.** Reference depth, full
  option tables, the evidence behind a recommendation, troubleshooting. Someone
  who wants to understand *why*, or who hit a wall, drills down here.

The rule is structural, not sequential: **numbered is essential, lettered is
supporting.** No essential step hides in an appendix; no appendix padding sits in
the front. Never write "sections 1–3 matter, 4–10 are extra" — split them into
numbered versus lettered so the hierarchy is obvious at a glance.

### Front matter

Every guide opens with:

- **MQ version** — pinned (currently 9.4)
- **Status** — Draft or Active
- **Last validated in lab** — a date; the freshness signal
- **Related guides** — links across the section

### Body — the how-to (numbered)

1. **Purpose & audience** — what this configures, who needs it
2. **Scope & version floor** — what is in and out; the MQ version it applies from
3. **Recommendation** — approaches ranked strongest → weakest, compromises stated
   (omit only where there is genuinely one way)
4. **How to configure it** — steps in prose, minimal inline declarative config
5. **Verify it worked** — the checks that prove success, in prose
6. **What stays / caveats** — what cannot be turned off; known sharp edges

### Appendices (lettered, as needed)

A guide uses only the appendices it needs, from this menu:

- **A: Full parameter reference** — every setting, value, default, meaning
- **B: Alternatives and tradeoffs** — the evidence behind the section 3 ranking
- **C: Complete configuration examples** — the exhaustive stanza / MQSC blocks
- **D: Troubleshooting** — symptom → cause → fix
- **E: References** — pinned IBM Docs topics (9.4)

## Ranking alternatives

When a task has more than one viable approach, present them **strongest → weakest
recommendation**, and name the compromise of each. Recommend the best technical
option and supply the evidence; do not decide the tradeoff for the reader — that
is theirs to own. State the compromise plainly ("simpler to operate, but no
cross-site failover"), never bury it. This keeps a guide honest and useful to a
reader whose constraints differ from ours.

## House style

- Pin every product reference to **MQ 9.4**.
- Prefer tables for option and parameter enumeration.
- One idea per stanza block; annotate with inline `#` comments where MQ allows.
- Keep the front-matter *last validated in lab* date current whenever you touch a
  guide.
