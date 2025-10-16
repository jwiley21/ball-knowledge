"""CLI entry point for the Chuck Norris Jokes tool.

Usage:
    python -m src.main random
    python -m src.main categories
    python -m src.main search <query> [--limit N]
"""
from __future__ import annotations

import argparse
from typing import List, Optional

from . import api


def _build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(
        prog="ball-knowledge-cli",
        description="Fun CLI that fetches data from a public API (Chuck Norris).",
    )
    sub = parser.add_subparsers(dest="command", required=True)

    p_random = sub.add_parser("random", help="Print a random Chuck Norris joke")

    p_categories = sub.add_parser("categories", help="List all joke categories")

    p_search = sub.add_parser("search", help="Search for jokes containing a word")
    p_search.add_argument("query", help="Search term (e.g., 'code')")
    p_search.add_argument("--limit", type=int, default=5, help="Max jokes to show (default: 5)")

    return parser


def _cmd_random() -> int:
    print(api.get_random_joke())
    return 0


def _cmd_categories() -> int:
    cats = api.get_categories()
    for c in cats:
        print(c)
    return 0


def _cmd_search(query: str, limit: int) -> int:
    jokes = api.search_jokes(query, limit=limit)
    if not jokes:
        print("No results.")
        return 0
    for i, j in enumerate(jokes, start=1):
        print(f"{i}. {j}")
    return 0


def main(argv: Optional[List[str]] = None) -> int:
    parser = _build_parser()
    args = parser.parse_args(argv)

    if args.command == "random":
        return _cmd_random()
    if args.command == "categories":
        return _cmd_categories()
    if args.command == "search":
        return _cmd_search(args.query, args.limit)

    parser.error("Unknown command")
    return 2


if __name__ == "__main__":
    raise SystemExit(main())

