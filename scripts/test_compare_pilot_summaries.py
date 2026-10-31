import tempfile
import unittest
from pathlib import Path

from compare_pilot_summaries import compare, read_summary


class PilotSummaryComparisonTests(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.addCleanup(self.tmp.cleanup)
        self.root = Path(self.tmp.name)
        self.old = self.root / "old"
        self.new = self.root / "new"
        self.old.mkdir()
        self.new.mkdir()
        self.write(self.old)
        self.write(self.new)

    def write(
        self,
        directory,
        extra="",
        passes=2,
        target="riscv-sim/-march=rv64gcv/-mabi=lp64d",
    ):
        for tool in ("gcc", "g++"):
            results = "".join(f"PASS: case-{n}\n" for n in range(passes))
            (directory / f"{tool}.sum").write_text(
                f"Schedule of variations:\n    {target}\n\nRunning target {target}\n"
                f"{results}FAIL: known-failure\n{extra}"
                f"\t\t=== {tool} Summary for {target} ===\n"
                f"\t\t=== {tool} Summary ===\n# of expected passes\t{passes}\n"
            )

    def test_identical_known_failures_are_retained_and_accepted(self):
        report = compare(self.old, self.new)
        self.assertTrue(report["accepted"])
        self.assertEqual(report["targets"][0]["current_counts"]["FAIL"], 1)

    def test_new_failures_unresolved_errors_and_duplicate_failures_are_rejected(self):
        for extra in (
            "FAIL: new-case\n",
            "UNRESOLVED: new-case\n",
            "ERROR: new-case\n",
            "FAIL: known-failure\n",
        ):
            with self.subTest(extra=extra):
                self.write(self.new, extra)
                self.assertFalse(compare(self.old, self.new)["accepted"])

    def test_lost_pass_coverage_is_rejected(self):
        self.write(self.new, passes=1)
        self.assertFalse(compare(self.old, self.new)["accepted"])

    def test_only_workspace_relocation_is_normalized(self):
        suffix = "riscv-gnu-toolchain/build/bin/nm: plugin needed\n"
        self.write(
            self.old,
            "ERROR: case /home/test/runner/_work/gcc-postcommit-ci/gcc-postcommit-ci/"
            + suffix,
        )
        self.write(
            self.new,
            "ERROR: case /var/lib/test/runner/_work/gcc-postcommit-ci/gcc-postcommit-ci/"
            + suffix,
        )
        self.assertTrue(compare(self.old, self.new)["accepted"])
        self.write(
            self.new,
            "ERROR: case /var/lib/test/runner/_work/gcc-postcommit-ci/gcc-postcommit-ci/"
            + suffix.replace("plugin needed", "different error"),
        )
        self.assertFalse(compare(self.old, self.new)["accepted"])

    def test_changed_target_set_is_rejected(self):
        self.write(self.new, target="riscv-sim/-march=rv32gcv/-mabi=ilp32d")
        with self.assertRaisesRegex(ValueError, "target sets differ"):
            compare(self.old, self.new)

    def test_truncated_or_empty_results_are_rejected(self):
        path = self.new / "gcc.sum"
        original = path.read_text()
        for text in (
            "",
            original.split("=== gcc Summary ===")[0],
            original.replace("# of expected passes\t2", "# of expected passes\t99"),
        ):
            with self.subTest(text=text):
                path.write_text(text)
                with self.assertRaises(ValueError):
                    read_summary(path)


if __name__ == "__main__":
    unittest.main()
