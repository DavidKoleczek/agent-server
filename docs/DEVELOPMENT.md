# Development

## Setup

Create uv virtual environment and install dependencies:

```bash
uv sync --frozen --all-extras --all-groups
```

Start the development server:

```bash
uv run agent-server --reload
```

Connect the development client from another terminal:

```bash
uv run python scripts\ws_client.py --working-dir C:\path\to\workspace
```

Use `--session-database` to resume a specific session and display its persisted activities. 
Run `/help` in the client to list the supported agent events. 
The `AGENT_CONFIG` value at the top of `scripts\ws_client_support\client.py` provides defaults for omitted command-line options.

To launch both processes under the VS Code debugger, run the `Launch basic client and agent-server` compound.

## Code Quality

Format code:

```bash
uv run ruff format
```

Lint code:

```bash
uv run ruff check --fix
```

Type check:

```bash
uv run ty check
```

## Testing

Run tests:

```bash
uv run pytest
```

## Update Dependencies

```bash
uv sync -U --all-extras --all-groups
```
