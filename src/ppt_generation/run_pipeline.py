"""
端到端管線 CLI
===============
把完整流程串起來跑一次，供實測與 Demo 使用。

三種執行模式：

1. **連線檢查**：只確認 LLM 設定與金鑰能通
   ``python -m ppt_generation.run_pipeline --check-llm``

2. **內建範例資料**：不需要 backend 輸出，直接用內建的信用卡市場範例跑完整流程
   ``python -m ppt_generation.run_pipeline --sample --prompt "..."``

3. **直接讀 Excel**：由 backend ingestion 現場解析，支援兩種版型

   單檔多表（一個 .xlsx，每張工作表一個指標）::

       python -m ppt_generation.run_pipeline \
           --excel fixtures/data/fsc_114_workbook.xlsx --prompt "..."

   多檔單表（一個目錄，每個 .xlsx 一個指標）::

       python -m ppt_generation.run_pipeline \
           --excel fixtures/data/fsc_114 --prompt "..."

   兩種版型走同一條路徑，版型判斷在 :mod:`data.backend_bridge` 裡完成，
   下游拿到的 payload 形狀一致。ingestion 結果會落檔成
   ``<output-dir>/stages/00_ingestion.json``，之後可用 ``--ingestion``
   重跑同一份資料而不必再解析一次 Excel。

4. **既有 backend JSON**
   ``python -m ppt_generation.run_pipeline --ingestion outputs/ingestion.json --prompt "..."``

加上 ``--fake-llm`` 可完全不呼叫 LLM（用內建假回應），用來驗證非 LLM 的部分。

分階段驗證
----------
``--stage X`` 會跑到階段 X 為止就停，並把每個已完成階段的輸出寫成
``<output-dir>/stages/NN_<stage>.json``，方便逐段檢查中間結果：

``python -m ppt_generation.run_pipeline --list-stages``
    列出所有階段與其是否需要 LLM。

``python -m ppt_generation.run_pipeline --sample --stage metrics``
    只跑資料讀取與指標計算（完全不呼叫 LLM），檢查 MetricStore 是否正確。

``python -m ppt_generation.run_pipeline --sample --stage charts``
    跑到圖表決策為止，檢查 LLM 選的圖表類型與查表後的數值。
"""

from __future__ import annotations

import argparse
import json
import logging
import sys
from pathlib import Path
from typing import Any, Sequence

from .agents import chart_agent, narrative_writer, reviewer, section_planner
from .charts import chart_planner
from .core import config, llm_client
from .data import backend_bridge, dataset_loader, metric_engine
from .output import excel_exporter, renderer
from .verification import verify_chart_consistency as vcc


logger = logging.getLogger(__name__)


# ---------------------------------------------------------------------------
# 階段定義（--stage）
# ---------------------------------------------------------------------------
#: 管線階段順序。``--stage X`` 表示「跑到 X 為止就停」，預設跑完 verify。
STAGE_SEQUENCE: tuple[str, ...] = (
    "metrics",
    "sections",
    "charts",
    "narratives",
    "review",
    "render",
    "verify",
)

#: 封面標題預設值。用使用者的 prompt 原句當標題會在封面上出現
#: 「幫我做一份…」這種指令句；標題該是簡報的名字，不是下單的話。
DEFAULT_DECK_TITLE = "信用卡市場分析與經營洞察"

STAGE_LABELS: dict[str, str] = {
    "metrics": "Stage 1-3 資料讀取與指標計算",
    "sections": "Stage 4-1 章節規劃",
    "charts": "Stage 4-2 圖表決策",
    "narratives": "Stage 4-3 敘事撰寫",
    "review": "Stage 4-4 審查",
    "render": "Stage 5-6 產出檔案",
    "verify": "Stage 7 三方數值比對",
}

#: 各階段是否需要呼叫 LLM，供 ``--list-stages`` 顯示。
STAGE_NEEDS_LLM = {
    "metrics": False,
    "sections": True,
    "charts": True,
    "narratives": True,
    "review": True,
    "render": False,
    "verify": False,
}


class StageDump:
    """
    把每個階段的輸出寫成 JSON，讓中間結果可以被單獨檢查。

    檔名帶序號（``01_metrics.json``…），排序即為管線順序。
    這些檔案是除錯與人工驗證用，不是系統契約的一部分——
    模組之間仍然只透過記憶體中的 dataclass 傳遞。
    """

    def __init__(self, directory: Path | None) -> None:
        self.directory = directory

        if directory is not None:
            directory.mkdir(parents=True, exist_ok=True)

    def write(self, stage: str, payload: Any) -> Path | None:
        if self.directory is None:
            return None

        index = STAGE_SEQUENCE.index(stage) + 1
        target = self.directory / f"{index:02d}_{stage}.json"
        target.write_text(
            json.dumps(payload, ensure_ascii=False, indent=2, default=str),
            encoding="utf-8",
        )
        return target


def _resolved_chart_payload(chart: Any) -> dict[str, Any]:
    """序列化 ResolvedChart。數值來自 MetricStore 查表結果，非 LLM 產出。"""
    spec = chart.spec

    if chart.is_scatter:
        spec_payload: dict[str, Any] = {
            "kind": "scatter",
            "title": spec.title,
            "series_name": spec.series_name,
            "points": [list(point) for point in spec.points],
            "labels": list(spec.labels) if spec.labels else None,
        }
    else:
        spec_payload = {
            "kind": "category",
            "title": spec.title,
            "chart_type": str(spec.chart_type),
            "categories": list(spec.categories),
            "series": {
                name: list(values) for name, values in spec.series.items()
            },
        }

    return {
        "plan": chart.plan.to_dict(),
        "skill_name": chart.skill_name,
        "series_names": list(chart.series_names),
        "metric_key": chart.metric.metric_key,
        "spec": spec_payload,
    }


