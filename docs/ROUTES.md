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


## `GET /capabilities`

Advertises the session config options a client may change via the `session_config_change` client
event. Each option lists its valid values and default so a client can render the choices. The
response is derived from the config schema, so it always reflects the current set of options. This
route is static and takes no parameters.

### Responses

- `200`: The available config options.

### Example

```json
{
  "options": [
    {
      "key": "tool_preset",
      "label": "Tool Preset",
      "values": ["permissive", "standard"],
      "default": "permissive"
    },
    {
      "key": "model",
      "label": "Model",
      "values": ["gpt-5.5", "claude-opus-4-7", "gemini-3.5-flash"],
      "default": "gpt-5.5"
    }
  ]
}
```

Each option carries:

- `key`: The config key, matching `config_key` in a `session_config_change` client event.
- `label`: A human-friendly display name for the key.
- `values`: The full list of valid values for the key.
- `default`: The value used when a session has not set this key.


## `GET /session-config`

Returns the current configuration for a session so a client can populate its initial display. Pass
the same `session_database` used to open the `/agent` WebSocket.

### Query Parameters

- `session_database`: Absolute path to the existing SQLite session database. The file must already
  exist, so call this after connecting the `/agent` WebSocket (which creates the database).

### Responses

- `200`: The session's current config.
- `404`: The `session_database` file does not exist.
- `422`: A required query parameter is missing.

### Example

```json
{
  "tool_preset": "permissive",
  "model": "gpt-5.5"
}
```
