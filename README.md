# Remote ID Web UI

A standalone web interface for visualizing Remote ID drone data on an interactive Leaflet.js map. Designed for field operations — display real-time and historical drone positions, flight tracks, operator locations, and geozone alerts.

## Features

- Interactive map with OpenStreetMap, Carto Dark/Light tile providers
- Drone position markers with deterministic color-coding
- Flight track visualization with adjustable opacity
- Operator location display linked to drones
- Session-based flight grouping with time-window filtering
- Playback/replay controls for post-mission analysis
- Geozone alerts — circle/rectangle geozones with enter/exit notifications
- Drone detail panel with altitude profile chart, speed, and distance stats
- Track export (CSV, GPX, KML) per session
- Collector status tracking (mobile and fixed)
- Notification dispatch to Discord, ntfy, and Microsoft Teams
- Role-based access control (operator, viewer, guest)
- Dark mode, metric/imperial unit toggle, keep-screen-on for field use
- Mobile-responsive sidebar with swipe gestures
- PWA manifest for home-screen install

## Quick Start

### Docker (Recommended)

```bash
# 1. Create your config
mkdir -p config
cp default.web_config.yaml config/web_config.yaml
# Edit config/web_config.yaml with your settings (collector keys, map center, etc.)

# 2. Create .env with a secret key
echo "FLASK_SECRET_KEY=$(python3 -c 'import secrets; print(secrets.token_hex(24))')" > .env

# 3. Start the server
docker-compose up -d

# Access at http://localhost:5000
```

To update:

```bash
docker-compose pull && docker-compose up -d
```

### Development with Docker

```bash
# Starts with auto-reload and volume mounts for live editing
docker-compose -f docker-compose.dev.yml up
```

### Local Installation (Development Only)

For local development without Docker:

```bash
# 1. Create a virtual environment
python3 -m venv env
source env/bin/activate

# 2. Install dependencies
pip install -r requirements.txt

# 3. Create your config
mkdir -p config
cp default.web_config.yaml config/web_config.yaml
# Edit config/web_config.yaml with your settings

# 4. Start the development server
python app.py --config config/web_config.yaml

# Access at http://localhost:5000
```

## Configuration

Copy `default.web_config.yaml` to `config/web_config.yaml` and customize. The config file is gitignored and hot-reloadable — most changes take effect within 10 seconds without restarting.

### Key Settings

| Setting | Default | Description |
|---------|---------|-------------|
| `host` / `port` | `0.0.0.0` / `5000` | Server bind address |
| `database_path` | `./data/web.db` | SQLite database location |
| `map.center_lat` / `center_lon` | — | Initial map center (auto-fits if unset) |
| `map.tile_provider` | `osm` | Options: `osm`, `carto-dark`, `carto-light` |
| `default_hours` | `24` | Default time window |
| `max_positions_per_query` | `5000` | Limit to prevent browser lag |
| `use_metric` | `true` | `true` = meters, `false` = feet |
| `api_keys` | — | API keys for data submission (maps key to source name) |
| `drone_aliases` | — | Map UAS IDs to friendly names |
| `collectors` | — | Mobile/fixed collector definitions |
| `notifications` | — | Discord, ntfy, or Teams webhook targets |
| `roles` | — | Role-based permission definitions |

### Collector Setup

Collectors report position via a heartbeat ping:

```yaml
collectors:
  - name: "Car 1"
    type: "mobile"
    api_key: "your-collector-api-key"
    color: "#e67e22"
    timezone: "America/Denver"
  - name: "Base Station"
    type: "fixed"
    lat: 37.7749
    lon: -122.4194
    color: "#3498db"
```

The collector calls `GET /api/submit/ping?lat=...&lon=...` with a Bearer token. See [COLLECTOR_API.md](COLLECTOR_API.md) for details.

### Notifications

```yaml
notifications:
  - name: "Alert Channel"
    type: "discord"
    webhook_url: "https://discord.com/api/webhooks/..."
    events: [geozone_enter, new_session]
  - name: "Phone Alerts"
    type: "ntfy"
    webhook_url: "https://ntfy.sh/mytopic"
    token: "tk_xxx"
    events: [geozone_enter]
```

Supported types: `discord`, `ntfy`, `teams`. See `default.web_config.yaml` for full examples.

## Data Ingestion

### HTTP API (Primary)

Remote nodes push data via POST with Bearer token auth:

```bash
curl -X POST http://localhost:5000/api/submit \
  -H "Authorization: Bearer your-api-key" \
  -H "Content-Type: application/json" \
  -d '[{
    "timestamp": "2026-06-02T14:30:00",
    "uas_id": "drone-123",
    "latitude": 43.51746,
    "longitude": -112.01449,
    "altitude": 100.5
  }]'
```

