"""Pre-configured tool sets for common use cases."""

from pathlib import Path
import sys

from agent_server.core.tools._protocol import Tool
from agent_server.core.tools._utils import ConstraintPolicy
from agent_server.core.tools.bash import BashConstraintRule, BashTool, BashToolConfig
from agent_server.core.tools.edit import EditConstraintRule, EditTool, EditToolConfig
from agent_server.core.tools.glob import GlobConstraintRule, GlobTool, GlobToolConfig
from agent_server.core.tools.grep import GrepConstraintRule, GrepTool, GrepToolConfig
from agent_server.core.tools.powershell import PowershellConstraintRule, PowershellTool, PowershellToolConfig
from agent_server.core.tools.read import ReadConstraintRule, ReadTool, ReadToolConfig
from agent_server.core.tools.todo import TodoTool
from agent_server.core.tools.write import WriteConstraintRule, WriteTool, WriteToolConfig

# File patterns that allow all files in working_dir
_FILE_ALLOW_RULES_READ = [
    ReadConstraintRule(pattern="*", policy=ConstraintPolicy.ALLOW),
]

_FILE_ALLOW_RULES_WRITE = [
    WriteConstraintRule(pattern="*", policy=ConstraintPolicy.ALLOW),
]

_FILE_ALLOW_RULES_EDIT = [
    EditConstraintRule(pattern="*", policy=ConstraintPolicy.ALLOW),
]

_FILE_ALLOW_RULES_GLOB = [
    GlobConstraintRule(pattern="*", policy=ConstraintPolicy.ALLOW),
]

_FILE_ALLOW_RULES_GREP = [
    GrepConstraintRule(pattern="*", policy=ConstraintPolicy.ALLOW),
]


# Bash rules for permissive mode: deny dangerous commands, allow everything else
_BASH_PERMISSIVE_RULES = [
    # Destructive operations
    BashConstraintRule(pattern="rm -rf:*", policy=ConstraintPolicy.DENY),
    BashConstraintRule(pattern="rm -r -f:*", policy=ConstraintPolicy.DENY),
    BashConstraintRule(pattern="rm -fr:*", policy=ConstraintPolicy.DENY),
    BashConstraintRule(pattern="rm --recursive --force:*", policy=ConstraintPolicy.DENY),
    BashConstraintRule(pattern="rm --force --recursive:*", policy=ConstraintPolicy.DENY),
    BashConstraintRule(pattern="mkfs:*", policy=ConstraintPolicy.DENY),
    BashConstraintRule(pattern="mkfs.*:*", policy=ConstraintPolicy.DENY),
    BashConstraintRule(pattern="dd:*", policy=ConstraintPolicy.DENY),
    BashConstraintRule(pattern="shred:*", policy=ConstraintPolicy.DENY),
    # System control
    BashConstraintRule(pattern="shutdown:*", policy=ConstraintPolicy.DENY),
    BashConstraintRule(pattern="reboot:*", policy=ConstraintPolicy.DENY),
    BashConstraintRule(pattern="halt:*", policy=ConstraintPolicy.DENY),
    BashConstraintRule(pattern="poweroff:*", policy=ConstraintPolicy.DENY),
    BashConstraintRule(pattern="init:*", policy=ConstraintPolicy.DENY),
    BashConstraintRule(pattern="systemctl stop:*", policy=ConstraintPolicy.DENY),
    BashConstraintRule(pattern="systemctl disable:*", policy=ConstraintPolicy.DENY),
    BashConstraintRule(pattern="kill -9 1", policy=ConstraintPolicy.DENY),
    # Allow everything else
    BashConstraintRule(pattern=":*", policy=ConstraintPolicy.ALLOW),
]

