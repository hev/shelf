from __future__ import annotations

import argparse

from firn_demo.index import run


def main() -> None:
    parser = argparse.ArgumentParser(
        prog="firn_demo",
        description="Load Goodreads → embed → upsert into a Firn namespace, then build indexes.",
    )
    parser.add_argument("--limit", type=int, default=2000, help="max books (default: 2000; None-like 0 = all)")
    parser.add_argument("--batch-size", type=int, default=128)
    parser.add_argument("--dry-run", action="store_true", help="load + embed but do not write to Firn")
    parser.add_argument("--sample", type=int, default=2, help="rows to print in --dry-run")
    args = parser.parse_args()
    limit = None if args.limit in (0, -1) else args.limit
    run(limit=limit, batch_size=args.batch_size, dry_run=args.dry_run, sample=args.sample)


if __name__ == "__main__":
    main()
