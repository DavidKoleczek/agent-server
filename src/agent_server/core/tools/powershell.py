from __future__ import annotations

import asyncio
import base64
from collections.abc import Sequence
import json
from pathlib import Path
import re
import secrets
import shutil
import subprocess
import sys
import tempfile
import time
from typing import IO, TYPE_CHECKING

from liquid import render
from openai.types.responses.function_tool_param import FunctionToolParam
from pydantic import BaseModel, ConfigDict, ValidationError

from agent_server.core.tools._protocol import FunctionTool
from agent_server.core.tools._utils import ConstraintPolicy, ConstraintRule

if TYPE_CHECKING or sys.platform == "win32":
    from agent_server.agent.processes.windows_job import WindowsJob

_DEFAULT_TIMEOUT_SEC = 120
_MAXIMUM_TIMEOUT_SEC = 1800
_TOOL_OUTPUT_LIMIT_CHARS = 40000
_PARSER_TIMEOUT_SEC = 5

POWERSHELL_TOOL_NAME = "powershell"
POWERSHELL_TOOL_DESCRIPTION_TEMPLATE = """Executes a given PowerShell command with optional timeout. \
Commands run in the configured working directory. Shell state does not persist between commands.

IMPORTANT: This tool is for terminal operations via PowerShell like git, npm, docker, and PS cmdlets. \
DO NOT use it for file operations (reading, writing, editing, searching, finding files). Use the specialized tools for this instead.

Before executing the command, please follow these steps:

Directory Verification:
- If the command will create new directories or files, first use `Get-ChildItem` (or `ls`) to verify the parent directory exists and is the correct location

Command Execution:
- Always quote file paths that contain spaces with double quotes
- Capture the output of the command.

{{ powershell_version_instructions }}
- Variables use $ prefix: $myVar = "value"
- Escape character is backtick (`), not backslash
- Use Verb-Noun cmdlet naming: Get-ChildItem, Set-Location, New-Item, Remove-Item
- Pipe operator | works similarly to bash but passes objects, not text
- Use Select-Object, Where-Object, ForEach-Object for filtering and transformation
- String interpolation: "Hello $name" or "Hello $($obj.Property)"
- Registry access uses PSDrive prefixes: `HKLM:\\SOFTWARE\\...`, `HKCU:\\...` - NOT raw `HKEY_LOCAL_MACHINE\\...`
- Environment variables: read with `$env:NAME`, set with `$env:NAME = "value"` (NOT `Set-Variable` or bash `export`)
- Call native exe with spaces in path via call operator: `& "C:\\Program Files\\App\\app.exe" arg1 arg2`

Unix commands that DO NOT exist in PowerShell - use the equivalent instead:
- ls -> Get-ChildItem
- cd -> Set-Location
- cat -> Get-Content
- head / tail -> `Get-Content file -TotalCount N` / `-Tail N`; piped: `| Select-Object -First N` / `-Last N`
- which -> `(Get-Command name).Source`
- touch -> `if (-not (Test-Path path)) { New-Item -ItemType File path }` (NEVER use `New-Item -Force` on a file - it truncates existing content)
- wc -l -> `(Get-Content file | Measure-Object -Line).Lines`
- mkdir -p -> `New-Item -ItemType Directory -Force path` (`-p` is not a PowerShell flag)
- rm -rf -> `Remove-Item -Recurse -Force path`
- ln -s -> `New-Item -ItemType SymbolicLink -Path link -Target target`
- chmod / chown -> not applicable on Windows; use `icacls` only if ACL changes are required
- 2>/dev/null -> `2>$null` (but stderr is captured for you - usually unnecessary)
- VAR=x cmd -> `$env:VAR = 'x'; cmd` (PowerShell has no inline env-var prefix)
- Bash control flow (`if [ -f x ]`, `for x in *`, backtick ``cmd`` substitution) is a parser error - use `if (Test-Path x)`, `foreach ($x in ...)`, `$(cmd)`

Exit-code note: `-ErrorAction SilentlyContinue` suppresses error OUTPUT but the cmdlet failure still causes this tool to report exit 1. \
To make a cmdlet failure truly non-fatal, promote it to terminating, catch it, and explicitly succeed: \
`try { Cmdlet ... -ErrorAction Stop } catch {}; exit 0`.

Interactive and blocking commands (will hang - this tool runs with -NonInteractive):
- NEVER use `Read-Host`, `Get-Credential`, `Out-GridView`, `$Host.UI.PromptForChoice`, or `pause`
- Destructive cmdlets (`Remove-Item`, `Stop-Process`, `Clear-Content`, etc.) may prompt for confirmation. \
Add `-Confirm:$false` when you intend the action to proceed. Use `-Force` for read-only/hidden items.
- Never use `git rebase -i`, `git add -i`, or other commands that open an interactive editor

Passing multiline strings (commit messages, file content) to native executables:
- Use a single-quoted here-string so PowerShell does not expand `$` or backticks inside. \
The closing `'@` MUST be at column 0 (no leading whitespace) on its own line - indenting it is a parse error:
<example>
git commit -m @'
Commit message here.
Second line with $literal dollar signs.
'@
</example>
- Use `@'...'@` (single-quoted, literal) not `@"..."@` (double-quoted, interpolated) unless you need variable expansion
- For arguments containing `-`, `@`, or other characters PowerShell parses as operators, use the stop-parsing token: `git log --% --format=%H`

Usage notes:
- The command argument is required.
- It is very helpful if you write a clear, concise description of what this command does.
- You can specify an optional timeout in seconds (up to {{ maximum_timeout }} seconds). If not specified, commands will timeout after {{ default_timeout }} seconds.
- If the output exceeds {{ max_output_chars }} characters, output will be truncated before being returned to you.
- Background commands ignore the timeout and return immediately with an ID. Standard output and error are written to separate files.
- Avoid using PowerShell to run commands that have dedicated tools, unless explicitly instructed.
- When issuing multiple commands:
  - If the commands are independent and can run in parallel, make multiple {{ powershell_tool_name }} tool calls in a single message.
  - If the commands depend on each other and must run sequentially, chain them in a single {{ powershell_tool_name }} call (see chaining syntax above).
  - Use `;` only when you need to run commands sequentially but don't care if earlier commands fail.
  - DO NOT use newlines to separate commands (newlines are ok in quoted strings and here-strings)
  - Do NOT prefix commands with `cd` or `Set-Location` -- the working directory is already set to the correct project directory automatically.

# Committing changes with git

Only create commits when requested by the user. If unclear, ask first. When the user asks you to create a new git commit, follow these steps carefully:

Git Safety Protocol:
- NEVER update the git config
- NEVER run destructive/irreversible git commands (like push --force, hard reset, etc) unless the user explicitly requests them.
  - Before running requested destructive operations (e.g., git reset --hard, git push --force, git checkout --), 
consider whether there is a safer alternative that achieves the same goal. Only use destructive operations when they are truly the best approach.
- Never skip hooks (--no-verify) or bypass signing (--no-gpg-sign, -c commit.gpgsign=false) unless the user has explicitly asked for it. If a hook fails, investigate and fix the underlying issue.
- NEVER run force push to main/master, warn the user if they request it
- NEVER attribute commits to anyone/anything else other than the user
- Avoid git commit --amend. ONLY use --amend when ALL conditions are met:
  (1) User explicitly requested amend, OR commit SUCCEEDED but pre-commit hook auto-modified files that need including
  (2) HEAD commit was created by you in this conversation (verify: git log -1 --format=\'%an %ae\')
  (3) Commit has NOT been pushed to remote (verify: git status shows "Your branch is ahead")
- CRITICAL: If commit FAILED or was REJECTED by hook, NEVER amend - fix the issue and create a NEW commit
- CRITICAL: If you already pushed to remote, NEVER amend unless user explicitly requests it (requires force push)
- NEVER commit changes unless the user explicitly asks you to. It is VERY IMPORTANT to only commit when explicitly asked, otherwise the user will feel that you are being too proactive."""

