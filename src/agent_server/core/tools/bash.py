import asyncio
from collections.abc import Sequence
import contextlib
from dataclasses import dataclass
import json
from pathlib import Path
import secrets
import tempfile
import time
from typing import Any

from openai.types.responses.function_tool_param import FunctionToolParam
from pydantic import BaseModel
from tree_sitter import Language, Node, Parser
import tree_sitter_bash as tsbash

from agent_server.core.tools._protocol import FunctionTool
from agent_server.core.tools._utils import ConstraintPolicy, ConstraintRule

BASH_LANGUAGE = Language(tsbash.language())
_parser = Parser(BASH_LANGUAGE)

BASH_TOOL_NAME = "bash"

TOOL_DESCRIPTION = """Executes a given bash command in a persistent shell session with optional timeout, ensuring proper handling and security measures.

IMPORTANT: This tool is for terminal operations like git, npm, docker, etc. DO NOT use it for file operations (reading, writing, editing, searching, finding files) - use the specialized tools for this instead.

Before executing the command, please follow these steps:

1. Directory Verification:
   - If the command will create new directories or files, first use `ls` to verify the parent directory exists and is the correct location
   - For example, before running "mkdir foo/bar", first use `ls foo` to check that "foo" exists and is the intended parent directory

2. Command Execution:
   - Always quote file paths that contain spaces with double quotes (e.g., cd "path with spaces/file.txt")
   - Examples of proper quoting:
     - cd "/Users/name/My Documents" (correct)
     - cd /Users/name/My Documents (incorrect - will fail)
     - python "/path/with spaces/script.py" (correct)
     - python /path/with spaces/script.py (incorrect - will fail)
   - After ensuring proper quoting, execute the command.
   - Capture the output of the command.

Usage notes:
  - The command argument is required.
  - You can specify an optional timeout in seconds (up to {{maximum seconds}} seconds). If not specified, commands will timeout after {{timeout}} seconds.
  - It is very helpful if you write a clear, concise description of what this command does in 5-10 words.
  - If the output exceeds 30000 characters, output will be truncated before being returned to you.
  - You can use the `run_in_background` parameter to run the command in the background, which allows you to continue working while the command runs. \
You can monitor the output using the Bash tool as it becomes available. You do not need to use \'&\' at the end of the command when using this parameter.
  
  - Avoid using Bash with the `find`, `grep`, `cat`, `head`, `tail`, `sed`, `awk`, or `echo` commands, unless explicitly instructed or when these commands are truly necessary for the task. Instead, always prefer using the dedicated tools for these commands:
    - File search: Use Glob (NOT find or ls)
    - Content search: Use Grep (NOT grep or rg)
    - Read files: Use Read (NOT cat/head/tail)
    - Edit files: Use Edit (NOT sed/awk)
    - Write files: Use Write (NOT echo >/cat <<EOF)
    - Communication: Output text directly (NOT echo/printf)
  - When issuing multiple commands:
    - If the commands are independent and can run in parallel, make multiple Bash tool calls in a single message. For example, if you need to run "git status" and "git diff", send a single message with two Bash tool calls in parallel.
    - If the commands depend on each other and must run sequentially, use a single Bash call with \'&&\' to chain them together (e.g., `git add . && git commit -m "message" && git push`). \
For instance, if one operation must complete before another starts (like mkdir before cp, Write before Bash for git operations, or git add before git commit), run these operations sequentially instead.
    - Use \';\' only when you need to run commands sequentially but don\'t care if earlier commands fail
    - DO NOT use newlines to separate commands (newlines are ok in quoted strings)
  - Try to maintain your current working directory throughout the session by using absolute paths and avoiding usage of `cd`. You may use `cd` if the User explicitly requests it.
    <good-example>
    pytest /foo/bar/tests
    </good-example>
    <bad-example>
    cd /foo/bar && pytest tests
    </bad-example>

# Committing changes with git

Only create commits when requested by the user. If unclear, ask first. When the user asks you to create a new git commit, follow these steps carefully:

Git Safety Protocol:
- NEVER update the git config
- NEVER run destructive/irreversible git commands (like push --force, hard reset, etc) unless the user explicitly requests them 
- NEVER skip hooks (--no-verify, --no-gpg-sign, etc) unless the user explicitly requests it
- NEVER run force push to main/master, warn the user if they request it
- Avoid git commit --amend. ONLY use --amend when ALL conditions are met:
  (1) User explicitly requested amend, OR commit SUCCEEDED but pre-commit hook auto-modified files that need including
  (2) HEAD commit was created by you in this conversation (verify: git log -1 --format=\'%an %ae\')
  (3) Commit has NOT been pushed to remote (verify: git status shows "Your branch is ahead")
- CRITICAL: If commit FAILED or was REJECTED by hook, NEVER amend - fix the issue and create a NEW commit
- CRITICAL: If you already pushed to remote, NEVER amend unless user explicitly requests it (requires force push)
- NEVER commit changes unless the user explicitly asks you to. It is VERY IMPORTANT to only commit when explicitly asked, otherwise the user will feel that you are being too proactive.

1. You can call multiple tools in a single response. When multiple independent pieces of information are requested and all commands are likely to succeed, run multiple tool calls in parallel for optimal performance. run the following bash commands in parallel, each using the Bash tool:
  - Run a git status command to see all untracked files.
  - Run a git diff command to see both staged and unstaged changes that will be committed.
  - Run a git log command to see recent commit messages, so that you can follow this repository\'s commit message style.
2. Analyze all staged changes (both previously staged and newly added) and draft a commit message:
  - Summarize the nature of the changes (eg. new feature, enhancement to an existing feature, bug fix, refactoring, test, docs, etc.). \
Ensure the message accurately reflects the changes and their purpose (i.e. "add" means a wholly new feature, "update" means an enhancement to an existing feature, "fix" means a bug fix, etc.).
  - Do not commit files that likely contain secrets (.env, credentials.json, etc). Warn the user if they specifically request to commit those files
  - Draft a concise (1-2 sentences) commit message that focuses on the "why" rather than the "what"
  - Ensure it accurately reflects the changes and their purpose
3. You can call multiple tools in a single response. When multiple independent pieces of information are requested and all commands are likely to succeed, run multiple tool calls in parallel for optimal performance. Run the following commands:
  - Add relevant untracked files to the staging area.
  - Run git status after the commit completes to verify success. Note: git status depends on the commit completing, so run it sequentially after the commit.
4. If the commit fails due to pre-commit hook, fix the issue and create a NEW commit (see amend rules above)

Important notes:
- NEVER run additional commands to read or explore code, besides git bash commands
- NEVER use the TodoWrite or Task tools
- DO NOT push to the remote repository unless the user explicitly asks you to do so
- IMPORTANT: Never use git commands with the -i flag (like git rebase -i or git add -i) since they require interactive input which is not supported.
- If there are no changes to commit (i.e., no untracked files and no modifications), do not create an empty commit
- In order to ensure good formatting, ALWAYS pass the commit message via a HEREDOC, a la this example:
<example>
git commit -m "$(cat <<\'EOF\'
   Commit message here.

   EOF
   )"
</example>

# Creating pull requests
Use the gh command via the Bash tool for ALL GitHub-related tasks including working with issues, pull requests, checks, and releases. If given a Github URL use the gh command to get the information needed.

IMPORTANT: When the user asks you to create a pull request, follow these steps carefully:

1. You can call multiple tools in a single response. When multiple independent pieces of information are requested and all commands are likely to succeed, run multiple tool calls in parallel for optimal performance. \
Run the following bash commands in parallel using the Bash tool, in order to understand the current state of the branch since it diverged from the main branch:
   - Run a git status command to see all untracked files
   - Run a git diff command to see both staged and unstaged changes that will be committed
   - Check if the current branch tracks a remote branch and is up to date with the remote, so you know if you need to push to the remote
   - Run a git log command and `git diff [base-branch]...HEAD` to understand the full commit history for the current branch (from the time it diverged from the base branch)
2. Analyze all changes that will be included in the pull request, making sure to look at all relevant commits (NOT just the latest commit, but ALL commits that will be included in the pull request!!!), and draft a pull request summary
3. You can call multiple tools in a single response. When multiple independent pieces of information are requested and all commands are likely to succeed, run multiple tool calls in parallel for optimal performance. run the following commands in parallel:
   - Create new branch if needed
   - Push to remote with -u flag if needed
   - Create PR using gh pr create with the format below. Use a HEREDOC to pass the body to ensure correct formatting.
<example>
gh pr create --title "the pr title" --body "$(cat <<\'EOF\'
## Summary
<1-3 bullet points>

## Test plan
[Bulleted markdown checklist of TODOs for testing the pull request...]

EOF
)"
</example>

Important:
- DO NOT use the TodoWrite or Task tools
- Return the PR URL when you\'re done, so the user can see it

# Other common operations
- View comments on a Github PR: gh api repos/foo/bar/pulls/123/comments"""

