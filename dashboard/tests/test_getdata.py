import os
import sys
import tempfile
import types
import unittest
from pathlib import Path
from unittest import mock


DASHBOARD_DIR = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(DASHBOARD_DIR))

compare_module = types.ModuleType("compare_testsuite_log")
compare_module.Description = object
compare_module.classify_by_unique_failure = lambda failures: failures
compare_module.parse_testsuite_failures = lambda _path: {}
sys.modules.setdefault("compare_testsuite_log", compare_module)

download_module = types.ModuleType("download_artifact")
download_module.search_for_artifact = lambda *_args, **_kwargs: None
download_module.download_artifact = lambda *_args, **_kwargs: None
download_module.extract_artifact = lambda *_args, **_kwargs: None
sys.modules.setdefault("download_artifact", download_module)

import getdata


HASH_1 = "0123456789abcdef0123456789abcdef01234567"
HASH_2 = "89abcdef0123456789abcdef0123456789abcdef"


class FakeResponse:
    def __init__(self, payload, links=None, status_code=200, text=""):
        self._payload = payload
        self.links = links or {}
        self.status_code = status_code
        self.text = text

    def json(self):
        return self._payload


class DashboardIssueTests(unittest.TestCase):
    @mock.patch("getdata.requests.get")
    def test_get_issue_hashes_paginates_and_filters_titles(self, request_get):
        request_get.side_effect = [
            FakeResponse(
                [
                    {"title": "ordinary maintenance issue"},
                    {"title": f"Testsuite Status {HASH_1}"},
                    {"title": f"Testsuite Status {HASH_2}", "pull_request": {}},
                ],
                links={"next": {"url": "https://api.github.com/page/2"}},
            ),
            FakeResponse(
                [
                    {"title": f"Testsuite Status {HASH_1}"},
                    {"title": f"Testsuite Status {HASH_2}"},
                    {"title": "Testsuite Status not-a-hash"},
                ]
            ),
        ]

        self.assertEqual(
            getdata.get_issue_hashes("token", "riseproject-dev/gcc-postcommit-ci"),
            [HASH_1, HASH_2],
        )
        self.assertEqual(
            request_get.call_args_list[0].kwargs["params"],
            {"state": "all", "per_page": 100},
        )
        self.assertIsNone(request_get.call_args_list[1].kwargs["params"])

    def test_all_dashboard_status_titles_are_accepted(self):
        for title in getdata.DASHBOARD_STATUS_TITLES:
            with self.subTest(title=title):
                match = getdata.STATUS_ISSUE_PATTERN.fullmatch(f"{title} {HASH_1}")
                self.assertIsNotNone(match)
                self.assertEqual(match.group(1), HASH_1)

    def test_non_dashboard_status_titles_are_rejected(self):
        rejected = [
            f"ordinary issue {HASH_1}",
            f"Coordination Branch Testsuite Status {HASH_1}",
            f"Release 16 Branch Testsuite Status {HASH_1}",
            "Testsuite Status not-a-hash",
        ]
        for title in rejected:
            with self.subTest(title=title):
                self.assertIsNone(getdata.STATUS_ISSUE_PATTERN.fullmatch(title))

    @mock.patch("getdata.search_for_artifact", return_value=None)
    def test_missing_expired_artifact_is_skipped(self, _search):
        self.assertIsNone(
            getdata.download_summaries(
                f"{HASH_1}-current-logs",
                "token",
                "riseproject-dev/gcc-postcommit-ci",
            )
        )

    @mock.patch("getdata.search_for_artifact", return_value="123")
    @mock.patch("getdata.download_artifact")
    def test_artifact_expiring_during_download_is_skipped(self, download, _search):
        response = getdata.requests.Response()
        response.status_code = 410
        download.side_effect = getdata.requests.HTTPError(response=response)
        self.assertIsNone(
            getdata.download_summaries(
                f"{HASH_1}-current-logs", "token", "riseproject-dev/gcc-postcommit-ci"
            )
        )

    @mock.patch("getdata.search_for_artifact", return_value="123")
    @mock.patch("getdata.download_artifact")
    def test_other_download_errors_are_not_hidden(self, download, _search):
        for status in (403, 404, 500):
            with self.subTest(status=status):
                response = getdata.requests.Response()
                response.status_code = status
                download.side_effect = getdata.requests.HTTPError(response=response)
                with self.assertRaises(getdata.requests.HTTPError):
                    getdata.download_summaries(
                        f"{HASH_1}-current-logs",
                        "token",
                        "riseproject-dev/gcc-postcommit-ci",
                    )


class DashboardBootstrapTests(unittest.TestCase):
    def setUp(self):
        previous_directory = os.getcwd()
        directory = tempfile.TemporaryDirectory(prefix="rise-dashboard-test-")
        self.addCleanup(directory.cleanup)
        self.addCleanup(os.chdir, previous_directory)
        os.chdir(directory.name)

    def run_empty_ingestion(self, bootstrap=False):
        args = types.SimpleNamespace(
            bootstrap=bootstrap,
            token="token",
            repo="riseproject-dev/gcc-postcommit-ci",
        )
        with (
            mock.patch("getdata.parse_arguments", return_value=args),
            mock.patch("getdata.get_issue_hashes", return_value=[]),
            mock.patch("getdata.os.popen"),
        ):
            getdata.main()

    def assert_empty_dashboard(self):
        self.assertTrue(Path("testsuite_runs").is_dir())
        for name in getdata.DATA_FILES:
            self.assertEqual(Path(name).read_text(), getdata.CSV_HEADER)

    def test_empty_repository_creates_directories_and_csv_headers(self):
        self.run_empty_ingestion()
        self.assert_empty_dashboard()

    def test_empty_repository_bootstrap_creates_csv_headers(self):
        self.run_empty_ingestion(bootstrap=True)
        self.assert_empty_dashboard()

    def test_bootstrap_resets_each_csv_even_when_one_is_missing(self):
        for name in getdata.DATA_FILES[1:]:
            Path(name).write_text("old rows\n")
        self.run_empty_ingestion(bootstrap=True)
        self.assert_empty_dashboard()

    def test_normal_run_preserves_existing_csv_data(self):
        retained_data = getdata.CSV_HEADER + "retained row\n"
        for name in getdata.DATA_FILES:
            Path(name).write_text(retained_data)
        self.run_empty_ingestion()
        for name in getdata.DATA_FILES:
            self.assertEqual(Path(name).read_text(), retained_data)


if __name__ == "__main__":
    unittest.main()
