# 簡報生成模組（ppt_generation）

把「一份已解析的報表資料 + 一句話需求」變成一份數字 100% 可追溯的顧問風格簡報。

輸入是 `src/backend` 資料讀取管線輸出的 JSON，輸出是三個檔案：

| 輸出 | 說明 |
|---|---|
| `.pptx` | 每張圖表都是 PPT 原生 chart 物件，右鍵「編輯資料」可開啟內嵌工作表 |
| `.xlsx` | 與簡報圖表一一對應的稽核資料，每格附來源儲存格位置 |
| 驗證報告 | 三方數值比對結果（見下方 Stage 7） |

完整設計理由見 [`docs/圖表原生性與資料同步設計.md`](../../docs/圖表原生性與資料同步設計.md)。

---

## 一句話理解這個模組的設計

**LLM 只做決策，從不產生數字。**

所有數字都由 `data/metric_engine.py` 以 pandas 確定性計算，存進 MetricStore。
LLM 看得到「有哪些指標可用」，但看不到數值本身，因此它的輸出裡放不了數字——
只能回傳 `metric_key` 引用，由本地程式查表填入。

這是為了避開命題附件三揭露的核心問題：簡報上的數字與 Excel 不符。

---

## 目錄結構

```
ppt_generation/
├── Guide.md               ← 本文件
├── run_pipeline.py        端到端 CLI（根目錄唯一程式檔，見下方「執行」）
│
├── core/                  跨階段共用
│   ├── config.py          路徑常數、LLM 憑證載入、環境變數設定
│   ├── llm_client.py      LLM 呼叫統一介面（模型可抽換層）
│   └── placeholders.py    敘事佔位符語法：解析、查表代入、裸數字偵測
│
├── data/                  Stage 1-3：資料 → 指標
│   ├── backend_bridge.py  呼叫 backend ingestion 讀 Excel → backend JSON
│   ├── dataset_loader.py  讀 backend JSON → pandas DataFrame + 來源證據
│   ├── metric_engine.py   確定性指標計算（唯一允許產生數字的地方）
│   └── metric_store.py    MetricStore：系統唯一真相來源
│
├── charts/                圖表定義與防呆
│   ├── chart_builder.py   ChartSpec + add_chart() 單一入口 + skill registry
│   ├── table_builder.py   原生表格與熱力圖（走 add_table，無內嵌 workbook）
│   └── chart_planner.py   ChartPlan 驗證與查表組裝（圖表與表格共用）
│
├── agents/                Stage 4：多 Agent 協作
│   ├── section_planner.py 章節規劃
│   ├── chart_agent.py     圖表決策
│   ├── narrative_writer.py 敘事撰寫
│   └── reviewer.py        審查（規則層 + 語意層）
│
├── output/                Stage 5-6：檔案產出
│   ├── renderer.py        套模板組頁、插入原生圖表、代入佔位符
│   └── excel_exporter.py  FR-3 外部稽核 Excel
│
└── verification/          Stage 7：驗證
    └── verify_chart_consistency.py  三方數值比對（規格書 T1）
```

---

## 資料流

```
src/backend ingestion 產出的 UnifiedIngestionResult JSON
        │
        ▼  data/dataset_loader.py
   LoadedDataset[]（DataFrame + 每格 evidence）
        │        └─ 排除低信度、未經人工確認的資料集
        ▼  data/metric_engine.py          ← 唯一產生數字的地方
   MetricStore（.value / .yoy / .share / .rank / .period_growth / .forecast
                / .top{N} 切片 / market_by_period.* 市場期間序列）
        │        └─ 防呆：算不出來的指標標記 computable=False 並記錄原因
        │
        ├─────── catalog_for_llm() ──→ 只有 metadata，無數值
        │                                    │
        ▼                                    ▼  agents/
   （本地查表用）                    ① 章節規劃 → SectionPlan[]
        │                            ② 圖表決策 → ChartPlan（只有 metric_key）
        │                            ③ 敘事撰寫 → PageNarrative（只有佔位符）
        │                            ④ 審查 → APPROVED / REJECTED
        │                                    │
        ▼  charts/chart_planner.py ←─────────┘
   ResolvedChart（ChartSpec，數字在此由 MetricStore 查表填入）
        │
        ├──→ output/renderer.py ──→ .pptx
        │       └─ charts/chart_builder.py 的 add_chart() 單一入口
        │           同時寫入 chart XML 快取 + 內嵌 workbook（天生一致）
        │
        └──→ output/excel_exporter.py ──→ .xlsx（吃同一份 ChartSpec）
                        │
                        ▼  verification/
                 三方數值比對：chart 快取 ↔ 內嵌 workbook ↔ 外部 .xlsx
```

