---
name: local-private-deck
description: Operate a Local Private Edition deck through current-page selection, schema-validated revisions, and the formal pipeline.
---

# Local Private Deck

Use this skill only when the coding agent itself is approved for the data. An IDE running locally does not prove local inference.

## Hard boundaries

- Never inspect Excel to calculate, transcribe, rank, forecast, or summarize values.
- Never write PPTX/XLSX/chart XML or edit DeckSpec, MetricStore, stage dumps, `schemas/`, `system/`, `status/`, or `runs/`.
- Initial intent is editable only in `request.json`; follow-up intent only in `revision.json`.
- Agent JSON must not contain provider, model, endpoint, credential, Excel/output path, metric values, or chart data.
- Keep `preserve_unmentioned_pages` true. Generation must call `tools.local_agent_workspace`, which delegates to `GenerationRequest → generate_deck()`.
- Never weaken `LLM_PRIVACY_MODE=local_only` or fall back to cloud.

## Chat workflow

```powershell
# initialize, validate, and generate
.\scripts\local_private.ps1 agent-init -Excel <xlsx> -Workspace <workspace>
.\scripts\local_private.ps1 agent-validate -Workspace <workspace>
.\scripts\local_private.ps1 agent-run -Workspace <workspace>

# set the cursor used by “this page”
.\scripts\local_private.ps1 agent-select -Workspace <workspace> -Page <n>

# edit revision.json, then regenerate through the full pipeline
.\scripts\local_private.ps1 agent-revise -Workspace <workspace>

# rerun the active materialized request and locked input unchanged
.\scripts\local_private.ps1 agent-refresh -Workspace <workspace>
```

Before every edit, re-read `<workspace>/status/current.json`. Use `selection.page_number` and `selection.title` for “this page”, and copy `active_run_name` into `revision.json.base_run_name`. Use a unique `new_run_name`. After execution, re-read status; report success only when four artifacts exist and `verification_passed == true` with `external_checked == series_checked > 0`.
