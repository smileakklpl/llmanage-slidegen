"""格式辨識器驗收。

辨識器存在的理由是：實測三個模型的 structure locator 是 86–97%，
錯的全是 total_row / row_order 這種確定性可算的欄位。已知格式不該承擔那個風險。

所以這裡的斷言標準比模型嚴格：**對附件四必須 100%**，錯一格就是退步。
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
def test_attachment4_recognized_perfectly():
    """對附件四必須 100%——確定性辨識沒有「差不多」這種事。"""
    rec = recognize_workbook(REF)
    assert rec.kind == ENTITY_BY_PERIOD
    truth = SheetMap.model_validate(json.loads(GOLDEN.read_text(encoding="utf-8")))
    _, overall = score(SheetMap(workbook="x", sheets=rec.sheets), truth)
    assert overall == 1.0, f"命中率 {overall:.0%}，低於 100% 就不該走快路徑"


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