---

## 如何使用

### 前置：安裝依賴

```bash
python -m pip install -r requirements.txt   # 專案根目錄
```

### 設定 LLM

金鑰放在 `.venv/api_key/key.txt`（`.venv` 已被 gitignore，不會進版控）。
支援兩種格式：整檔即金鑰，或逐行 `KEY=VALUE`。

環境變數優先於檔案，切換後端不需改任何程式碼：

```bash
export LLM_PROVIDER=openai          # openai / google / bedrock / vllm / ollama / litellm
export LLM_MODEL_DEFAULT=gpt-4o-mini
export LLM_MODEL_CHART=...          # 可選：per-stage 模型路由
export LLM_MAX_PARALLEL=16          # 敘事平行度（下限 4）
export AWS_REGION=us-east-1         # 僅 bedrock 需要，必須顯式指定
```

#### 用 Google Gemini / Gemma

`LLM_PROVIDER=google` 會自動套用 Gemini 的 OpenAI 相容端點，不必手填 base_url：

```bash
export LLM_PROVIDER=google
export LLM_MODEL_DEFAULT=gemma-4-31b-it
# 金鑰寫進 .venv/api_key/key.txt 即可，或用環境變數：
export LLM_API_KEY=<你的 Google AI Studio 金鑰>
```

**Gemma 的三個能力差異已自動處理**（模型名稱含 `gemma` 時自動切換）：

| 差異 | 自動採用的降級方式 | 覆寫環境變數 |
|---|---|---|
| 無原生 tool calling | 把工具清單寫進提示詞，要求回傳 `{"tool_name":..., "arguments":{...}}`，再套用**相同**的白名單與 schema 驗證 | `LLM_TOOL_MODE=native\|json` |
| 不保證支援 `response_format` | 改由提示詞附上 JSON Schema 要求格式 | `LLM_JSON_MODE=native\|prompt` |
| 對話模板沒有 system turn | system 提示詞併入首個 user 訊息（不能直接丟掉，關鍵約束在裡面） | `LLM_SYSTEM_MODE=native\|merge` |

降級路徑的安全性與原生路徑等價：工具名稱仍只能來自 `CHART_SKILLS` 白名單，
參數仍經 schema 驗證，模型無法藉此觸發未註冊的程式碼路徑。

若實測發現某個模型其實支援原生功能，改環境變數即可，不需動程式碼。

### 執行

`run_pipeline.py` 是端到端 CLI，四種用法：

```bash
cd src

# 1. 只檢查 LLM 設定與連線（會實際呼叫一次）
../.venv/bin/python -m ppt_generation.run_pipeline --check-llm

# 2. 用內建範例資料跑完整流程（不需要 backend 輸出）
../.venv/bin/python -m ppt_generation.run_pipeline --sample \
    --prompt "幫我做一份 2026 信用卡市場分析簡報" \
    --sections 市場整體概況 成長動能檢視 業者競爭態勢

# 3. 直接讀 Excel，由 backend ingestion 現場解析（見下方「兩種輸入版型」）
../.venv/bin/python -m ppt_generation.run_pipeline \
    --excel ../fixtures/data/fsc_114_workbook.xlsx --prompt "..."

# 4. 用既有的 backend ingestion JSON
../.venv/bin/python -m ppt_generation.run_pipeline \
    --ingestion ../outputs/stages/00_ingestion.json \
    --prompt "..."
```

