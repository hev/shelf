from __future__ import annotations

import itertools
import json
from collections.abc import Iterable, Iterator

from shelf_common.config import Settings
from shelf_common.embed import Embedder
from shelf_common.gateway import (
    FACET_FIELD,
    close_client,
    make_client,
    materialize_facet_snapshot,
    write_books,
)
from indexer.dataset import load_books


def _chunked(items: Iterable, size: int) -> Iterator[list]:
    iterator = iter(items)
    while batch := list(itertools.islice(iterator, size)):
        yield batch


async def run(*, limit: int | None, batch_size: int, dry_run: bool, sample: int) -> None:
    settings = Settings()
    target = "[dry-run, no gateway]" if dry_run else settings.gateway_url
    print(
        f"shelf indexer → namespace={settings.namespace} "
        f"model={settings.embed_model} "
        f"dataset={settings.dataset_repo}@{settings.dataset_revision[:7]} "
        f"→ {target}"
    )

    embedder = Embedder(settings.embed_model)
    layer = None if dry_run else make_client(settings)
    total = 0
    shown = 0
    try:
        for batch in _chunked(load_books(settings, limit=limit), batch_size):
            vectors = embedder.embed_passages([record.embed_text for record in batch])
            rows = [record.to_row(vector) for record, vector in zip(batch, vectors)]

            if dry_run:
                for row in rows:
                    if shown >= sample:
                        break
                    preview = dict(row)
                    preview["vector"] = f"<{len(row['vector'])} floats>"
                    print(json.dumps(preview, ensure_ascii=False, indent=2))
                    shown += 1
            else:
                await write_books(layer, settings.namespace, rows)

            total += len(rows)
            print(f"  {total} books {'embedded' if dry_run else 'indexed'}…")

        # Materialize the genre facet snapshot — the imperative twin of
        # deploy/index.yaml's snapshot.facetFields auto-writer. The search
        # backends read this body to draw the genre rail. Best-effort: a failure
        # here just means the rail degrades to hidden, not a failed index.
        if layer is not None:
            try:
                job = await materialize_facet_snapshot(layer, settings.namespace)
                print(
                    f"{FACET_FIELD} facet snapshot: {job.sha} "
                    f"({job.documents_scanned} docs scanned)"
                )
            except Exception as exc:  # noqa: BLE001 — snapshot is non-fatal
                print(f"{FACET_FIELD} facet snapshot skipped: {exc}")
    finally:
        if layer is not None:
            await close_client(layer)

    print(f"done: {total} books.")
