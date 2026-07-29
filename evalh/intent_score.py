"""IntentSpec 對 golden 的比對計分 — 補上 harness 最大的盲點。

在此之前 harness 只驗 schema，不驗內容：一個 page_count=3、audience='大家'、
sections 只有 appendix 的 IntentSpec，五項檢查全過。schema 合法不等於解析正確。

計分原則刻意分成兩類，因為 IntentSpec 的欄位可驗證性天差地遠：

  可計分——受 Literal 列舉約束的欄位（sections / chart_preferences / style）
    與明確數值（page_count）。這些有唯一正確答案，答錯就是答錯。

  僅供參考——自由文字欄位（metrics / audience）。contracts 明講 metrics 是
    「語意名稱，非 MetricStore 實際 key，由 engine 解析對應」，
    拿字串相等去比會製造假失敗。只印出差異讓人眼睛看，不計入分數。

requested_derivations 雖然也是自由文字，但它是 FR-1.5 的關口，
漏一個就代表下游不會去判 computable，所以用集合比對計分而非參考。
"""

from dataclasses import dataclass
from typing import Any, Dict, List, Sequence, Tuple

from contracts.intent_spec import IntentSpec

# (欄位, 比對方式) — set 用 Jaccard 給部分分數，exact 全有全無
SCORED: Sequence[Tuple[str, str]] = (
    ("page_count", "exact"),
    ("style", "exact"),
    ("sections", "set"),
    ("chart_preferences", "set"),
    ("requested_derivations", "set"),
)

REFERENCE_ONLY: Sequence[str] = ("metrics", "audience")


@dataclass
class FieldScore:
    field: str
    score: float
    detail: str = ""


def _jaccard(a: List[str], b: List[str]) -> Tuple[float, str]:
    sa, sb = set(a), set(b)
    if not sa and not sb:
        return 1.0, ""
    inter, union = sa & sb, sa | sb
    missing, extra = sorted(sa - sb), sorted(sb - sa)
    bits = []
    if missing:
        bits.append(f"漏 {missing}")
    if extra:
        bits.append(f"多 {extra}")
    return len(inter) / len(union), "，".join(bits)


def score(predicted: IntentSpec, truth: IntentSpec) -> Tuple[List[FieldScore], float]:
    out: List[FieldScore] = []
    for field, mode in SCORED:
        tv, pv = getattr(truth, field), getattr(predicted, field)
        if mode == "exact":
            hit = tv == pv
            out.append(
                FieldScore(field, 1.0 if hit else 0.0, "" if hit else f"應為 {tv!r}，模型答 {pv!r}")
            )
        else:
            s, detail = _jaccard(tv, pv)
            out.append(FieldScore(field, s, detail))

    overall = sum(f.score for f in out) / len(out) if out else 0.0
    return out, overall


def reference_diff(predicted: IntentSpec, truth: IntentSpec) -> List[str]:
    """自由文字欄位的差異，只印不計分。"""
    lines: List[str] = []
    for field in REFERENCE_ONLY:
        tv, pv = getattr(truth, field), getattr(predicted, field)
        if isinstance(tv, list):
            _, detail = _jaccard(tv, pv)
            lines.append(f"  {field}: 模型給了 {len(pv)} 項，golden {len(tv)} 項" + (f"（{detail}）" if detail else ""))
        elif tv != pv:
            lines.append(f"  {field}: golden={tv!r} 模型={pv!r}")
    return lines


def report(scores: List[FieldScore], overall: float, refs: List[str]) -> str:
    lines = ["", f"{'欄位':<24}{'得分':>8}", "-" * 44]
    for f in scores:
        lines.append(f"{f.field:<24}{f.score:>7.0%}")
    lines.append("-" * 44)
    lines.append(f"內容正確率: {overall:.0%}")

    misses = [f for f in scores if f.score < 1.0 and f.detail]
    if misses:
        lines.append("\n內容錯誤明細:")
        for f in misses:
            lines.append(f"  [{f.field}] {f.detail}")
    if refs:
        lines.append("\n自由文字欄位（不計分，供人工判讀）:")
        lines.extend(refs)
    return "\n".join(lines)
