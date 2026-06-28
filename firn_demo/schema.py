from __future__ import annotations

from shelf_common.records import BookRecord

# Book fields carried as Firn attributes (everything the UI cards show, beyond
# id/vector/text). `genres` is a list → stored as Firn's []string attribute,
# which powers the genre facet rail (count per genre) and the genre filter
# (array_has(genres, '<g>')). The rest are scalars (string/float/int).
# Firn infers each column's type from the values — no schema is declared.
_SCALAR_FIELDS = ["title", "author", "description", "series", "url", "avg_rating", "num_ratings"]


def firn_id(record: BookRecord) -> int:
    """Map shelf's string id (`gr-2657`) to the u64 Firn requires.

    Uses the Goodreads numeric id so the indexer and the search backend's
    metadata sidecar agree on ids without depending on row order. Falls back to a
    stable non-negative 63-bit hash for the rare non-numeric id.
    """
    raw = record.id.removeprefix("gr-")
    if raw.isdigit():
        return int(raw)
    return abs(hash(record.id)) % (2**63)


def firn_row(record: BookRecord, vector: list[float]) -> dict:
    """Shape a BookRecord into a Firn upsert row: id/vector/text at top level,
    everything else nested under `attributes` (the shape Firn's upsert expects)."""
    flat = record.to_row(vector)  # {id:'gr-..', vector, text, title, author, genres, ...}
    attributes: dict = {"genres": record.genres}  # []string → facet + array_has filter
    for field in _SCALAR_FIELDS:
        if field in flat and flat[field] is not None:
            attributes[field] = flat[field]
    return {
        "id": firn_id(record),
        "vector": vector,
        "text": flat["text"],
        "attributes": attributes,
    }
