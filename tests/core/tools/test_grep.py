from pathlib import Path
from unittest.mock import MagicMock, patch

from agent_server.core.tools._ripgrep import RipgrepOutputDecodeError
from agent_server.core.tools._utils import ConstraintPolicy
from agent_server.core.tools.grep import GrepConstraintRule, GrepTool, GrepToolConfig


def test_check_constraint_allow(tmp_path: Path) -> None:
    config = GrepToolConfig(
        working_dir=tmp_path,
        rules=[GrepConstraintRule(pattern="**", policy=ConstraintPolicy.ALLOW)],
    )
    tool = GrepTool(config)
    assert tool.check_constraint(path=str(tmp_path / "test.py")) == ConstraintPolicy.ALLOW


def test_check_constraint_deny(tmp_path: Path) -> None:
    config = GrepToolConfig(
        working_dir=tmp_path,
        rules=[GrepConstraintRule(pattern="*.secret", policy=ConstraintPolicy.DENY)],
    )
    tool = GrepTool(config)
    assert tool.check_constraint(path=str(tmp_path / "data.secret")) == ConstraintPolicy.DENY


def test_check_constraint_default_ask(tmp_path: Path) -> None:
    config = GrepToolConfig(working_dir=tmp_path, rules=[])
    tool = GrepTool(config)
    assert tool.check_constraint(path=str(tmp_path / "any.txt")) == ConstraintPolicy.ASK


def test_execute_files_with_matches(tmp_path: Path) -> None:
    config = GrepToolConfig(
        working_dir=tmp_path,
        rules=[GrepConstraintRule(pattern="**", policy=ConstraintPolicy.ALLOW)],
    )
    tool = GrepTool(config)

    (tmp_path / "file1.py").write_text("def hello():\n    pass\n")
    (tmp_path / "file2.py").write_text("def world():\n    pass\n")
    (tmp_path / "file3.txt").write_text("no match here\n")

    result = tool.execute(pattern="def", output_mode="files_with_matches")

    assert "file1.py" in result
    assert "file2.py" in result
    assert "file3.txt" not in result


def test_execute_count(tmp_path: Path) -> None:
    config = GrepToolConfig(
        working_dir=tmp_path,
        rules=[GrepConstraintRule(pattern="**", policy=ConstraintPolicy.ALLOW)],
    )
    tool = GrepTool(config)

    (tmp_path / "test.py").write_text("def a():\ndef b():\ndef c():\n")

    result = tool.execute(pattern="def", output_mode="count")

    assert "test.py:3" in result


def test_execute_content_with_line_numbers(tmp_path: Path) -> None:
    config = GrepToolConfig(
        working_dir=tmp_path,
        rules=[GrepConstraintRule(pattern="**", policy=ConstraintPolicy.ALLOW)],
    )
    tool = GrepTool(config)

    (tmp_path / "test.py").write_text("line1\nmatch_here\nline3\n")

    result = tool.execute(pattern="match_here", output_mode="content")

    assert "2:" in result
    assert "match_here" in result


def test_execute_unicode_content(tmp_path: Path) -> None:
    config = GrepToolConfig(
        working_dir=tmp_path,
        rules=[GrepConstraintRule(pattern="**", policy=ConstraintPolicy.ALLOW)],
    )
    tool = GrepTool(config)

    (tmp_path / "test.py").write_text("match \u23ed\ufe0f\n", encoding="utf-8")

    result = tool.execute(pattern="match", output_mode="content", head_limit=1)

    assert "\u23ed\ufe0f" in result


@patch("agent_server.core.tools.grep.run_ripgrep")
def test_execute_invalid_utf8_output(mock_run: MagicMock, tmp_path: Path) -> None:
    config = GrepToolConfig(
        working_dir=tmp_path,
        rules=[GrepConstraintRule(pattern="**", policy=ConstraintPolicy.ALLOW)],
    )
    tool = GrepTool(config)
    decode_error = UnicodeDecodeError("utf-8", b"\xff", 0, 1, "invalid start byte")
    mock_run.side_effect = RipgrepOutputDecodeError("stdout", decode_error)

    result = tool.execute(pattern="match")

    assert result == (
        "Error: ripgrep stdout is not valid UTF-8: "
        "'utf-8' codec can't decode byte 0xff in position 0: invalid start byte"
    )


