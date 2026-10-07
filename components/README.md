# Guest components

Everything the lab runs **on its guests** that we write ourselves lives here. Each
component is a **standalone, standard Python project** (the C++ or other-language
form follows the same contract later). It is built and tested on the lab's one
pinned runtime, then installed onto guests from source with standard tooling. Design:
epic [logical-minds-foundry/.github#294](https://github.com/logical-minds-foundry/.github/issues/294),
spec `epics/294-guest-component-packaging/spec.md` in `logical-minds-foundry/.github`.

## Two quality tiers

| | `mqlab` (repo root) | `components/<name>/` |
|---|---|---|
| Bar | full Vergil: `vrg-validate`, container, 100% branch coverage | a standard Python project: `pyproject.toml`, `src/` layout, unit tests, its own `uv.lock` |
| Driven by | Vergil | plain `uv`: no container, no `vergil.toml` |
| Knows about the other | yes: `mqlab` builds, tests and installs components | **no**: a component knows nothing about Vergil or the lab |

A component becomes fully Vergil-managed only when it is **extracted** into its own
repository. Its directory name is that future repository's name, so extraction means
moving the directory.

`vrg-validate` never judges this directory. Root ruff and ansible-lint exclude it,
and root pytest and coverage cover only `tests/` and `src/`. The boundary is a tested
invariant, enforced by `tests/test_component_boundary.py`: mqlab never imports a
component, a component never imports mqlab, and the mqlab wheel never ships a
component.

## Layout

```text
components/<name>/
  pyproject.toml   name = <name>, own version, requires-python = "==3.14.*",
                   hatchling backend, [project.scripts] entry points,
                   own [tool.ruff] / [tool.pytest] config
  uv.lock          this component's lock only (incl. an optional `sdist-build`
                   dependency group: the build requirements of any sdist
                   dependency, e.g. setuptools + wheel for pymqi)
  src/<import_pkg>/
  tests/
  systemd/         static unit files (no templating)
  README.md        purpose, entry points, config keys
```

## Rules

1. **Never import the lab or Vergil.** No `mqlab` imports, no sibling-file imports,
   no `sys.path` manipulation.
2. **Every runnable is a console-script entry point.** Units, playbooks and operators
   call `/opt/logical-minds-foundry/<name>/venv/bin/<entry-point>`. Nothing runs
   `python file.py`.
3. **Units belong to the component; configuration belongs to the deployer.** Unit files
   are static and install to `/usr/lib/systemd/system/`. Site values come from
   `EnvironmentFile=/etc/opt/logical-minds-foundry/<name>/<unit>.env`, rendered by
   Ansible.
4. **Ship `<name>-selfcheck`.** It imports every module, asserts CPython 3.14, and
   reports versions via `importlib.metadata` (never a module's `__version__`; pymqi's
   is stale). The install runs it on the box before going live.
5. **The install records itself** in `/opt/logical-minds-foundry/<name>/INSTALLED.json`
   (version, tree, release id, runtime).
6. **Operator-facing names stay stable**: unit names, entry points, textfile paths.

## The install layout and rollback

A Python venv is **not relocatable**: pip writes absolute shebangs into console scripts.
So every install is a **release** that is built at its final path and never moved. Going
live is an atomic flip of a symlink:

```text
/opt/logical-minds-foundry/<name>/releases/<version>+<tree>-<UTC stamp>/venv   built here, never moved
/opt/logical-minds-foundry/<name>/venv     -> releases/<live>/venv                what the units run
/opt/logical-minds-foundry/<name>/previous -> releases/<prev>/venv                the rollback target
```

The install (Ansible role `component-install`, used by the bake and by
`mqlab component install`) works in this order:

1. Build the release.
2. Selfcheck it **in place**. A failure removes it, and nothing goes live.
3. Flip `venv` with `ln -s` + `mv -T` and record `previous`.
4. Selfcheck again **through `venv/bin`**. A failure flips back to `previous`.
5. Prune to the live and previous releases.

**Rollback by hand:** run `ln -s "$(readlink previous)" venv.tmp && mv -T venv.tmp venv`
in the component's directory, then restart its units. (Design: #1372, from the V1 #1357
finding.) The operator guide, with the `mqlab component` verbs, is
[`docs/site/docs/operate/guest-components.md`](../docs/site/docs/operate/guest-components.md).

## The runtime

The one guest runtime is pinned in `lab/versions.yaml` under `runtime.python`. It is a
python-build-standalone CPython `install_only` build, keyed by exact version, release
and per-arch sha256, and installed on guests at `/opt/vergil/cpython-<minor>/`. It is
proven on Ubuntu 24.04 (both arches) and RHEL 9.6 in
`docs/reports/2026-10-guest-runtime-spike.md`.

## Adding a component

1. Create `components/<name>/` with the layout above (`uv init --package`, then trim).
2. Write `uv.lock` with `uv --directory components/<name> lock`.
3. List it under `roles.<role>.components` in `lab/versions.yaml` for every box role
   that bakes it. The catalog refuses a name with no `components/<name>/pyproject.toml`.
4. Build it with `mqlab component build <name>`. This runs its tests on the pinned
   runtime and stages the installable artifact.
