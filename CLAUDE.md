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
`Avg_Rating`, `Num_Ratings`, `URL`. On-ramp is the HuggingFace `Warehouse` kind
(RFC 0053) at a **pinned revision** — capture the dataset commit SHA in the
loader. Download at build time; never commit the data (`data/` is gitignored).

## Stack & conventions

- **Python 3.11+**, FastAPI for both `indexer/` and `search/`.
- **`web/`**: a minimal single-page UI. The route badge + facet rail + result
  list are the whole surface — keep it light (vanilla JS or one small framework;
  do not pull in a heavy app framework).
- **Embedder**: `BAAI/bge-small-en-v1.5` (384-d) via sentence-transformers.
  The *same* model must embed both documents (index time) and queries (search
  time) — a mismatch silently wrecks the semantic route.
- **Talking to the gateway**: issue Turbopuffer-compatible queries to the
  deployed gateway; the `Auto` / `HybridText` `rank_by` values are hev layer
  extensions documented in `api/query.mdx`. Namespace: `shelf-books`.

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
- **~10k corpus.** Don't scale to the UCSD subset in v1.
- **No new gateway features.** If the demo seems to need one, that's a finding
  for `../layer`, not code here.

## Naming

In prose the product is **hev layer** (mid-sentence) / **Layer** (sentence
start) — never `Hev Layer` or `HevLayer`. Identifiers stay `hevlayer`
(URLs, metrics). This repo's own name is `shelf`.
