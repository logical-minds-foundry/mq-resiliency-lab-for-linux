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

Cache layout (gitignored — in the shared `cache/` bucket of `build/`, host-durable,
**not committed**; do not redistribute IBM content):

```
build/cache/refs/ibm-docs/<product>/<version>/<slug>/
  content.html   # raw canonical topic body
  content.txt    # tag-stripped text (quote primary IBM wording from here)
  meta.json      # source_url, content_url, retrieved_at, sha256, title
```

Both IBM Docs URL shapes are keyed by product **and** version, so versions of one page
coexist instead of overwriting each other (#1295):

| URL shape | Cache dir |
|-----------|-----------|
| `/docs/en/<product>/<version>?topic=<slug>` | `<product>/<version>/<slug>/` |
| `/docs/[en/]<SScode>_<version>/<dir>/<page>.html` | `<SScode>/<version>/<dir>-<page>/` |

Any other URL shape is refused (no guessed location).

The cache root comes from the build-layout authority (`mqlab.buildenv`, the same code
behind `mqlab build path cache`), imported from this checkout's `src/` (it is
stdlib-only, so plain `python3` works without the project venv). It resolves the
**main checkout's** cache bucket through git's common dir, so from any worktree the
cache accumulates in one place and **survives worktree removal**. Set
**`$IBM_DOC_CACHE`** to relocate the cache to a permanent home without any code change;
the permanent-home decision is backlogged in **#226**.

```bash
export IBM_DOC_CACHE=~/.cache/ibm-docs   # example permanent home
```

A long-lived checkout may still carry a legacy top-level `build/refs/` written by the
pre-bucket version of this tool. `mqlab build migrate` merges it into
`build/cache/refs/` without loss (it refuses, moving nothing, if a file differs).

Cite the cached text as the primary source (it *is* the IBM page body), with the
`source_url` from `meta.json`. Be polite: public docs, low volume, rate-limit.

**Follow-ups:** fold into `mqlab` with tests (currently a standalone stdlib script,
so it isn't covered by `vrg-validate`'s `src/`+`tests/` lint/type/coverage gates);
first collection target is the scattered **TLS/SSL** topics. See the repo memory
`ibm-docs-fetch-bypass` and the link-hygiene practice (#224).

## `sample-host-resources.sh` — host CPU/I-O/memory timeline during a bootstrap (#594)

The bootstrap runtime ballooned to ~1h16m and the host Cloud VM is oversubscribed
(8 vCPU / 31 GB running 12 nested lab VMs = ~20 nested vCPU). Ansible now emits
per-task timings (`profile_tasks`, enabled in `ansible.cfg`), but that tells us *what*
is slow, not *why*. This sampler adds the **host resource timeline** so each slow phase
can be classified CPU- vs I/O- vs memory-bound — the evidence for a rightsizing decision.

```bash
# launch in the background just before a rebuild; stop it after
tools/sample-host-resources.sh 5 &        # 5s interval; log lands under build/temp/
mqlab bootstrap rdqm-rhel
kill %1
```

Read `wa`/`blk` high → I/O-bound; `id` low with `wa` low → CPU-bound; `memavailMB`
approaching 0 → memory-bound. Cross the timeline against the `profile_tasks` slowest-task
summary to find the top sinks.
