import asyncio
import json
from pathlib import Path
import shutil
import subprocess
import sys

import pytest

from agent_server.core.tools._utils import ConstraintPolicy
from agent_server.core.tools.powershell import PowershellConstraintRule, PowershellTool, PowershellToolConfig

pytestmark = pytest.mark.skipif(sys.platform != "win32", reason="PowerShell tests require Windows.")


@pytest.fixture(params=[None, "pwsh.exe", "powershell.exe"], ids=["default", "pwsh", "windows-powershell"])
def powershell_path(request: pytest.FixtureRequest) -> Path | None:
    executable_name: str | None = request.param
    if executable_name is None:
        return None

    executable_path = shutil.which(executable_name)
    if executable_path is None:
        pytest.skip(f"{executable_name} is not installed.")
    return Path(executable_path)


@pytest.fixture
def powershell_major_version(powershell_path: Path | None) -> int:
    executable_path = powershell_path
    if executable_path is None:
        discovered_path = shutil.which("pwsh.exe") or shutil.which("powershell.exe")
        assert discovered_path is not None
        executable_path = Path(discovered_path)

    result = subprocess.run(
        [
            executable_path,
            "-NoLogo",
            "-NoProfile",
            "-NonInteractive",
            "-Command",
            "[Console]::Out.Write($PSVersionTable.PSVersion.Major)",
        ],
        capture_output=True,
        text=True,
        check=True,
        timeout=5,
    )
    return int(result.stdout)


def _make_tool(
    rules: list[PowershellConstraintRule],
    powershell_path: Path | None,
    default_policy: ConstraintPolicy = ConstraintPolicy.ASK,
) -> PowershellTool:
    config = PowershellToolConfig(
        working_dir=Path.cwd(),
        powershell_path=powershell_path,
        rules=rules,
        default_policy=default_policy,
    )
    return PowershellTool(config)


# Basic Pattern Matching


def test_exact_match(powershell_path: Path | None) -> None:
    tool = _make_tool(
        [PowershellConstraintRule(pattern="Get-Process", policy=ConstraintPolicy.ALLOW)],
        powershell_path,
    )
    assert tool.check_constraint(command="Get-Process") == ConstraintPolicy.ALLOW
    assert tool.check_constraint(command="Get-Processs") == ConstraintPolicy.ASK
    assert tool.check_constraint(command="Get-Service") == ConstraintPolicy.ASK


def test_wildcards_match_at_any_position(powershell_path: Path | None) -> None:
    tool = _make_tool(
        [PowershellConstraintRule(pattern="*-Item *json", policy=ConstraintPolicy.ALLOW)],
        powershell_path,
    )
    assert tool.check_constraint(command="Get-Item config.json") == ConstraintPolicy.ALLOW
    assert tool.check_constraint(command="Set-Item output.json") == ConstraintPolicy.ALLOW
    assert tool.check_constraint(command="Get-Item config.toml") == ConstraintPolicy.ASK


@pytest.mark.parametrize("pattern", ["Get-ChildItem:*", "Get-ChildItem*"])
def test_colon_star_suffix_is_equivalent_to_trailing_wildcard(
    pattern: str,
    powershell_path: Path | None,
) -> None:
    tool = _make_tool(
        [PowershellConstraintRule(pattern=pattern, policy=ConstraintPolicy.ALLOW)],
        powershell_path,
    )
    assert tool.check_constraint(command="Get-ChildItem") == ConstraintPolicy.ALLOW
    assert tool.check_constraint(command="Get-ChildItem -Force") == ConstraintPolicy.ALLOW
    assert tool.check_constraint(command="Get-Item") == ConstraintPolicy.ASK


def test_single_wildcard_matches_every_command(powershell_path: Path | None) -> None:
    tool = _make_tool(
        [PowershellConstraintRule(pattern="*", policy=ConstraintPolicy.ALLOW)],
        powershell_path,
    )
    assert tool.check_constraint(command="Get-Process") == ConstraintPolicy.ALLOW
    assert tool.check_constraint(command="Write-Output hello") == ConstraintPolicy.ALLOW


# Shell Operator Handling


def test_and_operator_most_restrictive(
    powershell_path: Path | None,
    powershell_major_version: int,
) -> None:
    tool = _make_tool(
        [PowershellConstraintRule(pattern="Write-Output *", policy=ConstraintPolicy.ALLOW)],
        powershell_path,
    )
    expected_policy = ConstraintPolicy.ASK if powershell_major_version >= 7 else ConstraintPolicy.DENY
    assert tool.check_constraint(command="Write-Output first && Get-Process") == expected_policy


