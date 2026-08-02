# Local Private Edition

本文件適用 `feature/local-private-agent-workspace` 分支。目標是在不複製 pipeline、engine、MetricStore 或 LLM adapter 的前提下，提供：

1. Ollama/vLLM local-only 推論模式。
2. 模仿 open-slide 的檔案契約式 agent workspace。

## 隱私範圍

`LLM_PRIVACY_MODE=local_only` 保證程式只接受 `ollama` 或 `vllm`，並在任何 prompt 送出前拒絕公開或未允許的 endpoint。它不等於完整 air-gap：

- FastAPI 正式服務仍依產品架構把輸入、job 與 artifacts 放在 S3。
- 本分支的 workstation CLI/agent workspace 是實驗入口，會把 run 寫在使用者指定的本機 workspace。
- 作業系統、Docker、Ollama、反向代理、IDE extension 與模型檔案仍需由部署者控制。
- 應預先安裝模型；生成期間不要自動下載模型。
- Kiro IDE/CLI 的推論不是 Ollama。敏感資料不可因 Kiro 在本機執行就假設沒有離開裝置。

因此本模式的精確承諾是：**應用程式不會把 LLM prompt 送往允許清單外的模型 endpoint，也不會自動回退雲端 provider。**

## 架構

```text
Local Runtime
  Excel + Prompt
    → backend ingestion
    → GenerationRequest
    → core.generation_orchestrator.generate_deck()
    → ppt_generation.run_pipeline
    → existing llm_client
    → local Ollama/vLLM
    → native renderer + audit XLSX
    → T1

Agent Workspace
  local coding agent
    → edits request.json only
    → schema validation + local-only gate
    → backend ingestion
    → same GenerationRequest / generate_deck()
    → same artifacts and T1
```

Agent workspace 不是 LLM adapter，也不接管 pipeline 內的 section/chart/narrative/reviewer。它只提供一個 provider-neutral 的檔案介面，讓 IDE agent 協助整理 prompt、章節與交付設定。

## 1. 準備 Ollama

請先自行安裝並啟動 Ollama，再確認模型已存在：

```powershell
ollama list
```

應用程式不會執行 `ollama pull`。模型名稱由環境變數指定，不應由 agent job request 控制。

## 2. PowerShell 本機模式

從 repo root 執行。腳本會強制設定 `LLM_PROVIDER=ollama` 與 `LLM_PRIVACY_MODE=local_only`。

```powershell
.\scripts\local_private.ps1 check -Model "<installed-model>"
```

產生簡報：

```powershell
.\scripts\local_private.ps1 generate `
  -Model "<installed-model>" `
  -Excel .\fixtures\data\fsc_114_workbook.xlsx `
  -Prompt "依資料製作高階管理層簡報" `
  -Sections "資料概況,趨勢分析,重點觀察"
```

輸出目錄採時間戳並拒絕覆寫既有檔案。若要明確指定新目錄：

```powershell
.\scripts\local_private.ps1 generate `
  -Excel .\fixtures\data\fsc_114_workbook.xlsx `
  -OutputDir "$env:TEMP\slidegen-local-run"
```

模型 A/B 會呼叫正式 full-pipeline：

```powershell
.\scripts\local_private.ps1 compare -Model "<installed-model>"
```

## 3. Docker Compose + host Ollama

Windows Docker Desktop 的 backend container 透過 `host.docker.internal` 連到 host Ollama：

```powershell
$env:LLM_MODEL_DEFAULT = "<installed-model>"
docker compose -f docker-compose.yml -f docker-compose.local.yml config
docker compose -f docker-compose.yml -f docker-compose.local.yml up --build
```

`docker-compose.local.yml` 只覆寫模型與郵件模式；正式 FastAPI/S3 job 架構保持不變。若 Ollama 不在 host，設定受控內網 endpoint 並把 hostname 加到明確 allowlist：

```powershell
$env:LLM_BASE_URL = "http://private-llm.internal:11434/v1"
$env:LLM_LOCAL_ENDPOINT_ALLOWLIST = "private-llm.internal"
```

不要加入公開網域或萬用字元。

## 4. Agent workspace

建立 workspace：

```powershell
.\scripts\local_private.ps1 agent-init `
  -Excel .\fixtures\data\fsc_114_workbook.xlsx `
  -Workspace .\agent-workspaces\fsc-demo
```

產生的結構：