### 兩種輸入版型（`--excel`）

`--excel` 同時吃兩種擺法，版型判斷在 `data/backend_bridge.py` 完成，
下游拿到的 payload 形狀一致：

| 版型 | 形狀 | 範例 |
|---|---|---|
| 單檔多表 | 一個 `.xlsx`，每張工作表一個指標 | `fixtures/data/fsc_114_workbook.xlsx`、`source/附件四_預期修正參照資料.xlsx` |
| 多檔單表 | 一個目錄，每個 `.xlsx` 一個指標 | `fixtures/data/fsc_114/` |

```bash
cd src

# 單檔多表
../.venv/bin/python -m ppt_generation.run_pipeline \
    --excel ../fixtures/data/fsc_114_workbook.xlsx --fake-llm --skip-semantic-review

# 多檔單表（目錄）
../.venv/bin/python -m ppt_generation.run_pipeline \
    --excel ../fixtures/data/fsc_114 --fake-llm --skip-semantic-review

# 只讀其中一張工作表
../.venv/bin/python -m ppt_generation.run_pipeline \
    --excel ../fixtures/data/fsc_114_workbook.xlsx --excel-sheet 流通卡數 --stage metrics
```

ingestion 結果會落檔成 `<output-dir>/stages/00_ingestion.json`，之後可用
`--ingestion` 重跑同一份資料而不必再解析一次 Excel。這份 JSON 也是「數字可
追溯」的起點——每一格都帶來源檔名、工作表與儲存格位置。

`--excel`、`--ingestion`、`--sample` 三者互斥，只能指定一個。

兩件容易誤解的事：

- **metric_key 來自工作表名／檔名，不是 backend 的 UUID。** backend 的
  `dataset_id` 是內容雜湊，指標會變成 `f47ac10b_....value` 這種 LLM 無從
  判斷的字串。bridge 會換成可讀 slug（`流通卡數.value`），原 UUID 留在
  `backend_dataset_id` 供追溯。
- **`--fake-llm` 在真實資料上也能用。** 內建範例那組假回應寫死了範例的
  metric_key；餵真實資料時會改用一組「看得懂當前 MetricStore」的假回應，
  依指標語意與軸型挑圖表。不呼叫模型，但走的是與真實模型相同的契約與防呆。

常用選項：

| 選項 | 用途 |
|---|---|
| `--sections A B C` | 明確指定章節，避免章節規劃階段回 `NEEDS_CONFIRMATION` 而中斷 |
| `--fake-llm` | 完全不呼叫 LLM，用內建假回應。驗證非 LLM 部分是否正常 |
| `--skip-semantic-review` | 只跑審查的規則層，省一次 LLM 呼叫 |
| `--verbose` | 顯示 LLM 重試與自我校正細節（第一次接新模型建議開） |
| `--stage X` | 跑到階段 X 為止就停（見下方「分階段驗證」） |
| `--list-stages` | 列出所有階段與其是否需要 LLM，不執行流程 |
| `--stage-dir` / `--no-stage-dump` | 改變或關閉階段中間結果 JSON 的輸出 |

建議的實測順序：先 `--fake-llm` 確認環境沒問題，再 `--check-llm` 確認金鑰可用，
最後才跑真實流程。

管線的 exit code：0 表示三方比對通過（或已跑到 `--stage` 指定的階段），
1 表示發現不一致或產出失敗，2 表示章節規劃需要你先確認。

### 分階段驗證（`--stage`）

管線有七個階段，`--stage X` 表示「跑到 X 為止就停」。每個已完成階段的輸出
會寫成 `outputs/stages/NN_<stage>.json`，序號即執行順序，可逐段檢查中間結果。

```bash
cd src
../.venv/bin/python -m ppt_generation.run_pipeline --list-stages
```

