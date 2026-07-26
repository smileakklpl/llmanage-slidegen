"""SheetData → MetricStore。管線 [2]，全部確定性計算，不碰 LLM。

指標定義一律讀 metric_definitions.json，不在程式裡寫死——
那份檔案是業務規則、有測試把關（tests/test_metric_definitions.py 以附件四為標準答案），
而業務規則會改，程式邏輯不該跟著散落各處。

## 三條規則的實作位置

  market_share  全年 12 個月加總佔比，**不是最新月份佔比**。
                用錯定義時簽帳金額有 7 家名次改變，含第 3/4 名對調。
  ranking       依 market_share 降序，且合計列早在 reader 就被排除了。
  yoy           附件四只有 114 年，無 113 基期 → computable=false，
                帶 reason 而不是丟例外或填 0。

## key 命名

    {實體slug}_{指標}_{期間}   taishin_cards_11412
    {實體slug}_{指標}_share    taishin_cards_share
    {實體slug}_{指標}_rank     taishin_cards_rank
    market_{指標}_{期間}       market_cards_11412（合計列）

實體 slug 表目前只收了主要幾家，其餘退回 bank_{序號}（依來源列序，同一份檔案內穩定）。
**完整的機構名稱 ↔ slug 對照表待補**，這裡只放跑得動的最小集合。
"""

import json
from pathlib import Path
from typing import Dict, List, Optional

from contracts.metric_store import Metric, MetricSource, MetricStore
from engine.reader import SheetData

from paths import METRIC_DEFS as DEFS_PATH

# 工作表名稱 → 指標 slug 與單位。
# 比對用 endswith，所以附件四的「P.5預期修正_流通卡數」與轉檔產出的「流通卡數」
# 都吃得到同一條規則。
SHEET_METRICS = {
    "流通卡數": ("cards", "張"),
    "有效卡數": ("active_cards", "張"),
    "當月簽帳金額": ("spend", "千元"),
    "循環信用餘額": ("revolving", "千元"),
    "未到期分期付款餘額": ("installment", "千元"),
    "當月轉銷呆帳金額": ("writeoff", "千元"),
}

# 機構名稱 → slug。只收主要幾家，其餘走 bank_{序號}。
ENTITY_SLUGS = {
    "總計": "market",
    "中國信託商業銀行": "ctbc",
    "國泰世華商業銀行": "cathay",
    "玉山商業銀行": "esun",
    "台北富邦商業銀行": "fubon",
    "台新國際商業銀行": "taishin",
    "臺灣銀行": "bot",
    "第一商業銀行": "firstbank",
    "合作金庫商業銀行": "tcb",
}


def load_definitions() -> dict:
    return json.loads(DEFS_PATH.read_text(encoding="utf-8"))


def metric_slug(sheet_name: str) -> tuple[str, str]:
    """由工作表名稱推出指標 slug。P.5預期修正_流通卡數 → ('cards', '張')"""
    for suffix, (slug, unit) in SHEET_METRICS.items():
        if sheet_name.endswith(suffix):
            return slug, unit
    return "unknown", ""


def entity_slug(name: str, index: int) -> str:
    return ENTITY_SLUGS.get(name, f"bank_{index:02d}")


def _add_yoy(store: MetricStore, sd: SheetData, slug: str, defs: dict) -> None:
    """年增率＝entity[period] / entity[period-100] - 1，基期不存在則 computable=false。

    期間是民國年月六碼（11412 = 114 年 12 月），去年同期就是減 100。
    """
    periods = set(sd.periods)
    formula = defs["yoy"]["definition"]

    for p in sd.periods:
        base = f"{int(p) - 100:05d}"
        entities = [("market_total", sd.total)] + [(e, v) for e, v in sd.rows.items()]

        for i, (name, vals) in enumerate(entities):
            es = "market_total" if name == "market_total" else entity_slug(name, i)
            key = f"{es}_{slug}_yoy_{p}"

            if base not in periods:
                store.add(Metric(
                    key=key, computable=False, label=f"{name} {slug}年增率 {p}",
                    reason=f"缺少基期 {base} 的資料，依 FR-1.5 不得產出"
                           f"（{defs['yoy']['note']}）",
                ))
                continue

            cur, prev = vals.get(p), vals.get(base)
            if cur is None or not prev:
                store.add(Metric(
                    key=key, computable=False, label=f"{name} {slug}年增率 {p}",
                    reason=f"基期 {base} 或當期 {p} 的值缺漏／為零，無法計算年增率",
                ))
                continue

            store.add(Metric(
                key=key, value=cur / prev - 1, unit="", label=f"{name} {slug}年增率 {p}",
                source=MetricSource(sheet=sd.sheet_name, range=f"{name} {p} vs {base}",
                                    formula=formula),
            ))


def build_store(sheets: List[SheetData], defs: Optional[dict] = None) -> MetricStore:
    defs = defs or load_definitions()
    store = MetricStore()

    for sd in sheets:
        slug, unit = metric_slug(sd.sheet_name)
        total_sum = sd.total_sum()

        # --- 合計列的逐期值 ---
        # key 名稱對齊規格書 §5.2 的範例 market_total_cards_11412，
        # 不要自創——A 會照規格書實作，兩邊名稱不一致下游就對不起來。
        for p, v in sd.total.items():
            store.add(Metric(
                key=f"market_total_{slug}_{p}", value=v, unit=unit, label=f"全市場{slug} {p}",
                source=MetricSource(sheet=sd.sheet_name, range=f"期間 {p} 之合計列",
                                    formula="來源檔合計列直接取值"),
            ))

        # --- 各機構逐期值 + 市佔率 ---
        shares: Dict[str, float] = {}
        for i, (name, vals) in enumerate(sd.rows.items(), 1):
            es = entity_slug(name, i)
            for p, v in vals.items():
                store.add(Metric(
                    key=f"{es}_{slug}_{p}", value=v, unit=unit, label=f"{name} {slug} {p}",
                    source=MetricSource(sheet=sd.sheet_name, range=f"{name} × 期間 {p}",
                                        formula="來源檔直接取值"),
                ))

            # market_share：全年加總佔比。最新月份法是 metric_definitions.json
            # 明列的 wrong_but_intuitive，誤差達 0.00425。
            s = sd.entity_sum(name)
            if s is not None and total_sum:
                share = s / total_sum
                shares[es] = share
                store.add(Metric(
                    key=f"{es}_{slug}_share", value=share, unit="", label=f"{name} {slug}市佔率",
                    source=MetricSource(
                        sheet=sd.sheet_name,
                        range=f"{name} 全期間 ÷ 合計列全期間",
                        formula=defs["market_share"]["definition"],
                    ),
                ))

        # --- 排名：依市佔率降序。合計列已在 reader 排除，這裡不會混進來 ---
        for rank, (es, _) in enumerate(
            sorted(shares.items(), key=lambda kv: kv[1], reverse=True), 1
        ):
            store.add(Metric(
                key=f"{es}_{slug}_rank", value=float(rank), unit="名",
                label=f"{es} {slug}排名",
                source=MetricSource(sheet=sd.sheet_name, range="全機構市佔率排序",
                                    formula=defs["ranking"]["definition"]),
            ))

        # --- YoY：基期存在才算，不存在就明確標記不可計算 ---
        #
        # 這個分支是 FR-1.5 的開關，也是附件三錯誤 #1 的防線。
        # 附件四只有 114 年 → 全部 computable=false；
        # 金管會 113+114 兩年 → 真的算得出來。同一段程式碼，資料決定行為。
        _add_yoy(store, sd, slug, defs)

    return store
