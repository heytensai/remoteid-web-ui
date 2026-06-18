# Remote ID Web Interface - Docker Container
# Multi-stage build for production deployment

FROM python:3.11-slim

# Install system dependencies
# - rsync: Required for syncing from remote collectors via SSH
# - openssh-client: Required for SSH connections to collectors
RUN apt-get update && apt-get install -y --no-install-recommends \
    rsync \
    openssh-client \
    && rm -rf /var/lib/apt/lists/*

# Create app user for security
RUN useradd -m -u 1000 appuser

# Set working directory
WORKDIR /app

# Copy requirements first for better layer caching
COPY requirements.txt .

# Install Python dependencies
RUN pip install --no-cache-dir -r requirements.txt

# Copy application code
COPY app.py config.py database.py session_detect.py session_scheduler.py sync.py wsgi.py gunicorn.conf.py ./
COPY templates/ templates/
COPY static/ static/

# Create directories for data and config
RUN mkdir -p /app/data /app/config && \
    chown -R appuser:appuser /app

# Switch to non-root user
USER appuser

# Expose the application port
EXPOSE 5000

# Health check
HEALTHCHECK --interval=30s --timeout=10s --start-period=5s --retries=3 \
    CMD python -c "import urllib.request; urllib.request.urlopen('http://localhost:5000/api/config')" || exit 1

# Default command - config file should be mounted at /app/config/web_config.yaml
# Uses preload_app (via gunicorn.conf.py) so background threads run only in master
CMD ["gunicorn", "-c", "gunicorn.conf.py", "wsgi:application"]
