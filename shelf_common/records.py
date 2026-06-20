from __future__ import annotations

import ast
import math
import re
from dataclasses import dataclass

# Trailing "(Series Name, #3)" suffix on the Book title.
_SERIES_RE = re.compile(r"\s*\(([^()]*#[^()]*)\)\s*$")
# Goodreads numeric id inside the canonical URL: /book/show/2657.To_Kill...
_ID_RE = re.compile(r"/show/(\d+)")


def _clean(value: object) -> str:
    if value is None:
        return ""
    if isinstance(value, float) and math.isnan(value):
        return ""
    return str(value).strip()


def split_series(book: str) -> tuple[str, str | None]:
    """Split "Title (Series, #1)" into ("Title", "Series, #1")."""
    match = _SERIES_RE.search(book or "")
    if not match:
        return (book or "").strip(), None
    return book[: match.start()].strip(), match.group(1).strip()


def parse_genres(raw: object) -> list[str]:
    """Genres arrive as a stringified Python list: "['Fantasy', 'Fiction']"."""
    text = _clean(raw)
    if not text:
        return []
    try:
        value = ast.literal_eval(text)
        if isinstance(value, (list, tuple)):
            return [str(g).strip() for g in value if str(g).strip()]
    except (ValueError, SyntaxError):
        pass
    return [g.strip() for g in text.split(",") if g.strip()]


def parse_num_ratings(raw: object) -> int | None:
    digits = _clean(raw).replace(",", "")
    return int(digits) if digits.isdigit() else None


def parse_rating(raw: object) -> float | None:
    text = _clean(raw)
    try:
        return float(text)
    except ValueError:
        return None


def extract_id(url: str, fallback: str) -> str:
    match = _ID_RE.search(url or "")
    return f"gr-{match.group(1)}" if match else f"gr-{fallback}"


@dataclass(slots=True)
class BookRecord:
    id: str
    title: str
    series: str | None
    author: str
    description: str
    genres: list[str]
    avg_rating: float | None
    num_ratings: int | None
    url: str

    @property
    def text(self) -> str:
        """The composed FTS+fuzzy field the Auto router ranks over.

        Title + author + description so short keyword routes (author/title) and
        the fuzzy legs all have tokens to match. A future field-aware router
        would split these back apart (README § "What this demo teaches").
        """
        return " ".join(p for p in (self.title, self.author, self.description) if p)

    @property
    def embed_text(self) -> str:
        """What the semantic vector is built from — the descriptive signal."""
        return f"{self.title}. {self.description}" if self.description else self.title

    def to_row(self, vector: list[float]) -> dict:
        row: dict = {
            "id": self.id,
            "vector": vector,
            "text": self.text,
            "title": self.title,
            "author": self.author,
            "description": self.description,
            "genres": self.genres,
            "url": self.url,
        }
        if self.series:
            row["series"] = self.series
        if self.avg_rating is not None:
            row["avg_rating"] = self.avg_rating
        if self.num_ratings is not None:
            row["num_ratings"] = self.num_ratings
        return row


def record_from_row(row: dict, index: int) -> BookRecord | None:
    """Build a BookRecord from one raw Eitanli/goodreads CSV row, or None to skip."""
    title_raw = _clean(row.get("Book"))
    if not title_raw:
        return None
    title, series = split_series(title_raw)
    url = _clean(row.get("URL"))
    return BookRecord(
        id=extract_id(url, str(index)),
        title=title,
        series=series,
        author=_clean(row.get("Author")),
        description=_clean(row.get("Description")),
        genres=parse_genres(row.get("Genres")),
        avg_rating=parse_rating(row.get("Avg_Rating")),
        num_ratings=parse_num_ratings(row.get("Num_Ratings")),
        url=url,
    )
