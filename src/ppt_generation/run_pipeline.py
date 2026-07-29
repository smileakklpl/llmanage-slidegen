"""
端到端管線 CLI
===============
把完整流程串起來跑一次，供實測與 Demo 使用。

三種執行模式：

1. **連線檢查**：只確認 LLM 設定與金鑰能通
   ``python -m ppt_generation.run_pipeline --check-llm``

2. **內建範例資料**：不需要 backend 輸出，直接用內建的信用卡市場範例跑完整流程
   ``python -m ppt_generation.run_pipeline --sample --prompt "..."``

3. **真實 backend JSON**
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
from .core import config, llm_client
from .data import dataset_loader, metric_engine
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
        "metric_key": chart.metric.key,
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
                    "intent": "呈現市場規模趨勢",
                    "suggested_metric_keys": ["market_cards.value"],
                },
                {
                    "title": "成長動能檢視",
                    "intent": "檢視年增動能變化",
                    "suggested_metric_keys": ["market_cards.yoy"],
                },
                {
                    "title": "業者競爭態勢",
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

    json_call = _fake_json_call if use_fake_llm else None
    tool_call = _fake_tool_call if use_fake_llm else None
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

    if ingestion_path is None:
        payload = _sample_ingestion_payload()
        print("  資料來源：內建範例（--sample）")
    else:
        payload = json.loads(Path(ingestion_path).read_text(encoding="utf-8"))
        print(f"  資料來源：{ingestion_path}")

    loaded = dataset_loader.load_ingestion_result(payload)
    store, engine_report = metric_engine.build_metric_store(loaded)

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
            "source": str(ingestion_path) if ingestion_path else "built-in sample",
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

    for section in plan.sections:
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

    bundles = [
        renderer.PageBundle(section, chart, narrative)
        for section, chart, narrative in approved
    ]

    render_report = renderer.render_deck(
        bundles,
        store,
        output_path=pptx_path,
        deck_title=user_prompt[:40],
    )

    print(
        f"  {render_report.output_path}"
        f"（{render_report.page_count} 頁，{render_report.chart_count} 圖表）"
    )

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
        "--ingestion",
        default=None,
        help="backend ingestion 輸出的 JSON 路徑",
    )
    parser.add_argument(
        "--sample",
        action="store_true",
        help="使用內建範例資料（與 --ingestion 二選一）",
    )
    parser.add_argument(
        "--prompt",
        default="幫我做一份 2026 信用卡市場分析簡報",
        help="使用者需求描述",
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

    if not args.sample and args.ingestion is None:
        parser.error("請指定 --ingestion <path> 或 --sample")

    output_dir = Path(args.output_dir)

    if args.no_stage_dump:
        dump_dir = None
    elif args.stage_dir:
        dump_dir = Path(args.stage_dir)
    else:
        dump_dir = output_dir / "stages"

    return run(
        ingestion_path=args.ingestion,
        user_prompt=args.prompt,
        sections=args.sections,
        output_dir=output_dir,
        use_fake_llm=args.fake_llm,
        skip_semantic_review=args.skip_semantic_review,
        stop_after=args.stage,
        dump_dir=dump_dir,
    )


if __name__ == "__main__":
    sys.exit(main())
