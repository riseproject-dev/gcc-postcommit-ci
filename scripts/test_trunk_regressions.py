"""Test which observations justify the new-regressions label on trunk issues."""

from contextlib import contextmanager
from io import StringIO
from pathlib import Path
import tempfile
import unittest

from trunk_regressions import separate_snapshot_rows, update_report
from trunk_sum_artifacts import EvidenceUnavailable


BASE = "a" * 40
CURRENT = "b" * 40
TARGET = "gcc-linux-rv64gc-lp64d"
GROUP = "rv64gc lp64d medlow"
BOARD = "riscv-sim/-march=rv64gc/-mabi=lp64d/-mcmodel=medlow"
CASE = "gcc.dg/atomic/c11-atomic-exec-4.c   -O0  execution test"


def raw_sum(*results, board=BOARD, driver="gcc.dg/dg.exp", triple="riscv64-linux-gnu"):
    return (
        f"Target is {triple}\nRunning target {board}\nRunning /work/gcc/testsuite/{driver} ...\n"
        + "\n".join(results)
        + "\n"
    )


class FixtureArtifact:
    def __init__(self, path, tools):
        self.url = f"https://example.org/{path.name}"
        self.tools = tools

    def open_tool(self, tool):
        if tool not in self.tools:
            raise EvidenceUnavailable(f"missing {tool}.sum")
        return StringIO(self.tools[tool])


