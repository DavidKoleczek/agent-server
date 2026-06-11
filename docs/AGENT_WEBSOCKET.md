# Agent WebSocket Protocol

This document specifies the WebSocket protocol used to communicate with the agent server.


## Connection

Connect to the WebSocket endpoint:

```
ws://host:port/agent
```

### Query Parameters

- `working_dir`: Absolute path to the directory the agent operates in. Defaults to the server's working directory.
- `session_database`: Absolute path to a SQLite database file for persisting session history. If omitted, a session database is created under `<working_dir>/.agents/sessions/`.


## Lifecycle

1. The server accepts the WebSocket connection immediately.
2. The agent subprocess starts when the first `user_message` arrives. The server emits a `status` event with `status_id` `agent_starting`.
3. During a turn, the agent streams `activity_created`, `activity_delta`, and `activity_updated` events that build up and finalize the session activities, interleaved with `status` events.
4. The agent subprocess exits after `agent_run_ended`. A new subprocess is spawned when the next `user_message` arrives.
5. Sending `cancel` kills the agent subprocess immediately.
6. Sending `quit` closes the WebSocket connection from the server side.
7. Invalid client messages produce an `activity_created` event wrapping an `error` activity. The connection remains open.


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

### `cancel`

Kill the running agent subprocess.

```json
{
  "type": "cancel"
}
```

### `quit`

Request the server to close the WebSocket connection.

```json
{
  "type": "quit"
}
```


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
- `agent_running`: The agent has initialized and is about to start processing.
- `starting_new_turn`: The agent is beginning a new turn.
- `waiting_for_llm_response`: The agent has sent a request and is waiting for the model to respond.
- `processing_llm_response`: The agent is processing the model's response.
- `executing_tool`: The agent is executing a tool call.
- `agent_run_ended`: The agent has finished its run and the current process will close.

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
