# Current Progress

> 更新時間：2026-07-30 20:52
> 對應分支：`main`（HEAD `5a96086`「ppt生成流程完善, 未測串接API」，已推上 origin）
> 本次 commit 規模：41 檔，`+108017 / -190` 行（其中約 10 萬行是 `outputs/full_deck/stages/` 的階段 JSON）

---

## 目前任務

上一階段的工作**已全部 commit 並推送**，工作區只剩這份進度檔待更新。

已完成兩件事：

1. 補上前一輪缺的三處 pytest（合計列規則、期間欄名辨識、`backend_bridge` 兩版型）
2. 把 PPT 生成補到能依附件三／附件四範例完整產出：雙軸圖、原生表格、熱力圖、
   Top N 切片、市場期間序列，以及封面／目錄／章節頁／結尾頁的完整簡報結構

實測 `fixtures/data/fsc_114_workbook.xlsx` 端到端可產出 **20 張投影片、8 章節、
9 張圖表**，T1 三方數值比對 22 個系列全數通過。commit message 已標明「未測串接 API」，
這也是下一步的第一件事。

---

## 已修改的檔案

工作區目前僅有一項未提交：

| 狀態 | 檔案路徑 | 說明 |
|:---|:---|:---|
| M | `docs/current_progress.md` | 本檔案 |

以下為 HEAD（`5a96086`）已納入的內容。

### 新增：`ppt_generation` 與工具

| 狀態 | 檔案路徑 | 行數 | 說明 |
|:---|:---|:---|:---|
| A | `src/ppt_generation/charts/table_builder.py` | 390 | 原生表格與熱力圖（FR-2.4／FR-2.3）；`format_value`／`parse_value` 成對格式化 |
| A | `src/ppt_generation/data/backend_bridge.py` | 330 | 呼叫 backend ingestion，支援單檔多表／目錄多檔兩種版型 |
| A | `tools/build_fsc_workbook.py` | 187 | 月報 → 單檔多工作表 xlsx（附件四版型） |
| A | `fixtures/data/fsc_114_workbook.xlsx` | — | 產出物：6 工作表 × 34 列 × 13 欄 |

### 新增：測試（共 168 個案例）

| 狀態 | 檔案路徑 | 案例 | 涵蓋 |
|:---|:---|:---|:---|
| A | `tests/test_metric_engine_total_row.py` | 37 | 合計列規則（含「不排除會剛好少一半」的反面斷言） |
| A | `tests/test_metric_engine_views.py` | 33 | Top N 切片與市場期間序列 |
| A | `tests/test_table_builder.py` | 33 | 原生表格、熱力圖色階、格式化成對性 |
| A | `tests/test_combo_chart.py` | 17 | 雙軸圖 XML 結構與內嵌 workbook 一致性 |
| A | `tests/test_backend_bridge.py` | 17 | 兩種輸入版型（含逐格比對） |
| A | `tests/test_deck_structure.py` | 14 | 簡報結構、頁碼指派與實際投影片位置交叉驗證 |
| A | `src/backend/tests/test_period_header.py` | 30 | 期間欄名辨識與交叉表表頭偵測 |

### 修改：`ppt_generation`

| 狀態 | 檔案路徑 | 說明 |
|:---|:---|:---|
| M | `charts/chart_builder.py` | `ComboSpec` / `add_combo_chart()`（雙軸圖）；combo 註冊進 `CHART_SKILLS` 與 tool schema |
| M | `charts/chart_planner.py` | `VISUAL_SKILLS`（圖表 + 表格合集）、`TABLE_LIKE_CHARTS`、combo 與表格防呆、`TableSpec`／`ComboSpec` 組裝 |
| M | `data/metric_engine.py` | `derive_top()`、`build_market_timeline()`、`is_period_label()`；`_TEMPORAL_PATTERNS` 加民國年月；合計列排除；`EngineConfig` 加 `enable_top_n` / `top_n` / `enable_market_timeline`，`enable_forecast` 預設改 True |
| M | `output/renderer.py` | `add_agenda_page()` / `add_closing_page()` / `assign_page_numbers()`；`render_deck()` 組裝完整簡報結構；`RenderReport` 加 `slide_count` / `divider_count` / `chapters` |
| M | `agents/section_planner.py` | `SectionPlan.chapter`；`DEFAULT_CHAPTERS`（FR-2.6 八章節）；`MAX_SECTIONS` 16；`group_by_chapter()`；forecast 章節只能引用 `.forecast` |
| M | `agents/chart_agent.py` | 改用 `VISUAL_SKILL_TOOL_SCHEMAS`；system prompt 補 combo／table／heatmap 規則 |
| M | `verification/verify_chart_consistency.py` | 走遍所有 `plots`；`read_table_shape()` 與表格比對路徑；顯示精度正規化 |
| M | `run_pipeline.py` | `--excel` / `--excel-sheet` / `--title`；blueprint 驅動的假 LLM（八章節）；`_pick_metric_keys` 前綴誤中修正；產出前依最終存留頁面重新指派頁碼 |
| M | `data/__init__.py` | 匯出 `ingest_excel` 與兩個例外 |

