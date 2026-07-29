"""Eval harness — 決定你這塊成敗的東西。

紀律：輸入固定，prompt 演進。
  - fixtures/inputs/*.txt 一旦定案就不再修改
  - prompts/*.md 才是你的實驗變因
  - 每次改 prompt 跑一次，比較同一張表

上 Bedrock 那天的第一件事，就是把 --provider 換成 bedrock 跑這支，
半小時內你會拿到「模型遵從率 / 延遲 / token 成本」三個真實數字。

用法:
    python -m evalh.harness --provider mock
    python -m evalh.harness --stage writer --provider ollama --model qwen2.5:14b
    python -m evalh.harness --provider bedrock --model <model-id> --repeat 3
"""

import argparse
import json
import statistics
import time
from dataclasses import dataclass, field
from pathlib import Path
from typing import Dict, List, NamedTuple, Type

from pydantic import BaseModel

from contracts.intent_spec import IntentSpec
from contracts.narrative import PageNarrative
from llm import fallbacks
from llm.factory import load_provider
from evalh import intent_score
from evalh.checks import CheckResult, run_all_checks

from paths import BASELINES, GOLDEN, INPUTS_INTENT, INPUTS_WRITER, PROMPTS


class Stage(NamedTuple):
    """管線的一段：固定輸入目錄 + 一份 system prompt + 一個輸出契約。

    分成 stage 而非各寫一支腳本，是為了讓所有段共用同一張報表——
    換 provider 時你要比較的是整條管線，不是某一段。

    golden: {輸入檔名: golden 檔名}。只有列在這裡的輸入會做內容比對；
    其餘輸入仍然只驗 schema。寧可少比也不要拿錯的標準答案比。
    """

    inputs: Path
    prompt: str
    schema: Type[BaseModel]
    golden: Dict[str, str] = {}



STAGES: Dict[str, Stage] = {
    "intent": Stage(
        INPUTS_INTENT,
        "intent_parser.system.md",
        IntentSpec,
        # intent_spec.json 照 01_official 的內容寫，直接當標準答案。其餘只驗 schema。
        {"01_official": "intent_spec.json"},
    ),
    "writer": Stage(
        INPUTS_WRITER,
        "insight_writer.system.md",
        PageNarrative,
        # 敘事沒有唯一正確答案，品質由 checks.py 的規則項把關。
        {},
    ),
}


@dataclass
class RunRecord:
    input_id: str
    ok: bool
    attempts: int
    fell_back: bool
    latency_ms: float
    input_tokens: int = 0
    output_tokens: int = 0
    checks: List[CheckResult] = field(default_factory=list)
    content_score: float | None = None  # 對 golden 的內容正確率，無 golden 時為 None
    content_detail: str = ""
    model: str = ""


def run(
    provider_name: str,
    model: str | None,
    repeat: int,
    stage: str = "intent",
    num_ctx: int | None = None,
) -> List[RunRecord]:
    if stage not in STAGES:
        raise ValueError(f"未知 stage: {stage}（可用：{', '.join(STAGES)}）")
    st = STAGES[stage]
    provider = load_provider(provider_name, model, num_ctx)
    system = (PROMPTS / st.prompt).read_text(encoding="utf-8")
    records: List[RunRecord] = []

    for path in sorted(st.inputs.glob("*.txt")):
        text = path.read_text(encoding="utf-8")
        for _ in range(repeat):
            t0 = time.perf_counter()
            res = provider.complete_json(
                system,
                text,
                st.schema,
                # §6.2：三次重試全敗時降級成預設文案，管線不中斷但明確標記。
                fallback=fallbacks.build(st.schema, text),
            )
            latency = res.latency_ms or (time.perf_counter() - t0) * 1000
            # 降級後的輸出是預設文案，拿去跟 golden 比毫無意義，會污染內容正確率
            cscore, cdetail = (
                (None, "") if res.fell_back
                else _content_score(st, path.stem, res.parsed)
            )
            records.append(
                RunRecord(
                    input_id=path.stem,
                    ok=res.parsed is not None and not res.fell_back,
                    attempts=res.attempts,
                    fell_back=res.fell_back,
                    latency_ms=latency,
                    input_tokens=res.input_tokens or 0,
                    output_tokens=res.output_tokens or 0,
                    checks=run_all_checks(res),
                    content_score=cscore,
                    content_detail=cdetail,
                    model=res.model or "",
                )
            )
    return records


