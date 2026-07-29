"""端到端管線 — 從 xlsx 跑到成品文字，證明所有契約真的接得起來。

進入點是 repo root 的 `main.py`（它負責補上 `src/` 的 import 路徑）：

    python main.py --provider mock                    # 對照組，全綠
    python main.py --provider ollama --model gemma2:9b
    python main.py --provider ollama --model gemma2:9b --locate  # 連結構定位也交給模型

## 管線

    fsc_113_114/
        │  profiler（確定性）
        ▼
    純文字結構描述 ──►【LLM】locator ──► SheetMap
        │                                   │
        │  ◄────────────────────────────────┘
        ▼  reader + metrics（確定性）
    MetricStore（每個值都帶 source）
        │  summarize（按頁切）
        ▼
    PageBrief ──►【LLM】writer ──► PageNarrative（只有 {{key}}，無數字）
        │                              │
        ▼  validator（確定性）          │
    T8 斷言：claim 是否與資料相符 ◄─────┘
        │
        ▼  substitute
    成品文字

貫穿全線的規則：**數字只從確定性程式流出，LLM 只做判斷與表達。**

`--locate` 預設關閉，走 golden SheetMap。理由是結構定位錯了不會長得像錯的
（見 fallbacks.py），端到端 demo 應該先確保下游正確；要驗模型的定位能力
請用 `python -m tools.spike_a`，那支才是為此設計的。
"""

import argparse
from pathlib import Path
from typing import List

from contracts.narrative import PageNarrative
from contracts.sheet_map import SheetMap, SheetSpec
from engine.metrics import build_store
from engine.recognize import recognize_dataset
from engine.reader import read_sheet
from engine.summarize import ranking_page, render_brief
from engine.profiler import profile_workbook
from locator import locate_one
from validator import render_page, validate_narrative
from llm import fallbacks
from llm.factory import load_provider

from paths import FSC_2Y, PROMPTS


def locate(provider, target: Path, force_model: bool) -> tuple[SheetMap, List[Path], str]:
    """定位資料結構。認得的格式走確定性快路徑，認不得的才問模型。

    回傳 (SheetMap, 檔案清單, 走了哪條路)。同一份 SheetMap 可能橫跨多個檔案，
    所以 SheetSpec 帶 source_file——一指標一檔的資料集就是這種形狀。
    """
    files = sorted(target.glob("*.xlsx")) if target.is_dir() else [target]
    recognized = recognize_dataset(target)

    specs: List[SheetSpec] = []
    used_model = False
    system = None

    for f in files:
        rec = recognized[f]
        if rec.recognized and not force_model:
            for s in rec.sheets:
                s.source_file = f.name
            specs.extend(rec.sheets)
            continue

        # 認不得（或使用者指定要考模型）→ profiler + LLM
        used_model = True
        if system is None:
            system = (PROMPTS / "structure_locator.system.md").read_text(encoding="utf-8")
        for name, text in profile_workbook(f).items():
            spec, _ = locate_one(provider, system, name, text)
            if spec is None:
                raise RuntimeError(
                    f"「{f.name} / {name}」結構定位失敗。"
                    f"SheetMap 沒有 fallback（見 fallbacks.py），管線中止。"
                )
            spec.source_file = f.name
            specs.append(spec)

    kinds = {recognized[f].kind for f in files}
    route = "模型定位" if used_model else f"確定性辨識（{'、'.join(sorted(kinds))}）"
    return SheetMap(workbook=target.name, sheets=specs), files, route


def main() -> None:
    ap = argparse.ArgumentParser()
    ap.add_argument("--provider", default="mock")
    ap.add_argument("--model", default=None)
    ap.add_argument("--num-ctx", type=int, default=None)
    ap.add_argument("--dataset", default=None,
                    help="資料來源：單一 xlsx 或一整個目錄，省略時用 fsc_113_114")
    ap.add_argument("--locate", action="store_true",
                    help="強制交給模型定位，即使格式認得（用來考模型）")
    ap.add_argument("--metric", default="cards", choices=["cards", "spend"])
    args = ap.parse_args()

    target = Path(args.dataset) if args.dataset else FSC_2Y
    provider = load_provider(args.provider, args.model, args.num_ctx)

    print("[1.5] 結構辨識…")
    smap, files, route = locate(provider, target, args.locate)
    print(f"      {target.name}：{len(files)} 檔 / {len(smap.sheets)} 表，走 {route}")

    print("[2]   讀取資料並計算指標…")
    by_name = {f.name: f for f in files}
    wanted = [s for s in smap.sheets if s.archetype == "entity_by_period"]
    sheets = [read_sheet(by_name.get(s.source_file, target), s) for s in wanted]
    store = build_store(sheets)
    print(f"      MetricStore：{len(store.computable_keys())} 個可算、"
          f"{len(store.uncomputable_keys())} 個不可算")

    print("[2.5] 按頁切出摘要…")
    brief = ranking_page(store, args.metric)
    brief_text = render_brief(brief, store)
    print(f"      第 {brief.page} 頁餵入 {len(brief.keys)} 個 key，"
          f"{len(brief_text)} 字元（全部塞的話是 {len(store.metrics)} 個 key）")

    print("[3]   敘事生成…")
    system = (PROMPTS / "insight_writer.system.md").read_text(encoding="utf-8")
    res = provider.complete_json(
        system, brief_text, PageNarrative,
        fallback=fallbacks.build(PageNarrative, brief_text),
    )
    if res.parsed is None:
        print("      敘事生成失敗且無法降級，管線中止。")
        for e in res.errors[:2]:
            print("       ", e[:200])
        return
    narrative: PageNarrative = res.parsed
    print(f"      {'降級文案' if res.fell_back else '模型輸出'}，重試 {res.attempts - 1} 次")

    print("[4]   T8 敘事一致性驗證…")
    findings = validate_narrative(narrative, store)
    if findings:
        print(f"      ✗ {len(findings)} 項不通過：")
        for f in findings:
            print(f"        {f}")
    else:
        n_claims = len(narrative.all_claims())
        print(f"      ✓ 全數通過（{len(narrative.all_referenced_metrics())} 個引用、"
              f"{n_claims} 條 claim 逐一對 MetricStore 斷言）")

    print("\n[5]   代入實值後的成品：\n")
    print(render_page(narrative, store))
    print()


if __name__ == "__main__":
    main()
