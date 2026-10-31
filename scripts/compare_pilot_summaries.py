#!/usr/bin/env python3
"""Compare complete GCC/G++ DejaGnu summaries from the same compiler revision.

This is an offline migration check, not a replacement for production regression
reporting. Callers must first verify source revisions and configure options.
Known failures remain visible; new failures or reduced PASS coverage fail the
comparison. Only the runner workspace prefix is normalized.
"""

import argparse
from collections import Counter
import json
from pathlib import Path
import re


ADVERSE = {"FAIL", "XPASS", "UNRESOLVED", "ERROR", "WARNING"}
STATUSES = ADVERSE | {"PASS", "XFAIL", "UNSUPPORTED"}
WORKSPACE = re.compile(
    r"/(?:home|var/lib)/\S*?/_work/gcc-postcommit-ci/gcc-postcommit-ci/"
)


def read_summary(path):
    tool = path.stem
    variants = {}
    scheduled = []
    completed = set()
    target = None
    in_schedule = False
    final = False
    final_passes = None
    with path.open(encoding="utf-8", errors="strict") as stream:
        for raw in stream:
            line = raw.strip()
            if line == "Schedule of variations:":
                in_schedule = True
                continue
            if in_schedule:
                if line and raw.startswith("    "):
                    scheduled.append(line)
                    continue
                in_schedule = False
            if line.startswith("Running target "):
                target = line.removeprefix("Running target ")
                if target in variants:
                    raise ValueError(f"{path}: repeated target {target}")
                variants[target] = {"counts": Counter(), "adverse": Counter()}
            elif line.startswith(f"=== {tool} Summary for "):
                completed.add(
                    line.removeprefix(f"=== {tool} Summary for ").removesuffix(" ===")
                )
            elif line == f"=== {tool} Summary ===":
                final = True
                target = None
            elif final and line.startswith("# of expected passes"):
                final_passes = int(line.split()[-1])
            else:
                status = line.split(":", 1)[0]
                if status not in STATUSES:
                    continue
                if target is None:
                    raise ValueError(f"{path}: result outside a target section")
                variants[target]["counts"][status] += 1
                if status in ADVERSE:
                    variants[target]["adverse"][
                        WORKSPACE.sub("<workspace>/", line)
                    ] += 1
    passes = sum(v["counts"]["PASS"] for v in variants.values())
    if (
        not final
        or not scheduled
        or len(scheduled) != len(set(scheduled))
        or set(scheduled) != set(variants)
        or completed != set(variants)
        or final_passes != passes
        or any(v["counts"]["PASS"] == 0 for v in variants.values())
    ):
        raise ValueError(f"{path}: missing or incomplete target summaries")
    return variants


def compare(baseline, current):
    rows = []
    for tool in ("gcc", "g++"):
        old = read_summary(baseline / f"{tool}.sum")
        new = read_summary(current / f"{tool}.sum")
        if old.keys() != new.keys():
            raise ValueError(f"{tool}: baseline and current target sets differ")
        for target in sorted(old):
            added = new[target]["adverse"] - old[target]["adverse"]
            resolved = old[target]["adverse"] - new[target]["adverse"]
            pass_delta = new[target]["counts"]["PASS"] - old[target]["counts"]["PASS"]
            rows.append(
                {
                    "tool": tool,
                    "target": target,
                    "baseline_counts": dict(old[target]["counts"]),
                    "current_counts": dict(new[target]["counts"]),
                    "added_adverse": dict(sorted(added.items())),
                    "resolved_adverse": dict(sorted(resolved.items())),
                    "pass_delta": pass_delta,
                    "accepted": not added and pass_delta >= 0,
                }
            )
    return {"accepted": all(row["accepted"] for row in rows), "targets": rows}


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument(
        "baseline", type=Path, help="directory containing gcc.sum and g++.sum"
    )
    parser.add_argument(
        "current", type=Path, help="directory containing gcc.sum and g++.sum"
    )
    parser.add_argument("--output", type=Path, required=True)
    args = parser.parse_args()
    try:
        report = compare(args.baseline, args.current)
    except (OSError, UnicodeError, ValueError) as error:
        report = {"accepted": False, "error": str(error)}
    args.output.write_text(json.dumps(report, indent=2) + "\n")
    if "error" in report:
        print(report["error"])
    else:
        for row in report["targets"]:
            print(
                f'{row["tool"]} {row["target"]}: '
                f'{sum(row["added_adverse"].values())} additional adverse results, '
                f'PASS delta {row["pass_delta"]:+d}'
            )
    return 0 if report["accepted"] else 1


if __name__ == "__main__":
    raise SystemExit(main())
