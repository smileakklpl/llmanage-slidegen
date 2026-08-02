"""Schema-validated local chat workspace backed by the formal deck pipeline.

Coding agents edit only ``request.json`` for an initial run and ``revision.json``
for follow-up intent. Every run performs backend ingestion and calls
``core.generation_orchestrator.generate_deck``; this module never edits PPTX,
chart XML, MetricStore, or stage output.
"""

from __future__ import annotations

import argparse
import json
import re
import sys
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Literal, Sequence

from pydantic import BaseModel, ConfigDict, Field, model_validator

REPO_ROOT = Path(__file__).resolve().parent.parent
SRC_ROOT = REPO_ROOT / "src"
BACKEND_ROOT = SRC_ROOT / "backend"
for import_root in (SRC_ROOT, BACKEND_ROOT):
    value = str(import_root)
    if value not in sys.path:
        sys.path.insert(0, value)

from app.ingestion.generation_bridge import ingest_excel, save_payload  # noqa: E402
from core.contracts.generation import (  # noqa: E402
    GenerationRequest,
    PageRevisionIntent,
    RevisionIntent,
)
from core.generation_orchestrator import generate_deck  # noqa: E402
from ppt_generation.contracts import DeckSpecContract  # noqa: E402
from ppt_generation.core import config  # noqa: E402

CONTRACT_VERSION = "1.0"
DEFAULT_PROMPT = (
    "依上傳資料製作高階管理層簡報，呈現資料概況、關鍵差異、重要洞察與"
    "可追溯的行動建議。所有數值必須來自 deterministic MetricStore。"
)
DEFAULT_SECTIONS = ["資料概況", "關鍵差異與趨勢", "風險與機會", "行動建議"]
RUN_NAME_PATTERN = r"^[A-Za-z0-9][A-Za-z0-9._-]{0,63}$"
ChartType = Literal[
    "bar", "column", "line", "pie", "scatter", "combo", "table", "heatmap"
]


class ApprovedInput(BaseModel):
    """Initializer-owned input lock; never part of agent-editable JSON."""

    model_config = ConfigDict(extra="forbid")

    contract_version: Literal["1.0"] = CONTRACT_VERSION
    excel_path: str = Field(min_length=1)
    sha256: str = Field(pattern=r"^[0-9a-f]{64}$")


class AgentDeck(BaseModel):
    model_config = ConfigDict(extra="forbid")

    prompt: str = Field(min_length=1)
    sections: list[str] = Field(min_length=1)
    title: str | None = None


class AgentGeneration(BaseModel):
    model_config = ConfigDict(extra="forbid")

    policy: Literal["strict", "required"] = "required"
    deadline_seconds: float = Field(default=1500.0, gt=0)
    render_reserve_seconds: float = Field(default=240.0, ge=0)
    skip_semantic_review: bool = False
    run_name: str | None = Field(default=None, pattern=RUN_NAME_PATTERN)

    @model_validator(mode="after")
    def validate_reserve(self) -> AgentGeneration:
        if self.render_reserve_seconds >= self.deadline_seconds:
            raise ValueError("render_reserve_seconds 必須小於 deadline_seconds")
        return self


class AgentWorkspaceRequest(BaseModel):
    """Versioned initial request with no provider, endpoint, path, or values."""

    model_config = ConfigDict(extra="forbid")

    contract_version: Literal["1.0"] = CONTRACT_VERSION
    deck: AgentDeck
    generation: AgentGeneration = Field(default_factory=AgentGeneration)


class AgentPageRevision(BaseModel):
    """Presentation-only instruction for an existing content page."""

    model_config = ConfigDict(extra="forbid")

    page_number: int = Field(ge=1)
    instruction: str = Field(min_length=1, max_length=2000)
    preferred_chart_type: ChartType | None = None


