"""以附件四為標準答案，斷言指標定義正確。

這是 T1 的核心：算出來的值必須逐格等於附件四。
附件四的位置由 paths.find_xlsx() 解析，規則與 spike_a.py 完全相同。
"""

import sys
from pathlib import Path

import pandas as pd
import pytest

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))
from paths import find_xlsx

XLSX = find_xlsx()
CASES = [
    ("P.7預期修正_流通卡數", "流通卡數市佔率"),
    ("P.7預期修正_當月簽帳金額", "簽帳金額市占率"),
]


def _load(sheet):
    df = pd.read_excel(XLSX, sheet_name=sheet).set_index("金融機構名稱")
    periods = [c for c in df.columns if isinstance(c, int)]
    return df, periods


@pytest.mark.skipif(XLSX is None, reason="找不到附件四，請放進 fixtures/data/ 或設 B_LLM_XLSX")
@pytest.mark.parametrize("sheet,share_col", CASES)
def test_market_share_is_full_year_aggregate(sheet, share_col):
    df, periods = _load(sheet)
    total, body = df.loc["總計"], df.drop("總計")
    calc = body[periods].sum(axis=1) / total[periods].sum()
    assert (calc - body[share_col]).abs().max() < 1e-9


@pytest.mark.skipif(XLSX is None, reason="找不到附件四，請放進 fixtures/data/ 或設 B_LLM_XLSX")
@pytest.mark.parametrize("sheet,share_col", CASES)
def test_latest_month_definition_is_wrong(sheet, share_col):
    """反面斷言：確認直覺算法確實會錯，避免有人日後「順手改回去」。"""
    df, periods = _load(sheet)
    total, body = df.loc["總計"], df.drop("總計")
    naive = body[periods[-1]] / total[periods[-1]]
    assert (naive - body[share_col]).abs().max() > 1e-4


@pytest.mark.skipif(XLSX is None, reason="找不到附件四，請放進 fixtures/data/ 或設 B_LLM_XLSX")
@pytest.mark.parametrize("sheet,share_col", CASES)
def test_total_row_excluded_from_ranking(sheet, share_col):
    df, _ = _load(sheet)
    ranked = df.drop("總計")[share_col].rank(ascending=False)
    assert "總計" not in ranked.index
    assert ranked.min() == 1


@pytest.mark.skipif(XLSX is None, reason="找不到附件四，請放進 fixtures/data/ 或設 B_LLM_XLSX")
def test_no_yoy_available():
    """附件四只有 114 年，任何 YoY 都不可計算（附件三錯誤 #1）。"""
    _, periods = _load("P.5預期修正_流通卡數")
    years = {str(p)[:3] for p in periods}
    assert years == {"114"}, "出現多個年度，YoY 的可計算性需重新評估"