| 階段 | 對應 | 需要 LLM | 產出 JSON 內容 |
|---|---|---|---|
| `metrics` | Stage 1-3 | 否 | MetricStore 全文、可用指標、被防呆擋下的指標與原因、給 LLM 的 catalog |
| `sections` | Stage 4-1 | 是 | 章節清單、狀態、被剔除的無效 metric_key |
| `charts` | Stage 4-2 | 是 | ChartPlan、查表後的完整 ChartSpec 數值、重試次數 |
| `narratives` | Stage 4-3 | 是 | 含佔位符的敘事原文（代入前）、重試次數 |
| `review` | Stage 4-4 | 是 | 每頁 APPROVED/REJECTED 與退件理由 |
| `render` | Stage 5-6 | 否 | 檔案路徑、頁數、圖表數、佔位符未代入清單 |
| `verify` | Stage 7 | 否 | 三方比對逐系列數值與 pass/fail |

典型用法：

```bash
# 只驗證指標計算，完全不花 LLM 呼叫
../.venv/bin/python -m ppt_generation.run_pipeline --sample --stage metrics

# 驗證 LLM 選的圖表類型是否合理，不跑後面的敘事與產出
../.venv/bin/python -m ppt_generation.run_pipeline --sample --stage charts

# 檢查敘事的佔位符寫法（JSON 裡是代入前的原文）
../.venv/bin/python -m ppt_generation.run_pipeline --sample --stage narratives --verbose
```

`03_charts.json` 是最有用的一份：它同時含 LLM 的決策（只有 `metric_key`）
與查表後的實際數值，可以直接對照確認「數字不是模型寫的」。

這些 JSON 是除錯用的觀測點，不是模組間的資料契約——
模組之間仍然只以記憶體中的 dataclass 傳遞，加 `--stage` 不改變管線行為。

### 完整流程

```python
from ppt_generation.core import config, placeholders
from ppt_generation.data import dataset_loader, metric_engine
from ppt_generation.agents import (
    section_planner, chart_agent, narrative_writer, reviewer,
)
from ppt_generation.output import renderer, excel_exporter
from ppt_generation.verification import verify_chart_consistency as vcc

# Stage 1-3：backend JSON → MetricStore
loaded = dataset_loader.load_ingestion_file("outputs/ingestion_result.json")
store, engine_report = metric_engine.build_metric_store(loaded)

print("可用指標：", store.computable_metric_keys())
print("被防呆擋下：", engine_report.blocked)   # 記得回報給使用者，別靜默忽略

# Stage 4-1：章節規劃（可能需要使用者確認）
plan = section_planner.plan_sections("幫我做 2026 信用卡市場分析", store)

if plan.needs_confirmation:
    # 中斷流程，把 plan.question_to_user 回給使用者，取得章節後再帶
    # existing_sections 參數重跑
    raise SystemExit(plan.question_to_user)

# Stage 4-2：圖表決策（內含驗證失敗自我修正）
chart_result = chart_agent.plan_charts(plan.sections, store)

# Stage 4-3：敘事撰寫（內含裸數字自我校正）
pairs = [
    (section, chart)
    for section in plan.sections
    for chart in chart_result.charts
    if chart.plan.page_number == section.page_number
]
narratives = narrative_writer.write_narratives(pairs, store)

# Stage 4-4：審查
for (section, chart), narrative in zip(pairs, narratives.narratives):
    result = reviewer.review_page(narrative, chart, store)

    if not result.approved:
        print(f"P.{section.page_number} 退件 → {result.target_agent}")
        print(result.all_issues)

# Stage 5-6：產出檔案
bundles = [
    renderer.PageBundle(section, chart, narrative)
    for (section, chart), narrative in zip(pairs, narratives.narratives)
]
renderer.render_deck(bundles, store, output_path="outputs/deck.pptx")
excel_exporter.export_audit_workbook(
    chart_result.charts, output_path="outputs/deck_data.xlsx"
)

# Stage 7：驗證
report = vcc.verify("outputs/deck.pptx", "outputs/deck_data.xlsx")
vcc.print_report(report)
assert report.passed
```

### 只跑驗證（CLI）

三方比對可獨立執行，通過回傳 exit code 0，發現不一致回傳 1，適合接 CI：

```bash
cd src
python -m ppt_generation.verification.verify_chart_consistency \
    ../outputs/deck.pptx ../outputs/deck_data.xlsx
```

