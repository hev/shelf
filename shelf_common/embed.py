from __future__ import annotations

from fastembed import TextEmbedding


class Embedder:
    """bge-small via fastembed (ONNX, CPU-friendly, no torch).

    bge is asymmetric: passages are embedded as-is, queries get an instruction
    prefix. fastembed's `query_embed` applies that prefix, so the index side
    (`embed_passages`) and the search side (`embed_query`) stay consistent.
    """

    def __init__(self, model_name: str) -> None:
        self.model = TextEmbedding(model_name=model_name)

    def embed_passages(self, texts: list[str]) -> list[list[float]]:
        return [vector.tolist() for vector in self.model.embed(texts)]

    def embed_query(self, text: str) -> list[float]:
        return next(iter(self.model.query_embed(text))).tolist()
