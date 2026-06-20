# shelf

**Book search that shows its routing.** A micro-app over [hev layer](https://hevlayer.com)
where the hero isn't the results — it's the badge that tells you *how* the
gateway searched. Type an author, a title, or a vibe, and watch the same search
box route the query to keyword, semantic, or a fusion of both, and say why.

`shelf` is a UX-shaped demo. Its sibling [hybrid-text-fusion-demo](https://github.com/hev/hybrid-text-fusion-demo)
(SciFact) is eval-shaped — it proves hybrid retrieval with qrels and recall
numbers. `shelf` proves the **query router**: it makes the routing decision
legible on a corpus where the three routes have obviously different intents.

## The hero: one box, three routes

hev layer's `Auto` rank expression ([RFC 0044](https://github.com/hev/layer/blob/main/docs/rfcs/0044-query-router.md))
picks a retrieval strategy from the *shape* of the query and returns the
decision alongside the results. `shelf` renders that decision as a badge.

| Query | Tokens | Route | Why |
|---|---|---|---|
| `Sanderson` | 1 | **hybrid_text** | Short and keyword-shaped — BM25 + fuzzy over the text field, no vector needed. |
| `branden sandersn` | 2 | **hybrid_text** | Same route; the fuzzy legs ([RFC 0057](https://github.com/hev/layer/blob/main/docs/rfcs/0057-hybrid-text-fuzzy-surfacing.md)) still surface Brandon Sanderson through the typos. |
| `the name of the wind` | 4 | **fused** | Mid-length — exact-title BM25 *and* semantic, merged by Turbopuffer-native RRF. |
| `sprawling epic fantasy with morally grey characters and political intrigue` | 10 | **semantic** | Long and natural-language — ANN over description embeddings. |

The v1 routing policy keys purely on token count: `≤2 → hybrid_text`,
`3–7 → fused`, `≥8 → semantic`. The four canned chips above are chosen to land
on each route so the badge visibly changes.

## Built on shipped hev layer features

`shelf` reimplements nothing — fusion, routing, and fuzzy matching all live in
the gateway. The app only composes them:

- **Hybrid text fusion** ([RFC 0022](https://github.com/hev/layer/blob/main/docs/rfcs/archive/0022-hybrid-text-fusion.md)) —
  the `HybridText` expansion: per-token fuzzy legs + a BM25 leg → upstream RRF.
- **Query router** (RFC 0044, phase 1) — the `Auto` expression and its
  routing decision block.
- **Fuzzy surfacing** (RFC 0057) — typo'd queries still return rows.

Wire shapes are authoritative in the gateway docs, not here: see the
**Hybrid text fusion** and **Query routing** sections of
`api/query.mdx` in the [layer](https://github.com/hev/layer) repo.

## Dataset

Pinned to [`Eitanli/goodreads`](https://huggingface.co/datasets/Eitanli/goodreads)
on the HuggingFace Hub — **MIT-licensed**, ~10k popular Goodreads books across
genres, at revision `622b9c6`. The indexer loads it from the Hub at that pinned
revision and upserts through the gateway — the same source hev layer's
HuggingFace `Warehouse` kind
([RFC 0053](https://github.com/hev/layer/blob/main/docs/rfcs/0053-huggingface-warehouse-kind.md))
reads declaratively in a full cluster deployment. The data is downloaded at run
time, never committed.

Fields used:

| Source field | Use |
|---|---|
| `Book` | title (series suffix in parens split into `series`) |
| `Author` | author — powers the `Sanderson` route |
| `Description` | semantic leg (embedded) |
| `Genres` | genre facet |
| `Avg_Rating` | rating facet |
| `Num_Ratings` | popularity facet / sort |
| `URL` | result link |

Being ~10k *popular* books is deliberate: the canned queries are guaranteed to
hit recognizable titles, and it's cheap to index for a UX demo. The dataset
carries no publication-year / page-count / language fields; if a richer facet
rail is ever wanted, swap in the [UCSD Book Graph](https://mengtingwan.github.io/data/goodreads)
"Fantasy & Paranormal" subset (258,585 books, full fields) under its academic
terms via the same loader-not-redistribute pattern.

## Namespace shape

One Turbopuffer namespace (`shelf-books`) behind the gateway:

| Attribute | Index | Role |
|---|---|---|
| `id` | key | book id |
| `text` | FTS + fuzzy | composed `title + authors + description`; the field `Auto` ranks over |
| `vector` | ANN (384-d) | embedding of `title + ". " + description` |
| `title`, `authors` | filterable (FTS) | display; reachable by a future field-aware router |
| `description` | — | display |
| `genres` | filterable | genre facet |
| `avg_rating` | filterable | rating facet |
| `num_ratings` | filterable | popularity facet / sort |
| `url` | — | result link |

Embedder: `BAAI/bge-small-en-v1.5` (384-d, CPU-friendly). The same model embeds
the query at search time so semantic/fused routes resolve in one hop (see below).

## Architecture

```
Eitanli/goodreads (HF, pinned 622b9c6)
  → indexer/        CLI: load → embed (fastembed bge-small) → upsert to shelf-books
  → search/app.py   FastAPI (dev): embed query → Auto+vector → rows + routing + hybrid + facets
  → src/worker.js   Cloudflare Worker (prod): same, query embedding via Workers AI bge-small
  → web/static/     vanilla single-page UI: search, route badge, Routing inspector, genre rail
```

Two backends, one UI — the same split as the SciFact demo (`server.py` +
`src/worker.js`). The FastAPI service is the local-dev/reference path (fastembed,
identical to the indexer's embeddings); the Cloudflare Worker is the production
deploy and embeds queries with Workers AI `@cf/baai/bge-small-en-v1.5` — the same
model, with bge's query-instruction prefix applied so the vectors match the
index. Both inject the gateway key server-side and return the gateway's
`routing`/`hybrid` echo blocks; the UI renders them in the Routing inspector.

### The one-hop embedding note

RFC 0044 phase 1 says the **gateway never embeds**. So for the semantic and
fused routes the app supplies the query vector itself: `search/` embeds every
query with bge-small and sends the vector alongside the `Auto` expression, so
the router resolves in a single round trip on any route. The only cost is a
wasted embed on short keyword queries that route to `hybrid_text` and never use
the vector — exactly the inefficiency RFC 0044 **phase 2** (gateway-side query
embedding, currently unscheduled) would remove. `shelf` lives happily on
phase 1; phase 2 is a footnote, not a blocker.

### What this demo teaches (forward note, not built)

`shelf` runs the shipped **single-field** router over the composed `text`
field, which is why `Sanderson` works at all — the author's name is in `text`.
But a book that merely *mentions* Sanderson in its description ranks too. Real
catalogs have several text fields with different query intents (exact author /
exact title / semantic description) that token count alone can't disambiguate.
That gap — **field-aware routing** — is the design question this corpus
surfaces, the way SciFact surfaced the fuzzy-leg ranking question RFC 0057
resolved. `shelf` observes it; it does not solve it.

## Run it

```bash
uv sync --extra search                 # install deps
cp .env.example .env                   # add LAYER_GATEWAY_API_KEY (the upstream Turbopuffer key)
uv run python -m indexer               # populate shelf-books (~10k books); --dry-run to preview
uv run uvicorn search.app:app --reload # serve UI + API at http://127.0.0.1:8000
```

Production is a Cloudflare Worker (mirrors the SciFact demo): `npm install`, set
the key with `wrangler secret put LAYER_API_KEY`, then `npm run deploy`.

## Status

v1 scope: the routing showcase above, on the shipped gateway, ~10k books.

Deliberately out of v1:
- **UDF-minted facets.** A genre/mood tagging Function over descriptions
  (cleaner facets than raw `Genres` tags) is a v1.1 transform-runtime cameo.
- **Field-aware routing.** Observed above; needs a gateway RFC, not demo code.

## License

MIT — see [LICENSE](LICENSE). The Goodreads data is MIT-licensed upstream and is
downloaded at build time, not redistributed here.
