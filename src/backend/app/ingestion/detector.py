import csv
import zipfile
from pathlib import Path

from app.ingestion.schemas import ContainerType, FileInspectionResult


MIME_BY_TYPE: dict[ContainerType, str] = {
    ContainerType.XLSX:
        "application/vnd.openxmlformats-officedocument.spreadsheetml.sheet",
    ContainerType.XLS:
        "application/vnd.ms-excel",
    ContainerType.CSV:
        "text/csv",
    ContainerType.PDF:
        "application/pdf",
    ContainerType.PNG:
        "image/png",
    ContainerType.JPEG:
        "image/jpeg",
    ContainerType.PPTX:
        "application/vnd.openxmlformats-officedocument.presentationml.presentation",
    ContainerType.DOCX:
        "application/vnd.openxmlformats-officedocument.wordprocessingml.document",
    ContainerType.ZIP:
        "application/zip",
    ContainerType.UNKNOWN:
        "application/octet-stream",
}


EXPECTED_EXTENSIONS: dict[ContainerType, set[str]] = {
    ContainerType.XLSX: {".xlsx"},
    ContainerType.XLS: {".xls"},
    ContainerType.CSV: {".csv", ".tsv"},
    ContainerType.PDF: {".pdf"},
    ContainerType.PNG: {".png"},
    ContainerType.JPEG: {".jpg", ".jpeg"},
    ContainerType.PPTX: {".pptx"},
    ContainerType.DOCX: {".docx"},
    ContainerType.ZIP: {".zip"},
}


def _detect_zip_container(
    file_path: Path,
) -> tuple[ContainerType, bool, list[str]]:
    """
    判斷 ZIP 是一般 ZIP，還是 XLSX、PPTX、DOCX。

    Office Open XML 檔案本質上都是 ZIP，
    差異在於壓縮檔內部的目錄結構。
    """
    warnings: list[str] = []

    try:
        with zipfile.ZipFile(file_path, "r") as archive:
            names = set(archive.namelist())

            is_encrypted = any(
                info.flag_bits & 0x1
                for info in archive.infolist()
            )

            if "xl/workbook.xml" in names:
                return ContainerType.XLSX, is_encrypted, warnings

            if "ppt/presentation.xml" in names:
                return ContainerType.PPTX, is_encrypted, warnings

            if "word/document.xml" in names:
                return ContainerType.DOCX, is_encrypted, warnings

            return ContainerType.ZIP, is_encrypted, warnings

    except zipfile.BadZipFile:
        warnings.append("檔案具有 ZIP 特徵，但壓縮結構已損壞")
        return ContainerType.UNKNOWN, False, warnings


def _looks_like_delimited_text(file_path: Path) -> bool:
    """
    判斷文字檔是否可能是 CSV、TSV 或其他分隔資料。

    目前支援常見的 UTF-8、Big5、CP950 編碼。
    """
    raw = file_path.read_bytes()[:65536]

    if not raw or b"\x00" in raw:
        return False

    decoded_text: str | None = None

    for encoding in ("utf-8-sig", "utf-8", "cp950", "big5"):
        try:
            decoded_text = raw.decode(encoding)
            break
        except UnicodeDecodeError:
            continue

    if decoded_text is None:
        return False

    lines = [
        line.strip()
        for line in decoded_text.splitlines()
        if line.strip()
    ]

    if len(lines) < 2:
        return False

    sample = "\n".join(lines[:20])

    try:
        dialect = csv.Sniffer().sniff(
            sample,
            delimiters=",\t;|",
        )

        delimiter = dialect.delimiter

        delimiter_counts = [
            line.count(delimiter)
            for line in lines[:10]
        ]

        positive_counts = [
            count
            for count in delimiter_counts
            if count > 0
        ]

        if len(positive_counts) < 2:
            return False

        # 至少多數列應有一致的欄位分隔數量。
        most_common_count = max(
            set(positive_counts),
            key=positive_counts.count,
        )

        matching_rows = sum(
            count == most_common_count
            for count in positive_counts
        )

        return matching_rows >= 2

    except csv.Error:
        return False


def _detect_container_type(
    file_path: Path,
) -> tuple[ContainerType, bool, list[str]]:
    """依照檔案簽章與內部結構判斷格式。"""
    warnings: list[str] = []

    with file_path.open("rb") as file:
        header = file.read(16)

    # PDF
    if header.startswith(b"%PDF"):
        return ContainerType.PDF, False, warnings

    # PNG
    if header.startswith(b"\x89PNG\r\n\x1a\n"):
        return ContainerType.PNG, False, warnings

    # JPEG
    if header.startswith(b"\xff\xd8\xff"):
        return ContainerType.JPEG, False, warnings

    # 舊版 Microsoft Office OLE Compound File
    if header.startswith(
        b"\xd0\xcf\x11\xe0\xa1\xb1\x1a\xe1"
    ):
        return ContainerType.XLS, False, warnings

    # ZIP 或 Office Open XML
    if header.startswith(b"PK"):
        return _detect_zip_container(file_path)

    # 沒有固定簽章的文字表格
    if _looks_like_delimited_text(file_path):
        return ContainerType.CSV, False, warnings

    return ContainerType.UNKNOWN, False, warnings


def inspect_file(file_path: str | Path) -> FileInspectionResult:
    """
    檢查使用者上傳的檔案。

    不依賴副檔名決定真實格式，
    副檔名只用於檢查名稱與內容是否一致。
    """
    path = Path(file_path)

    if not path.exists():
        raise FileNotFoundError(f"找不到檔案：{path}")

    if not path.is_file():
        raise ValueError(f"路徑不是檔案：{path}")

    size_bytes = path.stat().st_size
    extension = path.suffix.lower() or None
    warnings: list[str] = []

    if size_bytes == 0:
        return FileInspectionResult(
            filename=path.name,
            extension=extension,
            detected_container_type=ContainerType.UNKNOWN,
            mime_type=MIME_BY_TYPE[ContainerType.UNKNOWN],
            size_bytes=0,
            extension_matches_content=None,
            is_readable=False,
            warnings=["檔案內容為空"],
        )

    try:
        (
            detected_type,
            is_encrypted,
            detector_warnings,
        ) = _detect_container_type(path)

        warnings.extend(detector_warnings)

    except OSError as error:
        return FileInspectionResult(
            filename=path.name,
            extension=extension,
            detected_container_type=ContainerType.UNKNOWN,
            mime_type=MIME_BY_TYPE[ContainerType.UNKNOWN],
            size_bytes=size_bytes,
            extension_matches_content=None,
            is_readable=False,
            warnings=[f"檔案讀取失敗：{error}"],
        )

    expected_extensions = EXPECTED_EXTENSIONS.get(detected_type)

    if expected_extensions is None or extension is None:
        extension_matches_content = None
    else:
        extension_matches_content = extension in expected_extensions

        if not extension_matches_content:
            warnings.append(
                f"副檔名 {extension} 與實際內容 "
                f"{detected_type.value} 不一致"
            )

    if detected_type == ContainerType.UNKNOWN:
        warnings.append("目前無法判斷檔案格式")

    if is_encrypted:
        warnings.append("檔案可能受密碼保護或包含加密內容")

    return FileInspectionResult(
        filename=path.name,
        extension=extension,
        detected_container_type=detected_type,
        mime_type=MIME_BY_TYPE[detected_type],
        size_bytes=size_bytes,
        extension_matches_content=extension_matches_content,
        is_readable=detected_type != ContainerType.UNKNOWN,
        is_encrypted=is_encrypted,
        warnings=warnings,
    )