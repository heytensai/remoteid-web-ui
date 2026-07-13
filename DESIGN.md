# Web Interface Design Document
## Remote ID CalTopo Tracker

**Version**: 1.2
**Date**: 2026-06-02
**Status**: Implemented & Refined

---

## 1. Overview

A standalone web interface to visualize Remote ID drone data on an interactive map using Leaflet.js with OpenStreetMap tiles. The web interface runs separately from the Raspberry Pi decoder and receives data via HTTP API submission or on-demand database import.

---

## 2. Goals

- Display current and historical drone positions on a map
- Show drone tracks (flight paths) over time
- Display operator locations alongside drone positions
- Filter by date/time with Flatpickr picker
- Default time window: last 24 hours
- Mobile-responsive design
- Architecture supports future multi-source setup

---

## 3. Architecture

```
┌─────────────────────┐         ┌─────────────────────┐
│   Remote Node       │         │   Web Server        │
│   (Decoder)         │         │                     │
│                     │  POST   │  ┌───────────────┐  │
│  ┌───────────────┐  │─/api/───│  │  Flask App    │  │
│  │   decoder.py  │  │ submit  │  │               │  │
│  └───────┬───────┘  │ Bearer  │  │  ┌─────────┐  │  │
│          │          │  token  │  │  │ web.db  │  │  │
│          ▼          │         │  │  │ (SQLite)│  │  │
│  ┌───────────────┐  │         │  │  └─────────┘  │  │
│  │  remoteid.db  │  │         │  └───────┬───────┘  │
│  │  (SQLite)     │  │         │          │          │
│  └───────────────┘  │         │          │ HTTP     │
│                     │         │          ▼          │
│                     │         │  ┌────────────────┐ │
│                     │         │  │  Browser       │ │
│                     │         │  │  Leaflet Map   │ │
└─────────────────────┘         │  └────────────────┘ │
                                └──────────────────────┘
```

---

## 4. Key Design Decisions

| Feature | Decision |
|---------|----------|
| Map Library | Leaflet.js + OpenStreetMap (free, no API key) |
| Time Picker | Flatpickr |
| Color Scheme | Deterministic HSL from drone ID hash |
| Drone Icon | `fa-plane` (Font Awesome) |
| Operator Icon | `fa-user` (same color as drone) |
| Default View | Configurable center/zoom, or auto-fit |
| Data Ingestion | HTTP POST (push) or database import (pull) |
| Track Simplification | No (until usage data) |
| Data Retention | Keep everything |
| Deployment | Docker or manual run |
| Authentication | None for web UI (private network); API key (Bearer token) for `/api/submit` endpoint |

---

## 5. Configuration

```yaml
# web_config.yaml
web_interface:
  host: "0.0.0.0"
  port: 5000
  database_path: "./web.db"

  # API keys for remote node data submission
  api_keys:
    "your-secret-key-1": "Node-A"
    "your-secret-key-2": "Node-B"

  # Map defaults (optional - auto-fit if not set)
  map:
    center_lat: 40.7128
    center_lon: -74.0060
    default_zoom: 12
    tile_provider: "osm"  # osm, carto-dark, carto-light

  # Display defaults
  default_hours: 24
  max_positions_per_query: 5000
```

**Note**: For local development, submit data directly to `/api/submit` or use `import_db.py` for a one-time import.

---

## 6. File Structure

```
web_interface/
├── app.py                    # Flask entry point
├── config.py                 # YAML config loader
├── database.py               # Web DB read/write with explicit column mapping
├── import_db.py               # On-demand database import tool
├── requirements.txt          # Flask, PyYAML, etc.
├── CLIENT_API.md             # Client API documentation for remote nodes
├── static/
│   ├── css/
│   │   ├── style.css         # Mobile-first responsive styles
│   │   └── flatpickr.min.css
│   └── js/
│       ├── map.js            # Leaflet, markers, tracks
│       ├── api.js            # API calls
│       ├── ui.js             # Time picker, sidebar
│       └── flatpickr.min.js
└── templates/
    └── index.html            # Main page
```

---

## 7. API Endpoints

| Endpoint | Method | Description |
|----------|--------|-------------|
| `/api/drones` | GET | List unique drones in time window |
| `/api/positions` | GET | Get positions (filtered by time, optional uas_id) |
| `/api/tracks/<uas_id>` | GET | Get GeoJSON track for specific drone |
| `/api/operators` | GET | Get operator positions |
| `/api/config` | GET | Get map config (center, zoom defaults) |
| `/api/bounds` | GET | Get bounding box of all positions |
| `/api/last-timestamp` | GET | Get most recent timestamp (for bootstrapping clients) |
| `/api/submit` | POST | Submit data from remote nodes (requires API key) |

### Query Parameters
- `start`: ISO 8601 datetime
- `end`: ISO 8601 datetime
- `uas_id`: Filter to specific drone (optional)

---

## 8. Remote Node API (Data Submission)

Remote nodes push data to the web interface via HTTP API using Bearer token authentication. This is the primary data ingestion path for remote decoder nodes. For details, see `CLIENT_API.md`.

### Authentication

Remote node endpoints require Bearer token authentication:

```http
Authorization: Bearer <api_key>
```

API keys are configured in `web_config.yaml`:

```yaml
web_interface:
  api_keys:
    "your-secret-key-1": "Node-A"
    "your-secret-key-2": "Node-B"
```

The source name associated with the API key is automatically assigned to all submitted records.