COMMAND_DESCRIPTION_DESCRIPTION = """Clear, concise description of what this command does in 5-10 words, in active voice. Examples:
Input: ls
Output: List files in current directory

Input: git status
Output: Show working tree status

Input: npm install
Output: Install package dependencies

Input: mkdir foo
Output: Create directory 'foo'"""

POWERSHELL_7_INSTRUCTIONS = """PowerShell edition: PowerShell 7+ (pwsh)
- Pipeline chain operators `&&` and `||` ARE available and work like bash. Prefer `cmd1 && cmd2` over `cmd1; cmd2` when cmd2 should only run if cmd1 succeeds.
- Ternary (`$cond ? $a : $b`), null-coalescing (`??`), and null-conditional (`?.`) operators are available.
- Default file encoding is UTF-8 without BOM."""

# Used for any version 5.1
POWERSHELL_5_DETAILS = """- Pipeline chain operators `&&` and `||` are NOT available - they cause a parser error. To run B only if A succeeds: `A; if ($?) { B }`. To chain unconditionally: `A; B`.
- Ternary (`?:`), null-coalescing (`??`), and null-conditional (`?.`) operators are NOT available. Use `if/else` and explicit `$null -eq` checks instead.
- Avoid `2>&1` on native executables. In 5.1, redirecting a native command's stderr inside PowerShell wraps each line in an ErrorRecord (NativeCommandError) and sets `$?` to `$false` even when the exe returned exit code 0. stderr is already captured for you - don't redirect it.
- Default file encoding is UTF-16 LE (with BOM). When writing files other tools will read, pass `-Encoding utf8` to `Out-File`/`Set-Content`.
- `ConvertFrom-Json` returns a PSCustomObject, not a hashtable. `-AsHashtable` is not available."""

