import io
import json
from pathlib import Path
from tempfile import TemporaryDirectory
import unittest
from unittest.mock import patch
from urllib.parse import parse_qs, urlsplit
from zipfile import ZipFile

import trunk_sum_artifacts as artifacts


def bundle(members):
    buffer = io.BytesIO()
    with ZipFile(buffer, "w") as archive:
        for name, contents in members.items():
            archive.writestr(name, contents)
    return buffer.getvalue()


class TestSumArtifacts(unittest.TestCase):
    def setUp(self):
        directory = TemporaryDirectory()
        self.addCleanup(directory.cleanup)
        self.report = Path(directory.name) / "gcc-target-revision-multilib-report.log"
        self.report.write_bytes(b"chosen report\n")
        self.sum_name = self.report.name.removesuffix("-report.log") + "-sum-files"
        self.records = []
        self.archives = {}
        self.add(1, 10, self.report.name, bundle({self.report.name: b"other run\n"}))
        self.add(
            2,
            20,
            self.report.name,
            bundle({self.report.name: self.report.read_bytes()}),
        )
        inner = bundle(
            {"sum_files/gcc.sum": b"PASS: case\n", "sum_files/g++.sum": b"\xff"}
        )
        self.add(3, 20, self.sum_name, bundle({self.sum_name + ".zip": inner}))
        self.add(4, 10, self.sum_name, b"wrong run")
        self.enterContext(patch.object(artifacts, "_api", side_effect=self.api))

    def add(self, artifact_id, run_id, name, archive, expired=False):
        self.records.append(
            {
                "name": name,
                "id": artifact_id,
                "workflow_run": {"id": run_id},
                "expired": expired,
            }
        )
        self.archives[artifact_id] = archive

    def api(self, args, output=None):
        url = urlsplit(args[0])
        if output is not None:
            output.write(self.archives[int(url.path.split("/")[-2])])
            return
        name = parse_qs(url.query)["name"][0]
        records = [r for r in self.records if r["name"] == name]
        if "/runs/" in url.path:
            run = int(url.path.split("/")[-2])
            records = [r for r in records if r["workflow_run"]["id"] == run]
        return "\n".join(json.dumps(r) for r in records).encode()

    def test_matches_local_report_and_its_sibling_archive(self):
        with artifacts.open_sum_artifact("owner/repo", self.report) as artifact:
            self.assertEqual(
                artifact.url,
                "https://github.com/owner/repo/actions/runs/20/artifacts/3",
            )
            for tool in ("g++", "gfortran"):
                with self.assertRaises(artifacts.EvidenceUnavailable):
                    with artifact.open_tool(tool) as lines:
                        list(lines)
            with artifact.open_tool("gcc") as lines:
                self.assertEqual(list(lines), ["PASS: case\n"])

    def test_ambiguous_identical_report_or_sums(self):
        for name, source in ((self.report.name, 2), (self.sum_name, 3)):
            with self.subTest(name=name):
                self.add(5, 20, name, self.archives[source])
                with self.assertRaises(artifacts.EvidenceUnavailable):
                    with artifacts.open_sum_artifact("owner/repo", self.report):
                        pass
                self.records.pop()

    def test_expired_sums_do_not_fall_back_to_other_run(self):
        self.records[2]["expired"] = True
        with self.assertRaisesRegex(artifacts.EvidenceUnavailable, "unexpired sum"):
            with artifacts.open_sum_artifact("owner/repo", self.report):
                pass

    def test_no_matching_content_or_malformed_metadata(self):
        self.report.write_bytes(b"different")
        with self.assertRaisesRegex(
            artifacts.EvidenceUnavailable, "no unique workflow"
        ):
            with artifacts.open_sum_artifact("owner/repo", self.report):
                pass
        with patch.object(artifacts, "_api", return_value=b"broken"):
            with self.assertRaisesRegex(artifacts.EvidenceUnavailable, "metadata"):
                with artifacts.open_sum_artifact("owner/repo", self.report):
                    pass


if __name__ == "__main__":
    unittest.main()
