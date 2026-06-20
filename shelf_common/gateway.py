from __future__ import annotations

from typing import Any

from hevlayer import AsyncHevlayer

from .config import Settings

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
