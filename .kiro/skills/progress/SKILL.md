---
name: progress
description: 整理目前開發工作進度（git 狀態、變更檔案、待完成事項、測試結果），產出 outputs/current_progress.md 進度報告。
---

# Progress

整理目前的開發工作進度，產出 `docs/current_progress.md`。

## 執行步驟

1. 執行以下指令，取得目前 git 狀態與近期變更記錄：
   - `git status` — 列出所有已修改、新增、未追蹤的檔案
   - `git diff --stat HEAD` — 統計各檔案的變更量
   - `git log --oneline -10` — 最近 10 筆 commit

2. 掃描 `outputs/` 目錄，列出所有已產出的檔案及其最後修改時間。

3. 根據當前對話內容與上述指令結果，整理進度報告，內容涵蓋：
   - **目前正在做什麼任務**：本次工作的主要目標
   - **已修改的檔案**：來自 git status / diff，標注新增(A) / 修改(M) / 未追蹤(?)
   - **還有哪些事情沒做完**：根據對話脈絡與任務目標，列出待完成事項
   - **測試結果**：若有執行過任何 Skill 或產出任何檔案，記錄執行結果（成功 / 失敗 / 部分完成）

4. 將報告寫入 `outputs/current_progress.md`，格式如下：

```
# Current Progress

> 更新時間：[timestamp]

---

## 目前任務

[一句話描述正在進行的工作]

---

## 已修改的檔案

| 狀態 | 檔案路徑 | 說明 |
|:---|:---|:---|
| A（新增） | path/to/file | ... |
| M（修改） | path/to/file | ... |
| ?（未追蹤） | path/to/file | ... |

---

## 待完成事項

- [ ] ...
- [ ] ...

---

## 測試 / 執行結果

| Skill / 操作 | 結果 | 備注 |
|:---|:---|:---|
| /guide-parser | ✅ 成功 | 產出 outputs/Submission_Guide.md |
| /chart-reviewer | ⏳ 尚未執行 | — |
```

5. 回報：已將進度報告寫入 `docs/current_progress.md`，並簡述關鍵待完成項目。
6. 若有目錄層級的改變，更新README層級說明