POWERSHELL_5_INSTRUCTIONS = """PowerShell edition: Windows PowerShell 5.1 (powershell.exe)
{{ powershell_5_details }}"""

# Used for any other version
POWERSHELL_OTHER_INSTRUCTIONS = """PowerShell edition: unknown - assume Windows PowerShell 5.1 for compatibility
{{ powershell_5_details }}"""


def _create_powershell_tool_definition(description: str) -> FunctionToolParam:
    return {
        "type": "function",
        "name": POWERSHELL_TOOL_NAME,
        "description": description,
        "parameters": {
            "type": "object",
            "properties": {
                "command": {"type": "string", "description": "The PowerShell command to execute"},
                "description": {
                    "type": "string",
                    "description": COMMAND_DESCRIPTION_DESCRIPTION,
                },
                "timeout": {"type": "number", "description": "Optional timeout in seconds"},
                "run_in_background": {
                    "type": ["boolean", "null"],
                    "description": (
                        "Set to true to run this command in the background. Output file paths are returned immediately."
                    ),
                },
            },
            "required": ["command", "timeout", "description", "run_in_background"],
            "additionalProperties": False,
        },
        "strict": True,
    }


KILL_POWERSHELL_TOOL_NAME = "KillPowershell"
KILL_POWERSHELL_TOOL_DEFINITION: FunctionToolParam = {
    "type": "function",
    "name": KILL_POWERSHELL_TOOL_NAME,
    "description": """- Kills a running background PowerShell shell by its ID
- Takes a shell_id parameter identifying the shell to kill
- Returns a success or failure status 
- Use this tool when you need to terminate a long-running shell""",
    "parameters": {
        "type": "object",
        "properties": {
            "shell_id": {"type": "string", "description": "The ID of the background PowerShell shell to kill"}
        },
        "required": ["shell_id"],
        "additionalProperties": False,
    },
    "strict": True,
}


class PowershellConstraintRule(ConstraintRule):
    match_command_name: bool = False


