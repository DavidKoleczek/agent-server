# Routes

This document describes the HTTP routes exposed by the server. The `/agent` WebSocket is
documented separately in [Agent WebSocket](AGENT_WEBSOCKET.md).

Route handlers live under [`src/agent_server/routes/`](../src/agent_server/routes/) and are
registered in [`main.py`](../src/agent_server/main.py).


## `GET /healthz`

Liveness probe. Returns `200` once the application has started.

```json
{ "status": "ok" }
```


## `GET /resume`

Loads a prior session and returns its persisted activity history. This lets a client restore the
displayable state of a previous session without reconnecting to the agent.

### Query Parameters

- `working_dir`: Absolute path to the directory the session operated in.
- `session_database`: Absolute path to the existing SQLite session database to resume. The file
  must already exist.

### Responses

- `200`: A JSON array of session activity records, ordered by position. Each record carries its
  metadata and the full activity payload documented under
  [Session Activities](AGENT_WEBSOCKET.md#session-activities).
- `404`: The `session_database` file does not exist.
- `422`: A required query parameter is missing.

### Example

```json
[
  {
    "id": "rs_123",
    "position": 0,
    "timestamp": "2026-06-10T12:51:51.123723Z",
    "type": "reasoning",
    "state": "complete",
    "activity": {
      "id": "rs_123",
      "type": "reasoning",
      "state": "complete",
      "timestamp": "2026-06-10T12:51:51.123723Z",
      "content": "The reasoning summary"
    }
  }
]
```
