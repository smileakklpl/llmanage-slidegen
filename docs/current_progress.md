# Current Progress

> 更新時間：2026-07-30 19:30:38
> 對應分支：`main`（HEAD `16f4a14`，本次變更尚未 commit）

---

## 目前任務

在開始實作完整 PPT 生成前，先完成兩件前置工作：

1. 把 114 年度金管會月報整理成單一多工作表 Excel（版型對齊 `source/附件四_預期修正參照資料.xlsx`）
2. 讓 `ppt_generation` 能透過 `src/backend` 的 ingestion 讀取兩種輸入版型（單檔多表 / 多檔單表）

兩項都已完成並端到端驗證通過。過程中發現並修掉兩個原本會讓這件事做不成的既有 bug。

---

## 已修改的檔案

### 新增

| 狀態 | 檔案路徑 | 說明 |
|:---|:---|:---|
| ?（未追蹤） | `tools/build_fsc_workbook.py` | 月報 → 單檔多工作表 xlsx（附件四版型）。重用 `ingest_fsc.py` 的 `read_month()` 讓兩種版型同源 |
| ?（未追蹤） | `fixtures/data/fsc_114_workbook.xlsx` | 產出物：6 工作表 × 34 列 × 13 欄 |
| ?（未追蹤） | `src/ppt_generation/data/backend_bridge.py` | 呼叫 backend ingestion，支援兩種版型並合併成單一 payload |

### 修改：backend（修 bug）

| 狀態 | 檔案路徑 | 說明 |
|:---|:---|:---|
| M | `src/backend/app/ingestion/classifier.py` | 新增 `is_period_like_header()`；`_detect_header_row()` 改成計算「文字或期間型欄名」的比例 |
| M | `src/backend/app/ingestion/extractor.py` | `_header_value_is_plausible()` 改為共用 `is_period_like_header()`，消除兩層判斷不一致 |
| M | `src/backend/app/ingestion/pipeline.py` | PDF／影像剖析器改為延遲載入（`_load_pdf_parser()` / `_load_visual_parser()`），xlsx 路徑不再需要 pymupdf／pdfplumber |

### 修改：ppt_generation

| 狀態 | 檔案路徑 | 說明 |
|:---|:---|:---|
| M | `src/ppt_generation/data/metric_engine.py` | 新增 `is_total_category()`；`derive_share()` 分母排除合計列、`derive_rank()` 排除合計列 |
| M | `src/ppt_generation/run_pipeline.py` | 新增 `--excel` / `--excel-sheet`；三種資料來源互斥檢查；資料驅動的假 LLM；修掉 `chart.metric.key` → `metric_key` |
| M | `src/ppt_generation/data/__init__.py` | 匯出 `ingest_excel` 與兩個例外 |

### 修改：設定與文件

| 狀態 | 檔案路徑 | 說明 |
|:---|:---|:---|
| M | `.gitignore` | 放行 `fixtures/data/fsc_114_workbook.xlsx`（原本被 `fixtures/data/*` 擋掉，會導致 clone 後找不到 README 引用的檔案） |
| M | `README.md` | 資料段落補上單檔多表版型與產生指令 |
| M | `fixtures/README.md` | 新增「為什麼同一份資料要有兩種版型」 |
| M | `src/ppt_generation/Guide.md` | 四種用法、「兩種輸入版型」、「合計列不是一個類別」、目錄樹補 `backend_bridge.py` |
| M | `outputs/deck.pptx` / `deck_data.xlsx` | 端到端驗證重新產出 |

---

## 本次修掉的兩個既有 bug

### 1. backend 完全讀不到 entity × period 交叉表（阻斷性）

`classifier._detect_header_row()` 要求表頭列「至少一半是文字」，而
`金融機構名稱 + 11401…11412` 的文字比例只有 1/13 → 表頭偵測失敗 →
`structured_table_score < 0.6` → 整張工作表被跳過。

實測修復前：`fsc_114/流通卡數.xlsx` 得到 **0 個 dataset**；附件四 4 張表全被判成
`UNKNOWN` 或 `NATIVE_CHART`。也就是說在此之前，兩種版型都完全讀不進來。

### 2. metric_engine 沒有合計列意識（數值正確性）

來源報表自帶的「總計」列被當成與其他機構並列的類別：

- 占比：分母變兩倍，每家市占率剛好少一半（實測臺灣銀行 0.2393% vs 正確 0.4786%）
- 排名：總計佔第 1 名，其後所有名次位移一位

這條規則 `config/metric_definitions.json` 早有記載（`ranking` 要求排除 total_row），
但 `ppt_generation` 這一側沒有實作。

---

## 待完成事項