class PowershellToolConfig(BaseModel):
    working_dir: Path
    powershell_path: Path | None = None
    rules: list[PowershellConstraintRule] = []
    default_policy: ConstraintPolicy = ConstraintPolicy.ASK
    default_timeout_sec: float = _DEFAULT_TIMEOUT_SEC
    maximum_timeout_sec: float = _MAXIMUM_TIMEOUT_SEC
    tool_output_limit_chars: int = _TOOL_OUTPUT_LIMIT_CHARS
    background_output_dir: Path | None = None


class _BackgroundTask(BaseModel):
    """Used to track a background PowerShell command and its associated resources."""

    model_config = ConfigDict(arbitrary_types_allowed=True)

    process: asyncio.subprocess.Process
    job: WindowsJob
    command: str
    output_file: Path
    error_file: Path
    completion: asyncio.Task[int]


class PowershellTool(FunctionTool):
    def __init__(self, config: PowershellToolConfig) -> None:
        self.config = config

        self._powershell_executable = config.powershell_path or _find_powershell()
        self._powershell_version = (
            _get_powershell_version(self._powershell_executable) if self._powershell_executable is not None else None
        )

        self._background_tasks: dict[str, _BackgroundTask] = {}

    def get_tool_defs(self) -> dict[str, FunctionToolParam]:
        if self._powershell_version is not None and self._powershell_version[0] >= 7:
            powershell_version_instructions = POWERSHELL_7_INSTRUCTIONS
        elif self._powershell_version == (5, 1):
            powershell_version_instructions = render(
                POWERSHELL_5_INSTRUCTIONS,
                powershell_5_details=POWERSHELL_5_DETAILS,
            )
        else:
            powershell_version_instructions = render(
                POWERSHELL_OTHER_INSTRUCTIONS,
                powershell_5_details=POWERSHELL_5_DETAILS,
            )
        compiled_description = render(
            POWERSHELL_TOOL_DESCRIPTION_TEMPLATE,
            powershell_version_instructions=powershell_version_instructions,
            maximum_timeout=self.config.maximum_timeout_sec,
            default_timeout=self.config.default_timeout_sec,
            max_output_chars=self.config.tool_output_limit_chars,
            powershell_tool_name=POWERSHELL_TOOL_NAME,
        )
        function_tool_param = _create_powershell_tool_definition(compiled_description)
        return {
            POWERSHELL_TOOL_NAME: function_tool_param,
            KILL_POWERSHELL_TOOL_NAME: KILL_POWERSHELL_TOOL_DEFINITION,
        }

    def check_constraint(self, **arguments: object) -> ConstraintPolicy:
        """Check every parsed PowerShell command against the ordered rules."""
        shell_id = arguments.get("shell_id")
        if shell_id is not None:
            return ConstraintPolicy.ALLOW

        command = arguments.get("command")
        if command is None or self._powershell_executable is None:
            return ConstraintPolicy.DENY

        commands = _parse_powershell_commands(str(command), self._powershell_executable)
        if commands is None:
            return ConstraintPolicy.DENY
        if not commands:
            return self.config.default_policy

        policies = [
            _check_single_command(parsed_command, self.config.rules, self.config.default_policy)
            for parsed_command in commands
        ]
        if ConstraintPolicy.DENY in policies:
            return ConstraintPolicy.DENY
        if ConstraintPolicy.ASK in policies:
            return ConstraintPolicy.ASK
        return ConstraintPolicy.ALLOW

    async def execute(self, **arguments: object) -> str:
        command_arg = arguments.get("command")
        timeout_arg = arguments.get("timeout")
        timeout = float(str(timeout_arg)) if timeout_arg is not None else None
        run_in_background_arg = arguments.get("run_in_background")
        run_in_background = bool(run_in_background_arg) if run_in_background_arg is not None else None
        shell_id_arg = arguments.get("shell_id")

        if shell_id_arg is not None:
            return await self._kill_shell(str(shell_id_arg))
        elif command_arg is not None:
            return await self._execute_command(str(command_arg), timeout, run_in_background)
        else:
            return json.dumps({"errors": "Required arguments missing."})

    async def _execute_command(self, command: str, timeout: float | None, run_in_background: bool | None) -> str:
        if self._powershell_executable is None:
            return json.dumps(
                {
                    "stdout": "",
                    "stderr": "",
                    "errors": "PowerShell is unavailable. Do not attempt to use this tool again until it is made available.",
                }
            )

        if run_in_background:
            return await self._execute_in_background(command)

        effective_timeout = timeout if timeout is not None else self.config.default_timeout_sec
        if effective_timeout < 0:
            return json.dumps(
                {
                    "stdout": "",
                    "stderr": "",
                    "errors": f"invalid timeout: {effective_timeout}. Must be non-negative.",
                }
            )
        effective_timeout = min(effective_timeout, self.config.maximum_timeout_sec)

        start_time = time.perf_counter()
        process, job = await self._start_process(
            command,
            stdout=asyncio.subprocess.PIPE,
            stderr=asyncio.subprocess.PIPE,
        )
        timed_out = False
        try:
            try:
                stdout_bytes, stderr_bytes = await asyncio.wait_for(
                    process.communicate(),
                    timeout=effective_timeout,
                )
            except TimeoutError:
                timed_out = True
                # Terminating the job stops the PowerShell host and every native child it created.
                job.terminate()
                stdout_bytes, stderr_bytes = await process.communicate()
        finally:
            job.close()

        duration_sec = time.perf_counter() - start_time
        stdout = stdout_bytes.decode(errors="replace")
        stderr = stderr_bytes.decode(errors="replace")
        limit = self.config.tool_output_limit_chars
        truncation_message = "...\n[reached maximum powershell command output characters]"
        if len(stdout) > limit:
            stdout = stdout[:limit] + truncation_message
        if len(stderr) > limit:
            stderr = stderr[:limit] + truncation_message

        result: dict[str, object] = {
            "stdout": stdout,
            "stderr": stderr,
            "exit_code": process.returncode,
            "duration_sec": round(duration_sec, 3),
        }
        if timed_out:
            result["errors"] = f"command terminated (exceeded {effective_timeout} sec timeout)"
        return json.dumps(result)

    async def _execute_in_background(self, command: str) -> str:
        task_id = secrets.token_hex(4)
        output_dir = self.config.background_output_dir or Path(tempfile.gettempdir())
        output_dir.mkdir(parents=True, exist_ok=True)
        output_file = output_dir / f"{task_id}.output"
        error_file = output_dir / f"{task_id}.error"

        # The child inherits these handles, so the parent can close them immediately after startup.
        with output_file.open("wb") as output_stream, error_file.open("wb") as error_stream:
            process, job = await self._start_process(
                command,
                stdout=output_stream,
                stderr=error_stream,
            )

        completion = asyncio.create_task(_wait_and_close_job(process, job))
        self._background_tasks[task_id] = _BackgroundTask(
            process=process,
            job=job,
            command=command,
            output_file=output_file,
            error_file=error_file,
            completion=completion,
        )
        return (
            f"Command running in background with ID: {task_id}. "
            f"Output is being written to: {output_file}. Errors are being written to: {error_file}"
        )

    async def _start_process(
        self,
        command: str,
        *,
        stdout: int | IO[bytes],
        stderr: int | IO[bytes],
    ) -> tuple[asyncio.subprocess.Process, WindowsJob]:
        executable = self._powershell_executable
        if executable is None:
            raise RuntimeError("PowerShell is unavailable.")

        job = WindowsJob()
        process: asyncio.subprocess.Process | None = None
        try:
            powershell_arguments = ("-NoLogo", "-NoProfile", "-NonInteractive", "-Command")
            # This runner starts before the user command and waits until the process belongs to its Job Object.
            powershell_command_runner = (
                # Emit redirected output as UTF-8 so Python can decode it consistently across PowerShell versions.
                "$global:OutputEncoding = [Console]::OutputEncoding = [Text.UTF8Encoding]::new($false); "
                # Assume success until the injected user script records its final PowerShell status.
                "$global:__AgentServerCommandSucceeded = $true; "
                # Clear stale native exit state so cmdlet-only commands cannot inherit an unrelated exit code.
                "$global:__AgentServerLastExitCode = $null; "
                # Block until the parent assigns the process to its Job Object and sends the encoded command.
                "$encodedCommand = [Console]::In.ReadLine(); "
                # Convert the decoded command text to a script block and invoke it in a child scope.
                "& ([ScriptBlock]::Create([Text.Encoding]::UTF8.GetString("
                # Decode the base64 payload without introducing another PowerShell quoting layer.
                "[Convert]::FromBase64String($encodedCommand)))); "
                # Return normally when the user script reports success; otherwise select a failure exit code.
                "if (-not $global:__AgentServerCommandSucceeded) { "
                # Check whether the failure came from a native executable with a usable exit code.
                "if ($null -ne $global:__AgentServerLastExitCode -and "
                # Preserve that native exit code so callers receive the original process result.
                "$global:__AgentServerLastExitCode -ne 0) { exit $global:__AgentServerLastExitCode }; "
                # Use a generic nonzero code for PowerShell cmdlet and language failures.
                "exit 1 }"
            )
            process = await asyncio.create_subprocess_exec(
                str(executable),
                *powershell_arguments,
                powershell_command_runner,
                stdin=asyncio.subprocess.PIPE,
                stdout=stdout,
                stderr=stderr,
                cwd=str(self.config.working_dir),
                creationflags=subprocess.CREATE_NO_WINDOW,
            )
            job.assign(process.pid)

            # The runner cannot execute user code until this write releases its stdin gate.
            stdin = process.stdin
            if stdin is None:
                raise RuntimeError("PowerShell process stdin is unavailable.")

            # The child script scope records its final status in globals that the runner can read.
            instrumented_command = (
                f"{command}\n"
                "$global:__AgentServerCommandSucceeded = $?\n"
                "$global:__AgentServerLastExitCode = $LASTEXITCODE"
            )
            # Base64 preserves arbitrary PowerShell syntax without adding another quoting layer.
            encoded_command = base64.b64encode(instrumented_command.encode()) + b"\n"
            stdin.write(encoded_command)
            await stdin.drain()
            stdin.close()
        except BaseException:
            # Process and Job Object cleanup must also run when the surrounding asyncio task is cancelled.
            try:
                if process is not None and process.returncode is None:
                    process.kill()
                    await process.wait()
            finally:
                job.close()
            raise

        return process, job

    async def _kill_shell(self, shell_id: str) -> str:
        task = self._background_tasks.get(shell_id)
        if task is None:
            return json.dumps({"errors": f"No background shell found with ID: {shell_id}"})

        if task.process.returncode is not None:
            await task.completion
            del self._background_tasks[shell_id]
            return json.dumps({"message": f"Shell {shell_id} had already completed ({task.command})"})

        task.job.terminate()
        await task.completion
        del self._background_tasks[shell_id]
        return json.dumps({"message": f"Successfully killed shell: {shell_id} ({task.command})"})


