"""多模型並排比較 — FR-A1 的驗收證據。

驗收條件是「現場切換至地端開源模型仍可跑通」。一次跑完整個模型 × stage 矩陣，
輸出同一張表，才回答得了三個問題：

  1. 哪些缺陷**跟著換模型消失** → 那是該模型的特性，不是你的 prompt 缺陷
  2. 哪些**留下來** → 那是 prompt 或契約真的有問題，值得修
  3. 哪些是**新出現的** → 你的 prompt 隱含依賴了原模型的某些行為

實例：繁體中文違規率在 qwen2.5:14b 是 25%、在 gemma2:9b 是 0%——
只用一個模型的話，你會以為那是自己 prompt 沒寫好，然後去加一個
根本不需要的繁簡轉換層。

用法:
    python compare_models.py --models gemma2:9b,qwen2.5:7b,llama3.1:8b
    python compare_models.py --models gemma2:9b --stages writer --repeat 5
"""

import argparse
import statistics
import time
import traceback
from typing import Dict, List, Optional

from evalh.harness import STAGES, RunRecord, run


def _pad(s: str, width: int) -> str:
    """以顯示寬度對齊：CJK 字元佔兩格。"""
    w = sum(2 if ord(c) > 0x2E80 else 1 for c in s)
    return s + " " * max(0, width - w)


class Result:
    def __init__(self, model: str, records: List[RunRecord]):
        self.model = model
        self.records = records
        n = len(records) or 1
        self.schema = sum(1 for r in records if r.ok) / n
        self.first_try = sum(1 for r in records if r.attempts == 1 and r.ok) / n
        self.fallback = sum(1 for r in records if r.fell_back) / n
        self.p50 = statistics.median([r.latency_ms for r in records] or [0])
        self.tok_in = sum(r.input_tokens for r in records)
        self.tok_out = sum(r.output_tokens for r in records)
        scored = [r for r in records if r.content_score is not None]
        self.content: Optional[float] = (
            sum(r.content_score for r in scored) / len(scored) if scored else None
        )
        self.check_fails: Dict[str, int] = {}
        # 只有次數無法診斷。次數告訴你「有問題」，例句才告訴你「是什麼問題」。
        self.check_examples: Dict[str, List[str]] = {}
        for r in records:
            for c in r.checks:
                if c.applicable and not c.passed:
                    self.check_fails[c.name] = self.check_fails.get(c.name, 0) + 1
                    if c.detail:
                        self.check_examples.setdefault(c.name, []).append(c.detail)


def compare(
    models: List[str],
    stage: str,
    repeat: int,
    num_ctx: Optional[int],
    max_examples: int = 3,
) -> str:
    results: List[Result] = []
    errors: Dict[str, str] = {}

    for m in models:
        print(f"  跑 {m} / {stage} …", flush=True)
        t0 = time.perf_counter()
        try:
            results.append(Result(m, run("ollama", m, repeat, stage, num_ctx)))
            print(f"    完成，耗時 {time.perf_counter() - t0:.0f}s", flush=True)
        except Exception as e:  # 模型沒 pull、服務沒開等等，不要讓整個矩陣停擺
            errors[m] = f"{type(e).__name__}: {e}"
            print(f"    失敗：{errors[m][:120]}", flush=True)

    lines = [f"\n{'=' * 78}", f"stage = {stage}（repeat={repeat}）", "=" * 78]
    if not results:
        lines.append("所有模型都失敗了。")
        for m, e in errors.items():
            lines.append(f"  {m}: {e[:200]}")
        return "\n".join(lines)

    head = (
        _pad("模型", 18) + _pad("schema", 9) + _pad("一次過", 9)
        + _pad("fallback", 10) + _pad("內容", 8) + _pad("p50ms", 9) + "tok in/out"
    )
    lines += ["", head, "-" * 78]
    for r in results:
        content = f"{r.content:.0%}" if r.content is not None else "—"
        lines.append(
            _pad(r.model, 18)
            + _pad(f"{r.schema:.0%}", 9)
            + _pad(f"{r.first_try:.0%}", 9)
            + _pad(f"{r.fallback:.0%}", 10)
            + _pad(content, 8)
            + _pad(f"{r.p50:.0f}", 9)
            + f"{r.tok_in}/{r.tok_out}"
        )

    names = sorted({k for r in results for k in r.check_fails})
    if names:
        lines += ["", "檢查項失敗次數（越低越好；分母為實際判定數）", "-" * 78]
        lines.append(_pad("模型", 18) + "".join(_pad(n, 16) for n in names))
        for r in results:
            lines.append(
                _pad(r.model, 18)
                + "".join(_pad(str(r.check_fails.get(n, 0)), 16) for n in names)
            )
        lines.append("")
        lines.append("讀法：某欄只有一個模型有數字 → 該模型的特性；"
                     "所有模型都有 → 你的 prompt 或契約的問題。")

        # 三個模型都失敗的檢查項最值得診斷，優先印它的例句
        shared = [n for n in names if all(r.check_fails.get(n) for r in results)]
        for name in shared or names:
            if name == "一次過":  # 重試次數不是內容缺陷，例句沒有診斷價值
                continue
            lines += ["", f"--- 「{name}」失敗例句 ---"]
            for r in results:
                for ex in r.check_examples.get(name, [])[:max_examples]:
                    lines.append(f"  [{r.model}] {ex[:150]}")
    else:
        lines += ["", "所有模型的檢查項全數通過。"]

    for m, e in errors.items():
        lines.append(f"\n[{m}] 未能執行：{e[:300]}")
    return "\n".join(lines)


def main() -> None:
    ap = argparse.ArgumentParser()
    ap.add_argument(
        "--models",
        default="gemma2:9b,qwen2.5:7b,llama3.1:8b",
        help="逗號分隔的 ollama 模型名稱",
    )
    ap.add_argument("--stages", default="intent,writer", help="逗號分隔，可用：" + ",".join(STAGES))
    ap.add_argument("--repeat", type=int, default=3)
    ap.add_argument("--num-ctx", type=int, default=None)
    ap.add_argument(
        "--examples",
        type=int,
        default=3,
        help="每個模型每個檢查項最多印幾則失敗例句，設 0 只看次數",
    )
    args = ap.parse_args()

    models = [m.strip() for m in args.models.split(",") if m.strip()]
    stages = [s.strip() for s in args.stages.split(",") if s.strip()]
    out: List[str] = []
    for stage in stages:
        out.append(compare(models, stage, args.repeat, args.num_ctx, args.examples))
    print("\n".join(out))


if __name__ == "__main__":
    main()