def _comparison_payload(comparison: Any) -> dict[str, Any]:
    return {
        "slide_number": comparison.slide_number,
        "chart_title": comparison.chart_title,
        "series_name": comparison.series_name,
        "chart_cache": comparison.chart_cache,
        "embedded": comparison.embedded,
        "external": comparison.external,
        "passed": comparison.passed,
        "failure": comparison.describe_failure() or None,
    }


# ---------------------------------------------------------------------------
# 內建範例資料（模擬 backend ingestion 輸出）
# ---------------------------------------------------------------------------
def _sample_ingestion_payload() -> dict[str, Any]:
    """
    產生一份結構完整的假 backend 輸出，數字取自附件三的信用卡市場情境。

    刻意包含兩種類別軸：月份（時間序列）與銀行（橫斷面），
    這樣可以一次驗證 metric_engine 的軸判斷防呆。
    """
    months = [f"{index}月" for index in range(1, 13)]
    cards_2025 = [
        5865.5, 5874.8, 5899.4, 5938.9, 5979.6, 5986.9,
        5994.7, 6014.9, 6035.4, 6047.9, 6042.5, 6048.6,
    ]
    cards_2026 = [
        6100.2, 6150.9, 6210.1, 6280.4, 6321.7, 6355.2,
        6390.8, 6421.3, 6455.9, 6490.2, 6512.7, 6548.1,
    ]
    banks = ["中信", "國泰", "玉山", "台新", "富邦"]
    bank_cards = [1295.4, 1148.2, 982.7, 891.3, 684.5]

    filename = "附件四_預期修正參照資料.xlsx"

    def cell(value: Any, ref: str | None = None, sheet: str = "P.5_流通卡數"):
        evidence = []

        if ref is not None:
            evidence.append(
                {
                    "evidence_type": "cell",
                    "source_kind": "excel",
                    "filename": filename,
                    "sheet_name": sheet,
                    "cell": ref,
                    "extraction_method": "openpyxl",
                    "confidence": 1.0,
                }
            )

        return {
            "raw_value": value,
            "value": value,
            "confidence": 1.0,
            "evidence": evidence,
        }

    return {
        "filename": filename,
        "pipeline_status": "completed",
        "datasets": [
            {
                "dataset_id": "market_cards",
                "filename": filename,
                "source_kind": "excel",
                "table_kind": "structured_table",
                "metadata": {"title": "市場流通卡數", "unit": "萬張"},
                "confidence": 1.0,
                "requires_human_review": False,
                "review_status": "not_required",
                "columns": [
                    {
                        "key": "month",
                        "label": "月份",
                        "index": 0,
                        "data_type": "string",
                    },
                    {
                        "key": "y2025",
                        "label": "2025年",
                        "index": 1,
                        "data_type": "number",
                        "unit": "萬張",
                    },
                    {
                        "key": "y2026",
                        "label": "2026年",
                        "index": 2,
                        "data_type": "number",
                        "unit": "萬張",
                    },
                ],
                "records": [
                    {
                        "record_index": index,
                        "source_row": index + 3,
                        "values": {
                            "month": cell(month),
                            "y2025": cell(cards_2025[index], f"B{index + 3}"),
                            "y2026": cell(cards_2026[index], f"C{index + 3}"),
                        },
                    }
                    for index, month in enumerate(months)
                ],
            },
            {
                "dataset_id": "bank_cards",
                "filename": filename,
                "source_kind": "excel",
                "table_kind": "structured_table",
                "metadata": {"title": "各銀行流通卡數", "unit": "萬張"},
                "confidence": 1.0,
                "requires_human_review": False,
                "review_status": "not_required",
                "columns": [
                    {
                        "key": "bank",
                        "label": "銀行",
                        "index": 0,
                        "data_type": "string",
                    },
                    {
                        "key": "cards",
                        "label": "流通卡數",
                        "index": 1,
                        "data_type": "number",
                        "unit": "萬張",
                    },
                ],
                "records": [
                    {
                        "record_index": index,
                        "source_row": index + 3,
                        "values": {
                            "bank": cell(bank),
                            "cards": cell(
                                bank_cards[index],
                                f"B{index + 3}",
                                "P.7_各行卡數",
                            ),
                        },
                    }
                    for index, bank in enumerate(banks)
                ],
            },
        ],
    }


# ---------------------------------------------------------------------------
# 假 LLM（--fake-llm）
# ---------------------------------------------------------------------------
def _fake_json_call(prompt: str, schema: dict[str, Any], **_: Any) -> Any:
    """依 prompt 內容判斷這是哪個階段的呼叫，回傳合理的假結果。"""
    properties = schema.get("properties", {})

    if "sections" in properties:
        return {
            "status": section_planner.STATUS_READY,
            "sections": [
                {
                    "title": "市場整體概況",
                    "chapter": "市場整體概況",
                    "intent": "呈現市場規模趨勢",
                    "suggested_metric_keys": ["market_cards.value"],
                },
                {
                    "title": "成長動能檢視",
                    "chapter": "同業成長及競爭分析",
                    "intent": "檢視年增動能變化",
                    "suggested_metric_keys": ["market_cards.yoy"],
                },
                {
                    "title": "業者競爭態勢",
                    "chapter": "同業成長及競爭分析",
                    "intent": "比較各銀行市占",
                    "suggested_metric_keys": ["bank_cards.share"],
                },
            ],
        }

    if "headline" in properties:
        return _fake_narrative(prompt)

    if "tool_name" in properties:
        return _fake_tool_payload(prompt)

    # 審查 Agent
    return {"status": reviewer.STATUS_APPROVED, "issues": []}


