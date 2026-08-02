# Current Progress

> 更新時間：2026-08-02 11:45
> 目前分支：`main`（HEAD `99565e8`「Merge pull request #14 from smileakklpl/feature/human-correction」）
> 本次工作成果所在分支：**`ppt_current`（HEAD `0231abd`「簡報更新, 版面與措詞偵測」，已推上 origin）**

---

## 目前任務

本輪工作是**簡報視覺品質與文字品質的三件修復**：圖表配色改為單色階（白→台新紅 + 黑）、
字級改為依內容長度推算、以及在審查流程加入錯別字／疊字／用詞檢查。

### ⚠️ 分支狀態要先確認

工作區目前在 `main`，而**本輪的改動不在 `main` 上**。它們已 commit 為 `0231abd` 並推到
`origin/ppt_current`。剛才的 `git pull` 把 `main` 快進到 `99565e8`，因此工作區看不到這些檔案
（`src/ppt_generation/agents/text_quality.py` 在 `main` 上不存在）。**沒有遺失，只是不在這個分支。**

```
main         99565e8   領先 ppt_current 4 個 commit
ppt_current  0231abd   領先 main 1 個 commit ← 本輪成果
```

`main` 新增的 4 個 commit（human review correction、25 分鐘 SLA 與有界平行控制）
**完全沒有動到我改的 5 支檔案**，`git merge-tree` 預演也無衝突，合併應該乾淨。

---

## 已修改的檔案

以下為 `0231abd`（分支 `ppt_current`）的內容，共 15 檔、`+975 / -57` 行。

### 程式碼

| 狀態 | 檔案路徑 | 行數 | 說明 |
|:---|:---|:---|:---|
| A（新增） | `src/ppt_generation/agents/text_quality.py` | +437 | 錯別字／疊字／用詞檢查；佔位符邊界重複偵測、簡體字對照表、合法疊字白名單 |
| M（修改） | `src/ppt_generation/output/renderer.py` | +170 | 自適應字級套用、數值改紅字、主題標語（`add_theme_callout`）、移除 `p:style` 主題色引用 |
| M（修改） | `src/ppt_generation/core/theme.py` | +105 | 圖表單色階調色盤、`fit_font_size` 系列、`fit_chart_label_font_size`、`METRIC_VALUE_COLOR` |
| M（修改） | `src/ppt_generation/charts/chart_builder.py` | +83 | `apply_chart_style()` 單一樣式入口、類別軸標籤自適應與 45° 旋轉、圖表底板透明化 |
| M（修改） | `src/ppt_generation/agents/reviewer.py` | +39 | `check_text_quality()` 掛進規則層；語意層 prompt 補第 7 項（錯別字與用詞） |
| M（修改） | `src/ppt_generation/agents/narrative_writer.py` | +13 | prompt 補「佔位符代入後自帶單位，不可重複寫」與「一律繁體中文」 |

### 產出物與設定

| 狀態 | 檔案路徑 | 說明 |
|:---|:---|:---|
| A | `outputs/styled_preview/fsc_114_workbook-分析簡報.pptx` | 本輪示範產出（20 頁） |
| A | `outputs/styled_preview/fsc_114_workbook-分析資料.xlsx` | 對應稽核 Excel |
| M | `outputs/styled_preview/{deckspec,generation_manifest}.json`、`stages/*.json` | 重跑後的階段輸出 |
| M | `.gitignore` | +1 行 |

### 前一輪已併入 `main` 的相關工作

`table_builder.py` 的 `NO_STYLE_NO_GRID`、`chart_builder.py` 的 `apply_chart_style` 已在
`main` 上（經先前的 PR 併入），因此 `main` 目前已具備「表格不再露出藍底」與
「圖表走單一樣式入口」兩項修復。

---

## 本輪修掉的問題

1. **圖表五彩繽紛**（視覺一致性）。`chart_builder.py` 原本**完全沒有設定任何系列顏色**，
   所有圖表繼承模板主題的 `accent1..accent6`，一張圖出現藍、橘、灰、黃四色。
   改為單色階：單系列逐資料點依排名取白→紅漸層，多系列紅／黑交錯。
2. **表格露出藍底**（視覺一致性）。`shapes.add_table()` 不接受樣式參數，一律套用
   「Medium Style 2 - Accent 1」，其 `wholeTbl` 帶著 accent1（本模板為藍 `4472C4`）的淺色底。
   先前只填了部分儲存格，奇數列與熱力圖標籤欄留空 → 露出藍色。
   改為換掉 `tableStyleId` 並逐格填色。