def _content_score(st: Stage, input_id: str, parsed) -> tuple[float | None, str]:
    """有 golden 且輸出是 IntentSpec 時，回傳 (內容正確率, 明細)。"""
    name = st.golden.get(input_id)
    if name is None or not isinstance(parsed, IntentSpec):
        return None, ""

    truth = IntentSpec.model_validate(
        json.loads((GOLDEN / name).read_text(encoding="utf-8"))
    )
    scores, overall = intent_score.score(parsed, truth)
    detail = intent_score.report(
        scores, overall, intent_score.reference_diff(parsed, truth)
    )
    return overall, detail


def summarize(records: List[RunRecord], provider: str, repeat: int) -> dict:
    """把一次 run 壓成可存檔比較的數字。

    model 取自實際回應而非 CLI 參數——省略 --model 時走 adapter 預設值，
    而基準線跨模型比較沒有意義，這欄必須是真的跑了哪個模型。
    """
    n = len(records)
    live = [c for r in records for c in r.checks if c.applicable]
    fails: Dict[str, int] = {}
    for c in live:
        if not c.passed:
            fails[c.name] = fails.get(c.name, 0) + 1
    return {
        "provider": provider,
        "model": next((r.model for r in records if r.model), "-"),
        "recorded_at": time.strftime("%Y-%m-%d"),
        "repeat": repeat,
        "inputs": len({r.input_id for r in records}),
        "schema_pass": round(sum(1 for r in records if r.ok) / n, 4),
        "first_try": round(sum(1 for r in records if r.attempts == 1 and r.ok) / n, 4),
        "fallback": round(sum(1 for r in records if r.fell_back) / n, 4),
        "check_fail_rate": round(sum(fails.values()) / len(live), 4) if live else 0.0,
        "check_fails": dict(sorted(fails.items())),
    }


def baseline_path(stage: str, provider: str) -> Path:
    return BASELINES / f"{stage}_{provider}.json"


def load_baseline(stage: str, provider: str) -> dict | None:
    p = baseline_path(stage, provider)
    return json.loads(p.read_text(encoding="utf-8")) if p.exists() else None


