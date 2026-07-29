# Current Progress

> 更新時間：2026-07-28 23:2x（本機時間）
> 對應分支：`main`（HEAD `524086e`，尚未 commit 本次變更）

---

## 目前任務

實作簡報生成模組 `src/ppt_generation`（backend JSON → 指標計算 → 多 Agent → PPT + 稽核 Excel → 三方數值比對），已端到端跑通並用 Google Gemma 實測連線成功；目前卡在**架構過於複雜、缺少分階段測試入口**，待決定簡化方向。

---

## 已修改的檔案

### 設定與文件

| 狀態 | 檔案路徑 | 說明 |
|:---|:---|:---|
| M（修改） | `.gitignore` | 新增 `__pycache__/`、`*.py[cod]` 排除規則 |
| M（修改） | `requirements.txt` | 原為空檔，填入簡報生成模組依賴，版本全部精確釘死 |
| M（修改） | `docs/圖表原生性與資料同步設計.md` | 更新至 v0.3：輸入源改為 backend JSON、模組狀態表、端到端驗證結果、已知落差 |
| ?（未追蹤） | `src/ppt_generation/README.md` | 模組使用說明：目錄結構、資料流、執行方式、Gemma 相容性說明 |

### 簡報生成模組（全部新增）

| 狀態 | 檔案路徑 | 說明 |
|:---|:---|:---|
| ?（未追蹤） | `src/ppt_generation/__init__.py` | 套件門面與子套件對應說明 |
| ?（未追蹤） | `src/ppt_generation/run_pipeline.py` | 端到端 CLI（`--check-llm` / `--sample` / `--fake-llm`） |
| ?（未追蹤） | `src/ppt_generation/config.py` | 路徑常數、憑證載入、`LLMSettings`（含模型能力自動判斷） |
| ?（未追蹤） | `src/ppt_generation/llm_client.py` | `complete_json()` / `complete_tool_call()`；OpenAI 相容與 Bedrock；Gemma 降級路徑 |
| ?（未追蹤） | `src/ppt_generation/placeholders.py` | 佔位符解析與查表代入、裸數字偵測 |
| ?（未追蹤） | `src/ppt_generation/data/` | `dataset_loader.py`、`metric_engine.py`、`metric_store.py` |
| ?（未追蹤） | `src/ppt_generation/charts/` | `chart_builder.py`（自舊位置移入並擴充）、`chart_planner.py` |
| ?（未追蹤） | `src/ppt_generation/agents/` | `section_planner.py`、`chart_agent.py`、`narrative_writer.py`、`reviewer.py` |
| ?（未追蹤） | `src/ppt_generation/output/` | `renderer.py`、`excel_exporter.py` |
| R（搬移） | `src/ppt_generation/verification/verify_chart_consistency.py` | 自 `src/ppt_generation/` 搬入並完全改寫為三方比對 |
| D（刪除） | `src/ppt_generation/chart_builder.py` | 已搬至 `charts/chart_builder.py` |
| D（刪除） | `src/ppt_generation/__pycache__/*.pyc` | 快取檔，已加入 gitignore |

### 產出物

| 狀態 | 檔案路徑 | 說明 |
|:---|:---|:---|
| ?（未追蹤） | `outputs/deck.pptx` | 3 頁內容 + 封面，每頁一張 PPT 原生圖表 |
| ?（未追蹤） | `outputs/deck_data.xlsx` | 索引頁 + 3 張稽核資料表，每格附來源儲存格 |

---

## 待完成事項

### 立即待決策（阻塞中）

- [ ] **架構簡化方向待確認**。已提出三個選項等你選：
  - A：`run_pipeline.py` 加 `--stage` 開關，可跑到指定階段就停並輸出中間 JSON（約 30 行，解決「不知道怎麼測」）
  - B：打平 `data/ charts/ agents/ output/ verification/` 五個子套件回單層（只動 import，減少跳躍層級）
  - C：砍抽象層（llm_client 降級模式、reviewer 語意層、section_planner 併入 chart_agent，約可減 1500 行，但換模型要重寫）
  - 目前建議 A + B，C 暫緩

### 功能落差

