import base64
from pathlib import Path

import pymupdf

from agent_server.core.tools.read import ConstraintPolicy, ReadConstraintRule, ReadTool, ReadToolConfig


def test_execute_regular_file(tmp_path: Path) -> None:
    config = ReadToolConfig(
        working_dir=tmp_path,
        rules=[ReadConstraintRule(pattern="**", policy=ConstraintPolicy.ALLOW)],
    )
    tool = ReadTool(config)

    test_file = tmp_path / "test.txt"
    test_file.write_text("line one\nline two\nline three\n")

    result = tool.execute(file_path=str(test_file))

    assert isinstance(result, str)
    assert "     1\tline one" in result
    assert "     2\tline two" in result
    assert "     3\tline three" in result


def test_execute_pdf_file(tmp_path: Path) -> None:
    config = ReadToolConfig(
        working_dir=tmp_path,
        rules=[ReadConstraintRule(pattern="**", policy=ConstraintPolicy.ALLOW)],
    )
    tool = ReadTool(config)

    pdf_path = tmp_path / "test.pdf"
    doc = pymupdf.open()
    page = doc.new_page()
    page.insert_text((50, 50), "Hello PDF World")
    doc.save(pdf_path)
    doc.close()

    result = tool.execute(file_path=str(pdf_path))

    assert isinstance(result, str)
    assert "Hello PDF World" in result


def test_execute_image_file(tmp_path: Path) -> None:
    config = ReadToolConfig(
        working_dir=tmp_path,
        rules=[ReadConstraintRule(pattern="**", policy=ConstraintPolicy.ALLOW)],
    )
    tool = ReadTool(config)

    png_data = base64.b64decode(
        "iVBORw0KGgoAAAANSUhEUgAAAAIAAAACCAIAAAD91JpzAAAAFklEQVR4nGP4z8DAAAb/GRiYGBgYoAwACjAC/xlvz4sAAAAASUVORK5CYII="
    )
    png_path = tmp_path / "test.png"
    png_path.write_bytes(png_data)

    result = tool.execute(file_path=str(png_path))

    assert isinstance(result, list)
    assert len(result) == 1
    assert result[0]["type"] == "input_image"
    image_url = result[0]["image_url"]
    assert image_url is not None
    assert image_url.startswith("data:image/png;base64,")
