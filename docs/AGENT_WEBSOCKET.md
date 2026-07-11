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
1. The server emits `agent_starting`, places the new worker in a managed process tree, and emits `agent_ready` after initialization succeeds. Queued client events are not forwarded before `agent_ready`.
1. The main agent subprocess stays alive for the WebSocket connection and processes `user_message` events as they arrive.
1. During a turn, the agent streams `activity_created`, `activity_delta`, and `activity_updated` events that build up and finalize the session activities, interleaved with `status` events.
1. When a tool call requires approval, the agent emits a `task` activity with `permission` set to `pending` and pauses the turn until the client sends a `permission_change` event accepting or denying it, after which the turn resumes.
1. When the main agent runs the `agent` tool, it starts a sub-agent. The sub-agent's streaming events are forwarded over the same WebSocket, and its session activities carry the sub-agent's `agent_id`.
1. Sending `session_config_change` updates a session setting; the server persists it, applies it to the main agent
   and active sub-agents, and replies with a `session_config_changed` event.
1. Sending `cancel` terminates the main worker and all descendants, emits cancellation status events, and starts a fresh worker.
1. Sending `quit` terminates the complete worker process tree and manager, then closes the WebSocket connection from the server side.
1. If the main worker exits unexpectedly, the server terminates its remaining descendants, emits an error activity, and starts a fresh worker after bounded exponential backoff.
1. Invalid client messages produce an `activity_created` event wrapping an `error` activity. The connection remains open.


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

Approve or deny a pending tool call. The server routes the event to `agent_id`, re-evaluates the tool call identified
by `id`, and resumes that agent.

```json
{
  "type": "permission_change",
  "agent_id": "main",
  "id": "fc_123",
  "permission": "accepted"
}
```

- `agent_id`: The agent that owns the tool call. Defaults to `main`.
- `id`: The `id` of the `task` activity (the tool call) to update.
- `permission`: The decision for the call. One of `accepted`, `denied`, `pending`.

### `cancel`

Cancel the current agent run. The server terminates the main worker and all descendants, discards queued client events for that run, and starts a fresh worker.

```json
{
  "type": "cancel"
}
```

### `quit`

Request a clean shutdown. The server terminates the complete worker process tree and manager, then closes the WebSocket connection.

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

These are the streaming events that wrap the lifecycle signals and the evolving session activities. Every streaming
event includes `agent_id`, which identifies the agent that produced it. The session activity payloads carried inside
`activity_created`, `activity_delta`, and `activity_updated` are documented under
[Session Activities](#session-activities).

### `status`

Reports the agent's current lifecycle phase. The `status_id` field identifies the phase.

```json
{
  "type": "status",
  "agent_id": "main",
  "status_id": "agent_running"
}
```

`status_id` is one of:

- `agent_starting`: The server is spawning the agent subprocess.
- `agent_ready`: The agent subprocess has started and is ready to receive client events.
- `agent_cancelling`: The server is terminating the current worker process tree.
- `agent_cancelled`: The current worker process tree exited due to cancellation.
- `agent_stopping`: The server is terminating the worker process tree for connection shutdown.
- `agent_stopped`: The worker process tree exited due to connection shutdown.
- `agent_running`: The agent reconciliation loop is processing available work.
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
  "agent_id": "main",
  "activity": { ... }
}
```

### `activity_delta`

Patches an existing activity. Intended for streaming efficiency, so only the fields that changed are present. `activity_id` identifies the target activity.

```json
{
  "type": "activity_delta",
  "agent_id": "main",
  "activity_id": "fc_123",
  "delta": {
    "content_delta": "appended text",
    "argument_delta": { "key": "file_path", "value": "C:\\path\\to\\project\\src\\main.py" },
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
  "agent_id": "main",
  "activity": { ... }
}
```

### `session_config_changed`

Confirms that a session config value changed, in response to a `session_config_change` client event. Carries the value that was actually applied.

```json
{
  "type": "session_config_changed",
  "agent_id": "main",
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
- `agent_id`: Identifier for the agent that produced the activity. The main agent uses `main`; sub-agents use generated IDs like `sub-1234abcd`.
- `type`: The activity type, one of the values below.
- `state`: Lifecycle state, one of `in_progress`, `complete`, `error`, `cancelled`.
- `timestamp`: ISO 8601 timestamp in UTC.

### `user`

A message from the user.

```json
{
  "id": "msg_123",
  "agent_id": "main",
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
  "agent_id": "main",
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
  "agent_id": "main",
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
  "agent_id": "main",
  "type": "task",
  "state": "complete",
  "timestamp": "2026-06-05T12:00:00Z",
  "name": "read",
  "permission": "accepted",
  "arguments": { "file_path": "C:\\path\\to\\project\\src\\main.py" },
  "result": "file contents",
  "sub_agent_id": null
}
```

- `name`: The name of the tool being called.
- `permission`: The permission decision for the call. One of `accepted`, `denied`, `pending`. Defaults to `pending`.
- `arguments`: The tool call arguments as a JSON object, or `null` until they are known.
- `result`: The tool output, or `null` until the call completes.
- `sub_agent_id`: The generated agent ID when the task launches a sub-agent, otherwise `null`.

For sub-agent calls, `name` is `agent`; `arguments` contains `description`, `prompt`, `subagent_type`, and the injected `sub_agent_id`; and `result` is the sub-agent's final assistant message.

### `error`

An error surfaced as an activity.

```json
{
  "id": "err_123",
  "agent_id": "main",
  "type": "error",
  "state": "error",
  "timestamp": "2026-06-05T12:00:00Z",
  "error_type": "invalid_client_activity_format",
  "detail": "Validation error description"
}
```

- `error_type`: A short, machine-readable error category.
- `detail`: A human-readable description of the error.
