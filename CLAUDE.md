# shelf — build context

`shelf` is a standalone demo micro-app for **hev layer**. Its one job is to make
the gateway's **query router** legible: a book search where the visible artifact
is the *routing decision* (keyword / fused / semantic), not just the results.
Read `README.md` first — it is the docs-first spec and the source of truth for
the user-visible shape. This file is for the agent doing the build.

## The one rule

`shelf` **reimplements nothing**. Hybrid fusion, query routing, fuzzy matching,
and RRF all live in the gateway. The app composes shipped features and renders
their output. If you find yourself writing fusion math, a tokenizer, or routing
logic, stop — the gateway already owns it.

## Docs-first, against the layer repo

The gateway is the sibling repo at `../layer` (GitHub `hev/layer`). It is the
authoritative source for every request/response shape. Do **not** invent API:

- Query surface (`Auto` and `HybridText` rank expressions, the routing decision
  block): `../layer/site/src/content/docs/api/query.mdx` — the **Hybrid text
  fusion** and **Query routing** sections.
- Wire-level schema: `../layer/apps/layer-gateway/openapi.yaml`.
- The why behind the features: RFC 0022 (hybrid), 0044 (router), 0057 (fuzzy),
  0053 (HuggingFace `Warehouse`) in `../layer/docs/rfcs/`.

When in doubt about a field name or response shape, read the doc — don't guess.

## Siblings (don't duplicate them)

- `../layer` — the product. Source of truth for API and RFCs.
- `../shop` — the *image-native* showcase (CLIP image embeddings; pipelines,
  document cache, facets, trending). `shelf` is the *text-native* routing
  showcase. Do not re-tell shop's pipeline/cache/trending story here.
- `hev/hybrid-text-fusion-demo` (SciFact) — the *eval-shaped* sibling (qrels,
  recall). `shelf` is *UX-shaped* (the routing badge). No qrels here.

## Dataset

Pinned: [`Eitanli/goodreads`](https://huggingface.co/datasets/Eitanli/goodreads),
MIT, ~10k popular books. Fields: `Book`, `Author`, `Description`, `Genres`,
`Avg_Rating`, `Num_Ratings`, `URL`. The indexer loads from the HF Hub at the
**pinned revision** `622b9c6` (RFC 0053's HuggingFace `Warehouse` kind is the
declarative in-cluster equivalent). Download at run time; never commit the data.

## Stack & conventions

- **Python 3.11+**, managed with `uv`. `indexer/` is a CLI batch job
  (`uv run python -m indexer`); `search/app.py` is a FastAPI service
  (`uv run uvicorn search.app:app`) — the local-dev/reference backend.
- **Two backends, one UI** (the same split as the SciFact demo): `search/app.py`
  (FastAPI, fastembed) for dev, `src/worker.js` (Cloudflare Worker, Workers AI)
  for production. Both proxy to the gateway, inject the key server-side, and
  return the `routing`/`hybrid` echo. Keep them in lockstep.
- **`web/static/`**: a vanilla single-page UI (no framework). The search box,
  route badge, Routing inspector, and genre rail are the whole surface.
- **Embedder**: `BAAI/bge-small-en-v1.5` (384-d). Index with `fastembed`
  `embed_passages`; query with `embed_query` (dev) or Workers AI bge-small +
  the `"Represent this sentence for searching relevant passages: "` prefix
  (prod). bge is asymmetric and the index/query models must match — a mismatch
  silently wrecks the semantic route.
- **Talking to the gateway**: issue Turbopuffer-compatible queries to the
  deployed gateway; the `Auto` / `HybridText` `rank_by` values are hev layer
  extensions documented in `api/query.mdx`. Namespace: `shelf-books`.
- **`deploy/`**: the declarative CR bundle (`VectorStore`, `Warehouse`,
  `Pipeline`, `Index`) — the in-cluster equivalent of what the indexer + schema
  do imperatively. Illustrative, not applied (shelf doesn't own the shared
  cluster). `deploy/README.md` holds the imperative↔declarative map.
- **Facet snapshots**: the genre rail is a materialized facet histogram, *not* a
  tally of the returned rows. Declared declaratively on `Index.spec.snapshot.
  facetFields: [genres]` (`deploy/index.yaml`); materialized imperatively against
  the shared gateway by `materialize_facet_snapshot()` (a `POST /snapshots`,
  `source=origin`) at the end of the indexer run. Both backends serve the rail
  from `/api/facets` (`latest_facets()` → history + body); keep that route in
  lockstep too.

## The gateway

Point at the deployed gateway `https://aws-us-east-1.hevlayer.com`. It uses
`deriveFromStore` auth, so the inbound bearer token **is** the upstream
Turbopuffer API key (1Password: `layer-turbopuffer`, field `credential`, vault
`mesh-staging`). Load it from the environment (`.env`, gitignored). **Never
commit the key.**

## v1 boundaries (hold the line)

- **Single-field router only.** v1 runs the shipped `Auto` over the composed
  `text` field (`title + authors + description`). Field-aware routing is an
  *observation* documented in the README, not demo code.
- **No UDF facet beat in v1.** The genre/mood tagging Function is a v1.1 cameo.
  This is *distinct* from facet **snapshots** over the raw `genres` field, which
  *are* in v1 (a shipped feature, declared on the `Index` CR) and power the genre
  rail. The v1.1 cameo is *minting* cleaner facets with a UDF, not histogramming
  the ones already there.
- **~10k corpus.** Don't scale to the UCSD subset in v1.
- **No new gateway features.** If the demo seems to need one, that's a finding
  for `../layer`, not code here.

## Naming

In prose the product is **hev layer** (mid-sentence) / **Layer** (sentence
start) — never `Hev Layer` or `HevLayer`. Identifiers stay `hevlayer`
(URLs, metrics). This repo's own name is `shelf`.
