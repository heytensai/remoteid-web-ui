# Web Interface Improvement Ideas

Ideas for improving the Remote ID CalTopo Tracker web interface. Categorized by area and rough effort level.

---

## 1. UI/UX Improvements

### 1.1 Toast Notification System
**Priority: Low-Medium | Effort: Low**

Currently errors just go to `console.error` (ui.js:339). Replace with a toast/notification system that appears briefly in the corner of the screen. Useful for:
- Sync failures
- API errors
- "Drone selected" confirmation
- "Track loaded" feedback

### 1.2 Loading States Beyond Spinner
**Priority: Low | Effort: Low**

The loading overlay is defined in CSS but never used. Wire it up during data refresh so users see a full-screen or map-overlay spinner instead of the stale data sitting there.

### 1.3 Drone Detail Panel ✅ **COMPLETED**
**Priority: Medium | Effort: Medium**

Clicking a drone in the sidebar opens a detail panel showing:
- Full flight history in a mini chart (altitude over time)
- Speed calculation between consecutive points
- Total flight distance
- Number of positions recorded
- Operator info if available

Moved from sidebar to bottom of map view alongside time controls.

### 1.4 Heatmap / Density View
**Priority: Medium | Effort: Medium**

Add a toggle to show a Leaflet heatmap layer instead of individual markers. Useful for:
- Identifying frequently used corridors
- Spotting hotspots of drone activity
- Understanding operational patterns

Would use `leaflet-heat` plugin.

### 1.5 Playback Controls
**Priority: Medium | Effort: Medium**

Add a timeline scrubber / playback controls to animate drone positions over time. Users could:
- Set a start and end time with a fixed step interval
- Watch drones move along their tracks in real-time animation
- Pause, step forward/backward

This would be a significant UX win for post-mission analysis.

---

## 2. Data & Performance

### 2.1 Track Simplification
**Priority: High (at scale) | Effort: Medium**

DESIGN.md notes "Track Simplification: No (until usage data)." When datasets grow large, full-fidelity polylines will slow down the map. Implement:
- **Viewport-aware simplification**: Only simplify tracks that are far from the visible viewport
- **Level-of-detail**: Use Douglas-Peucker or similar algorithm with a tolerance that increases as the user zooms out
- **Configurable max points per track** with a "show full track" expand option

### 2.2 Pagination / Lazy Loading for Drone List
**Priority: Medium | Effort: Low**

If hundreds of drones are active, the sidebar list could become very long. Consider:
- Virtual scrolling for the drone list
- Search/filter by uas_id or mac_address
- Group drones by source/collector

### 2.3 Position Query Optimization
**Priority: Medium | Effort: Low**

The `/api/positions` endpoint loads all positions into memory. For large time windows:
- Add server-side streaming or chunked responses
- Consider returning only key frames (every Nth position) with an option to fetch full detail
- Add a `limit` parameter to `/api/positions` that's already accepted but could be more aggressive

### 2.4 Database Vacuum / Archival
**Priority: Low | Effort: Low**

"Keep everything" (DESIGN.md:73) means the database grows indefinitely. Consider:
- Automatic archival of data older than X days to a separate file
- Periodic `VACUUM` to reclaim space
- Configurable retention policy (e.g., "keep last 90 days")

---

## 3. Sync & Backend

### 3.1 Delta Sync Instead of Full rsync
**Priority: Medium | Effort: Medium**

Currently the entire `remoteid.db` is rsynced every 30 seconds. For large databases this is wasteful. Alternatives:
- **WAL-based delta**: Read only new rows from the source DB's WAL file
- **SQLite remote query**: Use `mod_sqlite` or a lightweight protocol over SSH to query only new records
- **rsync with checksums**: Use `--checksum` flag to avoid transferring unchanged pages

### 3.2 WebSocket for Real-Time Updates
**Priority: Medium | Effort: Medium**

Instead of polling the API every time the user changes the time window, use WebSockets to push new positions as they arrive. This would:
- Eliminate the need for the 30s sync gap during live operations
- Allow instant map updates when new positions arrive
- Reduce server load from repeated API queries

### 3.3 Health Check Endpoint
**Priority: Low | Effort: Low**

Add a `/health` endpoint that returns:
- Last sync time per collector
- Database size / record count
- Uptime
- Collector connectivity status

Useful for monitoring and alerting.

### 3.4 Graceful Degradation for Failed Collectors
**Priority: Low | Effort: Low**

