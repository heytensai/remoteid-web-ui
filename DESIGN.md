# Web Interface Design Document
## Remote ID CalTopo Tracker

**Version**: 1.1  
**Date**: 2026-05-29  
**Status**: Implemented & Refined

---

## 1. Overview

A standalone web interface to visualize Remote ID drone data on an interactive map using Leaflet.js with OpenStreetMap tiles. The web interface runs separately from the Raspberry Pi decoder and pulls data via rsync (for remote) or direct file access (for local development).

---

## 2. Goals

- Display current and historical drone positions on a map
- Show drone tracks (flight paths) over time
- Display operator locations alongside drone positions
- Filter by date/time with Flatpickr picker
- Default time window: last 24 hours
- Mobile-responsive design
- Architecture supports future multi-collector setup

---

## 3. Architecture

```
┌─────────────────────┐         ┌─────────────────────┐
│   Raspberry Pi      │         │   Web Server        │
│                     │         │                     │
│  ┌───────────────┐  │         │  ┌───────────────┐  │
│  │   decoder.py  │  │         │  │  Flask App    │  │
│  └───────┬───────┘  │         │  │               │  │
│          │ write    │         │  │  ┌─────────┐  │  │
│          ▼          │         │  │  │  sync   │  │  │
│  ┌───────────────┐  │         │  │  │ thread  │  │  │
│  │  remoteid.db  │◀─┼─rsync───┤  │  └────┬────┘  │  │
│  │  (SQLite)     │  │  30s    │  │       │       │  │
│  └───────────────┘  │         │  │  ┌────▼────┐  │  │
└─────────────────────┘         │  │   web.db   │  │  │
                                │  │  (SQLite)  │  │  │
                                │  └────────────┘  │  │
                                └──────────────────┘  │
                                         │            │
                                         │ HTTP       │
                                         ▼            │
                                 ┌─────────────────────┐ │
                                 │   Browser (Mobile)  │◀┘
                                 │   Leaflet Map       │
                                 └─────────────────────┘
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
| Sync Interval | 30 seconds (configurable) |
| Track Simplification | No (until usage data) |
| Data Retention | Keep everything |
| Deployment | Manual run |
| Authentication | None (private network) |

---

## 5. Configuration

```yaml
# web_config.yaml
web_interface:
  host: "0.0.0.0"
  port: 5000
  database_path: "./web.db"
  
  # Sync settings
  sync_interval: 30  # seconds
  collectors:
    # Remote collector (Raspberry Pi via SSH/rsync)
    - name: "Pi-Field-1"
      host: "rpi.local"
      remote_db_path: "/opt/remoteid/remoteid.db"
    
    # Local collector (for development - no SSH/rsync needed)
    # - name: "Local-Dev"
    #   remote_db_path: "../remoteid.db"  # Path to local database file
  
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

**Note**: For local development, omit the `host` field to use direct file access instead of rsync.

---

## 6. File Structure

```
web_interface/
├── app.py                    # Flask entry point
├── config.py                 # YAML config loader
├── database.py               # Web DB read/write with explicit column mapping
├── sync.py                   # Background sync thread (rsync + local)
├── requirements.txt          # Flask, PyYAML, etc.
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
| `/api/sync` | POST | Manually trigger sync from collectors |

### Query Parameters
- `start`: ISO 8601 datetime
- `end`: ISO 8601 datetime
- `uas_id`: Filter to specific drone (optional)

---

## 8. Database Schema

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

## 9. Sync Mechanism

The sync system supports both remote (rsync) and local collectors:

```python
def sync_loop():
    while True:
        for collector in config.collectors:
            if collector.host:
                # Remote: rsync over SSH
                rsync(collector.host, collector.remote_db_path, '/tmp/incoming.db')
                import_new_records('/tmp/incoming.db', collector.name)
            else:
                # Local: direct file import
                import_new_records(collector.remote_db_path, collector.name)
        time.sleep(config.sync_interval)
```

### Key Implementation Details

- **Explicit Column Mapping**: Import uses explicit column names to avoid order mismatches between source and destination databases
- **Incremental Sync**: Only imports records newer than the last sync timestamp
- **Duplicate Prevention**: Checks for existing records by `uas_id` + `timestamp` before inserting
- **WAL Mode**: Database uses Write-Ahead Logging for better concurrent access

---

## 10. Color Generation

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

## 11. Mobile UI Layout

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

## 12. Layout Architecture

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

## 13. Installation

```bash
cd web_interface
pip install -r requirements.txt
# Edit web_config.yaml with your collector settings
python app.py --config web_config.yaml
```

Access at `http://localhost:5000`

---

## 14. Known Issues & Solutions

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
