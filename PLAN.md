# PLAN — convert `shelf` to hev search behind Layer

Goal: back `shelf` with the **hev search** engine and remove the Turbopuffer
backing, **without** changing what the demo is. shelf still fronts hev layer and
still exists to make the gateway's **query router** legible — only the store
*behind* Layer changes, from Turbopuffer to hev search.

## Decisions (locked)

1. **Architecture — Layer stays in front.** Keep the demo pointed at Layer; flip
   Layer's `VectorStore` from `kind: turbopuffer` to `kind: search`. The router
   badge, fusion, fuzzy, and facet snapshots stay Layer-owned and unchanged. The
   demo never reaches the engine (honors the design-preview contract).
2. **Retire `firn_demo/`.** Delete the direct-to-engine variant (its app-side
   routing heuristic and metadata sidecar were only needed because it bypassed
   Layer). Salvage its Kubernetes engine bring-up, renamed `firnflow → hevsearch`.
3. **Provision on the shared gateway.** shelf doesn't own the shared cluster, so
   the production cutover (a `kind: search` store + repointed `shelf-books`
   `Index`) is a **request to the Layer team**, filed as a design-preview
   coordination. `deploy/` stays illustrative; local dev runs hev search behind a
   local Layer via `docker-compose`.

## Why this is a small change (the findings that ground it)

- **RFC 0086 is shipped, not a design doc.** Layer has a `VectorStore`
  `kind: search` (`HttpSearchClient`) behind the same wire as `kind: turbopuffer`
  (`../layer/apps/layer-gateway/src/vector_store.rs`, `clients/search.rs`).
- **Router, fusion/RRF, fuzzy, and facet snapshots are backend-agnostic** — they
  run in the gateway *above* the store (`routes/query_router.rs`,
  `routes/hybrid_text.rs`, `snapshots.rs`) and behave identically on either
  backend. So shelf's identity (the routing badge = Layer's real decision)
  survives the swap. Every relevant OpenAPI op declares
  `x-hevlayer-store-support: { turbopuffer: supported, search: supported }`.
- **The engine is ready and is canonically `hevsearch`.** Metadata attributes,
  `filter` (DataFusion SQL), and `POST /facet` all shipped in v0.1.0
  (2026-06-29). Crates `hevsearch-*`, wheel `hevsearch`, image `hevsearch-api`,
  env `HEVSEARCH_*`, metrics `hevsearch_*`. The engine does **not** route — the
  caller picks the mode by which fields it sends.
