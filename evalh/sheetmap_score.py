"""SheetMap 命中率計分 — Spike A 的驗收工具。

這支程式回答的問題是：**模型看了 profiler 的文字描述後，有沒有正確定位?**
不是「敘事好不好看」，是可以硬斷言的對錯。

每張工作表逐欄位比對，欄位權重相同。
total_row 特別重要——附件四 P.5 在末列、P.7 在首列，
定位錯的後果是合計列被當成一般機構納入排名，
這正是附件三 P.7 錯誤 #6/#7 那類錯誤的成因。
"""

from dataclasses import dataclass
from typing import Any, Dict, List, Optional, Tuple

from contracts.sheet_map import SheetMap, SheetSpec

FIELDS = [
    "archetype",
    "header_row",
    "first_data_row",
    "last_data_row",
    "total_row",
    "entity_col",
    "period_cols",
    "derived_cols",
    "row_order",
]


def _extract(s: SheetSpec) -> Dict[str, Any]:
    return {
        "archetype": s.archetype,
        "header_row": s.header_row,
        "first_data_row": s.first_data_row,
        "last_data_row": s.last_data_row,
        "total_row": s.total_row,
        "entity_col": s.entity_col,
        "period_cols": tuple(s.period_cols),
        "derived_cols": tuple(s.derived_cols),
        "row_order": s.row_order,
    }


@dataclass
class SheetScore:
    sheet_name: str
    hits: int
    total: int
    misses: List[Tuple[str, Any, Any]]

    @property
    def rate(self) -> float:
        return self.hits / self.total if self.total else 0.0


def score(predicted: SheetMap, truth: SheetMap) -> Tuple[List[SheetScore], float]:
    scores: List[SheetScore] = []
    for t in truth.sheets:
        p = predicted.get(t.sheet_name)
        if p is None:
            scores.append(
                SheetScore(
                    t.sheet_name,
                    0,
                    len(FIELDS),
                    [("<模型未輸出此工作表>", t.sheet_name, "缺")],
                )
            )
            continue
        tv, pv = _extract(t), _extract(p)
        misses = [(f, tv[f], pv[f]) for f in FIELDS if tv[f] != pv[f]]
        scores.append(SheetScore(t.sheet_name, len(FIELDS) - len(misses), len(FIELDS), misses))

    hits = sum(s.hits for s in scores)
    total = sum(s.total for s in scores)
    return scores, (hits / total if total else 0.0)


def report(scores: List[SheetScore], overall: float) -> str:
    lines = ["", f"{'工作表':<26}{'命中':>8}{'比率':>8}", "-" * 44]
    for s in scores:
        lines.append(f"{s.sheet_name:<26}{s.hits}/{s.total:<6}{s.rate:>7.0%}")
    lines.append("-" * 44)
    lines.append(f"整體命中率: {overall:.0%}")
    misses = [(s.sheet_name, m) for s in scores for m in s.misses]
    if misses:
        lines.append("\n定位錯誤明細:")
        for name, (field, exp, got) in misses:
            lines.append(f"  [{name}] {field}: 應為 {exp!r}，模型答 {got!r}")
    return "\n".join(lines)
