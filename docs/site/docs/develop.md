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
