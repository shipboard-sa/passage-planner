# Purpose: Container image for the hylas-routing passage-briefing GUI (the internal
#   service vessel-ops proxies /routing to).
# Version: v1 (2026-09-24)
# Scope: PRODUCTION - built by CI for the vessel-ops stack on boat-automation (not yet deployed).
# Architecture: hylas-routing subsystem. Stage 1 builds frontend/dist with Node; stage 2 is
#   python:3.13-slim-bookworm running backend.main:app on :8100. config/ and var/ are NOT
#   in the image - the stack mounts them (crew roster etc. never land in a registry).
#   See mx-inv design/DOCKER_DEPLOYMENT_SPEC.md (v5).

# ---- Frontend build ----
FROM node:24-slim AS web
WORKDIR /web
COPY frontend/package.json frontend/package-lock.json ./
RUN npm ci --no-fund --no-audit
COPY frontend/ ./
RUN npm run build

# ---- Runtime ----
FROM python:3.13-slim-bookworm
ENV PYTHONUNBUFFERED=1 \
    PYTHONDONTWRITEBYTECODE=1 \
    PIP_NO_CACHE_DIR=1 \
    HOME=/tmp
# Mirrors the laptop's sibling layout so every relative path in the code
# (../mx-inv/vessel_local.db, config/, var/) resolves unchanged.
WORKDIR /srv/code/routing
COPY pyproject.toml ./
COPY src/ src/
COPY data/ data/
COPY backend/ backend/
# Editable install: arrival.py / consular.py locate data/ and var/ via parents[2].
RUN pip install -e ".[web]"
COPY --from=web /web/dist frontend/dist
# Mount points (the stack bind-mounts real data over these).
RUN mkdir -p config var /srv/code/mx-inv
EXPOSE 8100
HEALTHCHECK --interval=30s --timeout=5s --start-period=20s --retries=3 \
    CMD python -c "import urllib.request,sys; sys.exit(0 if urllib.request.urlopen('http://127.0.0.1:8100/api/health', timeout=4).status == 200 else 1)"
CMD ["uvicorn", "backend.main:app", "--host", "0.0.0.0", "--port", "8100", "--proxy-headers"]