def test_and_operator_deny_wins(powershell_path: Path | None) -> None:
    tool = _make_tool(
        [
            PowershellConstraintRule(pattern="Write-Output *", policy=ConstraintPolicy.ALLOW),
            PowershellConstraintRule(pattern="Remove-Item *", policy=ConstraintPolicy.DENY),
        ],
        powershell_path,
    )
    assert tool.check_constraint(command="Write-Output safe && Remove-Item important.txt") == ConstraintPolicy.DENY


def test_semicolon_operator(powershell_path: Path | None) -> None:
    tool = _make_tool(
        [PowershellConstraintRule(pattern="Get-Process", policy=ConstraintPolicy.ALLOW)],
        powershell_path,
    )
    assert tool.check_constraint(command="Get-Process; Remove-Item important.txt") == ConstraintPolicy.ASK


def test_pipeline_operator(powershell_path: Path | None) -> None:
    tool = _make_tool(
        [
            PowershellConstraintRule(pattern="Get-ChildItem*", policy=ConstraintPolicy.ALLOW),
            PowershellConstraintRule(pattern="Select-Object *", policy=ConstraintPolicy.ALLOW),
        ],
        powershell_path,
    )
    assert tool.check_constraint(command="Get-ChildItem -Force | Select-Object Name") == ConstraintPolicy.ALLOW


def test_or_operator(
    powershell_path: Path | None,
    powershell_major_version: int,
) -> None:
    tool = _make_tool(
        [PowershellConstraintRule(pattern="Write-Output *", policy=ConstraintPolicy.ALLOW)],
        powershell_path,
    )
    expected_policy = ConstraintPolicy.ASK if powershell_major_version >= 7 else ConstraintPolicy.DENY
    assert tool.check_constraint(command="Write-Output primary || Write-Error fallback") == expected_policy


# Edge Cases


def test_empty_rules_returns_default(powershell_path: Path | None) -> None:
    tool_ask = _make_tool([], powershell_path, default_policy=ConstraintPolicy.ASK)
    tool_deny = _make_tool([], powershell_path, default_policy=ConstraintPolicy.DENY)
    assert tool_ask.check_constraint(command="Get-Process") == ConstraintPolicy.ASK
    assert tool_deny.check_constraint(command="Get-Process") == ConstraintPolicy.DENY


def test_first_rule_wins_for_each_command(powershell_path: Path | None) -> None:
    tool = _make_tool(
        [
            PowershellConstraintRule(pattern="Get-*", policy=ConstraintPolicy.ALLOW),
            PowershellConstraintRule(pattern="Get-Process*", policy=ConstraintPolicy.DENY),
        ],
        powershell_path,
    )
    assert tool.check_constraint(command="Get-Process -Id 1") == ConstraintPolicy.ALLOW


def test_variables_are_matched_literally(powershell_path: Path | None) -> None:
    tool = _make_tool(
        [PowershellConstraintRule(pattern="Write-Output $HOME", policy=ConstraintPolicy.ALLOW)],
        powershell_path,
    )
    assert tool.check_constraint(command="Write-Output $HOME") == ConstraintPolicy.ALLOW


def test_quoted_strings(powershell_path: Path | None) -> None:
    tool = _make_tool(
        [PowershellConstraintRule(pattern="Write-Output *", policy=ConstraintPolicy.ALLOW)],
        powershell_path,
    )
    assert tool.check_constraint(command='Write-Output "hello world"') == ConstraintPolicy.ALLOW


def test_command_name_rule_matches_direct_script_execution(powershell_path: Path | None) -> None:
    tool = _make_tool(
        [
            PowershellConstraintRule(
                pattern="*.ps1",
                policy=ConstraintPolicy.ASK,
                match_command_name=True,
            ),
            PowershellConstraintRule(pattern="*", policy=ConstraintPolicy.ALLOW),
        ],
        powershell_path,
    )
    assert tool.check_constraint(command=r".\script.ps1 -Argument value") == ConstraintPolicy.ASK
    assert tool.check_constraint(command=r"C:\tools\script.ps1") == ConstraintPolicy.ASK


def test_command_name_rule_does_not_match_script_arguments(powershell_path: Path | None) -> None:
    tool = _make_tool(
        [
            PowershellConstraintRule(
                pattern="*.ps1",
                policy=ConstraintPolicy.ASK,
                match_command_name=True,
            ),
            PowershellConstraintRule(pattern="*", policy=ConstraintPolicy.ALLOW),
        ],
        powershell_path,
    )
    assert tool.check_constraint(command=r"Get-Content .\script.ps1") == ConstraintPolicy.ALLOW


