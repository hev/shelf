"""Search backend for shelf-on-Firn — the search/app.py analog, but pointed at
Firn (firnflow) instead of the Layer gateway.

Firn doesn't route, so this backend OWNS the routing decision (a token-count
heuristic → vector / BM25 / hybrid) and emits a `routing` echo the UI renders as
a strategy badge. It also surfaces Firn's own headline artifact — the cold-vs-warm
cache and object-storage request savings — in a cost panel.

Firn stores only id/vector/text today (the fork's metadata-column work, #84 Part 2,
is in progress), so rich result cards are enriched from an app-side metadata
sidecar (id→BookRecord). The genre rail/filter is wired but hides until the fork's
facet endpoint exists. Both light up automatically once the engine gains them.

Run:  uv run python -m uvicorn firn_demo.app:app --reload   (UI + API at http://127.0.0.1:8000)
"""
from __future__ import annotations

import json
import os
import re
import time
from pathlib import Path

from fastapi import FastAPI
from fastapi.responses import FileResponse, JSONResponse
from pydantic import BaseModel

from shelf_common.embed import Embedder
from shelf_common.records import BookRecord
from indexer.dataset import load_books

from firn_demo.client import FirnClient, FirnError
from firn_demo.config import EMBED_DIM, FirnSettings
from firn_demo.schema import firn_id

WEB = Path(__file__).resolve().parent / "static"

settings = FirnSettings()
embedder = Embedder(settings.embed_model)
client = FirnClient(
    settings.firn_url, settings.namespace, settings.firn_api_key, settings.http_timeout_seconds
)

app = FastAPI(title="shelf · search on object storage (Firn)")

# App-side metadata sidecar: u64 id → BookRecord, rebuilt from the same pinned
# dataset the indexer used. Firn has no metadata columns yet, so the app does the
# join the engine can't (RFC 0086 / fork #84 Part 2). Lazy so startup stays fast.
_meta: dict[int, BookRecord] = {}


def _ensure_meta() -> None:
    if _meta:
        return
    for record in load_books(settings, limit=None):
        _meta[firn_id(record)] = record


@app.on_event("startup")
def _warm() -> None:
    # In-cluster (FIRN_WARM_ON_START=1) eagerly load the metadata sidecar so
    # readiness reflects true readiness; local dev keeps it lazy (fast startup).
    if os.environ.get("FIRN_WARM_ON_START"):
        _ensure_meta()


def route_for(query: str) -> tuple[str, list[str]]:
    """The routing decision the gateway used to make, owned here.

    Short/keyword input → BM25 (`keyword`); a few words → hybrid (`fused`);
    a full sentence → ANN over the embedding (`semantic`)."""
    tokens = re.findall(r"\w+", query.lower())
    n = len(tokens)
    route = "keyword" if n <= 2 else ("fused" if n <= 5 else "semantic")
    return route, tokens


def _enrich(result: dict) -> dict:
    """Build a UI card row, preferring Firn-returned attributes (future) and
    falling back to the metadata sidecar (today)."""
    rid = result["id"]
    attrs = result.get("attributes") or {}
    rec = _meta.get(rid)

    def pick(field, rec_default):
        if attrs.get(field) is not None:
            return attrs[field]
        return rec_default

    if rec is None and not attrs:
        # text-only fallback (no sidecar hit): show whatever Firn returned.
        return {"id": rid, "title": f"book {rid}", "author": "", "series": None,
                "description": result.get("text") or "", "genres": [], "avg_rating": None,
                "num_ratings": None, "url": None, "$score": result.get("score"), "source": "text-only"}

    return {
        "id": rid,
        "title": pick("title", rec.title if rec else f"book {rid}"),
        "author": pick("author", rec.author if rec else ""),
        "series": pick("series", rec.series if rec else None),
        "description": pick("description", rec.description if rec else (result.get("text") or "")),
        "genres": pick("genres", rec.genres if rec else []),
        "avg_rating": pick("avg_rating", rec.avg_rating if rec else None),
        "num_ratings": pick("num_ratings", rec.num_ratings if rec else None),
        "url": pick("url", rec.url if rec else None),
        "$score": result.get("score"),
        "source": "firn-attributes" if attrs else "sidecar",
    }


class SearchRequest(BaseModel):
    query: str
    top_k: int = 12
    genre: str | None = None


@app.get("/")
def index() -> FileResponse:
    return FileResponse(WEB / "index.html")


@app.get("/healthz")
def healthz() -> dict:
    return {"status": "ok"}


@app.get("/api/config")
def get_config() -> dict:
    return {"namespace": settings.namespace, "firn_url": settings.firn_url, "dim": EMBED_DIM}


@app.get("/api/examples")
def examples() -> dict:
    path = WEB / "queries.json"
    return json.loads(path.read_text()) if path.exists() else {"examples": []}


@app.get("/api/facets")
def facets() -> dict:
    """Corpus genre rail from Firn's facet endpoint; empty (rail hidden) until the
    fork's facet work lands."""
    try:
        rows = client.facet("genres", top_n=14)
    except FirnError:
        rows = None
    return {"field": "genres", "facets": rows or []}


@app.post("/api/search")
def search(req: SearchRequest) -> JSONResponse:
    query = req.query.strip()
    if not query:
        return JSONResponse({"rows": [], "routing": None, "cache": None})
    _ensure_meta()

    route, tokens = route_for(query)
    body: dict = {"k": max(1, min(req.top_k, 50)), "include_vector": False}
    if route in ("semantic", "fused"):
        body["vector"] = embedder.embed_query(query)
    if route in ("keyword", "fused"):
        body["text"] = query
    if req.genre:
        # Needs the fork's metadata columns; gracefully retried-without below.
        body["filter"] = f"array_has(genres, '{req.genre.replace(chr(39), chr(39) * 2)}')"

    before = client.read_metrics()
    t0 = time.perf_counter()
    genre_applied = bool(req.genre)
    try:
        resp = client.query(body)
    except FirnError as exc:
        if req.genre and exc.status_code == 400:
            # Genre filter not supported on this Firn build yet — fall back.
            body.pop("filter", None)
            genre_applied = False
            resp = client.query(body)
        else:
            return JSONResponse({"error": exc.message}, status_code=exc.status_code)
    took_ms = round((time.perf_counter() - t0) * 1000)
    after = client.read_metrics()

    rows = [_enrich(r) for r in resp.get("results", [])]
    cache = {
        "hit": after["cache_hits"] - before["cache_hits"] >= 1,
        "backend_requests": int(after["s3_requests"] - before["s3_requests"]),
        "took_ms": took_ms,
    }
    routing = {"route": route, "policy": "token-count", "tokens": tokens, "executed": route}
    return JSONResponse({
        "rows": rows,
        "routing": routing,
        "cache": cache,
        "took_ms": took_ms,
        "query": query,
        "genre": req.genre if genre_applied else None,
        "genre_unsupported": bool(req.genre) and not genre_applied,
        "metadata_source": rows[0]["source"] if rows else None,
    })
