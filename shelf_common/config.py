from __future__ import annotations

from pydantic import AliasChoices, Field
from pydantic_settings import BaseSettings, SettingsConfigDict

# BAAI/bge-small-en-v1.5 output dimensionality. Documented here so the schema /
# query sides agree without loading the model.
EMBED_DIM = 384


class Settings(BaseSettings):
    model_config = SettingsConfigDict(
        env_file=".env", env_file_encoding="utf-8", extra="ignore"
    )

    # Gateway. deriveFromStore auth: the key IS the upstream Turbopuffer key.
    gateway_url: str = Field(
        default="https://aws-us-east-1.hevlayer.com",
        validation_alias=AliasChoices("LAYER_GATEWAY_URL", "HEVLAYER_BASE_URL"),
    )
    api_key: str | None = Field(
        default=None,
        validation_alias=AliasChoices("LAYER_GATEWAY_API_KEY", "LAYER_TURBOPUFFER_KEY"),
    )
    namespace: str = Field(default="shelf-books", validation_alias="SHELF_NAMESPACE")
    embed_model: str = Field(
        default="BAAI/bge-small-en-v1.5", validation_alias="SHELF_EMBED_MODEL"
    )
    http_timeout_seconds: float = 60.0

    # Dataset, pinned to an exact revision (RFC 0053 exact-replay discipline).
    dataset_repo: str = "Eitanli/goodreads"
    dataset_revision: str = "622b9c6b6d960bf75725246986852718cde070b9"
    dataset_split: str = "train"
