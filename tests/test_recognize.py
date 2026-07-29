"""格式辨識器驗收。

已知格式不該交給模型定位：實測模型在 total_row / row_order 這類
確定性可算的欄位上會出錯。辨識得出來就走確定性路徑。
"""

import json
import sys
from pathlib import Path

import pytest

from contracts.sheet_map import SheetMap
from engine.recognize import (
    ENTITY_BY_PERIOD,
    FSC_MONTHLY,
    UNKNOWN,
    recognize_dataset,
    recognize_workbook,
)
from evalh.sheetmap_score import score
from paths import find_xlsx

from paths import FSC_RAW as FSC_SRC, GOLDEN as _G
GOLDEN = _G / "sheet_map.json"
REF = find_xlsx()


@pytest.mark.skipif(REF is None, reason="缺少附件四")
def test_attachment4_also_recognized():
    """辨識器不只認得金管會月報，也認得附件四那種單檔多工作表的排版。

    這是**通用性**的證據：辨識規則不是為單一檔案寫死的。
    附件四本身是選用的外部交叉驗證檔，不進版控。
    """
    rec = recognize_workbook(REF)
    assert rec.recognized and rec.kind == ENTITY_BY_PERIOD
    assert len(rec.sheets) == 4
    for spec in rec.sheets:
        assert spec.archetype == ENTITY_BY_PERIOD
        assert spec.total_row is not None, f"{spec.sheet_name} 沒認出合計列"
        assert len(spec.period_cols) == 12


@pytest.mark.skipif(not FSC_SRC.exists(), reason="缺少金管會月報")
def test_fsc_monthly_recognized():
    """金管會原始檔：標題列在第 4 列、合計列第 37 列、第 38 列之後是註釋。"""
    f = sorted(FSC_SRC.glob("*/*.xlsx"))[-1]
    rec = recognize_workbook(f)
    assert rec.kind == FSC_MONTHLY
    s = rec.sheets[0]
    assert (s.header_row, s.first_data_row, s.total_row) == (4, 5, 37)
    assert s.archetype == "entity_by_metric"
    assert s.last_data_row == 37, "註釋列不得算進資料範圍"


@pytest.mark.parametrize("ds,n_periods", [("fsc_114", 12), ("fsc_113_114", 24)])
def test_converted_datasets_recognized(ds, n_periods):
    from paths import DATA
    d = DATA / ds
    if not d.exists():
        pytest.skip(f"尚未產出 {ds}，先跑 engine.ingest_fsc")
    recs = recognize_dataset(d)
    assert len(recs) == 6, "一指標一檔，應有 6 個檔"
    for r in recs.values():
        assert r.kind == ENTITY_BY_PERIOD
        assert len(r.periods) == n_periods


def test_unknown_shape_falls_back(tmp_path):
    """認不得就必須回 unknown 交給模型，不可以硬猜。"""
    from openpyxl import Workbook

    wb = Workbook()
    ws = wb.active
    ws.append(["姓名", "興趣", "備註"])
    ws.append(["甲", "登山", ""])
    ws.append(["乙", "游泳", ""])
    p = tmp_path / "無關資料.xlsx"
    wb.save(p)

    rec = recognize_workbook(p)
    assert rec.kind == UNKNOWN
    assert not rec.recognized


@pytest.mark.skipif(REF is None, reason="缺少附件四")
def test_summary_stays_bounded_regardless_of_store_size():
    """摘要長度不得隨 MetricStore 大小成長——那正是摘要策略的目的。"""
    from engine.metrics import build_store
    from engine.reader import read_sheet
    from engine.summarize import ranking_page, render_brief

    rec = recognize_workbook(REF)
    store = build_store([read_sheet(REF, s) for s in rec.sheets])
    text = render_brief(ranking_page(store, "cards"), store)
    assert len(store.metrics) > 1000
    assert len(text) < 1200, f"摘要 {len(text)} 字元，過長；不可用指標應按規則分組而非逐一列舉"
