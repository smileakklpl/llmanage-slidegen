---
inclusion: always
---

# 技術棧與核心技術原則

## 技術選型

| 領域 | 選用技術 | 說明 |
|---|---|---|
| 報表解析 | pandas、openpyxl | 確定性計算，不使用 LLM 計算數值 |
| PPT 生成 | python-pptx | 原生圖表、原生表格生成 |
| 外推/預測 | scikit-learn（線性回歸/移動平均） | 內嵌於管線，非部署 SageMaker endpoint |
| LLM 介接 | LiteLLM 或自建 thin wrapper（OpenAI 相容格式） | 統一介面，可抽換雲端/地端模型 |
| LLM 後端 | AWS Bedrock（競賽預設）/ 地端 vLLM、Ollama | 依環境變數切換，程式碼零改動 |
| API 框架 | FastAPI | 非同步 job 模式（`POST /generate` → `202` + job_id → 輪詢） |
| 信件模擬 | MailHog（與 API 同一 Docker Compose stack） | 不做真實 SMTP 對外寄信 |
| 檔案儲存 | S3 | uploads / outputs / deckspecs / jobs / mail，所有輸入輸出一律落 S3 |
| 部署 | EC2 + Docker Compose | 容器映像預先 build 推至 GHCR，部署端僅 pull |
| 口述驅動（Stretch） | Amazon Transcribe | 音檔上傳 → S3 → 非同步轉文字 → 進 Intent Parser |

## 核心技術原則（違反視為缺陷，不是風格問題）

### 1. LLM 職責邊界
LLM 只能做三件事：**意圖解析、洞察敘事、信件摘要**。三者皆為結構化 JSON 輸出，並經 schema 驗證。LLM 絕不負責任何數值計算——所有指標計算（市占率、YoY/MoM、有效卡率、排名、趨勢外推等）必須由 `engine`（pandas + 確定性程式碼）完成。

`writer` 輸出的敘事若包含數字，必須用 `{{metric_key}}` 佔位符引用 MetricStore 中的值，由 renderer 代入，不可讓 LLM 自己寫出數字字面值。

### 2. PPT 圖表原生性機制（關鍵設計，勿誤解為「連結 Excel」）
命題要求「右鍵編輯資料可查看原始資料表」，這是 PowerPoint **內嵌工作簿（embedded workbook）**機制，不是外部連結。技術上：

- 一張原生圖表由三部分組成：Chart XML 快取值（畫面顯示用）、內嵌 workbook（右鍵編輯資料開啟）、圖表物件本身。
- 正確做法：**所有圖表一律透過 `python-pptx` 的 `shapes.add_chart()` 單一入口生成**，讓 chart XML 快取與內嵌 workbook 自動保持一致，不手動操作底層 XML 寫入數值。
- 完整設計說明見 `docs/圖表原生性與資料同步設計.md`，實作見 `src/chart_builder.py`。
- 除非圖表類型 python-pptx 無高階 API 支援（如雙軸圖、散點標籤），才允許 fallback 到直接操作 `chart._chartSpace` 的底層 lxml，且必須驗證不破壞內嵌工作簿的自動同步。

### 3. 三份資料副本一致性
系統中同一組數字會有三份副本：① Chart XML 快取值、② 內嵌 workbook、③ FR-3 外部稽核用 `.xlsx`。①②由同一次 `add_chart()` 呼叫保證一致；③是獨立輸出路徑，但因輸入源頭相同（同一個 `ChartSpec`/`MetricStore`）數值仍一致。這三者的比對是 T1 測試（三方數值比對）的核心。

### 4. 模組間僅以 JSON 契約溝通
各模組（intent / engine / writer / renderer / validator / mailer / deckspec / llm）之間不得跨模組直接取值，一律透過定義好的 JSON 資料契約（IntentSpec、MetricStore、DeckSpec、PageNarrative）傳遞。

### 5. 圖表類型對應
| 圖表需求 | 實作方式 | 狀態 |
|---|---|---|
| 排名圖、成長率圖 | `CategoryChartData` + `COLUMN_CLUSTERED`/`BAR_CLUSTERED` | 已驗證 |
| 市占率圖 | `CategoryChartData` + `PIE` | 待實作，同一入口 |
| 散點圖（規模 vs 成長） | `XyChartData` | 已實作，資料點標籤需額外處理 `c:dLbls` |
| 雙軸圖（長條+折線疊圖） | 無高階 API，需手動插入第二個 plot | M0 必驗項，風險最高 |
| 熱力圖 | 無原生圖表類型，改用原生表格 + 儲存格底色模擬 | 非真正圖表，右鍵無編輯資料選項 |

## 環境變數（現場調參用，不動程式碼）
- `LLM_MODEL_INTENT` / `LLM_MODEL_WRITER` / `LLM_MODEL_WRITER_KEYPAGES` / `LLM_MODEL_MAILER`：per-stage 模型路由
- `LLM_MAX_PARALLEL`（預設 16，防禦下限 4）/ `LLM_BACKOFF_BASE`：敘事 LLM 呼叫平行度與重試
- `AWS_REGION`：所有 AWS SDK client 必須顯式指定，禁止依賴環境預設

## 開發環境
- Python，使用 `.venv` 虛擬環境（見 `requirements.txt`）
- 開發用 Kiro IDE（本身也是加分項 S2）
