from __future__ import annotations

import argparse
import asyncio

from indexer.index import run


def main() -> None:
    parser = argparse.ArgumentParser(
        prog="indexer",
        description="Load Goodreads → embed → upsert to the shelf-books namespace.",
    )
    parser.add_argument("--limit", type=int, default=None, help="max books (default: all)")
    parser.add_argument("--batch-size", type=int, default=256)
    parser.add_argument(
        "--dry-run", action="store_true",
        help="load + embed but do not write to the gateway",
    )
    parser.add_argument(
        "--sample", type=int, default=2, help="rows to print in --dry-run",
    )
    args = parser.parse_args()
    asyncio.run(
        run(
            limit=args.limit,
            batch_size=args.batch_size,
            dry_run=args.dry_run,
            sample=args.sample,
        )
    )


if __name__ == "__main__":
    main()
