# RISE Post-Commit CI Deployment

This repository owns scheduled GCC RISC-V post-commit builds, baseline status
issues, comparison artifacts, and the public dashboard consumed by pre-commit
CI.

## Architecture

The service consumes `riseproject-dev/riscv-gnu-toolchain-ci` as the
`riscv-gnu-toolchain` submodule on `build-frequent`. Scheduled and manual
workflows build GCC, upload target artifacts, generate status issues in this
repository, and publish dashboard pages at:

`https://riseproject-dev.github.io/gcc-postcommit-ci/`

Pre-commit CI reads status issues and artifacts from this repository as its
baseline source.

## Repository Settings

| Setting | Required value |
| --- | --- |
| Actions | Enabled for all workflows |
| Default branch | `main` |
| Workflow permissions | Read repository contents by default; workflows that create issues, upload Pages artifacts, or write dashboard commits request explicit permissions |
| Issues | Enabled |
| Pages | GitHub Actions source, published from the `github-pages` environment |
| Environments | `production`, `github-pages` |
| Branch protection | Require PR validation and migration guard before merging to `main` |

## Secrets And Variables

| Name | Scope | Minimum permission | Purpose | Required for |
| --- | --- | --- | --- | --- |
| `GITHUB_TOKEN` | Repository default | Workflow-declared permissions | Issue updates, artifact reads, Pages publish | Shadow and production |
| `GIST_TOKEN` | RISE service account | Create secret gists only | Optional long issue body overflow storage | Production if gist overflow is retained |
| `RISE_CI_READ_TOKEN` | Organization or repository | Read Actions artifacts and issues in RISE CI repositories | Reserved for cross-repository consumers; not needed for same-repository post-commit reads | Pre-commit integration |
| `PATCHWORK_API` | RISE service account | Patchwork check write | Not used by post-commit; documented for full service cutover | Pre-commit production |
| `PATCHWORK_REPORTING_ENABLED` | Repository variable | N/A | Not used by post-commit | Pre-commit production |

## Runner Inventory

| Workflow/job | Labels | Purpose | Architecture | Required software | Concurrency | Scope |
| --- | --- | --- | --- | --- | --- | --- |
| `build-test.yaml` build/test self-hosted jobs | `self-hosted`, `linux`, `x64`, `rise-gcc-ci` | Long multilib GCC builds and tests | Linux x64 | GCC build deps, DejaGnu, QEMU, Python, zip/unzip | Multiple jobs per scheduled run | Dedicated RISE runner group |
| `rvv-intrinsic-test.yaml` | `self-hosted`, `linux`, `x64`, `rise-gcc-ci` | RVV intrinsic validation | Linux x64 | RVV intrinsic dependencies, compiler toolchain | Low | Dedicated RISE runner group |

GitHub-hosted jobs continue to use `ubuntu-24.04`.

## Bootstrap

1. Merge the RISE toolchain submodule update first.
2. Confirm self-hosted RISE runners are online with the labels above.
3. Create the labels listed in `postcommit-maintenance-runbook.md`.
4. Configure `production` and `github-pages` environments.
5. Resolve the dashboard bot's `main` ruleset bypass or use a dedicated data
   branch; do not leave dashboard persistence blocked by branch protection.
6. Run a manual post-commit workflow on `main`.
7. Confirm artifacts upload to this repository.
8. Confirm a `Testsuite Status <hash>` issue is created with `valid-baseline`.
9. Run `Deploy-Dashboard` and confirm Pages publishes under the RISE URL.
10. Only then point pre-commit baseline reads at this repository.

For an empty repository bootstrap, use a manual post-commit run to create the
first valid baseline issue. Do not configure a permanent fallback to any
non-RISE repository.

## Cutover

1. Merge `riseproject-dev/riscv-gnu-toolchain-ci`.
2. Fetch the current RISE `main` immediately before preparing the post-commit
   change. The live dashboard continuously adds generated-data commits; replay
   the migration commits on top of that current head and never force-push or
   replace the dashboard history.
3. Merge this repository with the updated submodule gitlink.
4. Run one full post-commit workflow and dashboard deployment.
5. Record the first valid baseline issue and artifact run.
6. Merge pre-commit CI only after the baseline exists.
7. Disable legacy schedules after RISE workflows have produced valid artifacts,
   issues, and dashboard pages.

Before enabling production schedules, update the repository description and
homepage, make the RISE repository the local `origin`, verify the RISE
maintainer team/rulesets, and export the old repository's labels, environments,
Actions settings, Pages configuration, webhooks, Apps, deploy keys, and runner
registrations. Git pushes do not migrate that service state.

## Governance Handoff

This repository does not currently contain a top-level license file. Before the
RISE repository is presented as the authoritative project, confirm the code's
provenance and redistribution terms with the former maintainers and add the
approved license; changing repository URLs does not itself transfer copyright.

Add `CODEOWNERS` only after the exact RISE maintainer team slug is confirmed,
and grant ownership to that team rather than to an individual account. Record
the primary and backup service owners, incident contact, and token-rotation
owner in the RISE operations system.

## Rollback

1. Disable post-commit schedules in this repository.
2. Re-enable the previous production schedules only if required to keep service
   continuity.
3. Revert the latest post-commit migration commit on `main`.
4. Keep RISE status issues and artifacts for audit; do not rewrite history or
   delete remote records.
5. Re-run the migration guard before attempting cutover again.

## Release Policy

Maintained release workflows include `release_15_` and `release_16_` artifact
prefixes and use `release-15` and `release-16` branch inputs. The older retired
release line is no longer scheduled, listed in workflow choices, or consumed by
dashboard ingestion.