# ---------------------------------------------------------------------------
# 資料驅動的假 LLM
# ---------------------------------------------------------------------------
# 上面那組假回應寫死了內建範例的 metric_key（``market_cards.value`` 等），
# 只有 ``--sample`` 能用。餵真實資料時那些 key 不存在於 MetricStore，
# 防呆會正確地把每一頁都擋掉，於是 ``--fake-llm`` 無法用來驗證 render 與
# 三方比對——偏偏那兩段才是最需要在不花 LLM 額度的前提下反覆驗的。
#
# 所以再提供一組「看得懂當前 MetricStore」的假回應：從 prompt 裡認出這一輪
# 允許引用的 metric_key，再依指標語意與軸型挑圖表類型。這仍然不呼叫任何模型，
# 但走的是與真實模型相同的契約與防呆路徑。
def _pick_metric_keys(prompt: str, store: Any) -> list[str]:
    """
    找出 prompt 中出現、且存在於 MetricStore 的 metric_key。

    只做子字串比對會出事：``流通卡數.value`` 是 ``流通卡數.value.top10``
    的前綴，prompt 裡明明只允許 top10 那個，卻會連短的那個一起中，
    敘事就會引用一個本頁不允許的指標而被審查退回（實測 10 頁掉 5 頁）。
    所以命中後要剔除「本身是另一個命中鍵之前綴」的那些。
    """
    matched = [
        key
        for key in store.computable_metric_keys()
        if key in prompt
    ]

    return [
        key
        for key in matched
        if not any(
            other != key and other.startswith(key) for other in matched
        )
    ]


def _fake_chart_for_metric(metric: Any) -> dict[str, Any]:
    """依指標語意與軸型挑一個一定過得了 chart_planner 防呆的圖表。"""
    series_names = metric.series_names

    if metric.semantic == "share":
        # 圓餅圖只能一組系列（取最後一期＝最新狀態），且類別數不能太多——
        # 33 家銀行畫成圓餅沒有人看得懂，chart_planner 會擋。超過上限就改用
        # 橫條圖，這是真實模型收到防呆錯誤後也會被引導去做的選擇。
        if len(metric.categories) > chart_planner.MAX_PIE_CATEGORIES:
            return {
                "tool_name": "bar",
                "chart_title": f"{metric.name}：業者份額排序",
                "series_names": series_names[-1:],
            }

        return {
            "tool_name": "pie",
            "chart_title": f"{metric.name}結構：前段業者掌握主要份額",
            "series_names": series_names[-1:],
        }

    if metric.semantic == "rank":
        return {
            "tool_name": "bar",
            "chart_title": f"{metric.name}：業者排序",
            "series_names": series_names[-1:],
        }

    if metric.axis_kind == metric_engine.AXIS_TEMPORAL:
        return {
            "tool_name": "line",
            "chart_title": f"{metric.name}走勢",
            "series_names": series_names[:2],
        }

    return {
        "tool_name": "column",
        "chart_title": f"{metric.name}：業者間規模差異明顯",
        "series_names": series_names[-1:],
    }


#: 假 LLM 的章節骨架：(章節, 頁標題模板, 要挑什麼指標, 用什麼圖)。
#: 對齊 FR-2.6 的八章節與附件三的頁面型態。順序即簡報順序。
_FAKE_DECK_BLUEPRINT: tuple[tuple[str, str, str, str], ...] = (
    ("Executive Summary", "關鍵指標總覽", "top_value", "table"),
    ("市場整體概況", "市場規模趨勢", "timeline", "combo"),
    ("市場整體概況", "市場動能檢視", "timeline_growth", "line"),
    ("同業成長及競爭分析", "業者規模排序", "top_value", "bar"),
    ("同業成長及競爭分析", "市占結構", "top_share", "pie"),
    ("客戶活躍度", "有效卡數表現", "top_value_2", "column"),
    ("獲利能力", "循環信用與分期貢獻", "top_value_3", "column"),
    ("風險與警訊", "轉銷呆帳分佈", "top_value_last", "heatmap"),
    ("未來趨勢推測", "市場規模外推", "forecast", "line"),
    ("對台新的策略建議", "市占提升空間", "share", "bar"),
)


def _select_fake_metric(store: Any, kind: str) -> str | None:
    """依 blueprint 的 kind 從當前 MetricStore 挑一個指標鍵。"""
    keys = store.computable_metric_keys()

    def ends(suffix: str) -> list[str]:
        return [key for key in keys if key.endswith(suffix)]

    top_values = [key for key in ends(".top10") if ".value." in key]
    top_shares = [key for key in ends(".top10") if ".share." in key]

    table = {
        "top_value": top_values[:1],
        "top_value_2": top_values[1:2],
        "top_value_3": top_values[2:3],
        "top_value_last": top_values[-1:],
        "top_share": top_shares[:1],
        "share": [key for key in ends(".share") if ".top" not in key][:1],
        "timeline": [key for key in ends("market_by_period.value")],
        "timeline_growth": [key for key in ends("market_by_period.period_growth")],
        "forecast": [key for key in ends(".forecast")],
    }

    candidates = table.get(kind) or []

    return candidates[0] if candidates else None


