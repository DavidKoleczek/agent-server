"""Pre-configured tool sets for common use cases."""

from pathlib import Path

from agent_server.core.tools._protocol import Tool
from agent_server.core.tools._utils import ConstraintPolicy
from agent_server.core.tools.bash import BashConstraintRule, BashTool, BashToolConfig
from agent_server.core.tools.edit import EditConstraintRule, EditTool, EditToolConfig
from agent_server.core.tools.glob import GlobConstraintRule, GlobTool, GlobToolConfig
from agent_server.core.tools.grep import GrepConstraintRule, GrepTool, GrepToolConfig
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
    BashConstraintRule(pattern="rm:-rf:*", policy=ConstraintPolicy.DENY),
    BashConstraintRule(pattern="rm:-r:-f:*", policy=ConstraintPolicy.DENY),
    BashConstraintRule(pattern="rm:-fr:*", policy=ConstraintPolicy.DENY),
    BashConstraintRule(pattern="rm:--recursive:--force:*", policy=ConstraintPolicy.DENY),
    BashConstraintRule(pattern="rm:--force:--recursive:*", policy=ConstraintPolicy.DENY),
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
    BashConstraintRule(pattern="systemctl:stop:*", policy=ConstraintPolicy.DENY),
    BashConstraintRule(pattern="systemctl:disable:*", policy=ConstraintPolicy.DENY),
    BashConstraintRule(pattern="kill:-9:1", policy=ConstraintPolicy.DENY),
    # Allow everything else
    BashConstraintRule(pattern=":*", policy=ConstraintPolicy.ALLOW),
]


def standard_tools(working_dir: Path) -> list[Tool]:
    """Standard tool set with permission prompts for bash commands.

    - File tools (read, write, edit, glob, grep): ALLOW all within working_dir
    - Bash: ASK for all commands
    - Todo: ALLOW
    """
    return [
        ReadTool(config=ReadToolConfig(working_dir=working_dir, rules=list(_FILE_ALLOW_RULES_READ))),
        WriteTool(config=WriteToolConfig(working_dir=working_dir, rules=list(_FILE_ALLOW_RULES_WRITE))),
        EditTool(config=EditToolConfig(working_dir=working_dir, rules=list(_FILE_ALLOW_RULES_EDIT))),
        GlobTool(config=GlobToolConfig(working_dir=working_dir, rules=list(_FILE_ALLOW_RULES_GLOB))),
        GrepTool(config=GrepToolConfig(working_dir=working_dir, rules=list(_FILE_ALLOW_RULES_GREP))),
        BashTool(
            config=BashToolConfig(
                working_dir=working_dir,
                rules=[BashConstraintRule(pattern=":*", policy=ConstraintPolicy.ASK)],
            )
        ),
        TodoTool(),
    ]


def permissive_tools(working_dir: Path) -> list[Tool]:
    """Permissive tool set that auto-approves most operations.

    - File tools (read, write, edit, glob, grep): ALLOW all within working_dir
    - Bash: DENY dangerous commands (rm -rf, mkfs, dd, shutdown, etc.), ALLOW everything else
    - Todo: ALLOW

    Blocked bash commands:
        Destructive: rm -rf, mkfs, dd, shred
        System control: shutdown, reboot, halt, poweroff, init, systemctl stop/disable, kill -9 1
    """
    return [
        ReadTool(config=ReadToolConfig(working_dir=working_dir, rules=list(_FILE_ALLOW_RULES_READ))),
        WriteTool(config=WriteToolConfig(working_dir=working_dir, rules=list(_FILE_ALLOW_RULES_WRITE))),
        EditTool(config=EditToolConfig(working_dir=working_dir, rules=list(_FILE_ALLOW_RULES_EDIT))),
        GlobTool(config=GlobToolConfig(working_dir=working_dir, rules=list(_FILE_ALLOW_RULES_GLOB))),
        GrepTool(config=GrepToolConfig(working_dir=working_dir, rules=list(_FILE_ALLOW_RULES_GREP))),
        BashTool(config=BashToolConfig(working_dir=working_dir, rules=list(_BASH_PERMISSIVE_RULES))),
        TodoTool(),
    ]
