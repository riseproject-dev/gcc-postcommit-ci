"""Safety invariants for the temporary, branch-only Hetzner pilot."""

import unittest
from pathlib import Path

import yaml


ROOT = Path(__file__).resolve().parents[1]
WORKFLOW = ROOT / ".github/workflows/pilot-hetzner07.yaml"


class PilotWorkflowTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.text = WORKFLOW.read_text()
        cls.workflow = yaml.load(cls.text, Loader=yaml.BaseLoader)
        cls.job = cls.workflow["jobs"]["newlib-rv64gcv-pilot"]

    def test_only_pilot_branch_pushes_trigger(self):
        self.assertEqual(set(self.workflow["on"]), {"push"})
        self.assertEqual(
            self.workflow["on"]["push"]["branches"], ["pilot/hetzner-07-20261030"]
        )
        self.assertIn("github.event_name == 'push'", self.job["if"])

    def test_read_only_token_and_no_production_environment(self):
        self.assertEqual(
            self.workflow["permissions"], {"contents": "read", "actions": "read"}
        )
        self.assertNotIn("environment", self.job)
        self.assertNotIn("secrets.", self.text)

    def test_only_pilot_runner_can_receive_job(self):
        self.assertEqual(
            self.job["runs-on"], ["self-hosted", "linux", "x64", "rise-gcc-ci-pilot-07"]
        )
        self.assertNotIn("rise-gcc-ci", self.job["runs-on"])
        self.assertEqual(self.workflow["concurrency"]["cancel-in-progress"], "false")

    def test_actions_and_sources_are_pinned(self):
        for step in self.job["steps"]:
            if "uses" in step:
                self.assertRegex(step["uses"], r"^actions/[a-z-]+@[0-9a-f]{40}$")
        for key in ("GCC_SHA", "TOOLCHAIN_SHA", "SOURCE_CI_SHA"):
            self.assertRegex(self.workflow["env"][key], r"^[0-9a-f]{40}$")

    def test_no_install_sudo_or_production_reporting(self):
        self.assertNotRegex(self.text, r"sudo\s+(?:apt|pip|rm|bash|sh|\./)")
        self.assertNotIn("generate-summary", self.text)
        self.assertNotIn("gh issue", self.text)
        self.assertNotIn("git push", self.text)
        checkout = next(
            s
            for s in self.job["steps"]
            if s.get("uses", "").startswith("actions/checkout@")
        )
        self.assertEqual(checkout["with"]["persist-credentials"], "false")

    def test_tests_preserve_make_exit_and_upload_on_failure(self):
        tests = next(s for s in self.job["steps"] if s.get("id") == "tests")
        self.assertNotIn("|| true", tests["run"])
        self.assertIn('exit "${pipeline_status[0]}"', tests["run"])
        self.assertIn("stamps/check-gcc-newlib", tests["run"])
        upload = self.job["steps"][-1]
        self.assertEqual(upload["if"], "always()")
        self.assertEqual(upload["with"]["retention-days"], "7")


if __name__ == "__main__":
    unittest.main()
