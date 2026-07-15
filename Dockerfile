# ============================================================
# STAGE 1: Builder
# Installs all dependencies in a clean virtual environment.
# Keeps the final image free of build tools (gcc, make, etc.)
# ============================================================
FROM python:3.11-slim as builder

WORKDIR /app

# Install system build dependencies needed to compile psycopg2 and cryptography
RUN apt-get update && apt-get install -y \
    build-essential \
    libpq-dev \
    gcc \
    && rm -rf /var/lib/apt/lists/*

# Create an isolated virtual environment
RUN python -m venv /opt/venv
ENV PATH="/opt/venv/bin:$PATH"

# Copy and install requirements (this layer is cached unless requirements.txt changes)
COPY requirements.txt .
RUN pip install --no-cache-dir --upgrade pip && \
    pip install --no-cache-dir -r requirements.txt


# ============================================================
# STAGE 2: Production Runner
# Copies only the compiled venv and the application code.
# Results in a minimal, secure final image.
# ============================================================
FROM python:3.11-slim

WORKDIR /app

# Install only runtime system libraries (not build tools)
RUN apt-get update && apt-get install -y \
    libpq5 \
    curl \
    && rm -rf /var/lib/apt/lists/*

# Create a non-root user to run the application (security best practice)
RUN groupadd -r appuser && useradd -r -g appuser appuser

# Copy the virtual environment from the builder stage
COPY --from=builder /opt/venv /opt/venv
ENV PATH="/opt/venv/bin:$PATH"
ENV PYTHONDONTWRITEBYTECODE=1
ENV PYTHONUNBUFFERED=1
# appuser is a system account (useradd -r) with no home directory created.
# Point HOME at /app (already chowned to appuser below) so anything that
# tries to write cache/config files under $HOME has somewhere valid to
# write — Gunicorn 25+'s control socket defaults to $HOME/.gunicorn/ and
# fails with "Permission denied: '/home/appuser'" otherwise (that dir was
# never created). Also explicitly disabled in docker-entrypoint.sh, since
# we don't use gunicornc runtime management and this feature has caused
# restart loops in other constrained container platforms.
ENV HOME=/app

# Copy the application source code
COPY . .

# Make the entrypoint executable
RUN chmod +x docker-entrypoint.sh

# Change ownership so the non-root user can read files
RUN chown -R appuser:appuser /app

# Switch to non-root user
USER appuser

# Expose FastAPI port (default; actual bind port follows $PORT if the host sets one)
EXPOSE 8000

# Run with Gunicorn + Uvicorn workers for production stability.
# -w 4 = 4 worker processes (adjust based on CPU cores: 2 * cores + 1)
# -k uvicorn.workers.UvicornWorker = use async workers
# See docker-entrypoint.sh: binds to $PORT if the host injects one (Render,
# Cloud Run, etc.), otherwise defaults to 8000 (docker-compose / local runs).
CMD ["/app/docker-entrypoint.sh"]
