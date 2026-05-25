# WebSocket Protocol

This document specifies the WebSocket protocol used to communicate with the agent server.


## Connection

Connect to the WebSocket endpoint:

```
ws://host:port/agent
```

### Query Parameters

- `working_dir`: Absolute path to the directory the agent operates in. Defaults to the server's working directory.
- `chat_file`: Absolute path to a JSON file for persisting conversation history. If omitted, a session file is created under `<working_dir>/.agents/sessions/`.


## Lifecycle

1. The server accepts the WebSocket connection immediately.
2. The agent subprocess starts when the first `user_message` arrives.
3. The agent emits `ready` followed by `turn_start` when it begins processing.
4. During a turn, the agent streams `openai_stream` events and may emit a final `router_response`.
5. When the turn completes, the agent emits `turn_end`.
6. The agent subprocess exits after `turn_end`. A new subprocess is spawned when the next `user_message` arrives.
7. Sending `cancel` kills the agent subprocess immediately.
8. Sending `quit` closes the WebSocket connection from the server side.
9. Invalid client messages produce an `error` activity. The connection remains open.


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

### `ready`

Emitted when the agent has initialized and is about to start processing.

```json
{
  "type": "ready"
}
```

### `turn_start`

Emitted at the beginning of an agent turn. Currently the same as ready.

```json
{
  "type": "turn_start"
}
```

### `turn_end`

Emitted when the agent has finished its turn and will imminently be returning.

```json
{
  "type": "turn_end"
}
```

### `error`

```json
{
  "type": "error",
  "error_type": "invalid_client_activity_format",
  "detail": "Validation error description"
}
```

### `openai_stream`

A streaming event from the model. Emitted as the model generates its response.

```json
{
  "type": "openai_stream",
  "model_name": "gpt-5.5",
  "stream_event": { ... }
}
```

`stream_event` is an [OpenAI `ResponseStreamEvent` object](https://developers.openai.com/api/reference/resources/responses/streaming-events). 
The exact events that are used, especially for different models, is determined by [interop-router](https://github.com/DavidKoleczek/interop-router)

### `router_response`

The complete model response, emitted after all streaming events for a model call.

```json
{
  "type": "router_response",
  "response": { ... }
}
```

`response` is the `RouterResponse` object from interop-router.
