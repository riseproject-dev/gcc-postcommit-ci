"""Verify newly observed trunk test failures against the paired DejaGnu sums."""

import argparse
import re
from collections import Counter, defaultdict
from html import escape
from pathlib import Path

from trunk_sum_artifacts import EvidenceUnavailable, open_sum_artifact


HASH = r"[0-9a-f]{40}"
RESULTS = {
    "PASS",
    "FAIL",
    "XPASS",
    "XFAIL",
    "ERROR",
    "UNRESOLVED",
    "UNTESTED",
    "UNSUPPORTED",
    "KFAIL",
    "KPASS",
}


def board_description(board, multilib):
    """Reproduce the description printed by testsuite-filter and compare_testsuite_log."""
    flags = {"-march": "", "-mabi": "", "-mcmodel": ""}
    extra = []
    for part in board.split("/"):
        option, _, value = part.partition("=")
        if option in flags:
            flags[option] = value.lower()
        elif part != "riscv-sim":
            extra.append(part)
    parts = [*flags.values()]
    if multilib:
        parts.append("multilib")
    return " ".join([*parts, " ".join(extra)]).strip()


def new_results(summary):
    """Read the per-tool detail; the aggregate table loses individual options."""
    _, heading, details = summary.partition("\n# New Failures\n")
    if not heading:
        raise ValueError("missing New Failures section")
    candidates = defaultdict(Counter)
    group = tool = None
    for line in details.splitlines():
        if line.startswith("## "):
            group = line[3:].strip()
            tool = None
        elif line.startswith("### ") and line.endswith(" failures"):
            tool = line[4 : -len(" failures")]
        elif line.strip():
            status, sep, case = line.partition(": ")
            if tool not in {"gcc", "g++", "gfortran"} or group is None or not sep:
                raise ValueError("unrecognized test result in comparison")
            candidates[tool][group, case, status] += 1
    return candidates


def read_outcomes(lines, candidates, multilib):
    """Keep only requested tests, preserving their raw board and .exp driver."""
    wanted = {(group, case) for group, case, _ in candidates}
    outcomes = defaultdict(lambda: defaultdict(Counter))
    timeouts = Counter()
    triple = board = group = driver = warning = None
    for line in lines:
        line = line.rstrip("\r\n")
        if line.startswith("Test run by "):
            triple = board = group = driver = None
        elif line.startswith("Target is "):
            triple = line.removeprefix("Target is ").strip()
        elif line.startswith("Running target "):
            board = line.removeprefix("Running target ").strip()
            group = board_description(board, multilib)
            driver = None
        elif line.startswith("Running "):
            # Worker paths differ between GitHub and the Hetzner runners.
            match = re.search(r"/testsuite/(.+\.exp) \.\.\.$", line)
            driver = match[1] if match else None
        elif driver and board:
            status, sep, case = line.partition(": ")
            key = (group, case)
            if sep and status in RESULTS and key in wanted:
                scope = (triple, board, driver)
                outcomes[key][scope][status] += 1
                if (
                    status == "FAIL"
                    and warning == f"WARNING: {case} program timed out."
                ):
                    timeouts[key, scope] += 1
        warning = line if line.startswith("WARNING: ") else None
    return outcomes, timeouts


def transitions(before, after, timeouts, key):
    confirmed = timed_out = 0
    for scope, counts in after.get(key, {}).items():
        previous = before.get(key, {}).get(scope, {})
        total = sum(previous.values())
        if not total or total != sum(counts.values()):
            continue
        # Repeated identical subtests have no execution ID. Count just the
        # FAILs that cannot be accounted for by any previous non-PASS.
        changed = max(0, counts["FAIL"] - (total - previous.get("PASS", 0)))
        confirmed += changed
        timed_out += max(0, changed - (counts["FAIL"] - timeouts[key, scope]))
    return confirmed, timed_out


def comparison_note(before, after, key, status):
    if status == "XPASS":
        return "unexpected pass"
    scopes = [scope for scope, counts in after.get(key, {}).items() if counts[status]]
    if not scopes:
        return "outcome missing from the current raw results"
    matched = [before[key][scope] for scope in scopes if scope in before.get(key, {})]
    if not matched:
        return "no baseline result for the same board, test driver and subtest"
    prior = sum(matched, Counter())
    return "matching baseline outcomes: " + ", ".join(
        f"{result} × {count}" for result, count in sorted(prior.items())
    )


def code(text):
    return f"<code>{escape(text)}</code>"


def separate_snapshot_rows(body, sha):
    """The shared comparator compares a first report with itself to seed history."""
    heading = "|Unresolved Failures|gcc|g++|gfortran|Previous Hash|\n"
    head, marker, tail = body.partition(heading)
    if not marker:
        return body
    table, blank, rest = tail.partition("\n\n")
    if not table.startswith("|---|"):
        return body
    header, *rows = table.splitlines()
    kept, snapshots = [], []
    for row in rows:
        if f"|[{sha}-no-baseline](" in row or f"|[{sha}](" in row:
            snapshots.append(row.rsplit("|", 2)[0] + "|")
        else:
            kept.append(row)
    if not snapshots:
        return body
    return (
        head
        + marker
        + "\n".join(
            [
                header,
                *kept,
                "",
                "|Current failures without a distinct baseline|gcc|g++|gfortran|",
                "|---|---|---|---|",
                *snapshots,
            ]
        )
        + blank
        + rest
    )