If a remote collector is unreachable (SSH down, rsync times out), the sync thread currently fails silently. Add:
- Per-collector failure logging with timestamp
- Visual indicator in the UI showing which collectors are online/offline
- Exponential backoff for failed sync attempts

---

## 4. Mapping & Visualization

### 4.1 Custom Tile Layers
**Priority: Low | Effort: Low**

Currently supports osm, carto-dark, carto-light. Could add:
- Satellite imagery (Esri World Imagery)
- Topographic maps
- User-provided tile URLs (for offline maps or custom basemaps)

### 4.2 Measurement Tools
**Priority: Low | Effort: Low**

Add a ruler tool to measure distances on the map. Useful for:
- Measuring distance between drone and operator
- Measuring distance between waypoints
- Understanding coverage area

Would use `leaflet-measure` or a custom implementation.

### 4.3 Altitude Color-Coding
**Priority: Low | Effort: Low**

Instead of (or in addition to) ID-based coloring, allow coloring markers by altitude band. This would make it easy to spot:
- Drones at different flight levels
- Climb/descent profiles
- Altitude anomalies

### 4.4 Geofencing / Alerts
**Priority: Medium | Effort: Medium**

Allow users to draw polygons on the map and flag drones that enter/exit them. Useful for:
- No-fly zone monitoring
- Perimeter breach detection
- Area-of-interest tracking

---

## 5. Deployment & Operations

### 5.1 Systemd Service
**Priority: Medium | Effort: Low**

DESIGN.md notes "Deployment: Manual run." Add a systemd unit file so the app starts on boot and restarts on crash. Include:
- `remote-id-web.service` unit file
- Log rotation config
- Environment file for config path

### 5.2 Docker Support ✅ **COMPLETED**
**Priority: Medium | Effort: Medium**

Containerize the app for easy deployment:
- `Dockerfile` with Python slim base
- `docker-compose.yml` for local dev with a mock data source
- Volume mounts for config and database persistence

### 5.3 HTTPS / Reverse Proxy Setup
**Priority: Low | Effort: Low**

Even on private networks, HTTPS is a good practice. Provide:
- Nginx/Caddy reverse proxy config with self-signed cert
- Instructions for Let's Encrypt if a public domain is available

### 5.4 Configuration Validation
**Priority: Low | Effort: Low**

Add startup validation for `web_config.yaml`:
- Check that collector paths exist (local) or SSH is reachable (remote)
- Validate coordinate ranges
- Warn about misconfigured tile providers
- Validate port is available

---

## 6. Security

### 6.1 Basic Authentication
**Priority: Low | Effort: Low**

Even on private networks, a simple auth layer prevents accidental exposure:
- Flask-HTTPAuth with basic auth
- Configurable username/password in YAML
- Or token-based auth via query parameter

### 6.2 Rate Limiting
**Priority: Low | Effort: Low**

Add Flask-Limiter to prevent abuse:
- Rate limit `/api/positions` and `/api/tracks` (heavy queries)
- Allow higher limits for `/api/drones` (light queries)
- Configurable limits in YAML

---

## 7. Data Sources

### 7.3 Multiple Time Ranges Simultaneously
**Priority: Low | Effort: Low**

Currently only one time window at a time. Allow overlaying two time ranges (e.g., today vs. yesterday) with different opacity to compare flight patterns.

### 7.4 Live Decoder Integration
**Priority: Medium | Effort: Medium**

Instead of rsync from the SQLite file, connect directly to the decoder's in-memory data via:
- A shared memory segment
- A lightweight IPC protocol
- A direct database connection (if decoder writes to the same DB)

This would eliminate the 30-second sync gap entirely for live operations.

---

## Quick Wins (Low Effort, High Impact)

1. **Toast notifications** — 1-2 hours
2. **Systemd service file** — 30 minutes
3. **Loading overlay during refresh** — 1 hour
4. **Drone search/filter in sidebar** — 1 hour
5. **Health check endpoint** — 30 minutes
6. **SQLite VACUUM on startup** — 15 minutes
7. **Rate limiting** — 1 hour
8. **Basic authentication** — 1-2 hours

## Bigger Projects

1. **Playback controls** — 1-2 days
2. **WebSocket real-time updates** — 2-3 days
3. **Track simplification** — 2-3 days
4. **Geofencing** — 2-3 days
5. **Delta sync** — 3-5 days
6. **Heatmap view** — 1-2 days
7. **Docker support** — 1-2 days
8. **Drone detail panel** — 2-3 days
