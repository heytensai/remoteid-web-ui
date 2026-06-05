# Remote ID Web UI - Client API Documentation

This document explains how to write a client that submits Remote ID data to the web interface.

## Overview

Remote nodes (collectors) can submit drone detection data via HTTP API. The API uses Bearer token authentication and accepts JSON payloads.

## Authentication

All API endpoints require authentication via the `Authorization` header using a Bearer token.

```http
Authorization: Bearer <api_key>
```

API keys are configured in `web_config.yaml` and map to source names:

```yaml
api_keys:
  "node1-secure-api-key": "Node-A"
  "node2-secure-api-key": "Node-B"
```

The source name associated with the API key is automatically assigned to all submitted records.

## Endpoints

### GET /api/last-timestamp

Returns the most recent timestamp in the database. Use this to bootstrap a client and determine where to start uploading from.

#### Request

```http
GET /api/last-timestamp
Authorization: Bearer <api_key>
```

If the API key is provided, returns the most recent timestamp for that source only. If no Authorization header is provided (or public access is allowed), returns the most recent timestamp across all sources.

#### Response

```json
{
  "last_timestamp": "2026-06-02T14:30:00"
}
```

Or if no data exists:

```json
{
  "last_timestamp": null
}
```

#### Error Response

```json
{
  "success": false,
  "error": "Error message"
}
```

### POST /api/submit

Submit one or more Remote ID events to the server.

#### Request

```http
POST /api/submit
Content-Type: application/json
Authorization: Bearer <api_key>

[
  {
    "timestamp": "2026-06-02T14:30:00",
    "mac_address": "AA:BB:CC:DD:EE:FF",
    "uas_id": "drone-123",
    "session_id": "session-456",
    "latitude": 43.51746,
    "longitude": -112.01449,
    "altitude": 100.5,
    "operator_id": "op-789",
    "operator_latitude": 43.51800,
    "operator_longitude": -112.01500
  }
]
```

#### Field Reference

| Field | Type | Required | Description |
|-------|------|----------|-------------|
| `timestamp` | string (ISO 8601) | Yes | Detection time, e.g., `"2026-06-02T14:30:00"` or `"2026-06-02T14:30:00Z"` |
| `uas_id` | string | Yes | Unique drone identifier |
| `mac_address` | string | No | MAC address of the drone |
| `session_id` | string | No | Session identifier |
| `latitude` | number | No | Drone latitude (-90 to 90) |
| `longitude` | number | No | Drone longitude (-180 to 180) |
| `altitude` | number | No | Drone altitude in meters |
| `operator_id` | string | No | Operator identifier |
| `operator_latitude` | number | No | Operator latitude |
| `operator_longitude` | number | No | Operator longitude |

**Note:** The `source` field is automatically set based on the API key and should NOT be included in the payload.

#### Response

```json
{
  "success": true,
  "inserted": 5,
  "errors": [
    {
      "index": 2,
      "reason": "Missing uas_id"
    },
    {
      "index": 7,
      "reason": "Invalid timestamp: not-a-date"
    }
  ],
  "last_timestamp": "2026-06-02T14:35:22"
}
```

| Field | Description |
|-------|-------------|
| `success` | Boolean indicating if the request was processed |
| `inserted` | Number of new records inserted |
| `errors` | Array of validation errors, each with `index` (position in input array) and `reason` |
| `last_timestamp` | Most recent timestamp for this source after insert (use for resuming) |

#### Behavior

- **Duplicate Detection**: Records with matching `uas_id` + `timestamp` are silently skipped (not counted as errors)
- **Partial Success**: Valid events are processed even if some events have errors
- **Validation**: Invalid coordinates are sanitized (set to null), invalid timestamps or missing required fields generate errors
- **All-or-Nothing Per Record**: Each record is validated and inserted independently

## Client Implementation Guide

### Python Example

