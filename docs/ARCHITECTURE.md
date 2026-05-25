# Architecture

`agent-server` implements an AI agent and exposes it through FastAPI endpoints, namely a `/agent` websocket connection which enables bidirectional commincation.


## Overview

The server is a FastAPI application that exposes a WebSocket endpoint at `/agent`. 
When a client connects, the server spawns a dedicated subprocess to run the AI agent. 
All communication between the client and the agent flows through JSON-serialized activity messages.

The key components, in order of the request path:

1. [WebSocket route](../src/agent_server/routes/agent.py): Accepts the client connection, validates incoming messages, and forwards them inward.
2. [AgentManager](../src/agent_server/agent/agent_manager.py): Manages the agent subprocess lifecycle. Bridges activities between the WebSocket handler and the subprocess over stdin/stdout pipes.
3. [Agent worker](../src/agent_server/agent/agent_worker.py): The subprocess entry point. Reads user activities from stdin, runs the `Agent`, and writes assistant activities to stdout.
4. [Agent](../src/agent_server/agent/agent.py): The core AI loop. Calls the model, streams responses, executes tools, and manages conversation history.