def _fake_deck_pages(store: Any) -> list[dict[str, Any]]:
    """
    依 blueprint 排出這份資料能產出的內容頁。

    挑不到指標的頁面直接不排——寧可少一頁，也不要為了湊頁數硬塞一個
    語意不合的指標。這也讓「資料不足時簡報會少哪幾頁」變得可預期。
    """
    pages: list[dict[str, Any]] = []
    used: set[str] = set()

    for chapter, title, kind, skill in _FAKE_DECK_BLUEPRINT:
        key = _select_fake_metric(store, kind)

        if key is None or key in used:
            continue

        used.add(key)
        pages.append(
            {
                "chapter": chapter,
                "title": title,
                "metric_key": key,
                "skill": skill,
            }
        )

    if not pages:
        # 完全對不上 blueprint 的資料（例如非交叉表），退回「有什麼畫什麼」。
        for key in store.computable_metric_keys()[:3]:
            pages.append(
                {
                    "chapter": "資料概況",
                    "title": store.get(key).name,
                    "metric_key": key,
                    "skill": None,
                }
            )

    return pages


def _fake_chart_arguments(
    store: Any,
    metric_key: str,
    skill: str | None,
) -> dict[str, Any]:
    """
    把 blueprint 指定的圖型轉成工具參數，並依指標形狀挑系列。

    blueprint 指定的圖型若與指標形狀不合（例如對時間軸要求 pie），
    這裡不硬幹——退回 :func:`_fake_chart_for_metric` 的自動選擇，
    讓 chart_planner 的防呆仍然是最後一道關卡而不是被繞過。
    """
    metric = store.get(metric_key)
    auto = _fake_chart_for_metric(metric)

    if skill is None:
        return {
            "tool_name": auto["tool_name"],
            "metric_key": metric_key,
            "chart_title": auto["chart_title"],
            "series_names": auto["series_names"],
        }

    names = metric.series_names
    is_temporal = metric.axis_kind == metric_engine.AXIS_TEMPORAL

    if skill == "combo" and len(names) >= 2:
        # 附件三 P.5 的形狀：規模走長條、金額走折線掛次軸。
        series_names = [names[0], names[2] if len(names) > 2 else names[1]]
        title = f"{metric.name}：規模與金額走勢並列"
    elif skill == "line" and is_temporal:
        series_names = names[:2]
        title = f"{metric.name}走勢"
    elif skill == "pie" and not is_temporal:
        series_names = names[-1:]
        title = f"{metric.name}：前段業者掌握主要份額"
    elif skill in {"table", "heatmap"}:
        # 表格欄位太多會擠成一片，取最後 6 期就夠看出強弱分佈。
        series_names = names[-6:]
        title = f"{metric.name}明細"
    elif skill in {"bar", "column"} and not is_temporal:
        series_names = names[-1:]
        title = f"{metric.name}：業者間差異明顯"
    else:
        return {
            "tool_name": auto["tool_name"],
            "metric_key": metric_key,
            "chart_title": auto["chart_title"],
            "series_names": auto["series_names"],
        }

    return {
        "tool_name": skill,
        "metric_key": metric_key,
        "chart_title": title,
        "series_names": series_names,
    }


def _make_store_aware_fakes(store: Any):
    """
    產生一組認得當前 MetricStore 的假 LLM 呼叫。

    Returns:
        ``(json_call, tool_call)``，簽名與 :mod:`core.llm_client` 相同，
        可直接餵給各 Agent 的 ``llm_call`` 參數。
    """
    keys = store.computable_metric_keys()
    pages = _fake_deck_pages(store)

    #: 頁標題 → 該頁的圖表參數。chart_agent 的 prompt 會帶入頁標題，
    #: 所以靠標題就能認出「現在在規劃哪一頁」。
    by_title = {page["title"]: page for page in pages}

    def _page_for(prompt: str) -> dict[str, Any] | None:
        for title, page in by_title.items():
            if title in prompt:
                return page

        return None

    def json_call(prompt: str, schema: dict[str, Any], **_: Any) -> Any:
        properties = schema.get("properties", {})

        if "sections" in properties:
            return {
                "status": section_planner.STATUS_READY,
                "sections": [
                    {
                        "title": page["title"],
                        "chapter": page["chapter"],
                        "intent": (
                            f"呈現 {store.get(page['metric_key']).name}"
                        ),
                        "suggested_metric_keys": [page["metric_key"]],
                    }
                    for page in pages
                ],
            }

        if "headline" in properties:
            found = _pick_metric_keys(prompt, store)

            if not found:
                return {
                    "headline": "資料觀察",
                    "bullets": ["本頁無可引用指標"],
                }

            key = found[0]
            metric = store.get(key)
            series = metric.series_names[-1]

            # 數字一律走佔位符，由 renderer 代入——與真實模型受同一條規則約束。
            return {
                "headline": f"{metric.name}呈現明顯的業者集中態勢",
                "bullets": [
                    f"領先者 {{{{{key}|{series}|max_category}}}} 達 "
                    f"{{{{{key}|{series}|max}}}}",
                    f"末位者僅 {{{{{key}|{series}|min}}}}",
                ],
            }

        if "tool_name" in properties:
            payload = _resolve_fake_chart(prompt)

            return {
                "tool_name": payload.pop("tool_name"),
                "arguments": payload,
            }

        return {"status": reviewer.STATUS_APPROVED, "issues": []}

    def _resolve_fake_chart(prompt: str) -> dict[str, Any]:
        page = _page_for(prompt)

        if page is not None:
            return _fake_chart_arguments(
                store, page["metric_key"], page["skill"]
            )

        found = _pick_metric_keys(prompt, store)
        key = found[0] if found else keys[0]

        return _fake_chart_arguments(store, key, None)

    def tool_call(
        prompt: str,
        tool_schemas: Sequence[dict[str, Any]],
        **_: Any,
    ) -> llm_client.ToolCall:
        payload = _resolve_fake_chart(prompt)
        name = payload.pop("tool_name")

        return llm_client.ToolCall(name, payload)

    return json_call, tool_call