### 修改：`backend`（修 bug）

| 狀態 | 檔案路徑 | 說明 |
|:---|:---|:---|
| M | `app/ingestion/classifier.py` | `is_period_like_header()`；`_detect_header_row()` 改計「文字或期間型欄名」比例 |
| M | `app/ingestion/extractor.py` | `_header_value_is_plausible()` 改共用 `is_period_like_header()` |
| M | `app/ingestion/pipeline.py` | PDF／影像剖析器改延遲載入，xlsx 路徑不需 pymupdf／pdfplumber |

### 修改：設定與文件

| 狀態 | 檔案路徑 | 說明 |
|:---|:---|:---|
| M | `tests/conftest.py` | 加掛 `src` 到 sys.path（`ppt_generation` 的 import 根目錄） |
| M | `.gitignore` | 放行 `fixtures/data/fsc_114_workbook.xlsx` |
| M | `README.md` / `fixtures/README.md` | 補單檔多表版型與產生指令 |
| M | `src/ppt_generation/Guide.md` | skill 對照表、combo 做法、表格數字守法、簡報結構、已知落差 |
| M | `docs/圖表原生性與資料同步設計.md` | §8 雙軸圖做法與坑、§8.2 表格驗證、狀態表與 `CHART_SKILLS` 更新 |
| R | `outputs/deck.pptx` → `outputs/full_deck/deck.pptx` | 產出物移入子目錄，並換成 20 頁完整版 |

---

## 本次修掉的四個 bug

1. **`_detect_header_row()` 讀不到 entity × period 交叉表**（阻斷性）。表頭要求「至少一半
   是文字」，而 `金融機構名稱 + 11401…11412` 的文字比例只有 1/13 → 整張工作表被跳過。
   修復前 `fsc_114/流通卡數.xlsx` 得到 0 個 dataset。
2. **`metric_engine` 沒有合計列意識**（數值正確性）。占比分母變兩倍（每家剛好少一半，
   實測臺灣銀行 0.2393% vs 正確 0.4786%）、排名整體位移一位。
3. **三方比對只檢查 `chart.plots[0]`**（靜默漏驗）。雙軸圖是 `barChart` + `lineChart`
   兩個 plot，次軸系列完全沒被驗過——那正是量級差異大、最需要核對的一組。
4. **`_pick_metric_keys()` 子字串誤中**。`流通卡數.value` 是 `流通卡數.value.top10` 的
   前綴，敘事會引用本頁不允許的指標而被審查退回，實測 10 頁掉 5 頁。

---

## 待完成事項

### 下一步（依「離可 Demo 最近」排序）

- [x] **A1 真實 API 已實跑**（2026-07-30）。Gemini（`gemini-3.5-flash-lite` 敘事 /
      `gemini-3.1-flash-lite` 章節與圖表），端到端產出 `outputs/real_llm/deck.pptx`：
      20 投影片 / 8 章節 / 8 圖表，敘事 8 頁全數一次過規則檢查，T1 71/71 PASS。
      首跑抓到三個假 LLM 永遠測不到的契約問題（schema 沒送進 prompt → 模型把
      `sections` 猜成 `pages`；非必填欄位的 null 被當型別錯誤；429 限流沒有重試），
      已修並有 `tests/test_llm_client_contract.py` 守著。
      註：免費額度是 per-model per-day（約 20 次），全流程要靠 per-stage 模型路由分散
- [ ] **真實模型的 prompt 迭代**。目前敘事品質已可用，後續以
      `python -m tools.compare_models` 對固定 Excel/prompt 跑完整 generation pipeline，
      比較 reviewer fail-closed、T1 通過率與延遲