_POWERSHELL_PERMISSIVE_RULES = [
    # Recursive deletion
    PowershellConstraintRule(pattern="Remove-Item *-Recurse*", policy=ConstraintPolicy.DENY),
    PowershellConstraintRule(pattern="remove-item *-recurse*", policy=ConstraintPolicy.DENY),
    PowershellConstraintRule(pattern="rm *-r*", policy=ConstraintPolicy.DENY),
    PowershellConstraintRule(pattern="ri *-r*", policy=ConstraintPolicy.DENY),
    PowershellConstraintRule(pattern="del *-r*", policy=ConstraintPolicy.DENY),
    PowershellConstraintRule(pattern="erase *-r*", policy=ConstraintPolicy.DENY),
    PowershellConstraintRule(pattern="rd *-r*", policy=ConstraintPolicy.DENY),
    PowershellConstraintRule(pattern="rmdir *-r*", policy=ConstraintPolicy.DENY),
    # Disk operations
    PowershellConstraintRule(pattern="Clear-Disk*", policy=ConstraintPolicy.DENY),
    PowershellConstraintRule(pattern="clear-disk*", policy=ConstraintPolicy.DENY),
    PowershellConstraintRule(pattern="Format-Volume*", policy=ConstraintPolicy.DENY),
    PowershellConstraintRule(pattern="format-volume*", policy=ConstraintPolicy.DENY),
    PowershellConstraintRule(pattern="Initialize-Disk*", policy=ConstraintPolicy.DENY),
    PowershellConstraintRule(pattern="initialize-disk*", policy=ConstraintPolicy.DENY),
    PowershellConstraintRule(pattern="Remove-Partition*", policy=ConstraintPolicy.DENY),
    PowershellConstraintRule(pattern="remove-partition*", policy=ConstraintPolicy.DENY),
    PowershellConstraintRule(pattern="Remove-Volume*", policy=ConstraintPolicy.DENY),
    PowershellConstraintRule(pattern="remove-volume*", policy=ConstraintPolicy.DENY),
    PowershellConstraintRule(pattern="Set-Disk*", policy=ConstraintPolicy.DENY),
    PowershellConstraintRule(pattern="set-disk*", policy=ConstraintPolicy.DENY),
    PowershellConstraintRule(pattern="Set-Partition*", policy=ConstraintPolicy.DENY),
    PowershellConstraintRule(pattern="set-partition*", policy=ConstraintPolicy.DENY),
    PowershellConstraintRule(pattern="Resize-Partition*", policy=ConstraintPolicy.DENY),
    PowershellConstraintRule(pattern="resize-partition*", policy=ConstraintPolicy.DENY),
    PowershellConstraintRule(pattern="DiskPart*", policy=ConstraintPolicy.DENY),
    PowershellConstraintRule(pattern="diskpart*", policy=ConstraintPolicy.DENY),
    PowershellConstraintRule(pattern="Format.com*", policy=ConstraintPolicy.DENY),
    PowershellConstraintRule(pattern="format.com*", policy=ConstraintPolicy.DENY),
    PowershellConstraintRule(pattern="Disable-BitLocker*", policy=ConstraintPolicy.DENY),
    PowershellConstraintRule(pattern="disable-bitlocker*", policy=ConstraintPolicy.DENY),
    PowershellConstraintRule(pattern="Manage-Bde*", policy=ConstraintPolicy.DENY),
    PowershellConstraintRule(pattern="manage-bde*", policy=ConstraintPolicy.DENY),
    PowershellConstraintRule(pattern="Clear-Tpm*", policy=ConstraintPolicy.DENY),
    PowershellConstraintRule(pattern="clear-tpm*", policy=ConstraintPolicy.DENY),
    # System control
    PowershellConstraintRule(pattern="Stop-Computer*", policy=ConstraintPolicy.DENY),
    PowershellConstraintRule(pattern="stop-computer*", policy=ConstraintPolicy.DENY),
    PowershellConstraintRule(pattern="Restart-Computer*", policy=ConstraintPolicy.DENY),
    PowershellConstraintRule(pattern="restart-computer*", policy=ConstraintPolicy.DENY),
    PowershellConstraintRule(pattern="Stop-Service*", policy=ConstraintPolicy.DENY),
    PowershellConstraintRule(pattern="stop-service*", policy=ConstraintPolicy.DENY),
    PowershellConstraintRule(pattern="Set-Service *-StartupType*Disabled*", policy=ConstraintPolicy.DENY),
    PowershellConstraintRule(pattern="set-service *-startuptype*disabled*", policy=ConstraintPolicy.DENY),
    PowershellConstraintRule(pattern="Shutdown*", policy=ConstraintPolicy.DENY),
    PowershellConstraintRule(pattern="shutdown*", policy=ConstraintPolicy.DENY),
    PowershellConstraintRule(pattern="Remove-Service*", policy=ConstraintPolicy.DENY),
    PowershellConstraintRule(pattern="remove-service*", policy=ConstraintPolicy.DENY),
    PowershellConstraintRule(pattern="Sc.exe delete*", policy=ConstraintPolicy.DENY),
    PowershellConstraintRule(pattern="sc.exe delete*", policy=ConstraintPolicy.DENY),
    PowershellConstraintRule(pattern="Remove-LocalUser*", policy=ConstraintPolicy.DENY),
    PowershellConstraintRule(pattern="remove-localuser*", policy=ConstraintPolicy.DENY),
    PowershellConstraintRule(pattern="Unregister-ScheduledTask*", policy=ConstraintPolicy.DENY),
    PowershellConstraintRule(pattern="unregister-scheduledtask*", policy=ConstraintPolicy.DENY),
    # Dynamic and nested execution
    PowershellConstraintRule(pattern="Invoke-Expression*", policy=ConstraintPolicy.ASK),
    PowershellConstraintRule(pattern="invoke-expression*", policy=ConstraintPolicy.ASK),
    PowershellConstraintRule(pattern="iex*", policy=ConstraintPolicy.ASK),
    PowershellConstraintRule(pattern="Invoke-Command*", policy=ConstraintPolicy.ASK),
    PowershellConstraintRule(pattern="invoke-command*", policy=ConstraintPolicy.ASK),
    PowershellConstraintRule(pattern="icm*", policy=ConstraintPolicy.ASK),
    PowershellConstraintRule(pattern="Start-Process*", policy=ConstraintPolicy.ASK),
    PowershellConstraintRule(pattern="start-process*", policy=ConstraintPolicy.ASK),
    PowershellConstraintRule(pattern="saps*", policy=ConstraintPolicy.ASK),
    PowershellConstraintRule(pattern="PowerShell*", policy=ConstraintPolicy.ASK),
    PowershellConstraintRule(pattern="powershell*", policy=ConstraintPolicy.ASK),
    PowershellConstraintRule(pattern="Pwsh*", policy=ConstraintPolicy.ASK),
    PowershellConstraintRule(pattern="pwsh*", policy=ConstraintPolicy.ASK),
    PowershellConstraintRule(pattern="Cmd*", policy=ConstraintPolicy.ASK),
    PowershellConstraintRule(pattern="cmd*", policy=ConstraintPolicy.ASK),
    PowershellConstraintRule(pattern="Wsl*", policy=ConstraintPolicy.ASK),
    PowershellConstraintRule(pattern="wsl*", policy=ConstraintPolicy.ASK),
    PowershellConstraintRule(pattern="& *", policy=ConstraintPolicy.ASK),
    PowershellConstraintRule(pattern=". *", policy=ConstraintPolicy.ASK),
    PowershellConstraintRule(pattern="*.ps1", policy=ConstraintPolicy.ASK, match_command_name=True),
    PowershellConstraintRule(pattern="*.PS1", policy=ConstraintPolicy.ASK, match_command_name=True),
    PowershellConstraintRule(pattern="*.psm1", policy=ConstraintPolicy.ASK, match_command_name=True),
    PowershellConstraintRule(pattern="*.PSM1", policy=ConstraintPolicy.ASK, match_command_name=True),
    PowershellConstraintRule(pattern="Import-Module*", policy=ConstraintPolicy.ASK),
    PowershellConstraintRule(pattern="import-module*", policy=ConstraintPolicy.ASK),
    PowershellConstraintRule(pattern="ipmo*", policy=ConstraintPolicy.ASK),
    PowershellConstraintRule(pattern="Start-Job*", policy=ConstraintPolicy.ASK),
    PowershellConstraintRule(pattern="start-job*", policy=ConstraintPolicy.ASK),
    PowershellConstraintRule(pattern="sajb*", policy=ConstraintPolicy.ASK),
    PowershellConstraintRule(pattern="Start-ThreadJob*", policy=ConstraintPolicy.ASK),
    PowershellConstraintRule(pattern="start-threadjob*", policy=ConstraintPolicy.ASK),
    PowershellConstraintRule(pattern="Register-ScheduledTask*", policy=ConstraintPolicy.ASK),
    PowershellConstraintRule(pattern="register-scheduledtask*", policy=ConstraintPolicy.ASK),
    PowershellConstraintRule(pattern="Schtasks*", policy=ConstraintPolicy.ASK),
    PowershellConstraintRule(pattern="schtasks*", policy=ConstraintPolicy.ASK),
    PowershellConstraintRule(pattern="Add-Type*", policy=ConstraintPolicy.ASK),
    PowershellConstraintRule(pattern="add-type*", policy=ConstraintPolicy.ASK),
    PowershellConstraintRule(pattern="Cscript*", policy=ConstraintPolicy.ASK),
    PowershellConstraintRule(pattern="cscript*", policy=ConstraintPolicy.ASK),
    PowershellConstraintRule(pattern="Wscript*", policy=ConstraintPolicy.ASK),
    PowershellConstraintRule(pattern="wscript*", policy=ConstraintPolicy.ASK),
    PowershellConstraintRule(pattern="Mshta*", policy=ConstraintPolicy.ASK),
    PowershellConstraintRule(pattern="mshta*", policy=ConstraintPolicy.ASK),
    # Allow everything else
    PowershellConstraintRule(pattern="*", policy=ConstraintPolicy.ALLOW),
]