def verify_tool(tool, candidates, artifacts, multilib, confirmed, review):
    try:
        previous, current = artifacts
        with previous.open_tool(tool) as lines:
            before, _ = read_outcomes(lines, candidates, multilib)
        with current.open_tool(tool) as lines:
            after, timeouts = read_outcomes(lines, candidates, multilib)
    except EvidenceUnavailable as err:
        review.append(f"- {tool}: {escape(str(err))}. See the observed results below.")
        return
    for (group, case, status), observed in candidates.items():
        key = (group, case)
        count, timed_out = (
            transitions(before, after, timeouts, key) if status == "FAIL" else (0, 0)
        )
        verified = min(count, observed)
        name = f"{code(tool)} {code(group)} {code(case)}"
        if verified:
            timeout = f" ({min(verified, timed_out)} timed out)" if timed_out else ""
            confirmed.append(f"- PASS → FAIL × {verified}{timeout}: {name}")
        if verified < observed:
            note = comparison_note(before, after, key, status)
            review.append(
                f"- {escape(status)} × {observed - verified}: {name} ({note})"
            )


def verify_report(root, current, sha, repo, load):
    summary = root / "summaries" / (current.stem + "-summary.md")
    if not summary.is_file():
        raise ValueError("comparison summary unavailable")
    text = summary.read_text()
    pair = re.search(rf"^title: ({HASH})->({HASH})$", text, re.MULTILINE)
    if not pair or pair[2] != sha or pair[1] == sha:
        raise ValueError("no distinct baseline in the comparison summary")
    previous = root / "previous_logs" / current.name.replace(f"-{sha}-", f"-{pair[1]}-")
    if not previous.is_file():
        raise ValueError("matching previous filtered report unavailable")
    candidates = new_results(text)
    if not candidates:
        return [], []
    target = current.name.removesuffix("-report.log").replace(f"-{sha}-", "-")
    heading = f"**{code(target)}** · {code(pair[1][:12])} → {code(sha[:12])}"
    verified, review = [], []
    try:
        with load(repo, previous) as old, load(repo, current) as new:
            heading += f" · [baseline sums]({old.url}) · [current sums]({new.url})"
            for tool, cases in candidates.items():
                verify_tool(
                    tool,
                    cases,
                    (old, new),
                    "-non-multilib-" not in current.name,
                    verified,
                    review,
                )
    except EvidenceUnavailable as err:
        review.append(f"- {escape(str(err))}. See the observed results below.")
    return (
        [heading, "", *verified, ""] if verified else [],
        [heading, "", *review, ""] if review else [],
    )


def update_report(root, sha, repo, load=open_sum_artifact):
    if not re.fullmatch(HASH, sha):
        raise ValueError("invalid GCC revision")
    verified, review = [], []
    paired = 0
    for current in sorted((root / "current_logs").glob("*-report.log")):
        try:
            if f"-{sha}-" not in current.name:
                raise ValueError("current report has a different revision")
            confirmed, pending = verify_report(root, current, sha, repo, load)
        except ValueError as err:
            review.append(f"- {code(current.name)}: {escape(str(err))}.")
            continue
        paired += 1
        verified.extend(confirmed)
        review.extend(pending)

    labels_file = root / "labels.txt"
    labels = set(filter(None, labels_file.read_text().strip().split(",")))
    labels.discard("new-regressions")
    if verified:
        labels.add("new-regressions")

    lines = [
        "## Verified test regressions",
        "",
        f"Target reports compared with a distinct baseline: {paired}.",
    ]
    if {"invalid", "build-failure", "testsuite-failure"} & labels:
        lines.append(
            "Coverage is incomplete. See the build, testsuite and comparison details below."
        )
    lines.extend(
        [
            "",
            *(
                verified
                or [
                    "No PASS → FAIL transition was confirmed in the available comparisons."
                ]
            ),
        ]
    )
    if review:
        lines.extend(
            [
                "",
                "## Results needing review",
                "",
                "A comparable prior PASS could not be established for these observations.",
                "",
                *review,
            ]
        )

    body_file = root / "issue.md"
    body = separate_snapshot_rows(body_file.read_text(), sha)
    # The shared comparator lists differences between filtered reports,
    # including results for newly introduced tests and configurations.
    body = body.replace("|New Failures|gcc|", "|Newly observed results|gcc|")
    body = body.replace(
        "\n## New Failures Across All Affected Targets",
        "\n## Newly observed results across affected targets",
    )
    body = body.replace(
        "\n## Architecture Specific New Failures",
        "\n## Newly observed results by architecture",
    )
    body_file.write_text("\n".join(lines).rstrip() + "\n\n" + body.lstrip())
    labels_file.write_text(",".join(sorted(labels)))


if __name__ == "__main__":
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--repo", required=True)
    parser.add_argument("--sha", required=True)
    args = parser.parse_args()
    update_report(Path.cwd(), args.sha, args.repo)
