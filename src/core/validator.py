"""敘事驗證與佔位符代入 — 規格書 T8 與管線 [4] 的最後一哩。

在此之前 `Claim` 只是個沒人驗證的裝飾欄位：
`evalh/checks.py` 的 check_claims_present 只檢查「有沒有附 claim」，
不檢查「claim 是不是真的」。所以模型可以吐出
`{op:"gt", left:"taishin_share", right:"ctbc_share"}`——台新市佔高於中信——
這在資料上是假的，而目前沒有任何東西會抓到。

附件三錯誤 #8「敘事邏輯矛盾（10.7% 高於 11.0%）」就是這類錯誤，
T8 就是為了它而存在。這支程式把 T8 從規格文字變成可執行的斷言。

## 三種失敗要分開

  CLAIM_FALSE     宣告與資料不符 —— 敘事在說謊，必須擋
  KEY_MISSING     引用了 MetricStore 沒有的 key —— 模型幻覺出一個指標
  NOT_COMPUTABLE  引用了 computable=false 的 key —— FR-1.5 的違規

第三種特別重要：模型引用 yoy 而 engine 判定不可計算時，
不能靜默跳過，那正是附件三錯誤 #1 的成因。
"""

from dataclasses import dataclass
from typing import List, Optional

from contracts.metric_store import MetricStore
from contracts.narrative import PLACEHOLDER, Claim, PageNarrative

CLAIM_FALSE = "CLAIM_FALSE"
KEY_MISSING = "KEY_MISSING"
NOT_COMPUTABLE = "NOT_COMPUTABLE"


@dataclass
class Finding:
    kind: str
    detail: str

    def __str__(self) -> str:
        return f"[{self.kind}] {self.detail}"


def _check_key(store: MetricStore, key: str) -> Optional[Finding]:
    m = store.get(key)
    if m is None:
        return Finding(KEY_MISSING, f"MetricStore 沒有 {key!r}，模型引用了不存在的指標")
    if not m.computable:
        return Finding(
            NOT_COMPUTABLE,
            f"{key!r} 的 computable=false（{m.reason or '未說明'}），依 FR-1.5 不得引用",
        )
    return None


def verify_claim(c: Claim, store: MetricStore) -> Optional[Finding]:
    if (f := _check_key(store, c.left)) is not None:
        return f

    left = store.value_of(c.left)
    if c.op == "rank":
        try:
            want = float(c.right)
        except ValueError:
            return Finding(CLAIM_FALSE, f"rank 的 right 必須是名次整數，收到 {c.right!r}")
        ok = left == want
        return None if ok else Finding(
            CLAIM_FALSE, f"宣告 {c.left} 居第 {c.right} 名，實際為第 {left:g} 名"
        )

    if (f := _check_key(store, c.right)) is not None:
        return f
    right = store.value_of(c.right)

    ops = {
        "gt": (left > right, ">"), "lt": (left < right, "<"),
        "gte": (left >= right, ">="), "lte": (left <= right, "<="),
        "eq": (left == right, "=="),
    }
    ok, sym = ops[c.op]
    return None if ok else Finding(
        CLAIM_FALSE,
        f"宣告 {c.left} {sym} {c.right}，實際為 {left:.6g} vs {right:.6g}",
    )


def validate_narrative(n: PageNarrative, store: MetricStore) -> List[Finding]:
    """驗證一頁敘事：所有引用的 key 都存在可算，且所有 claim 都成立。"""
    findings: List[Finding] = []
    for key in n.all_referenced_metrics():
        if (f := _check_key(store, key)) is not None:
            findings.append(f)
    for c in n.all_claims():
        if (f := verify_claim(c, store)) is not None:
            findings.append(f)
    return findings


# --- 代入（renderer 的職責，這裡做純文字版供端到端驗收） -------------------
def _fmt(v: float, unit: str) -> str:
    if unit == "":  # 比率 → 百分比
        return f"{v * 100:.1f}%"
    if unit == "名":
        return f"第 {v:g} 名"
    return f"{v:,.0f}{unit}"


def substitute(text: str, store: MetricStore) -> str:
    """把 {{key}} 換成實值。找不到的 key 保留原樣並加註，不靜默吞掉。"""
    def repl(m):
        key = m.group(1)
        metric = store.get(key)
        if metric is None or not metric.computable or metric.value is None:
            return f"{{{{{key}?}}}}"
        return _fmt(metric.value, metric.unit)

    return PLACEHOLDER.sub(repl, text)


def render_page(n: PageNarrative, store: MetricStore) -> str:
    """把一頁敘事代入實值後印成純文字。renderer 的最小替身。"""
    out = [f"── 第 {n.page} 頁：{n.title} ──", substitute(n.key_message.text, store)]
    out += [f"  • {substitute(b.text, store)}" for b in n.bullets]
    if n.chart_caption:
        out.append(f"  （圖說）{substitute(n.chart_caption.text, store)}")
    return "\n".join(out)
