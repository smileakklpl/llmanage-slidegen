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
import hashlib
import json
import logging
import sys
import time
from dataclasses import dataclass
from pathlib import Path
from typing import Any, Callable, Sequence

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

#: Reviewer 退回 narrative_writer 後，單頁最多額外重寫次數。
#: 初始 writer 本身已有 MAX_NARRATIVE_ATTEMPTS 次確定性自我修正；這裡再設
#: 硬上限，避免 semantic reviewer 不穩定時形成無限 Bedrock 呼叫。
MAX_REVIEW_REPAIR_ATTEMPTS = 2

DELIVERY_APPROVED = "APPROVED"
DELIVERY_APPROVED_WITH_WARNING = "APPROVED_WITH_WARNING"
CANDIDATE_LLM = "writer"
CANDIDATE_FALLBACK_MODEL = "writer_fallback"
CANDIDATE_DETERMINISTIC = "deterministic_fallback"

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


def _sha256_text(text: str) -> str:
    """Return the lowercase SHA-256 digest of UTF-8 text."""
    return hashlib.sha256(text.encode("utf-8")).hexdigest()


def _hash_input(path: Path | None, payload: dict[str, Any]) -> tuple[str, str, int]:
    """Hash the exact source input; directories include relative names and bytes."""
    digest = hashlib.sha256()

    if path is None:
        canonical = json.dumps(
            payload,
            ensure_ascii=False,
            sort_keys=True,
            separators=(",", ":"),
            default=str,
        )
        digest.update(canonical.encode("utf-8"))
        return digest.hexdigest(), "sample", 0

    resolved = path.resolve()

    if resolved.is_file():
        with resolved.open("rb") as stream:
            for chunk in iter(lambda: stream.read(1024 * 1024), b""):
                digest.update(chunk)

        return digest.hexdigest(), "file", 1

    if not resolved.is_dir():
        raise ValueError(f"無法計算輸入雜湊，路徑不存在：{resolved}")

    files = sorted(item for item in resolved.rglob("*") if item.is_file())

    for item in files:
        relative = item.relative_to(resolved).as_posix()
        digest.update(relative.encode("utf-8"))
        digest.update(b"\0")

        with item.open("rb") as stream:
            for chunk in iter(lambda: stream.read(1024 * 1024), b""):
                digest.update(chunk)

        digest.update(b"\0")

    return digest.hexdigest(), "directory", len(files)


def _write_generation_manifest(
    *,
    output_dir: Path,
    input_path: Path | None,
    data_source: str,
    payload: dict[str, Any],
    user_prompt: str,
    sections: Sequence[str] | None,
    deck_title: str | None,
    use_fake_llm: bool,
    skip_semantic_review: bool,
    generation_policy: str,
    deadline_seconds: float,
    render_reserve_seconds: float,
) -> Path:
    """Persist model routing and content hashes for post-run audit."""
    input_sha256, input_kind, input_file_count = _hash_input(input_path, payload)

    if use_fake_llm:
        llm_payload: dict[str, Any] = {
            "mode": "fake",
            "provider": "fake",
            "aws_region": None,
            "configured_stage_models": {
                "sections": "fake",
                "charts": "fake",
                "narratives": "fake",
                "narratives_fallback": "fake",
                "review": "fake" if not skip_semantic_review else None,
            },
        }
    else:
        try:
            settings = config.load_llm_settings()
        except Exception as error:  # noqa: BLE001 - required mode must deliver
            if generation_policy != "required":
                raise
            llm_payload = {
                "mode": "configuration_unavailable",
                "provider": None,
                "aws_region": None,
                "configured_stage_models": {},
                "configuration_error_type": type(error).__name__,
            }
        else:
            llm_payload = {
                "mode": "live",
                "provider": settings.provider,
                "aws_region": (
                    settings.aws_region
                    if settings.provider == "bedrock"
                    else None
                ),
                "configured_stage_models": {
                    "sections": settings.model_for("intent"),
                    "charts": settings.model_for("chart"),
                    "narratives": settings.model_for("writer"),
                    "narratives_fallback": settings.model_for(
                        "writer_fallback"
                    ),
                    "review": (
                        settings.model_for("reviewer")
                        if not skip_semantic_review
                        else None
                    ),
                },
                "capabilities": {
                    "tool_mode": settings.tool_mode,
                    "json_mode": settings.json_mode,
                    "system_mode": settings.system_mode,
                },
            }

    llm_payload["semantic_review_enabled"] = not skip_semantic_review
    llm_payload["calls_by_stage"] = {
        "sections": 0,
        "charts": 0,
        "narratives": 0,
        "review": 0,
    }

    manifest = {
        "contract_version": "1.0",
        "input": {
            "source": data_source,
            "kind": input_kind,
            "file_count": input_file_count,
            "sha256": input_sha256,
        },
        "request": {
            "user_prompt_sha256": _sha256_text(user_prompt),
            "sections": list(sections) if sections is not None else None,
            "deck_title": deck_title or DEFAULT_DECK_TITLE,
            "generation_policy": generation_policy,
            "deadline_seconds": deadline_seconds,
            "render_reserve_seconds": render_reserve_seconds,
        },
        "configured_system_prompt_sha256": {
            "sections": _sha256_text(section_planner.SYSTEM_PROMPT),
            "charts": _sha256_text(chart_agent.SYSTEM_PROMPT),
            "narratives": _sha256_text(narrative_writer.SYSTEM_PROMPT),
            "review": (
                _sha256_text(reviewer.SYSTEM_PROMPT)
                if not skip_semantic_review
                else None
            ),
        },
        "llm": llm_payload,
    }

    output_dir.mkdir(parents=True, exist_ok=True)
    target = output_dir / "generation_manifest.json"
    target.write_text(
        json.dumps(manifest, ensure_ascii=False, indent=2),
        encoding="utf-8",
    )
    return target


