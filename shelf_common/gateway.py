from __future__ import annotations

from typing import Any

from hevlayer import AsyncHevlayer

from .config import Settings

# The one column auto-schema can't infer for us: the FTS + fuzzy text field the
# Auto router ranks over (RFC 0022 requires both flags). The vector column and
# the facet attributes (genres, avg_rating, num_ratings) infer from the rows.
TEXT_SCHEMA: dict[str, Any] = {"type": "string", "full_text_search": True, "fuzzy": True}


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
            "schema": {"text": TEXT_SCHEMA},
        },
    )
