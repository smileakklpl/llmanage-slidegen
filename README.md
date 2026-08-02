# llmanage-slidegen — Local Private Edition

「智匯數據簡報神器」把一段自然語言需求與一份 Excel 報表，轉成**圖表可右鍵編輯的 PowerPoint**、一份與圖表逐頁對應的稽核 Excel，以及一份數值驗證報告。主要應用場景是金融管理報表，但 ingestion 與確定性 metric engine 不以金融分類為輸入門檻（另有餐飲、旅遊、股價的跨領域測試）。

- 環境設定 → [快速開始](#快速開始)
- 執行範例 → [執行方式](#執行方式)
- 重現驗收數字 → [Benchmark 與驗收重現](#benchmark-與驗收重現)
- 資料／模型／版本基準 → [版本與重現性基準](#版本與重現性基準)
- 架構與資料流程 → [`docs/系統架構與資料流程.md`](docs/系統架構與資料流程.md)

---

> 本文件位於功能分支 `feature/local-private-agent-workspace`。此分支不取代 AWS `main` 版本；它在相同正式 pipeline 上加入 Ollama/vLLM local-only 推論與檔案契約式 agent workspace。完整說明見 [`docs/Local_Private_Edition.md`](docs/Local_Private_Edition.md)。

## 正式流程

```text
FastAPI POST /api/v1/jobs/generate
  → 上傳檔案與 job 狀態落 S3
  → backend worker（app/worker/generation_job_runner.py）
  → core.generation_orchestrator.generate_deck()
  → ppt_generation.run_pipeline
  → backend ingestion → deterministic metric engine
  → section / chart / narrative / reviewer agents
  → native PPT charts/tables + audit XLSX
  → T1 三方數值驗證
  → PPTX / XLSX / verification JSON / generation manifest 回存 S3
```

產品碼只有這一條 generation 路徑。**LLM 僅負責結構化意圖、敘事與摘要**；所有數值、排名、成長率、預測與圖表資料均由確定性程式計算。圖表一律透過 `python-pptx` 的 `add_chart()` 建立，因此保留 PowerPoint 內嵌 workbook（右鍵「編輯資料」可開啟）。

---

## Local Private Edition 快速開始

本分支把兩個模式放在同一套正式生成邊界上：

- **Local Runtime**：既有 `llm_client` 直接呼叫 Ollama/vLLM，`local_only` 會拒絕雲端 provider 與未允許 endpoint。
- **Agent Workspace**：模仿 open-slide，以 `AGENTS.md`、skill、`request.json`、JSON Schema 與 `status/current.json` 協作；agent 不能直接生成 PPT 或數值。

Windows PowerShell：

```powershell
# Ollama 與模型須事先安裝
.\scripts\local_private.ps1 check -Model "<installed-model>"

# 直接走正式 pipeline
.\scripts\local_private.ps1 generate `
  -Model "<installed-model>" `
  -Excel .\fixtures\data\fsc_114_workbook.xlsx

# 建立 open-slide 式 agent workspace
.\scripts\local_private.ps1 agent-init `
  -Excel .\fixtures\data\fsc_114_workbook.xlsx `
  -Workspace .\agent-workspaces\demo

# 初次生成
.\scripts\local_private.ps1 agent-validate -Workspace .\agent-workspaces\demo
.\scripts\local_private.ps1 agent-run -Workspace .\agent-workspaces\demo

# Chat 中的「這一頁」由 current-page cursor 決定
.\scripts\local_private.ps1 agent-select -Workspace .\agent-workspaces\demo -Page 3

# 依 schema 編輯 revision.json 後，完整重跑正式 pipeline + T1
.\scripts\local_private.ps1 agent-revise -Workspace .\agent-workspaces\demo

# 不改 intent，以 active run + 鎖定輸入全量重生
.\scripts\local_private.ps1 agent-refresh -Workspace .\agent-workspaces\demo
```

VS Code/Kiro Chat 的整合不是直接操作 PowerPoint，而是讓 Copilot、Codex、Claude 或 Kiro 依各自 instructions 修改 `request.json` / `revision.json`，再由上述命令產生新的 immutable run。相關入口為 `.github/copilot-instructions.md`、`AGENTS.md`、`CLAUDE.md`、`.claude/skills/` 與 `.kiro/skills/`。

Kiro、Copilot、Claude 或 Codex 安裝在本機不代表推論使用本地 Ollama。敏感 workspace 只能交給已證實且政策允許的本地 coding agent。完整隱私邊界、current-page/revision contract、Docker 操作與 chat prompts 見 [`docs/Local_Private_Edition.md`](docs/Local_Private_Edition.md)。

## 快速開始

### 先決條件

| 項目 | 版本 | 說明 |
|---|---|---|
| Python | **3.12**（實測 3.12.4） | `requirements.lock` 的基準環境 |
| Node.js | **20**（前端 Docker 用 node:20-alpine） | 只有要跑前端開發伺服器時需要 |
| Docker / Compose | 任一近期版本 | 部署與整合測試 |
| AWS 帳號 | 選用 | 僅 S3 落檔、SES 寄信、Bedrock 模型需要 |

CLI 端到端流程**不需要 AWS，也不需要任何模型金鑰**（用 `--fake-llm`）。

### 安裝

從 repo root 執行。macOS / Linux：

```bash
python3.12 -m venv .venv
.venv/bin/python -m pip install -r requirements.lock          # 完整鎖定（建議）
.venv/bin/python -m pip install -r src/backend/requirements.txt
```

Windows PowerShell：

```powershell
py -3.12 -m venv .venv
.\.venv\Scripts\python.exe -m pip install -r requirements.lock
.\.venv\Scripts\python.exe -m pip install -r src\backend\requirements.txt
```

三份依賴檔的分工：

| 檔案 | 內容 | 維護方式 |
|---|---|---|
| `requirements.txt` | 主管線**直接** import 的套件，含選型理由註解 | 人工 |
| `src/backend/requirements.txt` | backend 直接 import 的套件 | 人工 |
| `requirements.lock` | **完整傳遞依賴**（65 個套件），重現環境用 | `pip freeze` 產生，勿手改 |

要精確重現驗收數字請裝 `requirements.lock`；只裝 `requirements.txt` 時間接依賴由 pip 當下解析，可能拿到不同組合。

選用的 OCR（掃描 PDF／圖片報表）另外裝：

```bash
.venv/bin/python -m pip install -r src/backend/requirements-ocr.txt
```

`paddlepaddle` 不在該檔內，需從 PaddlePaddle 自己的 CPU index 安裝（見 `src/backend/Dockerfile`）。程式碼把 OCR 當選用：`visual_parser.py` 在函式內才 import，缺套件會提示安裝指令，整套測試注入 FakeEngine 不碰真引擎。

### 環境變數

**完全不設也能跑** CLI 的 `--fake-llm` 流程。以下依用途分組，預設值取自程式碼。

生成行為（`src/ppt_generation/core/config.py`）：

| 變數 | 預設 | 說明 |
|---|---|---|
| `GENERATION_POLICY` | `required` | `strict`=退件即停；`required`=期限內必產出 |
| `GENERATION_DEADLINE_SECONDS` | `1500` | 全案 SLA（25 分鐘），自 job 落庫回 202 起算 |
| `GENERATION_RENDER_RESERVE_SECONDS` | `240` | 保留給 render/verify，不得 ≥ deadline |
| `GENERATION_OUTPUT_RESERVE_SECONDS` | `150` | 保留給產出上傳 |
| `GENERATION_DEFAULT_CONTENT_PAGES` | `8` | 預設內容頁數 |
| `GENERATION_MAX_CONTENT_PAGES` | `15` | 內容頁上限，**程式硬上限 15，只能往下調** |
| `GENERATION_USE_FAKE_LLM` | `false` | 走內建假回應，零模型呼叫 |
| `GENERATION_SKIP_SEMANTIC_REVIEW` | `false` | 只跑審查規則層 |
| `LLM_REPAIR_ESCALATE_AFTER` | `2` | 修正幾次後升級到 fallback 模型 |

模型路由（同檔，**切後端不需改任何程式碼**）：

| 變數 | 預設 | 說明 |
|---|---|---|
| `LLM_PROVIDER` | `openai` | `openai` / `google` / `bedrock` / `vllm` / `ollama` / `litellm` |
| `LLM_BASE_URL` | 依 provider | ollama→`:11434/v1`、vllm→`:8000/v1`、google→Gemini OpenAI 相容層 |
| `LLM_MODEL_DEFAULT` | `gpt-4o-mini` | 全案預設模型 |
| `LLM_MODEL_INTENT` / `_CHART` / `_WRITER` / `_WRITER_FALLBACK` / `_WRITER_KEYPAGES` / `_REVIEWER` / `_MAILER` | 回退 default | per-stage 路由；免費額度常是 per-model per-day，分散才跑得完 |
| `LLM_MAX_PARALLEL` | `16`（下限 4） | 敘事平行度 |
| `LLM_RPM_LIMIT` | `24` | 每個 provider/region/model 的請求啟動預算 |
| `LLM_TIMEOUT_SECONDS` | `60` | 單次請求逾時 |
| `LLM_BACKOFF_BASE` | `1.0` | 重試退避基數（秒） |
| `LLM_TOOL_MODE` / `LLM_JSON_MODE` / `LLM_SYSTEM_MODE` | 依模型自動判斷 | 模型不支援原生 tool calling／JSON 模式／system role 時的降級開關 |

金鑰查找順序：`LLM_API_KEY` → `OPENAI_API_KEY` → `GOOGLE_API_KEY` → `GEMINI_API_KEY`，都沒有才回退讀 `.venv/api_key/key.txt`。**Bedrock 走 AWS SDK 簽章，不需要 API key，但必須顯式給 `AWS_REGION`**。

服務與儲存（`src/backend/app/core/config.py`，支援 `.env`）：

| 變數 | 預設 | 說明 |
|---|---|---|
| `S3_BUCKET` | `""` | 空值時 `/ready` 不會通過 |
| `AWS_REGION` | `us-west-2` | 所有 AWS client 顯式指定，禁止依賴環境預設 |
| `S3_ENDPOINT_URL` | `None` | 指向 MinIO 等 S3 相容端點時使用 |
| `S3_PRESIGN_EXPIRES_SECONDS` | `3600` | 下載連結有效期（60–604800） |
| `CORS_ORIGINS` | `["http://localhost:5173"]` | |
| `EMAIL_PROVIDER` | `mock` | `mock` 不真的寄出；`ses` 走 AWS SES |
| `SES_SENDER_EMAIL` | — | SES 寄件人，需為已驗證地址 |
| `JWT_SECRET_KEY` | **隨機產生** | 未設定時每次啟動都變，既有 token 全失效，部署務必顯式指定 |
| `JWT_EXPIRE_MINUTES` | `1440` | |
| `AUTH_USERS_FILE` | `config/users.json` | |
| `OCR_MAX_SECONDS` / `OCR_MAX_PAGES` | `360` / `20` | OCR 軟預算，逾時保留已完成頁面 |
| `GENERATION_MAX_CONCURRENT_JOBS` | `1` | |

> **已知不一致**：`AWS_REGION` 在 `core/config.py` 預設 `us-west-2`，但 `services/s3_service.py` 與 `services/email_service.py` 各自回退 `us-east-1`。上傳大小上限也有三處不同值（`ingestion/settings.py` 50MB、`ingestion/router.py` 硬編 25MB、nginx 50M）。部署時請顯式設定 `AWS_REGION`，並以 25MB 為實際可用上限。

---

## 執行方式

### 1. 本地 CLI（不需 AWS、不呼叫模型）

CLI 與 API 走**同一套** `ppt_generation.run_pipeline`，不是另一份 engine。

`run_pipeline` 只吃 ingestion JSON，因此 Excel 要先過一次 backend ingestion bridge：

```bash
OUT=$(mktemp -d)
PYTHONPATH=src:src/backend .venv/bin/python -c "
from app.ingestion import generation_bridge
generation_bridge.save_payload(
    generation_bridge.ingest_excel('fixtures/data/fsc_114_workbook.xlsx'),
    '$OUT/ingestion.json')
"

cd src && ../.venv/bin/python -m ppt_generation.run_pipeline \
    --ingestion "$OUT/ingestion.json" \
    --title "信用卡市場分析與經營洞察" \
    --prompt "依資料產出管理層簡報" \
    --fake-llm --skip-semantic-review \
    --output-dir "$OUT/deck"
```

> `--sample`（內建範例資料）**目前無法使用**：內建 payload 缺少 `contract_version`，
> 會在 Stage 1-3 拋 `IngestionPayloadError: 不支援的 ingestion contract_version：None`
> （`data/dataset_loader.py:236`）。ingestion 契約加上版號後，這條路徑沒有同步更新。
> 請改用上面的 ingestion bridge 方式。

產出檔名依**來源檔名**推導，不是固定的 `deck.pptx`：

```text
<output-dir>/
├── fsc_114_workbook-分析簡報.pptx     簡報
├── fsc_114_workbook-分析資料.xlsx     FR-3 稽核 Excel
├── generation_manifest.json           產出清單與 sha256
├── deckspec.json                      重生成用 spec
└── stages/
    ├── 00_ingestion.json … 06_render.json   各階段中間結果
    └── 07_verify.json                        T1 三方比對結果
```

`--no-stage-dump` 可關閉 `stages/`。注意 **T1 結果在 CLI 路徑寫在 `stages/07_verify.json`**；獨立的 `verification.json` 只有走 orchestrator／API 那條路徑才會產生（見 `core/generation_orchestrator.py`），這也是 `verify_all.py` 斷言的四項 artifacts 之一。

主要旗標（完整清單見 `--help`）：

| 旗標 | 說明 |
|---|---|
| `--ingestion <path>` / `--sample` | 資料來源，**兩者必須且只能擇一**（`--sample` 目前失效，見上方說明） |
| `--prompt` / `--title` / `--sections` | 需求描述、封面標題、指定章節（給定章節可避免規劃階段要求確認） |
| `--fake-llm` | 內建假回應，零模型呼叫 |
| `--skip-semantic-review` | 只跑審查規則層，省一次模型呼叫 |
| `--stage <name>` | 跑到指定階段就停 |
| `--list-stages` | 列出階段與各階段是否需要模型 |
| `--generation-policy` / `--deadline-seconds` / `--render-reserve-seconds` | 覆寫交付政策與時間預算 |
| `--check-llm` | 只檢查模型設定與連線 |
| `--verbose` | 顯示重試等細節 |

七個階段（`--stage` 的可選值）：

```text
1. metrics     Stage 1-3 資料讀取與指標計算   [純確定性]
2. sections    Stage 4-1 章節規劃            [需要 LLM]
3. charts      Stage 4-2 圖表決策            [需要 LLM]
4. narratives  Stage 4-3 敘事撰寫            [需要 LLM]
5. review      Stage 4-4 審查                [需要 LLM]
6. render      Stage 5-6 產出檔案            [純確定性]
7. verify      Stage 7  三方數值比對          [純確定性]
```

> `scripts/generate_deck.py` 是另一支便利 CLI，直接吃 `--excel`（可為檔案或目錄），預設輸出到 `outputs/<slug>_bedrock_<timestamp>/`。它會拒絕寫入非空目錄。正式流程仍以 `run_pipeline` 為準。

### 2. Docker Compose（正式服務）

```bash
export AWS_REGION=us-west-2
export S3_BUCKET=llmanage-slidegen-files
export JWT_SECRET_KEY="$(openssl rand -base64 32)"
docker compose up --build
```

前端在 `http://localhost`（nginx 代理 `/api/`、`/auth/`、`/ingestion/` 到 backend:8000），backend 不對外開 port，只 `expose 8000`。

本機 Docker 會轉傳 `AWS_ACCESS_KEY_ID`、`AWS_SECRET_ACCESS_KEY`、`AWS_SESSION_TOKEN`。部署到 EC2 時建議改用 Instance Profile：`boto3` 會自動經 IMDS 取得臨時憑證，compose 裡那三個變數留空即可。若走 IMDSv2 且請求需多跳一層（容器 → host → IMDS），要把 hop limit 設為 2。

寄信預設 `EMAIL_PROVIDER=mock`（不真的寄出）。要走 SES 需 `ses:SendRawEmail` 權限，且 sandbox 帳號的收件地址也必須先驗證。

> 命題指定的 MailHog 模擬信箱**尚未納入 compose stack**，這是已知缺口。

API 端點：

| Method | Path | 認證 | 說明 |
|---|---|---|---|
| `POST` | `/api/v1/jobs/generate` | — | multipart `files` + `prompt`（+ 選填 `template`），回 `202` 與 `job_id` |
| `GET` | `/api/v1/jobs/{job_id}` | — | 輪詢狀態與 artifacts（`download_url` 每次重新簽章） |
| `GET` | `/api/v1/jobs/{job_id}/review` | — | 取得待人工複核的 dataset 與來源預覽 |
| `POST` | `/api/v1/jobs/{job_id}/datasets/{dataset_id}/review` | — | 核可／退回／修正單一 dataset |
| `POST` | `/api/v1/jobs/{job_id}/resume` | — | 全部 dataset 通過後續跑（`202`） |
| `POST` | `/api/v1/jobs/{job_id}/send` | **JWT** | 依 `EMAIL_PROVIDER` 寄送 artifacts |
| `GET` | `/api/v1/downloads/{job_id}/{filename}` | — | 授權後 307 轉址到新簽的 S3 URL |
| `POST` | `/auth/login`、`/auth/register` | — | 取得 JWT |
| `POST` | `/ingestion/inspect`、`/inspect-excel-content`、`/extract-excel-tables`、`/validate-excel-data`、`/process`、`/review-dataset` | — | ingestion 互動端點 |
| `GET` | `/health`、`/ready`、`/api/v1/health` | — | `/ready` 另檢查 orchestrator 可呼叫與 S3 設定 |

> **安全性現況**：目前只有 `/jobs/{id}/send` 要求 JWT。生成、ingestion、下載端點皆未認證——下載端點有「key 必須屬於該 job」的授權檢查，但任何知道 `job_id` 的人都能取得該 job 的產出。對外部署前應補上認證。

### 3. 真實模型

模型後端完全由環境變數切換：

```bash
# AWS Bedrock
export LLM_PROVIDER=bedrock
export AWS_REGION=us-west-2
export LLM_MODEL_DEFAULT=<bedrock-model-id>

# 或 Google Gemini（OpenAI 相容層）
export LLM_PROVIDER=google
export GOOGLE_API_KEY=<key>
export LLM_MODEL_DEFAULT=<gemini-model>

# 或地端
export LLM_PROVIDER=ollama          # 自動指向 http://localhost:11434/v1
```

先確認連線再跑完整流程：

```bash
cd src && ../.venv/bin/python -m ppt_generation.run_pipeline --check-llm
```

Bedrock 需要三件事同時成立：IAM 有 `bedrock:InvokeModel` 與 `bedrock:Converse`、該 model 已在 Bedrock console 完成 access request、model 在指定 region 可用。可用 `aws bedrock list-foundation-models --region <region>` 核對 model id。

---

## Benchmark 與驗收重現

### 合併前唯一關卡

```bash
PYTHONIOENCODING=utf-8 .venv/bin/python scripts/verify_all.py
```

四道關卡，全程使用系統暫存目錄，**不寫入 `outputs/`、不呼叫外部模型**：

1. **root 測試** —— `pytest tests -q`
2. **backend 測試** —— 在 `src/backend/` 執行 `pytest -q`
3. **分層依賴掃描** —— 靜態掃 `src/`：任何檔案不得 `import tools`；backend 不得 import `ppt_generation`；core 不得 import `app`/`backend`；ppt_generation 不得 import `app`/`backend`/`core`
4. **正式端到端管線** —— 以版控內的真 Excel + store-aware fake LLM 呼叫 `GenerationRequest → generate_deck()`，斷言四項 artifacts 存在且非空、`verification_passed is True`、`series_checked > 0`、`external_checked == series_checked`、`slide_count >= page_count`、`chart_count > 0`

本次提交的實測結果（`c42bafd`，Python 3.12.4 / macOS arm64 / `requirements.lock`）：

| 關卡 | 結果 |
|---|---|
| root 測試 | **189 passed** |
| backend 測試 | **114 passed, 2 skipped** |
| 分層依賴掃描 | 通過（`src/` 100 支檔案） |
| 正式端到端管線 | 18 slides / 8 charts；**T1 21/21**；4 artifacts |

兩項 skip 是缺少未進版控的本機驗收活頁簿（Amkor／ASE），非失敗。

### 三方數值一致性（T1）

同一組數字在系統中有三份副本，T1 逐格比對三者：

| # | 副本 | 產生時機 |
|---|---|---|
| ① | Chart XML 快取值（畫面顯示） | `add_chart()` |
| ② | 內嵌 workbook（右鍵編輯資料） | 同一次 `add_chart()` |
| ③ | FR-3 外部稽核 `.xlsx` | 獨立輸出路徑，同一份 `ChartSpec` |

①② 由同一次呼叫保證一致；③ 因輸入源頭相同（同一個 `MetricStore`）而一致。原生表格沒有內嵌 workbook，改以「儲存格文字 ↔ 稽核 Excel」反向解析比對。

單獨重現：

```bash
cd src && ../.venv/bin/python -m ppt_generation.run_pipeline \
    --ingestion "$OUT/ingestion.json" --fake-llm --skip-semantic-review \
    --output-dir "$OUT/deck" --stage verify
```

結尾會印出每個系列的 PASS/FAIL 與總計。

### 模型 A/B 比較

`tools.compare_models` 對固定 Excel 與 prompt 跑**完整正式管線**（含 T1），不是獨立 harness：

```bash
.venv/bin/python -m tools.compare_models \
    --provider ollama \
    --models gemma2:9b,qwen2.5:7b,llama3.1:8b \
    --input fixtures/data/fsc_114_workbook.xlsx \
    --sections 市場概況,趨勢分析,重點觀察 \
    --repeat 1
```

輸出成功率、p50 延遲、平均投影片數、平均圖表數、平均 T1 系列數，以及失敗細節；任一 run 失敗則 exit 1。它會把 `LLM_MODEL_DEFAULT/_INTENT/_WRITER/_WRITER_KEYPAGES/_CHART/_REVIEWER` 一起設為受測 model（用完還原），且 `use_fake_llm=False`、`skip_semantic_review=False`，所以會真的呼叫模型。結果只印到 stdout，不寫 `outputs/`。

模型品質**不阻擋 CI**：`verify_all.py` 全程 fake LLM，確保 deterministic。

---

## 版本與重現性基準

要重現本 README 記載的數字，四個維度都要對齊。

### 1. 環境版本

| 項目 | 基準 |
|---|---|
| Python | 3.12.4 |
| 平台 | macOS arm64（Docker 內為 `python:3.12-slim`，linux） |
| Python 依賴 | `requirements.lock`（65 套件完整傳遞依賴） |
| 前端依賴 | `src/frontend/package-lock.json`（lockfileVersion 3） |
| Node | 20（`node:20-alpine`） |

> 前端 `package.json` 使用 caret 範圍（如 `react: ^19.1.0`，實際解析 `19.2.8`）。Dockerfile 走 `npm ci`，**以 lock 檔為準因此可重現**；但本機若執行 `npm install` 可能升級到範圍內的新版。要嚴格一致請一律用 `npm ci`。

### 2. 資料版本

所有 fixture 都在版控內，可離線重現：

```text
fixtures/data/金融業務資訊揭露/       金管會原始月報，24 個月目錄（真相來源，唯讀）
fixtures/data/fsc_114/               單年、多檔單表版型（6 檔）
fixtures/data/fsc_113_114/           雙年、多檔單表版型（6 檔）
fixtures/data/fsc_114_workbook.xlsx  單年、單檔多表版型 ← verify_all 與 compare_models 預設輸入
fixtures/data/旅遊資料/              12 檔月報；目前無任何程式碼引用（孤兒資料）
source/                              命題素材與 template.pptx，唯讀
outputs/                             使用者保留成果；驗收不清理也不覆寫
```

由原始月報重新產生衍生 fixture：

```bash
.venv/bin/python -m tools.ingest_fsc \
    --out fixtures/data/fsc_114 \
    --periods 11401,11402,11403,11404,11405,11406,11407,11408,11409,11410,11411,11412

.venv/bin/python -m tools.build_fsc_workbook \
    --out fixtures/data/fsc_114_workbook.xlsx \
    --periods 11401,11402,11403,11404,11405,11406,11407,11408,11409,11410,11411,11412
```

兩支工具只搬原始數量欄位，**不做任何衍生計算**；市占率、成長率、排名等一律在執行期由 `ppt_generation.data.metric_engine` 算出。因此「換一份 Excel 就換一份簡報」，指標不會被烘進 fixture。

關鍵輸入的 sha256（`c42bafd`）：

```text
57c2556b…  fixtures/data/fsc_114_workbook.xlsx
615600152…  source/template.pptx
90c454d7…  config/metric_definitions.json
```

### 3. 模型版本

模型沒有釘死，也**刻意不釘**——A1 的需求就是可抽換。重現任何模型相關數字時，請一併記錄：

- `LLM_PROVIDER` 與 region
- 各 per-stage 的 `LLM_MODEL_*` 實際值（未設定者回退 `LLM_MODEL_DEFAULT`）
- `LLM_MAX_PARALLEL`、`LLM_RPM_LIMIT`、`LLM_TIMEOUT_SECONDS`
- `GENERATION_POLICY` 與時間預算

`generation_manifest.json` 會記錄該次生成的模型路由與階段耗時，是回溯某份簡報產生條件的第一手依據。

> 供應商的同一個 model id 可能在不同時間指向不同權重（尤其 `-latest` 別名）。跨日期比較時 model id 相同**不等於**模型相同。

### 4. 「索引」版本

本系統**沒有向量索引，也沒有 RAG**。相對應的角色是兩個確定性產物：

| 產物 | 角色 | 版本依據 |
|---|---|---|
| `config/metric_definitions.json` | 指標定義（可計算什麼、公式、單位） | 版控 + 上方 sha256 |
| `MetricStore`（執行期建立） | 該次生成的唯一數值真相來源 | 序列化在 `stages/01_metrics.json` |

模型只能引用 `MetricStore.computable_metric_keys()` 白名單內的 key，且敘事中的數字只能是佔位符，由 renderer 代入。資料範圍不足的指標（例如只有單年資料卻要算 YoY）由防呆機制擋下，不會產出。

---

## 專案結構

```text
src/
├── backend/                 FastAPI、S3、job repository/worker、ingestion、auth
├── core/
│   ├── contracts/generation.py        versioned generation contract
│   └── generation_orchestrator.py     唯一可呼叫邊界
├── ppt_generation/
│   ├── agents/              section / chart / narrative / reviewer
│   ├── core/                LLM abstraction、config、placeholders、theme（唯一視覺來源）
│   ├── data/                ingestion bridge、MetricStore、確定性 engine
│   ├── charts/              原生圖表與表格 builder
│   ├── output/              PPTX renderer 與稽核 XLSX exporter
│   ├── verification/        T1 三方數值一致性
│   └── run_pipeline.py      唯一 CLI 入口
└── frontend/                React + Vite + Tailwind，nginx 提供靜態檔

config/metric_definitions.json  指標定義
prompts/                        agent system prompts
fixtures/data/                  可重現的公開資料
tests/                          core / ppt_generation 測試
src/backend/tests/              backend 專屬測試
scripts/verify_all.py           合併前唯一驗收入口
scripts/local_private.ps1       Local Private Edition 統一操作入口
tools/                          公開月報轉檔、模型比較與 agent workspace
.agents/skills/                 Provider-neutral coding-agent 操作規則
docs/                           規格、設計決策與 Local Private Edition 指南
outputs/                        使用者保留成果
```

模組依賴方向固定為 `backend → core → ppt_generation`；`src/` 不得反向 import `tools/` 或任何量測程式，此界線由 `verify_all.py` 靜態掃描守住。

---

## 文件索引

| 文件 | 內容 |
|---|---|
| [`docs/系統架構與資料流程.md`](docs/系統架構與資料流程.md) | 分層架構、七階段管線、JSON 契約邊界、S3 物件佈局、失敗與退讓路徑 |
| [`docs/圖表原生性與資料同步設計.md`](docs/圖表原生性與資料同步設計.md) | 內嵌 workbook 機制、雙軸圖做法、三份副本一致性、配色與字級 |
| [`docs/資料引擎與LLM層設計.md`](docs/資料引擎與LLM層設計.md) | MetricStore、確定性 engine、模型抽換層 |
| [`docs/智匯數據簡報神器_開發規格書_v0.3.md`](docs/智匯數據簡報神器_開發規格書_v0.3.md) | 功能需求 FR / NFR 與驗收條件 |
| [`docs/DEVELOPMENT_GUIDELINE.md`](docs/DEVELOPMENT_GUIDELINE.md) | 開發慣例與協作規範 |
| [`docs/HUMAN_REVIEW_FRONTEND_GUIDE.md`](docs/HUMAN_REVIEW_FRONTEND_GUIDE.md) | 人工複核前端契約 |
| [`docs/current_progress.md`](docs/current_progress.md) | 目前進度、待完成事項、驗收狀態 |
| [`src/ppt_generation/Guide.md`](src/ppt_generation/Guide.md) | 簡報生成模組的開發指南 |
| [`fixtures/README.md`](fixtures/README.md) | fixture 來源與重現方式 |

---

## 選用：附件四交叉驗證

命題方附件四不是 production pipeline 的輸入。若 `source/附件四_預期修正參照資料.xlsx` 存在，或以 `SLIDEGEN_XLSX` 指定路徑，相關測試會用 pandas/openpyxl **獨立**驗算市占率與轉檔結果，等於用第二套實作交叉檢查 metric engine；缺檔時只跳過這組測試。

## 已知缺口

功能面：

- MailHog 模擬信箱未納入 `docker-compose.yml`（寄送端點與 mock/SES provider 已實作）
- DeckSpec 已隨每次生成輸出，但 `replay(deckspec, new_data)` 一鍵重生成未實作
- job 以 process-local `asyncio.create_task` 執行，程序在 queued/running 中重啟時無重新 claim 與 retry 機制

正確性與安全性：

- `run_pipeline --sample` 失效：內建範例 payload 缺 `contract_version`，Stage 1-3 即中斷
- 生成、ingestion、下載端點未認證（僅 `/jobs/{id}/send` 要求 JWT）
- `JWT_SECRET_KEY` 未設定時每次啟動隨機產生，既有 token 全數失效
- `AWS_REGION` 預設值在 `core/config.py`（`us-west-2`）與 `services/s3_service.py`、`services/email_service.py`（`us-east-1`）不一致
- 上傳大小上限三處不同：`ingestion/settings.py` 50MB、`ingestion/router.py` 硬編 25MB、nginx 50M

文件與資料：

- `fixtures/data/旅遊資料/` 無任何程式碼引用，`fixtures/README.md` 也未記載其來源
- `src/core/engine/`、`src/core/llm/` 僅剩 `__pycache__`，是整併前的殘留空套件
- `src/backend/guideline.md` 為 0 bytes；`src/backend/test_sales.csv` 位於套件根而非 `tests/`