- [ ] **新行為尚未加 pytest**（已詢問使用者，等回覆）。三處值得鎖進測試：
  `is_total_category()` 的合計列規則（數值正確性規則，`verify_all.py` 目前擋不到）、
  `is_period_like_header()` 的期間欄名辨識、`backend_bridge` 的兩種版型
- [ ] 114 年單年資料在 entity × period 版型下 `yoy` 與 `period_growth` 一律被防呆擋掉。
  要做月增率或趨勢外推，需改用 `fsc_113_114`，或在 engine 加轉置路徑把期間變成類別軸
  （獨立的設計決策，本次未動）
- [ ] 本次所有變更尚未 commit
- [ ] `src/backend/test_sales.csv`（孤兒檔案，無任何程式碼引用）仍待處理
- [ ] 熱力圖、雙軸圖、`table_builder.py`、散點圖資料點標籤、DeckSpec Refresh 仍未實作

---

## 測試 / 執行結果

### 任務一：資料整理（逐格驗證）

| 比對項目 | 結果 | 規模 |
|:---|:---|:---|
| vs `fixtures/data/fsc_114/*.xlsx` | ✅ 全等 | 6 指標 × 33 列 × 12 期 = 2376 格 |
| vs `source/附件四` P.5 兩張工作表 | ✅ 全等 | 792 格 |
| 合計列自洽（總計 == 各機構加總） | ✅ 相符 | 6 指標 × 12 期 |

### 任務二：兩種版型端到端

| 輸入 | 結果 | 備註 |
|:---|:---|:---|
| 版型 A `fsc_114_workbook.xlsx` | ✅ exit 0 | 1 檔 → 6 datasets / 18 metrics；3 頁 3 圖表；T1 全部一致 PASS |
| 版型 A `source/附件四` | ✅ 成功 | 4 datasets / 12 metrics |
| 版型 B `fsc_114/` 目錄 | ✅ exit 0 | 6 檔 → 6 datasets / 18 metrics；結果與版型 A 完全一致 |
| 兩版型 MetricStore 逐格比對 | ✅ 差異 0 格 | 7128 格，metric keys 完全相同 |
| `--excel-sheet 有效卡數` | ✅ exit 0 | 單張工作表 |
| `--ingestion` 重跑 `00_ingestion.json` | ✅ exit 0 | 6 個資料集，T1 PASS |
| `--sample`（回歸） | ✅ exit 0 | 3 系列全 PASS，不受影響 |

### 合計列修正驗證

| 驗證 | 結果 |
|:---|:---|
| share == value/總計 | ✅ 15.6369% == 15.6369% |
| rank 第 1 名 | ✅ 中國信託（總計為 `None`） |
| 全年加總法 vs 附件四 P.7 市佔率欄 | ✅ 33 家最大誤差 `0.00e+00` |

### 錯誤路徑

| 情境 | 結果 |
|:---|:---|
| 未給資料來源 / 給兩個 / `--excel-sheet` 缺 `--excel` | ✅ exit 2，訊息清楚 |
| 檔案不存在 / 非 `.xlsx` / 目錄+sheet | ✅ exit 1，只印一行訊息（`--verbose` 才拋 traceback） |

### 回歸測試

| 測試 | 結果 | 備註 |
|:---|:---|:---|
| root `pytest` | ✅ 29 passed | |
| `scripts/verify_all.py` | ✅ 8 項全綠 | 含 golden 辨識器、FR-1.5 開關、writer fixture 漂移 |
| backend（可跑的 8 個模組） | ✅ 36 passed | |
| backend `test_pdf_ingestion` / `test_visual_ingestion` | ⚠️ 無法 collect | 本機缺 reportlab／pymupdf。已用 `git stash` 確認與本次改動無關（stash 後同樣失敗）；CI 會裝 backend requirements |

---

## 如何重現

```bash
cd "/Users/william2013/Desktop/Main File/coding/llmanage-slidegen"

# 1. 產生單檔多表版型
.venv/bin/python -m tools.build_fsc_workbook --out fixtures/data/fsc_114_workbook.xlsx \
    --periods 11401,11402,11403,11404,11405,11406,11407,11408,11409,11410,11411,11412

# 2. 兩種版型端到端（不呼叫 LLM）
cd src
../.venv/bin/python -m ppt_generation.run_pipeline --excel ../fixtures/data/fsc_114_workbook.xlsx \
    --fake-llm --skip-semantic-review
../.venv/bin/python -m ppt_generation.run_pipeline --excel ../fixtures/data/fsc_114 \
    --fake-llm --skip-semantic-review

# 3. 回歸
cd ..
.venv/bin/python -m pytest -q
PYTHONIOENCODING=utf-8 .venv/bin/python scripts/verify_all.py
cd src/backend && ../../.venv/bin/python -m pytest tests/ -q \
    --ignore=tests/test_pdf_ingestion.py --ignore=tests/test_visual_ingestion.py
```