def _fake_narrative(prompt: str) -> dict[str, Any]:
    if "market_cards.yoy" in prompt:
        return {
            "headline": "年增動能逐季轉強，成長並未趨緩",
            "bullets": [
                "年增率由 {{market_cards.yoy|2026 vs 2025|first}} 提升至 "
                "{{market_cards.yoy|2026 vs 2025|latest}}",
                "全年平均年增 {{market_cards.yoy|2026 vs 2025|avg}}",
            ],
        }

    if "bank_cards.share" in prompt:
        return {
            "headline": "市場集中度偏高，龍頭優勢穩固",
            "bullets": [
                "龍頭 {{bank_cards.share|流通卡數|max_category}} 市占達 "
                "{{bank_cards.share|流通卡數|max}}",
                "末位業者市占僅 {{bank_cards.share|流通卡數|min}}",
            ],
        }

    return {
        "headline": "市場規模穩健擴張，全年未見動能衰退",
        "bullets": [
            "流通卡數自年初 {{market_cards.value|2026年|first}} 增至 "
            "{{market_cards.value|2026年|latest}}",
            "全年高點 {{market_cards.value|2026年|max}} 出現於 "
            "{{market_cards.value|2026年|max_category}}",
        ],
    }


def _fake_tool_payload(prompt: str) -> dict[str, Any]:
    if "成長動能" in prompt:
        return {
            "tool_name": "column",
            "arguments": {
                "metric_key": "market_cards.yoy",
                "chart_title": "年增動能逐季走高，反映滲透率持續提升",
            },
        }

    if "競爭態勢" in prompt:
        return {
            "tool_name": "pie",
            "arguments": {
                "metric_key": "bank_cards.share",
                "chart_title": "前三大業者掌握逾六成市占",
                "series_names": ["流通卡數"],
            },
        }

    return {
        "tool_name": "line",
        "arguments": {
            "metric_key": "market_cards.value",
            "chart_title": "市場規模穩健擴張，動能未見趨緩",
            "series_names": ["2026年"],
        },
    }


def _fake_tool_call(
    prompt: str,
    tool_schemas: Sequence[dict[str, Any]],
    **_: Any,
) -> llm_client.ToolCall:
    payload = _fake_tool_payload(prompt)
    return llm_client.ToolCall(payload["tool_name"], payload["arguments"])


# ---------------------------------------------------------------------------
# 連線檢查
# ---------------------------------------------------------------------------
def list_stages() -> int:
    """列出所有階段，讓人一眼看出可以停在哪、哪些階段要花 LLM 呼叫。"""
    print("=" * 68)
    print("管線階段（--stage X 表示跑到 X 為止就停）")
    print("=" * 68)

    for index, stage in enumerate(STAGE_SEQUENCE, start=1):
        llm_mark = "需要 LLM" if STAGE_NEEDS_LLM[stage] else "純確定性"
        print(f"  {index}. {stage:11s} {STAGE_LABELS[stage]:24s} [{llm_mark}]")

    print(
        "\n每個階段的輸出會寫成 <output-dir>/stages/NN_<stage>.json，"
        "可用 --no-stage-dump 關閉。"
    )
    return 0


def check_llm() -> int:
    """驗證 LLM 設定與金鑰是否可用，回傳 exit code。"""
    settings = config.load_llm_settings()

    print("=" * 68)
    print("LLM 設定檢查")
    print("=" * 68)
    print(f"  provider     : {settings.provider}")
    print(f"  base_url     : {settings.base_url or '(SDK 預設)'}")
    print(f"  model        : {settings.model_for('default')}")
    print(f"  api_key      : {'已載入' if settings.api_key else '未設定'}")
    print(f"  tool_mode    : {settings.tool_mode}")
    print(f"  json_mode    : {settings.json_mode}")
    print(f"  system_mode  : {settings.system_mode}")

    if settings.provider != "bedrock" and not settings.api_key:
        print(
            f"\n找不到金鑰。請設定 LLM_API_KEY 環境變數，"
            f"或把金鑰寫入 {config.DEFAULT_API_KEY_FILE}"
        )
        return 1

    print("\n實際呼叫一次（要求回傳固定 JSON）...")

    schema = {
        "type": "object",
        "properties": {"ok": {"type": "boolean"}, "model_said": {"type": "string"}},
        "required": ["ok"],
    }

    try:
        result = llm_client.complete_json(
            '請回傳 {"ok": true, "model_said": "hello"}',
            schema,
            system_prompt="你是一個測試用的 JSON 產生器。",
        )
    except Exception as error:  # noqa: BLE001 - CLI 要顯示所有失敗原因
        print(f"呼叫失敗：{type(error).__name__}: {error}")
        return 1

    print(f"回應：{result}")
    print("\nLLM 連線正常")
    return 0


