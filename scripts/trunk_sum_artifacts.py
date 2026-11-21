"""Find the original DejaGnu results for a locally selected trunk report."""

from contextlib import contextmanager
from dataclasses import dataclass
from io import TextIOWrapper
import json
from pathlib import Path
import re
import shutil
import subprocess
from tempfile import TemporaryFile
from time import sleep
from urllib.parse import urlencode
from zipfile import BadZipFile, ZipFile
import zlib


class EvidenceUnavailable(Exception):
    """The selected report cannot be tied to readable raw test outcomes."""


def _api(args, output=None):
    for attempt in range(2):
        try:
            return subprocess.run(
                ["gh", "api", *args],
                stdout=output or subprocess.PIPE,
                stderr=subprocess.PIPE,
                check=True,
                timeout=180,
            ).stdout
        except (OSError, subprocess.SubprocessError) as exc:
            # gh stderr may contain a signed storage URL. Keep it out of issues.
            message = getattr(exc, "stderr", None) or b""
            transient = isinstance(exc, subprocess.TimeoutExpired) or re.search(
                rb"timeout|timed out|deadline exceeded|EOF|connection reset|HTTP 5\d\d",
                message,
                re.IGNORECASE,
            )
            if attempt or not transient:
                raise EvidenceUnavailable(
                    "GitHub Actions artifact lookup or download failed"
                ) from exc
            if output is not None:
                output.seek(0)
                output.truncate()
            sleep(1)


def _find_artifacts(endpoint, name):
    query = urlencode({"name": name, "per_page": 100})
    lines = _api([f"{endpoint}?{query}", "--paginate", "--jq", ".artifacts[] | @json"])
    try:
        found = [json.loads(line) for line in lines.splitlines()]
        return [
            item for item in found if item["name"] == name and item["expired"] is False
        ]
    except (ValueError, KeyError, TypeError) as exc:
        raise EvidenceUnavailable("malformed Actions artifact metadata") from exc


@contextmanager
def _download(repository, artifact_id):
    with TemporaryFile() as archive:
        _api(
            [f"repos/{repository}/actions/artifacts/{artifact_id}/zip"], output=archive
        )
        archive.seek(0)
        with ZipFile(archive) as bundle:
            yield bundle


def _member(archive, name):
    matches = [member for member in archive.infolist() if member.filename == name]
    if len(matches) != 1:
        raise EvidenceUnavailable(f"archive has no unique {name}")
    return matches[0]


@dataclass
class SumArtifact:
    url: str
    archive: ZipFile

    @contextmanager
    def open_tool(self, tool):
        """Yield decoded .sum lines for the given DejaGnu tool (e.g. 'g++')."""
        member = _member(self.archive, f"sum_files/{tool}.sum")
        try:
            with self.archive.open(member) as raw, TextIOWrapper(
                raw, encoding="utf-8"
            ) as lines:
                yield lines
        except (
            UnicodeError,
            BadZipFile,
            RuntimeError,
            EOFError,
            OSError,
            zlib.error,
        ) as exc:
            raise EvidenceUnavailable(f"unreadable {tool}.sum") from exc


@contextmanager
def open_sum_artifact(repository, report_path):
    """Open the raw results uploaded by the run that produced this exact report.

    The workflow can reuse reports from other runs. The GCC hash alone cannot
    distinguish reruns. Require a byte match with the downloaded report and
    retrieve its sibling sum archive from that run.
    """
    report_path = Path(report_path)
    name = report_path.name
    if not name.endswith("-report.log"):
        raise EvidenceUnavailable("expected a testsuite report filename")
    actions = f"repos/{repository}/actions"
    sum_name = name.removesuffix("-report.log") + "-sum-files"
    try:
        contents = report_path.read_bytes()
        matches = []
        for report in _find_artifacts(f"{actions}/artifacts", name):
            with _download(repository, report["id"]) as bundle:
                if bundle.read(_member(bundle, name)) == contents:
                    matches.append(report["workflow_run"]["id"])
        if len(matches) != 1:
            raise EvidenceUnavailable(
                "no unique workflow run matches the filtered report"
            )
        run_id = matches[0]
        sums = _find_artifacts(f"{actions}/runs/{run_id}/artifacts", sum_name)
        if len(sums) != 1:
            raise EvidenceUnavailable(
                f"run {run_id} has no unique unexpired sum archive"
            )
        artifact_id = sums[0]["id"]
        url = f"https://github.com/{repository}/actions/runs/{run_id}/artifacts/{artifact_id}"
        with _download(repository, artifact_id) as outer, TemporaryFile() as inner:
            with outer.open(_member(outer, sum_name + ".zip")) as packed:
                shutil.copyfileobj(packed, inner)
            inner.seek(0)
            with ZipFile(inner) as archive:
                yield SumArtifact(url, archive)
    except (
        KeyError,
        TypeError,
        BadZipFile,
        RuntimeError,
        EOFError,
        OSError,
        zlib.error,
    ) as exc:
        raise EvidenceUnavailable(f"cannot read the artifacts for {name}") from exc
