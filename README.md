# shelf

**[Live demo → shelf.hevlayer.com](https://shelf.hevlayer.com)**

**Book search that shows its routing.** A micro-app over [hev layer](https://hevlayer.com)
whose hero is the routing badge: it shows *how* the gateway searched, not just
the results. Type an author, a title, or a vibe, and watch the same search box
route the query to keyword, semantic, or a fusion of both, and say why.

`shelf` is a UX-shaped demo. Its sibling [hybrid-text-fusion-demo](https://github.com/hev/hybrid-text-fusion-demo)
(SciFact) is eval-shaped: it proves hybrid retrieval with qrels and recall
numbers. `shelf` proves the **query router**: it makes the routing decision
legible on a corpus where the three routes have obviously different intents.

## The hero: one box, three routes

hev layer's `Auto` rank expression ([RFC 0044](https://github.com/hev/layer/blob/main/docs/rfcs/0044-query-router.md))
picks a retrieval strategy from the *shape* of the query and returns the
decision alongside the results. `shelf` renders that decision as a badge.

| Query | Tokens | Route | Why |
|---|---|---|---|
| `Sanderson` | 1 | **hybrid_text** | Short and keyword-shaped — BM25 + fuzzy over the text field, no vector needed. |
| `branden sandersn` | 2 | **hybrid_text** | Same route; the fuzzy legs ([RFC 0057](https://github.com/hev/layer/blob/main/docs/rfcs/0057-hybrid-text-fuzzy-surfacing.md)) still find Brandon Sanderson through the typos. |
| `the name of the wind` | 4 | **fused** | Mid-length — exact-title BM25 *and* semantic, merged by Turbopuffer-native RRF. |
| `sprawling epic fantasy with morally grey characters and political intrigue` | 10 | **semantic** | Long and natural-language — ANN over description embeddings. |

The v1 routing policy keys purely on token count: `≤2 → hybrid_text`,
`3–7 → fused`, `≥8 → semantic`. The four canned chips above are chosen to land
on each route so the badge visibly changes.

## Built on shipped hev layer features

`shelf` reimplements nothing. Fusion, routing, and fuzzy matching all live in
the gateway; the app only composes them:

- **Hybrid text fusion** ([RFC 0022](https://github.com/hev/layer/blob/main/docs/rfcs/archive/0022-hybrid-text-fusion.md)) —
  the `HybridText` expansion: per-token fuzzy legs + a BM25 leg → upstream RRF.
- **Query router** (RFC 0044, phase 1) — the `Auto` expression and its
  routing decision block.
- **Fuzzy surfacing** (RFC 0057) — typo'd queries still return rows.
- **Facet snapshots** — the genre rail is a materialized facet histogram with
  visible provenance (corpus-wide counts, not a tally of the returned rows).
  Declared on the namespace's `Index` CR; see [Declarative config](#declarative-config).

Wire shapes are authoritative in the gateway docs, not here: see the
**Hybrid text fusion** and **Query routing** sections of
`api/query.mdx` in the [layer](https://github.com/hev/layer) repo.

## Dataset

Pinned to [`Eitanli/goodreads`](https://huggingface.co/datasets/Eitanli/goodreads)
on the HuggingFace Hub: an MIT-licensed set of ~10k popular Goodreads books
across genres, at revision `622b9c6`. The indexer loads it from the Hub at that pinned
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
  → indexer/        CLI: load → embed (fastembed bge-small) → upsert + snapshot genres
  → search/app.py   FastAPI (dev): embed query → Auto+vector → rows + routing + hybrid
  → src/worker.js   Cloudflare Worker (prod): same, query embedding via Workers AI bge-small
  → web/static/     vanilla single-page UI: search, route badge, Routing inspector, genre rail
  → deploy/         declarative config: the in-cluster CR bundle the above mirrors
```

The genre rail loads from `/api/facets` (a snapshot read), separate from search.

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
field, which is why `Sanderson` works at all: the author's name is in `text`.
But a book that merely *mentions* Sanderson in its description ranks too. Real
catalogs have several text fields with different query intents (exact author /
exact title / semantic description) that token count alone can't disambiguate.
That gap is **field-aware routing**: the design question this corpus raises,
the way SciFact raised the fuzzy-leg ranking question that RFC 0057
resolved. `shelf` observes it; it does not solve it.

The fused route raises a second one. A `fused` row carries only the aggregate
RRF `$score`, so nothing in the response says which leg found it: keyword (BM25 +
fuzzy) or semantic (ANN). `shelf` reconstructs that client-side by re-issuing the
same input with the route forced to `hybrid_text` and `semantic` and matching ids
back — the per-row **kw**/**sem** rank pills on any fused result. That is enough
to spot a keyword-only rider, but the finer grain (BM25 vs each fuzzy token, in
one round trip, over a shared consistency cut) needs the gateway. Written up to
revive RFC 0021's per-row `$fused.legs`:
[docs/fused-leg-attribution.md](docs/fused-leg-attribution.md).

## Declarative config

hev layer apps get a clean separation: *what the data is* and *how a namespace
behaves* live in config the operator reconciles, not in application code.
`shelf` runs against the shared deployed gateway, so it can't own that cluster —
it configures the gateway imperatively (schema on write, the dataset pin in
`config.py`, a `POST /snapshots` call after indexing). [`deploy/`](deploy/) is
the **declarative equivalent**: the in-cluster CR bundle that same setup would
be, with a one-to-one map back to the imperative paths (see
[`deploy/README.md`](deploy/README.md)). It's the same move the §Dataset section
already makes for the loader and the `Warehouse` kind. The manifests are
illustrative; the runtime paths they mirror are real.

**This is where facet snapshots are declared.** A *snapshot* in hev layer is a
materialized facet histogram — a field's distinct values and their counts,
written durably to S3. You turn one on with
[`Index.spec.snapshot.facetFields`](deploy/index.yaml):

```yaml
spec:
  snapshot:
    facetFields: [genres]   # the genre rail; ratings are continuous, not faceted
    interval: 5m
    retention: never
```

The gateway then re-materializes the genre histogram on each upstream-stable
advance, and the search backends read the latest body to draw the rail —
corpus-wide counts with the snapshot's `sha` and `row_count` shown as
provenance. Against the shared gateway the indexer does the same thing by
calling `create_snapshot(field="genres", source="origin")` once after upserting:
the imperative twin of the auto-writer the CR turns on. Wire shapes are
authoritative in the gateway docs — see
[Snapshot History](https://hevlayer.com/docs/api/snapshots) and the
[Index CRD](https://hevlayer.com/docs/kubernetes/index-crd) in the
[layer](https://github.com/hev/layer) repo.

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

In v1: facet snapshots over the raw `genres` field power the genre rail (see
[Declarative config](#declarative-config)).

Deliberately out of v1:
- **UDF-minted facets.** A genre/mood tagging Function over descriptions
  (cleaner facets than the raw `Genres` tags the snapshot histograms) is a v1.1
  transform-runtime cameo.
- **Field-aware routing.** Observed above; needs a gateway RFC, not demo code.

## License

MIT; see [LICENSE](LICENSE). The Goodreads data is MIT-licensed upstream and is
downloaded at build time, not redistributed here.
