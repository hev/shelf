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

  const gateway = env.LAYER_GATEWAY_URL.replace(/\/+$/, "");
  const url = `${gateway}/v2/namespaces/${env.LAYER_NAMESPACE}/query`;
  const body = JSON.stringify({
    // The Layer-only Auto rank expression: the gateway picks the route from the
    // query's token count and, since a vector is supplied, executes it.
    rank_by: ["text", "Auto", query, { vector }],
    top_k: topK,
    include_attributes: INCLUDE,
    ...(genre ? { filters: ["genres", "Contains", genre] } : {}),
  });
  const headers = { "Content-Type": "application/json" };
  if (env.LAYER_API_KEY) headers["Authorization"] = `Bearer ${env.LAYER_API_KEY}`;

  // Retry transient gateway/edge hiccups (502/503/504). Real 4xx fail immediately.
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
      const rows = data.rows || [];
      return json({
        rows,
        routing: data.routing || null,
        hybrid: data.hybrid || null,
        took_ms: tookMs,
        query,
        genre,
      });
    }
    if (!TRANSIENT.has(resp.status)) return new Response(await resp.text(), { status: resp.status });
    lastDetail = await resp.text();
    if (attempt < 2) await sleep(400 * (attempt + 1));
  }
  return new Response(`gateway error after retries: ${lastDetail}`, { status: 502 });
}
