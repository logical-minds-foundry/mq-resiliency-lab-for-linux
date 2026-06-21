# Release runbook

Releases are published by `.github/workflows/release.yml` on a `vX.Y.Z` tag push.

## One-time setup

1. Generate the dedicated release signing subkey (see commands in the
   signed-tarball plan, Task 5 Step 1) and record its full fingerprint.
2. Store `RELEASE_GPG_PRIVATE_KEY` and `RELEASE_GPG_PASSPHRASE` as GitHub Actions
   secrets. Commit only the public `RELEASE-KEY.asc`.
3. Publish the fingerprint in the README and upload the public key to a keyserver.

## Cutting a release

1. Bump `version` in `pyproject.toml` and the `VERSION` file (same value), commit.
2. Tag and push: `git tag vX.Y.Z && git push origin vX.Y.Z`
   (the human runs raw git via `! …`; the version guard fails the job if the tag,
   pyproject, and VERSION disagree).
3. The workflow archives, checksums, signs, and publishes the Release.

## Consumer verification (document in README)

The trust root is the fingerprint `5BABD50A78EBF24D2410AE52ADF1A99B75D24E54` + an
out-of-band key — never the copy inside the tarball. See README "Getting Started →
verify".
