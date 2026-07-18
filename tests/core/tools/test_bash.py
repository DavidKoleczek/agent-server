import asyncio
import json
from pathlib import Path
import sys

import pytest

from agent_server.core.tools._utils import ConstraintPolicy
from agent_server.core.tools.bash import BashConstraintRule, BashTool, BashToolConfig
from agent_server.core.tools.presets import _BASH_PERMISSIVE_RULES

pytestmark = pytest.mark.skipif(sys.platform == "win32", reason="Bash tests require a POSIX environment.")


def _make_tool(rules: list[BashConstraintRule], default_policy: ConstraintPolicy = ConstraintPolicy.ASK) -> BashTool:
    config = BashToolConfig(working_dir=Path("/tmp"), rules=rules, default_policy=default_policy)
    return BashTool(config)


# Basic Pattern Matching


def test_exact_match() -> None:
    tool = _make_tool([BashConstraintRule(pattern="npm run build", policy=ConstraintPolicy.ALLOW)])
    assert tool.check_constraint(command="npm run build") == ConstraintPolicy.ALLOW
    assert tool.check_constraint(command="npm run test") == ConstraintPolicy.ASK


def test_prefix_match() -> None:
    tool = _make_tool([BashConstraintRule(pattern="npm run test:*", policy=ConstraintPolicy.ALLOW)])
    assert tool.check_constraint(command="npm run test") == ConstraintPolicy.ALLOW
    assert tool.check_constraint(command="npm run test:unit") == ConstraintPolicy.ALLOW
    assert tool.check_constraint(command="npm run build") == ConstraintPolicy.ASK


# Shell Operator Handling


def test_and_operator_most_restrictive() -> None:
    tool = _make_tool([BashConstraintRule(pattern="safe-cmd", policy=ConstraintPolicy.ALLOW)])
    # safe-cmd allowed, but other-cmd not in rules -> ASK
    assert tool.check_constraint(command="safe-cmd && other-cmd") == ConstraintPolicy.ASK


def test_and_operator_deny_wins() -> None:
    tool = _make_tool(
        [
            BashConstraintRule(pattern="safe-cmd", policy=ConstraintPolicy.ALLOW),
            BashConstraintRule(pattern="dangerous-cmd", policy=ConstraintPolicy.DENY),
        ]
    )
    assert tool.check_constraint(command="safe-cmd && dangerous-cmd") == ConstraintPolicy.DENY


def test_semicolon_operator() -> None:
    tool = _make_tool([BashConstraintRule(pattern="git status", policy=ConstraintPolicy.ALLOW)])
    assert tool.check_constraint(command="git status; rm -rf /") == ConstraintPolicy.ASK


def test_pipe_operator() -> None:
    tool = _make_tool(
        [
            BashConstraintRule(pattern="ls:*", policy=ConstraintPolicy.ALLOW),
            BashConstraintRule(pattern="grep:*", policy=ConstraintPolicy.ALLOW),
        ]
    )
    assert tool.check_constraint(command="ls -la | grep foo") == ConstraintPolicy.ALLOW


def test_or_operator() -> None:
    tool = _make_tool([BashConstraintRule(pattern="cmd1", policy=ConstraintPolicy.ALLOW)])
    assert tool.check_constraint(command="cmd1 || cmd2") == ConstraintPolicy.ASK


# Edge Cases


def test_empty_rules_returns_default() -> None:
    tool_ask = _make_tool([], default_policy=ConstraintPolicy.ASK)
    tool_deny = _make_tool([], default_policy=ConstraintPolicy.DENY)
    assert tool_ask.check_constraint(command="any command") == ConstraintPolicy.ASK
    assert tool_deny.check_constraint(command="any command") == ConstraintPolicy.DENY


def test_first_rule_wins() -> None:
    tool = _make_tool(
        [
            BashConstraintRule(pattern="git:*", policy=ConstraintPolicy.ALLOW),
            BashConstraintRule(pattern="git push:*", policy=ConstraintPolicy.DENY),
        ]
    )
    # First rule matches, so ALLOW
    assert tool.check_constraint(command="git push origin main") == ConstraintPolicy.ALLOW


def test_variables_matched_literally() -> None:
    tool = _make_tool([BashConstraintRule(pattern="echo $HOME", policy=ConstraintPolicy.ALLOW)])
    assert tool.check_constraint(command="echo $HOME") == ConstraintPolicy.ALLOW


def test_quoted_strings() -> None:
    tool = _make_tool([BashConstraintRule(pattern="echo:*", policy=ConstraintPolicy.ALLOW)])
    assert tool.check_constraint(command='echo "hello world"') == ConstraintPolicy.ALLOW


def test_subshell_commands() -> None:
    tool = _make_tool([BashConstraintRule(pattern="echo:*", policy=ConstraintPolicy.ALLOW)])
    # Command substitution - the outer echo is allowed
    assert tool.check_constraint(command="echo $(whoami)") == ConstraintPolicy.ALLOW


# Security Edge Cases