```python
import requests
from datetime import datetime
import time

class RemoteIDClient:
    def __init__(self, base_url: str, api_key: str):
        self.base_url = base_url.rstrip("/")
        self.api_key = api_key
        self.headers = {
            "Authorization": f"Bearer {api_key}",
            "Content-Type": "application/json",
        }
    
    def get_last_timestamp(self) -> datetime | None:
        """Get the most recent timestamp to resume from."""
        response = requests.get(
            f"{self.base_url}/api/last-timestamp",
            headers=self.headers,
        )
        response.raise_for_status()
        
        data = response.json()
        if data["last_timestamp"]:
            return datetime.fromisoformat(data["last_timestamp"])
        return None
    
    def submit_events(self, events: list[dict]) -> dict:
        """Submit events to the server."""
        response = requests.post(
            f"{self.base_url}/api/submit",
            headers=self.headers,
            json=events,
        )
        response.raise_for_status()
        return response.json()
    
    def sync_continuously(self, get_events_func, batch_size: int = 100):
        """Continuously sync events from a data source.
        
        Args:
            get_events_func: Callable that takes (start_time) and returns list of events
            batch_size: Number of events to send per request
        """
        # Start from where we left off
        last_ts = self.get_last_timestamp()
        print(f"Resuming from: {last_ts}")
        
        while True:
            # Get new events
            events = get_events_func(last_ts)
            
            if not events:
                # No new data, wait before checking again
                time.sleep(5)
                continue
            
            # Process in batches
            for i in range(0, len(events), batch_size):
                batch = events[i:i + batch_size]
                
                try:
                    result = self.submit_events(batch)
                    
                    if result["errors"]:
                        print(f"Batch had {len(result['errors'])} errors")
                        for error in result["errors"]:
                            print(f"  Index {error['index']}: {error['reason']}")
                    
                    print(f"Inserted {result['inserted']} records")
                    
                    # Update last timestamp for next iteration
                    if result["last_timestamp"]:
                        last_ts = datetime.fromisoformat(result["last_timestamp"])
                        
                except requests.exceptions.RequestException as e:
                    print(f"Upload failed: {e}")
                    time.sleep(10)  # Wait before retrying


# Example usage
if __name__ == "__main__":
    client = RemoteIDClient(
        base_url="http://localhost:5000",
        api_key="your-api-key-here",
    )
    
    # Example: Submit a single event
    events = [
        {
            "timestamp": datetime.now().isoformat(),
            "uas_id": "test-drone-001",
            "latitude": 43.51746,
            "longitude": -112.01449,
            "altitude": 50.0,
        }
    ]
    
    result = client.submit_events(events)
    print(result)
```

### Error Handling Best Practices

1. **Always check `errors` array**: Even when `success` is true, individual records may have failed validation

2. **Handle network errors**: Implement retry logic with exponential backoff for transient failures

3. **Store last_timestamp locally**: Persist the last known timestamp to resume after client restarts

4. **Batch events**: Send multiple events per request (recommended: 50-500) to reduce overhead

5. **Respect duplicate detection**: The server skips duplicates, so clients can safely retry uploads

### Bootstrap Workflow

When a client starts for the first time:

1. Query `/api/last-timestamp` to find the most recent data
2. Load local data starting from that timestamp
3. Send data in batches to `/api/submit`
4. Store the returned `last_timestamp` after each batch
5. On restart, repeat from step 1

### Rate Limiting Recommendations

While not enforced by the server, clients should:

- Limit requests to 1 per second during normal operation
- Use larger batches (100-500 events) instead of frequent small requests
- Implement backoff if receiving HTTP 500/503 errors

## Configuration

To enable API access, add API keys to `web_config.yaml`:

```yaml
web_interface:
  # ... other settings ...
  
  api_keys:
    # Format: "api_key_string": "source_name"
    # Source name will be assigned to all records from this key
    "prod-node-1-key-change-this": "Production-Node-1"
    "prod-node-2-key-change-this": "Production-Node-2"
```

**Security Note**: Use long, randomly generated API keys in production (e.g., 32+ character alphanumeric strings).