- [ ] **F4 自動寄送完全未做**。`src/` 下沒有 mailer 模組，也沒有 MailHog 的
      docker compose。做起來最快、Demo 效果明顯
- [x] **F5.3 非同步 job API 已接通**。`POST /api/v1/jobs/generate` 回 `202 + job_id`，
      worker 透過 `GenerationRequest` 呼叫正式 orchestrator，`GET /api/v1/jobs/{id}`
      回傳狀態與四項 artifacts
- [ ] **job durable dispatch / restart recovery 尚未完成**。job 狀態已落 S3，但目前以
      process-local `asyncio.create_task` 執行；程序在 queued/running 中重啟時，尚無重新 claim
      與 retry 機制。部署前需改為可恢復的 worker queue 或啟動時 recovery scan
- [ ] **A2 DeckSpec / Refresh 未做**。找不到 deckspec 模組，`replay(deckspec, new_data)`
      不存在
- [ ] **intent 契約尚未獨立落地**。目前 `run_pipeline` 直接把 prompt 交給
      section planner；若要完整滿足 FR-5.2，仍需在正式 agent schemas 中加入頁數、
      對象與收件人的結構化意圖契約，不得恢復已刪除的第二套 core contracts
- [ ] **敘事平行化未做**。`narrative_writer.py:306` 自己留了註記，目前序列執行，
      影響 NFR-1 的 5 分鐘目標
- [x] **S3 落檔已接通**。uploads、jobs 與生成 artifacts 由 backend storage/repository
      保存；本地 worker temp path 不成為持久化真相來源
- [ ] **前端**只有 `.gitkeep`；**部署**沒有 Dockerfile / docker-compose

### 圖表與驗收

- [ ] **雙軸圖的 PowerPoint 實機驗收**。XML 結構、軸配對、內嵌 workbook 兩系列完整、
      與快取逐格相同都有測試守著，但「右鍵編輯資料」尚未在 PowerPoint 實機開啟確認
      （本機無可自動化的 PowerPoint／LibreOffice CLI）。建議手動開
      `outputs/full_deck/deck.pptx` 第 6 頁確認
- [ ] **散點圖資料點標籤**（附件三 P.10 需顯示銀行名稱）需操作 `c:dLbls` XML
- [ ] **FR-3.4 數字溯源附錄**未做
- [ ] 真模型接上後確認內容頁數是否符合 FR-2.6 的 16 頁（目前假 LLM 的 blueprint 產 9 頁）

### 版控整理

- [ ] `outputs/full_deck/stages/` 的階段 JSON 已進版控，佔本次 commit 約 10 萬行
      （`00_ingestion.json` 5.9 萬行、`01_metrics.json` 4.1 萬行）。這些是每次重跑都會
      變的除錯產物，值得確認是否該留在版控裡，或改為只保留 `deck.pptx` / `deck_data.xlsx`
- [ ] `outputs/current_progress.md`（7/29 舊檔，已進版控）與本檔並存，
      `structure.md` 指的是 `docs/` 這份，建議刪掉 `outputs/` 那份
- [ ] `outputs/stages/`（舊的 3 頁版產出）目前被 gitignore 忽略但仍留在本機，可清掉
- [ ] `src/backend/test_sales.csv` 孤兒檔案（無任何程式碼引用）仍待處理

---

## 測試 / 執行結果

### 回歸

| 測試 | 結果 | 備註 |
|:---|:---|:---|
| root `pytest` | ✅ 187 passed | 已移除只綁舊 core engine/contracts 的重複測試；關鍵 YoY 斷言已遷到正式 metric engine |
| `src/backend` `pytest` | ✅ 73 passed | ingestion、API、S3 repository/worker 路徑全綠 |
| `scripts/verify_all.py` | ✅ 4 gates 全綠 | root/backend tests、分層掃描、正式 orchestrator full smoke；21 slides / 9 charts / T1 22/22 / 4 artifacts |

### 端到端（全部 `--fake-llm`，零次模型呼叫）

