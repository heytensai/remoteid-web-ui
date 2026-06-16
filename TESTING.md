# Testing

## Quick Start

```bash
make test          # Run all tests
make test-py       # Python tests only
make test-js       # JavaScript tests only
make test-py-cov   # Python tests with coverage report
make lint          # Run all linters
make lint-py       # Pylint on Python source files
make lint-js       # ESLint on JavaScript source files
```

## Prerequisites

### Python

Dev dependencies are listed in `dev-requirements.txt`:

```bash
pip install -r requirements.txt
pip install -r dev-requirements.txt
```

The test suite uses `pytest`, `pytest-cov`, `pytest-flask`, and `beautifulsoup4`.

### JavaScript

Node.js (v18+) and npm are required. Install test dependencies:

```bash
npm install
```

Jest runs with the `jsdom` environment to simulate browser DOM APIs.

## Test Structure

```
tests/
├── conftest.py          # Shared fixtures: app, client, db
├── test_config.py       # Config loader (8 tests)
├── test_database.py     # Database operations (24 tests)
├── test_app.py          # Flask API endpoints (29 tests)
├── test_html.py         # HTML template / BeautifulSoup (17 tests)
└── js/
    ├── units.test.js    # Units module (24 tests)
    ├── api.test.js      # API client (19 tests)
    ├── map.test.js      # MapController helpers (16 tests)
    └── ui.test.js       # UIController helpers (9 tests)
```

**Total: 146 tests** (78 Python, 68 JavaScript)

## Python Tests

### What's Tested

| Module | Tests | Scope |
|--------|-------|-------|
| `config.py` | 8 | Loading defaults, MapConfig, CollectorConfig, `to_dict`, missing files |
| `database.py` | 24 | Record validation, sanitize, insert, duplicates, queries (drones, positions, tracks, operators, bounds, timestamps), session detection |
| `app.py` | 29 | All API endpoints, auth (Bearer token, CSRF), error handling, time range parsing |
| `index.html` | 17 | Template rendering, DOM structure, CDN links, JS/CSS assets, CSP, favicon, time presets |

### Running Python Tests

```bash
pytest tests/ -v
pytest tests/ --cov=. --cov-report=term   # With coverage
pytest tests/test_app.py -v               # Single file
pytest tests/test_app.py::TestApiDrones -v  # Single class
```

### Fixtures

- `app` — Flask app with temp SQLite database and test config
- `client` — Flask test client
- `db` — Database pre-populated with 4 sample drone records
- `sample_records` — Clean records for insertion tests
- `sample_config_yaml` — Reusable YAML config fixture

### HTML Testing

The HTML template is rendered through Flask's `render_template` and parsed with `BeautifulSoup`. Tests verify:

- Required DOM elements exist by ID
- CDN links (Leaflet, Flatpickr, Font Awesome) are present
- All 4 JS files and CSS files are loaded
- Content-Security-Policy meta tag is set
- Time preset buttons (1h, 6h, 24h, 7d) exist
- `data-base-url` attribute is on `<body>`
- Favicon is linked

## JavaScript Tests

### What's Tested

| Module | Tests | Scope |
|--------|-------|-------|
| `units.js` | 24 | Metric/imperial conversions for distance, altitude, speed; edge cases (null, NaN, zero) |
| `api.js` | 19 | URL construction, CSRF headers, POST requests, retry logic |
| `map.js` | 16 | `escapeHtml`, `getDroneColor` (determinism, hue range), `getDroneName`, Haversine distance |
| `ui.js` | 9 | `escapeHtml`, `_niceStep` chart axis helper, Haversine distance, date checkbox state |

### Running JavaScript Tests

```bash
npx jest                # All JS tests
npx jest --watch        # Watch mode
npx jest --coverage     # With coverage
npx jest tests/js/units.test.js  # Single file
```

### Mock Strategy

JS tests use `eval` with the source files to access global singletons (`Units`, `API`, `MapController`, `UIController`). External dependencies are mocked:

- **Leaflet (`L`)** — entire API mocked with `jest.fn()`
- **`fetch`** — mocked per test via `jest.fn().mockResolvedValue(...)`
- **`flatpickr`** — `global.flatpickr = jest.fn()`
- **DOM** — `jsdom` environment simulates `document`, `window`, etc.

Only pure functions are tested for `MapController` and `UIController`. Leaflet-dependent features (map rendering, markers, popups) require a real browser and are not covered by unit tests.

## Coverage

```bash
make test-py-cov            # Python coverage (HTML report in htmlcov/)
npx jest --coverage         # JS coverage
```

Python coverage runs via `pytest-cov`. JS coverage runs via Jest's built-in Istanbul integration.

## Linting

### Python (Pylint)

Configured via `.pylintrc` at project root. Covers all `*.py` files.

```bash
make lint-py           # pylint *.py
env/bin/pylint app.py  # Single file
```

Inline disables are used sparingly for specific methods (e.g., `# pylint: disable=too-many-locals`).

### JavaScript (ESLint)

Configured via `eslint.config.js` (flat config format). Covers `static/js/` source files. Test files are excluded.

```bash
make lint-js                       # eslint static/js/
npx eslint static/js/map.js        # Single file
```

Key config: single quotes, semicolons required, browser & ES2021 env, custom globals (`L`, `flatpickr`, `Units`, `API`, `MapController`, `UIController`).

### Running both

```bash
make lint
```

## CI Integration

### GitHub Actions (example)

```yaml
jobs:
  test-py:
    runs-on: ubuntu-latest
    steps:
      - uses: actions/checkout@v4
      - uses: actions/setup-python@v5
        with:
          python-version: '3.12'
      - run: pip install -r requirements.txt dev-requirements.txt
      - run: pytest tests/ --cov=.

  test-js:
    runs-on: ubuntu-latest
    steps:
      - uses: actions/checkout@v4
      - uses: actions/setup-node@v4
        with:
          node-version: 20
      - run: npm ci
      - run: npx jest
```
