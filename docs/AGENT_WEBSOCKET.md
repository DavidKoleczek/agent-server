# Agent WebSocket Protocol

This document specifies the WebSocket protocol used to communicate with the agent server.


## Connection

Connect to the WebSocket endpoint:

```
ws://host:port/agent
```

### Query Parameters

- `working_dir`: Absolute path to the directory the agent operates in. Defaults to the server's working directory.
- `session_database`: Absolute path to a SQLite database file for persisting session history. The client owns this path; if it is omitted the server rejects the connection by closing with code 1008.


## Lifecycle

1. The server accepts the WebSocket connection immediately.
2. The server starts an agent subprocess immediately and emits `agent_starting`, then `agent_ready` once the subprocess is ready.
3. The agent subprocess stays alive for the WebSocket connection and processes `user_message` events as they arrive.
4. During a turn, the agent streams `activity_created`, `activity_delta`, and `activity_updated` events that build up and finalize the session activities, interleaved with `status` events.
5. When a tool call requires approval, the agent emits a `task` activity with `permission` set to `pending` and pauses the turn until the client sends a `permission_change` event accepting or denying it, after which the turn resumes.
6. Sending `session_config_change` updates a session setting; the server persists it, applies it to the running agent, and replies with a `session_config_changed` event.
7. Sending `cancel` kills the current agent subprocess, emits cancellation status events, and starts a fresh subprocess.
8. Sending `quit` stops the agent subprocess and manager, then closes the WebSocket connection from the server side.
9. If the agent subprocess exits unexpectedly, the server emits an error activity and starts a fresh subprocess.
10. Invalid client messages produce an `activity_created` event wrapping an `error` activity. The connection remains open.


## Client Activities

Messages sent by the client to the server. All messages are JSON text frames.

### `user_message`

Send a message to the agent.

```json
{
  "type": "user_message",
  "content": "Your message here"
}
```

### `permission_change`

Approve or deny a pending tool call. The server re-evaluates the tool call identified by `id` using the new permission and resumes the turn.

```json
{
  "type": "permission_change",
  "id": "fc_123",
  "permission": "accepted"
}
```

- `id`: The `id` of the `task` activity (the tool call) to update.
- `permission`: The decision for the call. One of `accepted`, `denied`, `pending`.

### `cancel`

Cancel the current agent run. The server kills the current agent subprocess, discards queued client events for that run, and starts a fresh subprocess.

```json
{
  "type": "cancel"
}
```

### `quit`

Request a clean shutdown. The server stops the agent subprocess and manager, then closes the WebSocket connection.

```json
{
  "type": "quit"
}
```

### `session_config_change`

