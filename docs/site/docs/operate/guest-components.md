# Guest components

Everything the lab runs **on its guests** that we write ourselves — the HA/DR
state collectors, the request/reply clients, the benchmark, the probes and the DR
measurement clients — ships as a **guest component**: a standalone, standard
Python project under
[`components/`](https://github.com/logical-minds-foundry/mq-resiliency-lab-for-linux/tree/develop/components),
built and tested on one pinned Python runtime and installed onto guests from
source with standard tooling. The design is epic
[logical-minds-foundry/.github#294](https://github.com/logical-minds-foundry/.github/issues/294);
the authoritative contract is
[`components/README.md`](https://github.com/logical-minds-foundry/mq-resiliency-lab-for-linux/blob/develop/components/README.md).
This page is the operator's view: what is on a guest, where it lives, and the
`mqlab component` verbs that build, inspect and reinstall it.

Before #294 the same code ran as loose `clients/*.py` scripts and `/usr/local/bin`
collector copies on the guests' system Python, with ad-hoc pymqi venvs
(`/home/vagrant/mqvenv`, `/var/mqm/rvenv`) installed from PyPI at provision time.
All of those are gone; the provisioning roles delete them if they find them.

## The two components

| Component | Import package | Baked into (box role) | What it runs |
|---|---|---|---|
| [`mq-resiliency-observability`](https://github.com/logical-minds-foundry/mq-resiliency-lab-for-linux/blob/develop/components/mq-resiliency-observability/README.md) | `mqro` | `mq-rdqm`, `mq-nativeha`, `pcmk`, `san` | the stdlib-only, non-MQI HA/DR state collectors `lab-cluster-state`, `lab-nativeha-state`, `lab-loglifecycle-state`, `lab-rdqm-state`, each writing a node_exporter textfile on a 5-second timer |
| [`mq-resiliency-clients`](https://github.com/logical-minds-foundry/mq-resiliency-lab-for-linux/blob/develop/components/mq-resiliency-clients/README.md) | `mqrc` | `mq-client` (`svc-sim`, `app-client`, `mon-probe`) | `mq-app-requester`, `mq-svc-responder`, `mq-bench`, the authorization / dead-letter / reconnect probes, and the DR flow, responder and loss-analysis clients |

The box roles come from `roles.<role>.components` in
[`lab/versions.yaml`](https://github.com/logical-minds-foundry/mq-resiliency-lab-for-linux/blob/develop/lab/versions.yaml).
The `obs` and `infra` boxes bake no component.

The observability collectors keep the boundary they always had: they shell out to
the cluster's own command-line tools (`crm_mon`, `drbdsetup`, `dspmq`,
`rdqmstatus`, `df`/`ls`), never the MQI, and supplement the stock MQ Prometheus
exporter rather than replace it. Only `mq-resiliency-clients` uses the MQI; its
pymqi dependency is compiled on the guest against the MQ SDK.

## Two quality tiers

| | `mqlab` (repo root) | `components/<name>/` |
|---|---|---|
| Bar | full Vergil: `vrg-validate`, container, 100% branch coverage | a standard Python project: `pyproject.toml`, `src/` layout, unit tests, its own `uv.lock` |
| Driven by | Vergil | plain `uv`; no container, no `vergil.toml` |
| Knows about the other | yes: `mqlab` builds, tests and installs components | **no**: a component never imports the lab or Vergil |

This is the middle step of the [factor-out path](../methodology.md#5-what-the-lab-is-for-the-evolution):
a component is already a real project with its own tests and lock, and becomes
fully Vergil-managed only when it is extracted into its own repository. Its
directory name is that future repository's name. `tests/test_component_boundary.py`
enforces the boundary in both directions.

## Where a component lives on a guest

| Path | What it is |
|---|---|
| `/opt/vergil/cpython-3.14/` | The one pinned guest runtime: a python-build-standalone CPython `install_only` build (`runtime.python` in `lab/versions.yaml`, currently 3.14.8), stamped with a `PIN` file. Never Ansible's interpreter and never the distro's. |
| `/opt/logical-minds-foundry/<name>/releases/<version>+<tree>-<UTC stamp>/venv` | One install of the component. Built at this path and never moved. |
| `/opt/logical-minds-foundry/<name>/venv` | Symlink to the live release's venv. Units and operators run `venv/bin/<entry-point>`. |
| `/opt/logical-minds-foundry/<name>/previous` | Symlink to the previous release's venv: the rollback target. |
| `/opt/logical-minds-foundry/<name>/INSTALLED.json` | What is installed: version, source tree hash, commit, runtime, release id, install time. |
| `/etc/opt/logical-minds-foundry/<name>/<unit>.env` | Site values for each unit (QM name, CONNAME, TLS arguments, rate). Rendered by the per-run Ansible roles, never by the component. |
| `/usr/lib/systemd/system/<unit>` | The component's own static unit files, installed verbatim (no templating). |

The `<tree>` in a release name is the git tree hash of `components/<name>` at the
commit it was built from, so an installed release always names its exact source.

Every runnable is a console-script entry point; nothing runs `python file.py`. For
example, the on-demand benchmark is
`/opt/logical-minds-foundry/mq-resiliency-clients/venv/bin/mq-bench`, and the
`mq-svc-responder@<queue>.service` unit reads
`/etc/opt/logical-minds-foundry/mq-resiliency-clients/mq-svc-responder.env`.

### Baked inert, started per run

A box bake runs the `runtime-install` role and then the `component-install` role
for each component the box bakes. The units are installed but left **inert**.
At provision time, the per-run roles (`cluster-state`, `nativeha-state`,
`rdqm-state`, `app-requester`, `mq-inter-qm`, `bench-client`) check that the
component is baked, render its env files and enable its units. A committed change
to a component, or a bump of the runtime pin, changes the box's manifest hash, so
the next bootstrap rebakes exactly the boxes that carry it.

### Installs never consult an index

Every guest install uses the venv's own `pip` with `--no-index --find-links deps/`
and `--require-hashes`, against artifacts staged on the dev host from `uv.lock`.
The guarantee is that **installs never consult a package index**. It does not
depend on the guest's network: the RHEL 9.6 guest can reach PyPI
([#1359](https://github.com/logical-minds-foundry/mq-resiliency-lab-for-linux/issues/1359),
[spike report §5](https://github.com/logical-minds-foundry/mq-resiliency-lab-for-linux/blob/develop/docs/reports/2026-10-guest-runtime-spike.md)),
but the install never asks it.

## `mqlab component` verbs

The bake installs components for you. These verbs are for the development loop:
change a component, test it on the pinned runtime, and put it on a running guest
without rebaking the box.

```bash
mqlab component build <name>... | --all            # test on the pinned runtime, stage an artifact
mqlab component status [<name>...] [--host <guest>]... # built artifact vs HEAD; what each guest runs
mqlab component install <name> --host <guest>... [--version <v>]  # reinstall onto running guests
```

### `build`: test and stage

`mqlab component build` is the only producer of an installable artifact. In order,
it:

1. refuses if `components/<name>` has uncommitted or untracked changes (an
   artifact must be a commit);
2. fetches the pinned runtime tarball into `$(mqlab build path cache)/runtime/`,
   checks its sha256 against the pin, and unpacks it on the dev VM;
3. refuses a stale lock (`uv lock --check`);
4. creates a build-owned test venv from the pinned interpreter, proves it was made
   from that interpreter, and runs the component's tests (a failure stages
   nothing);
5. stages, under `$(mqlab build path cache)/components/<name>/<version>+<tree>/`,
   the component's wheel, hash-pinned `requirements.txt` (and
   `build-requirements.txt` for sdist dependencies such as pymqi), every
   dependency artifact fetched from `uv.lock` and checked against its hash
   (`deps/`), the static units (`systemd/`), and `BUILD.json`;
6. points `CURRENT` at the new artifact.

Each refusal names the fix, for example
`run uv --directory components/<name> lock, commit it, then mqlab component build <name>`.
An unknown component name, or neither a name nor `--all`, exits 2 and lists the
known components.

### `status`: what is built and what is installed

The output looks like this (the tree hashes are illustrative):

```text
COMPONENT  CURRENT  TREE  RUNTIME  HEAD
mq-resiliency-clients  0.1.0  3f2a…  3.14.8+20261003  up to date
  app-client: 0.1.0 tree 3f2a… runtime 3.14.8+20261003  matches CURRENT
```

Without `--host` it reads only the dev side: each component's `CURRENT` artifact
and whether it still matches HEAD (`STALE (HEAD …)` means the source changed since
the last build). With `--host` it also reads each guest's `INSTALLED.json` and
reports `matches CURRENT`, `DIFFERS from CURRENT`, `not installed` or
`unreachable`.

### `install`: reinstall onto running guests

`mqlab component install <name> --host <guest>` resolves an artifact (HEAD's,
building it first if needed, or with `--version` an already staged
`<version>+<tree>` or a version that names exactly one staged artifact) and runs
`ansible/component-install.yml`, the same `component-install` role the bake uses.
`--host` is required and repeatable; the play refuses to run fleet-wide and refuses
a host the inventory does not have.

On each guest the role:

1. checks the guest runtime's `PIN` against the pin, **before any change**. A
   mismatch refuses with `rebuild its box (mqlab box build <box>)`: the install
   never replaces a running guest's interpreter;
2. builds a new release under `releases/` with the pinned interpreter, offline and
   hash-pinned (pymqi compiles here with `gcc` against `/opt/mqm`);
3. runs `<name>-selfcheck` **in place**. A failure removes the release; nothing
   goes live;
4. flips `venv` to the new release atomically (`ln -s` + `mv -T`) and points
   `previous` at the old one;
5. runs the selfcheck again **through `venv/bin`**. A failure flips `venv` back to
   `previous` and fails the play;
6. prunes to the live and previous releases, writes `INSTALLED.json`, installs the
   static units, reloads systemd, and restarts the component's units that are
   **enabled** on that guest.

A failure at any step exits non-zero with the Ansible output above it.

## Rolling back by hand

`previous` always points at the release that was live before the last install.
To roll back, flip `venv` to it and restart the component's units:

```bash
cd /opt/logical-minds-foundry/<name>
sudo ln -s "$(readlink previous)" venv.tmp && sudo mv -T venv.tmp venv
sudo systemctl restart <unit>...
```

`INSTALLED.json` still describes the newer release after a hand rollback;
`readlink venv` is the truth. To make the rollback permanent, rebuild the earlier
commit and reinstall it (`mqlab component install <name> --host <guest> --version
<version>+<tree>`).

The layout is versioned releases plus a symlink flip because a Python venv is not
relocatable: pip writes absolute shebangs into console scripts. The first design
built `venv.new` and renamed it to `venv`, which left every entry point pointing
at `venv.new`. The first cold rebuild caught it
([#1357](https://github.com/logical-minds-foundry/mq-resiliency-lab-for-linux/issues/1357)),
and [#1372](https://github.com/logical-minds-foundry/mq-resiliency-lab-for-linux/issues/1372)
replaced it with this layout.
