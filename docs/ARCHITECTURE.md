# Architecture

`agent-server` implements an AI agent and exposes it through FastAPI endpoints, namely a `/agent` websocket connection which enables bidirectional communication.


## Overview

The server is a FastAPI application that exposes a WebSocket endpoint at `/agent`.
When a client connects to `/agent`, the server starts a dedicated worker inside an OS managed process tree. 
The worker stays alive for the connection and is restarted if it crashes or the client cancels the current run.
The complete tree is terminated before a worker is restarted or shut down, preventing sub-agent workers from being orphaned.
All communication between the client and the agent flows through JSON-serialized activity messages.
Session activities, chat history, and session config are persisted to the client-provided SQLite database.

The key components, in order of the request path:

1. [WebSocket route](../src/agent_server/routes/agent.py): Accepts the client connection, validates incoming messages, and forwards them inward. It stops the agent manager on quit or disconnect.
1. [AgentManager](../src/agent_server/agent/agent_manager.py): Manages worker readiness, event forwarding, lifecycle statuses, restart backoff, cancellation, and shutdown.
1. [Managed worker process](../src/agent_server/agent/processes/managed_worker_process.py): Starts a worker only after process-tree containment is established.
1. [Agent worker](../src/agent_server/agent/agent_worker.py): Adapts stdin/stdout pipes to in-process queues, calls `Agent.start()`, and exits if the agent returns unexpectedly.
1. [Agent](../src/agent_server/agent/agent.py): The core AI loop. Calls the model, streams responses, executes tools, manages conversation history, and coordinates mode changes and structured input requests.
1. [Agent tool](../src/agent_server/core/subagent/tool.py): Lets the main agent launch a sub-agent through another `AgentManager`. Sub-agent activity is persisted in the same session database with a distinct `agent_id` and streamed back to the client.
