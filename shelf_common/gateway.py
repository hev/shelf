from __future__ import annotations

import asyncio
import time
from typing import Any

from hevlayer import AsyncHevlayer

from .config import Settings

# The single facet field shelf snapshots. Declared declaratively in
# deploy/index.yaml (spec.snapshot.facetFields); materialized imperatively here
# against the shared gateway. avg_rating / num_ratings are continuous, not
# facet-shaped, so they are deliberately excluded.
FACET_FIELD = "genres"

# Explicit schema for the columns tpuf can't (or shouldn't) infer. tpuf infers
# only string / int / bool from the payload:
#  - text: the FTS + fuzzy field the Auto router ranks over (RFC 0022).
#  - avg_rating: float must be declared — inference guesses int from a
#    whole-number rating (4.0) and then rejects the next 4.27.
#  - genres / num_ratings: pinned so the facet columns are stable regardless of
#    which row is seen first.
# The vector column infers from the rows; distance_metric is set on the write.
SCHEMA: dict[str, Any] = {
    "text": {"type": "string", "full_text_search": True, "fuzzy": True},
    # Display-only and often >4 KiB; non-filterable dodges tpuf's 4096-byte
    # filter-value limit (it's still stored, returned, and embedded).
    "description": {"type": "string", "filterable": False},
    "genres": {"type": "[]string"},
    "avg_rating": {"type": "float"},
    "num_ratings": {"type": "int"},
}


def make_client(settings: Settings) -> AsyncHevlayer:
    if not settings.api_key:
        raise SystemExit(
            "No gateway key. Set LAYER_GATEWAY_API_KEY in .env — it's the upstream "
            "Turbopuffer key (1Password: layer-turbopuffer / mesh-staging). "
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
    return await layer.write_namespace(
        namespace,
        {
            "upsert_rows": rows,
            "distance_metric": "cosine_distance",
            "schema": SCHEMA,
        },
    )


async def materialize_facet_snapshot(
    layer: AsyncHevlayer, namespace: str, *, field: str = FACET_FIELD, timeout: float = 180.0
) -> Any:
    """Materialize the facet histogram for `field` and wait for it to land.

    The imperative twin of deploy/index.yaml's `snapshot.facetFields` auto-writer:
    a `source="origin"` snapshot scans the namespace and persists the histogram
    body to S3, which the search backends then read for the genre rail. Used by
    the indexer because shelf doesn't own the Index CR on the shared gateway.
    """
    job = await layer.create_snapshot(namespace, {"field": field, "source": "origin"})
    start = time.monotonic()
    while job.status == "running":
        if time.monotonic() - start > timeout:
            raise TimeoutError(f"snapshot job {job.id} still running after {timeout:.0f}s")
        await asyncio.sleep(1.0)
        job = await layer.get_snapshot_job(namespace, job.id)
    if job.status != "completed":
        raise RuntimeError(f"snapshot job {job.id} {job.status}: {job.error or 'no detail'}")
    return job


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
    top = sorted(column.values, key=lambda v: v.n, reverse=True)[:limit]
    facets = [{"value": v.v, "count": v.n} for v in top]
    provenance = {
        "sha": body.sha,
        "watermark_ms": body.watermark_ms,
        "row_count": body.row_count,
    }
    return facets, provenance
