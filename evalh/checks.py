"""可程式化的品質檢查項。

新增檢查的原則：只加「能自動判定通過與否」的項目。
「敘事讀起來夠不夠顧問風格」不要放進來，那個靠人看。
"""

from dataclasses import dataclass
from typing import List

from contracts.intent_spec import IntentSpec
from contracts.narrative import NarrativeSet, PageNarrative, find_numeric_violations
from llm.base import LLMResult


@dataclass
class CheckResult:
    name: str
    passed: bool
    detail: str = ""
    # 這個檢查項對本次輸出「適用」嗎？writer 的檢查拿到 IntentSpec 時無從判起，
    # 過去一律回傳 passed=True，於是報表上「0 失敗」有四成是不可能失敗的項目。
    # 分開統計，才不會把沒考的題目算成考滿分。
    applicable: bool = True


def check_schema(res: LLMResult) -> CheckResult:
    return CheckResult(
        "schema",
        res.parsed is not None and not res.fell_back,
        "; ".join(res.errors[:2]) if res.errors else "",
    )


def check_first_try(res: LLMResult) -> CheckResult:
    return CheckResult("一次過", res.attempts == 1, f"重試 {res.attempts - 1} 次")


def check_no_literal_numbers(res: LLMResult) -> CheckResult:
    """核心防線：敘事欄位不得出現實際數值。

    schema 其實已經擋掉了，這裡重跑一次是為了在 harness 報表上獨立看到違規率——
    你要知道模型「試圖」寫死數字的頻率，那才是換模型時真正該比較的指標。
    """
    obj = res.parsed
    pages: List[PageNarrative] = []
    if isinstance(obj, NarrativeSet):
        pages = obj.pages
    elif isinstance(obj, PageNarrative):
        pages = [obj]
    else:
        return CheckResult("無實數字", True, "n/a", applicable=False)

    bad: List[str] = []
    for p in pages:
        for blk in [p.key_message, *p.bullets] + ([p.chart_caption] if p.chart_caption else []):
            bad.extend(find_numeric_violations(blk.text))
    return CheckResult("無實數字", not bad, "; ".join(bad[:2]))


def check_claims_present(res: LLMResult) -> CheckResult:
    """T8：出現比較詞的句子必須附帶機器可驗證的 Claim。"""
    obj = res.parsed
    if not isinstance(obj, (NarrativeSet, PageNarrative)):
        return CheckResult("比較句有 claim", True, "n/a", applicable=False)
    pages = obj.pages if isinstance(obj, NarrativeSet) else [obj]
    # 這份清單必須與 prompts/insight_writer.system.md 的比較詞列表一致。
    # 只在檢查器加詞會產生「模型沒被告知卻被扣分」的假失敗；只在 prompt 加詞
    # 則會有偵測不到的漏網比較句——兩邊都要動。
    words = (
        "高於", "低於", "領先", "落後", "居第", "超越", "不及", "最高", "最低",
        "位居末位", "優於", "遜於",
    )
    missing = []
    for p in pages:
        for blk in [p.key_message, *p.bullets]:
            if any(w in blk.text for w in words) and not blk.claims:
                missing.append(f"p{p.page}: {blk.text[:24]}…")
    return CheckResult("比較句有 claim", not missing, "; ".join(missing[:2]))


def check_no_forbidden_derivation(res: LLMResult) -> CheckResult:
    """FR-1.5：資料無基期時不得自行產出 YoY。

    intent 階段允許「使用者要求 YoY」，但必須落在 requested_derivations，
    由 engine 判定 computable，而不是在 metrics 裡混充成既有指標。
    """
    obj = res.parsed
    if not isinstance(obj, IntentSpec):
        return CheckResult("推導指標歸位", True, "n/a", applicable=False)
    leaked = [m for m in obj.metrics if "yoy" in m.lower() or "年增" in m]
    return CheckResult("推導指標歸位", not leaked, f"metrics 內混入推導指標: {leaked}")


# 簡體字偵測用的字集：只收「簡體獨有」的字，不收兩岸共用字。
# 刻意不收 后(皇后) / 台(台灣) / 干 / 里 / 只 這類繁體也在用的字，避免假陽性。
# 這是啟發式而非完備轉換表——目的是把問題量出來，不是做繁簡轉換。
_SIMPLIFIED = set(
    "数额签风险业务产会计价报资贷账变场长银关单总应达过现发对时间条营销"
    "际处权终类结构统华丽显势张规划组织员复杂简传论说语读书写记认识让"
    "为与个们这么开们无问题满创双击点线图标题层级项"
)


def check_traditional_chinese(res: LLMResult) -> CheckResult:
    """輸出必須是繁體中文。

    qwen2.5 是中國訓練的模型，會穩定漂向簡體——實測 intent 階段吐出
    「流通卡数」「签帐金额」「市占率变化」。交付對象是台灣金控，
    簡體字進到簡報就是直接的品質瑕疵，而且人工校對很容易漏掉。
    """
    obj = res.parsed
    if obj is None:
        return CheckResult("繁體中文", True, "n/a", applicable=False)

    hits = sorted({c for c in obj.model_dump_json() if c in _SIMPLIFIED})
    return CheckResult(
        "繁體中文", not hits, f"簡體字: {''.join(hits)}" if hits else ""
    )


ALL = [
    check_schema,
    check_first_try,
    check_no_literal_numbers,
    check_claims_present,
    check_no_forbidden_derivation,
    check_traditional_chinese,
]


def run_all_checks(res: LLMResult) -> List[CheckResult]:
    return [fn(res) for fn in ALL]
