from pathlib import Path

from liquid import render

from agent_server.core.tools.edit import (
    ERR_FILE_NOT_FOUND,
    ERR_MULTIPLE_MATCHES,
    ERR_NOT_FOUND,
    ERR_OLD_EQUALS_NEW,
    ERR_PATH_IS_DIRECTORY,
    EditTool,
    EditToolConfig,
)


def test_simple_replace(tmp_path: Path) -> None:
    """Exact match replacement works."""
    config = EditToolConfig(working_dir=tmp_path)
    tool = EditTool(config)

    test_file = tmp_path / "test.txt"
    test_file.write_text("hello world\ngoodbye world\n")

    result = tool.execute(file_path=str(test_file), old_string="hello", new_string="hi")

    assert test_file.read_text() == "hi world\ngoodbye world\n"
    assert "has been updated" in result
    assert "hi world" in result


def test_line_trimmed_replace(tmp_path: Path) -> None:
    """Matches with different leading/trailing whitespace per line."""
    config = EditToolConfig(working_dir=tmp_path)
    tool = EditTool(config)

    test_file = tmp_path / "test.txt"
    # File has leading/trailing spaces that differ from search string
    test_file.write_text("    def foo():  \n        pass\n")

    # Search string has different whitespace - LineTrimmedReplacer finds it by comparing trimmed lines
    result = tool.execute(
        file_path=str(test_file), old_string="def foo():\n    pass", new_string="def bar():\n    return None"
    )

    # The replacement uses exact new_string (no indentation preservation).
    # LineTrimmedReplacer only makes finding content flexible, not the replacement.
    assert test_file.read_text() == "def bar():\n    return None\n"
    assert "has been updated" in result


def test_replace_all(tmp_path: Path) -> None:
    """Multiple occurrences all replaced, snippet shows each change region."""
    config = EditToolConfig(working_dir=tmp_path, context_lines=1)
    tool = EditTool(config)

    test_file = tmp_path / "test.txt"
    test_file.write_text("foo = 1\nbar = 2\nfoo = 3\nbaz = 4\nfoo = 5\n")

    result = tool.execute(file_path=str(test_file), old_string="foo", new_string="qux", replace_all=True)

    assert test_file.read_text() == "qux = 1\nbar = 2\nqux = 3\nbaz = 4\nqux = 5\n"
    assert "has been updated" in result
    # Snippet should contain lines around each replacement
    assert "qux = 1" in result
    assert "qux = 3" in result
    assert "qux = 5" in result


# Edge case tests


def test_error_old_string_equals_new_string(tmp_path: Path) -> None:
    """Returns error when old_string and new_string are identical."""
    config = EditToolConfig(working_dir=tmp_path)
    tool = EditTool(config)

    test_file = tmp_path / "test.txt"
    test_file.write_text("hello world\n")

    result = tool.execute(file_path=str(test_file), old_string="hello", new_string="hello")
    assert result == ERR_OLD_EQUALS_NEW
    assert test_file.read_text() == "hello world\n"  # File unchanged


def test_create_file_with_empty_old_string(tmp_path: Path) -> None:
    """Empty old_string creates or overwrites the file."""
    config = EditToolConfig(working_dir=tmp_path)
    tool = EditTool(config)

    test_file = tmp_path / "new_file.txt"

    result = tool.execute(file_path=str(test_file), old_string="", new_string="new content\n")
    assert test_file.read_text() == "new content\n"
    assert "has been updated" in result


def test_error_file_not_found(tmp_path: Path) -> None:
    """Returns error when file does not exist."""
    config = EditToolConfig(working_dir=tmp_path)
    tool = EditTool(config)

    result = tool.execute(file_path=str(tmp_path / "nonexistent.txt"), old_string="old", new_string="new")
    assert result == render(ERR_FILE_NOT_FOUND, path=(tmp_path / "nonexistent.txt").resolve())


def test_error_path_is_directory(tmp_path: Path) -> None:
    """Returns error when path is a directory."""
    config = EditToolConfig(working_dir=tmp_path)
    tool = EditTool(config)

    result = tool.execute(file_path=str(tmp_path), old_string="old", new_string="new")
    assert result == render(ERR_PATH_IS_DIRECTORY, path=tmp_path.resolve())


def test_error_old_string_not_found(tmp_path: Path) -> None:
    """Returns error when old_string is not in the file."""
    config = EditToolConfig(working_dir=tmp_path)
    tool = EditTool(config)

    test_file = tmp_path / "test.txt"
    test_file.write_text("hello world\n")

    result = tool.execute(file_path=str(test_file), old_string="nonexistent", new_string="new")
    assert result == ERR_NOT_FOUND
    assert test_file.read_text() == "hello world\n"  # File unchanged


def test_error_multiple_matches_without_replace_all(tmp_path: Path) -> None:
    """Returns error when multiple matches found without replace_all."""
    config = EditToolConfig(working_dir=tmp_path)
    tool = EditTool(config)

    test_file = tmp_path / "test.txt"
    test_file.write_text("foo bar foo\n")

    result = tool.execute(file_path=str(test_file), old_string="foo", new_string="baz")
    assert result == ERR_MULTIPLE_MATCHES
    assert test_file.read_text() == "foo bar foo\n"  # File unchanged
