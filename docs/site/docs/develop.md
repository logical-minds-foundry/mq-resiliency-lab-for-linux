# Develop the lab

Develop the lab itself — the Vergil-based developer workflow used to build and
maintain it (as distinct from *running* the lab as a consumer, which
[Getting Started](getting-started.md) covers).

!!! note "In progress"
    This is a placeholder stub. The developer-workflow content is filled in
    under epic
    [logical-minds-foundry/.github#161](https://github.com/logical-minds-foundry/.github/issues/161).

Until then, the in-repo developer notes are the reference:

- **Run `mqlab` with `uv run mqlab …` from a dev checkout.** `uv run` syncs the
  environment and puts the venv's `bin/` on `PATH`, so the tools `mqlab` calls by
  bare name (`ansible-playbook` and the rest) resolve. Calling the venv's `mqlab`
  by its path skips that and fails mid-run. A release install activates the venv
  instead ([Getting Started](getting-started.md#3-set-up-and-pre-flight-the-host)).
  See
  [operating the lab from a dev session](https://github.com/logical-minds-foundry/mq-resiliency-lab-for-linux/blob/develop/docs/development/operating-the-lab-from-a-dev-session.md).
- **[Perf reports and bootstrap staging](https://github.com/logical-minds-foundry/mq-resiliency-lab-for-linux/blob/develop/docs/development/perf-and-staging.md)**:
  comparing a macOS run with an x86 cloud run of the same commit, tuning one
  lever at a time, and the measured results.
- **[Box model](https://github.com/logical-minds-foundry/mq-resiliency-lab-for-linux/blob/develop/docs/development/box-model.md)**
  and **[box bake manifest](https://github.com/logical-minds-foundry/mq-resiliency-lab-for-linux/blob/develop/docs/development/box-bake-manifest.md)**:
  how the per-role boxes are baked, kept registered, and rebuilt.
- **[`build/` layout](https://github.com/logical-minds-foundry/mq-resiliency-lab-for-linux/blob/develop/docs/development/build-layout.md)**:
  the `build/` buckets and the cloud VM's boot disk.
- **Ansible roles read facts only as `ansible_facts['<fact>']`.** Write
  `ansible_facts['os_family']`, never the top-level `ansible_os_family`.
  [`ansible/ansible.cfg`](https://github.com/logical-minds-foundry/mq-resiliency-lab-for-linux/blob/develop/ansible/ansible.cfg)
  sets `inject_facts_as_vars = False`, so a top-level fact name is undefined at
  runtime. ansible-core 2.20 deprecates the old `True` default, and 2.24 makes
  `False` the default. Setting it now means a missed reference fails today
  instead of on the 2.24 upgrade (#136). The guard test
  [`tests/test_ansible_facts_guard.py`](https://github.com/logical-minds-foundry/mq-resiliency-lab-for-linux/blob/develop/tests/test_ansible_facts_guard.py)
  fails on any `ansible_*` name under `ansible/` that is not `ansible_facts` or
  an allowlisted connection or magic variable (`ansible_host`, `ansible_user`,
  and so on). If you need a new one of those, add it to the test's allowlist.
  The
  [Ansible dependency currency](https://github.com/logical-minds-foundry/mq-resiliency-lab-for-linux/blob/develop/docs/reference/ansible-dependency-currency.md)
  report says when the `ansible.cfg` line can go.
- **Docs toolchain.** The site builds with the `docs` dependency group in
  `pyproject.toml` (`uv sync --group docs`), which pins `mkdocs>=1.6,<2`.
  MkDocs 2.0 breaks plugins and theme overrides. The MkDocs 2.0 notice printed
  during the build is turned off upstream in the shared docs pipelines
  (`NO_MKDOCS_2_WARNING=1`, vergil-actions v2.1.43).
