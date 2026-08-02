---
name: local-private-deck
description: Operate schema-validated local deck workspaces through the formal generation pipeline.
---

# Local Private Deck for Claude

Use this skill only with non-sensitive demo data or when the active Claude deployment is explicitly approved for the data. A local Claude client does not prove local inference.

## Workflow

1. Read `<workspace>/AGENTS.md`, `schemas/request.schema.json`, `schemas/revision.schema.json`, and a fresh `status/current.json`.
2. Initial generation: edit only `request.json`, then run `agent-validate` and `agent-run`.
3. Select the page meant by “this page”:
   `powershell -ExecutionPolicy Bypass -File scripts/local_private.ps1 agent-select -Workspace <workspace> -Page <n>`
4. Follow-up: re-read status, edit only `revision.json`, keep its `base_run_name` equal to the active run, use a new run name, and run `agent-revise`.
5. Refresh the active request without changing intent with `agent-refresh`.
6. Re-read status and report success only from four artifacts plus passing T1 counts.

## Hard boundaries

- Do not inspect Excel to calculate, rank, forecast, summarize, or transcribe values.
- Do not write PPTX/XLSX/chart XML or edit DeckSpec, MetricStore, stage dumps, `system/`, `schemas/`, `status/`, or `runs/`.
- Revision JSON contains presentation intent only; no provider, model, endpoint, credential, Excel path, output path, metric values, chart data, or prose containing invented business numbers.
- Keep `preserve_unmentioned_pages` true. Page targets must exist in the selected base run.
- Never bypass `LLM_PRIVACY_MODE=local_only`; generation must call `tools.local_agent_workspace` and the single production pipeline.
