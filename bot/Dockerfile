# Mr. Swede Discord bot - optimized for Cloud Run
# Multi-stage build to keep the runtime image small.

# ==================== Builder ====================
FROM python:3.12-slim AS builder

RUN apt-get update && apt-get install -y --no-install-recommends \
    build-essential \
    curl \
    && rm -rf /var/lib/apt/lists/*

ENV POETRY_HOME=/opt/poetry
ENV POETRY_VERSION=1.7.1
RUN curl -sSL https://install.python-poetry.org | python3 - && \
    ln -s /opt/poetry/bin/poetry /usr/local/bin/poetry

WORKDIR /app
COPY pyproject.toml poetry.lock* ./

# Use pip in the runtime image (smaller than carrying poetry forward).
RUN poetry export -f requirements.txt --output requirements.txt --without-hashes

# ==================== Runtime ====================
FROM python:3.12-slim AS runtime

# No FFmpeg / libffi / system deps -- the bot doesn't play audio anymore.
RUN useradd --create-home --shell /bin/bash appuser

WORKDIR /app

COPY --from=builder /app/requirements.txt .
RUN pip install --no-cache-dir --upgrade pip && \
    pip install --no-cache-dir -r requirements.txt

COPY src/ ./src/

RUN chown -R appuser:appuser /app
USER appuser

ENV PYTHONUNBUFFERED=1
ENV PYTHONDONTWRITEBYTECODE=1
ENV PYTHONPATH=/app
ENV PORT=8080
EXPOSE 8080

HEALTHCHECK --interval=30s --timeout=10s --start-period=10s --retries=3 \
    CMD python -c "import urllib.request; urllib.request.urlopen('http://localhost:8080/health')" || exit 1

CMD ["python", "-m", "src.main"]
