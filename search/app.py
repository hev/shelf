"""Search backend for shelf — the query-routing book demo on hev layer.

Serves the single-page UI and proxies search to the Layer gateway, injecting the
API key server-side so it never reaches the browser. Unlike the SciFact
HybridText demo (pure text, no embeddings), shelf uses the `Auto` router: the
backend embeds the query so the semantic and fused routes execute in one hop,
and returns the gateway's `routing` + `hybrid` echo blocks so the UI can show
*which* route fired and why.

Run:  uv run --extra search uvicorn search.app:app --reload
"""
from __future__ import annotations

import asyncio
import json
import time
from pathlib import Path

from fastapi import FastAPI, HTTPException
from fastapi.responses import FileResponse
from hevlayer import AsyncHevlayer, QueryRequest
from hevlayer.client import HevlayerError
from pydantic import BaseModel

from shelf_common.config import Settings
from shelf_common.embed import Embedder
from shelf_common.gateway import FACET_FIELD, latest_facets, query_facets

WEB = Path(__file__).resolve().parent.parent / "web" / "static"
INCLUDE = ["title", "author", "series", "description", "genres", "avg_rating", "num_ratings", "url"]
TRANSIENT = {502, 503, 504}
FACET_TTL = 300.0  # corpus facets are query-independent; cache the snapshot read

settings = Settings()
embedder = Embedder(settings.embed_model)
layer = AsyncHevlayer(
    api_key=settings.api_key,
    base_url=settings.gateway_url,
    timeout=settings.http_timeout_seconds,
)

app = FastAPI(title="shelf · query routing on hev layer")


class SearchRequest(BaseModel):
    query: str
    top_k: int = 12
    genre: str | None = None


# Corpus facets come from a materialized snapshot (deploy/index.yaml's
# facetFields), not a tally of the returned rows — so cache the read.
_facet_cache: dict = {"at": 0.0, "facets": None, "snapshot": None}


@app.get("/")
def index() -> FileResponse:
    return FileResponse(WEB / "index.html")


@app.get("/api/config")
def get_config() -> dict:
    return {"namespace": settings.namespace, "field": "text", "gateway": settings.gateway_url}


@app.get("/api/examples")
def examples() -> dict:
    path = WEB / "queries.json"
    return json.loads(path.read_text()) if path.exists() else {"examples": []}


@app.get("/api/facets")
async def facets(q: str | None = None) -> dict:
    """The genre rail, in two modes.

    With `q`, counts are scoped to that search via a values scan (uncached —
    each query is different); the genre filter is deliberately NOT applied, so
    the rail shows where else the query lands. Without `q`, the corpus-wide
    histogram from the latest facet snapshot, cached; empty (rail hidden) until
    a snapshot exists. Either mode degrades to the other's absence gracefully.
    """
    query = (q or "").strip()
    if query:
        try:
            facets, scan = await query_facets(layer, settings.namespace, query)
        except Exception:  # noqa: BLE001 — rail falls back to corpus counts on any error
            return {"field": FACET_FIELD, "facets": None, "scan": None, "query": query}
        return {"field": FACET_FIELD, "facets": facets, "scan": scan, "query": query}
    now = time.monotonic()
    cached = _facet_cache["facets"]
    if cached and now - _facet_cache["at"] <= FACET_TTL:
        return {"field": FACET_FIELD, "facets": cached, "snapshot": _facet_cache["snapshot"]}
    try:
        facets, snapshot = await latest_facets(layer, settings.namespace)
    except Exception:  # noqa: BLE001 — rail degrades to hidden on any error
        facets, snapshot = None, None
    if facets:
        _facet_cache.update(at=now, facets=facets, snapshot=snapshot)
    return {"field": FACET_FIELD, "facets": facets or [], "snapshot": snapshot}


async def _run_query(
    rank_by: list,
    top_k: int,
    genre: str | None,
    include: list[str] = INCLUDE,
    include_leg_breakdown: bool = False,
) -> dict:
    """One gateway query with transient-error retry; returns rows + echo blocks.

    Retries transient gateway/edge hiccups (502/503/504), common for a few
    seconds after a gateway rollout. Real 4xx (e.g. 422) fail immediately.
    """
    body = QueryRequest(
        rank_by=rank_by,
        top_k=max(1, min(top_k, 50)),
        include_attributes=include,
        include_leg_breakdown=include_leg_breakdown,
        filters=["genres", "Contains", genre] if genre else None,
    )
    last_detail = "unknown error"
    for attempt in range(3):
        start = time.perf_counter()
        try:
            resp = await layer.query_namespace(settings.namespace, body)
        except HevlayerError as exc:
            if exc.status_code not in TRANSIENT:
                raise HTTPException(status_code=exc.status_code, detail=exc.message)
            last_detail = exc.message
        else:
            return {
                "rows": resp.rows or [],
                "routing": resp.routing.model_dump() if resp.routing else None,
                "hybrid": resp.hybrid.model_dump() if resp.hybrid else None,
                "took_ms": round((time.perf_counter() - start) * 1000),
            }
        if attempt < 2:
            await asyncio.sleep(0.4 * (attempt + 1))
    raise HTTPException(status_code=502, detail=f"gateway error after retries: {last_detail}")


@app.post("/api/search")
async def search(req: SearchRequest) -> dict:
    query = req.query.strip()
    if not query:
        return {"rows": [], "routing": None, "hybrid": None}

    # Embed up front and hand the vector to Auto in the 4th tuple slot: the
    # gateway still picks the route from token count, but semantic/fused execute
    # in one hop instead of deferring. (hybrid_text ignores the vector — the
    # wasted embed on keyword queries is what RFC 0044 phase 2 would remove.)
    vector = embedder.embed_query(query)
    result = await _run_query(
        ["text", "Auto", query, {"vector": vector}],
        req.top_k,
        req.genre,
        include_leg_breakdown=True,
    )

    attributed_rows = sum(
        1 for row in result["rows"]
        if isinstance(row.get("$fused"), dict) and isinstance(row["$fused"].get("legs"), list)
    )
    result["attribution"] = (
        {
            "source": "gateway",
            "calls": 0,
            "rows": attributed_rows,
            "leg_count": (result.get("hybrid") or {}).get("legs"),
        }
        if attributed_rows
        else None
    )

    result["query"] = query
    result["genre"] = req.genre
    return result
