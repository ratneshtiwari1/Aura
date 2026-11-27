FROM node:20-bookworm-slim AS frontend-build

WORKDIR /workspace/frontend
COPY frontend/package.json frontend/package-lock.json ./
RUN npm ci
COPY frontend/ ./
RUN npm run build

FROM python:3.12-slim-bookworm AS python-dependencies

RUN apt-get update \
    && apt-get install -y --no-install-recommends build-essential unixodbc-dev \
    && rm -rf /var/lib/apt/lists/*

WORKDIR /workspace
COPY backend/requirements.txt ./requirements.txt
RUN pip install --no-cache-dir --prefix=/install -r requirements.txt

FROM python:3.12-slim-bookworm AS runtime

ENV PYTHONDONTWRITEBYTECODE=1 \
    PYTHONUNBUFFERED=1 \
    APP_DATA_DIR=/home/data

RUN apt-get update \
    && apt-get install -y --no-install-recommends ca-certificates curl gnupg \
    && install -d /etc/apt/keyrings \
    && curl -fsSL https://packages.microsoft.com/keys/microsoft.asc \
        | gpg --dearmor -o /etc/apt/keyrings/microsoft-prod.gpg \
    && echo "deb [arch=amd64 signed-by=/etc/apt/keyrings/microsoft-prod.gpg] https://packages.microsoft.com/debian/12/prod bookworm main" \
        > /etc/apt/sources.list.d/microsoft-prod.list \
    && apt-get update \
    && ACCEPT_EULA=Y apt-get install -y --no-install-recommends msodbcsql18 unixodbc \
    && rm -rf /var/lib/apt/lists/*

WORKDIR /app
COPY --from=python-dependencies /install /usr/local
COPY backend/ ./backend/
COPY automation/ ./automation/
COPY theme/ ./theme/
COPY --from=frontend-build /workspace/frontend/dist ./frontend/dist

RUN mkdir -p /home/data \
    && useradd --create-home --uid 10001 --shell /usr/sbin/nologin aura \
    && chown -R aura:aura /app /home/data

USER aura
EXPOSE 8000
HEALTHCHECK --interval=30s --timeout=5s --start-period=30s --retries=3 \
    CMD python -c "import socket; socket.create_connection(('127.0.0.1', 8000), timeout=3).close()"

CMD ["uvicorn", "backend.main:app", "--host", "0.0.0.0", "--port", "8000"]