def _platform_shell_tool(
    working_dir: Path,
    bash_rules: list[BashConstraintRule],
    powershell_rules: list[PowershellConstraintRule],
) -> Tool:
    if sys.platform == "win32":
        return PowershellTool(config=PowershellToolConfig(working_dir=working_dir, rules=powershell_rules))
    return BashTool(config=BashToolConfig(working_dir=working_dir, rules=bash_rules))


def standard_tools(working_dir: Path) -> list[Tool]:
    """Standard tool set with the platform shell.

    - File tools (read, write, edit, glob, grep): ALLOW all within working_dir
    - Platform shell: ASK for all commands
    - Todo: ALLOW
    """
    return [
        ReadTool(config=ReadToolConfig(working_dir=working_dir, rules=list(_FILE_ALLOW_RULES_READ))),
        WriteTool(config=WriteToolConfig(working_dir=working_dir, rules=list(_FILE_ALLOW_RULES_WRITE))),
        EditTool(config=EditToolConfig(working_dir=working_dir, rules=list(_FILE_ALLOW_RULES_EDIT))),
        GlobTool(config=GlobToolConfig(working_dir=working_dir, rules=list(_FILE_ALLOW_RULES_GLOB))),
        GrepTool(config=GrepToolConfig(working_dir=working_dir, rules=list(_FILE_ALLOW_RULES_GREP))),
        _platform_shell_tool(
            working_dir,
            bash_rules=[BashConstraintRule(pattern=":*", policy=ConstraintPolicy.ASK)],
            powershell_rules=[PowershellConstraintRule(pattern="*", policy=ConstraintPolicy.ASK)],
        ),
        TodoTool(),
    ]


