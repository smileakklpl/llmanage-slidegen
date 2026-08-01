# llmanage-slidegen

「智匯數據簡報神器」將自然語言需求與 Excel 報表轉成可編輯 PowerPoint、對應稽核 Excel 與驗證報告。主要應用是金融管理報表，但 ingestion 與確定性 metric engine 不以金融分類為輸入門檻。

## 正式流程

```text
FastAPI POST /api/v1/jobs/generate
  → 上傳檔案與 job 狀態落 S3
  → backend worker
  → core.generation_orchestrator.generate_deck()
  → ppt_generation.run_pipeline
  → backend ingestion → deterministic metric engine
  → section/chart/narrative/reviewer agents
  → native PPT charts/tables + audit XLSX
  → T1 三方數值驗證
  → PPTX / XLSX / verification JSON / generation manifest 回存 S3
```

產品碼只有這一條 generation 路徑。LLM 僅負責結構化意圖、敘事與摘要；所有數值、排名、成長率、預測與圖表資料均由確定性程式計算。圖表透過 `python-pptx` 的 `add_chart()` 建立，保留 PowerPoint 內嵌 workbook。

## 安裝與驗收

Windows PowerShell，從 repo root 執行：

```powershell
.\.venv\Scripts\python.exe -m pip install -r requirements.txt
.\.venv\Scripts\python.exe -m pip install -r src\backend\requirements.txt
.\.venv\Scripts\python.exe scripts\verify_all.py
```

`verify_all.py` 會依序執行：

1. root 正式模組測試
2. backend ingestion/API 測試
3. `src/` 分層依賴掃描
4. 真實 Excel + store-aware fake LLM 的正式 orchestrator full smoke
5. PPT chart cache、embedded workbook、外部稽核 XLSX 的 T1 完整比對

驗收全程使用系統暫存目錄，不會寫入 `outputs/`，也不會呼叫外部模型。

## 執行方式

### FastAPI 非同步 job（正式服務入口）

服務需要 S3 bucket 與 AWS credentials；所有 AWS client 明確使用 `AWS_REGION`。

```powershell
$env:AWS_REGION = "ap-northeast-1"
$env:S3_BUCKET = "your-bucket"
docker compose up --build
```

若要透過 Amazon SES 真實寄信，寄件身分必須在同一個 `AWS_REGION` 完成驗證，
並設定以下環境變數後重新建立 backend 容器：

```powershell
$env:EMAIL_PROVIDER = "ses"
$env:SES_SENDER_EMAIL = "verified-sender@example.com"
docker compose up --build -d
```

本機 Docker 會轉傳標準的 `AWS_ACCESS_KEY_ID`、`AWS_SECRET_ACCESS_KEY` 與
`AWS_SESSION_TOKEN`；部署到 AWS 時則建議使用具備 `ses:SendRawEmail` 權限的 IAM Role。
若 SES 帳號仍在 sandbox，收件地址也必須先完成驗證。不設定 `EMAIL_PROVIDER=ses`
時會使用 `mock`，API 只模擬成功而不會寄出郵件。

主要 API：

- `POST /api/v1/jobs/generate`：multipart `files` + `prompt`，回傳 `202` 與 `job_id`
- `GET /api/v1/jobs/{job_id}`：輪詢狀態及 artifacts
- `POST /api/v1/jobs/{job_id}/send`：依 `EMAIL_PROVIDER` 模擬寄送或透過 SES 寄送 artifacts
- `GET /health`、`GET /ready`：服務檢查

### 本地 deterministic CLI smoke

CLI 是同一套 `ppt_generation.run_pipeline`，不是另一份 engine。PowerShell 從 repo root 執行：

```powershell
$env:PYTHONPATH = (Resolve-Path .\src)
python -m ppt_generation.run_pipeline --excel .\fixtures\data\fsc_114_workbook.xlsx --prompt "依資料產出管理層簡報" --sections "市場概況" "趨勢分析" "重點觀察" --fake-llm --output-dir .\local-smoke
```

如不希望在 repo 內留下結果，請指定系統 temp 目錄。正式 CI 使用 `scripts/verify_all.py` 自動管理 temp output。

### 真實模型與模型比較

模型後端完全由環境變數切換，不需修改程式碼：

```powershell
$env:LLM_PROVIDER = "bedrock"
$env:AWS_REGION = "ap-northeast-1"
$env:LLM_MODEL_DEFAULT = "your-model-id"
$env:LLM_MODEL_INTENT = "your-intent-model-id"
$env:LLM_MODEL_WRITER = "your-writer-model-id"
$env:LLM_MODEL_CHART = "your-chart-model-id"
$env:LLM_MODEL_REVIEWER = "your-reviewer-model-id"
```

多模型 A/B 也會跑完整正式管線與 T1，不再使用獨立的舊 contracts/harness：

```powershell
python -m tools.compare_models --provider ollama --models gemma2:9b,qwen2.5:7b --repeat 1
```

## 資料與輸出

```text
fixtures/data/金融業務資訊揭露/       金管會原始月報
fixtures/data/fsc_114/               單年、多檔單表版型
fixtures/data/fsc_113_114/           雙年、多檔單表版型
fixtures/data/fsc_114_workbook.xlsx  單年、單檔多表版型（正式 smoke）
source/template.pptx                 PowerPoint base template
outputs/                             使用者保留的生成成果；驗收不會清理或覆寫
```

轉換公開月報：

```powershell
python -m tools.ingest_fsc --out fixtures/data/fsc_114 --periods 11401,11402,11403,11404,11405,11406,11407,11408,11409,11410,11411,11412
python -m tools.build_fsc_workbook --out fixtures/data/fsc_114_workbook.xlsx --periods 11401,11402,11403,11404,11405,11406,11407,11408,11409,11410,11411,11412
```

## 專案結構

```text
src/
├── backend/                 FastAPI、S3、job repository/worker、ingestion
├── core/
│   ├── contracts/generation.py
│   └── generation_orchestrator.py
├── ppt_generation/
│   ├── agents/              section/chart/narrative/reviewer
│   ├── core/                LLM abstraction、設定、placeholder
│   ├── data/                ingestion bridge、MetricStore、確定性 engine
│   ├── charts/              原生圖表規格與 builder
│   ├── output/              PPTX 與稽核 XLSX renderer
│   ├── verification/        三方數值一致性 T1
│   └── run_pipeline.py
└── frontend/

tests/                       正式 core/ppt_generation 測試
src/backend/tests/           backend 專屬測試
scripts/verify_all.py         合併前唯一驗收入口
tools/                        公開月報轉檔與 full-pipeline 模型比較
docs/                         規格與設計決策
```

模組依賴方向為 `backend → core → ppt_generation`；`src/` 不得反向 import `tools/` 或量測程式。更完整的圖表與資料同步設計見 [`docs/圖表原生性與資料同步設計.md`](docs/圖表原生性與資料同步設計.md)。

## 選用：附件四交叉驗證

命題方附件四不是 production pipeline 的輸入。若檔案存在於 `source/附件四_預期修正參照資料.xlsx`，或以 `SLIDEGEN_XLSX` 指定，相關測試會用 pandas/openpyxl 獨立驗算市占率與轉檔結果；缺檔時只跳過這組外部參照測試。