class AgentRevisionRequest(BaseModel):
    """Agent-editable follow-up contract; base artifacts remain immutable."""

    model_config = ConfigDict(extra="forbid")

    contract_version: Literal["1.0"] = CONTRACT_VERSION
    base_run_name: str = Field(pattern=RUN_NAME_PATTERN)
    new_run_name: str = Field(pattern=RUN_NAME_PATTERN)
    deck_title: str | None = None
    sections: list[str] | None = Field(default=None, min_length=1)
    global_instruction: str | None = Field(default=None, min_length=1, max_length=4000)
    page_revisions: list[AgentPageRevision] = Field(default_factory=list)
    preserve_unmentioned_pages: Literal[True] = True

    @model_validator(mode="after")
    def validate_revision(self) -> AgentRevisionRequest:
        pages = [item.page_number for item in self.page_revisions]
        if len(pages) != len(set(pages)):
            raise ValueError("page_revisions 不得重複指定頁碼")
        if not any(
            (
                self.deck_title is not None,
                self.sections is not None,
                self.global_instruction is not None,
                bool(self.page_revisions),
            )
        ):
            raise ValueError("revision 至少需要一項實際修改")
        if self.base_run_name == self.new_run_name:
            raise ValueError("new_run_name 必須不同於 base_run_name")
        return self


def _utc_now() -> str:
    return datetime.now(timezone.utc).isoformat()


def _write_json(path: Path, payload: dict[str, Any]) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    temporary = path.with_suffix(path.suffix + ".tmp")
    temporary.write_text(
        json.dumps(payload, ensure_ascii=False, indent=2) + "\n",
        encoding="utf-8",
    )
    temporary.replace(path)


def _read_json(path: Path) -> dict[str, Any]:
    if not path.is_file():
        raise FileNotFoundError(f"找不到必要檔案：{path.name}")
    payload = json.loads(path.read_text(encoding="utf-8"))
    if not isinstance(payload, dict):
        raise ValueError(f"JSON 最外層必須是 object：{path.name}")
    return payload


def _read_status(workspace: Path) -> dict[str, Any]:
    path = workspace / "status" / "current.json"
    return _read_json(path) if path.is_file() else {}


def _write_status(workspace: Path, state: str, **details: object) -> None:
    previous = _read_status(workspace)
    payload: dict[str, Any] = {
        **previous,
        "contract_version": CONTRACT_VERSION,
        "state": state,
        "request_path": "request.json",
        "revision_path": "revision.json",
        "updated_at": _utc_now(),
        **details,
    }
    _write_json(workspace / "status" / "current.json", payload)


def _sections(raw: str) -> list[str]:
    values = [item.strip() for item in raw.split(",") if item.strip()]
    if not values:
        raise argparse.ArgumentTypeError("sections 至少需要一個非空值")
    return values


def _sha256(path: Path) -> str:
    import hashlib

    digest = hashlib.sha256()
    with path.open("rb") as stream:
        for chunk in iter(lambda: stream.read(1024 * 1024), b""):
            digest.update(chunk)
    return digest.hexdigest()


def _resolve_input(workspace: Path) -> Path:
    lock_path = workspace / "system" / "approved-input.json"
    lock = ApprovedInput.model_validate(_read_json(lock_path))
    resolved = Path(lock.excel_path).resolve()
    if not resolved.is_file() or resolved.suffix.lower() != ".xlsx":
        raise FileNotFoundError("核准的 XLSX 輸入不存在或格式不符")
    if _sha256(resolved) != lock.sha256:
        raise RuntimeError("核准的 Excel 在初始化後已變更；請建立新的 workspace")
    return resolved


def _load_request(path: Path) -> AgentWorkspaceRequest:
    return AgentWorkspaceRequest.model_validate(_read_json(path))


def _load_workspace_request(workspace: Path) -> AgentWorkspaceRequest:
    return _load_request(workspace / "request.json")


def _load_revision(workspace: Path) -> AgentRevisionRequest:
    return AgentRevisionRequest.model_validate(
        _read_json(workspace / "revision.json")
    )


def _require_local_only() -> config.LLMSettings:
    settings = config.load_llm_settings()
    config.validate_llm_settings(settings)
    if settings.privacy_mode != "local_only":
        raise RuntimeError(
            "Agent workspace 只允許 LLM_PRIVACY_MODE=local_only；"
            "拒絕在可能使用雲端模型的設定下讀取或生成敏感資料"
        )
    return settings