def permissive_tools(working_dir: Path) -> list[Tool]:
    """Permissive tool set that auto-approves most operations.

    - File tools (read, write, edit, glob, grep): ALLOW all within working_dir
    - Platform shell: denies dangerous commands and allows everything else
    - Todo: ALLOW

    Blocked commands include recursive deletion, destructive disk operations, system shutdown, and disabling services.
    Dynamic invocation and nested interpreters require approval.
    """
    return [
        ReadTool(config=ReadToolConfig(working_dir=working_dir, rules=list(_FILE_ALLOW_RULES_READ))),
        WriteTool(config=WriteToolConfig(working_dir=working_dir, rules=list(_FILE_ALLOW_RULES_WRITE))),
        EditTool(config=EditToolConfig(working_dir=working_dir, rules=list(_FILE_ALLOW_RULES_EDIT))),
        GlobTool(config=GlobToolConfig(working_dir=working_dir, rules=list(_FILE_ALLOW_RULES_GLOB))),
        GrepTool(config=GrepToolConfig(working_dir=working_dir, rules=list(_FILE_ALLOW_RULES_GREP))),
        _platform_shell_tool(
            working_dir,
            bash_rules=list(_BASH_PERMISSIVE_RULES),
            powershell_rules=list(_POWERSHELL_PERMISSIVE_RULES),
        ),
        TodoTool(),
    ]
