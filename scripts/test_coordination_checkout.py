"""Exercise the action against a mirror without vendor refs and local upstream."""

import os
from pathlib import Path
import subprocess
import tempfile
import unittest

import yaml


ACTION = (
    Path(__file__).resolve().parents[1]
    / ".github/actions/common/init-and-pull-gcc/action.yaml"
)
VENDOR_REF = "refs/vendors/riscv/heads/gcc-15-with-riscv-opts"
TRACKING_REF = "refs/remotes/upstream/vendors/riscv/heads/gcc-15-with-riscv-opts"


class CoordinationCheckoutTests(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.addCleanup(self.tmp.cleanup)
        self.root = Path(self.tmp.name)
        self.upstream = self.root / "upstream"
        self.mirror = self.root / "mirror"
        self.checkout = self.root / "gcc"
        self.env = dict(
            os.environ,
            GIT_CONFIG_NOSYSTEM="1",
            GIT_CONFIG_GLOBAL=os.devnull,
            GIT_TERMINAL_PROMPT="0",
            GIT_AUTHOR_NAME="CI test",
            GIT_AUTHOR_EMAIL="ci@example.invalid",
            GIT_COMMITTER_NAME="CI test",
            GIT_COMMITTER_EMAIL="ci@example.invalid",
        )
        self.git("init", "--initial-branch=master", str(self.upstream))
        self.git("-C", str(self.upstream), "commit", "--allow-empty", "-m", "master")
        self.git("clone", "--bare", str(self.upstream), str(self.mirror))
        self.git("clone", str(self.mirror), str(self.checkout))
        self.git("-C", str(self.upstream), "commit", "--allow-empty", "-m", "vendor")
        self.tip = self.git("-C", str(self.upstream), "rev-parse", "HEAD")
        self.git("-C", str(self.upstream), "update-ref", VENDOR_REF, self.tip)
        # Redirect only the official URL, keeping the real action commands intact.
        self.env.update(
            GIT_CONFIG_COUNT="1",
            GIT_CONFIG_KEY_0=f"url.{self.upstream}.insteadOf",
            GIT_CONFIG_VALUE_0="https://gcc.gnu.org/git/gcc.git",
        )
        action = yaml.safe_load(ACTION.read_text())
        self.initial, self.retry = [
            s["run"]
            for s in action["runs"]["steps"]
            if "inputs.branch == 'coordination'" in s.get("if", "")
        ]
        # Avoid the retry delay while still executing its fetch/checkout commands.
        self.retry = self.retry.replace("sleep 60", ":")

    def git(self, *args):
        return subprocess.check_output(
            ["git", *args], env=self.env, text=True, stderr=subprocess.DEVNULL
        ).strip()

    def run_action(self, script):
        return subprocess.run(
            ["bash", "--noprofile", "--norc", "-e", "-o", "pipefail", "-c", script],
            cwd=self.root,
            env=self.env,
            text=True,
            capture_output=True,
        )

    def test_fetches_vendor_only_ref_without_changing_origin(self):
        before = self.git("-C", str(self.checkout), "config", "--local", "--list")
        result = self.run_action(self.initial)
        self.assertEqual(result.returncode, 0, result.stderr)
        self.assertEqual(
            self.git("-C", str(self.checkout), "rev-parse", "HEAD"), self.tip
        )
        self.assertEqual(
            self.git("-C", str(self.checkout), "config", "--local", "--list"), before
        )
        self.assertEqual(
            self.git("-C", str(self.checkout), "branch", "--show-current"), ""
        )

    def test_retry_refreshes_a_cached_vendor_ref(self):
        self.assertEqual(self.run_action(self.initial).returncode, 0)
        self.git(
            "-C", str(self.upstream), "commit", "--allow-empty", "-m", "new vendor tip"
        )
        tip = self.git("-C", str(self.upstream), "rev-parse", "HEAD")
        self.git("-C", str(self.upstream), "update-ref", VENDOR_REF, tip)
        result = self.run_action(self.retry)
        self.assertEqual(result.returncode, 0, result.stderr)
        self.assertEqual(self.git("-C", str(self.checkout), "rev-parse", "HEAD"), tip)

    def test_missing_upstream_ref_does_not_use_stale_cache(self):
        self.git("-C", str(self.checkout), "update-ref", TRACKING_REF, "HEAD")
        original = self.git("-C", str(self.checkout), "rev-parse", "HEAD")
        self.git("-C", str(self.upstream), "update-ref", "-d", VENDOR_REF)
        for script in (self.initial, self.retry):
            with self.subTest(script=script):
                result = self.run_action(script)
                self.assertNotEqual(result.returncode, 0)
                self.assertEqual(
                    self.git("-C", str(self.checkout), "rev-parse", "HEAD"), original
                )
                self.assertEqual(
                    self.git("-C", str(self.checkout), "branch", "--show-current"),
                    "master",
                )


if __name__ == "__main__":
    unittest.main()