- [ ] 真實 LLM 端到端尚未跑完（連線已通，完整流程被中斷，改由你自行執行）
- [ ] 敘事未平行化，`write_narratives()` 仍為序列，未依 `LLM_MAX_PARALLEL`
- [ ] `orchestrator.py` 未實作（章節確認中斷、Reviewer 退件重試路由）
- [ ] `charts/table_builder.py` 未實作（原生表格、熱力圖模擬）
- [ ] 雙軸圖未實作（無高階 API，需手動插入第二個 plot 並驗證不破壞內嵌工作簿）
- [ ] 散點圖資料點標籤未實作（需操作 `c:dLbls` XML）
- [ ] 驗證仍為一次性腳本，未收攏成 pytest 套件（對應規格書 T1–T8）
- [ ] DeckSpec 落地與 Refresh 重放（規格書 A2）未實作

### 待清理

- [ ] `src/ppt_generation/__pycache__` 仍在 git 索引中，需 `git rm -r --cached` 才會真正移除（未執行，會動到 git 索引）
- [ ] `outputs/demo_chart.pptx` 為舊 POC 產物（7/25），可刪
- [ ] 本次所有變更尚未 commit

---

## 測試 / 執行結果

| 操作 | 結果 | 備註 |
|:---|:---|:---|
| 指標計算數值正確性 | ✅ 成功 | YoY／MoM／占比／排名／外推逐項比對預期值 |
| `axis_kind` 防呆 | ✅ 成功 | 修正初版把銀行名稱當時間軸外推、算出 −118 萬張的缺陷 |
| `catalog_for_llm()` 無數值洩漏 | ✅ 成功 | 實測 prompt 中不含任何指標數值 |
| 圖表類型防呆 | ✅ 成功 | 12 個案例全數符合預期 |
| 佔位符解析與代入 | ✅ 成功 | 含錯誤案例與裸數字偵測 |
| 四個 Agent 自我校正迴圈 | ✅ 成功 | 圖表選錯類型、敘事寫裸數字皆能修正後通過 |
| Reviewer 方向矛盾偵測 | ✅ 成功 | 抓到「說衰退但數值為正」（附件三 P.7 錯誤型） |
| T1 三方數值比對 | ✅ 成功 | 3 個系列全 PASS，且全部含外部 Excel 比對 |
| T1 負向測試 | ✅ 成功 | 竄改稽核 Excel（21.4→99.9）確實回報 FAIL 且 exit code 1 |
| 交叉驗證 PPT vs backend JSON | ✅ 成功 | PPT 圖表顯示值逐項等於原始 JSON 數值 |
| 目錄重組後回歸驗證 | ✅ 成功 | 全模組 import 正常，端到端與 CLI 皆通過 |
| `.venv` 安裝依賴 | ✅ 成功 | 7 個套件裝入專案虛擬環境 |
| Gemma 連線（`--check-llm`） | ✅ 成功 | `LLM_PROVIDER=google` + `gemma-4-31b-it` 回傳合法 JSON |
| Gemma 降級模式自動判斷 | ✅ 成功 | 自動切 `tool_mode=json` / `json_mode=prompt` / `system_mode=merge` |
| JSON 解析健壯性 | ✅ 成功 | 修正真實模型在 JSON 後附加說明文字造成的 "Extra data" 失敗 |
| 真實 LLM 完整管線 | ⏳ 尚未執行 | 連線已通，完整流程改由使用者自行執行 |
| pytest 測試套件 | ⏳ 尚未建立 | 目前驗證皆為一次性腳本 |

---

## 如何重現測試

```bash
cd "/Users/william2013/Desktop/Main File/coding/llmanage-slidegen/src"
export LLM_PROVIDER=google
export LLM_MODEL_DEFAULT=gemma-4-31b-it

# 1. 檢查 LLM 連線
../.venv/bin/python -m ppt_generation.run_pipeline --check-llm

# 2. 不呼叫 LLM 跑完整流程
../.venv/bin/python -m ppt_generation.run_pipeline --sample --fake-llm --skip-semantic-review

# 3. 真實 LLM
../.venv/bin/python -m ppt_generation.run_pipeline --sample \
    --sections 市場整體概況 成長動能檢視 業者競爭態勢 \
    --prompt "幫我做一份 2026 信用卡市場分析簡報" --verbose

# 4. 只跑三方比對（可接 CI，exit code 0/1）
../.venv/bin/python -m ppt_generation.verification.verify_chart_consistency \
    ../outputs/deck.pptx ../outputs/deck_data.xlsx
```