def test_prefix_matches_any_continuation() -> None:
    tool = _make_tool([BashConstraintRule(pattern="safe:*", policy=ConstraintPolicy.ALLOW)])
    # Prefix matches any continuation including -cmd
    assert tool.check_constraint(command="safe-cmd") == ConstraintPolicy.ALLOW
    assert tool.check_constraint(command="safexyz") == ConstraintPolicy.ALLOW


def test_no_whitespace_normalization() -> None:
    tool = _make_tool([BashConstraintRule(pattern="echo hello", policy=ConstraintPolicy.ALLOW)])
    # Extra whitespace is NOT normalized - literal matching
    assert tool.check_constraint(command="echo  hello") == ConstraintPolicy.ASK


def test_allow_all_with_dangerous_command_denylist() -> None:
    tool = _make_tool(
        [
            # Deny dangerous commands first (first match wins)
            BashConstraintRule(pattern="rm -rf:*", policy=ConstraintPolicy.DENY),
            BashConstraintRule(pattern="rm -r:*", policy=ConstraintPolicy.DENY),
            BashConstraintRule(pattern="sudo:*", policy=ConstraintPolicy.DENY),
            BashConstraintRule(pattern="chmod 777:*", policy=ConstraintPolicy.DENY),
            # Allow everything else
            BashConstraintRule(pattern=":*", policy=ConstraintPolicy.ALLOW),
        ]
    )
    # Safe commands are allowed
    assert tool.check_constraint(command="ls -la") == ConstraintPolicy.ALLOW
    assert tool.check_constraint(command="git status") == ConstraintPolicy.ALLOW
    assert tool.check_constraint(command="npm run build") == ConstraintPolicy.ALLOW

    # Dangerous commands are denied
    assert tool.check_constraint(command="rm -rf /") == ConstraintPolicy.DENY
    assert tool.check_constraint(command="rm -r important_dir") == ConstraintPolicy.DENY
    assert tool.check_constraint(command="sudo rm file") == ConstraintPolicy.DENY
    assert tool.check_constraint(command="chmod 777 script.sh") == ConstraintPolicy.DENY

    # Chained commands: most restrictive wins
    assert tool.check_constraint(command="ls && rm -rf /") == ConstraintPolicy.DENY
    assert tool.check_constraint(command="echo hello; sudo reboot") == ConstraintPolicy.DENY


def test_permissive_preset_denies_recursive_force_removal() -> None:
    shell_tool = _make_tool(list(_BASH_PERMISSIVE_RULES))
    assert shell_tool.check_constraint(command="rm -rf /") == ConstraintPolicy.DENY
    assert shell_tool.check_constraint(command="git status") == ConstraintPolicy.ALLOW


# Execute Tests


async def test_execute_simple_command() -> None:
    tool = _make_tool([])
    result = await tool.execute(command="echo hello", timeout=10, description="Echo hello", run_in_background=None)
    data = json.loads(result)
    assert "hello" in data["stdout"]
    assert "errors" not in data
    assert "duration_sec" in data
    assert isinstance(data["duration_sec"], float)
    assert data["duration_sec"] >= 0


async def test_execute_timeout() -> None:
    tool = _make_tool([])
    result = await tool.execute(command="sleep 10", timeout=0.1, description="Sleep", run_in_background=None)
    data = json.loads(result)
    assert "errors" in data
    assert "timeout" in data["errors"].lower()


async def test_execute_output_truncation(tmp_path: Path) -> None:
    config = BashToolConfig(working_dir=tmp_path, tool_output_limit_chars=50)
    tool = BashTool(config)
    result = await tool.execute(
        command="python -c \"import sys; print('x' * 200); sys.stderr.write('y' * 200)\"",
        timeout=10,
        description="Long output",
        run_in_background=None,
    )
    data = json.loads(result)
    assert "..." in data["stdout"]
    assert "[reached maximum bash command output characters]" in data["stdout"]
    assert "..." in data["stderr"]
    assert "[reached maximum bash command output characters]" in data["stderr"]


async def test_execute_with_custom_shell(tmp_path: Path) -> None:
    config = BashToolConfig(working_dir=tmp_path, shell_path=Path("/bin/bash"))
    tool = BashTool(config)
    result = await tool.execute(command="echo $BASH", timeout=10, description="Check shell", run_in_background=None)
    data = json.loads(result)
    assert "/bash" in data["stdout"]


async def test_execute_stderr_captured() -> None:
    tool = _make_tool([])
    result = await tool.execute(command="echo error >&2", timeout=10, description="Stderr test", run_in_background=None)
    data = json.loads(result)
    assert "error" in data["stderr"]


async def test_execute_both_stdout_and_stderr() -> None:
    tool = _make_tool([])
    result = await tool.execute(
        command="echo out && echo err >&2", timeout=10, description="Both streams", run_in_background=None
    )
    data = json.loads(result)
    assert "out" in data["stdout"]
    assert "err" in data["stderr"]


async def test_execute_exit_code_nonzero() -> None:
    tool = _make_tool([])
    result = await tool.execute(command="exit 1", timeout=10, description="Exit 1", run_in_background=None)
    data = json.loads(result)
    assert data["exit_code"] == 1


