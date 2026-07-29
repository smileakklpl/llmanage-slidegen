import os


def _read_int(
    name: str,
    default: int,
) -> int:
    raw_value = os.getenv(name)

    if raw_value is None:
        return default

    try:
        value = int(raw_value)
    except ValueError as error:
        raise ValueError(
            f"環境變數 {name} 必須是整數"
        ) from error

    if value <= 0:
        raise ValueError(
            f"環境變數 {name} 必須大於 0"
        )

    return value


MAX_UPLOAD_BYTES = _read_int(
    "MAX_UPLOAD_BYTES",
    50 * 1024 * 1024,
)

UPLOAD_CHUNK_BYTES = _read_int(
    "UPLOAD_CHUNK_BYTES",
    1024 * 1024,
)

MAX_XLSX_ENTRIES = _read_int(
    "MAX_XLSX_ENTRIES",
    5_000,
)

MAX_XLSX_UNCOMPRESSED_BYTES = _read_int(
    "MAX_XLSX_UNCOMPRESSED_BYTES",
    250 * 1024 * 1024,
)

MAX_XLSX_COMPRESSION_RATIO = _read_int(
    "MAX_XLSX_COMPRESSION_RATIO",
    300,
)