COMMAND_DESCRIPTION_DESCRIPTION = """Clear, concise description of what this command does in 5-10 words, in active voice. Examples:
Input: ls
Output: List files in current directory

Input: git status
Output: Show working tree status

Input: npm install
Output: Install package dependencies

Input: mkdir foo
Output: Create directory 'foo'"""

BASH_TOOL_DEFINITION: FunctionToolParam = {
    "type": "function",
    "name": BASH_TOOL_NAME,
    "description": TOOL_DESCRIPTION,
    "parameters": {
        "type": "object",
        "properties": {
            "command": {"type": "string", "description": "The command to execute"},
            "timeout": {"type": "number", "description": "Optional timeout in seconds"},
            "description": {
                "type": "string",
                "description": COMMAND_DESCRIPTION_DESCRIPTION,
            },
            "run_in_background": {
                "type": ["boolean", "null"],
                "description": "Set to true to run this command in the background. Use TaskOutput to read the output later.",
            },
        },
        "required": ["command", "timeout", "description", "run_in_background"],
        "additionalProperties": False,
    },
    "strict": True,
}

KILL_BASH_TOOL_NAME = "KillBash"

KILL_BASH_TOOL_DEFINITION: FunctionToolParam = {
    "type": "function",
    "name": KILL_BASH_TOOL_NAME,
    "description": """- Kills a running background bash shell by its ID
- Takes a shell_id parameter identifying the shell to kill
- Returns a success or failure status 
- Use this tool when you need to terminate a long-running shell""",
    "parameters": {
        "type": "object",
        "properties": {"shell_id": {"type": "string", "description": "The ID of the background shell to kill"}},
        "required": ["shell_id"],
        "additionalProperties": False,
    },
    "strict": True,
}