def _resolve_workspace(path: Path) -> Path:
    """Resolve a workspace without allowing writes into protected repo trees."""
    resolved = path.resolve()
    protected_roots = {
        "source": config.SOURCE_DIR.resolve(),
        "outputs": config.OUTPUT_DIR.resolve(),
    }
    for label, protected in protected_roots.items():
        if resolved == protected or protected in resolved.parents:
            raise ValueError(f"agent workspace 不得位於受保護的 {label}/ 目錄")
    return resolved


def _validate_run_name(value: str) -> str:
    """Validate every run name, including values read from owned status files."""
    if re.fullmatch(RUN_NAME_PATTERN, value) is None:
        raise ValueError("run name 格式不合法")
    return value


def _run_dir(workspace: Path, run_name: str) -> Path:
    workspace_root = workspace.resolve()
    runs_path = workspace_root / "runs"
    runs_root = runs_path.resolve()
    if runs_root.parent != workspace_root or (
        runs_path.exists() and runs_path.is_symlink()
    ):
        raise ValueError("workspace/runs 不得是 symlink 或指向 workspace 外")
    candidate = (runs_root / _validate_run_name(run_name)).resolve()
    if candidate.parent != runs_root:
        raise ValueError("run path 必須位於 workspace/runs 內")
    return candidate


def _active_run_name(workspace: Path) -> str:
    status = _read_status(workspace)
    value = status.get("active_run_name")
    if not isinstance(value, str) or not value:
        raise RuntimeError("workspace 尚無成功 run")
    return _validate_run_name(value)


def _page_index(run_dir: Path) -> list[dict[str, Any]]:
    deck = DeckSpecContract.model_validate(_read_json(run_dir / "deckspec.json"))
    pages: list[dict[str, Any]] = []
    for page in deck.pages:
        number = page.section.page_number
        if number is None:
            raise ValueError("DeckSpec 內容頁缺少 page_number")
        pages.append(
            {
                "page_number": number,
                "title": page.section.title,
                "chapter": page.section.chapter,
                "chart_type": page.chart_plan.chart_type,
                "metric_key": page.chart_plan.metric_key,
            }
        )
    if not pages:
        raise ValueError("DeckSpec 沒有可選取的內容頁")
    return sorted(pages, key=lambda item: int(item["page_number"]))


def _selection(run_name: str, page: dict[str, Any]) -> dict[str, Any]:
    return {"run_name": run_name, **page}


def _choose_selection(
    workspace: Path,
    run_name: str,
    pages: list[dict[str, Any]],
    preferred_page: int | None,
) -> dict[str, Any]:
    if preferred_page is not None:
        for page in pages:
            if page["page_number"] == preferred_page:
                return _selection(run_name, page)
    previous = _read_status(workspace).get("selection") or {}
    previous_title = previous.get("title")
    for page in pages:
        if previous_title and page["title"] == previous_title:
            return _selection(run_name, page)
    return _selection(run_name, pages[0])


def _timestamped_run_name(prefix: str) -> str:
    suffix = datetime.now().strftime("%Y%m%d_%H%M%S")
    safe = re.sub(r"[^A-Za-z0-9._-]+", "-", prefix).strip(".-_") or "run"
    return f"{safe[: 63 - len(suffix)]}-{suffix}"[:64]


def _revision_draft(run_name: str) -> dict[str, Any]:
    return {
        "contract_version": CONTRACT_VERSION,
        "base_run_name": run_name,
        "new_run_name": _timestamped_run_name(f"{run_name}-revision"),
        "deck_title": None,
        "sections": None,
        "global_instruction": None,
        "page_revisions": [],
        "preserve_unmentioned_pages": True,
    }