---

## 幾個容易誤解的地方

### 佔位符語法

敘事文字中的數字一律寫成佔位符，由 `placeholders.py` 在組裝階段代入：

```
{{metric_key|series_name|selector}}
```

`selector` 可以是類別名稱（`3月`、`中信`），或彙總關鍵字
`latest` / `first` / `max` / `min` / `sum` / `avg` / `max_category` / `min_category`。

```python
"流通卡數達 {{mkt.value|2026年|latest}}，年增 {{mkt.yoy|2026 vs 2025|latest}}"
# → "流通卡數達 6,548.1萬張，年增 8.3%"
```

`reviewer.py` 會攔截沒用佔位符的裸數字。年份、季度、Top N 這類結構性數字屬白名單。

### 「內嵌工作簿」不是「連結外部 Excel」

右鍵「編輯資料」開啟的是 `.pptx` 內部的一份工作簿，不是外部檔案連結。
`add_chart()` 會自動讓 chart XML 快取與內嵌工作簿同源，因此**所有圖表都必須
走 `chart_builder` 的 `add_chart()` 單一入口**，不要手動操作底層 XML 寫數值。

### 指標軸語意決定哪些指標算得出來

`metric_engine.detect_axis_kind()` 會判斷類別軸是時間序列還是橫斷面分類：

| 軸類型 | 可計算 | 不可計算 |
|---|---|---|
| `temporal`（月份、年度） | 期間成長率、YoY、趨勢外推 | 占比、排名 |
| `categorical`（銀行、卡種） | 占比、排名 | 期間成長率、趨勢外推 |

這不是保守，是防止產出無意義的數字。實作時曾出現把銀行名稱當時間軸做外推，
算出「預測+1 期的流通卡數為 -118 萬張」——加上這層判斷後就擋掉了。

被擋下的指標不會消失，而是以 `computable=False` 保留並記錄原因，
呼叫端應該把 `engine_report.blocked` 回報給使用者。

### 合計列不是一個類別

來源報表常自帶「總計」列（金管會月報第 37 列就是）。
`metric_engine.is_total_category()` 會認出它，讓占比與排名把它排除：

- **占比**：分母只算各機構，合計列自身的占比是 `None` 而不是 100%。
  不排除的話分母變成兩倍，每一家的市占率都剛好少一半。
- **排名**：合計列一定最大，不排除會佔據第 1 名並使其後所有名次位移一位。

認得的寫法有總計／合計／小計／總和／全體／全市場／`total`／`subtotal` 等，
比對前會去空白並轉小寫。排除了哪幾列會記在該指標的 `notes` 裡。

這條規則與 `config/metric_definitions.json` 的 `ranking` 定義一致，該處記錄了
用錯定義的實測影響（簽帳金額有 7 家名次改變，含第 3、4 名對調）。

### 圖表類型也有防呆

`chart_planner.validate_chart_plan()` 會擋掉：折線圖用在橫斷面軸、圓餅圖用在
時間軸、圓餅圖有多組系列或含負值、散點圖系列數不是 2 個。錯誤訊息寫成可讀的
建議（「建議改用 column 或 bar」），會回饋給 LLM 讓它自我修正。

---

## 測試與驗證

各 Agent 的 LLM 呼叫都可以用 `llm_call` 參數注入假回應，測試不需連網：

```python
def fake_llm(prompt, schema, **kwargs):
    return {"status": "READY", "sections": [...]}

plan = section_planner.plan_sections("...", store, llm_call=fake_llm)
```

已驗證的項目：

- 指標計算數值正確性、防呆邊界（單年資料不算 YoY、橫斷面不算外推）
- `catalog_for_llm()` 不洩漏任何數值
- 圖表類型防呆 12 個案例
- 佔位符解析、代入、裸數字偵測
- 四個 Agent 的自我校正迴圈
- 三方數值比對（含竄改稽核 Excel 的負向測試，確認驗證器會抓到）
- 端到端：PPT 圖表顯示值逐項等於原始 backend JSON

