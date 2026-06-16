# Agent Guidelines for Remote ID Web UI

## Configuration File Policy

**IMPORTANT**: The `config/web_config.yaml` file is in `.gitignore` and should NEVER be modified directly. It contains user-specific settings and sensitive data like API keys.

### Correct Workflow for Config Changes

1. **Always edit `default.web_config.yaml`** - This is the template file that IS tracked in git
2. **Never edit `config/web_config.yaml`** - This is the user's local config (not tracked)
3. **Also update `docker-config/web_config.docker.yaml`** - Docker-specific config

### Config Files Overview

| File | Purpose | Tracked in Git? |
|------|---------|-----------------|
| `default.web_config.yaml` | Template with defaults and documentation | Yes |
| `config/web_config.yaml` | User's actual configuration | No (in .gitignore) |
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

## XSS Protection

User-controlled data (UAS IDs, session IDs, operator IDs) must ALWAYS be HTML-escaped before inserting into the DOM. Both `MapController` and `UIController` have an `escapeHtml()` method that uses the `textContent` → `innerHTML` round-trip for reliable escaping.

### When to escape

| Scenario | Method |
|----------|--------|
| Template literals building HTML | `esc(userValue)` where `const esc = (v) => this.escapeHtml(v)` |
| Direct `innerHTML` assignment | Convert to DOM methods (`textContent`, `createElement`, `appendChild`) |
| Leaflet popup content | Escape values in the returned template string via `esc()` |

### Rules

1. **Never** interpolate user-controlled data directly into HTML strings or `innerHTML` — always use `escapeHtml()` or DOM manipulation.
2. **Data attributes are safe** when set via `esc()` — the browser auto-decodes them on read via `.dataset.*`.
3. `color` values from `getDroneColor()` are safe (constrained HSL output).
4. Formatted values from `Units.*` are safe (numbers with unit labels).
5. If adding a new object/controller, add an `escapeHtml` method following the same pattern.

## CSRF Protection

All POST endpoints in `app.py` are protected by `flask_wtf.csrf.CSRFProtect` (except `/api/submit` which uses Bearer token auth). A CSRF token is generated on the server and sent to the frontend via the `/api/config` response's `csrf_token` field.

### How it works

1. `app.secret_key` is set at startup (from `FLASK_SECRET_KEY` env var or auto-generated)
2. `CSRFProtect` middleware validates all POST/PUT/DELETE/PATCH requests
3. The frontend (`api.js`) reads the token from the config response and stores it as `API.csrfToken`
4. All POST requests from the frontend include the token as the `X-CSRFToken` header
5. The `/api/submit` endpoint is `@csrf.exempt` since it authenticates via API key Bearer token

### When Adding New POST Endpoints

1. Add the route to `app.py` — CSRF protection is automatic
2. If the endpoint is consumed by external services (not the frontend), add `@csrf.exempt` and use an alternative auth mechanism (API key, etc.)
3. Frontend POST calls automatically include the CSRF token via `API._post()`

### Config Options

- `FLASK_SECRET_KEY` — env var for a persistent secret key (optional; a random key is generated at startup if not set)

## Testing Strategy

Tests live in `tests/` with Python (`pytest`) and JavaScript (`Jest` + `jsdom`) suites.

### When Adding/Changing Code

- **Python backend** — add or update tests in `tests/`. Key areas: config loading (`test_config.py`), database operations (`test_database.py`), API endpoints (`test_app.py`), and HTML template rendering via BeautifulSoup (`test_html.py`).
- **JavaScript frontend** — add or update tests in `tests/js/`. External dependencies (Leaflet, fetch, flatpickr) are mocked. Only pure functions are tested for `MapController` and `UIController` (map rendering requires a real browser).
- **HTML template** — structural tests in `test_html.py` verify required DOM elements, CDN links, CSP headers, and asset loading.

### Testing Conventions

1. **Isolated test DB** — fixtures in `conftest.py` create temp SQLite databases and tear them down after each test.
2. **API tests** use Flask's test client with `TESTING=True` and `WTF_CSRF_ENABLED=False` (except when explicitly testing CSRF).
3. **JS tests** load source files via `eval` (with `const` stripped) into the `jsdom` global scope. Mock `global.fetch`, `global.L`, and `global.flatpickr` as needed.
4. **HTML escaping** — test that `escapeHtml()` handles `<`, `>`, `&`, `"` via the `textContent` → `innerHTML` round-trip. jsdom escapes `"` as `\"` not `&quot;`, so tests check for `not.toContain('<')` rather than exact equality.
5. **Coverage** — run `make test-py-cov` for Python, `npx jest --coverage` for JS.

## Linting

### Python (Pylint)

- Config: `.pylintrc` at project root (disables docstring, too-many-*, invalid-name, global-statement, etc.)
- Run: `make lint-py` or `env/bin/pylint *.py`
- Inline disables (`# pylint: disable=...`) are acceptable for specific methods

### JavaScript (ESLint)

- Config: `eslint.config.js` (flat config, ESLint v9+)
- Run: `make lint-js` or `npx eslint static/js/`
- Key rules: single quotes, semicolons, `prefer-const`
- Globals: `L`, `flatpickr` (readonly), `Units`, `API`, `MapController`, `UIController` (defined in source files)
- The `no-redeclare` rule is disabled — the project uses global singletons across script-tag-loaded files

### When Adding/Changing Code

Run `make lint` and fix all errors before committing.

### Running

```bash
make test        # All tests (146 total)
make test-py     # Python only
make test-js     # JS only
make lint        # All linters
make lint-py     # Pylint only
make lint-js     # ESLint only
```

See `TESTING.md` for details.
