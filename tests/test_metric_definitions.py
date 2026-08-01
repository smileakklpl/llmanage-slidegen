"""指標定義的獨立驗算 —— 用 pandas 重算一次，不走 engine 的實作。

選用的外部交叉驗證：參照檔有一欄自算的市佔率，月報沒有。
缺檔時整份 skip，不影響驗收。見 fixtures/README.md。
"""

import os
from pathlib import Path

import pandas as pd
import pytest

REPO_ROOT = Path(__file__).resolve().parent.parent


def _find_reference_xlsx() -> Path | None:
    configured = os.getenv("SLIDEGEN_XLSX")
    candidates = [
        Path(configured).expanduser() if configured else None,
        REPO_ROOT / "source" / "附件四_預期修正參照資料.xlsx",
        REPO_ROOT / "fixtures" / "data" / "附件四_預期修正參照資料.xlsx",
    ]
    return next((path for path in candidates if path and path.is_file()), None)


XLSX = _find_reference_xlsx()
CASES = [
    ("P.7預期修正_流通卡數", "流通卡數市佔率"),
    ("P.7預期修正_當月簽帳金額", "簽帳金額市占率"),
]


def _load(sheet):
    df = pd.read_excel(XLSX, sheet_name=sheet).set_index("金融機構名稱")
    periods = [c for c in df.columns if isinstance(c, int)]
    return df, periods


@pytest.mark.skipif(XLSX is None, reason="找不到附件四，請放進 source/ 或設 SLIDEGEN_XLSX")
@pytest.mark.parametrize("sheet,share_col", CASES)
def test_market_share_is_full_year_aggregate(sheet, share_col):
    df, periods = _load(sheet)
    total, body = df.loc["總計"], df.drop("總計")
    calc = body[periods].sum(axis=1) / total[periods].sum()
    assert (calc - body[share_col]).abs().max() < 1e-9


@pytest.mark.skipif(XLSX is None, reason="找不到附件四，請放進 source/ 或設 SLIDEGEN_XLSX")
@pytest.mark.parametrize("sheet,share_col", CASES)
def test_latest_month_definition_is_wrong(sheet, share_col):
    """反面斷言：確認直覺算法確實會錯，避免有人日後「順手改回去」。"""
    df, periods = _load(sheet)
    total, body = df.loc["總計"], df.drop("總計")
    naive = body[periods[-1]] / total[periods[-1]]
    assert (naive - body[share_col]).abs().max() > 1e-4


@pytest.mark.skipif(XLSX is None, reason="找不到附件四，請放進 source/ 或設 SLIDEGEN_XLSX")
@pytest.mark.parametrize("sheet,share_col", CASES)
def test_total_row_excluded_from_ranking(sheet, share_col):
    df, _ = _load(sheet)
    ranked = df.drop("總計")[share_col].rank(ascending=False)
    assert "總計" not in ranked.index
    assert ranked.min() == 1


@pytest.mark.skipif(XLSX is None, reason="找不到附件四，請放進 source/ 或設 SLIDEGEN_XLSX")
def test_no_yoy_available():
    """附件四只有 114 年，任何 YoY 都不可計算（附件三錯誤 #1）。"""
    _, periods = _load("P.5預期修正_流通卡數")
    years = {str(p)[:3] for p in periods}
    assert years == {"114"}, "出現多個年度，YoY 的可計算性需重新評估"
