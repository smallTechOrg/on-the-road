FROM python:3.12-slim

# uv for dependency management
COPY --from=ghcr.io/astral-sh/uv:latest /uv /uvx /usr/local/bin/

WORKDIR /app

COPY pyproject.toml uv.lock ./
RUN uv sync --frozen --no-install-project --no-dev

COPY src ./src
RUN uv sync --frozen --no-dev

# Hermes agent lives in /opt/hermes (installed at deploy time or mounted);
# config/keys/session DB come from the ~/.hermes volume.
RUN mkdir -p /opt/hermes /data

ENV ONTHEROAD_DB_PATH=/data/ontheroad.db \
    ONTHEROAD_PORT=8100

EXPOSE 8100

CMD ["uv", "run", "--no-sync", "uvicorn", "ontheroad.main:app", "--host", "0.0.0.0", "--port", "8100"]
