# Guest runtime spike: pinned CPython 3.14.8, pymqi from sdist, offline installs

> Plan task **T2** of epic
> [logical-minds-foundry/.github#294](https://github.com/logical-minds-foundry/.github/issues/294)
> (issue [#1349](https://github.com/logical-minds-foundry/mq-resiliency-lab-for-linux/issues/1349)).
> It turns the spec's §11 judgment rows into data before T3 starts.
>
> The live data was gathered on 2026-10-05 on two hosts:
>
> - **macOS arm64** (Lima Vergil VM, Ubuntu 24.04.5 aarch64 guests). The evidence is
>   in the [#1349 comment](https://github.com/logical-minds-foundry/mq-resiliency-lab-for-linux/issues/1349#issuecomment-6000718652).
> - **cloud x86_64** (GCP Vergil VM: Ubuntu 24.04.5 x86_64 guests and a RHEL 9.6
>   guest). This was validation task
>   [#1359](https://github.com/logical-minds-foundry/mq-resiliency-lab-for-linux/issues/1359),
>   run by a cloud-VM agent; see its
>   [Outcome comment](https://github.com/logical-minds-foundry/mq-resiliency-lab-for-linux/issues/1359#issuecomment-6001457307).
>
> Both hosts ran the same reproducible assets under
> [`assets/guest-runtime-spike/`](assets/guest-runtime-spike/): `bundle.py`,
> `demo-pyproject.toml` and `spike.yml`. Those comments hold every results file
> verbatim.
>
> Convention: each claim is tagged **[data]** (what a run or a cited source shows)
> or **[judgment]** (our reasoning on top).

## 1. Verdict

**GO.** Every spec §11 judgment row now holds as data, on both arches and both
distros. The spike also found **four corrections** that the plan's tasks must
absorb (§4), and one false premise in the spec (§5). None of them changes the
design.

## 2. Results

| Row | macOS arm64 | cloud x86_64 |
|---|---|---|
| 1 runtime: Ubuntu 24.04.5 | ✅ app-client, svc-sim, infra-client | ✅ app-client, svc-sim |
| 1 runtime: RHEL 9.6 | refused on ARM (box-model D11) | ✅ nha-rhel-crr-a1 |
| 2 pymqi from sdist (mq-client box) | ✅ app-client, svc-sim | ✅ app-client, svc-sim |
| 3 offline, hash-pinned install on RHEL | n/a | ✅ nha-rhel-crr-a1 |
| 4 build-requirement versions | lock-derived (below) | same lock |

### 2.1 Row 1: the pinned runtime

- **[data]** The tarball each guest received matched the pin in
  `lab/versions.yaml`: x86_64 `371b6c28…0ae8`, aarch64 `abc0c8dd…ae23`.
- **[data]** On every host, `python3.14` reports **3.14.8**, built with Clang 22.1.3,
  and links **OpenSSL 3.5.9** and **sqlite 3.53.1**. `ctypes`, `venv`, `ensurepip`,
  `zlib`, `lzma` and `bz2` all import.
- **[data]** Guest OS and glibc: Ubuntu 24.04.5 has glibc **2.39**. RHEL 9.6 (Plow)
  has glibc **2.34**.
- **[data]** `objdump -T` over every ELF file in both tarballs shows the highest
  required glibc symbol version is **`GLIBC_2.17`** on x86_64 and aarch64. Both
  distros clear that floor by a wide margin.
- **[judgment]** One python-build-standalone glibc build per arch is enough for
  every Ubuntu/RHEL version the OS axis (#280) is likely to add. Any glibc newer
  than 2.17 qualifies, so Ubuntu 26.04 and RHEL 10 add no new guest runtimes.

### 2.2 Row 2: pymqi 1.12.13 from sdist

- **[data]** On the mq-client box (Ubuntu, gcc 13.3.0, MQ SDK at `/opt/mqm`), on both
  arches, `pymqi==1.12.13` built and imported on 3.14.8. The install used
  `--no-index --find-links deps/ --require-hashes --no-build-isolation`, after the
  hash-pinned build requirements were installed into the same venv.
- **[data]** `pymqi.__version__` reports **`1.12.11`** while `pip freeze` shows
  **`pymqi==1.12.13`**. Upstream's version string is stale.
- **[data]** pymqi declares no `[build-system]`, and its `setup.py` imports
  `distutils`. With setuptools 84.0.0 installed, the build succeeds through
  setuptools' shim on 3.14.

### 2.3 Row 3: an offline install on RHEL

- **[data]** On nha-rhel-crr-a1, a venv on the pinned runtime installed the
  lock-derived set with `--no-index --require-hashes`: `packaging==26.3`,
  `setuptools==84.0.0` and `wheel==0.48.0`.
- **[data]** The RHEL guest **can reach PyPI**. The reachability probe printed
  `PYPI REACHABLE` (see §5).

### 2.4 Row 4: build-requirement versions (for T8's `sdist-build` group)

- **[data]** `uv lock` resolved `setuptools==84.0.0` and `wheel==0.48.0`, plus
  `packaging==26.3` pulled in transitively by wheel (`packaging>=24.0`). The hashes
  are in `bundle.py`'s output in #1359.

## 3. What failed first, and why it matters

| Failure | Where | Cause | Status |
|---|---|---|---|
| `No matching distribution found for packaging>=24.0` | arm64 row 2, first run | a hand-picked `setuptools`+`wheel` list had no transitive closure | fixed: `bundle.py` now stages from a real `uv lock` |
| `No such file or directory: 'clang'` | arm64 row 2, second run | python-build-standalone's sysconfig records `CC=clang -pthread`, `LDSHARED=clang -pthread -shared -Wl,-z,noexecstack -Wl,-z,relro -Wl,-z,now -Wl,--build-id=sha1 -Wl,--exclude-libs,ALL`; guests have gcc only | spike overrides `CC`/`LDSHARED`; correction C2 |
| 602 × `Cannot open` under `share/terminfo` | dev VM, unpacking into `build/` | `build/` is a virtiofs mount of the Mac's **case-insensitive** filesystem, and terminfo has case-colliding names (`2621A`/`2621a`) | correction C3 |
| row 1 `rc=141` on Ubuntu x86 | #1359, first run | `ldd --version \| head -1` under `pipefail`: `ldd` takes SIGPIPE when `head` exits. The probe failed 13/20 on RHEL and intermittently on Ubuntu, so the arm64 pass was luck. | fixed in `spike.yml` (`sed -n 1p`) |

- **[judgment]** The first failure **confirms a design choice**. `--no-index
  --require-hashes` refused the incomplete set loudly instead of silently fetching.
  That is the behaviour spec §7 asks for, and it shows why §5.6 derives every set
  from `uv.lock`.

## 4. Corrections the plan must absorb

**C1, T6/T8: the selfcheck reads versions from metadata.** It must report
versions with `importlib.metadata.version(...)`, never a module's
`__version__` (§2.2). The plan's selfcheck already does this, so this records why.

**C2, T5: override the compiler for sdist builds.** `component-install`
sets `CC` and `LDSHARED` when building sdist dependencies.

- **[judgment]** Swap only the compiler name and keep the recorded flags: `CC=gcc -pthread`,
  `LDSHARED=gcc -pthread -shared -Wl,-z,noexecstack -Wl,-z,relro -Wl,-z,now
  -Wl,--build-id=sha1 -Wl,--exclude-libs,ALL`. Derive both from the interpreter's own
  `sysconfig` by replacing the leading `clang` with `gcc`, so a future pin bump
  can't silently drift.
- The spike's plain `gcc -shared` worked, but it drops the hardening flags
  (`relro`, `now`, `noexecstack`).
- Add a structural test that the role exports both variables before the
  `requirements.txt` install.

**C3, T4: unpack the dev-VM interpreter outside `build/`.**

- `mqlab component build` keeps the **tarball** in `$(mqlab build path cache)/runtime/`,
  since a single file is safe on any filesystem.
- It unpacks onto a Linux filesystem: `$XDG_CACHE_HOME/mqlab/runtime/` (default
  `~/.cache/mqlab/runtime/`).
- The test venv (`paths.work(...)`) must also not live on the case-insensitive
  mount if it ever contains terminfo-like trees. **[judgment]** A venv holds only
  symlinks plus site-packages, so `work/` is acceptable. Revisit only if a dependency
  ships case-colliding files.
- Guests unpack into `/opt` on ext4/xfs and are unaffected.

**C4, T8: the `sdist-build` group's pins.** Use `setuptools==84.0.0` and
`wheel==0.48.0`. `uv lock` adds `packaging` transitively; do not hand-list it.

## 5. A false premise in the spec

- **[data]** Spec §1.1 says *"RHEL guests are offline and use the DVD repo."* The
  RHEL 9.6 guest on the cloud host reached `https://pypi.org/simple/` (#1359).
- **[judgment]** The design doesn't depend on that premise. Every guest install
  uses `--no-index --find-links` with hashes, so the property M1 guarantees is
  **"installs never consult an index"**, not "the network is absent". Two
  consequences:
  - V2 ([#1358](https://github.com/logical-minds-foundry/mq-resiliency-lab-for-linux/issues/1358))
    should assert the former, using the install logs and the `--no-index` flags, not
    claim "no PyPI access from any RHEL guest".
  - The spec's §1.1 sentence gets a correction note in the documentation-review
    sweep ([#1348](https://github.com/logical-minds-foundry/mq-resiliency-lab-for-linux/issues/1348)).

## 6. Reproducing

```bash
T="$(uv run mqlab build path temp)/spike-294"
python3 docs/reports/assets/guest-runtime-spike/bundle.py "$T"
cd ansible && uv run ansible-playbook -i "$(uv run mqlab build path work)/inventory.ini" \
  ../docs/reports/assets/guest-runtime-spike/spike.yml -e spike_dir="$T" \
  --limit app-client,svc-sim[,nha-rhel-crr-a1]
```

The playbook is evidence tooling, not lab provisioning. No site or bake playbook
includes it. Each host's raw output lands in `$T/results/<host>.txt`.
