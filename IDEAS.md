# Web Interface Improvement Ideas

Ideas for improving the Remote ID web interface. Categorized by area and rough effort level.

---

## 1. UI/UX Improvements

### 1.1 Toast Notification System
**Priority: Low-Medium | Effort: Low**

Currently errors just go to `console.error`. Replace with a toast/notification system that appears briefly in the corner of the screen. Useful for:
- Sync failures
- API errors
- "Drone selected" confirmation
- "Track loaded" feedback

### 1.2 Loading States Beyond Spinner
**Priority: Low | Effort: Low**

The loading overlay is defined in CSS but never used. Wire it up during data refresh so users see a full-screen or map-overlay spinner instead of the stale data sitting there.

### 1.3 Heatmap / Density View
**Priority: Medium | Effort: Medium**

Add a toggle to show a Leaflet heatmap layer instead of individual markers. Useful for:
- Identifying frequently used corridors
- Spotting hotspots of drone activity
- Understanding operational patterns

Would use `leaflet-heat` plugin.

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
- Group drones by source

### 2.3 Position Query Optimization
**Priority: Medium | Effort: Low**

The `/api/positions` endpoint loads all positions into memory. For large time windows:
- Add server-side streaming or chunked responses
- Consider returning only key frames (every Nth position) with an option to fetch full detail
- Add a `limit` parameter to `/api/positions` that's already accepted but could be more aggressive

### 2.4 Database Archival / Retention Policy
**Priority: Medium | Effort: Medium**

"Keep everything" (DESIGN.md) means the database grows indefinitely. Implement:
- Configurable retention policy (e.g., "keep last 90 days")
- Automatic archival of data older than X days to a separate file
- Background job to enforce retention on a schedule
- Periodic `VACUUM` to reclaim space
- Expose record counts and DB size in a stats or health endpoint

---

## 3. Sync & Backend

### 3.1 Delta Sync Instead of Full rsync
**Priority: Medium | Effort: Medium**

Currently the entire `remoteid.db` is rsynced every 30 seconds. For large databases this is wasteful. Alternatives:
- **WAL-based delta**: Read only new rows from the source DB's WAL file
- **SQLite remote query**: Use `mod_sqlite` or a lightweight protocol over SSH to query only new records
- **rsync with checksums**: Use `--checksum` flag to avoid transferring unchanged pages

### 3.2 Server-Sent Events (SSE) for Live Push
**Priority: Medium | Effort: Medium**

The app already polls at 2s/10s intervals. Instead of the WebSockets approach, use SSE which is simpler for this server-to-client-only use case. Benefits:
- Push new positions, alerts, and collector pings in real time
- Eliminate wasted bandwidth on empty polling responses
- Simpler than WebSockets (no bidirectional protocol needed)
- Reconnects automatically on connection drop

### 3.3 Health Check Endpoint
**Priority: Low | Effort: Low**

Add a `/health` endpoint that returns:
- Last sync time per remote
- Database size / record count
- Uptime
- Collector connectivity status

Useful for monitoring and alerting.

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

### 4.4 Multiple Time Ranges Simultaneously
**Priority: Low | Effort: Low**

Currently only one time window at a time. Allow overlaying two time ranges (e.g., today vs. yesterday) with different opacity to compare flight patterns.

---

## 5. Deployment & Operations

### 5.1 Systemd Service
**Priority: Medium | Effort: Low**

DESIGN.md notes "Deployment: Manual run." Add a systemd unit file so the app starts on boot and restarts on crash. Include:
- `remote-id-web.service` unit file
- Log rotation config
- Environment file for config path

### 5.2 HTTPS / Reverse Proxy Setup
**Priority: Low | Effort: Low**

Even on private networks, HTTPS is a good practice. Provide:
- Nginx/Caddy reverse proxy config with self-signed cert
- Instructions for Let's Encrypt if a public domain is available

### 5.3 Configuration Validation
**Priority: Low | Effort: Low**

Add startup validation for `web_config.yaml`:
- Validate coordinate ranges
- Warn about misconfigured tile providers
- Validate port is available

---

## 6. Frontend Architecture

### 6.1 ES Module Migration
**Priority: Medium | Effort: Medium**

`map.js` (1,454 lines) and `ui.js` (2,727 lines) are monolithic globals loaded via `<script>` tags. Convert to ES modules with a lightweight bundler (esbuild or Vite) to enable:
- Tree-shaking and code splitting
- Proper imports instead of global dependency
- Faster development iteration
- Eliminates the `no-redeclare` ESLint workaround

### 6.2 Local CDN Fallbacks
**Priority: High | Effort: Low**

All frontend dependencies (Leaflet, Flatpickr, Font Awesome) are loaded from CDNs with zero fallback. If the network is down or the CDN is blocked (common in field deployments), the entire UI breaks. Bundle local copies and use them as fallbacks — critical for a field-deployable drone tracker.

### 6.3 Keyboard Shortcuts & Accessibility
**Priority: Medium | Effort: Medium**

The app has minimal keyboard navigation. Add:
- Keyboard shortcuts: `R` for refresh, `Space` for play/pause replay, arrow keys for timeline scrubbing
- ARIA labels and `role` attributes throughout
- Focus trapping for modals (sidebar, alert log, chart)
- Visible focus indicators on all interactive elements

### 6.4 Offline / PWA Support
**Priority: Medium | Effort: Medium**

The app registers a service worker stub but there's no actual offline capability. For a field tracker, being able to view cached map tiles and last-known positions when connectivity drops would be a major reliability win. Implement a proper Workbox-based SW with stale-while-revalidate for tiles.

---

## 7. User Features

### 7.1 Structured Frontend Error Display
**Priority: Medium | Effort: Low**

Errors go to `console.error` and are invisible to users. Implement a non-intrusive error banner/toast system that surfaces API failures, sync errors, and connection drops — especially important when multiple remote collectors are involved and one might silently stop reporting.

### 7.2 Data Export Enhancements
**Priority: Medium | Effort: Low**

The app exports alert history as CSV, but there's no export for drone tracks, position data, or flight summaries. Add configurable exports (CSV, GPX, KML) for the currently visible data — a common need for post-flight analysis and compliance reporting.

### 7.3 README and Getting Started Guide
**Priority: High | Effort: Low**

The project has no README. For a project with Docker support, CLI tools (`flask auth create-user`), YAML config, and multiple deployment options, a proper README with install instructions, config examples, and troubleshooting would dramatically lower the barrier for new users.

---

## Quick Wins (Low Effort, High Impact)

1. **README** — 1 hour
2. **Toast notifications** — 1-2 hours
3. **Local CDN fallbacks** — 1 hour
4. **Loading overlay during refresh** — 1 hour
5. **Drone search/filter in sidebar** — 1 hour
6. **Health check endpoint** — 30 minutes
7. **Systemd service file** — 30 minutes
8. **Structured error display** — 1-2 hours
9. **Data export (tracks, positions)** — 2-3 hours

## Bigger Projects

1. **SSE real-time push** — 2-3 days
2. **ES module migration** — 2-3 days
3. **Track simplification** — 2-3 days
4. **Database archival / retention** — 1-2 days
5. **Keyboard shortcuts & a11y** — 2-3 days
6. **Offline / PWA support** — 2-3 days
7. **Heatmap view** — 1-2 days
8. **Measurement tools** — 1 day
