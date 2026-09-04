import argparse
import contextlib
import os
import re
import shutil
from typing import Dict, List, Set
from zipfile import ZipFile
import requests
from compare_testsuite_log import (
    Description,
    classify_by_unique_failure,
    parse_testsuite_failures,
)
from download_artifact import search_for_artifact, download_artifact, extract_artifact


DEFAULT_POSTCOMMIT_REPOSITORY = os.environ.get(
    "POSTCOMMIT_REPOSITORY", "riseproject-dev/gcc-postcommit-ci"
)
DASHBOARD_STATUS_TITLES = (
    "Testsuite Status",
    "Testsuite zve Status",
    "Testsuite rv32gcv-zvl Status",
    "Testsuite rv64gcv-zvl Status",
    "Testsuite rv64gcv-zvl lmul2 Status",
    "Testsuite with checking Status",
)
STATUS_ISSUE_PATTERN = re.compile(
    rf"^(?:{'|'.join(re.escape(title) for title in DASHBOARD_STATUS_TITLES)}) "
    r"([0-9a-f]{40})$"
)


def parse_arguments():
    """parse command line arguments"""
    parser = argparse.ArgumentParser(description="Get issue information")
    parser.add_argument(
        "-token",
        required=True,
        type=str,
        help="Github access token",
    )
    parser.add_argument(
        "-bootstrap",
        action="store_true",
        help="Build the current_logs from scratch. Takes a long time.",
    )
    parser.add_argument(
        "-repo",
        default=DEFAULT_POSTCOMMIT_REPOSITORY,
        type=str,
        help="GitHub repository to read dashboard issues and artifacts from",
    )
    return parser.parse_args()


def get_issue_hashes(token: str, repo: str):
    issue_url = f"https://api.github.com/repos/{repo}/issues"
    query = {"state": "all", "per_page": 100}
    headers = {
        "Accept": "application/vnd.github+json",
        "Authorization": f"token {token}",
        "X-Github-Api-Version": "2022-11-28",
    }
    hashes: List[str] = []
    seen_hashes: Set[str] = set()

    while issue_url:
        response = requests.get(issue_url, headers=headers, params=query, timeout=60)
        if response.status_code != 200:
            raise RuntimeError(
                f"Failed to list dashboard issues from {repo}: "
                f"HTTP {response.status_code}: {response.text[:500]}"
            )
        try:
            issues = response.json()
        except ValueError as exc:
            raise RuntimeError(
                f"GitHub returned invalid JSON while listing issues from {repo}"
            ) from exc
        if not isinstance(issues, list):
            raise RuntimeError(
                f"GitHub returned {type(issues).__name__}, expected an issue list"
            )
        for issue in issues:
            if "pull_request" in issue:
                continue
            match = STATUS_ISSUE_PATTERN.fullmatch(issue.get("title", ""))
            if match and match.group(1) not in seen_hashes:
                hashes.append(match.group(1))
                seen_hashes.add(match.group(1))

        issue_url = response.links.get("next", {}).get("url")
        query = None

    return hashes


def download_summaries(artifact_name: str, token: str, repo: str):
    artifact_id = None
    # Try all prefixes
    for prefix in [
        "",
        "zve_",
        "rv32_zvl_",
        "rv64_zvl_lmul2_",
        "rv64_zvl_",
        "coord_",
        "release_15_",
        "release_16_",
        "binutils_",
        "checking_",
    ]:

        print(f"searching for {prefix + artifact_name}")
        artifact_id = search_for_artifact(prefix + artifact_name, repo, token, None)
        print(f"found id {artifact_id}")
        if artifact_id is not None:
            artifact_name = prefix + artifact_name
            break
    if artifact_id is None:
        print(
            f"No retained dashboard artifact was found for {artifact_name}; "
            "skipping this issue"
        )
        return None
    if artifact_name.startswith(("coord_", "binutils_")) or artifact_name.startswith(
        "release_"
    ):
        # Ignore coordination/release/binutils runs
        return None
    artifact_path = download_artifact(artifact_name, artifact_id, token, repo, "temp")
    return artifact_path


def download_logs(token: str, repo: str, existing_hashes: Set[str]):
    hashes = get_issue_hashes(token, repo)
    shutil.rmtree("temp", ignore_errors=True)
    os.mkdir("temp")
    hashes = [gcc_hash for gcc_hash in hashes if gcc_hash not in existing_hashes]

    failure_hashes: Set[str] = set()

    for gcc_hash in hashes:
        artifact_name = f"{gcc_hash}-current-logs"
        artifact_zip = download_summaries(artifact_name, token, repo)
        if artifact_zip is None:
            continue
        os.makedirs(f"testsuite_runs/{gcc_hash}", exist_ok=True)
        extract_artifact(artifact_zip, outdir=f"testsuite_runs/{gcc_hash}")
        with ZipFile(f"./testsuite_runs/{gcc_hash}/current_logs.zip", "r") as zf:
            zf.extractall(path=f"./testsuite_runs/{gcc_hash}")
        if os.path.isfile(
            f"./testsuite_runs/{gcc_hash}/current_logs/failed_testsuite.txt"
        ):
            # Failed build, drop this hash
            print(
                f"Testsuite(s) failed for {gcc_hash}, dropping failing testsuite runs from testsuite_runs"
            )
            failure_hashes.add(gcc_hash)
            os.remove(f"./testsuite_runs/{gcc_hash}/current_logs/failed_testsuite.txt")
        if os.path.isfile(f"./testsuite_runs/{gcc_hash}/current_logs/failed_build.txt"):
            # Failed build, drop this hash
            print(
                f"Build(s) failed for {gcc_hash}, dropping failing builds from testsuite_runs"
            )
            failure_hashes.add(gcc_hash)
            os.remove(f"./testsuite_runs/{gcc_hash}/current_logs/failed_build.txt")
        os.remove(f"./testsuite_runs/{gcc_hash}/current_logs.zip")
    shutil.rmtree("./temp")

    hashes = [gcc_hash for gcc_hash in hashes if gcc_hash not in failure_hashes]

    return hashes