See [CLIENT_API.md](CLIENT_API.md) for the full API reference and a Python client implementation guide.

### Database Import (One-Time)

Import from a collector's SQLite database:

```bash
python import_db.py \
  --web-db ./data/web.db \
  --source /path/to/collector.db \
  --name "Field-Node" \
  --timezone "America/Denver"
```

## User Management

The app includes a token-based auth system with role-based access control.

### Create a User

```bash
flask auth create-user --config config/web_config.yaml "Alice" "alice@example.com" operator
```

This prints a one-time login link like `?login_token=...`. The user visits this URL to authenticate. The link expires after 7 days (configurable with `--expires`).

### List Users

```bash
flask auth list-users --config config/web_config.yaml
```

### Revoke Access

```bash
flask auth revoke-tokens --config config/web_config.yaml 1
```

### Default Roles

| Role | Permissions |
|------|-------------|
| `operator` | Full access — map, drones, tracks, export, waypoints, settings, alerts, replay |
| `viewer` | Read-only — map, drones, tracks, alerts, replay |
| `guest` | Minimal — map, drones, tracks |

First-time visitors are auto-created as ephemeral guest users. Customize roles in `config/web_config.yaml`.

## Project Structure

```
├── app.py                  # Flask application and API endpoints
├── config.py               # YAML config loader
├── database.py             # SQLite database operations
├── session_detect.py       # Background session boundary detection
├── session_scheduler.py    # Periodic session detection runner
├── maintenance_scheduler.py # Background auth cleanup
├── alert_engine.py         # Geozone alert evaluation
├── notifier.py             # Discord/ntfy/Teams notification dispatch
├── import_db.py            # One-time database import tool
├── wsgi.py                 # WSGI entry point for gunicorn
├── gunicorn.conf.py        # Gunicorn configuration
├── default.web_config.yaml # Config template (tracked in git)
├── config/
│   └── web_config.yaml     # Your local config (gitignored)
├── templates/
│   └── index.html          # Main HTML template
├── static/
│   ├── css/style.css       # Styles (mobile-first)
│   └── js/
│       ├── units.js        # Metric/imperial conversions
│       ├── api.js          # API client with retry logic
│       ├── map.js          # Leaflet map, markers, tracks, replay
│       └── ui.js           # Sidebar, time picker, settings, auth
├── tests/                  # Python (pytest) and JS (Jest) test suites
├── Dockerfile              # Production container
├── docker-compose.yml      # Production compose
└── docker-compose.dev.yml  # Development compose with hot-reload
```

## Testing

```bash
# Install dev dependencies
make install
# or: pip install -r dev-requirements.txt && npm install

# Run all tests
make test

# Run by language
make test-py          # Python only
make test-js          # JavaScript only

# Coverage
make test-py-cov      # Python with coverage report
npx jest --coverage   # JavaScript

# Linting
make lint             # All linters
make lint-py          # Pylint only
make lint-js          # ESLint only
```

146 tests total (78 Python, 68 JavaScript). See [TESTING.md](TESTING.md) for details.

## API Reference

| Endpoint | Method | Description |
|----------|--------|-------------|
| `/api/config` | GET | Map config, CSRF token, aliases, waypoints |
| `/api/drones` | GET | List drones in time window |
| `/api/positions` | GET | Positions (filtered by time, optional uas_id) |
| `/api/tracks/<uas_id>` | GET | GeoJSON track, grouped by session |
| `/api/tracks/batch` | POST | Batch fetch tracks for multiple sessions |
| `/api/operators` | GET | Operator positions |
| `/api/bounds` | GET | Bounding box of all positions |
| `/api/stats` | GET | Aggregate statistics |
| `/api/sources` | GET | Remote source status |
| `/api/alerts` | GET | Active geozone alerts |
| `/api/alerts/history` | GET | Alert event history with filtering |
| `/api/export/<csv\|gpx\|kml>/<uas_id>` | GET | Export track data |
| `/api/submit` | POST | Submit Remote ID events (requires API key) |
| `/api/submit/ping` | GET | Collector heartbeat/position (requires API key) |
| `/api/auth/anon` | POST | Create ephemeral account |
| `/api/auth/login` | POST | Exchange login token for session |
| `/api/auth/me` | GET | Current user info and permissions |
| `/api/auth/logout` | POST | Revoke session |

All query endpoints accept `start` and `end` ISO 8601 datetime parameters.

## License

GPL-3.0. See [LICENSE](LICENSE).
