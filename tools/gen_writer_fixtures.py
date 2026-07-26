"""從真實引擎產生 writer 的固定輸入。

    python -m tools.gen_writer_fixtures            # 預覽，不寫檔
    python -m tools.gen_writer_fixtures --write    # 覆寫 fixtures/inputs_writer/

## 為什麼要有這支

fixtures/inputs_writer/*.txt 的用途是「模擬 engine 的 MetricStore 會送來什麼」。
初版是手寫的，於是發生了兩層漂移：

  1. 我改 summarize.py 的不可用指標格式（逐行列 → 按原因分組），
     fixture 和 prompt 的格式說明都沒跟上，writer 的 prompt 開始描述
     一個引擎已經不再產出的格式。
  2. 手寫的 key 是 ctbc_share / taishin_rank，
     引擎實際產出的是 ctbc_cards_share / taishin_cards_rank。

手寫 fixture 必然會漂移，因為沒有東西強迫它跟上。改成由引擎產生就結構性地解決了。

## 但這不代表 fixture 不再凍結

產生 → commit → **凍結**。只有在引擎輸出格式**刻意**改變時才重新產生，
而且重新產生就等於作廢既有的 writer 基準線，必須重跑。
不要為了讓數字好看而重新產生——那就回到「輸入和 prompt 同時變」的老問題了。

四份輸入各自的考點見 fixtures/inputs_writer/README.md。
"""

import argparse
import json
from pathlib import Path
from typing import Dict, List

from contracts.sheet_map import SheetMap
from engine.metrics import build_store
from engine.reader import read_sheet
from engine.summarize import PageBrief, render_brief, top_n_keys
from paths import resolve_xlsx

from paths import GOLDEN, INPUTS_WRITER as OUT_DIR


def _store():
    truth = SheetMap.model_validate(
        json.loads((GOLDEN / "sheet_map.json").read_text(encoding="utf-8"))
    )
    xlsx = resolve_xlsx()
    # P.7 兩張表：一張卡數、一張金額，剛好給得出「單位不同不可比」的情境
    return build_store([read_sheet(xlsx, s) for s in truth.sheets if s.sheet_name.startswith("P.7")])


def briefs(store) -> Dict[str, PageBrief]:
    cards = top_n_keys(store, "_cards_share", 5)
    spend = top_n_keys(store, "_spend_share", 3)

    return {
        # 有明確可比關係，看會不會把實值寫死進敘事
        "01_p5_market_overview": PageBrief(
            page=5, title="市場規模與消費金額趨勢",
            keys=["market_total_cards_11412", "market_total_cards_11401",
                  "market_total_spend_11412", "market_total_spend_11401"] + cards[:2],
            unavailable=[],
        ),
        # 排名情境，最誘人直接寫出市佔率數字
        "02_p7_ranking": PageBrief(
            page=7, title="同業競爭格局與市佔率排名",
            keys=cards + [k.replace("_share", "_rank") for k in cards],
            unavailable=[],
        ),
        # 兩個單位不同的指標，看會不會硬湊 claims
        "03_no_comparison": PageBrief(
            page=3, title="本期市場總覽",
            keys=["market_total_cards_11412", "market_total_spend_11412"],
            unavailable=[],
        ),
        # 列出不可用的 YoY，看會不會偷用或改用文字迂迴描述
        "04_yoy_unavailable": PageBrief(
            page=11, title="成長動能與趨勢觀察",
            keys=["market_total_spend_11401", "market_total_spend_11412"] + spend,
            unavailable=[k for k in store.uncomputable_keys() if "_spend_yoy_" in k],
        ),
    }


def main() -> None:
    ap = argparse.ArgumentParser()
    ap.add_argument("--write", action="store_true", help="實際覆寫；省略則只預覽")
    args = ap.parse_args()

    store = _store()
    for name, brief in briefs(store).items():
        text = render_brief(brief, store)
        path = OUT_DIR / f"{name}.txt"
        if args.write:
            path.write_text(text, encoding="utf-8")
            print(f"寫入 {path}（{len(text)} 字元）")
        else:
            print(f"─── {path.name}（{len(text)} 字元）───")
            print(text)

    if not args.write:
        print("（預覽模式。加 --write 才會覆寫，並記得重跑 writer 基準線。）")


if __name__ == "__main__":
    main()
