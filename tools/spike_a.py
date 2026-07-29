"""Spike A — 量模型結構定位的命中率。

    python -m tools.spike_a --provider mock
    python -m tools.spike_a --provider bedrock
    python -m tools.spike_a --dump-profile   # 只看 profiler 產出，不打模型

輸入固定為 fixtures/data/fsc_113_114，標準答案是 fixtures/golden/sheet_map.json。
被量的 `locate_one` 在 src/locator.py。
"""

import argparse
import json
from pathlib import Path
from typing import Dict, List, Optional, Tuple

from contracts.sheet_map import SheetMap, SheetSpec
from evalh.sheetmap_score import FIELDS, report, score
from llm.factory import load_provider
from locator import locate_one
from paths import FSC_2Y
from engine.profiler import profile_workbook

from paths import GOLDEN, PROMPTS


def main() -> None:
    ap = argparse.ArgumentParser()
    ap.add_argument("--provider", default="mock")
    ap.add_argument("--model", default=None)
    ap.add_argument("--dataset", default=None, help="資料集目錄，省略時用 fsc_113_114")
    ap.add_argument("--dump-profile", action="store_true")
    ap.add_argument(
        "--num-ctx",
        type=int,
        default=None,
        help="Ollama context 長度。各模型上限不同（gemma2 系列為 8192）",
    )
    ap.add_argument(
        "--repeat",
        type=int,
        default=1,
        help="每張工作表重跑次數，量穩定度用。total_row 曾出現同結構兩張表一對一錯，"
        "那種抖動 n=1 看不出來。",
    )
    args = ap.parse_args()

    dataset = Path(args.dataset) if args.dataset else FSC_2Y
    profiles = {}
    for f in sorted(dataset.glob("*.xlsx")):
        profiles.update(profile_workbook(f))

    if args.dump_profile:
        for name, text in profiles.items():
            print(text)
            print(f"\n[「{name}」{len(text)} 字元，約 {len(text)//3} tokens]\n")
        total = sum(len(t) for t in profiles.values())
        print(f"[共 {len(profiles)} 張，合計 {total} 字元，約 {total//3} tokens]")
        return

    system = (PROMPTS / "structure_locator.system.md").read_text(encoding="utf-8")
    provider = load_provider(args.provider, args.model, args.num_ctx)

    truth = SheetMap.model_validate(
        json.loads((GOLDEN / "sheet_map.json").read_text(encoding="utf-8"))
    )

    retries = total_ms = 0.0
    tok_in = tok_out = calls = 0
    overalls: List[float] = []
    # 逐欄位累計「跑了幾次、對了幾次」，才看得出哪個欄位是穩定錯、哪個是抖動
    field_hits: Dict[Tuple[str, str], List[int]] = {}

    for _ in range(args.repeat):
        specs: List[SheetSpec] = []
        for name, text in profiles.items():
            spec, res = locate_one(provider, system, name, text)
            calls += 1
            retries += res.attempts - 1
            total_ms += res.latency_ms or 0
            tok_in += res.input_tokens or 0
            tok_out += res.output_tokens or 0

            if spec is None:
                print(f"「{name}」未產出合法 SheetMap，計為漏失。錯誤：")
                for e in res.errors[:2]:
                    print("  ", e[:200])
                continue
            specs.append(spec)

        scores, overall = score(SheetMap(workbook=dataset.name, sheets=specs), truth)
        overalls.append(overall)
        for s in scores:
            missed = {m[0] for m in s.misses}
            for f in FIELDS:
                rec = field_hits.setdefault((s.sheet_name, f), [0, 0])
                rec[0] += 1
                rec[1] += 0 if f in missed else 1

    print(report(scores, overall))
    if args.repeat > 1:
        print(f"\n=== {args.repeat} 次重跑的穩定度 ===")
        print("各次整體命中率: " + "  ".join(f"{o:.0%}" for o in overalls))
        flaky = [
            (sheet, f, hit, ran)
            for (sheet, f), (ran, hit) in sorted(field_hits.items())
            if 0 < hit < ran
        ]
        if flaky:
            print("時對時錯的欄位（抖動，非系統性錯誤）:")
            for sheet, f, hit, ran in flaky:
                print(f"  [{sheet}] {f}: {hit}/{ran} 次正確")
        else:
            print("沒有時對時錯的欄位——所有錯誤都是穩定重現的")

    print(f"\nprovider={provider.name} model={args.model or '-'} "
          f"呼叫={calls} 重試={retries:.0f} "
          f"總延遲={total_ms:.0f}ms tokens={tok_in}/{tok_out}")


if __name__ == "__main__":
    main()