def _agent_guide() -> str:
    return """# Local Private Deck Agent Guide

Initial intent is editable only in `request.json`. After a successful run, follow-up
intent is editable only in `revision.json`. Validate against `schemas/*.schema.json`.

Hard rules:

- Re-read `status/current.json` before every operation. Its `selection` is the page
  meant by “this page”; use `agent-select` to change it.
- Do not modify `schemas/`, `system/`, `status/`, `runs/`, DeckSpec, stage dumps,
  PPTX/XLSX, embedded workbooks, or chart XML.
- The approved XLSX canonical path and SHA-256 are locked in
  `system/approved-input.json`; changing input requires a new workspace.
- Do not add provider, model, endpoint, credential, Excel path, output path,
  MetricStore data, chart data, or invented business values to agent JSON.
- Do not inspect Excel to calculate, transcribe, rank, forecast, or summarize values.
- Keep `preserve_unmentioned_pages` true. A revision may target only a page present
  in its immutable base run.
- Use only `scripts/local_private.ps1` / `tools.local_agent_workspace`. Every run
  must pass local-only provider validation and the single
  `GenerationRequest → generate_deck() → renderer → T1` pipeline.
- `agent-refresh` reuses the locked input and active materialized request; a changed
  workbook requires a new workspace because the input hash is immutable.

Kiro, Copilot, Claude, or Codex being installed locally does not prove local model
inference. Sensitive workspaces may be opened only by an agent deployment explicitly
approved for that data.
"""


def _load_materialized_request(workspace: Path, run_name: str) -> AgentWorkspaceRequest:
    run_dir = _run_dir(workspace, run_name)
    if not (run_dir / "deckspec.json").is_file():
        raise FileNotFoundError(f"base run 不完整或未成功：{run_name}")
    return _load_request(run_dir / "agent-request.json")


def _load_revision_intent(workspace: Path, run_name: str) -> RevisionIntent | None:
    path = _run_dir(workspace, run_name) / "revision-intent.json"
    if not path.is_file():
        return None
    return RevisionIntent.model_validate(_read_json(path))


def _merge_page_revisions(
    previous: RevisionIntent | None,
    current: list[PageRevisionIntent],
    *,
    base_run_name: str,
) -> RevisionIntent | None:
    merged: dict[tuple[int, str], PageRevisionIntent] = {}
    if previous is not None:
        for item in previous.page_revisions:
            merged[(item.target_page_number, item.target_page_title)] = item
    for item in current:
        merged[(item.target_page_number, item.target_page_title)] = item
    if not merged:
        return None
    return RevisionIntent(
        base_run_name=base_run_name,
        page_revisions=list(merged.values()),
        preserve_unmentioned_pages=True,
    )


def _assert_revision_applied(
    output_dir: Path,
    revision_intent: RevisionIntent,
    *,
    policy: str,
) -> None:
    """Fail closed unless every targeted page has semantic approval evidence."""
    review_payload = _read_json(output_dir / "stages" / "05_review.json")
    reviews = review_payload.get("reviews")
    if not isinstance(reviews, list):
        raise RuntimeError("revision run 缺少 reviewer 證據")

    for requested in revision_intent.page_revisions:
        matches = [
            item
            for item in reviews
            if isinstance(item, dict)
            and item.get("section_title") == requested.target_page_title
        ]
        if len(matches) != 1 or matches[0].get("status") != "APPROVED":
            raise RuntimeError(
                "revision 目標頁未取得 reviewer 核准："
                f"{requested.target_page_title}"
            )
        if policy == "required":
            evidence = matches[0]
            if (
                evidence.get("candidate_source")
                not in {"writer", "writer_fallback"}
                or int(evidence.get("reviewer_attempts") or 0) < 1
            ):
                raise RuntimeError(
                    "revision 目標頁只取得未驗證 fallback，拒絕回報成功："
                    f"{requested.target_page_title}"
                )