def test_nested_subexpression_commands_are_checked(powershell_path: Path | None) -> None:
    tool = _make_tool(
        [
            PowershellConstraintRule(pattern="Write-Output *", policy=ConstraintPolicy.ALLOW),
            PowershellConstraintRule(pattern="Remove-Item *", policy=ConstraintPolicy.DENY),
        ],
        powershell_path,
    )
    assert tool.check_constraint(command="Write-Output $(Remove-Item important.txt)") == ConstraintPolicy.DENY


def test_script_block_commands_are_checked(powershell_path: Path | None) -> None:
    tool = _make_tool(
        [
            PowershellConstraintRule(pattern="ForEach-Object *", policy=ConstraintPolicy.ALLOW),
            PowershellConstraintRule(pattern="Remove-Item *", policy=ConstraintPolicy.DENY),
        ],
        powershell_path,
    )
    assert tool.check_constraint(command="ForEach-Object { Remove-Item important.txt }") == ConstraintPolicy.DENY


def test_no_whitespace_normalization(powershell_path: Path | None) -> None:
    tool = _make_tool(
        [PowershellConstraintRule(pattern="Write-Output hello", policy=ConstraintPolicy.ALLOW)],
        powershell_path,
    )
    assert tool.check_constraint(command="Write-Output  hello") == ConstraintPolicy.ASK


def test_parse_errors_are_denied(powershell_path: Path | None) -> None:
    tool = _make_tool(
        [PowershellConstraintRule(pattern="*", policy=ConstraintPolicy.ALLOW)],
        powershell_path,
        default_policy=ConstraintPolicy.ALLOW,
    )
    assert tool.check_constraint(command='Write-Output "unterminated') == ConstraintPolicy.DENY


def test_allow_all_with_dangerous_command_denylist(powershell_path: Path | None) -> None:
    tool = _make_tool(
        [
            PowershellConstraintRule(pattern="Remove-Item *", policy=ConstraintPolicy.DENY),
            PowershellConstraintRule(pattern="Stop-Computer*", policy=ConstraintPolicy.DENY),
            PowershellConstraintRule(pattern="*", policy=ConstraintPolicy.ALLOW),
        ],
        powershell_path,
    )
    assert tool.check_constraint(command="Get-Process") == ConstraintPolicy.ALLOW
    assert tool.check_constraint(command="Write-Output hello") == ConstraintPolicy.ALLOW
    assert tool.check_constraint(command="Remove-Item important.txt") == ConstraintPolicy.DENY
    assert tool.check_constraint(command="Stop-Computer -Force") == ConstraintPolicy.DENY
    assert tool.check_constraint(command="Get-Process; Remove-Item important.txt") == ConstraintPolicy.DENY


# Execute Tests


async def test_execute_simple_command(powershell_path: Path | None) -> None:
    tool = PowershellTool(PowershellToolConfig(working_dir=Path.cwd(), powershell_path=powershell_path))
    result = await tool.execute(
        command="Write-Output hello",
        timeout=10,
        description="Echo hello",
        run_in_background=None,
    )
    data = json.loads(result)
    assert "hello" in data["stdout"]
    assert "errors" not in data
    assert "duration_sec" in data
    assert isinstance(data["duration_sec"], float)
    assert data["duration_sec"] >= 0


async def test_execute_timeout(powershell_path: Path | None) -> None:
    tool = PowershellTool(PowershellToolConfig(working_dir=Path.cwd(), powershell_path=powershell_path))
    result = await tool.execute(
        command="Start-Sleep -Seconds 5",
        timeout=0.1,
        description="Sleep",
        run_in_background=None,
    )
    data = json.loads(result)
    assert "errors" in data
    assert "timeout" in data["errors"].lower()


async def test_execute_output_truncation(tmp_path: Path, powershell_path: Path | None) -> None:
    config = PowershellToolConfig(
        working_dir=tmp_path,
        powershell_path=powershell_path,
        tool_output_limit_chars=50,
    )
    tool = PowershellTool(config)
    result = await tool.execute(
        command="Write-Output ('x' * 200); Write-Error ('y' * 200)",
        timeout=10,
        description="Long output",
        run_in_background=None,
    )
    data = json.loads(result)
    assert "..." in data["stdout"]
    assert "[reached maximum powershell command output characters]" in data["stdout"]
    assert "..." in data["stderr"]
    assert "[reached maximum powershell command output characters]" in data["stderr"]


