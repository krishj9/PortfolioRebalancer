FROM python:3.14-slim

WORKDIR /app

# Install build dependencies for pydantic-core on ARM64
RUN apt-get update && apt-get install -y --no-install-recommends \
    build-essential \
    && rm -rf /var/lib/apt/lists/*

# Copy backend code (needed for imports)
COPY backend/app ./app
COPY backend/pyproject.toml .

# Install dependencies
COPY mcp-servers/sentiment/requirements.txt .
RUN pip install --no-cache-dir -r requirements.txt

# Copy application code
COPY mcp-servers/sentiment/server.py .
COPY prompts ./prompts

# Expose port
EXPOSE 8201

# Health check
HEALTHCHECK --interval=30s --timeout=10s --start-period=5s --retries=3 \
  CMD python -c "import requests; requests.get('http://localhost:8201/health')"

# Run the application
CMD ["python", "server.py"]
