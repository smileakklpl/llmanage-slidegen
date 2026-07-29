"""SheetData → MetricStore。管線 [2]，全部確定性計算，不碰 LLM。

指標定義讀 metric_definitions.json，不寫死在程式裡。

key 命名：

    {實體slug}_{指標}_{期間}   taishin_cards_11412
    {實體slug}_{指標}_share / _rank / _yoy_{期間}
    market_total_{指標}_{期間}  合計列

取捨與踩過的坑見 docs/設計決策.md。
"""

import hashlib
import json
from pathlib import Path
from typing import Dict, List, Optional

from contracts.metric_store import Metric, MetricSource, MetricStore
from engine.reader import SheetData

from paths import METRIC_DEFS as DEFS_PATH

# 工作表名稱 → 指標 slug 與單位。用 endswith 比對，容許來源檔加前綴。
SHEET_METRICS = {
    "流通卡數": ("cards", "張"),
    "有效卡數": ("active_cards", "張"),
    "當月簽帳金額": ("spend", "千元"),
    "循環信用餘額": ("revolving", "千元"),
    "未到期分期付款餘額": ("installment", "千元"),
    "當月轉銷呆帳金額": ("writeoff", "千元"),
}

# 月報 32 家發卡機構全部收錄。未收錄者會退回名稱雜湊，
# tests/test_entity_slugs.py 會擋下來要求補齊。
ENTITY_SLUGS = {
    "總計": "market",
    # 公股與大型行庫
    "臺灣銀行": "bot",
    "臺灣土地銀行": "landbank",
    "合作金庫商業銀行": "tcb",
    "第一商業銀行": "firstbank",
    "華南商業銀行": "hncb",
    "彰化商業銀行": "chb",
    "上海商業儲蓄銀行": "scsb",
    "高雄銀行": "bok",
    "兆豐國際商業銀行": "megabank",
    "臺灣中小企業銀行": "tbb",
    # 民營主要發卡行
    "台北富邦商業銀行": "fubon",
    "國泰世華商業銀行": "cathay",
    "玉山商業銀行": "esun",
    "台新國際商業銀行": "taishin",
    "中國信託商業銀行": "ctbc",
    "永豐商業銀行": "sinopac",
    "元大商業銀行": "yuanta",
    "凱基商業銀行": "kgi",
    "聯邦商業銀行": "ubot",
    "遠東國際商業銀行": "feib",
    "臺灣新光商業銀行": "skbank",
    "安泰商業銀行": "entie",
    "陽信商業銀行": "sunny",
    "三信商業銀行": "cotabank",
    "台中商業銀行": "taichung",
    "華泰商業銀行": "hwatai",
    # 外商
    "花旗(台灣)商業銀行": "citi",
    "渣打國際商業銀行": "sc",
    "滙豐(台灣)商業銀行": "hsbc",
    "星展(台灣)商業銀行": "dbs",
    # 非銀行發卡機構
    "台灣樂天信用卡股份有限公司": "rakuten",
    "台灣美國運通國際(股)公司": "amex",
}


def load_definitions() -> dict:
    return json.loads(DEFS_PATH.read_text(encoding="utf-8"))


def metric_slug(sheet_name: str) -> tuple[str, str]:
    """由工作表名稱推出指標 slug。P.5預期修正_流通卡數 → ('cards', '張')"""
    for suffix, (slug, unit) in SHEET_METRICS.items():
        if sheet_name.endswith(suffix):
            return slug, unit
    return "unknown", ""


def entity_slug(name: str) -> str:
    """機構名稱 → slug。刻意不吃列序，否則 key 會隨來源檔案改變。"""
    slug = ENTITY_SLUGS.get(name)
    if slug:
        return slug
    digest = hashlib.sha1(name.encode("utf-8")).hexdigest()[:6]
    return f"bank_{digest}"


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
            es = "market_total" if name == "market_total" else entity_slug(name)
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

        # 合計列。key 名稱對齊規格書 §5.2 的範例。
        for p, v in sd.total.items():
            store.add(Metric(
                key=f"market_total_{slug}_{p}", value=v, unit=unit, label=f"全市場{slug} {p}",
                source=MetricSource(sheet=sd.sheet_name, range=f"期間 {p} 之合計列",
                                    formula="來源檔合計列直接取值"),
            ))

        shares: Dict[str, float] = {}
        for i, (name, vals) in enumerate(sd.rows.items(), 1):
            es = entity_slug(name)
            for p, v in vals.items():
                store.add(Metric(
                    key=f"{es}_{slug}_{p}", value=v, unit=unit, label=f"{name} {slug} {p}",
                    source=MetricSource(sheet=sd.sheet_name, range=f"{name} × 期間 {p}",
                                        formula="來源檔直接取值"),
                ))

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

        # 排名：依市佔率降序。合計列已在 reader 排除。
        for rank, (es, _) in enumerate(
            sorted(shares.items(), key=lambda kv: kv[1], reverse=True), 1
        ):
            store.add(Metric(
                key=f"{es}_{slug}_rank", value=float(rank), unit="名",
                label=f"{es} {slug}排名",
                source=MetricSource(sheet=sd.sheet_name, range="全機構市佔率排序",
                                    formula=defs["ranking"]["definition"]),
            ))

        # FR-1.5 的開關：基期存在才算，不存在標記 computable=false
        _add_yoy(store, sd, slug, defs)

    return store
