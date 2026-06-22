# Collector API

Collectors (mobile or fixed) authenticate with a Bearer token and use the ping
endpoint to check in and optionally report GPS position.

## Types

Collectors have a `type` configured on the server:

- **`mobile`** — Reports position via the ping API. Stale detection applies: if
  no ping is received within `position_stale_minutes`, the marker turns gray.
- **`fixed`** — Static position from config. If no ping is received within
  `position_stale_minutes`, the marker turns gray (position stays, color fades).

Both types follow the same stale detection — the marker turns gray when the
last ping is older than `position_stale_minutes`.

## Configuration

Each collector must be defined in `web_config.yaml` under `web_interface.collectors`:

```yaml
web_interface:
  position_stale_minutes: 30  # minutes without ping before stale
  collectors:
    - name: "Car 1"
      type: "mobile"
      api_key: "collector-car-1-key"
      color: "#e67e22"
    - name: "Base Station"
      type: "fixed"
      api_key: "collector-base-key"   # optional, needed for ping auth
      lat: 37.7749
      lon: -122.4194
      color: "#3498db"
```

| Field | Required | Description |
|-------|----------|-------------|
| `name` | Yes | Display name shown on the map and footer |
| `type` | Yes | `"mobile"` or `"fixed"` |
| `api_key` | Yes (for ping auth) | Secret key for Bearer token authentication |
| `color` | No | CSS color for map marker (default: `#e67e22`) |
| `lat` | Only for fixed | Latitude |
| `lon` | Only for fixed | Longitude |

## GET /api/submit/ping

Combined heartbeat and position-reporting endpoint. All collectors use this
single endpoint — it logs a check-in (updates Last Seen) and optionally
stores a GPS position.

### Request

```
GET /api/submit/ping?lat=37.7749&lon=-122.4194 HTTP/1.1
Host: drone.example.com
Authorization: Bearer <api_key>
```

| Header | Value |
|--------|-------|
| `Authorization` | `Bearer <api_key>` — the collector's `api_key` from config |

| Query param | Type | Required | Description |
|------------|------|----------|-------------|
| `lat` | number | No | Latitude (-90 to 90). Mobile collectors should always send this. |
| `lon` | number | No | Longitude (-180 to 180). Mobile collectors should always send this. |

Omitting `lat`/`lon` is a simple heartbeat (updates Last Seen only).

### Response

**Success (200):**
```json
{
  "success": true,
  "source": "Car 1"
}
```

**Unauthorized (401):**
```json
{
  "success": false,
  "error": "Unauthorized"
}
```

### Example (curl)

```bash
# Heartbeat only (no position update)
curl -H "Authorization: Bearer collector-car-1-key" \
  https://drone.example.com/ddgv/api/submit/ping

# Heartbeat with position
curl "https://drone.example.com/ddgv/api/submit/ping?lat=37.7749&lon=-122.4194" \
  -H "Authorization: Bearer collector-car-1-key"
```

## Footer Detail Panel

All collectors appear in the "Remote Sources" footer detail panel alongside
API data sources, each showing its name, Last Seen time, and Data time.
Collectors are tagged with a **Collector** badge (vs **API** for data
submitters).

Stale detection for collectors uses `position_stale_minutes` (configurable),
while API submitters use a fixed 20-minute threshold.

## Notes

- The collector does **not** set its own name, color, or type — those come
  from the server config.
- Only the latest position per collector is stored. Repeated pings replace
  the previous position.
- The map refreshes collector status on the same polling cycle as drone data.
- API keys and stale timeout are hot-reloadable — changes take effect within
  10 seconds.
- Fixed collectors without an `api_key` still render at their configured
  position but will always appear stale (no check-in possible).
