from pathlib import Path

from agent_server.core.tools.write import WriteTool, WriteToolConfig


def test_execute_writes_new_file(tmp_path: Path) -> None:
    config = WriteToolConfig(working_dir=tmp_path)
    tool = WriteTool(config)

    test_file = tmp_path / "test.txt"
    tool.execute(file_path=str(test_file), content="hello world")

    assert test_file.read_text() == "hello world"


def test_execute_creates_parent_directories(tmp_path: Path) -> None:
    config = WriteToolConfig(working_dir=tmp_path)
    tool = WriteTool(config)

    test_file = tmp_path / "deep" / "nested" / "file.txt"
    tool.execute(file_path=str(test_file), content="content")

    assert test_file.exists()
    assert test_file.read_text() == "content"


def test_execute_overwrites_existing_file(tmp_path: Path) -> None:
    config = WriteToolConfig(working_dir=tmp_path)
    tool = WriteTool(config)

    test_file = tmp_path / "existing.txt"
    test_file.write_text("old content")

    tool.execute(file_path=str(test_file), content="new content")

    assert test_file.read_text() == "new content"
