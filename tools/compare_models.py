"""以正式 generation orchestrator 執行多模型端到端 A/B。

每個模型都從真實 Excel 開始，完整跑過 ingestion、engine、agents、renderer、
外部稽核 Excel 與 T1 驗證。輸出一張成功率/延遲/頁數/圖表數報表；所有暫存
產物放在系統 temp 目錄，不會寫入 outputs/。

PowerShell 範例：
    python -m tools.compare_models --provider ollama --models gemma2:9b,qwen2.5:7b
    python -m tools.compare_models --provider bedrock --models <model-id> --repeat 3
"""

from __future__ import annotations

import argparse
import io
import os
import statistics
import sys
import tempfile
import time
from contextlib import contextmanager, redirect_stdout
from dataclasses import dataclass
from pathlib import Path
from typing import Iterator

REPO_ROOT = Path(__file__).resolve().parent.parent
SRC_ROOT = str(REPO_ROOT / "src")
if SRC_ROOT not in sys.path:
    sys.path.insert(0, SRC_ROOT)

from core.contracts.generation import GenerationRequest  # noqa: E402
from core.generation_orchestrator import generate_deck  # noqa: E402

MODEL_ENV_NAMES = (
    "LLM_MODEL_DEFAULT",
    "LLM_MODEL_INTENT",
    "LLM_MODEL_WRITER",
    "LLM_MODEL_WRITER_KEYPAGES",
    "LLM_MODEL_CHART",
    "LLM_MODEL_REVIEWER",
)


def _prepare_ingestion(source: Path, target: Path) -> Path:
    """Materialize backend ingestion JSON before invoking core."""
    backend_root = str(REPO_ROOT / "src" / "backend")
    if backend_root not in sys.path:
        sys.path.insert(0, backend_root)

    from app.ingestion.generation_bridge import ingest_excel, save_payload

    return save_payload(ingest_excel(source), target)


@dataclass
class RunRecord:
    model: str
    attempt: int
    ok: bool
    latency_seconds: float
    slide_count: int = 0
    chart_count: int = 0
    series_checked: int = 0
    error: str = ""


@contextmanager
def _model_environment(provider: str, model: str) -> Iterator[None]:
    updates = {"LLM_PROVIDER": provider}
    updates.update({name: model for name in MODEL_ENV_NAMES})
    previous = {name: os.environ.get(name) for name in updates}

    try:
        os.environ.update(updates)
        yield
    finally:
        for name, value in previous.items():
            if value is None:
                os.environ.pop(name, None)
            else:
                os.environ[name] = value


def run_model(
    *,
    provider: str,
    model: str,
    ingestion_path: Path,
    prompt: str,
    sections: list[str],
    repeat: int,
) -> list[RunRecord]:
    records: list[RunRecord] = []

    with _model_environment(provider, model):
        for attempt in range(1, repeat + 1):
            started = time.perf_counter()
            captured = io.StringIO()

            try:
                with tempfile.TemporaryDirectory(
                    prefix="slidegen-model-compare-"
                ) as temp_dir, redirect_stdout(captured):
                    result = generate_deck(
                        GenerationRequest(
                            job_id=f"compare-{attempt}",
                            prompt=prompt,
                            ingestion_path=str(ingestion_path),
                            output_dir=str(Path(temp_dir) / "artifacts"),
                            sections=sections,
                            deck_title=f"模型驗收：{model}",
                            options={
                                "use_fake_llm": False,
                                "skip_semantic_review": False,
                            },
                        ).model_dump(mode="json")
                    )

                records.append(
                    RunRecord(
                        model=model,
                        attempt=attempt,
                        ok=True,
                        latency_seconds=time.perf_counter() - started,
                        slide_count=result.slide_count,
                        chart_count=result.chart_count,
                        series_checked=result.series_checked,
                    )
                )
            except Exception as error:  # noqa: BLE001 - A/B 不因單一模型中止
                log_lines = captured.getvalue().strip().splitlines()
                log_tail = " | ".join(log_lines[-3:])
                detail = f"{type(error).__name__}: {error}"
                if log_tail:
                    detail = f"{detail}；{log_tail}"
                records.append(
                    RunRecord(
                        model=model,
                        attempt=attempt,
                        ok=False,
                        latency_seconds=time.perf_counter() - started,
                        error=detail[:500],
                    )
                )

    return records