- **One forced behavior change — auth.** `kind: search` **forbids**
  `deriveFromStore`; it requires `inboundAuth.mode: keys` (a scoped inbound key;
  the engine's data token stays upstream-only, never in the browser or `.env`).
  So the bearer key stops meaning "the upstream Turbopuffer key" and becomes "a
  Layer-issued inbound key for `shelf-books`."

---

## Phase 0 — Validation spike (do first; it generates the findings)

Stand up the real thing locally before rewriting docs, so assumptions are
confirmed, not hoped:

- `docker-compose` the `hevsearch-api` (from `../search`) + a local Layer
  configured with a `kind: search` `VectorStore` for `shelf-books`.
- Index a ~500-book slice **through Layer** (`layer.write_namespace` + the inline
  `SCHEMA`), then run the four canned queries.

Confirm — or file a finding for — each:

1. **Echo parity.** Do `routing`, `hybrid`, and `$fused.legs`
   (`include_leg_breakdown`) return identical on `kind: search`? If yes,
   `search/app.py` + the UI are untouched.
2. **`genres` as `[]string`** *(highest risk)*. Does a list column round-trip? Do
   the genre **filter** (`filters=["genres","Contains",genre]`) and the genre-rail
   **snapshot** work over a list through `kind: search`? Engine docs list only
   *scalar* attributes (Int64/Float64/Boolean/Utf8).
3. **Index builds on write.** Turbopuffer indexes implicitly on upsert; hevsearch
   has explicit `/fts-index` + `/index` **admin** ops. Does Layer's `kind: search`
   adapter trigger them on `write_namespace`, or do the semantic/FTS routes return
   empty until an admin build runs?
4. **Schema-on-write translation.** Does Layer translate the inline `SCHEMA`
   (`text` FTS+fuzzy, floats/ints) into hevsearch index creation?

Whatever fails becomes a `hev/search` or `hev/layer` issue/RFC (Phase 5) — that is
the design-preview signal.

## Phase 1 — Auth & config swap (the app-code delta)

- `shelf_common/config.py:16,23` — drop the "key IS the upstream Turbopuffer key"
  comment and the `LAYER_TURBOPUFFER_KEY` alias; the key is now a Layer inbound
  key. Keep `LAYER_GATEWAY_API_KEY` as canonical.
- `shelf_common/gateway.py:17,27,40` — rewrite the `SCHEMA` comments (drop "tpuf
  infers…" and "tpuf's 4096-byte filter-value limit") per Phase 0 reality; update
  the `make_client` error message (no more "1Password: layer-turbopuffer").
- `.env.example`, `.dev.vars.example` — rewrite header comments; point the
  1Password reference at the new inbound-key secret.
- `src/worker.js` / `wrangler.jsonc` — no logic change; only the `LAYER_API_KEY`
  secret's meaning/comment. Verify the Worker still just proxies + injects.
- **Target: `search/app.py` and `web/static/` unchanged** (contingent on Phase 0
  echo parity).

## Phase 2 — Declarative bundle (`deploy/`)

- `deploy/vectorstore.yaml` — replace wholesale: `kind: search`, `endpoint.url` →
  the hevsearch service, `search.adminCredential` + `credential` (data key)
  secretRefs, and **`inboundAuth.mode: keys`** with a scoped `shelf-books` inbound
  key. Rename `turbopuffer-default` → `search-default`.
- `deploy/index.yaml:33` — `storeRef: turbopuffer-default` → `search-default`.
- **Salvage the engine bring-up**: move `firn_demo/deploy/10-firn-engine.yaml`
  (+ namespace/SA) into `deploy/` as the *illustrative* hevsearch engine the
  `VectorStore` points at — renamed `firn→hevsearch`, `FIRNFLOW_*→HEVSEARCH_*`,
  image `firnflow-api→hevsearch-api`. Stays "illustrative, not applied."
- `deploy/README.md` — update the imperative↔declarative map (store kind, auth
  mode).
- **New `docker-compose.yml`** for local dev — `hevsearch-api` + local Layer, so
  `search/app.py` has something to point at offline. (Depends on a local Layer
  image from `../layer` — note as a dependency.)

## Phase 3 — Retire `firn_demo/`

Delete the direct-to-engine variant and its bypass-Layer deploy: `app.py`,
`client.py`, `config.py`, `schema.py`, `index.py`, `__main__.py`, `__init__.py`,
`README.md`, `static/`, `Dockerfile*`, and
`deploy/{20-demo,30-indexer-job,40-ingress,deploy.sh,secret.example,README}`.
Keep only the engine Deployment/Service salvaged in Phase 2. The app-side routing
heuristic and metadata sidecar go away because Layer now routes and stores
metadata.

## Phase 4 — Docs scrub

- `README.md:82` ("One **Turbopuffer** namespace") and `:192` (env comment) → hev
  search backing; the §Namespace-shape and §Architecture prose otherwise stand
  (routing story is unchanged).
- `CLAUDE.md:99,118` and `AGENTS.md:47` — update "The gateway" / Run sections: hev
  search behind Layer, `keys` auth, new secret reference.
- `docs/fused-leg-attribution.md:86` ("Turbopuffer native fusion") is a historical
  finding — add a one-line note that the backend is now hev search; don't rewrite
  history.

## Phase 5 — Paper trail (the real deliverable)

File in the right repos, per the contract:

- **`hev/layer` (coordination/issue):** request provisioning of the `kind: search`
  `VectorStore` + repoint of the `shelf-books` `Index` on the shared
  `aws-us-east-1.hevlayer.com` — the cutover shelf can't do itself. **This gates
  the production flip.**
- **Phase 0 findings** → `hev/search` (engine gaps, e.g. `[]string` attributes) or
  `hev/layer` (translation / index-build gaps), whichever layer breaks.
- **Doc bug:** RFC 0086's preamble says "draft" but the code shipped → `hev/layer`
  issue.
- **Optional RFC:** surface hev search's cache/cost (cold-vs-warm, S3 requests)
  through Layer's echo. This is the one thing `firn_demo`'s cache panel showed that
  Layer hides; retiring it leaves that story on the floor. Motivating case = shelf.

---

## What explicitly does **not** change

Dataset pin + loader, the embedder (bge-small, asymmetric), the two-backends-one-UI
split, the routing policy/badge, the `$fused.legs` attribution, the facet-snapshot
*mechanism* (Layer-owned), and the genre rail's "materialized snapshot, not row
tally" contract.

## Top risks / open questions

1. **`genres` `[]string`** through `kind: search` (rail + filter) — most likely to
   force a temporary rail-hide + a finding.
2. **Index-build trigger** — if Layer doesn't auto-build hevsearch indexes on
   write, the semantic/FTS routes are empty until an admin build; needs a Layer
   answer.
3. **Local Layer image** availability from `../layer` for the docker-compose dev
   loop.
4. **Shared-gateway cutover** is not in this repo's hands — Phase 5's request is
   the critical path to "get rid of Turbopuffer" in production.

## Sequencing

Phase 0 → (1, 2, 3, 4 in parallel) → 5. File the Phase 5 provisioning request
**early** — it is the long pole for the production flip.

## Reference surface (Turbopuffer / firn touch points)

Turbopuffer references to scrub (outside `firn_demo/`):
`.env.example`, `.dev.vars.example`, `shelf_common/config.py`,
`shelf_common/gateway.py`, `deploy/vectorstore.yaml`, `deploy/index.yaml`,
`README.md`, `CLAUDE.md`, `AGENTS.md`, `docs/fused-leg-attribution.md`.

`firn`/`firnflow` references (all inside `firn_demo/`, deleted in Phase 3 except
the salvaged engine YAML): the whole `firn_demo/` tree.