Change a session config value. The server validates the value, persists it, applies it to the running agent, and replies with a `session_config_changed` event. An invalid value produces an `error` activity and no change. See [`GET /capabilities`](ROUTES.md#get-capabilities) for the valid keys and values.

```json
{
  "type": "session_config_change",
  "config_key": "model",
  "new_value": "claude-opus-4-7"
}
```

- `config_key`: The config key to change. One of `tool_preset`, `model`.
- `new_value`: The new value for the key.


## Server Activities

Messages sent by the server to the client. All messages are JSON text frames.

These are the streaming events that wrap the lifecycle signals and the evolving session activities. The session activity payloads carried inside `activity_created`, `activity_delta`, and `activity_updated` are documented under [Session Activities](#session-activities).

### `status`

Reports the agent's current lifecycle phase. The `status_id` field identifies the phase.

```json
{
  "type": "status",
  "status_id": "agent_running"
}
```

`status_id` is one of:

- `agent_starting`: The server is spawning the agent subprocess.
- `agent_ready`: The agent subprocess has started and is ready to receive client events.
- `agent_cancelling`: The server is cancelling the current agent subprocess.
- `agent_cancelled`: The current agent subprocess exited due to cancellation.
- `agent_stopping`: The server is stopping the agent subprocess for connection shutdown.
- `agent_stopped`: The agent subprocess exited due to connection shutdown.
- `agent_running`: The agent loop is running and waiting for or processing client events.
- `agent_turn_ended`: The current agent turn has ended.
- `waiting_for_llm_response`: The agent has sent a request and is waiting for the model to respond.
- `processing_llm_response`: The agent is processing the model's response.
- `executing_tool`: The agent is executing a tool call.
- `starting_new_turn`: The agent is beginning a new turn.

### `activity_created`

Emitted when a new activity begins. The `activity` field carries the full session activity in its initial state, which is usually `in_progress` for streamed activities.

```json
{
  "type": "activity_created",
  "activity": { ... }
}
```

### `activity_delta`

Patches an existing activity. Intended for streaming efficiency, so only the fields that changed are present. `activity_id` identifies the target activity.

```json
{
  "type": "activity_delta",
  "activity_id": "msg_123",
  "delta": {
    "content_delta": "appended text",
    "argument_delta": { "key": "path", "value": "src/main.py" },
    "result_delta": "appended tool output",
    "permission": "accepted"
  }
}
```

The `delta` fields are all optional:

- `content_delta`: Text to append to the activity's `content`.
- `argument_delta`: A single task argument key and its current value. The value replaces any prior value for that key.
- `result_delta`: Text to append to a task activity's `result`.
- `permission`: An updated permission decision for a task activity. One of `accepted`, `denied`, `pending`.

### `activity_updated`

Carries the complete, finalized activity, replacing any previously created or patched copy. Emitted when an activity reaches a terminal state.

```json
{
  "type": "activity_updated",
  "activity": { ... }
}
```

### `session_config_changed`

Confirms that a session config value changed, in response to a `session_config_change` client event. Carries the value that was actually applied.

```json
{
  "type": "session_config_changed",
  "config_key": "model",
  "new_value": "claude-opus-4-7"
}
```

- `config_key`: The config key that changed.
- `new_value`: The value now in effect.


## Session Activities

Session activities are the persisted records of a conversation. They are delivered inside `activity_created` and `activity_updated` events and can also be loaded later from the session database.

Every activity shares a common base:

- `id`: Unique identifier for the activity.
- `type`: The activity type, one of the values below.
- `state`: Lifecycle state, one of `in_progress`, `complete`, `error`, `cancelled`.
- `timestamp`: ISO 8601 timestamp in UTC.

### `user`

A message from the user.

```json
{
  "id": "msg_123",
  "type": "user",
  "state": "complete",
  "timestamp": "2026-06-05T12:00:00Z",
  "content": "Your message here"
}
```

### `assistant`

A message from the model.

```json
{
  "id": "msg_123",
  "type": "assistant",
  "state": "complete",
  "timestamp": "2026-06-05T12:00:00Z",
  "content": "The assistant's response"
}
```

### `reasoning`

A reasoning summary from the model.

```json
{
  "id": "rs_123",
  "type": "reasoning",
  "state": "complete",
  "timestamp": "2026-06-05T12:00:00Z",
  "content": "The reasoning summary"
}
```

### `task`

A tool call made by the agent.

```json
{
  "id": "fc_123",
  "type": "task",
  "state": "complete",
  "timestamp": "2026-06-05T12:00:00Z",
  "name": "read_file",
  "permission": "accepted",
  "arguments": { "path": "src/main.py" },
  "result": "file contents"
}
```

- `name`: The name of the tool being called.
- `permission`: The permission decision for the call. One of `accepted`, `denied`, `pending`. Defaults to `pending`.
- `arguments`: The tool call arguments as a JSON object, or `null` until they are known.
- `result`: The tool output, or `null` until the call completes.

### `error`

An error surfaced as an activity.

```json
{
  "id": "err_123",
  "type": "error",
  "state": "error",
  "timestamp": "2026-06-05T12:00:00Z",
  "error_type": "invalid_client_activity_format",
  "detail": "Validation error description"
}
```

- `error_type`: A short, machine-readable error category.
- `detail`: A human-readable description of the error.
