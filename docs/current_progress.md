# Current Progress

> 更新時間：2026-07-27 23:36:25

---

## 目前任務

專案結構重整：將原本 `src/` 下的圖表生成模組移至 `src/ppt_generation/`，新增 `backend/` 資料擷取（ingestion）模組與對應測試，並新增 `src/frontend/`（尚為空殼）與 `.kiro/skills/`（含本次使用的 progress skill）。`app.py` 目前為空檔案，尚未實作內容，`main.py` 已被刪除。

---

## 已修改的檔案

| 狀態 | 檔案路徑 | 說明 |
|:---|:---|:---|
| D（刪除） | `main.py` | 專案進入點檔案已移除（尚未有替代進入點，`app.py` 為空） |
| D（刪除） | `src/chart_builder.py` | 已搬移至 `src/ppt_generation/chart_builder.py` |
| D（刪除） | `src/verify_chart_consistency.py` | 已搬移至 `src/ppt_generation/verify_chart_consistency.py` |
| D（刪除） | `src/__pycache__/chart_builder.cpython-312.pyc` | 快取檔案清除 |
| ?（未追蹤） | `src/ppt_generation/chart_builder.py` | 圖表生成模組新位置 |
| ?（未追蹤） | `src/ppt_generation/verify_chart_consistency.py` | 一致性驗證腳本新位置 |
| ?（未追蹤） | `app.py` | 目前為空檔案，尚未實作 |
| ?（未追蹤） | `src/frontend/` | 僅有 `.gitkeep`，前端尚未開始開發 |
| ?（未追蹤） | `backend/` | 新增完整資料擷取（ingestion）後端模組，含 12 個原始檔（classifier / delimited / detector / extractor / normalizer / pdf_parser / pipeline / router / schemas / security / settings / validator / visual_parser）及對應 pytest 測試 10 個檔案、`requirements.txt`、`pytest.ini`、`test_sales.csv` |
| ?（未追蹤） | `.kiro/skills/` | 新增 skills 目錄（含本次執行的 `progress` skill） |

> 註：以上為 `git status` / `git diff --stat HEAD` 之結果，尚未 commit。

---

## 待完成事項

- [ ] `app.py` 內容尚未實作（目前為空檔案），需確認其定位是否取代 `main.py` 作為整合式進入點（依規格書應為 FastAPI 服務入口）
- [ ] 依規格書 §4.3 模組切分，`intent` / `engine` / `writer` / `renderer` / `validator` / `mailer` / `deckspec` / `llm` 等模組尚未建立（目前只有 `backend/app/ingestion` 與 `src/ppt_generation`）
- [ ] `src/frontend/` 尚為空殼，前端（若採 Web UI 方案）尚未開始
- [ ] 尚未將本次結構調整（`backend/` 新增、`src/` 搬移、`main.py` 刪除）提交至 git
- [ ] README.md 的目錄結構說明已過時（仍記載舊路徑 `src/chart_builder.py`、`main.py`），需同步更新（本次已一併更新，見下方測試/執行結果）
- [ ] backend ingestion 模組是否已與 `engine` 模組（pandas 確定性計算）整合尚待確認

---

## 測試 / 執行結果

| Skill / 操作 | 結果 | 備注 |
|:---|:---|:---|
| `git status` / `git diff --stat HEAD` / `git log --oneline -10` | ✅ 成功 | 詳見上方「已修改的檔案」 |
| 掃描 `outputs/` 目錄 | ✅ 成功 | 僅有 `demo_chart.pptx`（最後修改於 2026-07-25 23:43） |
| README.md 目錄結構更新 | ✅ 已同步更新 | 反映 `src/ppt_generation/`、`backend/` 新結構 |
| backend pytest 測試套件 | ⏳ 尚未執行 | 本次進度整理未執行測試，如需驗證請另行執行 `pytest backend/tests` |
