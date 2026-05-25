# Development

## Setup

Create uv virtual environment and install dependencies:

```bash
uv sync --frozen --all-extras --all-groups
```

Start the development server:

```bash
uv run agent-server
```

The server listens on `http://127.0.0.1:8000` with auto-reload enabled.

To launch under the VS Code debugger, open the Run and Debug view and select the `agent-server` configuration.

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
