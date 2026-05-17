# Development

## Setup

Create uv virtual environment and install dependencies:

```bash
uv sync --frozen --all-extras --all-groups
```

Start the development server:

```bash
# TODO
```

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
