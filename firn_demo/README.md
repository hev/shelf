# shelf-on-Firn (`firn_demo/`)

A self-contained variant of the `shelf` book demo that runs **directly on
[Firn](https://github.com/hev/firnflow)** (object-storage-backed vector + BM25 +
hybrid search) instead of the Layer gateway. It's **additive**: the gateway demo
(`search/`, `web/`, `shelf_common/`, `src/worker.js`) is untouched. This package
reuses only shelf's gateway-agnostic pieces read-only —
`shelf_common.embed` (bge-small), `shelf_common.records` (`BookRecord`),
`indexer.dataset` (the pinned Goodreads loader).

## What it shows

- **Search on object storage**: vector / BM25 / hybrid over MinIO/S3, with Firn's
  RAM+NVMe cache in front.
- **App-side routing**: Firn doesn't route, so the backend picks the strategy by
  query length — `keyword` (BM25) → `fused` (hybrid, RRF) → `semantic` (ANN) — and
  shows it as a badge (the honest analog of shelf's gateway routing badge).
- **Cache & cost panel** — Firn's headline: per-query latency, warm-hit detection,
  and object-storage requests. Repeat a query to see a warm hit with zero backend
  requests.

## What it does / doesn't mirror (yet)

Firn currently stores only `id + vector + text`. The fork's metadata-column work
(`firnflow` issue #84 Part 2) and facets are in progress, so:

- **Result cards** (title/author/genres/rating/url) are enriched from an **app-side
  metadata sidecar** (`id → BookRecord`, rebuilt from the same pinned dataset) —
  the join Firn can't do yet. When the engine gains metadata columns, the indexer's
  declared `schema` + per-row attributes start persisting, and the backend reads
  them straight from Firn (sidecar becomes a no-op).
- **Genre rail + genre filter** are wired but **hidden** until Firn exposes a
  `/facet` endpoint and `array_has(genres, …)` filtering; they light up
  automatically when it does.

## Run

Requires the Firn stack running locally (MinIO + API on `:3000`).

```bash
uv sync --extra search                            # fastapi + uvicorn (shelf's search extra)
uv run python -m firn_demo --limit 2000           # load Goodreads → embed → upsert → build indexes
uv run python -m uvicorn firn_demo.app:app --reload  # UI + API at http://127.0.0.1:8000
```

Config (env / `.env`, all optional): `FIRN_URL` (default `http://localhost:3000`),
`FIRN_NAMESPACE` (`shelf-books`), `FIRN_API_KEY`, `FIRN_EMBED_MODEL`.
`--limit 0` indexes the whole dataset (~10k); `--dry-run` previews without writing.