class BashConstraintRule(ConstraintRule):
    pass


class BashToolConfig(BaseModel):
    working_dir: Path
    shell_path: Path | None = None
    rules: list[BashConstraintRule] = []
    default_policy: ConstraintPolicy = ConstraintPolicy.ASK
    default_timeout_sec: float = 120
    maximum_timeout_sec: float = 1800
    tool_output_limit_chars: int = 40000
    persist_working_dir: bool = True
    background_output_dir: Path | None = None


_PWD_MARKER = "__AGENT_PWD__:"


@dataclass
class BackgroundTask:
    task_id: str
    process: asyncio.subprocess.Process
    command: str
    output_file: Path


class BashTool(FunctionTool):
    def __init__(self, config: BashToolConfig) -> None:
        self.config = config
        self.current_working_dir = config.working_dir
        self._background_tasks: dict[str, BackgroundTask] = {}

    def get_tool_defs(self) -> dict[str, FunctionToolParam]:
        return {
            BASH_TOOL_NAME: BASH_TOOL_DEFINITION,
            KILL_BASH_TOOL_NAME: KILL_BASH_TOOL_DEFINITION,
        }

    async def execute(self, **arguments: object) -> str:
        """Unified execute method for bash and kill operations.

        Dispatches to _execute_bash or _kill_bash based on provided arguments.

        Args:
            **arguments: Tool arguments containing either:
                - command: The bash command to execute
                - timeout: Optional timeout in seconds
                - description: Optional human-readable description
                - run_in_background: If True, run in background
                Or:
                - shell_id: The ID of a background shell to kill
        """
        command_arg = arguments.get("command")
        timeout_arg = arguments.get("timeout")
        timeout = float(str(timeout_arg)) if timeout_arg is not None else None
        description_arg = arguments.get("description")
        description = str(description_arg) if description_arg else ""
        run_in_background_arg = arguments.get("run_in_background")
        run_in_background = bool(run_in_background_arg) if run_in_background_arg is not None else None
        shell_id_arg = arguments.get("shell_id")

        if shell_id_arg is not None:
            return await self._kill_bash(str(shell_id_arg))
        elif command_arg is not None:
            return await self._execute_bash(str(command_arg), timeout, description, run_in_background)
        else:
            return json.dumps({"errors": "Required arguments missing."})

    async def _execute_bash(
        self,
        command: str,
        timeout: float | None,
        description: str,
        run_in_background: bool | None,
    ) -> str:
        """Execute a bash command and return output.

        If persist_working_dir is enabled in config, directory changes via cd are
        tracked and applied to subsequent commands via current_working_dir.

        If run_in_background is True, the command runs as a background process
        with output redirected to a temp file. Returns immediately with task ID.

        TODO:
        - Cross-platform support - printf/pwd won't work on Windows cmd.exe or PowerShell.
        - Get a friendly string about which background tasks are running for use in a hook-like construct.

        Args:
            command: The command to execute
            timeout: Timeout in seconds (uses default if None, ignored for background)
            description: Human-readable description of the command
            run_in_background: If True, run in background and return task ID

        Returns:
            JSON string with stdout, stderr, exit_code, and duration_sec (foreground)
            Plain text with task ID and output file path (background)
        """
        shell_executable = str(self.config.shell_path) if self.config.shell_path else None

        if run_in_background:
            task_id = secrets.token_hex(4)
            output_dir = self.config.background_output_dir or Path(tempfile.gettempdir())
            output_file = output_dir / f"{task_id}.output"

            # Redirect stdout and stderr to output file (no pwd marker for background)
            wrapped_command = f"{command} > {output_file} 2>&1"

            proc = await asyncio.create_subprocess_shell(
                wrapped_command,
                executable=shell_executable,
                cwd=str(self.current_working_dir),
            )

            self._background_tasks[task_id] = BackgroundTask(
                task_id=task_id,
                process=proc,
                command=command,
                output_file=output_file,
            )

            return f"Command running in background with ID: {task_id}. Output is being written to: {output_file}"

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

        # Wrap command to capture final working directory if persistence enabled
        actual_command = command
        if self.config.persist_working_dir:
            actual_command = f"{command}; printf '\\n{_PWD_MARKER}%s' \"$(pwd)\""

        start_time = time.perf_counter()
        proc = await asyncio.create_subprocess_shell(
            actual_command,
            executable=shell_executable,
            cwd=str(self.current_working_dir),
            stdout=asyncio.subprocess.PIPE,
            stderr=asyncio.subprocess.PIPE,
        )

        timed_out = False
        try:
            stdout_bytes, stderr_bytes = await asyncio.wait_for(proc.communicate(), timeout=effective_timeout)
        except TimeoutError:
            timed_out = True
            proc.kill()
            # Close transport to prevent "Event loop is closed" warning during GC
            transport = getattr(proc, "_transport", None)
            if transport is not None:
                transport.close()
            with contextlib.suppress(TimeoutError):
                await asyncio.wait_for(proc.wait(), timeout=0.5)
            stdout_bytes = b""
            stderr_bytes = b""

        duration_sec = time.perf_counter() - start_time

        stdout = stdout_bytes.decode(errors="replace")
        stderr = stderr_bytes.decode(errors="replace")

        # Extract and update working directory if persistence enabled.
        if self.config.persist_working_dir and _PWD_MARKER in stdout:
            idx = stdout.rfind(_PWD_MARKER)
            pwd_value = stdout[idx + len(_PWD_MARKER) :].strip()
            stdout = stdout[:idx].rstrip("\n")
            if pwd_value:
                self.current_working_dir = Path(pwd_value)

        limit = self.config.tool_output_limit_chars
        truncation_msg = "...\n[reached maximum bash command output characters]"

        if len(stdout) > limit:
            stdout = stdout[:limit] + truncation_msg
        if len(stderr) > limit:
            stderr = stderr[:limit] + truncation_msg

        result: dict[str, Any] = {
            "stdout": stdout,
            "stderr": stderr,
            "exit_code": proc.returncode,
            "duration_sec": round(duration_sec, 3),
        }

        if timed_out:
            result["errors"] = f"command terminated (exceeded {effective_timeout} sec timeout)"

        return json.dumps(result)

    async def _kill_bash(self, shell_id: str) -> str:
        """Kill a background shell by its ID.

        Args:
            shell_id: The ID of the background task to kill

        Returns:
            JSON string with message or error
        """
        task = self._background_tasks.get(shell_id)
        if task is None:
            return json.dumps({"errors": f"No background shell found with ID: {shell_id}"})

        command = task.command

        # Check if process already finished
        if task.process.returncode is not None:
            del self._background_tasks[shell_id]
            return json.dumps({"message": f"Shell {shell_id} had already completed ({command})"})

        # Kill the running process
        task.process.kill()
        transport = getattr(task.process, "_transport", None)
        if transport is not None:
            transport.close()
        with contextlib.suppress(TimeoutError):
            await asyncio.wait_for(task.process.wait(), timeout=0.5)

        del self._background_tasks[shell_id]
        return json.dumps({"message": f"Successfully killed shell: {shell_id} ({command})"})

    def check_constraint(self, **arguments: object) -> ConstraintPolicy:
        """Check bash command against constraint rules.

        Uses tree-sitter-bash to parse command structure and extract individual
        commands. Each command is checked against rules; most restrictive policy
        wins (DENY > ASK > ALLOW). Rules are evaluated in order; first match wins.

        Args:
            **arguments: Tool arguments containing:
                - command: The bash command string to check

        Pattern syntax (prefix matching, not regex or glob):
            - "npm run build" matches exactly "npm run build"
            - "npm run test:*" matches commands starting with "npm run test"
            - The :* wildcard only works at the end of a pattern

        Shell operator awareness:
            - Commands joined by &&, ||, ;, | are checked separately
            - "safe-cmd:*" won't allow "safe-cmd && dangerous-cmd"

        NOTE: Patterns can be circumvented by:
            - Options before args: "curl -X GET http://..." won't match "curl http://...:*"
            - Protocol differences: "curl https://..." won't match "curl http://...:*"
            - Variables: "curl $URL" won't match URL patterns
            - Extra whitespace: "echo  hello" won't match "echo hello"
        """
        command_str = str(arguments.get("command", ""))
        source = command_str.encode()
        tree = _parser.parse(source)
        commands = _extract_commands(tree.root_node, source)

        if not commands:
            return self.config.default_policy

        policies = [_check_single_command(cmd, self.config.rules, self.config.default_policy) for cmd in commands]

        if ConstraintPolicy.DENY in policies:
            return ConstraintPolicy.DENY
        if ConstraintPolicy.ASK in policies:
            return ConstraintPolicy.ASK
        return ConstraintPolicy.ALLOW


def _extract_commands(node: Node, source: bytes) -> list[str]:
    commands: list[str] = []
    if node.type == "command":
        commands.append(source[node.start_byte : node.end_byte].decode())
    else:
        for child in node.children:
            commands.extend(_extract_commands(child, source))
    return commands


def _check_single_command(
    command: str,
    rules: Sequence[BashConstraintRule],
    default_policy: ConstraintPolicy,
) -> ConstraintPolicy:
    for rule in rules:
        pattern = rule.pattern
        matches = command.startswith(pattern[:-2]) if pattern.endswith(":*") else command == pattern
        if matches:
            return rule.policy
    return default_policy