def save_baseline(stage: str, provider: str, summary: dict) -> Path:
    BASELINES.mkdir(parents=True, exist_ok=True)
    p = baseline_path(stage, provider)
    p.write_text(json.dumps(summary, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
    return p


def compare_to_baseline(cur: dict, base: dict) -> List[str]:
    """輸出與基準線的差異。方向已正規化：+ 一律代表變好。"""
    lines = [f"\n=== 對基準線（{base['recorded_at']}，{base['model']}，"
             f"repeat={base['repeat']}）==="]
    if cur["inputs"] != base["inputs"]:
        lines.append(f"⚠ 輸入份數不同（{base['inputs']} → {cur['inputs']}），"
                     f"基準線已失效，請重新 --save")
    if cur["model"] != base["model"]:
        lines.append(f"⚠ 模型不同（{base['model']} → {cur['model']}），"
                     f"這是模型比較不是 prompt 比較")

    for key, label, higher_better in [
        ("schema_pass", "schema 通過率", True),
        ("first_try", "一次過", True),
        ("fallback", "fallback", False),
        ("check_fail_rate", "檢查項失敗率", False),
    ]:
        d = cur[key] - base[key]
        arrow = "→" if abs(d) < 1e-9 else ("↑" if d > 0 else "↓")
        good = "" if abs(d) < 1e-9 else ("  ✓" if (d > 0) == higher_better else "  ✗")
        lines.append(f"  {label:<14} {base[key]:>7.1%} {arrow} {cur[key]:>7.1%}"
                     f"  ({d:+.1%}){good}")

    names = sorted(set(cur["check_fails"]) | set(base["check_fails"]))
    if names:
        # repeat 不同時原始次數不可比（rate 仍可比），標明以免誤讀
        note = "" if cur["repeat"] == base["repeat"] else f"（repeat {base['repeat']}→{cur['repeat']}，次數不可直接比）"
        lines.append(f"  失敗項變化：{note}")
        for nm in names:
            b, c = base["check_fails"].get(nm, 0), cur["check_fails"].get(nm, 0)
            if b != c:
                lines.append(f"    {nm:<16} {b} → {c}  ({c - b:+d})")
    return lines


def report(records: List[RunRecord]) -> str:
    n = len(records)
    if n == 0:
        return "沒有測試輸入。請先在 fixtures/inputs/ 放入固定輸入。"

    by_input: Dict[str, List[RunRecord]] = {}
    for r in records:
        by_input.setdefault(r.input_id, []).append(r)

    lines = ["", f"{'input':<22}{'pass':>7}{'一次過':>8}{'fallback':>10}{'p50 ms':>9}"]
    lines.append("-" * 56)
    for k, rs in sorted(by_input.items()):
        passed = sum(1 for r in rs if r.ok) / len(rs)
        first_try = sum(1 for r in rs if r.attempts == 1 and r.ok) / len(rs)
        fb = sum(1 for r in rs if r.fell_back) / len(rs)
        p50 = statistics.median(r.latency_ms for r in rs)
        lines.append(f"{k:<22}{passed:>6.0%}{first_try:>8.0%}{fb:>10.0%}{p50:>9.0f}")

    lines.append("-" * 56)
    all_checks = [c for r in records for c in r.checks]
    live = [c for c in all_checks if c.applicable]
    fails = [c for c in live if not c.passed]
    skipped = len(all_checks) - len(live)
    lines.append(f"總體 schema 通過率     : {sum(1 for r in records if r.ok) / n:.0%}")
    lines.append(f"檢查項失敗數           : {len(fails)} / {len(live)} 項實際判定")
    if skipped:
        lines.append(f"（另有 {skipped} 項不適用本 stage，未計入）")
    tok_in = sum(r.input_tokens for r in records)
    tok_out = sum(r.output_tokens for r in records)
    if tok_in or tok_out:
        lines.append(f"token（in/out）        : {tok_in} / {tok_out}")
    scored = [r for r in records if r.content_score is not None]
    if scored:
        avg = sum(r.content_score for r in scored) / len(scored)
        lines.append(f"對 golden 內容正確率   : {avg:.0%}（{len(scored)}/{n} 份輸入有標準答案）")
    else:
        lines.append("對 golden 內容正確率   : 無標準答案，本 stage 僅驗 schema")

    if fails:
        lines.append("\n失敗明細（前 10 筆）:")
        for c in fails[:10]:
            lines.append(f"  [{c.name}] {c.detail}")

    seen: set = set()
    for r in scored:
        if r.content_score < 1.0 and r.input_id not in seen:
            seen.add(r.input_id)
            lines.append(f"\n=== {r.input_id} 內容比對 ===")
            lines.append(r.content_detail)
    return "\n".join(lines)


def main() -> None:
    ap = argparse.ArgumentParser()
    ap.add_argument("--provider", default="mock")
    ap.add_argument("--model", default=None)
    ap.add_argument("--repeat", type=int, default=1, help="同一輸入重跑次數，量穩定度用")
    ap.add_argument("--stage", default="intent", choices=sorted(STAGES))
    ap.add_argument("--save", action="store_true",
                    help="把這次結果存成新的基準線（改 prompt 前先跑一次）")
    ap.add_argument(
        "--num-ctx",
        type=int,
        default=None,
        help="Ollama context 長度。各模型上限不同（gemma2 系列為 8192），"
        "未指定時用 provider 預設值",
    )
    args = ap.parse_args()
    print(f"stage={args.stage} provider={args.provider} model={args.model or '-'}")

    records = run(args.provider, args.model, args.repeat, args.stage, args.num_ctx)
    print(report(records))

    cur = summarize(records, args.provider, args.repeat)
    base = load_baseline(args.stage, args.provider)
    if base:
        print("\n".join(compare_to_baseline(cur, base)))
    elif not args.save:
        print(f"\n（沒有基準線。加 --save 建立：{baseline_path(args.stage, args.provider)}）")

    if args.save:
        print(f"\n基準線已寫入 {save_baseline(args.stage, args.provider, cur)}")


if __name__ == "__main__":
    main()
