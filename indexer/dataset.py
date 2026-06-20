from __future__ import annotations

from collections.abc import Iterator

from shelf_common.config import Settings
from shelf_common.records import BookRecord, record_from_row


def load_books(settings: Settings, limit: int | None = None) -> Iterator[BookRecord]:
    """Stream BookRecords from Eitanli/goodreads at the pinned revision.

    The dataset is a single CSV at the repo root, so it loads as one split.
    """
    from datasets import load_dataset

    dataset = load_dataset(
        settings.dataset_repo,
        revision=settings.dataset_revision,
        split=settings.dataset_split,
    )
    yielded = 0
    for index, row in enumerate(dataset):
        record = record_from_row(row, index)
        if record is None:
            continue
        yield record
        yielded += 1
        if limit is not None and yielded >= limit:
            break
