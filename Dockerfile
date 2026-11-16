FROM python:3.12-slim AS base
COPY --from=ghcr.io/astral-sh/uv:latest /uv /usr/local/bin/uv
ENV UV_PROJECT_ENVIRONMENT=/opt/venv \
    UV_COMPILE_BYTECODE=1 \
    PATH=/opt/venv/bin:$PATH \
    PYTHONUNBUFFERED=1
WORKDIR /app
COPY pyproject.toml uv.lock ./

FROM base AS dev
RUN uv sync --frozen
COPY collector/ collector/
COPY tests/ tests/
ENTRYPOINT ["pytest"]

FROM base AS runtime
RUN uv sync --frozen --no-dev
COPY collector/ collector/
RUN useradd --uid 1000 --create-home app
USER app
ENTRYPOINT ["python", "-m", "collector"]
