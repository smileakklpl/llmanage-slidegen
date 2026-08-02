---
name: local-private-deck
description: 操作 Local Private Edition 的 current-page、revision 與 Refresh；只適用非敏感示範或使用者已接受 Kiro 模型邊界時。
---

# Local Private Deck

Kiro 可以在聊天視窗協助修改 schema-validated intent 並執行正式生成，但 Kiro 本身不是 Ollama runtime。敏感 Excel、prompt、request 或 revision 不可因 Kiro 在本機執行就假設沒有離開裝置。

## 不可違反

- 不讀 Excel 計算、轉錄、排名、預測或摘要數值。
- 不直接修改 PPTX、XLSX、DeckSpec、MetricStore、stage dumps、embedded workbook 或 chart XML。
- 不修改 workspace 的 `schemas/`、`system/`、`status/`、`runs/`。
- 初次 intent 只能改 `request.json`；後續 intent 只能改 `revision.json`。
- revision 禁止 provider、model、endpoint、credential、Excel/output path、metric values 與 chart data，且 `preserve_unmentioned_pages` 必須為 true。
- 執行只能走 `tools.local_agent_workspace`，完整進入 `GenerationRequest → generate_deck() → renderer → T1`。
- 不得關閉或繞過 `LLM_PRIVACY_MODE=local_only`。

## Chat 操作

```powershell
.\scripts\local_private.ps1 agent-init -Excel <xlsx> -Workspace <workspace>
.\scripts\local_private.ps1 agent-validate -Workspace <workspace>
.\scripts\local_private.ps1 agent-run -Workspace <workspace>
.\scripts\local_private.ps1 agent-select -Workspace <workspace> -Page <n>
# 依 schema 編輯 revision.json
.\scripts\local_private.ps1 agent-revise -Workspace <workspace>
.\scripts\local_private.ps1 agent-refresh -Workspace <workspace>
```

每輪先重新讀取 `status/current.json`；「這一頁」就是 `selection`。Revision 的 `base_run_name` 使用 `active_run_name`，`new_run_name` 必須全新。完成後只以四項 artifacts、`verification_passed` 與 `external_checked == series_checked > 0` 判斷成功。
