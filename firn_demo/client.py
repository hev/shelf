from __future__ import annotations

import time
from typing import Any

import httpx


class FirnError(RuntimeError):
    def __init__(self, status_code: int, message: str) -> None:
        self.status_code = status_code
        self.message = message
        super().__init__(f"firn {status_code}: {message}")


class FirnClient:
    """Thin synchronous Firn (firnflow) REST client — the gateway.py analog.

    Covers exactly what the demo needs: upsert (with optional declared schema),
    query (vector / BM25 / hybrid + filter), facets (forward-compatible), index
    builds + operation polling, and a small /metrics reader for the cost panel.
    """

    def __init__(
        self, base_url: str, namespace: str, api_key: str | None = None, timeout: float = 120.0
    ) -> None:
        self.namespace = namespace
        headers = {"Authorization": f"Bearer {api_key}"} if api_key else {}
        self._http = httpx.Client(base_url=base_url.rstrip("/"), headers=headers, timeout=timeout)

    def close(self) -> None:
        self._http.close()

    # ---- write ----
    def upsert(self, rows: list[dict], schema: dict | None = None) -> dict:
        body: dict[str, Any] = {"rows": rows}
        if schema:
            body["schema"] = schema
        return self._post(f"/ns/{self.namespace}/upsert", body)

    def build_vector_index(self) -> dict:
        return self._post(f"/ns/{self.namespace}/index", {})

    def build_fts_index(self) -> dict:
        return self._post(f"/ns/{self.namespace}/fts-index", {})

    def poll_operation(self, op_id: str, timeout: float = 600.0, interval: float = 2.0) -> dict:
        deadline = time.monotonic() + timeout
        while True:
            resp = self._http.get(f"/operations/{op_id}")
            self._raise(resp)
            data = resp.json()
            if data.get("status") in ("succeeded", "failed"):
                return data
            if time.monotonic() > deadline:
                raise TimeoutError(f"operation {op_id} still running after {timeout:.0f}s")
            time.sleep(interval)

    # ---- read ----
    def query(self, body: dict) -> dict:
        return self._post(f"/ns/{self.namespace}/query", body)

    def namespace_info(self) -> dict | None:
        resp = self._http.get(f"/ns/{self.namespace}")
        if resp.status_code == 404:
            return None
        self._raise(resp)
        return resp.json()

    def facet(self, column: str, top_n: int = 14) -> list[dict] | None:
        """Corpus-wide value→count histogram for a column.

        Forward-compatible: returns None (rail hides) when the running Firn build
        has no /facet endpoint yet, or the column isn't a known attribute. The
        rail lights up automatically once the fork's facet work lands.
        """
        try:
            resp = self._http.get(
                f"/ns/{self.namespace}/facet", params={"column": column, "top_n": top_n}
            )
        except httpx.HTTPError:
            return None
        if resp.status_code in (400, 404, 501):
            return None
        self._raise(resp)
        body = resp.json()
        counts = body.get("counts") or body.get("facets") or []
        # normalise to [{value, count}]
        return [
            {"value": c.get("value", c.get("v")), "count": c.get("count", c.get("n"))}
            for c in counts
        ]

    def read_metrics(self) -> dict[str, float]:
        """Sum the few firnflow_* counters for THIS namespace (cost panel)."""
        wanted = {
            "firnflow_s3_requests_total": "s3_requests",
            "firnflow_cache_hits_total": "cache_hits",
            "firnflow_cache_misses_total": "cache_misses",
        }
        out: dict[str, float] = {v: 0.0 for v in wanted.values()}
        try:
            resp = self._http.get("/metrics")
        except httpx.HTTPError:
            return out
        if resp.status_code >= 400:
            return out
        needle = f'namespace="{self.namespace}"'
        for line in resp.text.splitlines():
            if line.startswith("#") or not line.strip():
                continue
            name = line.split("{", 1)[0].split(" ", 1)[0]
            key = wanted.get(name)
            if key is None or needle not in line:
                continue
            try:
                out[key] += float(line.rsplit(" ", 1)[1])
            except (ValueError, IndexError):
                pass
        return out

    # ---- internals ----
    def _post(self, path: str, json: dict) -> dict:
        resp = self._http.post(path, json=json)
        self._raise(resp)
        return resp.json() if resp.content else {}

    @staticmethod
    def _raise(resp: httpx.Response) -> None:
        if resp.status_code >= 400:
            raise FirnError(resp.status_code, resp.text.strip())
