# tools/

Developer/research utilities (not lab runtime).

## `ibm_doc_cache.py` — canonical IBM Docs fetch + local cache (#225)

IBM Docs return **HTTP 403** to bot user-agents (the built-in WebFetch), so research
can't read the primary pages — only search snippets / mirrors, which weakens
load-bearing claims. The block is a **user-agent heuristic, not auth/paywall**: with a
browser UA the public page returns 200. This tool fetches the page as a browser,
follows the embedded `oldUrl` to the IBM content API
(`/docs/api/v1/content/<oldUrl>` → the clean canonical topic body), and caches it.

```bash
# fetch + cache one or more pages (pin to 9.4 — the lab runs MQ 9.4.5)
python3 tools/ibm_doc_cache.py \
  "https://www.ibm.com/docs/en/ibm-mq/9.4.x?topic=availability-creating-deleting-floating-ip-address"

# list what's cached
python3 tools/ibm_doc_cache.py --list
```

Cache layout (gitignored — under `build/`, host-durable, **not committed**; do not
redistribute IBM content):

```
build/refs/ibm-docs/<product>/<version>/<slug>/
  content.html   # raw canonical topic body
  content.txt    # tag-stripped text (quote primary IBM wording from here)
  meta.json      # source_url, content_url, retrieved_at, sha256, title
```

Cite the cached text as the primary source (it *is* the IBM page body), with the
`source_url` from `meta.json`. Be polite: public docs, low volume, rate-limit.

**Follow-ups:** fold into `mqlab` with tests (currently a standalone stdlib script,
so it isn't covered by `vrg-validate`'s `src/`+`tests/` lint/type/coverage gates);
first collection target is the scattered **TLS/SSL** topics. See the repo memory
`ibm-docs-fetch-bypass` and the link-hygiene practice (#224).