async def test_execute_stderr_captured(powershell_path: Path | None) -> None:
    tool = PowershellTool(PowershellToolConfig(working_dir=Path.cwd(), powershell_path=powershell_path))
    result = await tool.execute(
        command="Write-Error error",
        timeout=10,
        description="Stderr test",
        run_in_background=None,
    )
    data = json.loads(result)
    assert not data["stdout"]
    assert "error" in data["stderr"]


async def test_execute_expected_cmdlet_failure(tmp_path: Path, powershell_path: Path | None) -> None:
    tool = PowershellTool(PowershellToolConfig(working_dir=tmp_path, powershell_path=powershell_path))

    silenced_result = await tool.execute(
        command="Get-Item -LiteralPath 'optional-config.json' -ErrorAction SilentlyContinue",
        timeout=10,
        description="Read optional configuration",
        run_in_background=None,
    )
    silenced_data = json.loads(silenced_result)
    assert silenced_data["exit_code"] == 1
    assert not silenced_data["stdout"]
    assert not silenced_data["stderr"]

    handled_result = await tool.execute(
        command=("try { Get-Item -LiteralPath 'optional-config.json' -ErrorAction Stop } catch {}; exit 0"),
        timeout=10,
        description="Handle missing optional configuration",
        run_in_background=None,
    )
    handled_data = json.loads(handled_result)
    assert handled_data["exit_code"] == 0
    assert not handled_data["stdout"]
    assert not handled_data["stderr"]


async def test_execute_both_stdout_and_stderr(powershell_path: Path | None) -> None:
    tool = PowershellTool(PowershellToolConfig(working_dir=Path.cwd(), powershell_path=powershell_path))
    result = await tool.execute(
        command="Write-Output out; Write-Error err",
        timeout=10,
        description="Both streams",
        run_in_background=None,
    )
    data = json.loads(result)
    assert "out" in data["stdout"]
    assert "err" in data["stderr"]


async def test_execute_exit_code_nonzero(powershell_path: Path | None) -> None:
    tool = PowershellTool(PowershellToolConfig(working_dir=Path.cwd(), powershell_path=powershell_path))
    result = await tool.execute(command="exit 1", timeout=10, description="Exit 1", run_in_background=None)
    data = json.loads(result)
    assert data["exit_code"] == 1


async def test_execute_exit_code_zero(powershell_path: Path | None) -> None:
    tool = PowershellTool(PowershellToolConfig(working_dir=Path.cwd(), powershell_path=powershell_path))
    result = await tool.execute(
        command="Write-Output hello",
        timeout=10,
        description="Echo",
        run_in_background=None,
    )
    data = json.loads(result)
    assert data["exit_code"] == 0


async def test_execute_invalid_timeout(powershell_path: Path | None) -> None:
    tool = PowershellTool(PowershellToolConfig(working_dir=Path.cwd(), powershell_path=powershell_path))
    result = await tool.execute(
        command="Write-Output hello",
        timeout=-1,
        description="Invalid timeout",
        run_in_background=None,
    )
    data = json.loads(result)
    assert "errors" in data
    assert "invalid timeout" in data["errors"].lower()
    assert data["stdout"] == ""
    assert data["stderr"] == ""


async def test_execute_reports_path_and_version(powershell_path: Path | None) -> None:
    tool = PowershellTool(PowershellToolConfig(working_dir=Path.cwd(), powershell_path=powershell_path))
    result = await tool.execute(
        command="(Get-Process -Id $PID).Path; $PSVersionTable.PSVersion.ToString()",
        timeout=10,
        description="Show PowerShell path and version",
        run_in_background=None,
    )
    data = json.loads(result)
    assert data["exit_code"] == 0
    executable_path, version = data["stdout"].splitlines()
    expected_names = {"pwsh.exe", "powershell.exe"} if powershell_path is None else {powershell_path.name.lower()}
    assert Path(executable_path).name.lower() in expected_names
    assert version


async def test_execute_reads_windows_product_name_from_registry(powershell_path: Path | None) -> None:
    tool = PowershellTool(PowershellToolConfig(working_dir=Path.cwd(), powershell_path=powershell_path))
    result = await tool.execute(
        command=r"(Get-ItemProperty -Path 'HKLM:\SOFTWARE\Microsoft\Windows NT\CurrentVersion').ProductName",
        timeout=10,
        description="Read installed Windows product name",
        run_in_background=None,
    )
    data = json.loads(result)
    assert data["exit_code"] == 0
    assert not data["stderr"]
    assert "Windows" in data["stdout"]


