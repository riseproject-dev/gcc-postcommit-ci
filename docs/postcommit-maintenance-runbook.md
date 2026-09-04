# Post-Commit Maintenance Runbook

This runbook captures the human triage work that accompanies the automated
RISE GCC post-commit service. The workflows create status issues and artifacts;
they do not replace maintainer investigation or upstream bug reporting.

## Daily Queue

Start with the open
[`new-regressions` queue](https://github.com/riseproject-dev/gcc-postcommit-ci/issues?q=is%3Aissue+is%3Aopen+label%3Anew-regressions),
then review open issues labelled `build-failure`, `testsuite-failure`, or
`resolved-regressions`. If there are no new failures, no triage action is
required.

Each `Testsuite Status <gcc-hash>` issue must link to its Actions run. Use the
run's `*-current-logs`, `*-previous-logs`, report, summary, and build-log
artifacts when reproducing a result. Artifacts are retained for a limited time,
so preserve the relevant excerpt in the upstream bug before it expires.

## New Regression Triage

1. Re-run or reproduce the failure on current GCC trunk. Do not report a bug
   solely from an old status issue.
2. Confirm the target, libc, ABI, multilib mode, simulator, GCC hash, and exact
   command line. Record whether the failure is deterministic.
3. Classify the failure:
   - For a scan-dump mismatch, first determine whether the test expectation
     needs updating or code generation changed incorrectly.
   - For `test for excess errors`, inspect the full testsuite log for the first
     compiler or assembler diagnostic; the summary line is not the root cause.
   - For an abort or runtime crash, build the compiler locally and debug the
     failing command under GDB when a smaller reproducer is not available.
   - For a GCC internal compiler error, reproduce with `-freport-bug` and attach
     the generated preprocessed source and environment details.
4. Minimize the reproducer where practical and search
   [GCC Bugzilla](https://gcc.gnu.org/bugzilla/) before filing a new bug.
5. Link the upstream bug from the RISE status issue. Keep the issue open while
   the regression remains on trunk.

Do not add a deterministic compiler regression to an allowlist merely to make
the dashboard green.

## Resolved Regressions

For each `resolved-regressions` entry, search the RISE issue history using the
exact testsuite failure text. Close the earliest originating status issue only
after confirming the failure no longer exists and that the issue does not
contain another unresolved regression. Add the fixing commit or upstream bug
link when known. Close the current issue when it has no remaining actionable
failure.

## Infrastructure Failures

Treat clone timeouts, artifact expiry, runner loss, disk exhaustion, simulator
timeouts, and service rate limits separately from compiler regressions. Check
the complete workflow logs before retrying. Repeated infrastructure failures
need an issue with the runner label, run URL, frequency, and owner; they must not
silently become a compiler allowlist entry.

Self-hosted jobs are allowed to erase their GitHub Actions workspace. They must
therefore run only on ephemeral or dedicated RISE runners carrying all of these
labels: `self-hosted`, `linux`, `x64`, and `rise-gcc-ci` (plus `ping` for the
Patchwork polling runner). Never attach the generic label set to a shared
organization runner.

## Flaky Tests And Allowlists

An allowlist entry must include the observed symptom, affected target scope,
an upstream bug or RISE issue, and why the failure is considered flaky or
external. Use the narrowest matching file under `test/allowlist/`.

Post-commit and pre-commit carry separate allowlists. Every applicable change
must be made in both `riseproject-dev/gcc-postcommit-ci` and
`riseproject-dev/gcc-precommit-ci`, then validated against at least one clean
and one affected target.

## Toolchain And Cache Updates

Toolchain changes are published in dependency order:

1. Merge and push `riseproject-dev/riscv-gnu-toolchain-ci@build-frequent`.
2. Update the post-commit gitlink and `.gitmodules`, then run a manual build.
3. Update pre-commit only after post-commit has produced a valid baseline.

When source submodule revisions change, bump the numbered cache key everywhere
it is consumed in the affected repository. Search for `submodules-archive-`
and verify all restore and save sites use the same new value. A partial cache
key bump can mix source revisions across jobs.

## Dashboard Recovery

The dashboard ingests only the strict trunk and weekly status title formats
declared in `dashboard/getdata.py`; coordination, release, binutils, ordinary,
and malformed issues are excluded. Missing or expired artifacts are skipped and
reported in the deployment log.
For a clean rebuild, manually dispatch `Deploy-Dashboard` with `bootstrap=true`
after at least one full RISE post-commit run has produced retained artifacts.
Verify the generated CSV timestamps and all Pages links before relying on the
graph as a service-health signal.

The dashboard job persists generated data by committing to `main`. The RISE
ruleset must either grant the GitHub Actions app a narrowly reviewed bypass or
the persistence design must be moved to a dedicated data branch. Do not weaken
the general pull-request requirement for human changes.

## Required Labels

Create these labels before enabling schedules:

- `new-regressions`, `resolved-regressions`, `build-failure`,
  `testsuite-failure`, `invalid`, `build-warnings`, and `valid-baseline`;
- `staging`, `bisect`, `release`, `coord`, `binutils`, and `checking` where the
  corresponding workflows remain enabled.

Baseline consumers accept only a strict `Testsuite Status <40-hex>` issue with
the `valid-baseline` label. Staging, invalid, bisect, build-failure, and
testsuite-failure issues must never be selected.

## Fuzzer Handover Gap

The compiler fuzzer, Csmith installation, daily GCC/LLVM builds, result storage,
deduplication, reduction, and bisection scripts described in the former
maintainer notes are not present in these three repositories. Track that as a
separate handover item. Obtain the repository, runner registration, storage
location, schedule, compiler paths, service credentials, and retention policy
before declaring the full CI service transferred.