```text
agent-workspaces/fsc-demo/
├── AGENTS.md
├── request.json                 初次 intent，agent 可編輯
├── revision.json                第一次成功 run 後建立，後續 intent 可編輯
├── schemas/
│   ├── request.schema.json
│   └── revision.schema.json
├── system/
│   └── approved-input.json      初始化器鎖定的 canonical path + SHA-256
├── status/
│   └── current.json             active run + current-page cursor
└── runs/<run-name>/             immutable run；不得由 agent 修改
    ├── agent-request.json
    ├── revision.json            revise run 才有
    ├── revision-intent.json     pipeline 使用的累積 page intent
    ├── page-index.json
    ├── deckspec.json
    ├── stages/
    └── 四項正式 artifacts
```

初次生成只修改 `request.json`：

```json
{
  "contract_version": "1.0",
  "deck": {
    "prompt": "依資料製作高階管理層簡報",
    "sections": ["資料概況", "趨勢分析", "重點觀察"],
    "title": "管理報告"
  },
  "generation": {
    "policy": "required",
    "deadline_seconds": 1500,
    "render_reserve_seconds": 240,
    "skip_semantic_review": false,
    "run_name": "management-report-v1"
  }
}
```

```powershell
.\scripts\local_private.ps1 agent-validate -Workspace .\agent-workspaces\fsc-demo
.\scripts\local_private.ps1 agent-run -Workspace .\agent-workspaces\fsc-demo
```

成功後，`status/current.json` 會保存 `active_run_name` 與 `selection`。Chat 中說「這一頁」前，可先設定 cursor：

```powershell
.\scripts\local_private.ps1 agent-select `
  -Workspace .\agent-workspaces\fsc-demo `
  -Page 3
```

Agent 每輪重新讀取 status，再修改 `revision.json`。頁碼必須存在於 base run 的 `page-index.json`；工具會自行鎖定該頁 title，agent 不需也不得提供 chart data 或數值：

```json
{
  "contract_version": "1.0",
  "base_run_name": "management-report-v1",
  "new_run_name": "management-report-v2",
  "deck_title": null,
  "sections": null,
  "global_instruction": null,
  "page_revisions": [
    {
      "page_number": 3,
      "instruction": "將洞察改成更適合高階主管快速決策的表達，保留可追溯性。",
      "preferred_chart_type": "bar"
    }
  ],
  "preserve_unmentioned_pages": true
}
```

```powershell
.\scripts\local_private.ps1 agent-revise -Workspace .\agent-workspaces\fsc-demo
```

Revision 不是 patch PPT。它被驗證後轉成 `GenerationRequest.revision_intent`，依 base DeckSpec 綁定頁碼/標題，正式 chart/writer/reviewer 只收到該頁指令，然後 renderer 與 T1 全量重跑；原 run 永不覆寫。`preserve_unmentioned_pages` 表示 page-scoped revision 指令不會擴散到未指定頁；因正式 pipeline 會全量重生，它不承諾其他頁的措辭或檔案位元完全不變。`global_instruction` 會由工具展開成 base run 的每一個內容頁，確保全頁都進入相同 revision evidence gate。Revision run 強制啟用 semantic reviewer；指定頁若只取得 deterministic fallback 或沒有 reviewer 核准證據，`agent-revise` 會 fail closed，不會把未套用修改的 run 回報為成功。若只要用 active request 與鎖定輸入重新生成：

```powershell
.\scripts\local_private.ps1 agent-refresh -Workspace .\agent-workspaces\fsc-demo
# 或明確命名新 run
.\scripts\local_private.ps1 agent-refresh `
  -Workspace .\agent-workspaces\fsc-demo `
  -RunName management-report-refresh-1
```

Refresh 不允許靜默接受被修改的 Excel。輸入 SHA-256 若不同會 fail closed；資料更新時建立新 workspace，重新鎖定輸入。

Agent-controlled JSON 刻意沒有以下欄位：

- provider/model/base URL/API key
- Excel path、input hash 或任意 output directory
- MetricStore、預先計算數值或 chart data
- chart XML、PPTX/XLSX 路徑

每次操作後重新讀取 `status/current.json`。只有同時符合以下條件才算成功：

- `state == "succeeded"`
- `verification_passed == true`
- `external_checked == series_checked > 0`
- artifacts 包含 PPTX、XLSX、verification JSON、generation manifest

相同 `run_name` 不可重跑，以避免覆寫 artifacts。

## 5. Coding agent 使用限制

Provider-specific chat instructions：