async def _wait_and_close_job(process: asyncio.subprocess.Process, job: WindowsJob) -> int:
    try:
        return await process.wait()
    finally:
        # KILL_ON_JOB_CLOSE also terminates descendants that outlive the PowerShell host.
        job.close()


_PARSE_COMMAND = r"""
$ErrorActionPreference = 'Stop'
$utf8 = [System.Text.UTF8Encoding]::new($false)
[Console]::InputEncoding = $utf8
[Console]::OutputEncoding = $utf8

$source = [Console]::In.ReadToEnd()
$tokens = $null
$parseErrors = $null
$ast = [System.Management.Automation.Language.Parser]::ParseInput(
    $source,
    [ref]$tokens,
    [ref]$parseErrors
)

$commands = @(
    $ast.FindAll(
        {
            param($node)
            $node -is [System.Management.Automation.Language.CommandAst]
        },
        $true
    ) | ForEach-Object {
        [pscustomobject]@{
            text = $_.Extent.Text
            static_name = $_.GetCommandName()
        }
    }
)

[pscustomobject]@{
    commands = $commands
    parse_error_count = @($parseErrors).Count
} | ConvertTo-Json -Compress -Depth 3
"""


class _ParsedPowershellCommand(BaseModel):
    model_config = ConfigDict(extra="forbid", frozen=True)

    text: str
    static_name: str | None