尚未完成：這些驗證目前是一次性腳本，還沒收攏成 pytest 套件。

---

## 已知落差

| 項目 | 狀態 |
|---|---|
| 真實 LLM API 串接 | 契約與防呆都驗證過，但用的是注入假回應。真實模型能否穩定產出合規敘事需實測 |
| 敘事平行化 | `write_narratives()` 目前序列執行，未依 `LLM_MAX_PARALLEL` 平行化 |
| `orchestrator.py` | 串接全流程、處理章節確認中斷與退件重試，待實作 |
| 散點圖資料點標籤 | 銀行名稱標籤需操作 `c:dLbls` XML，`renderer.scatter_labels_pending()` 可查詢此限制 |
| 雙軸圖的 PowerPoint 實機驗收 | XML 結構、軸配對、內嵌工作簿一致性都有測試守著，但「右鍵編輯資料」尚未在 PowerPoint 實機開啟確認 |
| DeckSpec / Refresh（FR-A2） | 尚未實作 |

---

## 圖表與表格 skill

| skill | 產出 | 右鍵「編輯資料」 | 備註 |
|---|---|---|---|
| `column` / `bar` / `line` | 原生 chart | 有 | 走 `add_category_chart()` |
| `pie` | 原生 chart | 有 | 單一系列，類別數上限 `MAX_PIE_CATEGORIES`（12） |
| `scatter` | 原生 chart | 有 | 資料點標籤尚未支援 |
| `combo` | 原生 chart（雙軸） | 有 | 長條掛主軸、折線掛右側次軸。附件三 P.5/P.6 的圖型 |
| `table` | 原生 table | **沒有** | PowerPoint 表格本來就沒有內嵌工作簿 |
| `heatmap` | 原生 table + 儲存格底色 | **沒有** | 無原生熱力圖類型，FR-2.3 明訂的替代方案 |

`combo` 的做法要特別記一下，因為只有一條路是對的：**先用 `add_chart()`
把所有系列（含之後要變折線的）一次寫進去**，讓 python-pptx 把內嵌 workbook
寫完整，再把後段 `<c:ser>` 節點搬到新建的 `<c:lineChart>` 並掛上次軸。
搬的是「這個系列用什麼圖形畫」，沒有碰任何數值節點——三份副本的一致性
仍由那一次 `add_chart()` 保證。自己組 chart XML 手填 `<c:numCache>` 會讓
快取有值、內嵌 workbook 空白，那正是附件三的病灶本身。

表格沒有第二份副本可比，所以它的數字改由另一條路守著：儲存格文字一律經
`table_builder.format_value()` 產生，驗證時用 `parse_value()` 反向解析回來
與稽核 Excel 比對（比對前會把稽核值套用同一套格式化規則，才不會被顯示
精度的四捨五入誤判）。**沒有稽核 Excel 的表格一律視為「無法驗證」而非
「通過」。**

---

## 簡報結構

`render_deck()` 組出的版面順序：

```
封面（模板首頁，標題可用 --title 覆寫）
目錄（由章節清單產生，不另外撰寫）
├─ 章節分隔頁：Executive Summary
│  └─ 內容頁…
├─ 章節分隔頁：市場整體概況
│  └─ 內容頁…
…
結尾頁
```

章節來自 `SectionPlan.chapter`；`chapter` 變化時自動插入分隔頁，為 None 的
頁面不產生分隔頁。預設章節骨架見 `section_planner.DEFAULT_CHAPTERS`（FR-2.6
的八章節）。

`renderer.assign_page_numbers()` 把每頁的 `page_number` 換成**實際投影片
序號**。這件事必須在圖表決策之前做完，而且頁面因失敗被剔除後要再算一次：
稽核 Excel 的工作表名是 `P.{頁碼}_{指標名稱}`（FR-3.1），主管拿著 Excel
對照簡報，封面、目錄與每張章節分隔頁都會造成偏移，差一頁就是翻錯頁。
`tests/test_deck_structure.py` 有一條測試交叉驗證「算出來的頁碼 == 實際
投影片位置」。
