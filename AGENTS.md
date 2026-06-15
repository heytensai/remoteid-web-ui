# Agent Guidelines for Remote ID Web UI

## Configuration File Policy

**IMPORTANT**: The `web_config.yaml` file is in `.gitignore` and should NEVER be modified directly. It contains user-specific settings and sensitive data like API keys.

### Correct Workflow for Config Changes

1. **Always edit `default.web_config.yaml`** - This is the template file that IS tracked in git
2. **Never edit `web_config.yaml`** - This is the user's local config (not tracked)
3. **Also update `docker-config/web_config.docker.yaml`** - Docker-specific config

### Config Files Overview

| File | Purpose | Tracked in Git? |
|------|---------|-----------------|
| `default.web_config.yaml` | Template with defaults and documentation | Yes |
| `web_config.yaml` | User's actual configuration | No (in .gitignore) |
| `docker-config/web_config.docker.yaml` | Docker development config | Yes |

### When Adding New Config Options

1. Add the option to `config.py` (WebConfig class)
2. Add the option to `default.web_config.yaml` with comments
3. Add the option to `docker-config/web_config.docker.yaml`
4. Update the API endpoint in `app.py` if needed
5. Update this AGENTS.md file

### Current Config Options

- `host` - Web server host (default: "0.0.0.0")
- `port` - Web server port (default: 5000)
- `database_path` - Path to SQLite database
- `sync_interval` - Seconds between collector syncs
- `collectors` - List of data collector sources
- `map` - Map configuration (center_lat, center_lon, default_zoom, tile_provider)
- `default_hours` - Default time window for queries
- `max_positions_per_query` - Limit to prevent browser lag
- `use_metric` - Display units: true=metric (meters), false=imperial (feet)
- `api_keys` - API keys for remote data submission
- `drone_aliases` - Map UAS IDs to friendly names

## Code Style Guidelines

- JavaScript: Use single quotes for strings
- CSS: Use 4-space indentation
- Python: Follow PEP 8

## Unit Display

When displaying distances/altitudes/speeds, always use the `Units` module:

```javascript
// static/js/units.js
Units.formatAltitude(meters)     // Returns "100m" or "328ft"
Units.formatDistance(meters)      // Returns "500 m" or "1,640 ft"
Units.formatSpeed(metersPerSec)   // Returns "50.0 km/h" or "31.1 mph"
```

The underlying data always stays in meters - only display values are converted.