class _PowershellParseResult(BaseModel):
    model_config = ConfigDict(extra="forbid", frozen=True)

    commands: list[_ParsedPowershellCommand]
    parse_error_count: int


def _parse_powershell_commands(command: str, executable: Path) -> list[_ParsedPowershellCommand] | None:
    """Return every command AST extent, or None when parsing cannot be trusted."""
    try:
        parse_command = base64.b64encode(_PARSE_COMMAND.encode("utf-16-le")).decode("ascii")
        result = subprocess.run(
            [
                str(executable),
                "-NoLogo",
                "-NoProfile",
                "-NonInteractive",
                "-EncodedCommand",
                parse_command,
            ],
            input=command.encode(),
            stdout=subprocess.PIPE,
            stderr=subprocess.DEVNULL,
            check=False,
            timeout=_PARSER_TIMEOUT_SEC,
            creationflags=subprocess.CREATE_NO_WINDOW,
        )
    except (OSError, subprocess.TimeoutExpired):
        return None

    if result.returncode != 0:
        return None

    try:
        parsed_result = _PowershellParseResult.model_validate_json(result.stdout.decode("utf-8-sig"))
    except (UnicodeDecodeError, ValidationError):
        return None
    if parsed_result.parse_error_count != 0:
        return None
    return parsed_result.commands


