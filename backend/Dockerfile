# syntax=docker/dockerfile:1
FROM python:3.11.11-slim-bookworm AS base

# Prevent Python from writing .pyc files and enable unbuffered output
ENV PYTHONDONTWRITEBYTECODE=1 \
    PYTHONUNBUFFERED=1 \
    PORT=8000

# Install minimal OS dependencies for Pillow and OpenCV headless
RUN apt-get update && apt-get install -y --no-install-recommends \
    libglib2.0-0 \
    libgl1 \
    libheif1 \
    libde265-0 \
    && rm -rf /var/lib/apt/lists/*

# Create non-root system user and group
RUN groupadd -r appgroup && useradd -r -g appgroup -d /home/appuser -m -s /bin/bash appuser

WORKDIR /app

# Copy requirements and install via pip
COPY backend/requirements.txt /app/requirements.txt
RUN pip install --no-cache-dir -r /app/requirements.txt

# Copy application source code
COPY backend/app /app/app

# Set ownership to non-root user
RUN chown -R appuser:appgroup /app

USER appuser

EXPOSE 8000

HEALTHCHECK --interval=15s --timeout=5s --start-period=10s --retries=3 \
    CMD python3 -c "import urllib.request; urllib.request.urlopen('http://localhost:8000/api/health', timeout=5)" || exit 1

CMD ["uvicorn", "app.main:app", "--host", "0.0.0.0", "--port", "8000"]
