# Multi-stage: Node builds the React UI; Python serves API + web/dist.

FROM node:22-bookworm-slim AS web
WORKDIR /src/web
COPY web/package.json web/package-lock.json ./
RUN npm ci
COPY web/ ./
RUN npm run build

FROM python:3.13-slim-bookworm AS runtime
WORKDIR /app

# ca-certificates for outbound HTTPS (Mistral). pikepdf / pypdfium2 / Pillow /
# reportlab install from manylinux wheels — no compiler toolchain required.
RUN apt-get update \
    && apt-get install -y --no-install-recommends ca-certificates \
    && rm -rf /var/lib/apt/lists/*

COPY requirements.txt pyproject.toml ./
COPY src ./src
RUN pip install --no-cache-dir -r requirements.txt \
    && pip install --no-cache-dir -e .

COPY --from=web /src/web/dist ./web/dist

ENV OUTPUT_DIR=/tmp/pdfsplit-output \
    PYTHONUNBUFFERED=1 \
    PYTHONDONTWRITEBYTECODE=1

# Sessions live under OUTPUT_DIR for the life of the container instance only.
RUN mkdir -p /tmp/pdfsplit-output

EXPOSE 8000
CMD ["sh", "-c", "uvicorn pdfsplit.app.main:app --host 0.0.0.0 --port ${PORT:-8000}"]