| 輸入 | 結果 | 產出 |
|:---|:---|:---|
| `fsc_114_workbook.xlsx`（版型 A） | ✅ exit 0，T1 22/22 PASS | 20 投影片 / 8 章節 / 9 圖表 |
| `fsc_114/`（版型 B 目錄） | ✅ exit 0，T1 22/22 PASS | 與版型 A 完全一致 |
| `fsc_113_114/` | ✅ exit 0，T1 22/22 PASS | 20 投影片 / 8 章節 |
| `source/附件四` | ✅ exit 0，T1 全 PASS | 20 投影片；13 指標被防呆擋下（單年資料，符合預期） |
| `--sample`（回歸） | ✅ exit 0，T1 PASS | 8 投影片 / 2 章節 |

（含結論頁後，`fsc_114_workbook.xlsx` 的投影片數由 20 增為 21。）

### 端到端（真實 LLM，Gemini）

| 輸入 | 結果 | 產出 |
|:---|:---|:---|
| `fsc_114_workbook.xlsx` | ✅ exit 0，T1 71/71 PASS | `outputs/real_llm/`：20 投影片 / 8 章節 / 8 圖表 |

模型路由：`LLM_MODEL_INTENT` 與 `LLM_MODEL_CHART` 走 `gemini-3.1-flash-lite`，
`LLM_MODEL_WRITER` 走 `gemini-3.5-flash-lite`（免費額度是 per-model per-day，
分散才跑得完）。8 頁敘事全數第一次就通過規則檢查，無退件。

註：`outputs/real_llm/` 產出於 `narrative_writer._label_hints()` 與正文
`normAutofit` 兩項改動之前，因此該檔仍有一句「規模於 60,485,911 達到波段高點」
（selector 語意誤用）。改動後另跑 4 頁小樣驗證，模型已正確寫出
「114 年 12 月」與 `max_category`。

### 產出簡報結構（`outputs/full_deck/deck.pptx`）

| 頁 | 版面 | 內容 |
|:---|:---|:---|
| 1 | 2_標題投影片 | 封面「信用卡市場分析與經營洞察」 |
| 2 | 1_標題及內容 | 目錄（8 章節） |
| 3–4 | 章節頁 + 內容 | Executive Summary → 原生表格 11×7 |
| 5–7 | 章節頁 + 內容 | 市場整體概況 → **CHART[Bar+Line] 雙軸**、CHART[Line] |
| 8–9 | 章節頁 + 內容 | 同業成長及競爭分析 → CHART[Pie] |
| 10–11 | 章節頁 + 內容 | 客戶活躍度 → CHART[Bar] |
| 12–13 | 章節頁 + 內容 | 獲利能力 → CHART[Bar] |
| 14–15 | 章節頁 + 內容 | 風險與警訊 → 熱力圖（原生表格 + 色階） |
| 16–17 | 章節頁 + 內容 | 未來趨勢推測 → CHART[Line]（forecast 指標） |
| 18–19 | 章節頁 + 內容 | 對台新的策略建議 → CHART[Bar] |
| 20 | 3_標題投影片 | 感謝聆聽 |

稽核 Excel `deck_data.xlsx`：10 工作表（索引 + 9 圖表），工作表名為 `P.{頁碼}_{指標名稱}`，
頁碼已驗證等於實際投影片序號。

---

## 如何重現

```bash
cd "/Users/william2013/Desktop/Main File/coding/llmanage-slidegen"

# 1. 產生單檔多表版型
.venv/bin/python -m tools.build_fsc_workbook --out fixtures/data/fsc_114_workbook.xlsx \
    --periods 11401,11402,11403,11404,11405,11406,11407,11408,11409,11410,11411,11412

# 2. 完整簡報端到端（不呼叫 LLM）
cd src
../.venv/bin/python -m ppt_generation.run_pipeline \
    --excel ../fixtures/data/fsc_114_workbook.xlsx \
    --fake-llm --skip-semantic-review --output-dir ../outputs/full_deck

# 3. 回歸
cd ..
.venv/bin/python -m pytest -q
PYTHONIOENCODING=utf-8 .venv/bin/python scripts/verify_all.py
cd src/backend && ../../.venv/bin/python -m pytest tests/ -q \
    --ignore=tests/test_pdf_ingestion.py --ignore=tests/test_visual_ingestion.py

# 4.（下一步，尚未執行）真實模型
cd src
../.venv/bin/python -m ppt_generation.run_pipeline --check-llm
../.venv/bin/python -m ppt_generation.run_pipeline \
    --excel ../fixtures/data/fsc_114_workbook.xlsx \
    --prompt "做一份給信用卡事業處的市場分析簡報" --output-dir ../outputs/real_llm
```
