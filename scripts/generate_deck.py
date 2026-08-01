"""Thin CLI for the formal backend → core → ppt_generation flow."""

from __future__ import annotations

import argparse
import json
import re
import sys
from datetime import datetime
from pathlib import Path
from typing import Sequence


REPO_ROOT = Path(__file__).resolve().parent.parent
SRC_ROOT = REPO_ROOT / "src"
BACKEND_ROOT = SRC_ROOT / "backend"

for import_root in (SRC_ROOT, BACKEND_ROOT):
    value = str(import_root)
    if value not in sys.path:
        sys.path.insert(0, value)

from app.ingestion.generation_bridge import ingest_excel, save_payload  # noqa: E402
from core.contracts.generation import GenerationRequest  # noqa: E402
from core.generation_orchestrator import generate_deck  # noqa: E402


DEFAULT_PROMPT = (
    "依上傳資料製作高階管理層簡報，呈現資料概況、關鍵差異、"
    "重要洞察與可追溯的行動建議。所有數值必須由 deterministic "
    "MetricStore 提供，LLM 不得自行計算或產生數字。"
)
DEFAULT_SECTIONS = "資料概況,關鍵差異與趨勢,風險與機會,行動建議"


def _slug(text: str) -> str:
    value = re.sub(r"[^\w\u4e00-\u9fff]+", "_", text.strip())
    return value.strip("_") or "generation"


def _sections(value: str) -> list[str]:
    sections = [item.strip() for item in value.split(",") if item.strip()]
    if not sections:
        raise argparse.ArgumentTypeError("--sections 至少需要一個章節")
    return sections


def _default_output(source: Path) -> Path:
    stamp = datetime.now().strftime("%Y%m%d_%H%M%S")
    return REPO_ROOT / "outputs" / f"{_slug(source.stem)}_bedrock_{stamp}"


def _blocked_datasets(payload: dict) -> list[dict]:
    return [
        {
            "dataset_id": dataset["dataset_id"],
            "name": dataset["name"],
            "confidence": dataset["confidence"],
            "review_status": dataset["review_status"],
            "review_reasons": dataset.get("review_reasons", []),
        }
        for dataset in payload["datasets"]
        if dataset.get("requires_human_review")
        or dataset.get("review_status") in {"pending", "rejected"}
    ]


def _print_ingestion(payload: dict, blocked: list[dict]) -> None:
    print(
        "Ingestion: "
        f"status={payload['pipeline_status']}, "
        f"datasets={len(payload['datasets'])}, "
        f"blocked={len(blocked)}"
    )
    for dataset in payload["datasets"]:
        normalization = dataset.get("normalization_spec") or {}
        print(
            f"  - {dataset['name']}: "
            f"rows={len(dataset.get('records') or [])}, "
            f"columns={len(dataset.get('columns') or [])}, "
            f"strategy={dataset.get('layout_strategy')}, "
            f"confidence={dataset['confidence']}, "
            f"numeric_ratio={normalization.get('explained_numeric_ratio')}, "
            f"review={dataset.get('requires_human_review')}"
        )


def _parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(
        description=(
            "從 raw XLSX 執行正式 backend ingestion → core.generate_deck "
            "→ ppt_generation 管線"
        )
    )
    parser.add_argument("--excel", type=Path, required=True, help="來源 XLSX 檔案或目錄")
    parser.add_argument("--prompt", default=DEFAULT_PROMPT, help="簡報需求描述")
    parser.add_argument(
        "--sections",
        type=_sections,
        default=_sections(DEFAULT_SECTIONS),
        help="逗號分隔的章節名稱",
    )
    parser.add_argument("--title", default=None, help="簡報封面標題")
    parser.add_argument(
        "--output-dir",
        type=Path,
        default=None,
        help="輸出目錄；省略時自動寫入 outputs/<檔名>_bedrock_<時間>",
    )
    parser.add_argument(
        "--policy",
        choices=("strict", "required"),
        default="required",
        help="生成交付政策",
    )
    parser.add_argument("--deadline-seconds", type=float, default=900.0)
    parser.add_argument("--render-reserve-seconds", type=float, default=180.0)
    parser.add_argument("--fake-llm", action="store_true", help="使用 deterministic fake LLM")
    parser.add_argument(
        "--skip-semantic-review",
        action="store_true",
        help="只執行 reviewer 規則層",
    )
    parser.add_argument(
        "--preflight-only",
        action="store_true",
        help="只執行 ingestion 與人工確認 gate，不生成簡報",
    )
    return parser


def main(argv: Sequence[str] | None = None) -> int:
    parser = _parser()
    args = parser.parse_args(argv)

    source = args.excel.resolve()
    if not source.exists():
        parser.error(f"找不到 Excel 輸入：{source}")

    output_dir = (
        args.output_dir.resolve()
        if args.output_dir is not None
        else _default_output(source)
    )
    if output_dir.exists() and any(output_dir.iterdir()):
        parser.error(f"輸出目錄不是空的，拒絕覆寫：{output_dir}")

    ingestion_path = output_dir / "ingestion.json"
    payload = ingest_excel(source)
    save_payload(payload, ingestion_path)

    blocked = _blocked_datasets(payload)
    _print_ingestion(payload, blocked)
    print(f"Ingestion JSON: {ingestion_path}")

    if payload["pipeline_status"] not in {
        "completed",
        "completed_with_warnings",
    }:
        print(
            f"Ingestion 狀態不可生成：{payload['pipeline_status']}",
            file=sys.stderr,
        )
        return 2

    if blocked:
        print(
            "資料集尚未通過人工確認 gate：\n"
            + json.dumps(blocked, ensure_ascii=False, indent=2),
            file=sys.stderr,
        )
        return 2

    if args.preflight_only:
        print("Preflight passed；未呼叫 LLM。")
        return 0

    stamp = datetime.now().strftime("%Y%m%d_%H%M%S")
    request = GenerationRequest(
        job_id=f"cli-{_slug(source.stem)}-{stamp}",
        prompt=args.prompt,
        ingestion_path=str(ingestion_path),
        output_dir=str(output_dir),
        sections=args.sections,
        deck_title=args.title,
        options={
            "policy": args.policy,
            "deadline_seconds": args.deadline_seconds,
            "render_reserve_seconds": args.render_reserve_seconds,
            "use_fake_llm": args.fake_llm,
            "skip_semantic_review": args.skip_semantic_review,
        },
    )
    result = generate_deck(request.model_dump(mode="json"))

    print(
        f"Generation succeeded: slides={result.slide_count}, "
        f"charts={result.chart_count}, "
        f"T1={result.external_checked}/{result.series_checked}"
    )
    for artifact in result.artifacts:
        print(f"  - {artifact.filename}: {artifact.path}")
    print(f"Output: {output_dir}")
    return 0


if __name__ == "__main__":
    try:
        raise SystemExit(main())
    except Exception as error:  # noqa: BLE001 - CLI presents one actionable failure
        print(f"Generation failed: {type(error).__name__}: {error}", file=sys.stderr)
        raise SystemExit(1) from error