class TrunkRegressionTests(unittest.TestCase):
    def setUp(self):
        temp = tempfile.TemporaryDirectory()
        self.addCleanup(temp.cleanup)
        self.root = Path(temp.name)
        for name in ("current_logs", "previous_logs", "summaries"):
            (self.root / name).mkdir()
        self.sums = {}

    def pair(self, *candidates, before=None, after=None, target=TARGET):
        def report(directory, revision):
            path = (
                self.root / directory / f"{target}-{revision}-non-multilib-report.log"
            )
            path.touch()
            return path

        baseline = report("previous_logs", BASE)
        current = report("current_logs", CURRENT)
        self.sums[baseline] = {"gcc": before} if before is not None else {}
        self.sums[current] = {"gcc": after} if after is not None else {}
        summary = self.root / "summaries" / (current.stem + "-summary.md")
        summary.write_text(
            f"---\ntitle: {BASE}->{CURRENT}\n---\n"
            + "# New Failures\n"
            + (f"## {GROUP}\n### gcc failures\n" if candidates else "")
            + "\n".join(candidates)
            + "\n"
        )
        return baseline, current, summary

    @contextmanager
    def load(self, repo, path):
        yield FixtureArtifact(path, self.sums[path])

    def render(self, labels="new-regressions"):
        label_file = self.root / "labels.txt"
        issue = self.root / "issue.md"
        label_file.write_text(labels)
        issue.write_text("|New Failures|gcc|g++|gfortran|Previous Hash|\n")
        update_report(
            self.root, CURRENT, "riseproject-dev/gcc-postcommit-ci", self.load
        )
        return set(filter(None, label_file.read_text().split(","))), issue.read_text()

    def test_known_failures_retain_usable_baseline(self):
        previous, current, _ = self.pair()
        self.sums.pop(previous)
        self.sums.pop(current)
        labels, _ = self.render("valid-baseline,new-regressions")
        self.assertEqual(labels, {"valid-baseline"})

    def test_self_comparison_is_a_snapshot(self):
        header = "|Unresolved Failures|gcc|g++|gfortran|Previous Hash|\n"
        old = f"|linux|3/1|0/0|0/0|[{BASE}](compare)|"
        snapshot = f"|newlib|2/1|0/0|0/0|[{CURRENT}-no-baseline](compare)|"
        body = header + "|---|---|---|---|---|\n" + old + "\n" + snapshot + "\n\nMore\n"
        known, fresh = separate_snapshot_rows(body, CURRENT).split(
            "|Current failures without a distinct baseline"
        )
        self.assertIn(old, known)
        self.assertNotIn("newlib", known)
        self.assertIn("|newlib|2/1|0/0|0/0|\n\nMore", fresh)

    def test_timeout_has_paired_evidence_even_with_other_build_failures(self):
        self.pair(
            "FAIL: " + CASE,
            before=raw_sum("PASS: " + CASE),
            after=raw_sum(f"WARNING: {CASE} program timed out.", "FAIL: " + CASE),
        )
        labels, issue = self.render("build-failure")
        self.assertEqual(labels, {"new-regressions", "build-failure"})
        self.assertIn(CASE, issue)
        self.assertIn(BASE[:12], issue)
        self.assertIn(CURRENT[:12], issue)
        self.assertIn("1 timed out", issue)
        self.assertIn("Coverage is incomplete", issue)
        self.assertIn("[baseline sums]", issue)
        self.assertIn("[current sums]", issue)

    def test_unmatched_cases_and_non_pass_do_not_confirm_a_new_failure(self):
        # Reordering the board options preserves the filtered group name but
        # changes the raw board identity used to pair the two outcomes.
        other_board = "riscv-sim/-mabi=lp64d/-march=rv64gc/-mcmodel=medlow"
        baselines = {
            "different option": raw_sum("PASS: " + CASE.replace("-O0", "-O2")),
            "different subtest": raw_sum(
                "PASS: " + CASE.replace("execution test", "compilation")
            ),
            "different board": raw_sum("PASS: " + CASE, board=other_board),
            "different triple": raw_sum("PASS: " + CASE, triple="riscv64-unknown-elf"),
            "different driver": raw_sum("PASS: " + CASE, driver="gcc.dg/other.exp"),
            "already failing": raw_sum("FAIL: " + CASE),
            "never ran": raw_sum("UNSUPPORTED: " + CASE),
        }
        for description, previous in baselines.items():
            with self.subTest(description):
                self.pair(
                    "FAIL: " + CASE, before=previous, after=raw_sum("FAIL: " + CASE)
                )
                labels, issue = self.render()
                self.assertNotIn("new-regressions", labels)
                self.assertIn(CASE, issue.partition("## Results needing review")[2])

    def test_ambiguous_repeated_results_only_confirm_necessary_transitions(self):
        fail = "FAIL: " + CASE
        passed = "PASS: " + CASE
        self.pair(
            fail,
            fail,
            before=raw_sum(fail, passed, passed),
            after=raw_sum(fail, fail, passed),
        )
        labels, issue = self.render()
        self.assertIn("new-regressions", labels)
        self.assertIn("PASS → FAIL × 1", issue)
        self.assertIn("FAIL × 1", issue.partition("## Results needing review")[2])

        self.pair(fail, before=raw_sum(passed), after=raw_sum(passed, fail))
        labels, issue = self.render()
        self.assertNotIn("new-regressions", labels)
        self.assertIn(CASE, issue.partition("## Results needing review")[2])

    def test_xpass_is_retained_for_review(self):
        self.pair(
            "XPASS: " + CASE,
            before=raw_sum("XFAIL: " + CASE),
            after=raw_sum("XPASS: " + CASE),
        )
        labels, issue = self.render()
        self.assertNotIn("new-regressions", labels)
        self.assertIn("XPASS", issue.partition("## Results needing review")[2])

    def test_comparison_requires_the_right_revisions_and_both_reports(self):
        for problem in (
            "synthetic",
            "same hash",
            "wrong current",
            "missing previous",
            "missing summary",
        ):
            with self.subTest(problem):
                previous, _, summary = self.pair(
                    "FAIL: " + CASE,
                    before=raw_sum("PASS: " + CASE),
                    after=raw_sum("FAIL: " + CASE),
                )
                if problem == "missing previous":
                    previous.unlink()
                elif problem == "missing summary":
                    summary.unlink()
                else:
                    title = {
                        "synthetic": f"{CURRENT}-no-baseline->{CURRENT}",
                        "same hash": f"{CURRENT}->{CURRENT}",
                        "wrong current": f"{BASE}->{'c' * 40}",
                    }[problem]
                    summary.write_text(
                        summary.read_text().replace(f"{BASE}->{CURRENT}", title)
                    )
                labels, issue = self.render()
                self.assertNotIn("new-regressions", labels)
                self.assertIn("## Results needing review", issue)

    def test_missing_sum_leaves_other_verified_observations_visible(self):
        self.pair(
            "FAIL: " + CASE,
            before=raw_sum("PASS: " + CASE),
            after=raw_sum("FAIL: " + CASE),
        )
        self.pair(
            "FAIL: " + CASE,
            after=raw_sum("FAIL: " + CASE),
            target="gcc-newlib-rv64gc-lp64d",
        )
        labels, issue = self.render()
        self.assertIn("new-regressions", labels)
        self.assertIn("PASS → FAIL × 1", issue)
        self.assertIn(
            "missing gcc.sum", issue.partition("## Results needing review")[2]
        )


if __name__ == "__main__":
    unittest.main()
