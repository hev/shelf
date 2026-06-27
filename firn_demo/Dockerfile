# Container for the shelf-on-Firn demo app (firn_demo).
#
# Build context is the shelf repo ROOT (the app reuses shelf_common/ + indexer/):
#   docker build -f firn_demo/Dockerfile -t shelf-firn-demo .
# BuildKit honors firn_demo/Dockerfile.dockerignore so .venv/.git/node_modules
# stay out of the context.
#
# Bakes the bge-small encoder + the pinned Goodreads dataset into the image so
# pods are self-sufficient (no HF fetch at runtime) — mirrors shop baking CLIP.

FROM python:3.12-slim

ENV PYTHONUNBUFFERED=1 \
    HF_HOME=/opt/model-cache \
    HF_HUB_DISABLE_TELEMETRY=1

WORKDIR /app

RUN apt-get update \
    && apt-get install -y --no-install-recommends ca-certificates \
    && rm -rf /var/lib/apt/lists/*

# Runtime deps (the firn demo does NOT use the hevlayer gateway SDK).
RUN pip install --no-cache-dir \
    "fastapi>=0.115" "uvicorn[standard]>=0.30" "httpx>=0.27" \
    "fastembed>=0.4" "datasets>=3.0" "pydantic>=2.7" "pydantic-settings>=2.3"

# App code: the firn demo + the gateway-agnostic shelf pieces it reuses read-only.
COPY shelf_common/ ./shelf_common/
COPY indexer/ ./indexer/
COPY firn_demo/ ./firn_demo/

# Bake the encoder + dataset into the image cache (HF_HOME).
RUN python -c "from shelf_common.embed import Embedder; Embedder('BAAI/bge-small-en-v1.5')" \
 && python -c "from firn_demo.config import FirnSettings; from indexer.dataset import load_books; n=len(list(load_books(FirnSettings(), limit=None))); print(f'baked {n} books')"

EXPOSE 8000
CMD ["python", "-m", "uvicorn", "firn_demo.app:app", "--host", "0.0.0.0", "--port", "8000"]
