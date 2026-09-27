# shelf

A public demo on hev layer: book search over ~10k Goodreads books where the
visible artifact is the gateway's **routing decision** (keyword, fused or
semantic), not just the results. It is the text-native routing showcase; the
router is RFC 0044 in `../layer-pro/docs/rfcs/` (hybrid fusion 0022 in
`archive/`, fuzzy legs 0057, HuggingFace `Warehouse` 0053). `README.md` is the
public tour and the user-visible spec.

**This repo is public.** Never put client names, client systems or anything
from a client engagement in code, comments, docs or commit messages.

## You are a Layer customer

shelf exists to use Layer the way a customer would and to report what it
hits. The demo working is table stakes; the report is the deliverable.

- **Reimplement nothing Layer owns:** routing, fusion/RRF, fuzzy matching,
  facet snapshots, scans. The app posts `Auto` queries and renders the
  `routing` / `hybrid` / `$fused.legs` echo. If you're writing fusion math, a
  tokenizer or routing logic, the boundary is wrong.
- **Read the docs, don't invent API.** Shapes are in
  `../layer-pro/site/src/content/docs/api/query.mdx` (the **Hybrid text
  fusion** and **Query routing** sections), `api/snapshots.mdx`,
  `api/scans.mdx`, and `../layer-pro/apps/layer-gateway/openapi.yaml`.
- **Report friction in Linear** (team `layer`): a bug or a wrong or missing
  doc is an issue; a missing capability is an RFC in
  `../layer-pro/docs/rfcs/` with this workload as the motivating case. Old
  follow-ups cite `hev/layer-pro#NNN` GitHub issues; new ones go to Linear.
- **Layer operates itself.** Don't hand-tune scaling or rebuild store indexes
  by hand. If you must intervene to keep the demo up, the intervention gets an
  issue too.

## Run and test

```sh
uv sync --extra search                  # hevlayer comes from ../layer-pro/clients/python
export LAYER_GATEWAY_API_KEY="$(op read op://mesh-staging/layer-turbopuffer/credential)"
uv run python scripts/validate_cutover.py          # read-only probes: 4 routes, $fused.legs, genre filter, facet snapshot
uv run uvicorn search.app:app --reload             # UI + API at http://127.0.0.1:8000
uv run python -m indexer --dry-run --limit 20      # preview records; drop --dry-run to upsert shelf-books
```

There is no unit test suite. `validate_cutover.py` without `--reindex-limit`
only reads; with it, it rewrites that many books first. `node --check
src/worker.js` catches Worker syntax errors.

The gateway key comes from 1Password at run time (on the factory mini the
same item lives in the `layer-factory` vault). Never write it to a `.env`
file or print it. The FastAPI side reads `LAYER_GATEWAY_API_KEY`; the Worker
reads `LAYER_API_KEY`. Gateway: `https://aws-us-east-1.hevlayer.com`,
namespace `shelf-books` (override with `LAYER_GATEWAY_URL` /
`SHELF_NAMESPACE`).

Gotchas:

- **bge is asymmetric.** Index with fastembed `embed_passages`; query with
  `embed_query` (dev) or Workers AI `@cf/baai/bge-small-en-v1.5` plus the
  `"Represent this sentence for searching relevant passages: "` prefix
  (prod). A mismatch silently wrecks the semantic route.
- **Two backends, one UI.** `search/app.py` (dev) and `src/worker.js` (prod)
  must stay in lockstep: `/api/search`, `/api/facets` (no `q`: corpus
  snapshot; with `q`: per-query values scan), `/api/examples`.
- The dataset (`Eitanli/goodreads`, pinned revision `622b9c6`) downloads at
  run time. Never commit it.

## Deploy

- **Web (prod):** a Cloudflare Worker (`src/worker.js`, `wrangler.jsonc`)
  serving `web/static/` at `shelf.hevlayer.com`. There is no CI; deploy by
  hand: `npm install`, `op read op://mesh-staging/layer-turbopuffer/credential
  | npx wrangler secret put LAYER_API_KEY`, `npm run deploy`.
- **Cluster:** shelf doesn't own the shared cluster. `deploy/` is the
  illustrative declarative bundle (VectorStore, Warehouse, Pipeline, Index)
  mirroring what the indexer does imperatively; see `deploy/README.md`. It is
  not applied from this repo.
- **Images**, if you ever build one, go to the mesh-account ECR, never
  `ghcr.io`.

## Scope

v1 is the single-field router over the composed `text` field (`title +
author + description`), ~10k books, genre rail from the raw `genres` field.
Field-aware routing is a documented observation, not demo code. A
UDF-minted genre/mood facet is a v1.1 cameo. If the demo seems to need a new
gateway feature, that's a finding for Linear, not code here.

## State (2026-09-27)

Live at shelf.hevlayer.com. `shelf-books` is on the shared gateway's
`search-store` VectorStore (`kind: search`, confirmed from the gateway's
`"store":"search"` echo). hev search is flagged off on layer-pro main and not a
focus until 2027, so don't grow shelf's dependence on search-specific
behavior; moving the namespace to another store is a Layer team call.

The namespace holds **500 rows, not ~10k** (count scan and snapshot
`row_count` agree), apparently left over from the cutover spike. With no
Sanderson books indexed, the `Sanderson` and `branden sandersn` chips hit a
gateway bug where `HybridText` returns 502 on a token with no BM25 match
(LYR-131). Fused and semantic chips work. A full `uv run python -m indexer`
would restore the corpus; it hasn't been run. hev/layer-pro#134 (cutover
coordination) is still open though the cutover is live.