def get_gcc_hash_timestamp(gcc_hash: str):
    return (
        os.popen(
            f"cd ../riscv-gnu-toolchain/gcc && git show -s --format='%ci' {gcc_hash}"
        )
        .read()
        .strip()
    )


def aggregate_logs(logs_dir: str, gcc_hash: str):
    files = os.listdir(logs_dir)
    print(logs_dir)
    all_linux_failures: Dict[Description, List[str]] = {}
    all_newlib_failures: Dict[Description, List[str]] = {}
    all_filtered_linux_failures: Dict[Description, List[str]] = {}
    all_filtered_newlib_failures: Dict[Description, List[str]] = {}
    for file in files:
        current_failures = parse_testsuite_failures(logs_dir + file)
        filtered_current_failures: Dict[Description, List[str]] = {}
        for desc, fails in current_failures.items():
            filtered_current_failures[desc] = [
                fail
                for fail in fails
                if (
                    "internal compiler error" in fail
                    or "Segmentation fault" in fail
                    or "test for excess errors" in fail
                    or "execution test" in fail
                    or "execute" in fail.split(" ")[2:]
                )
            ]
        if "linux" in file:
            all_linux_failures.update(current_failures)
            all_filtered_linux_failures.update(filtered_current_failures)
        else:
            all_newlib_failures.update(current_failures)
            all_filtered_newlib_failures.update(filtered_current_failures)

    for target, fails in all_linux_failures.items():
        class_fails = classify_by_unique_failure(fails)

        hash_timestamp = get_gcc_hash_timestamp(gcc_hash)

        with open("linux.csv", "a") as csv:
            csv.write(
                f"{gcc_hash},{hash_timestamp},linux-{target.libname}-{target.tool},linux,{target.libname},{target.tool},{len(class_fails.keys())},{len(fails)}\n"
            )

    for target, fails in all_newlib_failures.items():
        class_fails = classify_by_unique_failure(fails)

        hash_timestamp = get_gcc_hash_timestamp(gcc_hash)

        with open("newlib.csv", "a") as csv:
            csv.write(
                f"{gcc_hash},{hash_timestamp},newlib-{target.libname}-{target.tool},newlib,{target.libname},{target.tool},{len(class_fails.keys())},{len(fails)}\n"
            )

    # Write filtered csvs
    for target, fails in all_filtered_linux_failures.items():
        class_fails = classify_by_unique_failure(fails)

        hash_timestamp = get_gcc_hash_timestamp(gcc_hash)

        with open("filtered_linux.csv", "a") as csv:
            csv.write(
                f"{gcc_hash},{hash_timestamp},linux-{target.libname}-{target.tool},linux,{target.libname},{target.tool},{len(class_fails.keys())},{len(fails)}\n"
            )

    for target, fails in all_filtered_newlib_failures.items():
        class_fails = classify_by_unique_failure(fails)

        hash_timestamp = get_gcc_hash_timestamp(gcc_hash)

        with open("filtered_newlib.csv", "a") as csv:
            csv.write(
                f"{gcc_hash},{hash_timestamp},newlib-{target.libname}-{target.tool},newlib,{target.libname},{target.tool},{len(class_fails.keys())},{len(fails)}\n"
            )


def main():
    args = parse_arguments()

    hashes = []

    if args.bootstrap:
        shutil.rmtree("./testsuite_runs", ignore_errors=True)
        data_files = [
            "linux.csv",
            "newlib.csv",
            "filtered_linux.csv",
            "filtered_newlib.csv",
        ]

        with contextlib.suppress(FileNotFoundError):
            for file in data_files:
                os.remove(file)
        existing_hashes: Set[str] = set()
        download_logs(args.token, args.repo, existing_hashes)
        hashes = sorted(os.listdir("testsuite_runs"))
        for file in data_files:
            with open(file, "w") as csv:
                csv.write(
                    "gcc_hash,hash_timestamp,libc-libname-tool,libc,target,tool,unique_fails,total_fails\n"
                )
    else:
        existing_hashes = set(os.listdir("testsuite_runs"))
        download_logs(args.token, args.repo, existing_hashes)
        new_hashes = sorted(set(os.listdir("testsuite_runs")) - existing_hashes)
        hashes = new_hashes

    print(hashes)

    # Get GCC ready for timestamp-getting
    os.popen(
        "cd ../riscv-gnu-toolchain && git submodule update --init gcc && cd gcc && git fetch"
    )

    for gcc_hash in hashes:
        aggregate_logs(f"./testsuite_runs/{gcc_hash}/current_logs/", gcc_hash)


if __name__ == "__main__":
    main()