# ---------------------------------------------------------------------------
# 主流程
# ---------------------------------------------------------------------------
def run(
    *,
    ingestion_path: str | Path | None,
    user_prompt: str,
    sections: Sequence[str] | None,
    output_dir: Path,
    use_fake_llm: bool,
    skip_semantic_review: bool,
    stop_after: str = STAGE_SEQUENCE[-1],
    dump_dir: Path | None = None,
    excel_path: str | Path | None = None,
    excel_sheet: str | None = None,
    deck_title: str | None = None,
) -> int:
    """
    跑管線。``stop_after`` 指定跑到哪個階段為止（見 :data:`STAGE_SEQUENCE`）。

    Returns:
        0 成功（或已跑到指定階段），1 產出／驗證失敗，2 章節規劃需使用者確認。
    """
    if stop_after not in STAGE_SEQUENCE:
        raise ValueError(
            f"未知階段 {stop_after!r}，可用：{', '.join(STAGE_SEQUENCE)}"
        )

    # 內建範例用寫死的假回應（它同時也在驗那組固定期望值）；
    # 真實資料的假回應要看得懂當前 MetricStore，等 store 建好後再產生。
    uses_sample = excel_path is None and ingestion_path is None
    json_call = _fake_json_call if (use_fake_llm and uses_sample) else None
    tool_call = _fake_tool_call if (use_fake_llm and uses_sample) else None
    dump = StageDump(dump_dir)
    stop_index = STAGE_SEQUENCE.index(stop_after)

    def finish(stage: str, payload: Any) -> bool:
        """寫出階段 JSON，回傳是否應在此停止。"""
        target = dump.write(stage, payload)

        if target is not None:
            print(f"  [階段輸出] {target}")

        if STAGE_SEQUENCE.index(stage) < stop_index:
            return False

        print(f"\n已跑到 --stage {stage}（{STAGE_LABELS[stage]}）後停止。")
        return True

    # ---- Stage 1-3 ----
    print("=" * 68)
    print("Stage 1-3：資料讀取與指標計算")
    print("=" * 68)

    data_source: str

    if excel_path is not None:
        # 直接讀 Excel：由 backend ingestion 產生 payload。
        # 單一檔案（多工作表）與目錄（多個單表檔）兩種版型都走這條，
        # 版型判斷在 backend_bridge 裡，這裡不分支。
        payload = backend_bridge.ingest_excel(
            excel_path,
            sheet_name=excel_sheet,
        )

        source_files = payload.get("source_files") or []
        data_source = str(excel_path)

        print(f"  資料來源：{excel_path}（經 backend ingestion）")
        print(
            f"  輸入檔案：{len(source_files)} 個"
            f"（{'、'.join(source_files[:6])}"
            f"{' …' if len(source_files) > 6 else ''}）"
        )

        for message in payload.get("warnings") or []:
            print(f"    註：{message}")

        # payload 落檔，之後可用 --ingestion 重跑同一份資料，
        # 也是「數字可追溯」的稽核起點。
        if dump_dir is not None:
            saved = backend_bridge.save_payload(
                payload,
                Path(dump_dir) / "00_ingestion.json",
            )
            print(f"  [階段輸出] {saved}")

    elif ingestion_path is None:
        payload = _sample_ingestion_payload()
        data_source = "built-in sample"
        print("  資料來源：內建範例（--sample）")
    else:
        payload = json.loads(Path(ingestion_path).read_text(encoding="utf-8"))
        data_source = str(ingestion_path)
        print(f"  資料來源：{ingestion_path}")

    loaded = dataset_loader.load_ingestion_result(payload)
    store, engine_report = metric_engine.build_metric_store(loaded)

    if use_fake_llm and not uses_sample:
        json_call, tool_call = _make_store_aware_fakes(store)

    print(f"  資料集：{len(loaded.datasets)} 個")
    print(f"  可用指標：{engine_report.metric_count} 個")

    for key in store.computable_metric_keys():
        metric = store.get(key)
        print(f"    - {key}（{metric.name}／{metric.axis_kind}）")

    if engine_report.blocked:
        print(f"  防呆擋下 {len(engine_report.blocked)} 個指標：")

        for key, reasons in engine_report.blocked.items():
            print(f"    - {key}：{'；'.join(reasons)}")

    for note in engine_report.notes:
        print(f"  註：{note}")

    if finish(
        "metrics",
        {
            "source": data_source,
            "dataset_ids": [dataset.dataset_id for dataset in loaded.datasets],
            "engine_report": {
                "metric_count": engine_report.metric_count,
                "blocked": engine_report.blocked,
                "notes": engine_report.notes,
            },
            "computable_metric_keys": store.computable_metric_keys(),
            "catalog_for_llm": store.catalog_for_llm(),
            "metric_store": store.to_dict(),
        },
    ):
        return 0

    # ---- Stage 4-1 章節規劃 ----
    print("\n" + "=" * 68)
    print("Stage 4-1：章節規劃")
    print("=" * 68)

    plan = section_planner.plan_sections(
        user_prompt,
        store,
        existing_sections=sections,
        llm_call=json_call,
    )

    print(f"  狀態：{plan.status}")

    if plan.needs_confirmation:
        print(f"\n需要你先確認：{plan.question_to_user}")
        print(
            "\n請用 --sections '章節1' '章節2' ... 指定章節後重跑，"
            "或把需求描述寫得更具體。"
        )
        return 2

    # 把頁碼換成「在最終 .pptx 中的實際投影片序號」。必須在圖表決策之前，
    # 因為稽核 Excel 的工作表名 P.{頁碼}_{指標} 是從 ChartPlan 帶下去的
    # （FR-3.1），頁碼錯了主管照著 Excel 就會翻錯頁。
    renderer.assign_page_numbers(plan.sections)

    current_chapter: str | None = None

    for section in plan.sections:
        if section.chapter and section.chapter != current_chapter:
            print(f"    ── 章節：{section.chapter}")
            current_chapter = section.chapter

        print(
            f"    P.{section.page_number} {section.title}"
            f"（指標：{section.suggested_metric_keys or '未指定'}）"
        )

    if plan.dropped_metric_keys:
        print("  已剔除模型給出的無效指標：")

        for key, reason in plan.dropped_metric_keys.items():
            print(f"    - {key}：{reason}")

    if finish("sections", plan.to_dict()):
        return 0

    # ---- Stage 4-2 圖表決策 ----
    print("\n" + "=" * 68)
    print("Stage 4-2：圖表決策")
    print("=" * 68)

    chart_result = chart_agent.plan_charts(
        plan.sections, store, llm_call=tool_call
    )

    for chart in chart_result.charts:
        attempts = chart_result.attempts.get(chart.plan.slide_title, 1)
        print(
            f"    P.{chart.plan.page_number} {chart.skill_name:8s} "
            f"{chart.plan.chart_title}（嘗試 {attempts} 次）"
        )

    for title, errors in chart_result.failures.items():
        print(f"    [失敗] {title}：{errors[0]}")

    charts_payload = {
        "charts": [
            _resolved_chart_payload(chart) for chart in chart_result.charts
        ],
        "failures": chart_result.failures,
        "attempts": chart_result.attempts,
    }

    if not chart_result.charts:
        dump.write("charts", charts_payload)
        print("\n沒有任何圖表產出，無法繼續。")
        return 1

    if finish("charts", charts_payload):
        return 0

    pairs = [
        (section, chart)
        for section in plan.sections
        for chart in chart_result.charts
        if chart.plan.page_number == section.page_number
    ]

    # ---- Stage 4-3 敘事 ----
    print("\n" + "=" * 68)
    print("Stage 4-3：敘事撰寫")
    print("=" * 68)

    narrative_result = narrative_writer.write_narratives(
        pairs, store, llm_call=json_call
    )

    for narrative in narrative_result.narratives:
        attempts = narrative_result.attempts.get(narrative.slide_title, 1)
        print(
            f"    P.{narrative.page_number} {narrative.headline}"
            f"（嘗試 {attempts} 次）"
        )

    for title, issues in narrative_result.failures.items():
        print(f"    [失敗] {title}：{issues[0]}")

    narratives_payload = {
        "narratives": [
            narrative.to_dict() for narrative in narrative_result.narratives
        ],
        "failures": narrative_result.failures,
        "attempts": narrative_result.attempts,
    }

    if not narrative_result.narratives:
        dump.write("narratives", narratives_payload)
        print("\n沒有任何敘事產出，無法繼續。")
        return 1

    if finish("narratives", narratives_payload):
        return 0

    # 只保留同時有圖表與敘事的頁面
    narrative_by_page = {
        narrative.page_number: narrative
        for narrative in narrative_result.narratives
    }

    usable = [
        (section, chart, narrative_by_page[section.page_number])
        for section, chart in pairs
        if section.page_number in narrative_by_page
    ]

    # ---- Stage 4-4 審查 ----
    print("\n" + "=" * 68)
    print("Stage 4-4：審查")
    print("=" * 68)

    approved: list[tuple[Any, Any, Any]] = []
    review_payload: list[dict[str, Any]] = []

    for section, chart, narrative in usable:
        review = reviewer.review_page(
            narrative,
            chart,
            store,
            llm_call=json_call,
            enable_semantic_layer=not skip_semantic_review,
        )

        status = "APPROVED" if review.approved else "REJECTED"
        print(f"    P.{section.page_number} {section.title}：{status}")

        if not review.approved:
            print(f"        退回 {review.target_agent}")

            for issue in review.all_issues:
                print(f"        - {issue}")

        review_payload.append(
            {
                "page_number": section.page_number,
                "section_title": section.title,
                **review.to_dict(),
            }
        )

        # 退件的頁面仍然輸出，但已在上方明確標示，方便人工判斷。
        approved.append((section, chart, narrative))

    if finish("review", {"reviews": review_payload}):
        return 0

    # ---- Stage 5-6 產出 ----
    print("\n" + "=" * 68)
    print("Stage 5-6：產出檔案")
    print("=" * 68)

    output_dir.mkdir(parents=True, exist_ok=True)
    pptx_path = output_dir / "deck.pptx"
    xlsx_path = output_dir / "deck_data.xlsx"

    # 重新指派頁碼。前面那次是在圖表決策之前算的，之後若有頁面因圖表或
    # 敘事失敗而被剔除，頁碼就會留下空號（P.6、P.7、P.9…），稽核 Excel 的
    # 工作表名也會跟著錯位。這裡以最終存留的頁面為準再算一次。
    renderer.assign_page_numbers([section for section, _, _ in approved])

    for section, chart, narrative in approved:
        chart.plan.page_number = section.page_number
        narrative.page_number = section.page_number

    bundles = [
        renderer.PageBundle(section, chart, narrative)
        for section, chart, narrative in approved
    ]

    render_report = renderer.render_deck(
        bundles,
        store,
        output_path=pptx_path,
        deck_title=deck_title or DEFAULT_DECK_TITLE,
    )

    print(
        f"  {render_report.output_path}"
        f"（共 {render_report.slide_count} 張投影片："
        f"封面 1 + 目錄 {1 if render_report.chapters else 0}"
        f" + 章節頁 {render_report.divider_count}"
        f" + 內容頁 {render_report.page_count}"
        f" + 結尾 1，圖表 {render_report.chart_count} 張）"
    )

    if render_report.chapters:
        print(f"  章節：{' / '.join(render_report.chapters)}")

    for warning in render_report.warnings:
        print(f"    註：{warning}")

    for page, errors in render_report.placeholder_errors.items():
        print(f"    [P.{page} 佔位符未代入] {errors}")

    export_report = excel_exporter.export_audit_workbook(
        [chart for _, chart, _ in approved], output_path=xlsx_path
    )

    print(f"  {export_report.output_path}（{len(export_report.sheet_names)} 工作表）")

    if finish(
        "render",
        {
            "pptx": str(render_report.output_path),
            "xlsx": str(export_report.output_path),
            "page_count": render_report.page_count,
            "slide_count": render_report.slide_count,
            "divider_count": render_report.divider_count,
            "chapters": list(render_report.chapters),
            "chart_count": render_report.chart_count,
            "warnings": render_report.warnings,
            "placeholder_errors": {
                str(page): errors
                for page, errors in render_report.placeholder_errors.items()
            },
            "sheet_names": list(export_report.sheet_names),
        },
    ):
        return 0

    # ---- Stage 7 驗證 ----
    print("\n" + "=" * 68)
    print("Stage 7：T1 三方數值比對")
    print("=" * 68)

    report = vcc.verify(pptx_path, xlsx_path)
    vcc.print_report(report)

    dump.write(
        "verify",
        {
            "passed": report.passed,
            "series_checked": len(report.comparisons),
            "external_checked": report.external_checked,
            "warnings": report.warnings,
            "comparisons": [
                _comparison_payload(comparison)
                for comparison in report.comparisons
            ],
        },
    )

    return 0 if report.passed else 1


