from __future__ import annotations

import asyncio
import json
import time
from typing import Any

from hevlayer import AsyncHevlayer
from pydantic import ValidationError

from .config import Settings

# The single facet field shelf snapshots. Declared declaratively in
# deploy/index.yaml (spec.snapshot.facetFields); materialized imperatively here
# against the shared gateway. avg_rating / num_ratings are continuous, not
# facet-shaped, so they are deliberately excluded.
FACET_FIELD = "genres"

# Explicit schema for the columns Layer should create consistently on the
# backing search store:
#  - text: the FTS + fuzzy field the Auto router ranks over (RFC 0022).
#  - avg_rating: float must stay a float even when early rows look integral.
#  - genres / num_ratings: pinned so the facet columns are stable regardless of
#    which row is seen first.
# The vector column infers from the rows; distance_metric is set on the write.
SCHEMA: dict[str, Any] = {
    "text": {"type": "string", "full_text_search": True, "fuzzy": True},
    # Display-only; stored and returned, but not filterable.
    "description": {"type": "string", "filterable": False},
    "genres": {"type": "[]string"},
    "avg_rating": {"type": "float"},
    "num_ratings": {"type": "int"},
}


def make_client(settings: Settings) -> AsyncHevlayer:
    if not settings.api_key:
        raise SystemExit(
            "No gateway key. Set LAYER_GATEWAY_API_KEY in .env — it is the "
            "Layer inbound key scoped to shelf-books. "
            "Or run with --dry-run to skip the gateway."
        )
    return AsyncHevlayer(
        api_key=settings.api_key,
        base_url=settings.gateway_url,
        timeout=settings.http_timeout_seconds,
    )


async def close_client(layer: AsyncHevlayer) -> None:
    for name in ("aclose", "close"):
        closer = getattr(layer, name, None)
        if closer is None:
            continue
        result = closer()
        if hasattr(result, "__await__"):
            await result
        return


async def write_books(layer: AsyncHevlayer, namespace: str, rows: list[dict]) -> Any:
    """Upsert a batch of book rows. Schema is sent inline (idempotent) so the
    text field is FTS+fuzzy-indexed and the vector column is cosine."""
    body = {
        "upsert_rows": rows,
        "distance_metric": "cosine_distance",
        "schema": SCHEMA,
    }
    try:
        return await layer.write_namespace(namespace, body)
    except ValidationError as exc:
        missing = {
            ".".join(str(part) for part in error["loc"])
            for error in exc.errors()
            if error.get("type") == "missing"
        }
        if missing == {"message", "rows_affected", "billing"}:
            # kind=search currently returns {"status":"OK"} for writes, while
            # the generated Python client still expects the Turbopuffer write
            # shape. The write already succeeded server-side; keep indexing and
            # track the client mismatch in hev/layer#137.
            return {"status": "OK", "client_parse_warning": "hev/layer#137"}
        raise


async def materialize_facet_snapshot(
    layer: AsyncHevlayer, namespace: str, *, field: str = FACET_FIELD, timeout: float = 180.0
) -> Any:
    """Materialize the facet histogram for `field` and wait for it to land.

    The imperative twin of deploy/index.yaml's `snapshot.facetFields` auto-writer:
    a `source="origin"` snapshot scans the namespace and persists the histogram
    body to S3, which the search backends then read for the genre rail. Used by
    the indexer because shelf doesn't own the Index CR on the shared gateway.
    """
    job = await layer.create_snapshot(namespace, {"field": field, "source": "origin", "page_size": 500})
    start = time.monotonic()
    while job.status == "running":
        if time.monotonic() - start > timeout:
            raise TimeoutError(f"snapshot job {job.id} still running after {timeout:.0f}s")
        await asyncio.sleep(1.0)
        job = await layer.get_snapshot_job(namespace, job.id)
    if job.status != "completed":
        raise RuntimeError(f"snapshot job {job.id} {job.status}: {job.error or 'no detail'}")
    return job


async def query_facets(
    layer: AsyncHevlayer, namespace: str, query: str, *, field: str = FACET_FIELD,
    text_field: str = "text", limit: int = 14, timeout: float = 15.0,
) -> tuple[list[dict], dict]:
    """Per-query facet counts via a values scan (docs: api/scans).

    Scoped by an `fts` selector over the routed text field — the closest scan
    selector to the router's lexical legs. A `hybrid_text` selector would mirror
    the fused route exactly, but kind=search rejects it today (hev/layer#141),
    so fused/semantic queries are approximated by their lexical terms. Unlike
    the snapshot histogram (hev/layer#108), scan values arrive per-element.
    """
    job = await layer.scan(
        namespace,
        {"mode": "values", "field": field, "source": "auto",
         "fts": {"field": text_field, "query": query}},
        timeout=timeout,
    )
    if job.status != "completed":
        raise RuntimeError(f"values scan {job.id} {job.status}: {job.error or 'no detail'}")
    results = await layer.get_scan_results(namespace, job.id)
    facets = [{"value": bucket.v, "count": bucket.n} for bucket in results.values[:limit]]
    provenance = {
        "scan_id": job.id,
        "effective_source": job.effective_source,
        "documents_scanned": job.documents_scanned,
        "unique_values": job.unique_values,
    }
    return facets, provenance


async def latest_facets(
    layer: AsyncHevlayer, namespace: str, *, field: str = FACET_FIELD, limit: int = 14
) -> tuple[list[dict] | None, dict | None]:
    """Read the newest stored facet snapshot for `field`.

    Returns (facets, provenance) where facets is [{value, count}] sorted by count
    desc, or (None, None) when no snapshot body exists yet (the rail then degrades
    to hidden). Two cheap reads: history (newest sha) → snapshot body.
    """
    history = await layer.list_namespace_history(namespace, limit=1)
    if not history:
        return None, None
    body = await layer.get_namespace_snapshot(namespace, history[0].sha)
    column = next((f for f in body.fields if f.name == field), None)
    if column is None:
        return None, None
    counts: dict[str, int] = {}
    for bucket in column.values:
        raw = bucket.v
        if isinstance(raw, str):
            try:
                decoded = json.loads(raw)
            except json.JSONDecodeError:
                decoded = raw
            values = decoded if isinstance(decoded, list) else [raw]
        elif isinstance(raw, list):
            values = raw
        else:
            values = [raw] if raw is not None else []
        for value in values:
            if value:
                genre = str(value)
                counts[genre] = counts.get(genre, 0) + bucket.n
    facets = [
        {"value": value, "count": count}
        for value, count in sorted(counts.items(), key=lambda item: (-item[1], item[0]))[:limit]
    ]
    provenance = {
        "sha": body.sha,
        "watermark_ms": body.watermark_ms,
        "row_count": body.row_count,
    }
    return facets, provenance