def _check_single_command(
    command: _ParsedPowershellCommand,
    rules: Sequence[PowershellConstraintRule],
    default_policy: ConstraintPolicy,
) -> ConstraintPolicy:
    for rule in rules:
        target = command.static_name if rule.match_command_name else command.text
        if target is None:
            continue
        pattern = f"{rule.pattern[:-2]}*" if rule.pattern.endswith(":*") else rule.pattern
        wildcard_expression = re.escape(pattern).replace(r"\*", ".*")
        if re.fullmatch(wildcard_expression, target, flags=re.DOTALL) is not None:
            return rule.policy
    return default_policy


def _get_powershell_version(executable: Path) -> tuple[int, int] | None:
    """Returns the major and minor version of the given PowerShell executable, or None if it cannot be determined."""
    try:
        powershell_arguments = ("-NoLogo", "-NoProfile", "-NonInteractive", "-Command")
        powershell_version_command = (
            '[Console]::Out.Write("$($PSVersionTable.PSVersion.Major).$($PSVersionTable.PSVersion.Minor)")'
        )
        result = subprocess.run(
            [str(executable), *powershell_arguments, powershell_version_command],
            stdin=subprocess.DEVNULL,
            stdout=subprocess.PIPE,
            stderr=subprocess.DEVNULL,
            check=False,
            timeout=5,
            creationflags=subprocess.CREATE_NO_WINDOW,
        )
    except (OSError, subprocess.TimeoutExpired):
        return None

    if result.returncode != 0:
        return None

    version_parts = result.stdout.decode("utf-8-sig", errors="replace").strip().split(".")
    if len(version_parts) != 2:
        return None
    try:
        return int(version_parts[0]), int(version_parts[1])
    except ValueError:
        return None


def _find_powershell() -> Path | None:
    """Finds the path to a PowerShell executable on Windows, preferring PowerShell 7+ (pwsh) over Windows PowerShell 5.1 (powershell).
    Returns None if no executable is found or if not running on Windows."""
    if sys.platform != "win32":
        return None

    for executable_name in ("pwsh.exe", "powershell.exe"):
        executable_path = shutil.which(executable_name)
        if executable_path is None:
            continue

        executable = Path(executable_path)
        return executable

    return None
