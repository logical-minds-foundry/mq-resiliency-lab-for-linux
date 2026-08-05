# OpenSearch Snapshot/Restore Spike — Findings (GATING for Tasks 4/11)

**Date:** 2026-08-05 (spike executed; filename date matches the epic plan's Task 2 artifact name)
**Issue:** #826 (epic logical-minds-foundry/.github#149 — logsearch tier)
**Task:** Epic plan Task 2 — prove the host-side snapshot/restore round-trip and the guest→host
transport before wiring them into the `opensearch` role (Task 4) and `mqlab logsearch` CLI (Task 11).

**Status:** DECIDED, proven end-to-end (both mechanisms + transport).

## Decision

**Use OpenSearch's native `_snapshot` filesystem repository, transported guest→host with
Ansible.** The cold-copy of a stopped `path.data` also works but is strictly more fragile and
higher-downtime; it is documented here as the rejected alternative, not the mechanism.

**Restore-on-bring-up sequence (for Task 4 Step 5 / Task 11):**

1. On the host, look for the latest snapshot artifact under
   `$(mqlab build path state)/logsearch/`. If none → clean no-op, **logged** (a fresh build is
   empty by design, not a silent skip).
2. Stage the artifact host→guest (Ansible `copy`) and untar into the node's `path.repo`
   (`/var/lib/opensearch/snapshots`).
3. Start OpenSearch, register the repo (`PUT _snapshot/logsearch-fs {type: fs, location: path.repo}`),
   then `POST _snapshot/logsearch-fs/<latest>/_restore`.

## Evidence

OpenSearch **3.8.0**, single-node, `plugins.security.disabled: true` (spike only).
`path.repo` set to a filesystem dir; repo registered:

```
PUT _snapshot/logsearch-fs  {"type":"fs","settings":{"location":"<path.repo>","compress":true}}
  -> {"acknowledged":true}
```

Indexed 5 docs into `snaptest`.

### Native `_snapshot` round-trip (chosen)

```
PUT _snapshot/logsearch-fs/snap-1?wait_for_completion=true {"indices":"snaptest"}
  -> state: SUCCESS | shards {total:1, failed:0, successful:1}
DELETE snaptest                      -> index now 404 (wiped)
POST _snapshot/logsearch-fs/snap-1/_restore?wait_for_completion=true
  -> restored shards {total:1, failed:0, successful:1}
snaptest _count                      -> 5   (all docs back; SNAPMARKER sample verified)
```

The repo on disk is a portable directory tree (`index-N`, `index.latest`, `indices/`,
`meta-*.dat`, `snap-*.dat`, `snapshot_shard_paths/`) — trivially tar-and-fetch.

### Cold-copy of stopped `path.data` (rejected alternative — also proven)

```
SIGTERM OpenSearch (clean stop, port 9200 closed)
tar czf pathdata.tar.gz path.data           # consistent ONLY because the node is stopped
start -> DELETE snaptest (404) -> stop again
rm -rf path.data && untar the copy -> start
snaptest _count                              -> 5   (docs back)
```

Works, but every disadvantage points the same way:

| | Native `_snapshot` fs repo | Cold-copy stopped `path.data` |
|---|---|---|
| Downtime to capture | **None** — live consistent snapshot via API | **Full node stop** (a hot copy risks torn/partial Lucene segments) |
| Incremental | **Yes** — only changed segments added to the repo | **No** — copies the entire `path.data` every time |
| Restore | API call, **no restart** | stop → replace files → start (downtime + orchestration) |
| Portability across a re-baked node | Clean (snapshot carries index data, not node identity) | Carries node/cluster UUID + full cluster state — fine same-node, brittle across a fresh box |
| First-class support | Yes (documented, `wait_for_completion` gives a clear SUCCESS state) | Ad-hoc; you own consistency |

Native wins on every axis. Cold-copy stays only as a mental fallback if the fs repo ever
misbehaves.

### Version-compat caveat (why the version pin matters — spec §5/§7, plan Task 6)

OpenSearch refuses to **restore a snapshot taken by a newer version**. A routine re-bake that
bumped OpenSearch could therefore orphan an existing snapshot. This is exactly why the version
is pinned in the shared manifest (Task 6) — the pin keeps the snapshot/restore contract stable
across re-bakes. (The cold-copy path is even stricter: Lucene segment-format compatibility,
effectively same-major-only.) The pin protects the chosen mechanism directly.

### Guest→host transport (Ansible) — proven both directions

Ran as an Ansible ad-hoc against the OpenSearch host over **SSH** (`ansible.builtin` modules,
`ansible-core` 2.21.1). No nested lab guest exists at spike time, so localhost stands in as the
"guest" over a real SSH connection — the transport mechanism (SSH `fetch`/`copy`) is exercised
faithfully; only the physical guest differs.

```
# guest-side: tar the fs repo dir      sha256 = 175d9f77…98ed6d
ansible … -m ansible.builtin.fetch  src=/tmp/logsearch-snap.tar.gz  dest=$DEST/  flat=true
  -> dest = <mqlab build path state>/logsearch/logsearch-snap.tar.gz
     ansible remote_checksum == local checksum; host sha256 = 175d9f77…98ed6d   (bytes intact)
ansible … -m ansible.builtin.copy   src=$DEST/logsearch-snap.tar.gz  dest=/tmp/roundtrip-back.tar.gz
  -> guest sha256 = 175d9f77…98ed6d   (guest→host→guest round-trip intact)
# restore FROM the transported bytes:
DELETE snaptest (404) -> untar round-tripped repo into path.repo -> restore snap-1 -> _count = 5
```

**The host target is `$(mqlab build path state)/logsearch/`** — resolved live from
`mqlab build path state`
(`…/build/state/logsearch`), never a hardcoded `build/<X>` path — the `state/` bucket
(host-durable, shared, irreplaceable), same family as `build/state/snapshots/`.

## Notes for Tasks 4 / 11

- **`fetch` is single-file.** The repo is a directory, so the guest tars it first, then `fetch`
  transports the tarball. For a large accrued corpus, `ansible.posix.synchronize` (rsync) of the
  repo dir is the incremental alternative — it avoids re-taring the whole repo each snapshot and
  mirrors the native snapshot's own incrementality. Either satisfies the "Ansible owns file
  transport, no synced folder" constraint.
- **Consistency = the OpenSearch snapshot API, not the tar.** `mqlab logsearch snapshot` should
  `PUT _snapshot/logsearch-fs/<name>?wait_for_completion=true` (assert `state: SUCCESS`) *before*
  taring/fetching the repo — the API guarantees a consistent point-in-time set; the tar just
  ships already-consistent files.
- **Naming for `latest_snapshot` (Task 11).** Snapshot names must sort so "latest" is
  unambiguous (e.g. `snap-<UTC-timestamp>`); the CLI's `latest_snapshot` helper picks the max.
- **Restore guard.** No artifact under `build/state/logsearch/` ⇒ log "no snapshot; starting
  empty" and continue (accepted tradeoff, spec §7) — never a silent skip, never a failure.