def report(records: list[RunRecord], repeat: int) -> str:
    lines = [
        "",
        f"{'模型':<34}{'成功率':>8}{'p50 秒':>10}{'slides':>9}{'charts':>9}{'T1':>8}",
        "-" * 78,
    ]

    models = list(dict.fromkeys(record.model for record in records))
    for model in models:
        model_records = [record for record in records if record.model == model]
        passed = [record for record in model_records if record.ok]
        success_rate = len(passed) / repeat
        p50 = statistics.median(record.latency_seconds for record in model_records)
        slide_count = round(statistics.mean(r.slide_count for r in passed)) if passed else 0
        chart_count = round(statistics.mean(r.chart_count for r in passed)) if passed else 0
        t1 = round(statistics.mean(r.series_checked for r in passed)) if passed else 0
        lines.append(
            f"{model:<34}{success_rate:>7.0%}{p50:>10.1f}"
            f"{slide_count:>9}{chart_count:>9}{t1:>8}"
        )

    failures = [record for record in records if not record.ok]
    if failures:
        lines.extend(["", "失敗明細："])
        for record in failures:
            lines.append(f"  [{record.model} #{record.attempt}] {record.error}")
    else:
        lines.extend(["", "所有模型均通過正式 full-pipeline 與 T1 fail-closed 驗證。"])

    return "\n".join(lines)


def main() -> int:
    parser = argparse.ArgumentParser(description="正式 generation pipeline 多模型 A/B")
    parser.add_argument("--provider", default="ollama")
    parser.add_argument(
        "--models",
        default="gemma2:9b,qwen2.5:7b,llama3.1:8b",
        help="逗號分隔的模型名稱",
    )
    parser.add_argument(
        "--input",
        type=Path,
        default=REPO_ROOT / "fixtures" / "data" / "fsc_114_workbook.xlsx",
    )
    parser.add_argument(
        "--prompt",
        default="依上傳資料產出管理層簡報，呈現市場概況、趨勢與重點觀察。",
    )
    parser.add_argument(
        "--sections",
        default="市場概況,趨勢分析,重點觀察",
        help="逗號分隔；固定章節可避免把章節確認行為混入模型比較",
    )
    parser.add_argument("--repeat", type=int, default=1)
    args = parser.parse_args()

    input_path = args.input.resolve()
    if not input_path.is_file():
        parser.error(f"找不到輸入 Excel：{input_path}")
    if args.repeat < 1:
        parser.error("--repeat 必須大於 0")

    models = [item.strip() for item in args.models.split(",") if item.strip()]
    sections = [item.strip() for item in args.sections.split(",") if item.strip()]
    if not models:
        parser.error("--models 不可為空")
    if not sections:
        parser.error("--sections 不可為空")

    records: list[RunRecord] = []
    with tempfile.TemporaryDirectory(prefix="slidegen-model-input-") as temp_dir:
        ingestion_path = _prepare_ingestion(
            input_path,
            Path(temp_dir) / "ingestion.json",
        )
        for model in models:
            print(f"跑 {args.provider}/{model} × {args.repeat} …", flush=True)
            records.extend(
                run_model(
                    provider=args.provider,
                    model=model,
                    ingestion_path=ingestion_path,
                    prompt=args.prompt,
                    sections=sections,
                    repeat=args.repeat,
                )
            )

    print(report(records, args.repeat))
    return 0 if records and all(record.ok for record in records) else 1


if __name__ == "__main__":
    sys.exit(main())
