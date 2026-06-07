# General Instructions

Agent Server is a product and any development of it must have the highest standards of quality, security, and reliability.

- Shortcuts are not appropriate. When in doubt, you must work with the user for guidance.
- Any documentation you write, including in the README.md, should be clear, concise, and accurate like the official documentation of other production-grade applications.
- Don't generate characters that a user could not type on a standard keyboard like fancy arrows within regular code or documentation.
- Any *new* comments should be necessary (do not driveby remove existing comments). A necessary comment captures intent that cannot be encoded in names, types, or structure. They should concisely describe the "why", only used to record rationale, trade-offs, links to specs/papers, or non-obvious domain insights. They should add signal that code cannot.
- Prefer "soft-wrap" for prose comments. Split up lines at natural breaking points at around 120 characters. Hard wraps tend to break mid-clause and look mechanical, and they create noisy diffs whenever the prose changes. Generally keep your comments in the style of the others in the project.
- The current code in the package should be treated as an example of high quality code. Make sure to follow its style and tackle issues in similar ways where appropriate.
- Don't generate characters that a user could not type on a standard keyboard like fancy arrows.
- Anything is possible. Do not blame external factors after something doesn't work on the first try. Instead, investigate and test assumptions through debugging through first principles.
- When writing documentation
  - Keep it very concise
  - No emojis or em dashes.
  - Documentation should be written exactly like it is for production-grade, polished projects.
  - Please do not use tables unless asked for or they are absolutely the right choice.
- Prefer to ask the user more questions to clarify their needs.
- NEVER store or update memories.
- New features should be continually organized in directories and files. We want things to be modular, so if a piece does not work well its easy to replace. Files like the server entry point should remain small when possible.


# Python Development Instructions
- `ty` by Astral is used for type checking. Always add appropriate type hints such that the code would pass ty's type check.
- Follow the Google Python Style Guide.
- After each code change, checks are automatically run. Fix any issues that arise.
- **IMPORTANT**: The checks will remove any unused imports after you make an edit to a file. So if you need to use a new import, be sure to use it FIRST (or do your edits at the same time) or else it will be automatically removed. DO NOT use local imports to get around this.
- At this stage of the project, NEVER add imports to __init__.py files. Leave them empty unless absolutely necessary.
- Always prefer pathlib for dealing with files. Use `Path.open` instead of `open`.
- When using pathlib, **always** Use `.parents[i]` syntax to go up directories instead of using `.parent` multiple times.
- When writing tests, use pytest and pytest-asyncio.
- Prefer using loguru for logging instead of the built-in logging module. Do not add logging unless requested.
- NEVER use `# type: ignore`. It is better to leave the issue and have the user work with you to fix it.
- Don't put types in quotes unless it is absolutely necessary to avoid circular imports and forward references.
- When adding new dependencies, you **must** use `uv add <package>`. AFTER that, update the `pyproject.toml` to follow the convention for versions like the other dependencies.
- When constructing long strings like prompts for LLMs, use `python-liquid`'s `render` function:
```python
from liquid import render

print(render("Hello, {{ you }}!", you="World"))
# Hello, World!
```
- To learn about how packages work, you should read from the relevant source code. This is especially important when determining which types to use.
- Do not manually run checks like `uv run ruff format` or `uv run pytest`. They will either be run automatically after code changes or user triggered.


# Development Environment

- Assume that everything is being tested in PowerShell and Windows Terminal first class. With support for inside VSCode as a close second.
- Assume that everything needs to work on Windows 10/11 as first class


# Key Files

reference/ includes the source code for key libraries as a reference:
- `agent-tui` - The main consumer of agent-server



@README.md

@docs/DEVELOPMENT.md
