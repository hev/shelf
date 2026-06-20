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
from shelf_common.gateway import FACET_FIELD, latest_facets

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
async def facets() -> dict:
    """Corpus-wide genre rail, read from the latest facet snapshot. Query-
    independent, so it's cached; empty (rail hidden) until a snapshot exists."""
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
    rank_by: list, top_k: int, genre: str | None, include: list[str] = INCLUDE
) -> dict:
    """One gateway query with transient-error retry; returns rows + echo blocks.

    Retries transient gateway/edge hiccups (502/503/504), common for a few
    seconds after a gateway rollout. Real 4xx (e.g. 422) fail immediately.
    """
    body = QueryRequest(
        rank_by=rank_by,
        top_k=max(1, min(top_k, 50)),
        include_attributes=include,
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


async def _attribute_legs(query: str, vector: list, genre: str | None, base: dict) -> dict:
    """Tag each fused row with its keyword-side and semantic-side rank.

    A `fused` response carries only the aggregate RRF `$score` — the gateway does
    not expose which leg matched each row (RFC 0022 cut per-leg provenance). So
    we re-issue the SAME input twice with the route forced — the override the
    docs bless for "A/B comparison of strategies on the same input" — and match
    ids back. This is observation, not a second fusion: the keyword side is the
    shipped `hybrid_text` expansion (BM25 + fuzzy), the semantic side is the ANN
    leg. Splitting the keyword side finer (BM25 vs each fuzzy token) needs the
    gateway — see the leg-attribution finding for ../layer.
    """
    # Re-run each side at least as deep as the legs the gateway fused, so any row
    # that contributed is found in at least one side's list.
    depth = max(12, (base.get("hybrid") or {}).get("per_leg_limit") or 50)
    keyword, semantic = await asyncio.gather(
        _run_query(["text", "Auto", query, {"vector": vector, "route": "hybrid_text"}], depth, genre, include=[]),
        _run_query(["text", "Auto", query, {"vector": vector, "route": "semantic"}], depth, genre, include=[]),
    )
    kw_rank = {r["id"]: i + 1 for i, r in enumerate(keyword["rows"])}
    sem_rank = {r["id"]: i + 1 for i, r in enumerate(semantic["rows"])}
    for row in base["rows"]:
        row["legs"] = {"keyword": kw_rank.get(row["id"]), "semantic": sem_rank.get(row["id"])}
    return {"depth": depth, "keyword_total": len(keyword["rows"]),
            "semantic_total": len(semantic["rows"]), "calls": 2}


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
    result = await _run_query(["text", "Auto", query, {"vector": vector}], req.top_k, req.genre)

    # Only the fused route mixes a keyword side and a semantic side worth
    # attributing; hybrid_text and semantic are single-sided. Degrade silently
    # if attribution fails — the fused list itself still stands.
    routing = result.get("routing") or {}
    result["attribution"] = None
    if routing.get("route") == "fused" and routing.get("executed", True):
        try:
            result["attribution"] = await _attribute_legs(query, vector, req.genre, result)
        except (HTTPException, HevlayerError):
            result["attribution"] = None

    result["query"] = query
    result["genre"] = req.genre
    return result