- GitHub Copilot：`.github/copilot-instructions.md`
- Codex/provider-neutral agent：`AGENTS.md`、`.agents/skills/local-private-deck/SKILL.md`
- Claude：`CLAUDE.md`、`.claude/skills/local-private-deck/SKILL.md`
- Kiro：`.kiro/skills/local-private-deck/SKILL.md`
- 每個 workspace 自己的 `AGENTS.md`

它們提供相同的 filesystem workflow，而不是直接串接 IDE session API。可在聊天視窗使用這類指令：

```text
請先讀 agent-workspaces/fsc-demo/status/current.json 和兩份 schema，告訴我目前選到哪一頁，不要讀 Excel。

請把目前頁改成更適合高階主管決策的敘事，偏好 bar；只修改 revision.json，先顯示預計 base/new run，再執行 agent-revise。

請將 current cursor 移到 P.5，不要修改任何簡報內容。

請用目前 active request 做 Refresh；不可改輸入鎖、不可覆寫既有 run，完成後回報四項 artifacts 與 T1。
```

Kiro、Copilot、Claude、Codex 的 IDE/CLI 本身都不因安裝在本機而自動變成 Ollama。真正敏感的 request/Excel 應只交給已證實且組織政策允許的本地 coding agent。

Agent 不得：

- 修改 `system/approved-input.json`；要換 Excel 必須建立新 workspace。
- 讀 Excel 後自行計算或抄寫數值。
- 直接生成 PPTX、XLSX、圖片式圖表或 chart XML。
- 修改 engine/MetricStore/stage output。
- 繞過 reviewer、placeholder resolver 或 T1。
- 把 endpoint、credential 或 provider 寫入 request。
- 因本地模型失敗而切換至雲端模型。

## 6. Local-only gate

允許的 provider：

```text
ollama
vllm
```

Credential 隔離：

- Ollama 永遠使用 SDK placeholder，不讀取或傳送 `LLM_API_KEY`、`OPENAI_API_KEY`、Google key 或 key file。
- 受控內網 vLLM 若需要認證，只能使用 `LLM_LOCAL_API_KEY`。
- `scripts/local_private.ps1` 會在啟動 Ollama 前清除 ambient `LLM_API_KEY`。

預設允許的 hostname：

```text
localhost
host.docker.internal
ollama
vllm
loopback/private/link-local IP
```

其他內網 hostname 必須逐一加入 `LLM_LOCAL_ENDPOINT_ALLOWLIST`，而且 DNS 解析出的每一個位址仍須是 private/loopback/link-local；驗證後 URL 會改寫成已核准的私有 IP，讓 SDK 連線時不會再次解析 DNS。Local-only HTTP client 也會停用環境 proxy 與 redirect，避免 prompt 經代理或重新導向離開核准 endpoint。HTTPS hostname 因 pinning 後無法安全保證憑證/SNI，local-only 模式要求改用受控 HTTP 內網端點或憑證涵蓋的私有 IP。allowlist 不能把公開 DNS 名稱偽裝成本地 endpoint，也不要加入公開網域或萬用字元。

以下會在 LLM 呼叫前失敗：

- `LLM_PROVIDER=openai/google/bedrock/litellm`
- 公開 IP
- 未列入 allowlist 的 hostname
- URL 內嵌帳號或密碼
- 缺少 `http://` 或 `https://`

Generation manifest 只記錄 endpoint hostname，不記錄 URL credential/path、prompt 原文或模型完整回覆。

## 7. 驗證

不呼叫外部模型的完整 deterministic gate：

```powershell
.\scripts\local_private.ps1 verify
```

真實本地模型則使用：

```powershell
.\scripts\local_private.ps1 check -Model "<installed-model>"
.\scripts\local_private.ps1 compare -Model "<installed-model>"
```

`check` 只驗 JSON 連線；`compare` 才會從真 Excel 跑到 renderer 與 T1。

## 8. 常見問題

### Container 連不到 Ollama

容器內的 `localhost` 是 backend container，不是 Windows host。使用 local compose override 的 `host.docker.internal`。

### Ollama 沒有 API key

這是正常情況。OpenAI SDK 內部使用 placeholder key，但 local health check 不會要求憑證。

### 模型不支援 tool calling/JSON mode

保留保守設定：

```text
LLM_TOOL_MODE=json
LLM_JSON_MODE=prompt
LLM_SYSTEM_MODE=merge
```

若實測證明特定模型完整支援，再以環境變數逐項改成 `native`。

### 想完全 air-gap

除了 local-only LLM，還必須另外處理 S3、套件下載、模型下載、OS/IDE telemetry、DNS、容器 registry 與郵件。這不在 `LLM_PRIVACY_MODE` 的保證範圍內。
