from pathlib import Path
import time

from agent_server.core.tools._utils import ConstraintPolicy
from agent_server.core.tools.glob import GlobConstraintRule, GlobTool, GlobToolConfig


def test_check_constraint_allow(tmp_path: Path) -> None:
    config = GlobToolConfig(
        working_dir=tmp_path,
        rules=[GlobConstraintRule(pattern="**", policy=ConstraintPolicy.ALLOW)],
    )
    tool = GlobTool(config)
    assert tool.check_constraint(path=str(tmp_path / "test.py")) == ConstraintPolicy.ALLOW


def test_check_constraint_deny(tmp_path: Path) -> None:
    config = GlobToolConfig(
        working_dir=tmp_path,
        rules=[GlobConstraintRule(pattern="*.secret", policy=ConstraintPolicy.DENY)],
    )
    tool = GlobTool(config)
    assert tool.check_constraint(path=str(tmp_path / "data.secret")) == ConstraintPolicy.DENY


def test_check_constraint_default_ask(tmp_path: Path) -> None:
    config = GlobToolConfig(working_dir=tmp_path, rules=[])
    tool = GlobTool(config)
    assert tool.check_constraint(path=str(tmp_path / "any.txt")) == ConstraintPolicy.ASK


def test_execute_basic(tmp_path: Path) -> None:
    config = GlobToolConfig(working_dir=tmp_path)
    tool = GlobTool(config)

    (tmp_path / "file1.py").write_text("content1")
    (tmp_path / "file2.py").write_text("content2")
    (tmp_path / "file3.txt").write_text("content3")

    result = tool.execute(pattern="*.py")

    assert "file1.py" in result
    assert "file2.py" in result
    assert "file3.txt" not in result


def test_execute_sorted_by_mtime(tmp_path: Path) -> None:
    config = GlobToolConfig(working_dir=tmp_path)
    tool = GlobTool(config)

    (tmp_path / "old.py").write_text("old")
    time.sleep(0.01)
    (tmp_path / "new.py").write_text("new")

    result = tool.execute(pattern="*.py")
    lines = result.strip().split("\n")

    assert len(lines) == 2
    assert "new.py" in lines[0]
    assert "old.py" in lines[1]


def test_execute_no_matches(tmp_path: Path) -> None:
    config = GlobToolConfig(working_dir=tmp_path)
    tool = GlobTool(config)

    (tmp_path / "file.txt").write_text("content")

    result = tool.execute(pattern="*.py")

    assert result == "No matches found"


def test_execute_directory_not_found(tmp_path: Path) -> None:
    config = GlobToolConfig(working_dir=tmp_path)
    tool = GlobTool(config)

    result = tool.execute(pattern="*.py", path=str(tmp_path / "nonexistent"))

    assert "Error: Directory not found" in result


def test_execute_path_not_directory(tmp_path: Path) -> None:
    config = GlobToolConfig(working_dir=tmp_path)
    tool = GlobTool(config)

    file_path = tmp_path / "file.txt"
    file_path.write_text("content")

    result = tool.execute(pattern="*.py", path=str(file_path))

    assert "Error: Path is not a directory" in result


def test_execute_with_explicit_path(tmp_path: Path) -> None:
    config = GlobToolConfig(working_dir=tmp_path)
    tool = GlobTool(config)

    subdir = tmp_path / "subdir"
    subdir.mkdir()
    (subdir / "file.py").write_text("content")
    (tmp_path / "root.py").write_text("root content")

    result = tool.execute(pattern="*.py", path=str(subdir))

    assert "file.py" in result
    assert "root.py" not in result


def test_execute_filters_directories(tmp_path: Path) -> None:
    config = GlobToolConfig(working_dir=tmp_path)
    tool = GlobTool(config)

    (tmp_path / "file.py").write_text("content")
    (tmp_path / "dir.py").mkdir()

    result = tool.execute(pattern="*.py")

    assert "file.py" in result
    assert "dir.py" not in result
