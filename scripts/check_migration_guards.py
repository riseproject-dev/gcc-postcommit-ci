#!/usr/bin/env python3
import re
import subprocess
import sys
from pathlib import Path


ACTIVE_PATH_PREFIXES = (
    ".github/",
    "configure-scripts/",
    "dashboard/",
    "docs/",
    "scripts/",
    ".gitmodules",
    ".pre-commit-config.yaml",
    "README.md",
)

EXCLUDED_FILES = {"scripts/check_migration_guards.py"}
EXCLUDED_PREFIXES = ("dashboard/site/", "dashboard/testsuite_runs/")

LEGACY_POSTCOMMIT_OWNER = "patrick" + "-rivos"
PERSONAL_FORK_OWNER = "pz" + "9115"
RETIRED_RELEASE = "1" + "4"

FORBIDDEN_PATTERNS = {
    "legacy pre-commit repository": re.compile("ew" + r"lu/gcc-precommit-ci"),
    "legacy post-commit repository": re.compile(
        rf"{LEGACY_POSTCOMMIT_OWNER}/gcc-postcommit-ci"
    ),
    "legacy toolchain repository": re.compile(
        rf"{LEGACY_POSTCOMMIT_OWNER}/riscv-gnu-toolchain"
    ),
    "legacy Pages domain": re.compile(rf"{LEGACY_POSTCOMMIT_OWNER}\.github\.io"),
    "legacy Rivos Patchwork account": re.compile(r"rivoscibot"),
    "legacy Rivos Patchwork context": re.compile(r"toolchain-ci-rivos"),
    "Rivos support email": re.compile(r"patchworks-ci@" + r"rivosinc\.com"),
    "personal fork repository": re.compile(
        rf"{PERSONAL_FORK_OWNER}/"
        r"(gcc-precommit-ci|gcc-postcommit-ci|riscv-gnu-toolchain)"
    ),
    "active GCC release 14 configuration": re.compile(
        rf"(gcc[-_ /]?{RETIRED_RELEASE}|release[-_/]?{RETIRED_RELEASE}|"
        rf"releases/gcc-{RETIRED_RELEASE}|release_{RETIRED_RELEASE}_)",
        re.IGNORECASE,
    ),
}


def active_files():
    output = subprocess.check_output(["git", "ls-files"], text=True)
    for name in output.splitlines():
        if name in EXCLUDED_FILES or name.startswith(EXCLUDED_PREFIXES):
            continue
        if name.startswith(ACTIVE_PATH_PREFIXES):
            yield Path(name)


def main():
    failures = []
    for path in active_files():
        if not path.exists():
            continue
        try:
            text = path.read_text(encoding="utf-8")
        except UnicodeDecodeError:
            continue
        for label, pattern in FORBIDDEN_PATTERNS.items():
            for match in pattern.finditer(text):
                line = text.count("\n", 0, match.start()) + 1
                failures.append(f"{path}:{line}: {label}: {match.group(0)}")

    if failures:
        print("Forbidden migration references found in active source/config:")
        print("\n".join(failures))
        return 1

    print("Migration ownership guard passed")
    return 0


if __name__ == "__main__":
    sys.exit(main())
