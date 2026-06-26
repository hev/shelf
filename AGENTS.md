# shelf — Agent Guide

Engineering and operations guide for `shelf`, the book-search demo that makes hev
layer's **query router** legible. For the build context and conventions read
`CLAUDE.md`; for the docs-first user-visible shape read `README.md`.

## ⚠️ IMPORTANT — this repo is a Layer design-preview customer

This repo is a **design-preview customer of hev layer**, not part of the Layer
product. Its job is to *use* Layer the way a real customer would and **report
back** to the Layer team. That feedback loop is a primary responsibility of this
repo, not a side task — the demo working is table stakes; the signal we send the
Layer team is the deliverable.

**When you hit friction, do not fix Layer from here — report it:**

- **A bug, or docs that are wrong / unclear / missing** → file a **GitHub issue**
  on the Layer repo (`hev/layer`) with a minimal repro and the exact page or
  behavior at fault.
- **A missing feature or capability gap** → open an **RFC** in the Layer repo
  (`../layer/docs/rfcs/`), in the existing RFC shape, with this workload as the
  motivating / acceptance case.

**Operations are Layer's job.** This repo has operational access to the shared
Layer cluster, but the goal is that Layer operates *itself* — autoscaling,
scale-to-zero, scheduling, binpacking. Let it. Do **not** hand-tune what Layer is
meant to manage.

- When Layer falls short — autoscaling lags, a pipeline stalls, scale-to-zero
  misbehaves — it is OK to **intervene** to keep the demo healthy. But every
  intervention **must** produce a GitHub issue (bug) or an RFC (missing
  capability). An undocumented manual fix is a process failure: the intervention
  is the symptom, the report is the deliverable.
- **Shared namespace / binpacking.** This repo deploys to a namespace in the
  shared demo cluster alongside the other demos (shelf, shop, chart,
  hybrid-text-fusion-demo, label). Scheduling / binpacking contention may bite.
  Same rule: intervene to stay healthy if you must, but the result is a GH issue
  or an RFC documenting the shortfall — never a silent workaround.

The deliverable of any friction is always a **paper trail in `hev/layer`** (issue
or RFC) so the design-preview signal reaches the Layer team.

## Run & gateway

```bash
uv sync --extra search
cp .env.example .env                     # LAYER_GATEWAY_API_KEY = upstream Turbopuffer key
uv run python -m indexer                 # populate shelf-books (~10k); --dry-run to preview
uv run uvicorn search.app:app --reload   # UI + API at http://127.0.0.1:8000
```

Gateway: `https://aws-us-east-1.hevlayer.com` (`deriveFromStore`; key from `.env`,
never committed). Namespace: `shelf-books`. Production is a Cloudflare Worker
(`src/worker.js`): `npm install`, `wrangler secret put LAYER_API_KEY`,
`npm run deploy`. Keep the FastAPI and Worker backends in lockstep (CLAUDE.md).

## Agent rules

- **Reimplement nothing** the gateway owns (routing, fusion, fuzzy, snapshots).
  A gap is a finding for `../layer`, not local code (see the contract above).
- Prefer the deployed gateway / public paths for checks.
- Don't commit secrets or the dataset; don't revert unrelated user changes.
