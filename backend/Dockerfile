FROM python:3.14-slim

WORKDIR /app

# Install system dependencies
RUN apt-get update && apt-get install -y --no-install-recommends \
    build-essential \
    && rm -rf /var/lib/apt/lists/*

# Install uv
RUN pip install --no-cache-dir uv

# Copy project files
COPY backend . 
COPY prompts ./prompts

# Install dependencies using uv
RUN uv pip install --system -e . --no-cache

# Expose port (default 8080 for Cloud Run, 8000 for local)
EXPOSE 8080

# Health check using Python standard library (no external requests dependency)
HEALTHCHECK --interval=30s --timeout=10s --start-period=10s --retries=3 \
  CMD python -c "import os, urllib.request; urllib.request.urlopen('http://localhost:' + os.environ.get('PORT', '8080') + '/health')" || exit 1

# Run the application with dynamic PORT
CMD ["sh", "-c", "exec uvicorn app.main:app --host 0.0.0.0 --port ${PORT:-8080}"]

