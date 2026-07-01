# deploy/ — the declarative config

`shelf` runs against the **shared deployed gateway** (`aws-us-east-1.hevlayer.com`)
with Layer `keys` inbound auth, so it doesn't own that cluster. It configures the
gateway *imperatively*: the indexer asserts the namespace schema on write, pins
the dataset in `config.py`, and materializes the genre facet snapshot with an API
call.

This directory is the **declarative equivalent** — the Kubernetes CRs the same
setup would be in a full in-cluster hev layer deployment. It's the separation hev
layer apps get for free: *what the data is* and *how the namespace behaves* live
in config the operator reconciles, not in application code. The sibling
[`../shop`](https://github.com/hev) ships the same split (`helm/`,
`indexer/pipelines/*.yaml`).

These manifests are **illustrative, not applied** — `shelf` can't `kubectl apply`
against shared infra. The runtime paths they mirror (schema-on-write, the
`POST /snapshots` materialize, the snapshot read) are real and load-bearing. The
README §Dataset already uses this move for the dataset itself: the imperative HF
loader is "the thing the declarative `Warehouse` kind reads in a cluster."

## Imperative ↔ declarative map

| `shelf` does, imperatively | CR here | What it owns |
|---|---|---|
| `gateway.py:make_client()` — base URL + bearer key | [`vectorstore.yaml`](vectorstore.yaml) | hev search backend connection + Layer scoped-key inbound auth |
| `config.py` — `dataset_repo` / `dataset_revision` | [`warehouse.yaml`](warehouse.yaml) | Upstream source identity (HF Hub) |
| `python -m indexer` — load → embed → upsert | [`pipeline.yaml`](pipeline.yaml) | Staged ingestion: dataset, split, revision, column mapping |
| `gateway.py:materialize_genre_snapshot()` — `create_snapshot(field=genres)` | [`index.yaml`](index.yaml) → `spec.snapshot.facetFields` | **Facet snapshots** + cache / scan / consistency policy |

The one config that stays imperative on **both** sides is the column schema
(`text` → FTS+fuzzy, `description` non-filterable, the float/int facet types).
It's asserted inline and idempotently on every write (`gateway.py:SCHEMA`); the
Index CR carries *operational* policy, not the schema.

## The facet-snapshot answer

A **snapshot** is a materialized facet histogram — per-field distinct values and
counts, durable in S3. You declare which fields get one with
`Index.spec.snapshot.facetFields`. For `shelf` that's just `genres` (ratings are
continuous, not facet-shaped). The gateway then re-materializes the histogram on
each upstream-stable advance; the search backends read the latest body and draw
the genre rail from it — corpus-wide counts with visible provenance (the snapshot
`sha` and `row_count`), not a tally of the 12 returned rows.

Against the shared gateway, `indexer/index.py` does the same thing by calling
`POST /snapshots` (`source: origin`) once after upserting — the imperative twin of
the auto-writer this CR turns on.

See `docs/api/snapshots`, `docs/kubernetes/index-crd`, and the Warehouse /
Pipeline CRD pages in [`../layer`](https://github.com/hev/layer) for the
authoritative shapes.

Production cutover is a Layer-team coordination item: the shared gateway must
provision the `kind: search` `VectorStore` and repoint the `shelf-books` `Index`.
The manifests here document the requested shape; they are not applied from this
repo. Tracking: hev/layer#134. The RFC status doc bug found during the same
conversion is hev/layer#135.

Once the shared gateway is provisioned, run
`uv run python scripts/validate_cutover.py --reindex-limit 500` from the repo
root. That executable check covers the PLAN.md Phase 0 gates: routing echo,
`$fused.legs`, `genres Contains`, facet snapshot body, and schema/index behavior
after writing through Layer.

Current shared-gateway findings from the shelf cutover:

- hev/layer#137 — the Python client rejects the search-backed write response
  shape, so shelf has a narrow compatibility catch while the gateway/client
  shapes are normalized.
- hev/layer#138 — Layer does not build hev search FTS/ANN indexes on write; the
  validation run required a manual `/fts-index` + `/index` intervention.
- hev/layer#139 — facet snapshots fail against search because the origin scan
  asks for `limit=1000`, above search's current max of 500. Until this is fixed,
  `/api/facets` returns an empty rail even though `genres Contains` filtering
  works.
- Auth on the shared gateway still uses the existing default-store-derived
  bearer while `shelf-books` routes to `search-store`; this is acceptable for
  the current shared demo cutover.

## Local compose

[`../docker-compose.yml`](../docker-compose.yml) builds `hevsearch-api` from
`../search` and the Layer gateway from `../layer`. The gateway resolves
`VectorStore` and `Index` CRs from Kubernetes at startup, so compose expects a
local Kubernetes context with the illustrative `shelf` namespace, Secrets, and
CRs applied. The compose file mounts `${HOME}/.kube` read-only and points the
gateway at the `shelf` control-plane namespace.

For a disposable local context with the Layer CRDs already installed:

```bash
kubectl apply -f deploy/local-compose.yaml
docker compose up --build
LAYER_GATEWAY_URL=http://127.0.0.1:8080 \
LAYER_GATEWAY_API_KEY=local-layer-inbound-key \
uv run python scripts/validate_cutover.py --reindex-limit 500
```

`deploy/local-compose.yaml` is intentionally separate from the illustrative
cluster bundle: its `VectorStore.endpoint.url` is `http://hevsearch:3000`, the
Docker Compose service name visible from the gateway container.

This extra Kubernetes dependency is tracked as hev/layer#136; the desired local
developer loop is a gateway mode that can resolve its store/index config without
a Kubernetes control plane.