def _execute_request(
    workspace: Path,
    request: AgentWorkspaceRequest,
    *,
    revision_intent: RevisionIntent | None = None,
    source_revision: dict[str, Any] | None = None,
    preferred_page: int | None = None,
) -> Path:
    settings = _require_local_only()
    excel = _resolve_input(workspace)
    run_name = request.generation.run_name or _timestamped_run_name("run")
    output_dir = _run_dir(workspace, run_name)
    if output_dir.exists():
        raise FileExistsError(f"run 已存在，拒絕覆寫：{output_dir}")

    materialized = request.model_copy(deep=True)
    materialized.generation.run_name = run_name
    if revision_intent is not None and materialized.generation.skip_semantic_review:
        raise ValueError("revision run 不得停用 semantic reviewer")
    _write_status(
        workspace,
        "running",
        pending_run_name=run_name,
        provider=settings.provider,
        endpoint_host=config.endpoint_host(settings.base_url),
    )

    try:
        _write_json(
            output_dir / "agent-request.json",
            materialized.model_dump(mode="json"),
        )
        if source_revision is not None:
            _write_json(output_dir / "revision.json", source_revision)
        if revision_intent is not None:
            _write_json(
                output_dir / "revision-intent.json",
                revision_intent.model_dump(mode="json"),
            )

        ingestion_path = output_dir / "ingestion.json"
        payload = ingest_excel(excel)
        blocked = [
            item.get("dataset_id")
            for item in payload.get("datasets", [])
            if item.get("requires_human_review")
            or item.get("review_status") in {"pending", "rejected"}
        ]
        if blocked:
            raise RuntimeError(
                "資料集尚未通過人工確認 gate：" + "、".join(map(str, blocked))
            )
        save_payload(payload, ingestion_path)

        generation_request = GenerationRequest(
            job_id=f"local-agent-{re.sub(r'[^A-Za-z0-9_-]+', '-', run_name)}",
            prompt=materialized.deck.prompt,
            ingestion_path=str(ingestion_path),
            output_dir=str(output_dir),
            sections=materialized.deck.sections,
            deck_title=materialized.deck.title,
            revision_intent=revision_intent,
            options={
                "policy": materialized.generation.policy,
                "deadline_seconds": materialized.generation.deadline_seconds,
                "render_reserve_seconds": materialized.generation.render_reserve_seconds,
                "use_fake_llm": False,
                "skip_semantic_review": materialized.generation.skip_semantic_review,
            },
        )
        result = generate_deck(generation_request.model_dump(mode="json"))
        if revision_intent is not None:
            _assert_revision_applied(
                output_dir,
                revision_intent,
                policy=materialized.generation.policy,
            )
        if (
            not result.verification_passed
            or result.external_checked != result.series_checked
            or result.series_checked <= 0
            or len(result.artifacts) != 4
        ):
            raise RuntimeError("正式 pipeline 未產生完整四項 artifacts 或 T1 覆蓋")

        pages = _page_index(output_dir)
        _write_json(output_dir / "page-index.json", {"pages": pages})
        selected = _choose_selection(workspace, run_name, pages, preferred_page)
        _write_status(
            workspace,
            "succeeded",
            active_run_name=run_name,
            pending_run_name=None,
            output_dir=str(output_dir),
            selection=selected,
            verification_passed=result.verification_passed,
            series_checked=result.series_checked,
            external_checked=result.external_checked,
            artifacts=[
                {
                    "artifact_type": item.artifact_type,
                    "filename": item.filename,
                    "sha256": item.sha256,
                }
                for item in result.artifacts
            ],
        )
        _write_json(workspace / "revision.json", _revision_draft(run_name))
    except Exception as error:
        _write_status(
            workspace,
            "failed",
            pending_run_name=run_name,
            error_type=type(error).__name__,
        )
        raise

    print(
        f"Generation succeeded: {output_dir}；"
        f"T1={result.external_checked}/{result.series_checked}；"
        f"current=P.{selected['page_number']}"
    )
    return output_dir


def init_workspace(args: argparse.Namespace) -> int:
    workspace = _resolve_workspace(args.workspace)
    if workspace.exists() and any(workspace.iterdir()):
        raise FileExistsError(f"workspace 不是空目錄，拒絕覆寫：{workspace}")
    workspace.mkdir(parents=True, exist_ok=True)

    excel = args.excel.resolve()
    if not excel.is_file() or excel.suffix.lower() != ".xlsx":
        raise FileNotFoundError(f"找不到可用的 XLSX：{excel}")

    request = AgentWorkspaceRequest(
        deck=AgentDeck(prompt=args.prompt, sections=args.sections, title=args.title),
        generation=AgentGeneration(run_name=args.run_name),
    )
    _write_json(
        workspace / "system" / "approved-input.json",
        ApprovedInput(excel_path=str(excel), sha256=_sha256(excel)).model_dump(
            mode="json"
        ),
    )
    _write_json(workspace / "request.json", request.model_dump(mode="json"))
    _write_json(
        workspace / "schemas" / "request.schema.json",
        AgentWorkspaceRequest.model_json_schema(),
    )
    _write_json(
        workspace / "schemas" / "revision.schema.json",
        AgentRevisionRequest.model_json_schema(),
    )
    (workspace / "AGENTS.md").write_text(_agent_guide(), encoding="utf-8")
    _write_status(workspace, "draft")
    print(f"Agent workspace created: {workspace}")
    print(f"Edit: {workspace / 'request.json'}")
    return 0


