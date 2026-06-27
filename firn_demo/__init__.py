"""shelf-on-Firn — a self-contained variant of the shelf demo that runs directly
on Firn (firnflow) instead of the Layer gateway.

Additive and isolated: it reuses shelf's gateway-agnostic pieces
(`shelf_common.embed`, `shelf_common.records`, `indexer.dataset`) read-only and
touches none of the gateway demo's files. See firn_demo/README.md.
"""
