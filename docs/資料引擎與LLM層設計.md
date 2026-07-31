# 資料引擎與 LLM 層設計

> **版本**：v0.2
> **狀態**：以正式 `backend → core.generate_deck → ppt_generation` 實作為準
> **相關程式**：`src/ppt_generation/data/`、`src/ppt_generation/core/llm_client.py`、`src/ppt_generation/agents/`

## 1. 邊界與唯一真相來源

backend ingestion 將 Excel 正規化為 JSON；`ppt_generation.data.dataset_loader` 讀取該契約，`metric_engine` 以 pandas/numpy/scikit-learn 產生唯一 `MetricStore`。LLM 不讀原始儲存格、不計算數字，也不能建立 MetricStore 中不存在的值。

```text
xlsx
 → backend ingestion JSON
 → LoadedDataset
 → deterministic metric_engine
 → MetricStore
 → agents 只取 catalog/允許的 metric keys
 → renderer 解析 placeholders
 → PPT chart cache + embedded workbook + audit XLSX
```

每個 `MetricSeries` 保存：

- `metric_key`、顯示名稱與單位
- categories、series、semantic、axis_kind
- `computable` 與 blocked notes
- evidence/source refs、formula、human-review flag

資料不足時建立 `computable=false` 的 blocked metric，不填 0、不讓 writer 猜測。

## 2. 確定性衍生指標

實作位於 `src/ppt_generation/data/metric_engine.py`：

| 指標 | 前置條件 | 防呆 |
|---|---|---|
| share / rank | categorical axis | 排除總計/合計列 |
| Top N | categorical axis 且類別數大於 N | 不重算 share 分母 |
| period growth | temporal axis、至少兩期 | 前值 0/缺值回 `None` |
| YoY | 至少兩個可辨識年度 series | 單年 blocked；支援西元與完整民國年 |
| forecast | temporal axis、至少四個歷史點 | 明確標示預測類別與公式 |
| market timeline | 多個同期間交叉表 | 優先採來源官方總計列 |

`11401` 是民國年月而非年度，YoY 年度辨識不得將它誤判成 `114年`。這個正反案例由 `tests/test_metric_engine_views.py` 守住。

## 3. LLM thin wrapper

唯一 LLM 介面位於 `src/ppt_generation/core/llm_client.py`，支援：

- AWS Bedrock Converse API（必須顯式 `AWS_REGION`）
- OpenAI-compatible endpoints：OpenAI、Google、vLLM、Ollama、LiteLLM
- `complete_json(prompt, schema, stage=...)`
- `complete_tool_call(prompt, tool_schemas, stage=...)`
- JSON 擷取、schema 驗證、暫時性錯誤與 schema 失敗重試
- 不支援原生 tool/system/JSON mode 的模型能力降級

模型與能力全部由環境變數切換，程式碼不分叉：

```text
LLM_PROVIDER
LLM_BASE_URL / LLM_API_KEY
LLM_MODEL_DEFAULT
LLM_MODEL_INTENT
LLM_MODEL_CHART
LLM_MODEL_WRITER
LLM_MODEL_WRITER_KEYPAGES
LLM_MODEL_REVIEWER
LLM_MODEL_MAILER
LLM_TOOL_MODE / LLM_JSON_MODE / LLM_SYSTEM_MODE
LLM_MAX_PARALLEL / LLM_BACKOFF_BASE
AWS_REGION
```

實際 provider、region、configured models、每階段 calls、prompt/input SHA-256 會寫入 `generation_manifest.json`。

## 4. Agent 與數值安全

- section planner：只規劃頁面結構。
- chart planner：只可選 MetricStore catalog 中可計算的 metric keys。
- narrative writer：數字必須使用 `{{metric_key...}}` placeholder，不可輸出數字字面值。
- reviewer：規則層與語意層任一退件，CLI/orchestrator 均 fail-closed，禁止 render artifacts。
- renderer：以確定性 placeholder resolver 代入數值；未解析 placeholder 視為失敗。

value-as-rank、未由 engine 計算的倍數語句、總計列被當成實體等情況由規則層拒絕，不依賴模型自律。

## 5. 驗證

`core.generation_orchestrator.generate_deck()` 在回傳前要求：

1. reviewer 全部 `APPROVED`。
2. renderer 無 unresolved placeholders。
3. T1 `passed=true` 且無 warnings。
4. `series_checked > 0`。
5. `external_checked == series_checked`。
6. PPTX、audit XLSX、verification JSON、generation manifest 全部存在並計算 SHA-256。

`scripts/verify_all.py` 用真實 Excel 與 store-aware fake LLM 跑上述完整 callable boundary，暫存輸出不寫入 `outputs/`。真實模型比較使用 `tools.compare_models`，每個模型同樣跑 full pipeline 與 T1，而不是另一套 contracts 或 engine。

## 6. 分層依賴

```text
backend → core.generation_orchestrator → ppt_generation

tests/tools → src     允許
src → tools           禁止
```

`core` 只保留 versioned generation contract 與 orchestration boundary；不得再建立第二套 MetricStore、engine 或 LLM adapter。