3. **字級有的太大有的太小**（版面）。python-pptx 建立的 `chartSpace` 帶著 `sz="1800"`，
   沒被逐一設定的圖表元素都以 18pt 渲染；而文字框只有 `normAutofit`，它**只會縮小不會放大**，
   短敘事永遠停在 11pt。改為依內容長度推算字級，autofit 僅作最後保險。
4. **「第一名名」重複字**（文字品質）。`format_value()` 產出的值自帶單位或前後綴
   （`名` → `第 3 名`、`%` → `12.3%`），模型只看得到佔位符、看不到展開結果，
   於是寫出「排名第 {{…}} 名」→「排名第第 3 名名」。**敘事模板本身完全合法，
   錯誤只在代入後才存在**，因此檢查必須跑在代入後的文字上。

---

## 待完成事項

### 下一步（依「離可 Demo 最近」排序）

- [ ] **把 `ppt_current` 合併回 `main`**。`git merge-tree` 預演無衝突，我改的 5 支檔案
      在 `main` 上沒有被新 commit 動過。合併後需重跑 `scripts/verify_all.py`
- [ ] **`text_quality.py` 沒有單元測試**。這是本輪唯一的新邏輯，且審查是 fail-closed 的
      ——合法疊字白名單（漸漸、層層、年年…）若誤判會把正常頁面退件。
      應補：邊界重複偵測、白名單不誤判、簡體字對照表無「簡繁相同」項
- [ ] **文字品質檢查尚未在真模型路徑驗證過**。`--fake-llm` 的敘事是模板化的，
      不會產生錯字，剛才的乾淨產出**不代表檢查生效**。目前是用手寫 case 直接呼叫
      `check_text_quality()` 驗證的，需接真模型端到端確認
- [ ] **P.18 有 33 個類別**，即使旋轉 45° 也只有 6.5pt。這是資料密度問題不是排版能救的，
      該頁應改用 Top N 或表格呈現
- [ ] **雙軸圖與圓餅圖的 PowerPoint 實機驗收**。本機無 PowerPoint 也無 LibreOffice CLI，
      顏色與版面都是靠 XML 稽核而非實際渲染判斷。建議手動開新產出的
      `fsc_114_workbook-分析簡報.pptx` 確認 P.6（雙軸）、P.12（圓餅）、P.4／P.14（表格）

### 版控整理

- [ ] `outputs/styled_preview/~$deck.pptx` 與 `~$fsc_114_workbook-分析簡報.pptx` 是
      PowerPoint 的鎖定暫存檔，**不該進版控**。應把 `~$*` 加進 `.gitignore`
- [ ] `outputs/styled_preview/deck.pptx`（08/01 21:44）與 `deck_data.xlsx` 是舊檔名的殘留。
      `run_pipeline.py:2195` 已改為依來源檔名命名（`{source_stem}-分析簡報.pptx`），
      這兩個舊檔可清掉
- [ ] `outputs/current_progress.md`（07/29 舊檔）與本檔並存，`structure.md` 指的是 `docs/` 這份，
      建議刪掉 `outputs/` 那份
- [ ] `outputs/segmented_passphrases_bedrock_20260801_122728/` 佔 37 MB
      （`ingestion.json` 單檔 26 MB），確認是否需要保留

### 承接前一輪、仍未完成

- [ ] **F4 自動寄送**：backend 已有 `/jobs/{id}/send` 與 SES／mock provider，
      但 MailHog 的 docker compose stack 未建
- [ ] **A2 DeckSpec Refresh**：`deckspec.json` 已產出，`replay(deckspec, new_data)` 未實作
- [ ] **敘事平行化**：`narrative_writer.py` 目前序列執行（新 commit 已加入有界平行控制，
      需確認是否已涵蓋敘事階段）
- [ ] **散點圖資料點標籤**（附件三 P.10 需顯示銀行名稱）需操作 `c:dLbls` XML
- [ ] **FR-3.4 數字溯源附錄**未做

---

## 測試 / 執行結果

### 目前 `main`（`99565e8`，不含本輪改動）

| 關卡 | 結果 | 備註 |
|:---|:---|:---|
| root `pytest` | ✅ 189 passed | |
| `src/backend` `pytest` | ✅ 114 passed, 2 skipped | 比本輪工作時多 2 個（新 commit 帶入） |
| 分層依賴掃描 | ✅ 通過 | `src/` 100 支檔案符合 backend → core → ppt_generation |
| 正式端到端管線 | ✅ 通過 | 18 slides / 8 charts；T1 21/21；4 artifacts |
| **`scripts/verify_all.py`** | **✅ 四關全綠** | |

