/**
 * Cloudflare Worker backend for the shelf query-routing demo.
 *
 * Mirrors search/app.py: serves the single-page UI from ./web/static and
 * proxies search to the Layer gateway, injecting the API key server-side so it
 * never reaches the browser. Unlike the SciFact HybridText demo, shelf's `Auto`
 * router needs a query vector for the semantic/fused routes — so this Worker
 * embeds the query with Workers AI bge-small (the SAME model the indexer uses
 * via fastembed) and hands the vector to Auto, so every route executes in one
 * hop. The response carries the gateway's `routing` + `hybrid` echo blocks.
 *
 * Static assets (index.html, queries.json) are served before this Worker runs;
 * the Worker only handles the /api/* routes below.
 */
import queriesData from "../web/static/queries.json";

const TRANSIENT = new Set([502, 503, 504]);
const INCLUDE = ["title", "author", "series", "description", "genres", "avg_rating", "num_ratings", "url"];
// bge is asymmetric: queries get this instruction prefix (matching the
// indexer's fastembed query_embed); passages are embedded without it. Keep this
// in lockstep with shelf_common/embed.py or the semantic route silently drifts.
const QUERY_PREFIX = "Represent this sentence for searching relevant passages: ";

// The single facet field shelf snapshots (mirrors shelf_common/gateway.py:
// FACET_FIELD and deploy/index.yaml's snapshot.facetFields).
const FACET_FIELD = "genres";
const FACET_TTL_MS = 300_000; // corpus facets are query-independent; cache per-isolate

const sleep = (ms) => new Promise((resolve) => setTimeout(resolve, ms));
const json = (data, status = 200) =>
  new Response(JSON.stringify(data), { status, headers: { "Content-Type": "application/json" } });

// Per-isolate cache for the corpus genre rail read from the facet snapshot.
let facetCache = { at: 0, facets: null, snapshot: null };

export default {
  async fetch(request, env) {
    const { pathname } = new URL(request.url);
    if (pathname === "/api/config") {
      return json({ namespace: env.LAYER_NAMESPACE, field: "text", gateway: env.LAYER_GATEWAY_URL });
    }
    if (pathname === "/api/examples") return json(queriesData);
    if (pathname === "/api/facets") return facets(env);
    if (pathname === "/api/search") {
      if (request.method !== "POST") return new Response("Method not allowed", { status: 405 });
      return search(request, env);
    }
    return new Response("Not found", { status: 404 });
  },
};

// Corpus-wide genre rail from the latest facet snapshot — the JS twin of
// shelf_common/gateway.py:latest_facets(). Two cheap reads (history → body);
// empty (rail hidden) until a snapshot exists. Mirrors search/app.py:facets().
async function facets(env) {
  const now = Date.now();
  if (facetCache.facets && now - facetCache.at <= FACET_TTL_MS) {
    return json({ field: FACET_FIELD, facets: facetCache.facets, snapshot: facetCache.snapshot });
  }
  let result = null;
  let snapshot = null;
  try {
    const gateway = env.LAYER_GATEWAY_URL.replace(/\/+$/, "");
    const ns = env.LAYER_NAMESPACE;
    const headers = {};
    if (env.LAYER_API_KEY) headers["Authorization"] = `Bearer ${env.LAYER_API_KEY}`;
    const hist = await fetch(`${gateway}/v2/namespaces/${ns}/history?limit=1`, { headers });
    const entries = hist.ok ? await hist.json() : [];
    if (Array.isArray(entries) && entries.length) {
      const bodyResp = await fetch(`${gateway}/v2/namespaces/${ns}/snapshots/${entries[0].sha}`, { headers });
      if (bodyResp.ok) {
        const body = await bodyResp.json();
        const field = (body.fields || []).find((f) => f.name === FACET_FIELD);
        if (field) {
          result = [...field.values]
            .sort((a, b) => b.n - a.n)
            .slice(0, 14)
            .map((v) => ({ value: v.v, count: v.n }));
          snapshot = { sha: body.sha, watermark_ms: body.watermark_ms, row_count: body.row_count };
        }
      }
    }
  } catch {
    /* degrade to empty rail */
  }
  if (result) facetCache = { at: now, facets: result, snapshot };
  return json({ field: FACET_FIELD, facets: result || [], snapshot });
}

