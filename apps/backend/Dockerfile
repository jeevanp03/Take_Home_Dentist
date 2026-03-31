# syntax=docker/dockerfile:1
FROM python:3.11-slim

WORKDIR /app

# System deps for chromadb (sqlite3, build tools) + health checks
RUN apt-get update && \
    apt-get install -y --no-install-recommends gcc g++ curl && \
    rm -rf /var/lib/apt/lists/*

# Install Python dependencies (cached across builds)
COPY requirements.txt .
RUN --mount=type=cache,target=/root/.cache/pip \
    pip install -r requirements.txt

# Copy application code
COPY . .

# Create data directories
RUN mkdir -p data/chroma data/knowledge/cache

EXPOSE 8000

# Run with single worker (SQLite + in-process ChromaDB require it)
CMD ["uvicorn", "src.main:app", "--host", "0.0.0.0", "--port", "8000", "--workers", "1"]
