# LLManage SlideGen Local Private Edition

本分支提供 Ollama/vLLM local-only 正式生成，以及 open-slide 式檔案契約 agent workspace；兩者都沿用唯一正式 pipeline。

## Provider 入口

- Codex 與 provider-neutral agent：`.agents/skills/local-private-deck/SKILL.md`
- GitHub Copilot：`.github/copilot-instructions.md`
- Claude：`CLAUDE.md` 與 `.claude/skills/local-private-deck/SKILL.md`
- Kiro：`.kiro/skills/local-private-deck/SKILL.md`

IDE 或 CLI 安裝在本機不代表模型推論在本機。真正敏感的 workspace 只能交給已明確證實、且組織政策允許讀取該資料的本地 agent；Kiro、Copilot、Claude、Codex 的應用模型邊界與本專案 Ollama runtime 是兩件事。

## Chat deck workflow

1. 每輪先讀 workspace 的 `AGENTS.md`、`schemas/*.schema.json` 與最新 `status/current.json`。
2. 初次生成只改 `request.json`，使用 `agent-validate`、`agent-run`。
3. 「這一頁」由 `status/current.json.selection` 決定；用 `agent-select` 移動 cursor。
4. 後續修改只改 `revision.json`，再使用 `agent-revise`；相同 intent 全量重生使用 `agent-refresh`。
5. 每次 revise/refresh 都建立新 run，完整進入 `GenerationRequest → generate_deck() → renderer → T1`。

## 硬性規則

- `source/` 唯讀；`outputs/` 不得清理或覆寫。
- 不新增第二套 pipeline、engine、MetricStore 或 LLM adapter。
- Agent 不得讀 Excel 自行計算、抄寫、排名、預測或產生業務數值。
- 不得直接修改 PPTX、XLSX、DeckSpec、stage dumps、embedded workbook 或 chart XML。
- Workspace agent 只能修改 `request.json` 與 `revision.json`；不得修改 initializer-owned `schemas/`、`system/`、`status/`、`runs/`。
- Revision 只能承載呈現 intent，不得含 provider、model、endpoint、credential、Excel/output path、MetricStore 或 chart data；`preserve_unmentioned_pages` 必須為 true。
- 圖表與表格只能由正式 renderer 生成；所有輸出必須通過 schema、reviewer、placeholder 與 T1。
- `local_only` 不得回退至任何雲端 provider。