// One gateway query with transient-error retry; resolves to rows + echo blocks
// or throws an Error carrying { status, detail }. The JS twin of app.py's
// _run_query(). Real 4xx fail immediately; 502/503/504 retry.
async function runQuery(env, rankBy, topK, genre, include) {
  const url = `${env.LAYER_GATEWAY_URL.replace(/\/+$/, "")}/v2/namespaces/${env.LAYER_NAMESPACE}/query`;
  const body = JSON.stringify({
    rank_by: rankBy,
    top_k: Math.max(1, Math.min(topK, 50)),
    include_attributes: include,
    ...(genre ? { filters: ["genres", "Contains", genre] } : {}),
  });
  const headers = { "Content-Type": "application/json" };
  if (env.LAYER_API_KEY) headers["Authorization"] = `Bearer ${env.LAYER_API_KEY}`;

  let lastDetail = "unknown error";
  for (let attempt = 0; attempt < 3; attempt++) {
    let resp;
    const t0 = Date.now();
    try {
      resp = await fetch(url, { method: "POST", headers, body });
    } catch (exc) {
      lastDetail = `gateway unreachable: ${exc}`;
      if (attempt < 2) await sleep(400 * (attempt + 1));
      continue;
    }
    const tookMs = Date.now() - t0;
    if (resp.status === 200) {
      const data = await resp.json();
      return { rows: data.rows || [], routing: data.routing || null, hybrid: data.hybrid || null, took_ms: tookMs };
    }
    if (!TRANSIENT.has(resp.status)) {
      const detail = await resp.text();
      throw Object.assign(new Error(detail), { status: resp.status, detail });
    }
    lastDetail = await resp.text();
    if (attempt < 2) await sleep(400 * (attempt + 1));
  }
  throw Object.assign(new Error(lastDetail), { status: 502, detail: `gateway error after retries: ${lastDetail}` });
}

// Tag each fused row with its keyword-side and semantic-side rank. The JS twin
// of app.py's _attribute_legs(): a fused response carries only the aggregate
// RRF $score, so we re-issue the SAME input twice with the route forced (the
// override the docs bless for "A/B comparison of strategies on the same input")
// and match ids back — observation, not a second fusion. Keyword side = the
// shipped hybrid_text expansion (BM25 + fuzzy); semantic side = the ANN leg.
async function attributeLegs(env, query, vector, genre, base) {
  // Re-run each side at least as deep as the legs the gateway fused, so any row
  // that contributed is found in at least one side's list.
  const depth = Math.max(12, (base.hybrid && base.hybrid.per_leg_limit) || 50);
  const [keyword, semantic] = await Promise.all([
    runQuery(env, ["text", "Auto", query, { vector, route: "hybrid_text" }], depth, genre, []),
    runQuery(env, ["text", "Auto", query, { vector, route: "semantic" }], depth, genre, []),
  ]);
  const kwRank = new Map(keyword.rows.map((r, i) => [r.id, i + 1]));
  const semRank = new Map(semantic.rows.map((r, i) => [r.id, i + 1]));
  for (const r of base.rows) {
    r.legs = { keyword: kwRank.get(r.id) ?? null, semantic: semRank.get(r.id) ?? null };
  }
  return { depth, keyword_total: keyword.rows.length, semantic_total: semantic.rows.length, calls: 2 };
}

async function search(request, env) {
  let req;
  try {
    req = await request.json();
  } catch {
    return json({ rows: [], routing: null, hybrid: null });
  }
  const query = (req.query || "").trim();
  if (!query) return json({ rows: [], routing: null, hybrid: null });
  const topK = Math.max(1, Math.min(Number(req.top_k) || 12, 50));
  const genre = (req.genre || "").trim() || null;

  // Embed up front so semantic/fused execute in one hop instead of deferring.
  let vector;
  try {
    const out = await env.AI.run("@cf/baai/bge-small-en-v1.5", { text: [QUERY_PREFIX + query] });
    vector = out.data[0];
  } catch (exc) {
    return new Response(`embedding failed: ${exc}`, { status: 502 });
  }

  let base;
  try {
    base = await runQuery(env, ["text", "Auto", query, { vector }], topK, genre, INCLUDE);
  } catch (exc) {
    return new Response(exc.detail || String(exc), { status: exc.status || 502 });
  }

  // Only the fused route mixes a keyword side and a semantic side worth
  // attributing; hybrid_text and semantic are single-sided. Degrade silently
  // if attribution fails — the fused list itself still stands.
  const routing = base.routing || {};
  let attribution = null;
  if (routing.route === "fused" && routing.executed !== false) {
    try {
      attribution = await attributeLegs(env, query, vector, genre, base);
    } catch {
      attribution = null;
    }
  }

  return json({
    rows: base.rows,
    routing: base.routing,
    hybrid: base.hybrid,
    took_ms: base.took_ms,
    attribution,
    query,
    genre,
  });
}
