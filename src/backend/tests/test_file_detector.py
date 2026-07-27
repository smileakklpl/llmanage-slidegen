import zipfile
from pathlib import Path

from app.ingestion.detector import inspect_file
from app.ingestion.schemas import ContainerType


def test_detect_csv(tmp_path: Path) -> None:
    file_path = tmp_path / "sample.csv"
    file_path.write_text(
        "name,value\nA,100\nB,200\n",
        encoding="utf-8",
    )

    result = inspect_file(file_path)

    assert result.detected_container_type == ContainerType.CSV
    assert result.extension_matches_content is True
    assert result.is_readable is True


def test_detect_png(tmp_path: Path) -> None:
    file_path = tmp_path / "sample.png"

    # PNG signature 加上一些測試內容。
    file_path.write_bytes(
        b"\x89PNG\r\n\x1a\n" + b"test-content"
    )

    result = inspect_file(file_path)

    assert result.detected_container_type == ContainerType.PNG
    assert result.extension_matches_content is True


def test_detect_xlsx_structure(tmp_path: Path) -> None:
    file_path = tmp_path / "sample.xlsx"

    with zipfile.ZipFile(file_path, "w") as archive:
        archive.writestr("[Content_Types].xml", "")
        archive.writestr("xl/workbook.xml", "")

    result = inspect_file(file_path)

    assert result.detected_container_type == ContainerType.XLSX
    assert result.extension_matches_content is True


def test_detect_wrong_extension(tmp_path: Path) -> None:
    file_path = tmp_path / "fake.xlsx"
    file_path.write_bytes(
        b"\x89PNG\r\n\x1a\n" + b"test-content"
    )

    result = inspect_file(file_path)

    assert result.detected_container_type == ContainerType.PNG
    assert result.extension_matches_content is False
    assert len(result.warnings) > 0


def test_empty_file(tmp_path: Path) -> None:
    file_path = tmp_path / "empty.csv"
    file_path.write_bytes(b"")

    result = inspect_file(file_path)

    assert result.is_readable is False
    assert result.detected_container_type == ContainerType.UNKNOWN