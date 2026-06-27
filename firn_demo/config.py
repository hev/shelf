from __future__ import annotations

from pydantic import Field
from pydantic_settings import BaseSettings, SettingsConfigDict

# BAAI/bge-small-en-v1.5 output dimensionality (same encoder the gateway demo
# uses). Documented here so the schema / query sides agree without loading the model.
EMBED_DIM = 384


class FirnSettings(BaseSettings):
    """Config for the Firn-backed variant. Self-contained so it never imports the
    gateway demo's `shelf_common.config.Settings` (keeps the gateway demo untouched).

    Reads from the same `.env` if present, ignoring unknown keys, so a single
    `.env` can carry both the gateway and Firn settings without collision.
    """

    model_config = SettingsConfigDict(
        env_file=".env", env_file_encoding="utf-8", extra="ignore"
    )

    # Firn (firnflow) server. Local stack default; no auth by default.
    firn_url: str = Field(default="http://localhost:3000", validation_alias="FIRN_URL")
    namespace: str = Field(default="shelf-books", validation_alias="FIRN_NAMESPACE")
    firn_api_key: str | None = Field(default=None, validation_alias="FIRN_API_KEY")

    embed_model: str = Field(
        default="BAAI/bge-small-en-v1.5", validation_alias="FIRN_EMBED_MODEL"
    )
    http_timeout_seconds: float = 120.0

    # Dataset pins — mirror shelf_common.config so the reused `indexer.dataset.load_books`
    # works when handed a FirnSettings (RFC 0053 exact-replay discipline).
    dataset_repo: str = "Eitanli/goodreads"
    dataset_revision: str = "622b9c6b6d960bf75725246986852718cde070b9"
    dataset_split: str = "train"
