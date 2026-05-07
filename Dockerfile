# ═══════════════════════════════════════════════════════════
# CryptositNews - Multi-stage Docker Build
# ═══════════════════════════════════════════════════════════

# --- Stage 1: Build ---
FROM python:3.12-slim AS builder

WORKDIR /build
COPY requirements.txt .
RUN pip install --no-cache-dir --prefix=/install -r requirements.txt

# --- Stage 2: Runtime ---
FROM python:3.12-slim

LABEL maintainer="CryptositNews"
LABEL version="2.0"

WORKDIR /app

# Install only runtime dependencies
COPY --from=builder /install /usr/local

# Copy application code
COPY . .

# Create non-root user
RUN groupadd -r appuser && useradd -r -g appuser appuser
RUN chown -R appuser:appuser /app
USER appuser

# Expose port (Railway/VPS provides PORT env)
EXPOSE 8000

# Health check
HEALTHCHECK --interval=30s --timeout=5s --start-period=10s --retries=3 \
    CMD python -c "import urllib.request; urllib.request.urlopen('http://localhost:${PORT:-8000}/health')" || exit 1

# Default: run with gunicorn (Railway uses Procfile)
CMD ["gunicorn", "wsgi:app", "--workers", "2", "--threads", "4", "--timeout", "120", "--bind", "0.0.0.0:${PORT:-8000}"]