### Endpoints

#### GET /api/last-timestamp

Returns the most recent timestamp in the database for a given source (or across all sources if no auth). Used by clients to determine where to resume uploading from.

**Response:**
```json
{"last_timestamp": "2026-06-02T14:30:00"}
```

#### POST /api/submit

Submit one or more Remote ID events.

**Request:**
```json
[
  {
    "timestamp": "2026-06-02T14:30:00",
    "uas_id": "drone-123",
    "latitude": 43.51746,
    "longitude": -112.01449,
    "altitude": 100.5,
    "operator_id": "op-789",
    "operator_latitude": 43.51800,
    "operator_longitude": -112.01500
  }
]
```

**Response:**
```json
{
  "success": true,
  "inserted": 1,
  "errors": [],
  "last_timestamp": "2026-06-02T14:30:00"
}
```

### Behavior

- **Duplicate Detection**: Records with matching `uas_id` + `timestamp` are silently skipped
- **Partial Success**: Valid events are processed even if some events have validation errors
- **Source Assignment**: The `source` field is automatically set based on the API key; clients should not include it
- **Resume Capability**: The `last_timestamp` returned can be used by clients to track sync progress

### Client Implementation

See `CLIENT_API.md` for detailed client implementation guide including Python example code.

---

## 9. Database Schema

Same as decoder schema, plus `source` column for multi-collector support:

```sql
CREATE TABLE remoteid(
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    source TEXT,              -- "Pi-Field-1", etc.
    timestamp DATETIME,
    mac_address TEXT,
    uas_id TEXT,
    session_id TEXT,
    latitude REAL,
    longitude REAL,
    altitude REAL,
    operator_id TEXT,
    operator_latitude REAL,
    operator_longitude REAL
);

CREATE INDEX idx_uas_time ON remoteid(uas_id, timestamp);
CREATE INDEX idx_source ON remoteid(source);
CREATE INDEX idx_timestamp ON remoteid(timestamp);
```

### Sync Log Table

```sql
CREATE TABLE sync_log(
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    source TEXT,
    last_sync DATETIME,
    records_imported INTEGER
);
```

---

## 11. Color Generation

```javascript
// Deterministic color from drone ID
function getDroneColor(uasId) {
    let hash = 0;
    for (let i = 0; i < uasId.length; i++) {
        hash = uasId.charCodeAt(i) + ((hash << 5) - hash);
    }
    const hue = Math.abs(hash % 360);
    return `hsl(${hue}, 70%, 50%)`;
}
```

---

## 12. Mobile UI Layout

```
┌────────────────────────────────┐
│  [≡]  Remote ID Tracker   [🔄] │
├────────────────────────────────┤
│                                │
│                                │
│         LEAFLET MAP          │
│    ┌────────────────────┐    │
│    │                    │    │
│    │   🚁 Drone markers │    │
│    │   👤 Operator      │    │
│    │   ➡️ Tracks        │    │
│    │                    │    │
│    └────────────────────┘    │
│                                │
├────────────────────────────────┤
│ [📅 Start] [📅 End] [24h▼]    │
└────────────────────────────────┘
```

- Swipe left on map → Opens sidebar with drone list
- Sidebar is scrollable independently from map
- 44px minimum touch targets
- Zoom buttons always visible

---

## 13. Layout Architecture

The app uses a flexbox-based layout:

```
.app-container (flex row)
├── .sidebar (fixed width, scrollable)
│   ├── .sidebar-header
│   ├── .drone-list (flex: 1, overflow-y: auto)
│   └── .sidebar-footer
└── .main (flex: 1)
    ├── .header (flex-shrink: 0)
    ├── .map-container (flex: 1, min-height: 200px)
    └── .time-controls (flex-shrink: 0)
```

- Desktop: Side-by-side layout (sidebar visible)
- Mobile: Sidebar overlays on demand
- Map always maintains minimum height of 200px

---

## 14. Installation

### Option A: Docker (Recommended)

```bash
# Production deployment
docker-compose up -d

# Development with mock data
docker-compose -f docker-compose.dev.yml up
```

See `default.web_config.yaml` for configuration options.

### Option B: Manual Installation

```bash
cd web_interface
pip install -r requirements.txt
# Edit web_config.yaml with your collector settings
python app.py --config web_config.yaml
```

Access at `http://localhost:5000`

---

## 15. Known Issues & Solutions

### Coordinate Import Issue

**Problem**: During import, columns could get misaligned if source and destination schemas differ slightly.

**Solution**: Import now uses explicit column names in SELECT queries and maps each field individually:

```python
columns = "id, timestamp, mac_address, uas_id, session_id, latitude, longitude, altitude, operator_id, operator_latitude, operator_longitude"
# ... then map by index: row[5] = latitude, row[6] = longitude, etc.
```

### Layout Scrolling Issue

**Problem**: Long drone lists would push the map off-screen.

**Solution**: Flexbox layout with `flex-shrink: 0` on header/time controls, sidebar with independent scrolling via `overflow-y: auto`.

---

## Implementation Notes

- Sync thread runs in background, pulling from configured collectors
- Database uses WAL mode for better concurrent access
- Colors are deterministic based on drone ID hash
- Mobile sidebar is swipeable and collapsible
- Time picker supports quick presets (1h, 6h, 24h, 7d, custom)
- Track opacity is adjustable via slider
- Operators and tracks can be toggled on/off
- Manual refresh button triggers immediate sync
