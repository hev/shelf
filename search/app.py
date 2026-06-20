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
from collections import Counter
from pathlib import Path

from fastapi import FastAPI, HTTPException
from fastapi.responses import FileResponse
from hevlayer import AsyncHevlayer, QueryRequest
from hevlayer.client import HevlayerError
from pydantic import BaseModel

from shelf_common.config import Settings
from shelf_common.embed import Embedder

WEB = Path(__file__).resolve().parent.parent / "web" / "static"
INCLUDE = ["title", "author", "series", "description", "genres", "avg_rating", "num_ratings", "url"]
TRANSIENT = {502, 503, 504}

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


def _facets(rows: list[dict]) -> list[dict]:
    counts: Counter[str] = Counter()
    for row in rows:
        for genre in row.get("genres") or []:
            counts[genre] += 1
    return [{"genre": g, "count": n} for g, n in counts.most_common(14)]


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


@app.post("/api/search")
async def search(req: SearchRequest) -> dict:
    query = req.query.strip()
    if not query:
        return {"rows": [], "routing": None, "hybrid": None, "facets": []}

    # Embed up front and hand the vector to Auto in the 4th tuple slot: the
    # gateway still picks the route from token count, but semantic/fused execute
    # in one hop instead of deferring. (hybrid_text ignores the vector — the
    # wasted embed on keyword queries is what RFC 0044 phase 2 would remove.)
    vector = embedder.embed_query(query)
    body = QueryRequest(
        rank_by=["text", "Auto", query, {"vector": vector}],
        top_k=max(1, min(req.top_k, 50)),
        include_attributes=INCLUDE,
        filters=["genres", "Contains", req.genre] if req.genre else None,
    )

    # Retry transient gateway/edge hiccups (502/503/504), common for a few
    # seconds after a gateway rollout. Real 4xx (e.g. 422) fail immediately.
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
            took_ms = round((time.perf_counter() - start) * 1000)
            rows = resp.rows or []
            return {
                "rows": rows,
                "routing": resp.routing.model_dump() if resp.routing else None,
                "hybrid": resp.hybrid.model_dump() if resp.hybrid else None,
                "facets": _facets(rows),
                "took_ms": took_ms,
                "query": query,
                "genre": req.genre,
            }
        if attempt < 2:
            await asyncio.sleep(0.4 * (attempt + 1))

    raise HTTPException(status_code=502, detail=f"gateway error after retries: {last_detail}")