def validate_workspace(args: argparse.Namespace) -> int:
    workspace = _resolve_workspace(args.workspace)
    settings = _require_local_only()
    request = _load_workspace_request(workspace)
    excel = _resolve_input(workspace)
    print(
        "Workspace valid: "
        f"provider={settings.provider}, endpoint={config.endpoint_host(settings.base_url)}, "
        f"excel={excel.name}, sections={len(request.deck.sections)}"
    )
    return 0


def run_workspace(args: argparse.Namespace) -> int:
    workspace = _resolve_workspace(args.workspace)
    request = _load_workspace_request(workspace)
    _execute_request(workspace, request)
    return 0


def select_page(args: argparse.Namespace) -> int:
    workspace = _resolve_workspace(args.workspace)
    active_run_name = _active_run_name(workspace)
    run_name = args.run_name or active_run_name
    if run_name != active_run_name:
        raise ValueError("current-page cursor 只能選取目前 active run")
    pages = _page_index(_run_dir(workspace, run_name))
    selected = next(
        (page for page in pages if page["page_number"] == args.page),
        None,
    )
    if selected is None:
        available = ", ".join(f"P.{item['page_number']}" for item in pages)
        raise ValueError(f"指定頁面不存在；可選內容頁：{available}")
    status = _read_status(workspace)
    _write_status(
        workspace,
        str(status.get("state") or "succeeded"),
        active_run_name=run_name,
        selection=_selection(run_name, selected),
    )
    print(f"Current page: P.{args.page} {selected['title']} ({run_name})")
    return 0


def revise_workspace(args: argparse.Namespace) -> int:
    workspace = _resolve_workspace(args.workspace)
    revision = _load_revision(workspace)
    base_request = _load_materialized_request(workspace, revision.base_run_name)
    pages = _page_index(_run_dir(workspace, revision.base_run_name))
    pages_by_number = {int(item["page_number"]): item for item in pages}

    current_page_intents: list[PageRevisionIntent] = []
    for item in revision.page_revisions:
        target = pages_by_number.get(item.page_number)
        if target is None:
            raise ValueError(f"revision 指定的 P.{item.page_number} 不存在於 base run")
        current_page_intents.append(
            PageRevisionIntent(
                target_page_number=item.page_number,
                target_page_title=str(target["title"]),
                instruction=item.instruction,
                preferred_chart_type=item.preferred_chart_type,
            )
        )

    previous_intent = _load_revision_intent(workspace, revision.base_run_name)
    if revision.global_instruction is not None:
        previous_by_title = {
            item.target_page_title: item
            for item in (previous_intent.page_revisions if previous_intent else [])
        }
        current_by_number = {
            item.target_page_number: item for item in current_page_intents
        }
        expanded: list[PageRevisionIntent] = []
        for page in pages:
            page_number = int(page["page_number"])
            page_title = str(page["title"])
            previous = previous_by_title.get(page_title)
            current = current_by_number.get(page_number)
            instructions = [
                item
                for item in (
                    previous.instruction if previous is not None else None,
                    revision.global_instruction.strip(),
                    current.instruction if current is not None else None,
                )
                if item
            ]
            expanded.append(
                PageRevisionIntent(
                    target_page_number=page_number,
                    target_page_title=page_title,
                    instruction="\n\n".join(instructions),
                    preferred_chart_type=(
                        current.preferred_chart_type
                        if current is not None
                        else (
                            previous.preferred_chart_type
                            if previous is not None
                            else None
                        )
                    ),
                )
            )
        current_page_intents = expanded

    revision_intent = _merge_page_revisions(
        previous_intent,
        current_page_intents,
        base_run_name=revision.base_run_name,
    )
    materialized = base_request.model_copy(deep=True)
    materialized.generation.run_name = revision.new_run_name
    if revision.deck_title is not None:
        materialized.deck.title = revision.deck_title
    if revision.sections is not None:
        materialized.deck.sections = revision.sections
    if revision.global_instruction is not None:
        materialized.deck.prompt = (
            materialized.deck.prompt.rstrip()
            + "\n\n[Validated follow-up presentation instruction]\n"
            + revision.global_instruction.strip()
        )

    preferred = (
        revision.page_revisions[0].page_number
        if revision.page_revisions
        else None
    )
    _execute_request(
        workspace,
        materialized,
        revision_intent=revision_intent,
        source_revision=revision.model_dump(mode="json"),
        preferred_page=preferred,
    )
    return 0


