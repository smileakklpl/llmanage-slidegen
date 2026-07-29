---
name: update-requirements
description: 掃描專案中實際使用的 Python 套件與 .venv 已安裝版本，自動更新 requirements.txt（含子目錄下的 requirements.txt），套件版本一律釘死（pinned），不使用開放範圍。
---

# Update Requirements

掃描專案程式碼實際 `import` 的套件，比對 `.venv` 已安裝版本，同步更新 `requirements.txt`。

## 執行步驟

1. **掃描程式碼中的 import**
   - 搜尋專案下所有 `.py` 檔案的 `import xxx` / `from xxx import`
   - 排除標準函式庫（`os`、`sys`、`json`、`typing`、`pathlib` 等）與專案內部模組
   - 若專案有多個 `requirements.txt`（例如根目錄與子目錄各一份），分別對應各自範圍內的程式碼

2. **掃描 `.venv` 已安裝套件**
   - 執行 `.venv/bin/python -m pip freeze` 取得套件與版本

3. **比對並更新**
   - 將步驟 1 掃到的套件對應到步驟 2 的安裝版本，寫入對應的 `requirements.txt`，格式 `package==x.y.z`，一行一個，按字母排序
   - import 名稱與 PyPI 套件名稱不同時（如 `cv2` → `opencv-python`、`PIL` → `Pillow`），以實際安裝名稱為準
   - 掃到但 `.venv` 未安裝的套件，不寫入，列在報告中提醒
   - 版本一律用 `==` 精確釘版，不使用 `>=`、`~=` 等開放範圍

4. **回報結果**：列出每個 requirements.txt 的新增/更新套件與版本，以及未安裝但被 import 的套件清單。
