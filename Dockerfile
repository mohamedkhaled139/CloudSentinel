# syntax=docker/dockerfile:1
# ---- Build the web console ------------------------------------------------------------------------
FROM node:26-alpine AS frontend
WORKDIR /src
COPY frontend/package.json frontend/package-lock.json ./
RUN npm ci --no-audit --no-fund
COPY frontend/ ./
RUN npm run build

# ---- Build the Python wheel -----------------------------------------------------------------------
FROM python:3.12-slim AS backend-build
WORKDIR /src
RUN pip install --no-cache-dir build
COPY backend/ ./
RUN python -m build --wheel --outdir /wheels

# ---- Runtime --------------------------------------------------------------------------------------
FROM python:3.12-slim
LABEL org.opencontainers.image.title="CloudSentinel" \
      org.opencontainers.image.description="Self-hosted infrastructure health and availability intelligence" \
      org.opencontainers.image.licenses="MIT" \
      org.opencontainers.image.source="https://github.com/mohamedkhaled139/CloudSentinel"

ENV PYTHONDONTWRITEBYTECODE=1 \
    PYTHONUNBUFFERED=1 \
    CS_STATIC_DIR=/app/static \
    CS_DATABASE_URL=sqlite:////data/cloudsentinel.db

COPY --from=backend-build /wheels /wheels
RUN pip install --no-cache-dir "$(ls /wheels/*.whl)[postgres]" && rm -rf /wheels \
    && useradd --system --uid 10001 --home /data cloudsentinel \
    && mkdir -p /data && chown cloudsentinel /data
COPY --from=frontend /src/dist /app/static
COPY agent/cloudsentinel_agent.py /app/agent/cloudsentinel_agent.py

USER cloudsentinel
WORKDIR /data
EXPOSE 8000
HEALTHCHECK --interval=30s --timeout=5s --start-period=20s --retries=3 \
  CMD python -c "import urllib.request,sys; sys.exit(0 if urllib.request.urlopen('http://127.0.0.1:8000/healthz', timeout=4).status==200 else 1)"
CMD ["cloudsentinel", "serve", "--host", "0.0.0.0", "--port", "8000"]
