"""Command line interface: ``devex-metrics --owner ORG --repo NAME``."""

from __future__ import annotations

import argparse
import json
import sys
import urllib.error

from .collector import GitHubMetricsCollector


def build_parser() -> argparse.ArgumentParser:
    p = argparse.ArgumentParser(
        prog="devex-metrics", description="Collect DORA metrics from GitHub."
    )
    p.add_argument("--owner", required=True, help="GitHub org or user")
    p.add_argument("--repo", required=True, help="Repository name")
    p.add_argument("--window", type=int, default=30, help="Lookback window in days (default 30)")
    p.add_argument("--environment", default="production", help="Deployment environment name")
    p.add_argument("--format", choices=["text", "json"], default="text", help="Output format")
    return p


def main(argv: list[str] | None = None) -> int:
    args = build_parser().parse_args(argv)
    if args.window <= 0:
        print("error: --window must be a positive number of days", file=sys.stderr)
        return 2
    try:
        collector = GitHubMetricsCollector(owner=args.owner, repo=args.repo)
        metrics = collector.collect(window_days=args.window, environment=args.environment)
    except ValueError as exc:  # missing token
        print(f"error: {exc}", file=sys.stderr)
        return 2
    except urllib.error.HTTPError as exc:
        print(f"error: GitHub API returned HTTP {exc.code} for {exc.url}", file=sys.stderr)
        return 1
    except urllib.error.URLError as exc:
        print(f"error: could not reach GitHub: {exc.reason}", file=sys.stderr)
        return 1

    if args.format == "json":
        print(json.dumps(metrics.to_dict(), indent=2))
    else:
        print(metrics.summary())
    return 0


if __name__ == "__main__":
    sys.exit(main())
