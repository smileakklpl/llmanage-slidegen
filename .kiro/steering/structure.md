---
inclusion: always
---

# 專案結構與模組切分

## 目錄結構

```
llmanage-slidegen/
├── README.md                          # 專案說明
├── main.py                            # 程式進入點（尚未實作）
├── requirements.txt                   # Python 套件依賴
├── docs/                               # 專案文件
│   ├── 智匯數據簡報神器_開發規格書_v0.3.md   # 完整開發規格書（規格的唯一真相來源）
│   └── 圖表原生性與資料同步設計.md          # PPT 圖表與 Excel 資料同步機制設計
├── source/                             # 命題原始素材（競賽單位提供，唯讀，不修改）
│   ├── (台新新光金控) 命題文件 - 雲湧智生：臺灣生成式 AI 應用黑客松競賽.pdf
│   ├── template.pptx                   # 台新新光金控簡報模板（renderer 的 base file）
│   ├── 附件二_系統提示詞.docx            # 智匯數據簡報神器指令稿
│   ├── 附件三_信用卡範例簡報及錯誤說明.pptx  # 錯誤範例，對應 T7 測試斷言
│   └── 附件四_預期修正參照資料.xlsx        # 正確數值參照，對應 T1/T7 測試資料
├── src/                                # 原始碼
│   ├── chart_builder.py                # 圖表生成模組（ChartSpec / add_chart 封裝）
│   └── verify_chart_consistency.py     # 圖表與內嵌工作表一致性驗證腳本
└── outputs/                             # 生成輸出結果（測試/示範用，可清空重生）
    └── demo_chart.pptx
```

## 模組切分（依開發規格書 §4.3，尚待實作）

系統管線分為 8 個模組，彼此僅以 JSON 契約溝通，任何模組不得跨模組直接取值：

| 模組 | 負責功能 | 輸入 | 輸出 |
|---|---|---|---|
| `intent` | 自然語言指令解析 | prompt 文字 | `IntentSpec`（JSON） |
| `engine` | 數據智能解析、跨表關聯、指標計算 | xlsx + IntentSpec | `MetricStore`（JSON/parquet） |
| `writer` | 敘事生成（洞察文案） | MetricStore + IntentSpec | `PageNarrative[]`（JSON） |
| `renderer` | PPT/Excel 生成 | MetricStore + Narrative + 模板 | .pptx + .xlsx |
| `validator` | 三方數值比對 | .pptx + .xlsx + MetricStore | 驗證報告（pass/fail） |
| `mailer` | 自動寄送 | 檔案 + 收件人 | 寄送紀錄 |
| `deckspec` | Spec 保存與 Refresh 重放 | 上述全部 | DeckSpec JSON；`replay(deckspec, new_data)` |
| `llm` | LLM 呼叫統一介面 | — | `complete_json(prompt, schema)` |

管線資料流向：

```
使用者 Prompt + Excel 上傳
        │
        ▼
[1] Intent Parser（LLM）→ IntentSpec
        ▼
[2] Data Engine（pandas，確定性）→ MetricStore（含來源追溯）
        ▼
[3] Insight Writer（LLM，平行）→ PageNarrative（只能引用 MetricStore 之值）
        ▼
[4] Renderer（python-pptx / openpyxl，確定性）→ .pptx + .xlsx
        ▼
[5] Validator（確定性）→ 三方比對
        ▼
[6] Mailer（模擬信箱）
        ▼
DeckSpec 落地保存（供 Refresh 重放）
```

## 命名慣例
- Excel 工作表命名規則：`P.{頁碼}_{指標名稱}`（對齊附件四），例如 `P.5_流通卡數`
- 環境變數以 `LLM_` 前綴統一管理模型路由與平行度設定

## 檔案處理原則
- `source/` 下的檔案是命題方提供的原始素材，**唯讀不修改**，所有解析/生成邏輯以它們為輸入
- `docs/` 下的規格文件是設計決策的唯一真相來源，程式實作若與文件衝突，先確認是否需要更新文件再動程式碼
- 新增模組時放在 `src/` 下，依模組切分表命名（如 `src/engine/`、`src/writer/`），避免所有邏輯塞在單一檔案

## 測試與驗證慣例
- 驗證腳本命名慣例：`verify_*.py`（如 `verify_chart_consistency.py`），對應規格書 §7 的 T1–T8 測試項目
- 任何新增的圖表生成邏輯，必須有對應的一致性驗證（chart cache vs 內嵌 workbook）
