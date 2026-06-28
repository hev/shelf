from __future__ import annotations

import itertools
import json
from collections.abc import Iterable, Iterator

from shelf_common.embed import Embedder
from indexer.dataset import load_books

from firn_demo.client import FirnClient, FirnError
from firn_demo.config import FirnSettings
from firn_demo.schema import firn_id, firn_row


def _chunked(items: Iterable, size: int) -> Iterator[list]:
    iterator = iter(items)
    while batch := list(itertools.islice(iterator, size)):
        yield batch


def run(*, limit: int | None, batch_size: int, dry_run: bool, sample: int) -> None:
    settings = FirnSettings()
    target = "[dry-run, no writes]" if dry_run else f"{settings.firn_url} ns={settings.namespace}"
    print(
        f"firn_demo indexer → {target} "
        f"model={settings.embed_model} "
        f"dataset={settings.dataset_repo}@{settings.dataset_revision[:7]}"
    )

    embedder = Embedder(settings.embed_model)
    client = (
        None
        if dry_run
        else FirnClient(
            settings.firn_url, settings.namespace, settings.firn_api_key, settings.http_timeout_seconds
        )
    )

    total = 0
    shown = 0
    try:
        for batch in _chunked(load_books(settings, limit=limit), batch_size):
            vectors = embedder.embed_passages([record.embed_text for record in batch])
            # {id:u64, vector, text, attributes:{title, author, genres, ...}}
            rows = [firn_row(record, vector) for record, vector in zip(batch, vectors)]

            if dry_run:
                for row in rows:
                    if shown >= sample:
                        break
                    preview = dict(row)
                    preview["vector"] = f"<{len(row['vector'])} floats>"
                    print(json.dumps(preview, ensure_ascii=False, indent=2))
                    shown += 1
            else:
                client.upsert(rows)

            total += len(rows)
            print(f"  {total} books {'embedded' if dry_run else 'upserted'}…")

        if client is not None:
            _build_indexes(client)
    finally:
        if client is not None:
            client.close()

    print(f"done: {total} books.")


def _build_indexes(client: FirnClient) -> None:
    """Build the BM25 (FTS) and vector (IVF_PQ) indexes. FTS is required for the
    keyword/hybrid routes; the vector index is best-effort (brute-force still
    answers vector queries on a small corpus if IVF_PQ build is rejected)."""
    try:
        op = client.build_fts_index()
        result = client.poll_operation(op["operation_id"])
        print(f"fts-index: {result.get('status')}")
    except (FirnError, KeyError, TimeoutError) as exc:
        print(f"fts-index FAILED (BM25/hybrid will error until built): {exc}")

    try:
        op = client.build_vector_index()
        result = client.poll_operation(op["operation_id"])
        print(f"vector index: {result.get('status')}")
    except (FirnError, KeyError, TimeoutError) as exc:
        print(f"vector index skipped (brute-force vector search still works): {exc}")
