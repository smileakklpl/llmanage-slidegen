"""由引擎產生 writer 的固定輸入。

    python -m tools.gen_writer_fixtures            # 預覽
    python -m tools.gen_writer_fixtures --write    # 覆寫 fixtures/inputs/writer/

紀律與每份輸入的考點見 fixtures/README.md。
"""

import argparse
from typing import Dict, List

from engine.metrics import build_store
from engine.reader import read_sheet
from engine.recognize import recognize_dataset
from engine.summarize import PageBrief, render_brief, top_n_keys
from paths import FSC_1Y, FSC_2Y, INPUTS_WRITER as OUT_DIR

P = "11412"  # 觀察期末


def _store(dataset, metrics=None):
    recs = recognize_dataset(dataset)
    sheets = [
        read_sheet(f, s)
        for f, rec in recs.items()
        for s in rec.sheets
        if metrics is None or any(m in f.name for m in metrics)
    ]
    return build_store(sheets)


def store_1y():
    """單年（11401–11412）。YoY 全部不可算——FR-1.5 的一半。"""
    return _store(FSC_1Y)


def store_2y():
    """雙年（11301–11412）。YoY 真的算得出來——FR-1.5 的另一半。"""
    return _store(FSC_2Y)


def _computable(store, keys: List[str]) -> List[str]:
    return [k for k in keys if store.get(k) and store.get(k).computable]


def briefs_1y(store) -> Dict[str, PageBrief]:
    spend = top_n_keys(store, "_spend_share", 3)
    return {
        "04_yoy_unavailable": PageBrief(
            page=11,
            title="成長動能與趨勢觀察",
            keys=[f"market_total_spend_11401", f"market_total_spend_{P}"] + spend,
            unavailable=[k for k in store.uncomputable_keys() if "_spend_yoy_" in k],
        ),
    }


def briefs_2y(store) -> Dict[str, PageBrief]:
    cards = top_n_keys(store, "_cards_share", 5)
    cards10 = top_n_keys(store, "_cards_share", 10)
    spend = top_n_keys(store, "_spend_share", 5)
    ents = [k.replace("_spend_share", "") for k in spend]

    return {
        "01_market_overview": PageBrief(
            page=5,
            title="市場規模與消費金額趨勢",
            keys=["market_total_cards_11401", f"market_total_cards_{P}",
                  "market_total_spend_11401", f"market_total_spend_{P}"] + cards[:2],
            unavailable=[],
        ),
        "02_ranking": PageBrief(
            page=7,
            title="同業競爭格局與市佔率排名",
            keys=cards + [k.replace("_share", "_rank") for k in cards],
            unavailable=[],
        ),
        "03_single_period": PageBrief(
            page=3,
            title="本期市場總覽",
            keys=[f"market_total_cards_{P}", f"market_total_spend_{P}"],
            unavailable=[],
        ),
        "05_yoy_available": PageBrief(
            page=5,
            title="消費金額年增動能",
            keys=_computable(store, [f"{e}_spend_yoy_{P}" for e in ents])
                 + _computable(store, [f"{e}_cards_yoy_{P}" for e in ents[:3]]),
            unavailable=[],
        ),
        "06_risk_metrics": PageBrief(
            page=9,
            title="信用風險指標分佈",
            keys=top_n_keys(store, "_revolving_share", 5)
                 + top_n_keys(store, "_writeoff_share", 5),
            unavailable=[],
        ),
        "07_wide_ranking": PageBrief(
            page=7,
            title="流通卡數市佔排名",
            keys=cards10 + [k.replace("_share", "_rank") for k in cards10
                            if store.get(k.replace("_share", "_rank"))],
            unavailable=[],
        ),
        "08_mixed_units": PageBrief(
            page=3,
            title="市場總覽與結構變化",
            keys=[f"market_total_cards_{P}", f"market_total_spend_{P}"]
                 + spend[:2]
                 + _computable(store, [f"{ents[0]}_spend_yoy_{P}",
                                       f"{ents[0]}_cards_yoy_{P}"]),
            unavailable=[],
        ),
    }


def all_briefs():
    """{名稱: (brief, store)}，依檔名排序。"""
    out = {}
    s1, s2 = store_1y(), store_2y()
    for name, b in briefs_1y(s1).items():
        out[name] = (b, s1)
    for name, b in briefs_2y(s2).items():
        out[name] = (b, s2)
    return dict(sorted(out.items()))


def main() -> None:
    ap = argparse.ArgumentParser()
    ap.add_argument("--write", action="store_true", help="實際覆寫；省略則只預覽")
    args = ap.parse_args()

    for name, (brief, store) in all_briefs().items():
        text = render_brief(brief, store)
        path = OUT_DIR / f"{name}.txt"
        if args.write:
            path.write_text(text, encoding="utf-8")
            print(f"寫入 {path.name}（{len(text)} 字元）")
        else:
            print(f"─── {path.name}（{len(text)} 字元）───")
            print(text)

    if not args.write:
        print("（預覽模式。加 --write 才會覆寫，並記得重跑 writer 基準線。）")


if __name__ == "__main__":
    main()