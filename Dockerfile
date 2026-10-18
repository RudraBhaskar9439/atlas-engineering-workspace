FROM node:20-bookworm-slim AS frontend
WORKDIR /frontend
COPY frontend/package*.json ./
RUN npm ci
COPY frontend/ ./
RUN npm run build

FROM ghcr.io/astral-sh/uv:python3.12-bookworm-slim AS runtime
RUN apt-get update && apt-get install -y --no-install-recommends git build-essential && rm -rf /var/lib/apt/lists/*
WORKDIR /app
COPY pyproject.toml uv.lock ./
RUN uv sync --frozen --no-dev
COPY codegraph-requirements.txt ./
RUN uv venv .tools/codegraph --python python3.12 && uv pip install --python .tools/codegraph/bin/python -r codegraph-requirements.txt
RUN .tools/codegraph/bin/python -c "from tree_sitter import Language; import tree_sitter_python; assert callable(Language(tree_sitter_python.language()).query)"
COPY atlas/ atlas/
COPY demo/ demo/
COPY --from=frontend /frontend/dist frontend/dist
EXPOSE 8001
CMD ["uv", "run", "--no-dev", "uvicorn", "atlas.main:app", "--host", "0.0.0.0", "--port", "8001", "--no-access-log"]

FROM runtime AS sandbox
RUN uv pip install --python /app/.venv/bin/python pytest==8.4.2
RUN mkdir /workspace && chmod 755 /workspace
CMD ["/app/.venv/bin/python", "-m", "pytest", "-q"]

FROM runtime AS app
