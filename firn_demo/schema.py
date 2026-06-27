from __future__ import annotations

from shelf_common.records import BookRecord

# Declared upsert schema, in the shape shelf already sends its gateway backend
# (shelf_common/gateway.py SCHEMA). Forward-compatible: Firn builds WITHOUT the
# fork's "arbitrary scalar columns" work (#84 Part 2) silently ignore the extra
# row keys + this schema and store only id/vector/text; once Part 2 lands, the
# same payload persists these as filterable/returnable attributes and the demo's
# rich cards + genre filter start reading them straight from Firn.
SCHEMA: dict[str, dict] = {
    "text": {"type": "string", "full_text_search": True, "fuzzy": True},
    "title": {"type": "string"},
    "author": {"type": "string"},
    "description": {"type": "string", "filterable": False},
    "series": {"type": "string"},
    "genres": {"type": "[]string"},
    "url": {"type": "string"},
    "avg_rating": {"type": "float"},
    "num_ratings": {"type": "int"},
}

# Attribute columns we want back on query results (when the engine supports them).
ATTRIBUTE_FIELDS = [
    "title", "author", "description", "series", "genres", "url",
    "avg_rating", "num_ratings",
]


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