def main(argv: Sequence[str] | None = None) -> int:
    parser = argparse.ArgumentParser(
        description="簡報生成端到端管線",
        formatter_class=argparse.RawDescriptionHelpFormatter,
        epilog=__doc__,
    )

    parser.add_argument(
        "--check-llm",
        action="store_true",
        help="只檢查 LLM 設定與連線，不跑管線",
    )
    parser.add_argument(
        "--excel",
        default=None,
        help=(
            "Excel 輸入，交給 backend ingestion 讀取。"
            "可以是單一 .xlsx（多工作表，每張表一個指標），"
            "或含多個 .xlsx 的目錄（每檔一個指標）"
        ),
    )
    parser.add_argument(
        "--excel-sheet",
        default=None,
        help="只讀 --excel 指定檔案中的某一張工作表",
    )
    parser.add_argument(
        "--ingestion",
        default=None,
        help="backend ingestion 輸出的 JSON 路徑",
    )
    parser.add_argument(
        "--sample",
        action="store_true",
        help="使用內建範例資料（與 --excel／--ingestion 三選一）",
    )
    parser.add_argument(
        "--prompt",
        default="幫我做一份 2026 信用卡市場分析簡報",
        help="使用者需求描述",
    )
    parser.add_argument(
        "--title",
        default=None,
        help=f"封面標題（預設「{DEFAULT_DECK_TITLE}」）",
    )
    parser.add_argument(
        "--sections",
        nargs="*",
        default=None,
        help="明確指定章節清單，可避免章節規劃階段要求確認",
    )
    parser.add_argument(
        "--output-dir",
        default=str(config.OUTPUT_DIR),
        help="輸出目錄",
    )
    parser.add_argument(
        "--fake-llm",
        action="store_true",
        help="不呼叫真實 LLM，用內建假回應（驗證非 LLM 部分）",
    )
    parser.add_argument(
        "--skip-semantic-review",
        action="store_true",
        help="只跑審查的規則層，省下一次 LLM 呼叫",
    )
    parser.add_argument(
        "--verbose",
        action="store_true",
        help="顯示 LLM 重試等細節",
    )
    parser.add_argument(
        "--stage",
        choices=STAGE_SEQUENCE,
        default=STAGE_SEQUENCE[-1],
        help="跑到指定階段就停下（預設 %(default)s，即完整流程）",
    )
    parser.add_argument(
        "--list-stages",
        action="store_true",
        help="列出所有階段與其是否需要 LLM，不執行任何流程",
    )
    parser.add_argument(
        "--stage-dir",
        default=None,
        help="階段中間結果 JSON 的輸出目錄（預設 <output-dir>/stages）",
    )
    parser.add_argument(
        "--no-stage-dump",
        action="store_true",
        help="不輸出階段中間結果 JSON",
    )

    args = parser.parse_args(argv)

    if args.list_stages:
        return list_stages()

    logging.basicConfig(
        level=logging.INFO if args.verbose else logging.WARNING,
        format="%(levelname)s %(name)s: %(message)s",
    )

    if args.check_llm:
        return check_llm()

    chosen_sources = [
        name
        for name, value in (
            ("--excel", args.excel),
            ("--ingestion", args.ingestion),
            ("--sample", args.sample or None),
        )
        if value
    ]

    if not chosen_sources:
        parser.error(
            "請指定資料來源：--excel <path|dir>、--ingestion <path> 或 --sample"
        )

    if len(chosen_sources) > 1:
        parser.error(
            f"資料來源只能指定一個，目前給了：{'、'.join(chosen_sources)}"
        )

    if args.excel_sheet and args.excel is None:
        parser.error("--excel-sheet 需要搭配 --excel 使用")

    output_dir = Path(args.output_dir)

    if args.no_stage_dump:
        dump_dir = None
    elif args.stage_dir:
        dump_dir = Path(args.stage_dir)
    else:
        dump_dir = output_dir / "stages"

    try:
        return run(
            ingestion_path=args.ingestion,
            user_prompt=args.prompt,
            sections=args.sections,
            output_dir=output_dir,
            use_fake_llm=args.fake_llm,
            skip_semantic_review=args.skip_semantic_review,
            stop_after=args.stage,
            dump_dir=dump_dir,
            excel_path=args.excel,
            excel_sheet=args.excel_sheet,
            deck_title=args.title,
        )
    except (
        backend_bridge.BackendUnavailableError,
        backend_bridge.NoExcelInputError,
        dataset_loader.IngestionPayloadError,
        ValueError,
    ) as error:
        # 輸入問題是使用者可以自己修的，印一行說明就好；
        # traceback 對「檔名打錯」這種狀況沒有幫助，只會蓋掉訊息本身。
        # --verbose 時仍完整拋出，方便查是不是程式的問題。
        if args.verbose:
            raise

        print(f"\n{type(error).__name__}: {error}")
        return 1


if __name__ == "__main__":
    sys.exit(main())