_LLM_STAGE_TO_MANIFEST = {
    "intent": "sections",
    "chart": "charts",
    "writer": "narratives",
    "writer_fallback": "narratives",
    "reviewer": "review",
}


def _record_llm_call(output_dir: Path, llm_stage: str) -> None:
    """Increment the actual invocation count for one routed LLM stage."""
    stage = _LLM_STAGE_TO_MANIFEST.get(llm_stage)

    if stage is None:
        return

    target = output_dir / "generation_manifest.json"
    payload = json.loads(target.read_text(encoding="utf-8"))
    calls = payload["llm"]["calls_by_stage"]
    calls[stage] = int(calls.get(stage) or 0) + 1
    target.write_text(
        json.dumps(payload, ensure_ascii=False, indent=2),
        encoding="utf-8",
    )


def _audited_llm_call(
    call: Callable[..., Any],
    output_dir: Path,
) -> Callable[..., Any]:
    """Wrap a live or fake LLM callable and record every actual invocation."""

    def wrapped(*args: Any, **kwargs: Any) -> Any:
        _record_llm_call(output_dir, str(kwargs.get("stage") or "default"))
        return call(*args, **kwargs)

    return wrapped


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
        "series_units": {
            name: chart.metric.unit_for(name) for name in chart.series_names
        },
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
                    "metric_scopes": [
                        {
                            "metric_key": "market_cards.value",
                            "series_names": ["2026年"],
                            "comparison_reason": "",
                        }
                    ],
                },
                {
                    "title": "成長動能檢視",
                    "chapter": "同業成長及競爭分析",
                    "intent": "檢視年增動能變化",
                    "metric_scopes": [
                        {
                            "metric_key": "market_cards.yoy",
                            "series_names": ["2026 vs 2025"],
                            "comparison_reason": "",
                        }
                    ],
                },
                {
                    "title": "業者競爭態勢",
                    "chapter": "同業成長及競爭分析",
                    "intent": "比較各銀行市占",
                    "metric_scopes": [
                        {
                            "metric_key": "bank_cards.share",
                            "series_names": ["流通卡數"],
                            "comparison_reason": "",
                        }
                    ],
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

    def _scope_for(page: dict[str, Any]) -> list[str]:
        arguments = _fake_chart_arguments(
            store, page["metric_key"], page["skill"]
        )
        return list(arguments.get("series_names") or [])

    def _comparison_reason_for(page: dict[str, Any]) -> str:
        scope = _scope_for(page)
        return (
            "依頁面 intent 比較同一主題的多期表現或兩項明確關係"
            if len(scope) > 1
            else ""
        )

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
                            f"回答 {store.get(page['metric_key']).name}"
                            "的分佈是否已形成穩定的競爭格局"
                        ),
                        "metric_scopes": [
                            {
                                "metric_key": page["metric_key"],
                                "series_names": _scope_for(page),
                                "comparison_reason": _comparison_reason_for(page),
                            }
                        ],
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
            page = _page_for(prompt)
            scoped_series = (
                _scope_for(page)
                if page is not None and page["metric_key"] == key
                else []
            )
            series = (scoped_series or metric.series_names)[-1]

            def cite(selector: str) -> str:
                return f"{{{{{key}|{series}|{selector}}}}}"

            # 數字一律走佔位符，由 renderer 代入——與真實模型受同一條規則約束。
            # 條數與字數也刻意寫到 narrative_writer 的下限之上：假 LLM 若寫得比
            # 規則允許的更短，端到端就會在敘事階段掉頁，測不到 render 與比對。
            return {
                "headline": (
                    f"{metric.name}呈現明顯的業者集中態勢，"
                    "規模差距短期內難以收斂"
                ),
                "bullets": [
                    f"領先者 {cite('max_category')} 達 {cite('max')}，"
                    "明顯拉開與其餘業者的距離，反映規模效應仍在累積",
                    f"末位業者僅 {cite('min')}，與領先者之間的落差顯示"
                    "中小型業者在此指標上缺乏可持續的規模優勢",
                    f"全體平均為 {cite('avg')}，位於平均之下的業者"
                    "需重新檢視資源配置是否與市場競爭強度相稱",
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
            "headline": "年增動能逐季轉強，成長並未出現趨緩跡象",
            "bullets": [
                "年增率由年初 {{market_cards.yoy|2026 vs 2025|first}} 提升至 "
                "{{market_cards.yoy|2026 vs 2025|latest}}，動能逐季累積",
                "全年平均年增 {{market_cards.yoy|2026 vs 2025|avg}}，"
                "高於市場對成熟市場的一般預期，滲透率仍有推進空間",
                "高點落在 {{market_cards.yoy|2026 vs 2025|max_category}}，"
                "達 {{market_cards.yoy|2026 vs 2025|max}}，反映季節性促銷效果",
            ],
        }

    if "bank_cards.share" in prompt:
        return {
            "headline": "市場集中度偏高，龍頭業者優勢短期穩固",
            "bullets": [
                "龍頭 {{bank_cards.share|流通卡數|max_category}} 市占達 "
                "{{bank_cards.share|流通卡數|max}}，領先幅度短期難被追上",
                "末位業者市占僅 {{bank_cards.share|流通卡數|min}}，"
                "在缺乏差異化定位的情況下更難累積規模",
                "前段業者合計掌握主要份額，後段業者若要突圍，"
                "需從單卡消費力而非發卡量切入",
            ],
        }

    return {
        "headline": "市場規模穩健擴張，全年未見動能衰退",
        "bullets": [
            "流通卡數自年初 {{market_cards.value|2026年|first}} 增至 "
            "{{market_cards.value|2026年|latest}}，全年維持正成長",
            "全年高點 {{market_cards.value|2026年|max}} 出現於 "
            "{{market_cards.value|2026年|max_category}}，之後未見明顯回落",
            "全年平均為 {{market_cards.value|2026年|avg}}，"
            "顯示規模擴張是全年常態而非單月異常",
        ],
    }


def _fake_tool_payload(prompt: str) -> dict[str, Any]:
    if "成長動能" in prompt:
        return {
            "tool_name": "column",
            "arguments": {
                "metric_key": "market_cards.yoy",
                "chart_title": "年增動能逐季走高，反映滲透率持續提升",
                "series_names": ["2026 vs 2025"],
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
# Required-output narrative orchestration
# ---------------------------------------------------------------------------
@dataclass
class RequiredPageOutcome:
    narrative: narrative_writer.PageNarrative
    review: reviewer.ReviewResult | None
    writer_attempts: int
    reviewer_attempts: int
    delivery_status: str
    candidate_source: str
    warning: str | None = None

    def review_dict(self, section: Any) -> dict[str, Any]:
        if self.review is None:
            payload: dict[str, Any] = {
                "status": "NOT_REVIEWED",
                "rule_issues": [],
                "semantic_issues": [],
                "target_agent": None,
            }
        else:
            payload = self.review.to_dict()

        return {
            "page_number": section.page_number,
            "section_title": section.title,
            **payload,
            "delivery_status": self.delivery_status,
            "hard_rules_passed": True,
            "selected_candidate_hard_issues": [],
            "candidate_source": self.candidate_source,
            "delivery_warning": self.warning,
            "writer_attempts": self.writer_attempts,
            "reviewer_attempts": self.reviewer_attempts,
        }


def _narrative_signature(narrative: narrative_writer.PageNarrative) -> str:
    return _sha256_text(narrative.all_text)


def _generate_required_page(
    section: Any,
    chart: Any,
    store: Any,
    *,
    json_call: Callable[..., Any],
    review_enabled: bool,
    enable_semantic_review: bool,
    page_deadline: float,
    repair_escalate_after: int,
) -> RequiredPageOutcome:
    """Generate one deliverable page while preserving deterministic hard gates."""
    writer_attempts = 0
    reviewer_attempts = 0
    last_errors: list[str] = []
    warning_reasons: list[str] = []
    candidate: narrative_writer.PageNarrative | None = None
    candidate_source = CANDIDATE_LLM

    def has_time() -> bool:
        return time.monotonic() < page_deadline

    for stage, attempts in (
        (CANDIDATE_LLM, narrative_writer.MAX_NARRATIVE_ATTEMPTS),
        (CANDIDATE_FALLBACK_MODEL, 1),
    ):
        if not has_time():
            warning_reasons.append("頁面 LLM 時間配額已用盡")
            break

        try:
            candidate, issues, used = narrative_writer.write_narrative_for_page(
                section,
                chart,
                store,
                llm_call=json_call,
                max_attempts=attempts,
                initial_errors=last_errors,
                llm_stage=stage,
                deadline_monotonic=page_deadline,
            )
            writer_attempts += used
            last_errors = list(issues)
        except Exception as error:  # noqa: BLE001 - required mode must degrade
            warning_reasons.append(f"{stage} 無法完成：{error}")
            candidate = None

        if candidate is not None:
            candidate_source = stage
            break

    if candidate is None:
        candidate = narrative_writer.build_deterministic_fallback(
            section, chart, store
        )
        candidate_source = CANDIDATE_DETERMINISTIC
        warning_reasons.append("LLM 未產生合法敘事，已套用確定性敘事模板")

    hard_issues = reviewer.run_rule_layer(candidate, chart, store)
    if hard_issues and (not review_enabled or not has_time()):
        candidate = narrative_writer.build_deterministic_fallback(
            section, chart, store
        )
        candidate_source = CANDIDATE_DETERMINISTIC
        hard_issues = reviewer.run_rule_layer(candidate, chart, store)
        warning_reasons.append("LLM 候選未通過硬性規則，已套用確定性敘事模板")

    if hard_issues and not review_enabled:
        raise RuntimeError(f"required fallback 仍未通過硬性規則：{hard_issues}")

    if not review_enabled:
        warning = "；".join(dict.fromkeys(warning_reasons)) or None
        return RequiredPageOutcome(
            narrative=candidate,
            review=None,
            writer_attempts=writer_attempts,
            reviewer_attempts=0,
            delivery_status=(
                DELIVERY_APPROVED_WITH_WARNING
                if warning is not None
                else DELIVERY_APPROVED
            ),
            candidate_source=candidate_source,
            warning=warning,
        )

    last_valid: narrative_writer.PageNarrative | None = (
        candidate if not hard_issues else None
    )
    last_valid_source = candidate_source if last_valid is not None else None
    current = candidate
    current_source = candidate_source
    final_review: reviewer.ReviewResult | None = None
    seen_candidates = {_narrative_signature(candidate)}
    seen_issue_signatures: set[str] = set()
    repair_attempts = 0

    while has_time():
        reviewer_attempts += 1
        try:
            final_review = reviewer.review_page(
                current,
                chart,
                store,
                llm_call=json_call,
                enable_semantic_layer=enable_semantic_review,
                deadline_monotonic=page_deadline,
            )
        except Exception as error:  # noqa: BLE001 - required mode must degrade
            warning_reasons.append(f"Reviewer 無法在期限內完成：{error}")
            break

        if not final_review.rule_issues:
            last_valid = current
            last_valid_source = current_source

        if final_review.approved:
            warning = "；".join(dict.fromkeys(warning_reasons)) or None
            return RequiredPageOutcome(
                narrative=current,
                review=final_review,
                writer_attempts=writer_attempts,
                reviewer_attempts=reviewer_attempts,
                delivery_status=(
                    DELIVERY_APPROVED_WITH_WARNING
                    if warning is not None
                    else DELIVERY_APPROVED
                ),
                candidate_source=current_source,
                warning=warning,
            )

        issue_signature = _sha256_text(
            json.dumps(final_review.all_issues, ensure_ascii=False, sort_keys=True)
        )
        repeated_issue = issue_signature in seen_issue_signatures
        seen_issue_signatures.add(issue_signature)

        if (
            final_review.target_agent == reviewer.AGENT_CHART
            and not final_review.rule_issues
        ):
            warning_reasons.append(
                "語意 Reviewer 建議重新規劃圖表；既有圖表已通過確定性驗證"
            )
            break

        repair_attempts += 1
        use_fallback_model = (
            repeated_issue or repair_attempts > repair_escalate_after
        )
        primary_stage = (
            CANDIDATE_FALLBACK_MODEL
            if use_fallback_model
            else CANDIDATE_LLM
        )
        stages_to_try = [primary_stage]
        if primary_stage != CANDIDATE_FALLBACK_MODEL:
            stages_to_try.append(CANDIDATE_FALLBACK_MODEL)

        replacement: narrative_writer.PageNarrative | None = None
        replacement_source = primary_stage

        for writer_stage in stages_to_try:
            if not has_time():
                break

            try:
                repaired, issues, used = (
                    narrative_writer.write_narrative_for_page(
                        section,
                        chart,
                        store,
                        llm_call=json_call,
                        max_attempts=1,
                        initial_errors=final_review.all_issues,
                        llm_stage=writer_stage,
                        deadline_monotonic=page_deadline,
                    )
                )
                writer_attempts += used
            except Exception as error:  # noqa: BLE001 - required mode must degrade
                repaired = None
                issues = [str(error)]

            if repaired is None:
                if writer_stage == CANDIDATE_FALLBACK_MODEL:
                    warning_reasons.append(
                        "升級模型後仍無法產生合法修正版："
                        + "；".join(issues)
                    )
                continue

            signature = _narrative_signature(repaired)
            if signature in seen_candidates:
                if writer_stage == CANDIDATE_FALLBACK_MODEL:
                    warning_reasons.append(
                        "升級模型仍回傳重複候選，停止無效重試"
                    )
                continue

            seen_candidates.add(signature)
            replacement = repaired
            replacement_source = writer_stage
            break

        if replacement is None:
            break

        current = replacement
        current_source = replacement_source

    if time.monotonic() >= page_deadline:
        warning_reasons.append("頁面修正期限已到")

    if last_valid is None:
        selected = narrative_writer.build_deterministic_fallback(
            section, chart, store
        )
        selected_source = CANDIDATE_DETERMINISTIC
        warning_reasons.append("無可交付 LLM 候選，已套用確定性敘事模板")
    else:
        selected = last_valid
        selected_source = last_valid_source or CANDIDATE_LLM

    selected_hard_issues = reviewer.run_rule_layer(selected, chart, store)

    if selected_hard_issues:
        selected = narrative_writer.build_deterministic_fallback(
            section, chart, store
        )
        selected_source = CANDIDATE_DETERMINISTIC
        selected_hard_issues = reviewer.run_rule_layer(selected, chart, store)
        warning_reasons.append("最佳候選未通過硬性規則，已套用確定性敘事模板")

    if selected_hard_issues:
        raise RuntimeError(
            f"required 最終候選未通過硬性規則：{selected_hard_issues}"
        )

    warning_reasons.append("語意審查未核准，採用期限內最佳合法候選")
    warning = "；".join(dict.fromkeys(warning_reasons))
    return RequiredPageOutcome(
        narrative=selected,
        review=final_review,
        writer_attempts=writer_attempts,
        reviewer_attempts=reviewer_attempts,
        delivery_status=DELIVERY_APPROVED_WITH_WARNING,
        candidate_source=selected_source,
        warning=warning,
    )


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
    generation_policy: str | None = None,
    generation_deadline_seconds: float | None = None,
    generation_render_reserve_seconds: float | None = None,
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

    generation = config.load_generation_settings()
    effective_policy = generation_policy or generation.policy
    deadline_seconds = (
        generation_deadline_seconds
        if generation_deadline_seconds is not None
        else generation.deadline_seconds
    )
    render_reserve_seconds = (
        generation_render_reserve_seconds
        if generation_render_reserve_seconds is not None
        else generation.render_reserve_seconds
    )

    if effective_policy not in config.GENERATION_POLICIES:
        raise ValueError(
            f"generation_policy 只能是 {sorted(config.GENERATION_POLICIES)} 之一"
        )

    if deadline_seconds <= 0 or not 0 <= render_reserve_seconds < deadline_seconds:
        raise ValueError("generation deadline 必須大於 render reserve，且兩者不可為負")

    pipeline_started = time.monotonic()
    llm_cutoff = pipeline_started + deadline_seconds - render_reserve_seconds

    # 內建範例用寫死的假回應（它同時也在驗那組固定期望值）；
    # 真實資料的假回應要看得懂當前 MetricStore，等 store 建好後再產生。
    uses_sample = excel_path is None and ingestion_path is None

    if use_fake_llm and uses_sample:
        json_call: Callable[..., Any] | None = _fake_json_call
        tool_call: Callable[..., Any] | None = _fake_tool_call
    elif use_fake_llm:
        # 真實資料的 store-aware fake 要等 MetricStore 建好後才能建立。
        json_call = None
        tool_call = None
    else:
        json_call = llm_client.complete_json
        tool_call = llm_client.complete_tool_call

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

    if excel_path is not None:
        manifest_input_path = Path(excel_path)
    elif ingestion_path is not None:
        manifest_input_path = Path(ingestion_path)
    else:
        manifest_input_path = None

    manifest_path = _write_generation_manifest(
        output_dir=output_dir,
        input_path=manifest_input_path,
        data_source=data_source,
        payload=payload,
        user_prompt=user_prompt,
        sections=sections,
        deck_title=deck_title,
        use_fake_llm=use_fake_llm,
        skip_semantic_review=skip_semantic_review,
        generation_policy=effective_policy,
        deadline_seconds=deadline_seconds,
        render_reserve_seconds=render_reserve_seconds,
    )
    print(f"  [執行稽核] {manifest_path}")

    loaded = dataset_loader.load_ingestion_result(payload)
    store, engine_report = metric_engine.build_metric_store(loaded)

    if use_fake_llm and not uses_sample:
        json_call, tool_call = _make_store_aware_fakes(store)

    if json_call is None or tool_call is None:
        raise RuntimeError("LLM callable 尚未初始化")

    json_call = _audited_llm_call(json_call, output_dir)
    tool_call = _audited_llm_call(tool_call, output_dir)

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

    section_warning: str | None = None
    try:
        plan = section_planner.plan_sections(
            user_prompt,
            store,
            existing_sections=sections,
            llm_call=json_call,
            deadline_monotonic=(
                llm_cutoff if effective_policy == "required" else None
            ),
        )
    except Exception as error:  # noqa: BLE001 - required mode must degrade
        if effective_policy != "required":
            raise
        plan = section_planner.build_deterministic_sections(store)
        section_warning = f"章節 LLM 無法完成，已使用確定性規劃：{error}"

    if plan.needs_confirmation and effective_policy == "required":
        plan = section_planner.build_deterministic_sections(store)
        section_warning = "章節需求不足，required 模式已使用可計算指標建立規劃"

    print(f"  狀態：{plan.status}")
    if section_warning:
        print(f"  [必產出降級] {section_warning}")

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

    sections_payload = plan.to_dict()
    if section_warning:
        sections_payload["delivery_warning"] = section_warning

    if finish("sections", sections_payload):
        return 0

    # ---- Stage 4-2 圖表決策 ----
    print("\n" + "=" * 68)
    print("Stage 4-2：圖表決策")
    print("=" * 68)

    chart_result = chart_agent.plan_charts(
        plan.sections,
        store,
        llm_call=tool_call,
        deadline_monotonic=(
            llm_cutoff if effective_policy == "required" else None
        ),
        recover_provider_errors=effective_policy == "required",
    )
    chart_fallbacks: dict[str, str] = {}

    if effective_policy == "required":
        produced_pages = {
            chart.plan.page_number for chart in chart_result.charts
        }
        for section in plan.sections:
            if section.page_number in produced_pages:
                continue

            previous_errors = chart_result.failures.get(section.title, [])
            fallback = chart_agent.build_deterministic_chart(section, store)
            chart_result.charts.append(fallback)
            chart_result.failures.pop(section.title, None)
            chart_fallbacks[section.title] = (
                "圖表 LLM 未產生合法規劃，已使用確定性 axis-kind fallback"
                + (f"：{'；'.join(previous_errors)}" if previous_errors else "")
            )

        chart_result.charts.sort(
            key=lambda item: item.plan.page_number or 0
        )

    for chart in chart_result.charts:
        attempts = chart_result.attempts.get(chart.plan.slide_title, 1)
        print(
            f"    P.{chart.plan.page_number} {chart.skill_name:8s} "
            f"{chart.plan.chart_title}（嘗試 {attempts} 次）"
        )

    for title, warning in chart_fallbacks.items():
        print(f"    [必產出降級] {title}：{warning}")

    for title, errors in chart_result.failures.items():
        print(f"    [失敗] {title}：{errors[0]}")

    expected_chart_pages = {
        section.page_number for section in plan.sections
    }
    produced_chart_pages = {
        chart.plan.page_number for chart in chart_result.charts
    }
    missing_chart_pages = sorted(expected_chart_pages - produced_chart_pages)

    charts_payload = {
        "charts": [
            _resolved_chart_payload(chart) for chart in chart_result.charts
        ],
        "failures": chart_result.failures,
        "missing_pages": missing_chart_pages,
        "attempts": chart_result.attempts,
    }
    if chart_fallbacks:
        charts_payload["delivery_warnings"] = chart_fallbacks

    if chart_result.failures or missing_chart_pages:
        dump.write("charts", charts_payload)
        print(
            "\n圖表決策未完整覆蓋所有規劃頁面，已 fail-closed；"
            f"缺少頁面：{missing_chart_pages}。"
        )
        return 1

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

    # ---- Stage 4-3/4-4：逐頁敘事與審查 ----
    print("\n" + "=" * 68)
    print("Stage 4-3：逐頁敘事撰寫")
    print("=" * 68)

    review_enabled = stop_index >= STAGE_SEQUENCE.index("review")
    if effective_policy == "required":
        print(
            "  策略：required-output；硬性規則不放寬，語意品質在每頁公平 "
            "deadline 內持續改善，期限到採最佳合法候選。"
        )
        print(
            f"  全案期限：{deadline_seconds:.0f} 秒；"
            f"保留 render/verify：{render_reserve_seconds:.0f} 秒。"
        )
    elif review_enabled:
        print(
            "  策略：strict；每頁寫完立即審查，退回 narrative_writer 時只重寫"
            f"該頁，最多 {MAX_REVIEW_REPAIR_ATTEMPTS} 次；耗盡即 fail-fast。"
        )

    narrative_result = narrative_writer.NarrativeResult()
    approved: list[tuple[Any, Any, Any]] = []
    review_payload: list[dict[str, Any]] = []
    expected_narrative_pages = {
        section.page_number for section, _ in pairs
    }

    def current_narratives_payload() -> dict[str, Any]:
        produced_pages = {
            narrative.page_number for narrative in narrative_result.narratives
        }
        return {
            "narratives": [
                narrative.to_dict()
                for narrative in narrative_result.narratives
            ],
            "failures": narrative_result.failures,
            "missing_pages": sorted(
                expected_narrative_pages - produced_pages
            ),
            "attempts": narrative_result.attempts,
        }

    def checkpoint_page_progress() -> None:
        # 每頁完成即覆寫 checkpoint。若後續頁失敗，已核准頁面與呼叫次數
        # 仍保留在 stage JSON，不必靠終端輸出回推發生了什麼。
        dump.write("narratives", current_narratives_payload())
        if review_enabled:
            dump.write("review", {"reviews": review_payload})

    for page_index, (section, chart) in enumerate(pairs):
        if effective_policy == "required":
            now = time.monotonic()
            pages_left = max(1, len(pairs) - page_index)
            remaining_llm_time = max(0.0, llm_cutoff - now)
            page_deadline = now + remaining_llm_time / pages_left
            outcome = _generate_required_page(
                section,
                chart,
                store,
                json_call=json_call,
                review_enabled=review_enabled,
                enable_semantic_review=not skip_semantic_review,
                page_deadline=page_deadline,
                repair_escalate_after=generation.repair_escalate_after,
            )
            narrative_result.narratives.append(outcome.narrative)
            narrative_result.attempts[section.title] = outcome.writer_attempts
            approved.append((section, chart, outcome.narrative))

            if review_enabled:
                review_payload.append(outcome.review_dict(section))

            status_text = outcome.delivery_status
            print(
                f"    P.{section.page_number} {section.title}：{status_text} "
                f"（writer {outcome.writer_attempts} 次、"
                f"reviewer {outcome.reviewer_attempts} 次、"
                f"來源 {outcome.candidate_source}）"
            )
            if outcome.warning:
                print(f"        - {outcome.warning}")

            checkpoint_page_progress()
            continue

        narrative, issues, attempts = narrative_writer.write_narrative_for_page(
            section,
            chart,
            store,
            llm_call=json_call,
        )
        total_writer_attempts = attempts
        narrative_result.attempts[section.title] = total_writer_attempts

        if narrative is None:
            narrative_result.failures[section.title] = issues or ["未知原因"]
            checkpoint_page_progress()
            unvisited = [
                pending.page_number
                for pending, _ in pairs[page_index + 1 :]
            ]
            print(f"    [失敗] P.{section.page_number} {section.title}：")
            for issue in narrative_result.failures[section.title]:
                print(f"        - {issue}")
            print(
                "\n敘事在單頁 writer 預算內仍未通過，已 fail-fast；"
                f"未再呼叫後續頁面：{unvisited}。"
            )
            return 1

        current_narrative = narrative

        if not review_enabled:
            narrative_result.narratives.append(current_narrative)
            print(
                f"    P.{current_narrative.page_number} "
                f"{current_narrative.headline}"
                f"（writer 嘗試 {total_writer_attempts} 次）"
            )
            checkpoint_page_progress()
            continue

        final_review: reviewer.ReviewResult | None = None
        repair_feedback: list[str] = []
        repair_attempts = 0
        review_attempts = 0

        while True:
            review_attempts += 1
            final_review = reviewer.review_page(
                current_narrative,
                chart,
                store,
                llm_call=json_call,
                enable_semantic_layer=not skip_semantic_review,
            )

            if final_review.approved:
                break

            print(
                f"    P.{section.page_number} {section.title}：REJECTED "
                f"→ {final_review.target_agent}"
            )
            for issue in final_review.all_issues:
                print(f"        - {issue}")

            if final_review.target_agent != reviewer.AGENT_NARRATIVE:
                # 圖表問題不能靠重寫文字掩蓋；目前 chart 已通過所有確定性
                # scope/量綱檢查，語意層仍要求改 chart 時直接停，避免繼續燒額度。
                break

            if repair_attempts >= MAX_REVIEW_REPAIR_ATTEMPTS:
                break

            repair_feedback = list(final_review.all_issues)
            repaired: narrative_writer.PageNarrative | None = None

            while (
                repaired is None
                and repair_attempts < MAX_REVIEW_REPAIR_ATTEMPTS
            ):
                repair_attempts += 1
                repaired, writer_issues, writer_attempts = (
                    narrative_writer.write_narrative_for_page(
                        section,
                        chart,
                        store,
                        llm_call=json_call,
                        max_attempts=1,
                        initial_errors=repair_feedback,
                    )
                )
                total_writer_attempts += writer_attempts
                narrative_result.attempts[section.title] = (
                    total_writer_attempts
                )

                if repaired is None:
                    repair_feedback = writer_issues or [
                        "修正版仍未通過確定性敘事規則"
                    ]

            if repaired is None:
                break

            current_narrative = repaired

        assert final_review is not None
        review_payload.append(
            {
                "page_number": section.page_number,
                "section_title": section.title,
                **final_review.to_dict(),
            }
        )
        narrative_result.narratives.append(current_narrative)

        if not final_review.approved:
            checkpoint_page_progress()
            unvisited = [
                pending.page_number
                for pending, _ in pairs[page_index + 1 :]
            ]
            reason = (
                "Reviewer 要求重新規劃 chart"
                if final_review.target_agent == reviewer.AGENT_CHART
                else (
                    "單頁 narrative 修正預算已耗盡"
                    if repair_attempts >= MAX_REVIEW_REPAIR_ATTEMPTS
                    else "Reviewer 退件"
                )
            )
            print(
                f"\nP.{section.page_number} 審查未通過（{reason}），"
                "已 fail-fast；"
                f"未再呼叫後續頁面：{unvisited}。"
            )
            return 1

        approved.append((section, chart, current_narrative))
        print(
            f"    P.{section.page_number} {section.title}：APPROVED "
            f"（writer {total_writer_attempts} 次、"
            f"reviewer {review_attempts} 次）"
        )
        checkpoint_page_progress()

    narratives_payload = current_narratives_payload()

    if not narrative_result.narratives:
        dump.write("narratives", narratives_payload)
        print("\n沒有任何敘事產出，無法繼續。")
        return 1

    if finish("narratives", narratives_payload):
        return 0

    # 完整流程走到這裡時，每頁已在上面的逐頁迴圈完成 reviewer。
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
        f" + 結論 {1 if render_report.conclusion_page else 0}"
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
        "--generation-policy",
        choices=sorted(config.GENERATION_POLICIES),
        default=None,
        help="strict=退件即停止；required=期限內必產出（預設讀 GENERATION_POLICY）",
    )
    parser.add_argument(
        "--deadline-seconds",
        type=float,
        default=None,
        help="全案時間上限秒數（預設讀 GENERATION_DEADLINE_SECONDS）",
    )
    parser.add_argument(
        "--render-reserve-seconds",
        type=float,
        default=None,
        help="保留給 render/verify 的秒數",
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
            generation_policy=args.generation_policy,
            generation_deadline_seconds=args.deadline_seconds,
            generation_render_reserve_seconds=args.render_reserve_seconds,
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
