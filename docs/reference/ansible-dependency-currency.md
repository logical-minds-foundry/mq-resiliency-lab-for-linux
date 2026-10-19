# Ansible dependency currency: gap to latest and how we manage it

> Scope: every Ansible dependency the lab uses — `ansible-core`, `ansible-lint`,
> `ansible-compat`, and the galaxy collections `community.crypto` and
> `ansible.posix`. For each, this report gives the declared pin, the version we
> actually lock or install, the latest upstream release, its support status, and
> the notable changes in between. It then reviews how the dependencies are
> managed and makes recommendations. This is task GAP of epic
> [logical-minds-foundry/.github#368](https://github.com/logical-minds-foundry/.github/issues/368)
> (issue [#1649](https://github.com/logical-minds-foundry/mq-resiliency-lab-for-linux/issues/1649)).
> It **measures and recommends only**: no pin changes in this task. The
> recommendations also feed later dependency-management work in vergil-tooling.
>
> Convention: each claim is tagged **[data]** (what a cited source actually says)
> or **[judgment]** (our reasoning on top of it). Source keys (S-n, R-n) are
> resolved to full URLs in §6. Repo facts (R-n) were read from `origin/develop`
> at `ef6e490`.
>
> Retrieved: **2026-10-19**. All upstream versions and dates below were fetched
> that day. Upstream moves fast; re-fetch before relying on a "latest".

## 1. Inventory

### 1.1 Python-side tools (locked by `uv.lock`)

| Dependency | Declared pin | Locked | Where declared | Installed in the main checkout's `.venv` |
|---|---|---|---|---|
| `ansible-core` | `>=2.16` | `2.21.1` | `pyproject.toml` `[project] dependencies` (runtime) | `2.21.1` |
| `ansible-lint` | none (unpinned) | `26.6.0` | `pyproject.toml` `[dependency-groups] dev` | `26.6.0` |
| `ansible-compat` | not declared (transitive) | `26.6.0` | pulled in by `ansible-lint` | `26.6.0` |

- **[data]** The pins and group placement are in `pyproject.toml` (R-1). The
  locked versions are the `[[package]]` entries in `uv.lock` (R-2). The installed
  versions are the `*.dist-info` directories in the main checkout's `.venv` (R-3).
- **[data]** `ansible-core 2.21.1` entered the lock on 2026-06-29, in commit
  `592d3f1` (#404, the ansible-lint gate). `git log -S` on `uv.lock` finds no
  later change to that line (R-2).
- **[data]** `ansible-compat` is not named in `pyproject.toml`. It enters as a
  dependency of `ansible-lint` (the `uv.lock` dependency list for
  `ansible-lint 26.6.0`, R-2). `ansible-lint 26.10.1` declares
  `ansible-compat>=26.3.0` and `ansible-core!=2.17.*,>=2.16.19` (S-2).
- **[data]** The project requires Python `>=3.14,<3.15` (`requires-python`, R-1).

### 1.2 Galaxy collections (no lock)

| Collection | Declared pin | Installed in the shared `build/cache` | Where declared | Used by |
|---|---|---|---|---|
| `community.crypto` | `>=2.20.0` | `3.2.2` | `ansible/requirements.yml` | `lab-pki` (`ca.yml`, `entity.yml`), `rdqm-replication-tls` (`certs.yml`), `rdqm-ssh-access` (`keygen.yml`) |
| `ansible.posix` | `>=1.5.0` | `2.2.1` | `ansible/requirements.yml` | `ansible/ansible.cfg` `callbacks_enabled` (the `profile_tasks` and `timer` callbacks) |

- **[data]** The floors are in `ansible/requirements.yml` (R-4). The installed
  versions are the `GALAXY.yml` files under
  `build/cache/ansible_collections/*.info/` in the main checkout (R-5).
  `community.crypto-3.2.2.info` is dated 2026-06-29 and `ansible.posix-2.2.1.info`
  2026-07-12 (file modification times, R-5).
- **[data]** Modules used, by FQCN (R-6):
  - `community.crypto.openssl_privatekey`, `.openssl_csr`, `.x509_certificate`
    (4 call sites each in `lab-pki` and `rdqm-replication-tls`)
  - `community.crypto.openssl_pkcs12` (1, `lab-pki/tasks/entity.yml`)
  - `community.crypto.openssh_keypair` (1, `rdqm-ssh-access/tasks/keygen.yml`)
  - `ansible.posix.profile_tasks` and `ansible.posix.timer` (callbacks only, in
    `ansible.cfg`)
- **[data]** No other collection is used. A grep of `ansible/` for FQCNs outside
  `ansible.builtin` finds only the two collections above (R-6). The mentions of
  `ansible.posix.sysctl` (`opensearch/tasks/install.yml`), `ansible.posix.mount`
  (`pdrbd-qm/tasks/main.yml`) and `community.general` (`drbd-san/tasks/main.yml`)
  are comments explaining why the code uses a builtin instead.

### 1.3 Interpreters the dependencies run against

- **[data]** Control node: the lab venv is Python 3.14 (R-1; the
  `.venv/bin/python3.14` interpreter in the run logs, R-7).
- **[data]** Managed nodes: the run logs under `build/state/runs/` record the
  interpreter `ansible-core 2.21` discovered on each box build (R-7):

  | Guest OS | Discovered interpreter |
  |---|---|
  | RHEL 9 (`mq-nativeha-rhel9`, `mq-rdqm-rhel9`) | `/usr/bin/python3.9` |
  | RHEL 10 (`mq-nativeha-rhel10`) | `/usr/bin/python3.12` |
  | Ubuntu 24.04 | `/usr/bin/python3.12` |
  | Ubuntu 26.04 | `/usr/bin/python3.14` |

  The inventory sets `ansible_python_interpreter=/usr/bin/python3`
  (`src/mqlab/inventory.py`, R-8). RHEL 9 stays selectable for `rdqm-rhel` and
  `nativeha-rhel-crr` (`lab/versions.yaml`, R-9).

## 2. Gap to latest

### 2.1 Summary

| Dependency | Ours | Latest stable (date) | Distance | Our version supported? |
|---|---|---|---|---|
| `ansible-core` | 2.21.1 (2026-06-18) | **2.21.5** (2026-10-05); 2.22.0rc1 out 2026-10-13 | 4 patch releases | **Yes.** 2.21 is in its maintenance window: critical fixes to Nov 2026, security fixes to May 2027, EOL Nov 2027 |
| `ansible-lint` | 26.6.0 (2026-06-30) | **26.10.1** (2026-10-13) | 5 releases (26.8.0, 26.9.0, 26.10.0, 26.10.1) | Upstream publishes no support window; only the latest release gets fixes (judgment, §2.3) |
| `ansible-compat` | 26.6.0 (2026-06-30) | **26.10.0** (2026-10-12) | 3 releases (26.8.0, 26.9.0, 26.10.0) | As for `ansible-lint` |
| `community.crypto` | 3.2.2 (2026-06-14) | **3.5.0** (2026-10-04) | 3 minor releases (3.3.0, 3.4.0, 3.5.0) | Yes on its terms: 3.x is the current major and needs `ansible-core>=2.17` |
| `ansible.posix` | 2.2.1 (2026-07-02) | **2.2.2** (2026-07-13) | 1 patch release | Yes: 2.2.x is current, needs `ansible-core>=2.16` |

All rows are **[data]** except where marked: versions and dates from PyPI (S-1,
S-2, S-3) and the Galaxy API (S-4, S-5); `ansible-core` support dates from the
support matrix (S-6).

### 2.2 `ansible-core`

- **[data]** Latest stable is 2.21.5 (2026-10-05). The 2.21 releases since ours:
  2.21.2 (2026-07-13), 2.21.3 (2026-08-10), 2.21.4 (2026-09-08), 2.21.5
  (2026-10-05). The 2.22 line is in pre-release: 2.22.0b1 (2026-09-18), 2.22.0b2
  (2026-09-29), 2.22.0rc1 (2026-10-13) (S-1).
- **[data]** Support matrix (S-6):

  | Version | GA | Critical to | Security to | EOL | Control Python | Target Python |
  |---|---|---|---|---|---|---|
  | 2.16 (our floor) | 06 Nov 2023 | 20 May 2024 | Nov 2024 | **EOL** Jul 2025 | 3.10–3.12 | 2.7, 3.6–3.12 |
  | 2.21 (ours) | May 2026 | Nov 2026 | May 2027 | Nov 2027 | 3.12–3.14 | 3.9–3.14 |
  | 2.22 (next) | Nov 2026 | May 2027 | Nov 2027 | May 2028 | 3.13–3.15 | 3.9–3.15 |

- **[data]** The source of that matrix also holds draft rows for 2.23 and 2.24,
  commented out so the published page does not show them. The draft 2.23 row
  gives target Python 3.10–3.15; the draft 2.24 row gives GA Nov 2027, control
  Python 3.14–3.16 and target Python 3.11–3.16 (S-6, the `.. *` comment block).
  A note on the published page says 2.24 releases in November 2027 (S-6).
- **[judgment]** The draft rows are not a commitment, but they agree with the
  documented rule that each release supports the six most recent Python versions
  (S-6, "target node Python support"). If they hold, **2.23 drops Python 3.9
  targets**, so the RHEL 9 boxes (`/usr/bin/python3.9`, §1.3) could not be
  managed by 2.23 or later without a newer interpreter on them. 2.22 still
  covers them.
- **[data]** Changes from 2.21.1 to 2.21.5 are bugfixes and minor changes, no
  breaking changes (S-7). Of note for us:
  - 2.21.3: `ansible-galaxy` retries a short download instead of failing on the
    hash mismatch (we run `ansible-galaxy collection install` at every bootstrap).
  - 2.21.4: `tempfile` rejects path components in `prefix`/`suffix` (path
    traversal); `uri`, `get_url`, `rpm_key`, `apt_key` and the `url` lookup mask
    credentials embedded in URLs.
  - 2.21.2: fixes a hang in parallel fact gathering caused by corrupt async job
    files.
- **[data]** The security fix for CVE-2026-11332 (`ansible-galaxy install`, git
  config injection through role dependencies) is in 2.21.1, which we have (S-7).
- **[data]** Deprecations that apply on our path to 2.24 (S-7, S-8):
  - `INJECT_FACTS_AS_VARS` defaulting to `True` is deprecated in 2.20 and switches
    to `False` by 2.24 (2.20 changelog, S-8). This is finding F1 of the epic.
  - 2.21 deprecates `apt_key` and `apt_repository` in favour of
    `deb822_repository`, the `ansible.module_utils.six` shim (removal in 2.24),
    and inferring task failure from a non-zero `rc` with no `failed` key (to be
    deprecated in 2.22) (S-7).
  - 2.21 removed the `paramiko` connection plugin and the `auto_legacy`
    interpreter-discovery options (S-7).
- **[data]** The tree uses `ansible.builtin.deb822_repository` and no
  `apt_key` or `apt_repository` module (R-6). Our `ansible.cfg` sets no
  `interpreter_python` (R-10).
- **[judgment]** Our declared floor `>=2.16` is inert, and it misleads. 2.16 is
  EOL and supports control Python 3.10–3.12 only (S-6), while the project requires
  3.14 (R-1). `community.crypto` 3.x needs 2.17 or later (§2.5), and
  `ansible-lint` excludes 2.17.x and needs 2.16.19 or later (S-2). Nothing below
  2.21 has been tested here.
- **[data]** For completeness, a `2.16.19` was published on 2026-06-18 (S-1),
  after the matrix's EOL date for 2.16. We have not established why; the matrix
  still lists 2.16 as EOL.

### 2.3 `ansible-lint` and `ansible-compat`

- **[data]** `ansible-lint` releases after ours (S-9):
  - **26.8.0** (2026-08-12): honours `ANSIBLE_VAULT_PASSWORD_FILE`; excludes
    `ansible-core 2.17.x` over CVE-2026-11332; dependency security updates.
  - **26.9.0** (2026-09-22): fixes for `include_tasks` path resolution, for
    `mock_modules` clobbering collections, and for profile-level `skip_list`;
    `black>=25.2.0` for CVE-2026-32274; drops `ruamel.yaml.clib`.
  - **26.10.0** (2026-10-12): `skip_list` respected for `yaml[comments]` under
    `--fix`; dependency security updates.
  - **26.10.1** (2026-10-13): lowers the `cryptography` minimum to 46.0.7.
- **[data]** None of those release notes has a breaking-changes section (S-9).
- **[data]** `ansible-compat` releases after ours: 26.8.0, 26.9.0, 26.10.0
  (S-3). We did not read its release notes; it moves in step with
  `ansible-lint`, which pulls it in.
- **[judgment]** Neither project publishes a support window, so "supported"
  means only that fixes ship in new releases. Several of the four releases we lack
  are security updates to dependencies. That makes staying current on the lint
  side cheap and worth doing.
- **[judgment]** 26.9.0's fixes to `include_tasks` resolution may change what
  ansible-lint reports for our tree, which uses OS-family `include_tasks`. A bump
  could surface new findings. That is a reason to bump on its own PR, not a
  reason to stay behind.

### 2.4 `community.crypto`

- **[data]** Latest is 3.5.0 (2026-10-04). Between ours (3.2.2) and it: 3.3.0
  (2026-07-13), 3.4.0 (2026-09-06), 3.5.0 (2026-10-04). Every 3.x release
  declares `requires_ansible: >=2.17.0`. The 2.x line is still maintained in
  parallel: 2.26.9 (2026-06-14), with `requires_ansible: >=2.9.10` (S-4).
- **[data]** The README says ansible-core before 2.17 is unsupported by 3.x and
  points users of older cores to 2.x.y. It also says the collection follows
  semantic versioning, so breaking changes come only in major releases (S-10).
- **[data]** Notable changes from 3.2.2 to 3.5.0 (S-11):
  - 3.3.0: ML-DSA private-key support in `openssl_privatekey*`, `openssl_csr*`,
    `x509_certificate*` and others.
  - 3.4.0: the `select_crypto_backend` option is **deprecated** on
    `openssl_privatekey`, `openssl_csr`, `openssl_pkcs12`, `x509_certificate` and
    others, for removal in 4.0.0. It adds `openssl_pkcs12_extract` and
    `openssl_pkcs12_info`.
  - 3.5.0: `mldsa44` keys for `openssh_keypair`; private-key content is marked
    sensitive on ansible-core 2.22+.
- **[data]** The tree sets no `select_crypto_backend` (R-6), so the 3.4.0
  deprecation does not warn for us.
- **[data]** 3.0.0 was the major release that dropped ansible-core before 2.17
  and Python before 3.7, removed the Entrust modules and the `pyopenssl` backend,
  and made `select_crypto_backend` ignored outside the ACME modules (S-11).
- **[judgment]** Our floor `>=2.20.0` admits **two majors**: the 2.x line and the
  3.x line. A fresh install resolves to the newest, 3.5.0. A cache that holds a
  2.2x stays on it (§3.2). The floor therefore does not say which major the lab
  depends on.

### 2.5 `ansible.posix`

- **[data]** Latest is 2.2.2 (2026-07-13); ours is 2.2.1 (2026-07-02) (S-5).
  2.2.2's only change is a README contact update (S-12).
- **[data]** 2.2.1 carries a security fix: `authorized_key` no longer follows
  symlinks when run as root (local privilege escalation). 2.2.0 removed many uses
  of the deprecated `ansible.module_utils._text` and `six` imports, including in
  the `profile_tasks` callback we enable (S-12).
- **[data]** 2.x declares `requires_ansible: >=2.16.0` (2.2.x) and 2.0.0 made
  breaking changes only to `firewalld` option types (S-5, S-12). We use no
  `firewalld` module (R-6).
- **[judgment]** Our floor `>=1.5.0` also admits two majors (1.x and 2.x). We
  have no gap that matters here; the risk is only the unbounded floor.

## 3. Management review

### 3.1 Python tools: locked, reproducible

- **[data]** `ansible-core`, `ansible-lint` and `ansible-compat` resolve through
  `uv.lock` (R-2). `pyproject.toml` explains why `ansible-lint` sits in the same
  venv as `ansible-core`: one venv holds one `ansible-core`, so the linter and the
  `ansible` CLI cannot skew (#691, R-1).
- **[judgment]** This side is reproducible. Every checkout that syncs the lock
  gets the same versions. The weakness is only that the lock is not refreshed: it
  has held `ansible-core 2.21.1` since 2026-06-29 (§1.1), and nothing in the
  pipeline flags that four patch releases have passed.

### 3.2 Galaxy collections: floors, no lock, skip-if-present

- **[data]** `ansible/requirements.yml` gives `>=` floors only (R-4). Nothing
  records the resolved version: there is no lock file for collections.
- **[data]** `mqlab`'s `_galaxy_install_step` runs
  `ansible-galaxy collection install -r ansible/requirements.yml -p build/cache`
  with no `--upgrade` or `--force`. Its comment says ansible-galaxy skips a
  collection already present (#343). `_verify_galaxy_collections` then checks only
  that each declared collection's directory exists, not its version (#596)
  (`src/mqlab/cli.py`, R-11).
- **[data]** `build/cache` is the **shared** bucket: in a git worktree it is a
  symlink back to the main checkout's `build/`, so every worktree uses the one
  cache. It survives a routine clean and is cleared only by
  `mqlab build clean --cache` (`docs/development/build-layout.md`, R-12).
- **[judgment]** Consequences:
  - **Reproducibility.** The collection versions are decided by **when a cache
    was first filled**, not by anything in git. Today's cache holds
    `community.crypto 3.2.2` and `ansible.posix 2.2.1` (R-5). A cache filled today
    would get 3.5.0 and 2.2.2 (S-4, S-5). Two runs of the same commit can use
    different collection code, and no artifact says which.
  - **Drift across hosts and cache instances.** Each host (the macOS VM, each
    cloud VM) has its own main-checkout `build/cache`. They drift apart with no
    signal. A VM rebuild keeps `build/` (it is host-mounted), so drift outlives
    rebuilds.
  - **Silent major moves.** Because the floors admit two majors (§2.4, §2.5), a
    cache filled from scratch can jump a major version, for example
    `community.crypto` 2.x to 3.x, with no change in git.
  - **Upgrades never arrive.** The flip side: once present, a collection never
    updates. Security fixes (such as `ansible.posix 2.2.1`'s) reach a host only
    if someone clears the cache by hand.
  - **The verify step hides drift.** It checks presence only, so a too-old
    collection passes.

### 3.3 The lint environment cannot resolve `community.crypto`

- **[data]** ansible-lint logs `Invalid value (None)for resolved_fqcn attribute
  of community.crypto.<module> module` for the `community.crypto` modules. The
  epic's sweep counted about 14 per run (epic spec §2). A `vrg-container-run --
  vrg-validate` of this branch on 2026-10-19 logged 14: one per call site of
  all five modules in §1.2 (4 each for `openssl_privatekey`, `openssl_csr` and
  `x509_certificate`, 1 each for `openssl_pkcs12` and `openssh_keypair`) (R-17).
  Every `community.crypto` task in the tree is unresolved in lint.
- **[data]** The same run installed `ansible-core==2.21.1` and
  `ansible-lint==26.6.0` into the container from the lock before linting (R-17).
  The lint environment therefore uses the locked versions.
- **[data]** `vrg-validate` runs `ansible-lint -c <vergil's bundled config>` from
  the repo root. From the repo's `.ansible-lint.yml` it reads **only** `skip_list`
  and `exclude_paths` and re-passes them as `-x` and `--exclude`
  (`vergil_tooling/bin/validate_common.py`, `_ansible_lint_overrides`, in
  vergil-tooling 2.1.248, R-13).
- **[data]** `.ansible-lint.yml` excludes `build/` (R-14). The collections live
  in `build/cache`, which `ansible/ansible.cfg` names as `collections_path`
  (R-10). A fresh worktree has no `build/` until `mqlab build ensure` runs, and
  the build directory is gitignored, so CI has none (R-12).
- **[judgment]** Excluding `build/` is not, by itself, the cause.
  `exclude_paths` controls which files are linted, not where modules are resolved
  from. We think there are two causes:
  1. In CI and in a fresh worktree, no collection is installed anywhere.
  2. Even where `build/cache` exists, ansible-lint runs from the repo root, and
     Ansible reads an `ansible.cfg` from the working directory, so
     `ansible/ansible.cfg` and its `collections_path` are not in effect.

  We have not tested either cause in isolation; a fix should confirm them first.
- **[judgment]** The gap has a real cost. With the module unresolved,
  ansible-lint cannot check those tasks' arguments, so a wrong
  `community.crypto` argument passes lint and fails only at runtime. It has also
  pushed authors away from collection modules: two roles use shell or a
  `sysctl.d` drop-in instead of `ansible.posix.mount` and `ansible.posix.sysctl`,
  citing the resolver (R-6, the comments in `pdrbd-qm` and `opensearch`).
- **[judgment]** A repo-local fix through `.ansible-lint.yml` (for example
  `mock_modules`, or an `offline`/requirements setting) would **not** take effect
  today, because `vrg-validate` ignores every key but the two above (R-13). The
  fix belongs in vergil-tooling, or in a repo-side step that installs the
  collections where ansible-lint looks before `vrg-validate` runs.

### 3.4 How a version bump would be proven

- **[data]** What the pipeline runs today: the unit suite (`pytest`) and
  ansible-lint inside `vrg-validate` (R-13). Neither runs a playbook against a
  host.
- **[data]** The box manifest hash covers the bake playbook and roles, the OS
  pin, the MQ-version pin and baked components. It does **not** include the
  `ansible-core` version, `uv.lock` or `ansible/requirements.yml`
  (`lab/boxes/_manifest-hash.sh`, `docs/development/box-model.md`, R-15). An
  Ansible bump therefore does not force a rebake.
- **[data]** The lab's established proof of runtime change is a cold-rebuild
  validation per stack, as done for MQ 10.0.1 and the OS default moves
  (#1474, #1475, #1496, #1497; `docs/site/docs/reference/index.md`, R-16).
- **[judgment]** A version bump would be proven in three layers:
  1. **Static.** `vrg-validate` green on the bump PR. After the lint fix (§3.3)
     this also checks collection-module arguments.
  2. **Deprecation scan.** Compare the warnings in a bootstrap or box-build log
     before and after the bump, as the epic's §1 sweep did, so new deprecations
     are seen at once.
  3. **Runtime.** `mqlab box rebuild` of each affected box, then a cold bootstrap
     and `mqlab qm e2e` per stack. Without an explicit rebuild the old baked
     boxes keep running, because the manifest hash does not see the bump. For an
     `ansible-core` minor bump (2.21 → 2.22), cover at least one RHEL 9 stack,
     because that is where the target-Python window is tightest (§2.2).

## 4. Recommendations

Each recommendation names the evidence it rests on. **VT** marks an input to the
later vergil-tooling dependency-management work.

1. **Bump `ansible-core` in the lock to 2.21.5.** *[judgment, on data S-1, S-6,
   S-7.]* It is in-series bugfixes with no breaking changes, and it brings the
   `ansible-galaxy` retry fix and the credential-masking fixes. Prove it with
   layers 1–3 of §3.4 (one stack is enough for a patch bump).
2. **Plan 2.22 for after its GA (Nov 2026), not before.** *[judgment, on data
   S-1, S-6.]* 2.21's critical-fix window ends in Nov 2026, the month 2.22 ships.
   2.22 supports our control Python (3.14) and still supports RHEL 9's Python 3.9
   targets. Move to it once 2.22.0 final is on PyPI, with a full §3.4 proof
   including a RHEL 9 stack.
3. **Decide RHEL 9's interpreter before 2.23 (May 2027 per the draft row).**
   *[judgment, on draft data S-6.]* If the draft 2.23 row holds, RHEL 9's
   `/usr/bin/python3.9` drops out of support. The choices are a newer interpreter
   on RHEL 9 boxes, capping `ansible-core` at 2.22 while RHEL 9 stays selectable,
   or retiring RHEL 9 from the catalog. We have not verified which newer Python
   streams RHEL 9 ships; check Red Hat's documentation before choosing.
4. **Raise the `ansible-core` floor to what we test.** *[judgment, on data R-1,
   S-2, S-6.]* `>=2.16` admits versions that cannot run on our Python and that
   our collections and linter reject. Set the floor to the current minor
   (`>=2.21`), and consider a ceiling at the next untested minor (`<2.22`) so a
   minor bump is always a deliberate lock change. **VT:** a fleet convention for
   floor and ceiling on version-matched tools.
5. **Bump `ansible-lint` (and so `ansible-compat`) to 26.10.1.** *[judgment, on
   data S-9.]* There are no breaking changes and several dependency security
   fixes. Expect possible new findings from 26.9.0's `include_tasks` resolution
   fixes, and land them in the same PR.
6. **Lock the galaxy collections.** *[judgment, on data R-4, R-11, S-4, S-5.]*
   Replace the `>=` floors with exact versions in `ansible/requirements.yml`:
   `community.crypto` 3.5.0 and `ansible.posix` 2.2.2 after a proven bump, or the
   currently installed 3.2.2 and 2.2.1 as a no-change first step. Ansible has no
   native lock file for collections, so the exact pin *is* the lock. **VT:** this
   is the main input — collections need a lock-equivalent and a bump workflow
   like `uv lock --upgrade-package`.
7. **Make the install converge on the pin.** *[judgment, on data R-11.]* Once
   pins are exact, the install step should replace a present collection whose
   version differs (for example by passing `--force` when the installed version
   does not match the pin, or by comparing `GALAXY.yml` first).
   `_verify_galaxy_collections` should check the installed **version**, not just
   the directory, and fail loudly on a mismatch, in line with its existing
   fail-loud design (#596). **VT:** drift detection across cache instances.
8. **Fix the lint environment.** *[judgment, on data R-10, R-13, R-14.]* First
   confirm the two suspected causes (§3.3). Then make the collections resolvable
   in the `vrg-validate` run. Two options:
   - vergil-tooling installs a repo's declared collections (from a configurable
     requirements path) into a lint-local collections path, and points
     ansible-lint at it with `ANSIBLE_COLLECTIONS_PATH`.
   - vrg-validate honours more of the repo's `.ansible-lint.yml`.

   Either way, keep `build/` excluded: it is not our source. **VT:** this is a
   vergil-tooling change; the repo cannot fix it alone (R-13).
9. **Fold the Ansible versions into the box manifest hash.** *[judgment, on data
   R-15.]* Adding the locked `ansible-core` version and the pinned collection
   versions to `_manifest-hash.sh` would make a bump force a rebake, as an MQ or
   OS re-pin already does. That turns layer 3 of §3.4 from a manual step into the
   default.
10. **Restore `inject_facts_as_vars` to its default only once the floor is
    `>=2.24`.** *[judgment, on data S-8, S-6.]* F1 sets `inject_facts_as_vars =
    False` explicitly in `ansible.cfg`, ahead of the 2.24 default change. Keep
    the line while any supported lock could be below 2.24, where the default is
    still `True`. Remove it in the PR that raises the floor to `>=2.24` (GA Nov
    2027 per S-6), after reading the 2.24 porting guide: if 2.24 also deprecates
    the option itself, the line must go then anyway. The guard test from F1 stays
    either way.
11. **Report dependency age in the pipeline.** *[judgment.]* Nothing tells us the
    lock is four months old. A periodic check of the lock and the collection pins
    against PyPI and Galaxy, run in CI or by a scheduled job, would make this
    report's measurement routine. **VT:** a fleet-wide dependency-currency check.

## 5. Open items

- The suspected causes of the lint resolution gap (§3.3) are untested.
- Why `ansible-core 2.16.19` was published after 2.16's EOL is unknown (§2.2).
- The `ansible-compat` release notes were not read (§2.3).
- RHEL 9's available newer Python streams are unverified (§4, item 3).
- `docs.ansible.com` returned HTTP 429 on 2026-10-19, so the support matrix was
  read from its source file (S-6). A person should confirm the published page
  matches.

## 6. Sources

Upstream sources, all retrieved 2026-10-19:

- **S-1** PyPI JSON, `ansible-core`: <https://pypi.org/pypi/ansible-core/json>
  (human page: <https://pypi.org/project/ansible-core/#history>)
- **S-2** PyPI JSON, `ansible-lint` (latest 26.10.1, `requires_dist`):
  <https://pypi.org/pypi/ansible-lint/json>
- **S-3** PyPI JSON, `ansible-compat`: <https://pypi.org/pypi/ansible-compat/json>
- **S-4** Galaxy API, `community.crypto` versions (with `requires_ansible`):
  <https://galaxy.ansible.com/api/v3/plugin/ansible/content/published/collections/index/community/crypto/versions/?limit=40>
  (human page: <https://galaxy.ansible.com/ui/repo/published/community/crypto/>)
- **S-5** Galaxy API, `ansible.posix` versions:
  <https://galaxy.ansible.com/api/v3/plugin/ansible/content/published/collections/index/ansible/posix/versions/?limit=40>
  (human page: <https://galaxy.ansible.com/ui/repo/published/ansible/posix/>)
- **S-6** ansible-core support matrix and target-Python policy. Published page:
  <https://docs.ansible.com/ansible/latest/reference_appendices/release_and_maintenance.html>
  (HTTP 429 on retrieval). Read from its source, at commit `72eedf8`
  (2026-09-13, "Ansible 14 is out, Ansible 13 is EOL"):
  <https://github.com/ansible/ansible-documentation/blob/devel/docs/docsite/rst/reference_appendices/release_and_maintenance.rst>
- **S-7** ansible-core 2.21 changelog:
  <https://github.com/ansible/ansible/blob/stable-2.21/changelogs/CHANGELOG-v2.21.rst>
- **S-8** ansible-core 2.20 changelog (the `INJECT_FACTS_AS_VARS` deprecation):
  <https://github.com/ansible/ansible/blob/stable-2.20/changelogs/CHANGELOG-v2.20.rst>
- **S-9** ansible-lint GitHub releases:
  <https://github.com/ansible/ansible-lint/releases> (26.8.0:
  <https://github.com/ansible/ansible-lint/releases/tag/v26.8.0>; 26.9.0:
  <https://github.com/ansible/ansible-lint/releases/tag/v26.9.0>; 26.10.0:
  <https://github.com/ansible/ansible-lint/releases/tag/v26.10.0>; 26.10.1:
  <https://github.com/ansible/ansible-lint/releases/tag/v26.10.1>)
- **S-10** community.crypto README:
  <https://github.com/ansible-collections/community.crypto/blob/main/README.md>
- **S-11** community.crypto changelog:
  <https://github.com/ansible-collections/community.crypto/blob/main/CHANGELOG.md>
- **S-12** ansible.posix changelog (stable-2 branch):
  <https://github.com/ansible-collections/ansible.posix/blob/stable-2/CHANGELOG.rst>;
  releases: <https://github.com/ansible-collections/ansible.posix/releases>

Repo facts (paths relative to the repo root, `origin/develop` at `ef6e490`,
unless noted):

- **R-1** `pyproject.toml`
- **R-2** `uv.lock`, and `git log -S'version = "2.21.1"' -- uv.lock`
- **R-3** the main checkout's `.venv/lib/python3.14/site-packages/*.dist-info`
  (local, not in git)
- **R-4** `ansible/requirements.yml`
- **R-5** the main checkout's `build/cache/ansible_collections/*.info/GALAXY.yml`
  (local, shared bucket, not in git)
- **R-6** `grep` of `ansible/` for `community.*`, `ansible.posix.*`,
  `select_crypto_backend`, `apt_key` and `apt_repository`
- **R-7** the main checkout's `build/state/runs/*.log` (local, not in git): the
  `is using the discovered Python interpreter at` warnings, for example
  `20261005T184336Z-box-build.log`
- **R-8** `src/mqlab/inventory.py`
- **R-9** `lab/versions.yaml`
- **R-10** `ansible/ansible.cfg`
- **R-11** `src/mqlab/cli.py` (`_galaxy_install_step`,
  `_verify_galaxy_collections`)
- **R-12** `docs/development/build-layout.md`
- **R-13** vergil-tooling 2.1.248, `vergil_tooling/bin/validate_common.py`, as
  installed at `/opt/vergil/vergil-tooling/venv` on the dev VM
- **R-14** `.ansible-lint.yml`
- **R-15** `lab/boxes/_manifest-hash.sh`, `docs/development/box-model.md`
  ("Staleness: manifest-hash + graduated age")
- **R-16** `docs/site/docs/reference/index.md`
- **R-17** the output of `vrg-container-run -- vrg-validate` run in this
  branch's worktree on 2026-10-19 (local, not kept)
