from pathlib import Path, PurePosixPath
from zipfile import BadZipFile, ZipFile, is_zipfile

from app.ingestion.schemas import (
    ContainerType,
    FileInspectionResult,
)
from app.ingestion.settings import (
    MAX_UPLOAD_BYTES,
    MAX_XLSX_COMPRESSION_RATIO,
    MAX_XLSX_ENTRIES,
    MAX_XLSX_UNCOMPRESSED_BYTES,
)


class IngestionSecurityError(ValueError):
    """檔案不符合資料讀取安全限制。"""


class FileTooLargeError(
    IngestionSecurityError
):
    pass


class UnsafeArchiveError(
    IngestionSecurityError
):
    pass


def _validate_archive_member_name(
    filename: str,
) -> None:
    member_path = PurePosixPath(filename)

    if member_path.is_absolute():
        raise UnsafeArchiveError(
            "壓縮檔包含絕對路徑"
        )

    if ".." in member_path.parts:
        raise UnsafeArchiveError(
            "壓縮檔包含路徑穿越內容"
        )


def validate_xlsx_archive(
    file_path: str | Path,
) -> None:
    path = Path(file_path)

    if not is_zipfile(path):
        raise UnsafeArchiveError(
            "XLSX 內容不是有效 ZIP 容器"
        )

    try:
        with ZipFile(path) as archive:
            entries = [
                entry
                for entry in archive.infolist()
                if not entry.is_dir()
            ]

            if len(entries) > MAX_XLSX_ENTRIES:
                raise UnsafeArchiveError(
                    "XLSX 壓縮項目過多："
                    f"{len(entries)}"
                )

            total_uncompressed = 0
            total_compressed = 0

            for entry in entries:
                _validate_archive_member_name(
                    entry.filename
                )

                total_uncompressed += (
                    entry.file_size
                )

                total_compressed += (
                    entry.compress_size
                )

            if (
                total_uncompressed
                > MAX_XLSX_UNCOMPRESSED_BYTES
            ):
                raise UnsafeArchiveError(
                    "XLSX 解壓後大小超過限制"
                )

            compression_ratio = (
                total_uncompressed
                / max(total_compressed, 1)
            )

            if (
                compression_ratio
                > MAX_XLSX_COMPRESSION_RATIO
            ):
                raise UnsafeArchiveError(
                    "XLSX 壓縮倍率異常，"
                    "可能是壓縮炸彈"
                )

    except BadZipFile as error:
        raise UnsafeArchiveError(
            "XLSX 壓縮結構已損壞"
        ) from error


def validate_ingestion_security(
    file_path: str | Path,
    inspection: FileInspectionResult,
) -> None:
    path = Path(file_path)

    file_size = path.stat().st_size

    if file_size > MAX_UPLOAD_BYTES:
        raise FileTooLargeError(
            "檔案大小超過限制："
            f"{file_size} bytes"
        )

    if (
        inspection.detected_container_type
        == ContainerType.XLSX
    ):
        validate_xlsx_archive(path)