"""期間型欄名辨識（entity × period 交叉表）。

金管會信用卡月報的版型是「金融機構名稱 | 11401 | … | 11412」——只有第一欄
是文字。表頭偵測原本要求「至少一半是文字」，這種表的文字比例只有 1/13，
於是整張工作表被判成無法辨識結構而跳過（實測：0 個 dataset）。

交叉表在金融報表裡極常見，所以這裡把「認得哪些寫法」與「交叉表確實能被
判成結構化表格」都鎖住。邊界值特別重要：民國年月與純數字的區間只差一位數，
放寬過頭會把金額欄誤認成期間欄。
"""

from pathlib import Path

import pytest
from openpyxl import Workbook

from app.ingestion.classifier import (
    inspect_excel_content,
    is_period_like_header,
)
from app.ingestion.schemas import SheetContentType


PERIODS = [
    11401,
    11402,
    11403,
    11404,
    11405,
    11406,
    11407,
    11408,
    11409,
    11410,
    11411,
    11412,
]


@pytest.mark.parametrize(
    "value",
    [
        11401,  # 民國年月，金管會月報
        11412,
        10001,  # 民國年月下界
        19912,  # 民國年月上界
        114,  # 民國年
        100,
        199,
        2026,  # 西元年
        1900,
        2200,
        202601,  # 西元年月
        220012,
        11401.0,  # openpyxl 讀回來可能是 float
    ],
)
def test_period_like_headers(value) -> None:
    assert is_period_like_header(value) is True


@pytest.mark.parametrize(
    "value",
    [
        11413,  # 月份 13，不存在
        11400,  # 月份 00
        99,  # 民國年下界之外
        200,  # 落在年與年月之間的空隙
        1899,
        2201,
        220013,
        1234567,  # 位數過多，多半是金額
        58723456,  # 卡數／金額這種大整數不可誤認成期間
        3.14,
        None,
        "11401",  # 字串本來就會被當文字欄名，不走這條
        True,  # bool 是 int 的子類，必須先排除
        False,
    ],
)
def test_non_period_headers(value) -> None:
    assert is_period_like_header(value) is False


def _write_cross_table(path: Path, title: str = "流通卡數") -> None:
    """造一張 entity × period 交叉表，形狀對齊金管會月報。"""
    workbook = Workbook()
    worksheet = workbook.active
    worksheet.title = title

    worksheet.append(["金融機構名稱"] + PERIODS)

    for index, bank in enumerate(
        ["臺灣銀行", "土地銀行", "中國信託", "台新銀行"],
        start=1,
    ):
        worksheet.append(
            [bank] + [100000 * index + month for month in range(1, 13)]
        )

    workbook.save(path)


def test_cross_table_is_structured_table(tmp_path: Path) -> None:
    file_path = tmp_path / "cross.xlsx"
    _write_cross_table(file_path)

    result = inspect_excel_content(file_path)
    sheet = result.sheets[0]

    assert (
        sheet.primary_content_type
        == SheetContentType.STRUCTURED_TABLE
    )
    assert sheet.detected_header_row == 1


def test_cross_table_score_passes_downstream_threshold(
    tmp_path: Path,
) -> None:
    """0.6 是 pipeline 決定要不要抽這張表的門檻，低於此值整張跳過。"""
    file_path = tmp_path / "cross.xlsx"
    _write_cross_table(file_path)

    sheet = inspect_excel_content(file_path).sheets[0]

    assert sheet.structured_table_score >= 0.6


def test_cross_table_with_leading_title_rows(
    tmp_path: Path,
) -> None:
    """月報上方常有標題與單位列，表頭不在第 1 列。"""
    file_path = tmp_path / "cross_with_title.xlsx"

    workbook = Workbook()
    worksheet = workbook.active
    worksheet.title = "流通卡數"

    worksheet.append(["信用卡業務統計"])
    worksheet.append(["單位：張"])
    worksheet.append(["金融機構名稱"] + PERIODS)

    for index, bank in enumerate(
        ["臺灣銀行", "中國信託", "台新銀行"],
        start=1,
    ):
        worksheet.append(
            [bank] + [100000 * index + month for month in range(1, 13)]
        )

    workbook.save(file_path)

    sheet = inspect_excel_content(file_path).sheets[0]

    assert (
        sheet.primary_content_type
        == SheetContentType.STRUCTURED_TABLE
    )
    assert sheet.detected_header_row == 3
