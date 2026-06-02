# Docker Support

This directory contains Docker configuration files for the Remote ID Web Interface.

## Files

- `Dockerfile` - Production container image
- `docker-compose.yml` - Production deployment
- `docker-compose.dev.yml` - Development environment with mock data
- `.dockerignore` - Excludes unnecessary files from build
- `docker-config/` - Configuration files for Docker environments

## Quick Start

### Production Deployment

1. Ensure your `web_config.yaml` is properly configured

2. Build and run:
   ```bash
   docker-compose up -d
   ```

3. Access the web interface at http://localhost:5000

### Development with Mock Data

1. Start the development environment:
   ```bash
   docker-compose -f docker-compose.dev.yml up
   ```

2. This creates:
   - Web interface at http://localhost:5000
   - Mock collector data for testing
   - Live code reloading for development

## Volume Mounts

### Production

| Host Path | Container Path | Purpose |
|-----------|---------------|---------|
| `./web_config.yaml` | `/app/config/web_config.yaml` | Configuration file (read-only) |
| `web-data` (named volume) | `/app/data` | Database persistence |
| `~/.ssh` | `/home/appuser/.ssh` | SSH keys for remote collectors |

### Development

| Host Path | Container Path | Purpose |
|-----------|---------------|---------|
| Source files | `/app/*.py` | Live code mounting |
| `./templates` | `/app/templates` | Template files |
| `./static` | `/app/static` | Static assets |
| `./docker-config/` | `/app/config/` | Docker-specific config |
| `web-data-dev` (named volume) | `/app/data` | Database persistence |

## Environment Variables

- `PYTHONUNBUFFERED=1` - Disable Python output buffering
- `PYTHONDONTWRITEBYTECODE=1` - Don't write .pyc files
- `FLASK_ENV=development` - Flask development mode (dev only)
- `FLASK_DEBUG=1` - Flask debug mode (dev only)

## Security

- Container runs as non-root user (`appuser`)
- SSH keys mounted read-only
- Configuration files mounted read-only
- Database stored in named volume for persistence

## Health Checks

The container includes a health check that verifies the API endpoint `/api/config` is responding.

## Building Manually

```bash
# Build image
docker build -t remoteid-web:latest .

# Run with custom config
docker run -d \
  -p 5000:5000 \
  -v $(pwd)/web_config.yaml:/app/config/web_config.yaml:ro \
  -v remoteid-data:/app/data \
  remoteid-web:latest
```

## Troubleshooting

### SSH Connection Issues

If using remote collectors via SSH, ensure:
1. SSH keys are properly mounted
2. `id_rsa` permissions are 600 (container uses UID 1000)
3. Host keys are accepted or StrictHostKeyChecking is disabled

### Database Permissions

The container runs as UID 1000. Ensure the data volume is writable:
```bash
docker-compose exec web ls -la /app/data
```

### Viewing Logs

```bash
# Production
docker-compose logs -f web

# Development
docker-compose -f docker-compose.dev.yml logs -f web
```
