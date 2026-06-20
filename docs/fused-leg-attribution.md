# Finding: per-leg result attribution on fused queries

**Status: finding from `shelf` (the query-routing demo). Candidate to revive as
an RFC against `hev/layer`. Not scheduled.** Authored docs-first, in the shape of
a layer RFC, so it can move to `../layer/docs/rfcs/` largely unchanged.

## Summary

A `fused` query returns each row with only the aggregate RRF `$score`. The
client cannot see which leg matched a given row, or at what per-leg rank: the
keyword side (the RFC 0022 `hybrid_text` expansion — one BM25 leg plus one fuzzy
leg per token) and the semantic side (ANN over the query vector) collapse into a
single fused number. This finding proposes reviving per-row leg provenance —
the `$fused.legs` shape RFC 0021 already specced — behind an opt-in flag, so an
application can show and evaluate *why* a row placed where it did.

RFC 0022 cut per-leg provenance deliberately, and left the door open:

> "Per-leg provenance and weighted fusion are gone with [RFC 0021] — upstream's
> fused list does not carry per-leg ranks, and we do not run a parallel fusion
> implementation to recover them. **Revisit only if a design partner needs
> ranking evidence upstream fusion cannot provide.**"
> — `docs/rfcs/archive/0022-hybrid-text-fusion.md:97-101`

`shelf` is that design partner. Its entire job is making the router and the
fusion legible; per-leg attribution is the missing half of that story.

## Motivation

The demo's value is letting a viewer assess the algorithm by eye. Without
per-leg data, a fused result list flattens two very different kinds of hit:

- a row both sides ranked highly (genuinely strong), and
- a row that rode in on a single keyword/fuzzy leg matching a common token.

You cannot tell them apart from `$score` alone. With per-leg ranks the
difference is obvious. Live from the deployed demo, `frodo and the one ring`
(route `fused`, 5 tokens):

| Row | keyword rank | semantic rank | reading |
| --- | --- | --- | --- |
| The Two Towers | 1 | 3 | strong on both sides |
| The Fellowship of the Ring | 7 | 1 | semantic carries it |
| The Lord of the Rings | 9 | 2 | semantic carries it |
| Cry No More | 2 | — | keyword-only rider (matched "no"/"one") |
| Number One Bestseller | 3 | — | keyword-only rider (matched "one") |

The two non-LOTR rows are exactly the failure mode worth seeing: high on the
keyword side via common tokens, absent from the semantic side, yet fused into the
top 5. That is ranking evidence the fused `$score` cannot provide — the precise
case RFC 0022 named.

## What `shelf` does today, and why it is a crutch

To ship the view now without a gateway change, both backends
(`search/app.py:_attribute_legs`, `src/worker.js:attributeLegs`) re-issue the
same input twice with the route forced — `route: "hybrid_text"` and
`route: "semantic"`, the override `api/query.mdx` blesses for "A/B comparison of
strategies on the same input" — then match row ids back to recover per-side
ranks. It is honest observation, not a second fusion. But it is a demo crutch:

- **Three round trips per fused query** instead of one (one routed, two forced).
- **Coarse grain only.** It resolves keyword-side vs semantic-side. It cannot
  split the keyword side into BM25 vs each fuzzy-token leg, because that
  expansion is gateway-internal — the exact grain most useful for tuning fuzzy.
- **No shared consistency cut.** The three calls can read different index
  states; the forced lists are not guaranteed to be the lists the routed query
  actually fused.
- **Approximate membership.** A forced re-run reconstructs each side at a chosen
  depth; it does not observe the gateway's real per-leg `per_leg_limit` cut.

The right home for this is the gateway, where the per-leg ranks already exist at
fusion time.

## Proposed surface (sketch)

An opt-in request flag (placement an open question — a top-level
`include_leg_breakdown: true`, or a third `Auto`/`HybridText` option) turns on
per-row provenance. Each row gains a `$fused.legs` array aligned to the leg order
the `hybrid` echo already reports, reusing RFC 0021's shape verbatim:

```json
{
  "id": "...",
  "$score": 0.0492,
  "$fused": {
    "legs": [
      {"leg": "bm25",          "rank": 4, "score": 11.2},
      {"leg": "fuzzy:frodo",   "rank": 1, "score": 7.4},
      {"leg": "semantic",      "rank": null, "score": null}
    ]
  }
}
```

`null` means the row fell outside that leg's `per_leg_limit` cut — same semantics
RFC 0021 defined. Default off, so payload and cost are unchanged for everyone who
does not ask.

## Implementation note: sharded already has it

The cost is asymmetric, and one path is nearly free:

- **Sharded namespaces.** The gateway already computes the deterministic RRF sum
  over scatter/gathered legs because upstream fusion cannot span shards
  (`docs/rfcs/archive/0022-hybrid-text-fusion.md:102-106`). The per-leg ranks
  exist in that merge; exposing them is plumbing, not new computation.
- **Unsharded namespaces.** Fusion is delegated to Turbopuffer's native
  `rerank_by: ["RRF", ...]`, which returns only fused scores. Per-leg ranks
  require either upstream surfacing them, or the gateway running the legs itself
  when the flag is set (the same legs it already builds, fused locally).

## Non-goals

- **Not on by default.** Cost and payload stay where they are unless requested.
- **Not cross-request scores.** Per-leg scores stay comparable within a response
  only, like `$score` itself.
- **No change to fusion math or routing.** This exposes existing intermediate
  values; it does not reweight or re-rank.

## Open questions

- **Flag placement and name.** Top-level request field vs an `Auto`/`HybridText`
  tuple option. `include_leg_breakdown` is a placeholder.
- **Leg labels.** How to name fuzzy legs (`fuzzy:<token>`?) so the per-token
  legs are distinguishable, and how that interacts with the 15-token cap.
- **Unsharded cost.** Is gateway-side leg execution acceptable on the unsharded
  path when the flag is set, or should it require upstream support first?

## References

- RFC 0021 (withdrawn), the original `$fused.legs` shape:
  `docs/rfcs/archive/0021-fused-queries.md:188-200`.
- RFC 0022, the cut and the standing invitation:
  `docs/rfcs/archive/0022-hybrid-text-fusion.md:97-106`.
- RFC 0044, what `fused` fuses (the hybrid_text expansion **and** the ANN leg):
  `docs/rfcs/0044-query-router.md:109-110`; the `route` override:
  `site/src/content/docs/api/query.mdx` Options table.
- `shelf` workaround: `search/app.py:_attribute_legs`,
  `src/worker.js:attributeLegs`; UI in `web/static/index.html` (`legStrip`).