async def test_execute_exit_code_zero() -> None:
    tool = _make_tool([])
    result = await tool.execute(command="echo hello", timeout=10, description="Echo", run_in_background=None)
    data = json.loads(result)
    assert data["exit_code"] == 0


async def test_execute_invalid_timeout() -> None:
    tool = _make_tool([])
    result = await tool.execute(command="echo hello", timeout=-1, description="Invalid timeout", run_in_background=None)
    data = json.loads(result)
    assert "errors" in data
    assert "invalid timeout" in data["errors"].lower()
    assert data["stdout"] == ""
    assert data["stderr"] == ""


# Persistent Working Directory Tests


async def test_persist_working_dir_enabled(tmp_path: Path) -> None:
    subdir = tmp_path / "subdir"
    subdir.mkdir()

    config = BashToolConfig(working_dir=tmp_path, persist_working_dir=True)
    tool = BashTool(config)

    assert tool.current_working_dir == tmp_path

    # Change directory
    result = await tool.execute(command="cd subdir", timeout=10, description="cd", run_in_background=None)
    data = json.loads(result)
    assert data["exit_code"] == 0

    # Verify working directory was updated
    assert tool.current_working_dir == subdir

    # Verify next command runs in new directory
    result = await tool.execute(command="pwd", timeout=10, description="pwd", run_in_background=None)
    data = json.loads(result)
    assert str(subdir) in data["stdout"]


async def test_persist_working_dir_disabled(tmp_path: Path) -> None:
    subdir = tmp_path / "subdir"
    subdir.mkdir()

    config = BashToolConfig(working_dir=tmp_path, persist_working_dir=False)
    tool = BashTool(config)

    # Change directory
    await tool.execute(command="cd subdir", timeout=10, description="cd", run_in_background=None)

    # Verify working directory was NOT updated
    assert tool.current_working_dir == tmp_path

    # Verify next command still runs in original directory
    result = await tool.execute(command="pwd", timeout=10, description="pwd", run_in_background=None)
    data = json.loads(result)
    assert str(tmp_path) in data["stdout"]
    assert str(subdir) not in data["stdout"]


async def test_persist_working_dir_marker_stripped(tmp_path: Path) -> None:
    config = BashToolConfig(working_dir=tmp_path, persist_working_dir=True)
    tool = BashTool(config)

    result = await tool.execute(command="echo hello", timeout=10, description="echo", run_in_background=None)
    data = json.loads(result)

    # Marker should be stripped from output
    assert "__AGENT_PWD__" not in data["stdout"]
    assert "hello" in data["stdout"]


# Background Execution Tests


async def test_background_execution_returns_task_id(tmp_path: Path) -> None:
    config = BashToolConfig(working_dir=tmp_path, background_output_dir=tmp_path)
    tool = BashTool(config)

    result = await tool.execute(command="echo hello", timeout=10, description="Echo", run_in_background=True)

    assert "Command running in background with ID:" in result
    assert "Output is being written to:" in result
    assert tmp_path.as_posix() in result


async def test_background_execution_writes_output(tmp_path: Path) -> None:
    config = BashToolConfig(working_dir=tmp_path, background_output_dir=tmp_path)
    tool = BashTool(config)

    result = await tool.execute(command="echo hello_background", timeout=10, description="Echo", run_in_background=True)

    # Extract task_id from result
    task_id = result.split("ID: ")[1].split(".")[0]
    output_file = tmp_path / f"{task_id}.output"

    # Wait for command to complete and file to be written
    await asyncio.sleep(0.2)

    assert output_file.exists()
    content = output_file.read_text()
    assert "hello_background" in content


async def test_kill_running_process(tmp_path: Path) -> None:
    config = BashToolConfig(working_dir=tmp_path, background_output_dir=tmp_path)
    tool = BashTool(config)

    result = await tool.execute(command="sleep 60", timeout=10, description="Sleep", run_in_background=True)
    task_id = result.split("ID: ")[1].split(".")[0]

    # Kill the process
    kill_result = await tool.execute(shell_id=task_id)
    data = json.loads(kill_result)

    assert "message" in data
    assert f"Successfully killed shell: {task_id}" in data["message"]
    assert "sleep 60" in data["message"]


async def test_kill_already_finished_process(tmp_path: Path) -> None:
    config = BashToolConfig(working_dir=tmp_path, background_output_dir=tmp_path)
    tool = BashTool(config)

    result = await tool.execute(command="echo done", timeout=10, description="Echo", run_in_background=True)
    task_id = result.split("ID: ")[1].split(".")[0]

    # Wait for command to finish
    await asyncio.sleep(0.2)

    kill_result = await tool.execute(shell_id=task_id)
    data = json.loads(kill_result)

    assert "message" in data
    assert f"Shell {task_id} had already completed" in data["message"]


async def test_kill_nonexistent_task() -> None:
    config = BashToolConfig(working_dir=Path("/tmp"))
    tool = BashTool(config)

    kill_result = await tool.execute(shell_id="nonexistent")
    data = json.loads(kill_result)

    assert "errors" in data
    assert "No background shell found with ID: nonexistent" in data["errors"]
