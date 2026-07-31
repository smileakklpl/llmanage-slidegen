"""合併前驗收：只驗正式 backend → core orchestrator → ppt_generation 路徑。

    python scripts/verify_all.py

全部使用確定性測試或 store-aware fake LLM，不呼叫外部模型、不寫入 outputs/。
任一檢查失敗即回傳非零狀態碼。
"""

from __future__ import annotations

import io
import subprocess
import sys
import tempfile
import time
from contextlib import redirect_stdout
from pathlib import Path
from typing import Callable

sys.path.insert(0, str(Path(__file__).resolve().parent))

import bootstrap  # noqa: E402,F401

REPO_ROOT = Path(__file__).resolve().parent.parent
SMOKE_INPUT = REPO_ROOT / "fixtures" / "data" / "fsc_114_workbook.xlsx"

Check = tuple[str, Callable[[], str]]


def _run_pytest(*, cwd: Path, target: str | None = None) -> str:
    command = [sys.executable, "-m", "pytest"]
    if target:
        command.append(target)
    command.append("-q")

    result = subprocess.run(
        command,
        cwd=cwd,
        capture_output=True,
        text=True,
        encoding="utf-8",
        errors="replace",
    )
    lines = (result.stdout or result.stderr).strip().splitlines()
    tail = lines[-1] if lines else "pytest 沒有輸出"
    if result.returncode != 0:
        detail = "\n".join(lines[-20:])
        raise AssertionError(detail or tail)
    return tail


def _root_pytest() -> str:
    return _run_pytest(cwd=REPO_ROOT, target="tests")


def _backend_pytest() -> str:
    return _run_pytest(cwd=REPO_ROOT / "src" / "backend")


def _layering() -> str:
    """Enforce backend → core → ppt_generation and block tool imports."""
    import re

    tool_pattern = re.compile(r"^\s*(?:from|import)\s+(tools)\b", re.MULTILINE)
    forbidden_by_layer = {
        "backend": re.compile(
            r"^\s*(?:from|import)\s+ppt_generation\b", re.MULTILINE
        ),
        "core": re.compile(
            r"^\s*(?:from|import)\s+(?:app|backend)\b", re.MULTILINE
        ),
        "ppt_generation": re.compile(
            r"^\s*(?:from|import)\s+(?:app|backend|core)\b",
            re.MULTILINE,
        ),
    }
    offenders: list[str] = []
    source_files = sorted((REPO_ROOT / "src").rglob("*.py"))

    for path in source_files:
        text = path.read_text(encoding="utf-8")
        patterns = [("tools", tool_pattern)]
        relative = path.relative_to(REPO_ROOT / "src")
        if relative.parts:
            layer = relative.parts[0]
            if layer in forbidden_by_layer:
                patterns.append(("layer", forbidden_by_layer[layer]))

        for rule, pattern in patterns:
            for match in pattern.finditer(text):
                line = text[: match.start()].count("\n") + 1
                offenders.append(
                    f"{path.relative_to(REPO_ROOT)}:{line} → {rule}: "
                    f"{match.group(0).strip()}"
                )

    assert not offenders, (
        "產品碼依賴方向違規：\n    " + "\n    ".join(offenders)
    )
    return (
        f"src/ 的 {len(source_files)} 支檔案符合 "
        "backend → core → ppt_generation"
    )


def _prepare_ingestion(source: Path, target: Path) -> Path:
    """Run the backend-owned ingestion bridge before crossing into core."""
    backend_root = str(REPO_ROOT / "src" / "backend")
    if backend_root not in sys.path:
        sys.path.insert(0, backend_root)

    from app.ingestion.generation_bridge import ingest_excel, save_payload

    return save_payload(ingest_excel(source), target)


def _production_pipeline() -> str:
    """用正式 callable boundary 跑真 Excel 到四項已驗證 artifacts。"""
    from core.contracts.generation import GenerationRequest
    from core.generation_orchestrator import generate_deck

    if not SMOKE_INPUT.is_file():
        raise AssertionError(f"缺少正式 smoke fixture：{SMOKE_INPUT}")

    with tempfile.TemporaryDirectory(prefix="slidegen-verify-") as temp_dir:
        output_dir = Path(temp_dir) / "artifacts"
        ingestion_path = _prepare_ingestion(
            SMOKE_INPUT,
            Path(temp_dir) / "ingestion.json",
        )
        request = GenerationRequest(
            job_id="verify-production-pipeline",
            prompt="依上傳資料產出管理層簡報，呈現核心概況、趨勢與重點觀察。",
            ingestion_path=str(ingestion_path),
            output_dir=str(output_dir),
            sections=["核心概況", "趨勢分析", "重點觀察"],
            deck_title="正式管線驗收",
            options={
                "use_fake_llm": True,
                "skip_semantic_review": False,
            },
        )
        result = generate_deck(request.model_dump(mode="json"))

        expected = {
            "deck.pptx",
            "deck_data.xlsx",
            "verification.json",
            "generation_manifest.json",
        }
        actual = {artifact.filename for artifact in result.artifacts}
        assert actual == expected, f"artifact 集合不完整：{sorted(actual)}"
        assert all(Path(item.path).is_file() for item in result.artifacts)
        assert all(item.size_bytes > 0 for item in result.artifacts)
        assert result.verification_passed is True
        assert result.series_checked > 0
        assert result.external_checked == result.series_checked
        assert result.page_count > 0
        assert result.slide_count >= result.page_count
        assert result.chart_count > 0

        return (
            f"{result.slide_count} slides / {result.chart_count} charts；"
            f"T1 {result.external_checked}/{result.series_checked}；"
            f"{len(result.artifacts)} artifacts"
        )


def main() -> int:
    checks: list[Check] = [
        ("root 測試", _root_pytest),
        ("backend 測試", _backend_pytest),
        ("分層依賴方向", _layering),
        ("正式端到端管線", _production_pipeline),
    ]

    print("=" * 72)
    print("合併前驗收（正式 production 路徑；不呼叫外部模型、不碰 outputs/）")
    print("=" * 72)

    failed = 0
    for label, check in checks:
        started = time.perf_counter()
        try:
            captured = io.StringIO()
            with redirect_stdout(captured):
                detail = check()
            print(f"  ✓ {label:<20s} {detail}  ({time.perf_counter() - started:.1f}s)")
        except Exception as error:  # noqa: BLE001 - 驗收需彙整所有 gate
            failed += 1
            print(
                f"  ✗ {label:<20s} {type(error).__name__}: "
                f"{str(error)[:500]}"
            )

    print("=" * 72)
    if failed:
        print(f"{failed} 項未通過 —— 不要合併，先修好。")
        return 1

    print("全數通過。正式 backend/core/ppt_generation 管線與契約完整。")
    print("模型品質請另外跑 python -m tools.compare_models。")
    return 0


if __name__ == "__main__":
    sys.exit(main())
