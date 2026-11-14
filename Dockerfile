# ---- dashboard
FROM node:22-alpine AS web
WORKDIR /web
COPY dashboard/package*.json ./
RUN npm ci --no-audit --no-fund || npm install --no-audit --no-fund
COPY dashboard/ ./
RUN npx vite build

# ---- server
FROM python:3.12-slim
ENV PYTHONUNBUFFERED=1 PIP_NO_CACHE_DIR=1 PORT=8000 \
    TRACELENS_DATABASE_URL=sqlite:////data/tracelens.db
WORKDIR /app
COPY server/pyproject.toml ./server/
COPY server/tracelens_server ./server/tracelens_server
RUN pip install "./server[postgres]"
COPY --from=web /web/dist /app/static
ENV TRACELENS_STATIC_DIR=/app/static
RUN useradd -m app && mkdir -p /data && chown -R app /data /app
USER app
VOLUME ["/data"]
EXPOSE 8000
HEALTHCHECK --interval=30s --timeout=5s CMD python -c "import urllib.request,os;urllib.request.urlopen(f'http://127.0.0.1:{os.environ.get(\"PORT\",8000)}/api/health')"
CMD ["sh", "-c", "uvicorn tracelens_server.main:app --host 0.0.0.0 --port ${PORT} --proxy-headers --forwarded-allow-ips='*'"]