async def test_execute_native_executable_with_spaces(tmp_path: Path, powershell_path: Path | None) -> None:
    source_path = shutil.which("where.exe")
    assert source_path is not None

    executable_dir = tmp_path / "Native Tools"
    executable_dir.mkdir()
    executable_path = executable_dir / "where.exe"
    shutil.copy2(source_path, executable_path)

    tool = PowershellTool(PowershellToolConfig(working_dir=tmp_path, powershell_path=powershell_path))
    result = await tool.execute(
        command=f'& "{executable_path}" powershell.exe',
        timeout=10,
        description="Locate Windows PowerShell executable",
        run_in_background=None,
    )
    data = json.loads(result)
    assert data["exit_code"] == 0
    assert "powershell.exe" in data["stdout"].lower()


# Background Execution Tests


async def _wait_for_file_content(file_path: Path, expected: str) -> str:
    loop = asyncio.get_running_loop()
    deadline = loop.time() + 5
    while loop.time() < deadline:
        if file_path.exists():
            content = file_path.read_text()
            if expected in content:
                return content
        await asyncio.sleep(0.05)
    pytest.fail(f"Timed out waiting for {expected!r} in {file_path}")


async def test_background_execution_returns_task_id(tmp_path: Path, powershell_path: Path | None) -> None:
    config = PowershellToolConfig(
        working_dir=tmp_path,
        powershell_path=powershell_path,
        background_output_dir=tmp_path,
    )
    tool = PowershellTool(config)

    result = await tool.execute(
        command="Write-Output hello",
        timeout=10,
        description="Echo",
        run_in_background=True,
    )

    assert "Command running in background with ID:" in result
    assert "Output is being written to:" in result
    assert "Errors are being written to:" in result
    assert str(tmp_path) in result

    task_id = result.split("ID: ")[1].split(".")[0]
    await _wait_for_file_content(tmp_path / f"{task_id}.output", "hello")


async def test_background_execution_writes_output(tmp_path: Path, powershell_path: Path | None) -> None:
    config = PowershellToolConfig(
        working_dir=tmp_path,
        powershell_path=powershell_path,
        background_output_dir=tmp_path,
    )
    tool = PowershellTool(config)

    result = await tool.execute(
        command="Write-Output hello_background; Write-Error error_background",
        timeout=10,
        description="Echo",
        run_in_background=True,
    )
    task_id = result.split("ID: ")[1].split(".")[0]
    output_file = tmp_path / f"{task_id}.output"
    error_file = tmp_path / f"{task_id}.error"

    assert output_file.exists()
    assert error_file.exists()
    await _wait_for_file_content(output_file, "hello_background")
    await _wait_for_file_content(error_file, "error_background")


async def test_kill_running_process(tmp_path: Path, powershell_path: Path | None) -> None:
    config = PowershellToolConfig(
        working_dir=tmp_path,
        powershell_path=powershell_path,
        background_output_dir=tmp_path,
    )
    tool = PowershellTool(config)

    result = await tool.execute(
        command="Start-Sleep -Seconds 60",
        timeout=10,
        description="Sleep",
        run_in_background=True,
    )
    task_id = result.split("ID: ")[1].split(".")[0]

    kill_result = await tool.execute(shell_id=task_id)
    data = json.loads(kill_result)

    assert "message" in data
    assert f"Successfully killed shell: {task_id}" in data["message"]
    assert "Start-Sleep -Seconds 60" in data["message"]


async def test_kill_already_finished_process(tmp_path: Path, powershell_path: Path | None) -> None:
    config = PowershellToolConfig(
        working_dir=tmp_path,
        powershell_path=powershell_path,
        background_output_dir=tmp_path,
    )
    tool = PowershellTool(config)

    result = await tool.execute(
        command="Write-Output done",
        timeout=10,
        description="Echo",
        run_in_background=True,
    )
    task_id = result.split("ID: ")[1].split(".")[0]

    await _wait_for_file_content(tmp_path / f"{task_id}.output", "done")
    await asyncio.sleep(0.1)

    kill_result = await tool.execute(shell_id=task_id)
    data = json.loads(kill_result)

    assert "message" in data
    assert f"Shell {task_id} had already completed" in data["message"]


async def test_kill_nonexistent_task(powershell_path: Path | None) -> None:
    tool = PowershellTool(PowershellToolConfig(working_dir=Path.cwd(), powershell_path=powershell_path))

    kill_result = await tool.execute(shell_id="nonexistent")
    data = json.loads(kill_result)

    assert "errors" in data
    assert "No background shell found with ID: nonexistent" in data["errors"]