### 本輪工作分支 `ppt_current`（`0231abd`）

| 關卡 | 結果 | 備註 |
|:---|:---|:---|
| root `pytest` | ✅ 189 passed | 含 3 個新增測試（表格填色、表格樣式、熱力圖色階改看亮度） |
| `src/backend` `pytest` | ✅ 112 passed, 2 skipped | |
| 分層依賴掃描 | ✅ 通過 | |
| 正式端到端管線 | ✅ 通過 | 20 slides / 9 charts；T1 22/22；4 artifacts |
| **`scripts/verify_all.py`** | **✅ 四關全綠** | |

### 本輪的專項驗證（非 pytest，一次性腳本）

| 驗證項目 | 結果 | 內容 |
|:---|:---|:---|
| 全簡報顏色歸屬 | ✅ | 100 種 `srgbClr` 全數歸屬（圖表漸層 57、熱力圖漸層 56、中性 7、黃色重點 3）；**`schemeClr` 主題色引用 0 處** |
| 表格填色完整性 | ✅ | 兩張表 77 格全部有明確填色；`tableStyleId` 已換成無樣式；`firstRow`／`bandRow` 已關 |
| 圖表原生性 | ✅ | 7 張圖表對應 7 份內嵌工作簿 |
| `c:ser` 子元素順序 | ✅ | barChart／lineChart／pieChart 皆符合 DrawingML schema |
| 類別軸標籤自適應 | ✅ | 12 期間→9pt 橫排；10 家銀行全名→9pt 旋轉 45°；33 家→6.5pt 旋轉 45° |
| 主題標語四條路徑 | ✅ | 預設 0 條、兩條→P.2+P.7、三條→截斷並 warning、空清單→0 條；**封面不再出現** |
| 文字品質檢查 | ✅ | 「第一名名」「12.3%%」「張張」「疊詞」「簡體字」「錯別字」皆抓到；合法疊字與正常句不誤判 |
| **確定性 fallback 安全性** | ✅ | 33 個可計算指標（含 6 個單位 `名`、13 個 `%`）逐一建出 fallback，**全數不觸發新規則** → 未新增崩潰路徑 |

> `hard_issues_for()` 跑的是整個規則層，我新加的檢查也在其中。若確定性 fallback 本身
> 觸發新規則，required 模式的最後一層安全網就會崩、整份簡報失敗。上表最後一項專門驗這件事。

### 環境修復

`verify_all.py` 的 backend gate 先前一直紅燈，原因不是程式而是 `.venv` 未同步——
以下 8 個套件**都已宣告在 `src/backend/requirements.txt`，只是沒安裝**
（隊友新增 auth 模組 PR #12 之後）：

```
PyJWT==2.9.0        PyMuPDF==1.28.0      pdfplumber==0.11.10   reportlab==5.0.0
Pillow==12.3.0      beautifulsoup4==4.15.0   pypdf==6.14.2     email-validator==2.2.0
```

已全部按宣告版本安裝，backend gate 因此轉綠。

---

## 如何重現

```bash
cd "/Users/william2013/Desktop/Main File/coding/llmanage-slidegen"

# 0. 切到本輪成果所在分支
git checkout ppt_current

# 1. Excel → ingestion JSON（run_pipeline 只吃 JSON，不再有 --excel 參數）
PYTHONPATH=src:src/backend .venv/bin/python -c "
from app.ingestion import generation_bridge
generation_bridge.save_payload(
    generation_bridge.ingest_excel('fixtures/data/fsc_114_workbook.xlsx'),
    'outputs/styled_preview/ingestion.json')
"

# 2. 端到端（不呼叫 LLM）
cd src && ../.venv/bin/python -m ppt_generation.run_pipeline \
    --ingestion ../outputs/styled_preview/ingestion.json \
    --title "信用卡市場分析與經營洞察" \
    --fake-llm --skip-semantic-review \
    --output-dir ../outputs/styled_preview

# 3. 合併前唯一關卡
cd .. && PYTHONIOENCODING=utf-8 .venv/bin/python scripts/verify_all.py
```

產出檔名為 `{來源檔名}-分析簡報.pptx`（非 `deck.pptx`）。
