---
inclusion: always
---

# 專案結構與模組切分

## 唯一正式流程

```text
Excel + Prompt
  → backend FastAPI / S3 / async worker
  → core.generation_orchestrator（可呼叫邊界）
  → ppt_generation.run_pipeline
  → backend ingestion bridge
  → ppt_generation.data deterministic engine / MetricStore
  → agents（section / chart / narrative / reviewer）
  → renderer（native PPT chart/table）+ audit XLSX
  → T1 validator
  → artifacts + generation manifest 回存 S3
```

不得新增第二套 pipeline、MetricStore、engine 或 LLM adapter。CLI、API、worker、測試與模型比較都必須呼叫上述正式實作。

## 目錄結構

```text
llmanage-slidegen/
├── .github/workflows/verify.yml
├── .kiro/                    steering、skills、hooks
├── config/metric_definitions.json
├── scripts/
│   ├── bootstrap.py          只將 repo/src 加入 import path
│   └── verify_all.py         合併前唯一驗收入口
├── src/
│   ├── backend/              FastAPI、S3、job repository/worker、ingestion
│   ├── core/
│   │   ├── contracts/generation.py
│   │   └── generation_orchestrator.py
│   ├── ppt_generation/
│   │   ├── agents/           section/chart/narrative/reviewer
│   │   ├── core/             LLM abstraction/config/placeholders
│   │   ├── data/             ingestion bridge/MetricStore/engine
│   │   ├── charts/           原生圖表規格與 builder
│   │   ├── output/           PPTX 與稽核 XLSX
│   │   ├── verification/     三方數值一致性 T1
│   │   └── run_pipeline.py
│   └── frontend/
├── tests/                    core/ppt_generation 正式測試
├── src/backend/tests/        backend 專屬測試
├── tools/                    公開資料轉檔與 full-pipeline 模型比較
├── fixtures/data/            可重現公開資料
├── prompts/                  agent system prompts
├── docs/                     規格與設計文件
├── source/                   template 與命題素材；唯讀
└── outputs/                  使用者保留成果；不得擅自清理或覆寫
```

## 分層依賴

正式呼叫方向：

```text
backend → core.generation_orchestrator → ppt_generation
```

`core` 現在只保存跨 backend/生成模組的 versioned generation contract 與 callable orchestration boundary；確定性 MetricStore/engine 和 LLM thin wrapper 位於 `ppt_generation`，不得再於 `core` 複製。

`tools/`、`tests/` 可 import `src/`；`src/` 禁止反向 import `tools/` 或其他量測碼。`scripts/verify_all.py` 以靜態掃描守住此界線。

## 模組契約

跨模組只傳 schema 驗證後的 JSON/模型契約，不跨模組直接抓內部狀態：

| 邊界 | 契約/資料 |
|---|---|
| API → worker | S3 refs + persisted job model |
| worker → core | `GenerationRequest` |
| core → worker | `GenerationResult` + four artifacts |
| ingestion → engine | normalized ingestion JSON |
| engine → agents/renderer | serialized `MetricStore` / catalog |
| agents → renderer | section/chart/narrative/review schemas |
| renderer → validator | PPTX + audit XLSX + resolved chart specs |

LLM 僅產生結構化意圖/規劃、洞察敘事與信件摘要；數值只能由 deterministic engine 產生。敘事數字只能使用 MetricStore placeholder，由 renderer 代入。

## 檔案處理

- `source/` 是命題方原始素材，唯讀。
- `outputs/` 是使用者保留成果；驗收一律用系統 temp，不得擅自刪除、搬移或覆寫。
- `docs/` 是設計決策來源；實作與文件衝突時先釐清並同步更新。
- renderer 輸出的 Excel 工作表使用 `P.{頁碼}_{指標名稱}`；ingestion 不得假設來源遵循此命名。
- 所有 AWS SDK client 必須顯式指定 `AWS_REGION`。

## 測試與驗證

`python scripts/verify_all.py` 是合併前唯一關卡，必須：

1. 執行 root tests。
2. 在 `src/backend/` 執行 backend tests。
3. 靜態檢查 `src/` 不反向依賴工具碼。
4. 以版控內真 Excel + store-aware fake LLM 呼叫 `GenerationRequest → generate_deck()`。
5. 斷言 PPTX、XLSX、verification JSON、generation manifest 四項 artifacts。
6. 斷言 reviewer fail-closed、無 unresolved placeholders、T1 `external_checked == series_checked > 0`。

新增任何圖表生成邏輯，都必須有 chart cache、embedded workbook、外部稽核 XLSX 的一致性驗證。模型品質另用 `python -m tools.compare_models` 跑正式 full-pipeline A/B，不得阻擋 deterministic CI。
