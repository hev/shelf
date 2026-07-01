from __future__ import annotations

import argparse
import asyncio
import sys
from dataclasses import dataclass
from pathlib import Path
from typing import Any

ROOT = Path(__file__).resolve().parent.parent
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from hevlayer import QueryRequest

from indexer.index import run as run_indexer
from shelf_common.config import Settings
from shelf_common.embed import Embedder
from shelf_common.gateway import FACET_FIELD, close_client, latest_facets, make_client


@dataclass(frozen=True)
class Probe:
    query: str
    expected_route: str


PROBES = [
    Probe("Sanderson", "hybrid_text"),
    Probe("branden sandersn", "hybrid_text"),
    Probe("the name of the wind", "fused"),
    Probe(
        "sprawling epic fantasy with morally grey characters and political intrigue",
        "semantic",
    ),
]

INCLUDE = ["title", "author", "series", "description", "genres", "avg_rating", "num_ratings", "url"]


def _dump_model(value: Any) -> dict | None:
    if value is None:
        return None
    if hasattr(value, "model_dump"):
        return value.model_dump()
    if isinstance(value, dict):
        return value
    return None


def _row_dict(row: Any) -> dict:
    if isinstance(row, dict):
        return row
    if hasattr(row, "model_dump"):
        return row.model_dump()
    return dict(row)


async def _query(layer, settings: Settings, embedder: Embedder, query: str, *, genre: str | None = None) -> dict:
    vector = embedder.embed_query(query)
    request = QueryRequest(
        rank_by=["text", "Auto", query, {"vector": vector}],
        top_k=12,
        include_attributes=INCLUDE,
        include_leg_breakdown=True,
        filters=["genres", "Contains", genre] if genre else None,
    )
    response = await layer.query_namespace(settings.namespace, request)
    return {
        "rows": [_row_dict(row) for row in (response.rows or [])],
        "routing": _dump_model(response.routing),
        "hybrid": _dump_model(response.hybrid),
    }


def _require(condition: bool, message: str, failures: list[str]) -> None:
    if not condition:
        failures.append(message)


async def validate(*, reindex_limit: int | None, batch_size: int) -> int:
    if reindex_limit is not None:
        await run_indexer(limit=reindex_limit, batch_size=batch_size, dry_run=False, sample=0)

    settings = Settings()
    layer = make_client(settings)
    embedder = Embedder(settings.embed_model)
    failures: list[str] = []

    try:
        print(f"gateway={settings.gateway_url} namespace={settings.namespace}")
        for probe in PROBES:
            result = await _query(layer, settings, embedder, probe.query)
            routing = result["routing"] or {}
            rows = result["rows"]
            route = routing.get("route")
            print(f"{probe.query!r}: route={route} rows={len(rows)}")
            _require(route == probe.expected_route, f"{probe.query!r}: expected route {probe.expected_route}, got {route}", failures)
            _require(bool(rows), f"{probe.query!r}: returned no rows", failures)
            _require(routing.get("executed") is True, f"{probe.query!r}: routing.executed was not true", failures)

            if route in {"hybrid_text", "fused"}:
                hybrid = result["hybrid"] or {}
                _require(bool(hybrid), f"{probe.query!r}: missing hybrid echo", failures)

            if route == "fused":
                with_legs = [
                    row
                    for row in rows
                    if isinstance(row.get("$fused"), dict)
                    and isinstance(row["$fused"].get("legs"), list)
                    and row["$fused"]["legs"]
                ]
                _require(bool(with_legs), f"{probe.query!r}: missing $fused.legs despite include_leg_breakdown", failures)

        facets, snapshot = await latest_facets(layer, settings.namespace, field=FACET_FIELD)
        print(f"facet snapshot: facets={len(facets or [])} snapshot_sha={(snapshot or {}).get('sha')}")
        _require(bool(facets), f"{FACET_FIELD}: latest facet snapshot is empty or missing", failures)

        genre = next((f["value"] for f in (facets or []) if f.get("value")), "Fantasy")
        filtered = await _query(layer, settings, embedder, "the name of the wind", genre=genre)
        filtered_rows = filtered["rows"]
        print(f"genre filter {genre!r}: rows={len(filtered_rows)}")
        _require(bool(filtered_rows), f"genres Contains {genre!r}: returned no rows", failures)
        mismatches = [
            row.get("id") or row.get("title")
            for row in filtered_rows
            if genre not in (row.get("genres") or [])
        ]
        _require(not mismatches, f"genres Contains {genre!r}: rows outside genre filter: {mismatches[:5]}", failures)
    finally:
        await close_client(layer)

    if failures:
        print("\nFAILED")
        for failure in failures:
            print(f"- {failure}")
        return 1

    print("\nOK: search-backed Layer cutover validates for routing, fused legs, genre filter, and facet snapshot")
    return 0


def main() -> None:
    parser = argparse.ArgumentParser(description="Validate shelf's hev search cutover through Layer.")
    parser.add_argument(
        "--reindex-limit",
        type=int,
        default=None,
        help="Index this many books through Layer before probing. Use 500 for the PLAN.md spike.",
    )
    parser.add_argument("--batch-size", type=int, default=128)
    args = parser.parse_args()
    raise SystemExit(asyncio.run(validate(reindex_limit=args.reindex_limit, batch_size=args.batch_size)))


if __name__ == "__main__":
    main()
