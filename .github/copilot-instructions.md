# GitHub Copilot instructions — Local Private Edition

This branch supports an open-slide-style chat workflow through versioned files and terminal commands. It does **not** make GitHub Copilot local or private. Before opening a sensitive workbook, prompt, `request.json`, or `revision.json`, confirm that the selected Copilot model and extension policy are approved for that data. Otherwise use only non-sensitive demo data.

For a deck workspace:

1. Read the workspace's `AGENTS.md`, `schemas/*.schema.json`, and a fresh `status/current.json` before every change.
2. Initial intent may be edited only in `<workspace>/request.json`.
3. Follow-up intent may be edited only in `<workspace>/revision.json`.
4. For “this page”, use `status/current.json.selection`; change it with `scripts/local_private.ps1 agent-select -Workspace <path> -Page <n>`.
5. Validate initial intent with `agent-validate`. Apply follow-up intent with `agent-revise`; rerun unchanged intent with `agent-refresh`.
6. Every run must use `tools.local_agent_workspace`, which calls `GenerationRequest → generate_deck()` and the existing renderer/T1 validator.

Never read Excel to calculate or transcribe business values. Never write PPTX/XLSX, MetricStore, stage dumps, embedded workbooks, or chart XML directly. Never add provider/model/endpoint/credential/input/output fields to agent-controlled JSON. Never weaken `LLM_PRIVACY_MODE=local_only` or fall back to a cloud model. A run is successful only when `status/current.json` reports four artifacts and `verification_passed == true` with `external_checked == series_checked > 0`.
