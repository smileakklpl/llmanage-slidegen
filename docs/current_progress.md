# Current Progress

> 更新時間：2026-07-30 00:48:06

---

## 目前任務

把 repo 根目錄下的測試／驗收輔助檔案歸位到合理的子目錄（`scripts/`、`tests/`、`config/`），
根目錄只保留 `main.py` 作為唯一程式進入點，並同步更新所有 import 路徑與文件引用。
過程中順帶發現 `src/ppt_generation/run_pipeline.py` 有一個既有 bug（`--stage` 序列化圖表結果時
`AttributeError: 'MetricSeries' object has no attribute 'key'`），尚未修復，等待使用者確認是否處理。

---

## 已修改的檔案

| 狀態 | 檔案路徑 | 說明 |
|:---|:---|:---|
| D（刪除，已搬移） | `bootstrap.py` | 搬到 `scripts/bootstrap.py`；`REPO_ROOT` 由 `parent` 改 `parent.parent` |
| D（刪除，已搬移） | `verify_all.py` | 搬到 `scripts/verify_all.py`；CI 呼叫路徑同步更新 |
| D（刪除，已搬移） | `conftest.py` | 搬到 `tests/conftest.py` |
| D（刪除，已搬移） | `metric_definitions.json` | 搬到 `config/metric_definitions.json` |
| ?（新增） | `scripts/` | 新目錄，放 `bootstrap.py`、`verify_all.py` |
| ?（新增） | `config/` | 新目錄，放 `metric_definitions.json` |
| ?（新增） | `tests/conftest.py` | `conftest.py` 新位置 |
| M（修改） | `main.py` | 改為先把 `scripts/` 加進 `sys.path` 再 `import bootstrap` |
| M（修改） | `evalh/__init__.py` | 同上，補 `sys.path` 邏輯 |
| M（修改） | `tools/__init__.py` | 同上，補 `sys.path` 邏輯 |
| M（修改） | `pytest.ini` | 曾試著搬進 `tests/`，實測會導致 pytest 遞迴掃進 `src/backend/tests/` 而報錯，故移回根目錄並補上實測說明註解 |
| M（修改） | `src/core/paths.py` | `METRIC_DEFS` 指向 `config/metric_definitions.json` |
| M（修改） | `.github/workflows/verify.yml` | CI 呼叫改為 `python scripts/verify_all.py` |
| M（修改） | `README.md` | 目錄結構圖與路徑引用同步更新 |
| M（修改） | `.kiro/steering/structure.md` | 同上 |
| M（修改） | `docs/資料引擎與LLM層設計.md` | 同上（`bootstrap.py`／`verify_all.py`／`metric_definitions.json` 路徑引用） |
| M（修改） | `fixtures/README.md` | `metric_definitions.json` 路徑引用更新 |
| D（已存在，尚未 commit） | `app.py` | 空檔案已刪除（前次對話遺留的變更，非本次任務範圍） |

---

## 待完成事項

- [ ] **`run_pipeline.py` 既有 bug 尚未修復**：`--stage` 模式序列化 `ResolvedChart` 時
  `chart.metric.key` 應為 `chart.metric.metric_key`。這是先前對話已修過一次，但組員合併
  `ppt_generation` 模組時帶回了沒有修正的版本。已詢問使用者是否要處理，尚未得到答覆。
- [ ] `src/backend` 的 pytest 測試本機因缺 `fastapi` 套件無法執行驗證，需另外安裝
  `src/backend/requirements.txt` 才能確認搬移沒有影響該模組（理論上沒有影響，因為完全沒動
  `src/backend/` 底下的檔案）。
- [ ] 本次所有搬移與修改尚未 commit。
- [ ] `src/backend/test_sales.csv`（前次對話發現的孤兒檔案，沒有任何程式碼引用）仍未處理，
  待使用者決定搬進 `src/backend/tests/fixtures/` 還是直接刪除。

---

## 測試 / 執行結果

| 操作 | 結果 | 備註 |
|:---|:---|:---|
| `.venv/bin/python -m pytest -q`（repo root） | ✅ 成功 | 29 個測試全過 |
| 系統 `python3`（anaconda）跑 `pytest -q` | ⚠️ 7 個失敗 | 環境問題（openpyxl 版本太舊配不上 pandas），非本次改動造成，改用 `.venv` 後全過 |
| `pytest -q --collect-only`（驗證 `pytest.ini` 留在根目錄的必要性） | ✅ 成功 | 曾將 `pytest.ini` 移進 `tests/` 實測，結果 pytest 找不到設定檔、遞迴掃進 `src/backend/tests/` 並因缺重量依賴報一排 collection error；移回根目錄後恢復正常，僅收集 `tests/` 下 29 項 |
| `.venv/bin/python scripts/verify_all.py` | ✅ 成功 | 8 項檢查全綠：單元測試、分層依賴方向、格式辨識器、FR-1.5 開關、writer fixture 漂移、eval harness、兩組端到端資料集（fsc_114 / fsc_113_114） |
| `.venv/bin/python main.py --provider mock`（repo root 執行） | ✅ 成功 | 管線跑到底，`scripts/bootstrap.py` 路徑掛載正常 |
| `src/backend` pytest | ⏳ 無法執行 | 本機缺 `fastapi` 等依賴，與本次搬移無關，未搬動該模組任何檔案 |
| `ppt_generation.run_pipeline --sample --fake-llm`（`--stage` 模式） | ❌ 失敗 | `AttributeError: 'MetricSeries' object has no attribute 'key'`，見上方「待完成事項」，非本次任務範圍但已記錄 |
