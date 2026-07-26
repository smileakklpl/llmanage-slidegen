"""MetricStore → writer 的單頁摘要。規格書沒定義這塊，是 engine 與 writer 之間的界面缺口。

為什麼需要摘要策略：附件四四張表就產生 922 個指標，全塞給 writer 會爆 token，
弱模型的 context 也撐不住。必須**按頁切，只餵該頁需要的 key**。

輸出格式刻意與 fixtures/inputs_writer/*.txt 逐字相同——
那四份手寫 fixture 當初就是在模擬這支程式的產出。現在它真的存在了，
fixture 從「假裝的輸入」變成「這支程式的迴歸基準」。

## 兩條硬規則

1. **不可用指標要明列，不能只是不給。** 只是省略的話，模型會自己腦補
   「沒給大概是我要自己算」——實測 gemma2 就寫過
   `{{cards_11412 - cards_11401}}` 這種自算式。明講 computable=false
   才擋得住。

2. **實值要給，佔位符要求要另外講。** writer 必須知道 A>B 才寫得出 claim，
   所以看得到數值是必要的；「看得到卻不准寫出來」的規則寫在 prompt，
   不寫在資料裡（fixtures/inputs_writer/README.md 記錄了為什麼）。
"""

import re
from dataclasses import dataclass, field
from typing import Dict, List, Optional

from contracts.metric_store import MetricStore


@dataclass
class PageBrief:
    """一頁要餵給 writer 的東西。"""

    page: int
    title: str
    keys: List[str]
    # None = 沿用 store 全部的不可計算指標；[] = 本頁明確沒有要提醒的。
    # 不能用空 list 當「未指定」的哨兵——`brief.unavailable or store.…()` 會把
    # 「本頁沒有」誤判成「未指定」，於是把整個 store 的不可算清單全倒進摘要。
    unavailable: Optional[List[str]] = None

    @property
    def narrative_id(self) -> str:
        return f"n_p{self.page}"


def top_n_keys(store: MetricStore, suffix: str, n: int) -> List[str]:
    """依數值降序取前 n 個以 suffix 結尾的 key。用來挑「市佔率前五名」這種。"""
    cands = [
        (k, m.value)
        for k, m in store.metrics.items()
        if k.endswith(suffix) and m.computable and m.value is not None
    ]
    cands.sort(key=lambda kv: kv[1], reverse=True)
    return [k for k, _ in cands[:n]]


def _fmt(v: float) -> str:
    if v == int(v):
        return str(int(v))
    return f"{v:.6f}".rstrip("0")


def render_brief(brief: PageBrief, store: MetricStore) -> str:
    """產生 writer 的輸入文字。格式與 fixtures/inputs_writer/*.txt 一致。"""
    lines = [
        f"頁碼：{brief.page}",
        f"narrative_id：{brief.narrative_id}",
        f"頁面主題：{brief.title}",
        "",
        "可用指標（key = 實值）：",
    ]
    for k in brief.keys:
        m = store.get(k)
        if m is None or not m.computable or m.value is None:
            continue
        lines.append(f"  {k} = {_fmt(m.value)}")

    keys = brief.unavailable if brief.unavailable is not None else store.uncomputable_keys()
    lines += _unavailable_lines(keys, store)
    return "\n".join(lines) + "\n"


# 不可用指標**按原因分組陳述，不逐一列舉**。
#
# 24 個月 × 33 家機構會產生數百個不可計算的年增率 key。逐一列出會把摘要
# 從 385 字元撐到一萬字以上——那正是這支模組要防的事。
# 但也不能整段省略：模型看不到「不可用」就會自己腦補推導
# （實測 gemma2 寫過 {{cards_11412 - cards_11401}} 這種自算式）。
#
# 折衷是陳述**規則**：「所有年增率都不可計算，因為沒有基期」比列 396 個 key
# 更短、也更好懂——模型要的是「這類東西不能碰」，不是完整清單。
# 只把「期間六碼」正規化掉，不要動其他數字——
# 一開始用 \d+ 把 FR-1.5 也吃成 FR-….…，規則本身就看不懂了。
_PERIOD_CODE = re.compile(r"\b1\d{4}\b")
EXAMPLES_PER_GROUP = 2


def _rule_of(reason: str) -> str:
    """把「缺少基期 11301 的資料」正規化成「缺少基期 XXXXX 的資料」，好讓同類合併。"""
    return _PERIOD_CODE.sub("該期", (reason or "未說明原因").split("（")[0].strip())


def _unavailable_lines(keys: List[str], store: MetricStore) -> List[str]:
    if not keys:
        return []

    groups: Dict[str, List[str]] = {}
    for k in sorted(keys):
        m = store.get(k)
        groups.setdefault(_rule_of(m.reason if m else ""), []).append(k)

    lines = ["", "不可用指標（engine 已判定 computable=false，不得引用也不得改用文字描述）："]
    for rule, ks in sorted(groups.items(), key=lambda kv: -len(kv[1])):
        eg = "、".join(ks[:EXAMPLES_PER_GROUP])
        more = f" 等 {len(ks)} 個" if len(ks) > EXAMPLES_PER_GROUP else ""
        lines.append(f"  {eg}{more}：{rule}")
    return lines


def ranking_page(store: MetricStore, metric: str = "cards", page: int = 7,
                 top: int = 5) -> PageBrief:
    """示範用的頁面組裝：市佔率排名頁。

    真正的頁面組裝規則應由 DeckSpec 決定（規格書 §5.3），
    這裡只是把管線串起來所需的最小版本。
    """
    share_keys = top_n_keys(store, f"_{metric}_share", top)
    rank_keys = [k.replace("_share", "_rank") for k in share_keys]
    keys = share_keys + [k for k in rank_keys if store.get(k)]
    return PageBrief(
        page=page,
        title="同業競爭格局與市佔率排名",
        keys=keys,
        unavailable=[k for k in store.uncomputable_keys() if metric in k],
    )