def test_execute_head_limit(tmp_path: Path) -> None:
    config = GrepToolConfig(
        working_dir=tmp_path,
        rules=[GrepConstraintRule(pattern="**", policy=ConstraintPolicy.ALLOW)],
    )
    tool = GrepTool(config)

    for i in range(10):
        (tmp_path / f"file{i}.py").write_text("pattern\n")

    result = tool.execute(pattern="pattern", output_mode="files_with_matches", head_limit=3)

    lines = [line for line in result.strip().split("\n") if line]
    assert len(lines) == 3


def test_execute_offset(tmp_path: Path) -> None:
    config = GrepToolConfig(
        working_dir=tmp_path,
        rules=[GrepConstraintRule(pattern="**", policy=ConstraintPolicy.ALLOW)],
    )
    tool = GrepTool(config)

    for i in range(10):
        (tmp_path / f"file{i:02d}.py").write_text("pattern\n")

    result = tool.execute(pattern="pattern", output_mode="files_with_matches", offset=5, head_limit=3)

    lines = [line for line in result.strip().split("\n") if line]
    assert len(lines) == 3


def test_execute_no_matches(tmp_path: Path) -> None:
    config = GrepToolConfig(
        working_dir=tmp_path,
        rules=[GrepConstraintRule(pattern="**", policy=ConstraintPolicy.ALLOW)],
    )
    tool = GrepTool(config)

    (tmp_path / "test.py").write_text("hello world\n")

    result = tool.execute(pattern="nonexistent")

    assert result == "No matches found"


def test_execute_case_insensitive(tmp_path: Path) -> None:
    config = GrepToolConfig(
        working_dir=tmp_path,
        rules=[GrepConstraintRule(pattern="**", policy=ConstraintPolicy.ALLOW)],
    )
    tool = GrepTool(config)

    (tmp_path / "test.py").write_text("HELLO\n")

    result = tool.execute(pattern="hello", output_mode="content", **{"-i": True})

    assert "HELLO" in result


def test_execute_glob_filter(tmp_path: Path) -> None:
    config = GrepToolConfig(
        working_dir=tmp_path,
        rules=[GrepConstraintRule(pattern="**", policy=ConstraintPolicy.ALLOW)],
    )
    tool = GrepTool(config)

    (tmp_path / "test.py").write_text("pattern\n")
    (tmp_path / "test.txt").write_text("pattern\n")

    result = tool.execute(pattern="pattern", glob="*.py", output_mode="files_with_matches")

    assert "test.py" in result
    assert "test.txt" not in result


def test_execute_multiline(tmp_path: Path) -> None:
    config = GrepToolConfig(
        working_dir=tmp_path,
        rules=[GrepConstraintRule(pattern="**", policy=ConstraintPolicy.ALLOW)],
    )
    tool = GrepTool(config)

    (tmp_path / "test.py").write_text("start\nmiddle\nend\n")

    result = tool.execute(pattern="start.*end", multiline=True, output_mode="content")

    assert "start" in result


def test_execute_context_lines(tmp_path: Path) -> None:
    config = GrepToolConfig(
        working_dir=tmp_path,
        rules=[GrepConstraintRule(pattern="**", policy=ConstraintPolicy.ALLOW)],
    )
    tool = GrepTool(config)

    (tmp_path / "test.py").write_text("line1\nline2\nmatch\nline4\nline5\n")

    result = tool.execute(pattern="match", output_mode="content", **{"-B": 1, "-A": 1})

    assert "line2" in result
    assert "match" in result
    assert "line4" in result


@patch("agent_server.core.tools.grep.find_ripgrep")
def test_execute_ripgrep_not_found(mock_find: MagicMock, tmp_path: Path) -> None:
    mock_find.return_value = None
    config = GrepToolConfig(
        working_dir=tmp_path,
        rules=[GrepConstraintRule(pattern="**", policy=ConstraintPolicy.ALLOW)],
    )
    tool = GrepTool(config)

    result = tool.execute(pattern="test")

    assert result == "Error: ripgrep execution failed, please use the shell to conduct the search"
