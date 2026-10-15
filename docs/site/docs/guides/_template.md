# IBM MQ &lt;topic&gt; — &lt;what it does&gt;

<!--
Copy this file to `mq-<topic>-guide.md` and fill it in.
Read `guide-standard.md` first. Declarative configuration only — no scripts.
Numbered sections = the 2–3 page how-to. Lettered appendices = supporting depth.
Delete any appendix you don't need, and delete these comments, when done.
-->

- **MQ version:** &lt;the lab's pinned level, from `lab/mq-version` — currently 10.0.x&gt;
- **Status:** Draft
- **Last validated in lab:** &lt;YYYY-MM-DD&gt; on MQ &lt;V.R.M.F&gt;
- **IBM Docs re-checked:** &lt;YYYY-MM-DD&gt; against &lt;pinned level, e.g. 10.0.x&gt;
- **Related guides:** &lt;links, or "none yet"&gt;

---

## 1. Purpose & audience

What this configures and who needs it. Two or three sentences.

## 2. Scope & version floor

What is in and out of scope. The MQ version this applies from.

## 3. Recommendation

If there is one approach, state it. If there are several, rank them
strongest → weakest, each with its compromise named. Recommend the best; let the
reader own the tradeoff.

## 4. How to configure it

Steps in prose, with minimal inline declarative config, for example:

```ini
Stanza:
   Attribute = value
```

## 5. Verify it worked

The checks that prove success, described in prose — not as runnable commands.

## 6. What stays / caveats

What cannot be turned off; known sharp edges.

---

## Appendix A: Full parameter reference

Every setting, value, default, and meaning. A table works well.

## Appendix B: Alternatives and tradeoffs

The evidence behind the section 3 ranking.

## Appendix C: Complete configuration examples

The exhaustive stanza / MQSC blocks.

## Appendix D: Troubleshooting

Symptom → cause → fix.

## Appendix E: References

IBM Docs topics at the lab's pinned MQ level (currently 10.0.x), one link per
topic, each resolved with `tools/ibm_doc_cache.py` before you cite it:

- *&lt;Topic title&gt;* —
  `https://www.ibm.com/docs/en/ibm-mq/10.0.x?topic=<slug>` (what it is
  authoritative for)