def refresh_workspace(args: argparse.Namespace) -> int:
    workspace = _resolve_workspace(args.workspace)
    base_run_name = args.base_run_name or _active_run_name(workspace)
    materialized = _load_materialized_request(workspace, base_run_name).model_copy(
        deep=True
    )
    materialized.generation.run_name = args.run_name or _timestamped_run_name(
        f"{base_run_name}-refresh"
    )
    revision_intent = _load_revision_intent(workspace, base_run_name)
    if revision_intent is not None:
        revision_intent = RevisionIntent(
            base_run_name=base_run_name,
            page_revisions=revision_intent.page_revisions,
            preserve_unmentioned_pages=True,
        )
    previous_selection = _read_status(workspace).get("selection") or {}
    preferred = previous_selection.get("page_number")
    _execute_request(
        workspace,
        materialized,
        revision_intent=revision_intent,
        preferred_page=int(preferred) if isinstance(preferred, int) else None,
    )
    return 0


def _parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(
        description="Local-only filesystem agent workspace"
    )
    subparsers = parser.add_subparsers(dest="command", required=True)

    init_parser = subparsers.add_parser("init", help="建立 agent workspace")
    init_parser.add_argument("--workspace", type=Path, required=True)
    init_parser.add_argument("--excel", type=Path, required=True)
    init_parser.add_argument("--prompt", default=DEFAULT_PROMPT)
    init_parser.add_argument(
        "--sections",
        type=_sections,
        default=list(DEFAULT_SECTIONS),
        help="逗號分隔章節",
    )
    init_parser.add_argument("--title", default=None)
    init_parser.add_argument("--run-name", default=None)
    init_parser.set_defaults(handler=init_workspace)

    validate_parser = subparsers.add_parser("validate", help="驗證 request 與隱私設定")
    validate_parser.add_argument("--workspace", type=Path, required=True)
    validate_parser.set_defaults(handler=validate_workspace)

    run_parser = subparsers.add_parser("run", help="初次呼叫唯一正式 pipeline")
    run_parser.add_argument("--workspace", type=Path, required=True)
    run_parser.set_defaults(handler=run_workspace)

    select_parser = subparsers.add_parser("select", help="設定 chat 的目前內容頁")
    select_parser.add_argument("--workspace", type=Path, required=True)
    select_parser.add_argument("--page", type=int, required=True)
    select_parser.add_argument("--run-name", default=None)
    select_parser.set_defaults(handler=select_page)

    revise_parser = subparsers.add_parser("revise", help="套用 revision 並完整重生")
    revise_parser.add_argument("--workspace", type=Path, required=True)
    revise_parser.set_defaults(handler=revise_workspace)

    refresh_parser = subparsers.add_parser("refresh", help="以相同 intent 完整重生")
    refresh_parser.add_argument("--workspace", type=Path, required=True)
    refresh_parser.add_argument("--base-run-name", default=None)
    refresh_parser.add_argument("--run-name", default=None)
    refresh_parser.set_defaults(handler=refresh_workspace)
    return parser


def main(argv: Sequence[str] | None = None) -> int:
    parser = _parser()
    args = parser.parse_args(argv)
    return int(args.handler(args))


if __name__ == "__main__":
    for stream in (sys.stdout, sys.stderr):
        if hasattr(stream, "reconfigure"):
            stream.reconfigure(encoding="utf-8")
    try:
        raise SystemExit(main())
    except Exception as error:  # noqa: BLE001 - never echo sensitive values
        print(
            f"Agent workspace failed: {type(error).__name__}; "
            "details are intentionally suppressed",
            file=sys.stderr,
        )
        raise SystemExit(1) from error
