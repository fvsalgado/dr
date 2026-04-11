#!/usr/bin/env python3
"""
Run scrapers sequentially. Targets share data/results.json — do not run in parallel
without merging logic (each script read-modify-writes the same file).
"""
from __future__ import annotations

import argparse
import subprocess
import sys
from datetime import date
from pathlib import Path


BASE_DIR = Path(__file__).resolve().parent
EXCLUDED = {"news_common.py", "source_meta.py", "run_all.py"}


def discover_targets() -> list[str]:
    targets: set[str] = set()
    for file in BASE_DIR.glob("*.py"):
        if file.name in EXCLUDED or file.name.startswith("_"):
            continue
        stem = file.stem
        if stem.startswith("news_"):
            targets.add("news")
        else:
            targets.add(stem)
    return sorted(targets)


def run_target(target: str, date_arg: str, days: int, news_sources: str, news_max_age_days: float) -> int:
    script = BASE_DIR / f"{target}.py"
    if target == "news":
        cmd = [
            sys.executable,
            str(script),
            "--sources",
            news_sources,
            "--max-age-days",
            str(news_max_age_days),
        ]
    else:
        cmd = [sys.executable, str(script)]
        if date_arg:
            cmd.append(date_arg)
        cmd.extend(["--days", str(days)])
    print(f"::group::run:{target}")
    print("Running:", " ".join(cmd))
    rc = subprocess.run(cmd, check=False).returncode
    print(f"Exit code [{target}]: {rc}")
    print("::endgroup::")
    return rc


def main() -> int:
    parser = argparse.ArgumentParser(description="Run all available scrapers with resilience")
    parser.add_argument("--date", default="", help="End date (YYYY-MM-DD), default today")
    parser.add_argument("--days", type=int, default=30, help="Days lookback for non-news scrapers")
    parser.add_argument("--targets", default="all", help="Comma-separated targets or 'all'")
    parser.add_argument("--news-sources", default="all", help="Comma-separated news sources or 'all'")
    parser.add_argument(
        "--news-max-age-days",
        type=float,
        default=7.0,
        help="News scraper: only items published within the last N days (0 = no date filter)",
    )
    parser.add_argument("--allow-partial", action="store_true", help="Do not fail if one target fails")
    args = parser.parse_args()

    date_arg = args.date.strip()
    if not date_arg:
        date_arg = date.today().isoformat()

    available = discover_targets()
    selected = available
    if args.targets.strip().lower() != "all":
        wanted = [x.strip() for x in args.targets.split(",") if x.strip()]
        selected = [x for x in wanted if x in available]
        unknown = sorted(set(wanted) - set(selected))
        if unknown:
            print(f"Unknown targets ignored: {unknown}")
    if not selected:
        print("No targets selected")
        return 1

    failures: list[str] = []
    for target in selected:
        rc = run_target(
            target,
            date_arg=date_arg,
            days=args.days,
            news_sources=args.news_sources,
            news_max_age_days=args.news_max_age_days,
        )
        if rc != 0:
            failures.append(target)

    print(f"Available targets: {available}")
    print(f"Selected targets: {selected}")
    if failures:
        print(f"Failed targets: {failures}")
        return 0 if args.allow_partial else 1
    print("All selected scrapers executed successfully")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
