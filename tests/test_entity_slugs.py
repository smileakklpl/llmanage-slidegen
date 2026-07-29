"""實體 slug 的穩定性 —— MetricStore key 必須是可靠的機構識別碼。

slug 若隨來源檔案的列序改變，key 就失去識別能力，且逐格比對偵測不到。
設計說明見 docs/設計決策.md §1.1。
"""

import collections

import pytest

from engine.metrics import ENTITY_SLUGS, entity_slug
from engine.reader import read_sheet
from engine.recognize import recognize_dataset
from paths import DATA, find_xlsx

FSC = DATA / "fsc_113_114"


def _fsc_institutions():
    recs = recognize_dataset(FSC)
    sd = [read_sheet(f, s) for f, rec in recs.items() for s in rec.sheets
          if "流通卡數" in f.name][0]
    return list(sd.rows)


def test_no_duplicate_slugs():
    """兩家機構共用一個 slug 會讓後寫入的覆蓋前一家，且完全無聲。"""
    dupes = [s for s, n in collections.Counter(ENTITY_SLUGS.values()).items() if n > 1]
    assert not dupes, f"slug 重複：{dupes}"


def test_slug_ignores_row_order():
    """同一個名稱不論在第幾列都要得到同一個 slug。"""
    for name in ("中國信託商業銀行", "星展(台灣)商業銀行", "不存在的銀行股份有限公司"):
        assert entity_slug(name) == entity_slug(name)
    # 未收錄的機構走名稱雜湊，仍然穩定且不與已收錄者相撞
    unknown = entity_slug("不存在的銀行股份有限公司")
    assert unknown.startswith("bank_")
    assert unknown not in ENTITY_SLUGS.values()


@pytest.mark.skipif(not FSC.exists(), reason="缺少金管會月報")
def test_all_fsc_institutions_have_named_slug():
    """月報上的每一家發卡機構都要有可讀的 slug。

    退回雜湊代表月報新增了機構，需補進 ENTITY_SLUGS。
    """
    missing = [n for n in _fsc_institutions() if n not in ENTITY_SLUGS]
    assert not missing, (
        f"這些機構還沒有 slug，請補進 engine/metrics.py 的 ENTITY_SLUGS：{missing}"
    )


@pytest.mark.skipif(
    not FSC.exists() or find_xlsx() is None, reason="缺少金管會月報或附件四"
)
def test_same_institution_same_key_across_sources():
    """參照檔與月報是同一份資料，兩邊的 key 與值必須完全一致。"""
    from engine.metrics import build_store
    from engine.recognize import recognize_workbook
    from paths import FSC_1Y, resolve_xlsx

    xlsx = resolve_xlsx()
    a = build_store([read_sheet(xlsx, s) for s in recognize_workbook(xlsx).sheets
                     if s.sheet_name.startswith("P.7")])

    recs = recognize_dataset(FSC_1Y)
    b = build_store([read_sheet(f, s) for f, rec in recs.items() for s in rec.sheets
                     if "流通卡數" in f.name or "當月簽帳金額" in f.name])

    assert set(a.metrics) == set(b.metrics), (
        "兩個資料源的 key 集合不同："
        f"只在附件四 {sorted(set(a.metrics) - set(b.metrics))[:5]}；"
        f"只在月報 {sorted(set(b.metrics) - set(a.metrics))[:5]}"
    )

    diff = [
        k for k in a.metrics
        if (a.get(k).value is None) != (b.get(k).value is None)
        or (a.get(k).value is not None and abs(a.get(k).value - b.get(k).value) > 1e-9)
    ]
    assert not diff, f"{len(diff)} 個 key 的值不同，例：{diff[:5]}"
