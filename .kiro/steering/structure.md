---
inclusion: always
---

# 專案結構與模組切分

## 目錄結構

```
llmanage-slidegen/
├── .github/workflows/        # CI：每次 push / PR 跑 verify_all.py
├── .kiro/
│   ├── steering/             # 專案級指導文件（本檔所在）
│   └── skills/               # 開發輔助 skill（progress、update-requirements）
├── verify_all.py             # 驗收關卡（CI 入口，對應規格書 §7）
├── bootstrap.py              # import 路徑設定：一律從 repo root 執行
├── main.py                   # 程式進入點 → src/pipeline.py
├── requirements.txt
├── metric_definitions.json   # 指標定義（業務規則外部化，對應通用性風險對策）
│
├── src/                      # 產品碼，依規格書 §4.3 模組切分命名
│   ├── contracts/            # 跨模組 JSON 契約（§5）；改動需知會所有下游模組
│   ├── llm/                  # FR-A1 統一介面 + adapter + factory + repair + fallback
│   ├── engine/               # FR-1 資料解析與指標計算（確定性，不碰 LLM）
│   ├── renderer/             # FR-2/FR-3 簡報生成（chart_builder / 一致性驗證）
│   ├── backend/              # FR-A2 檔案上傳與 ingestion（FastAPI）
│   │   ├── app/ingestion/    #   偵測、分類、擷取、正規化、驗證管線
│   │   └── tests/            #   ingestion 專屬測試（見下方「測試分佈」）
│   ├── frontend/             # 前端（尚為空殼）
│   ├── locator.py            # 結構定位（LLM）：profiler 文字 → SheetSpec
│   ├── validator.py          # NFR-2 / T8 敘事一致性
│   ├── pipeline.py           # 端到端串接
│   └── paths.py              # 專案路徑的唯一權威
│
├── prompts/                  # system prompt 外部化（§6.3）
├── evalh/                    # eval harness 與計分器
├── tools/                    # 轉檔、spike、多模型比較
├── tests/                    # 以附件四為標準答案的斷言測試
├── fixtures/                 # 固定輸入、golden、資料集
│                             #   data/ 僅金管會月報進版控，附件四不進
│
├── docs/                     # 規格書（設計決策的唯一真相來源）
│   ├── 智匯數據簡報神器_開發規格書_v0.3.md   # 規格的唯一真相來源
│   ├── 圖表原生性與資料同步設計.md          # PPT 圖表與 Excel 資料同步機制設計
│   └── current_progress.md                # 進度快照
├── source/                   # 命題原始素材（唯讀，不進版控）
│   ├── template.pptx         #   台新新光金控簡報模板（renderer 的 base file）
│   ├── 附件二_系統提示詞.docx            #   智匯數據簡報神器指令稿
│   ├── 附件三_信用卡範例簡報及錯誤說明.pptx  #   錯誤範例，對應 T7 測試斷言
│   └── 附件四_預期修正參照資料.xlsx        #   正確數值參照，對應 T1/T7 測試資料
└── outputs/                  # 生成結果（可清空重生）
```

## 分層依賴方向（單向，不可逆）

`evalh/`、`tools/`、`tests/` → `src/`，**反過來禁止**。

`src/` 是要能單獨出貨的東西；`tools/` 下的 spike 照定義是可以隨時砍掉的，
量測骨架也不該成為產品的必要相依。曾經破過一次：`pipeline.py` 為了拿
`load_provider`（原在 evalh）和 `locate_one`（原在 tools/spike_a）而反向 import，
形成 `src → evalh → src` 的循環，副作用是 README 寫的執行指令直接
ModuleNotFoundError。兩者現已歸位到 `src/llm/factory.py` 與 `src/locator.py`。

要在 `src/` 用到某個東西，就把那個東西搬進 `src/`，不要反向 import。
這條規則由 `verify_all.py` 的「分層依賴方向」檢查靜態掃描守著。

## 模組切分（依開發規格書 §4.3）

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
- Excel 工作表命名規則：`P.{頁碼}_{指標名稱}`（對齊附件四），例如 `P.5_流通卡數`。
  此規則用於 **renderer 的輸出**；讀取端不得假設來源檔遵循它
- MetricStore key：`{實體slug}_{指標}_{期間}`，衍生值加後綴 `_share` / `_rank` / `_yoy_{期間}`；
  合計列用 `market_total_` 前綴（對齊規格書 §5.2 範例）
- 環境變數以 `LLM_` 前綴統一管理模型路由與平行度設定

## 敘事慣例
文件與註解一律以**模組名**指稱職責（intent / engine / writer / renderer / validator / llm），
不使用開發者代號。分工是暫時的，模組邊界才是規格的一部分。

## 檔案處理原則
- `source/` 下的檔案是命題方提供的原始素材，**唯讀不修改**，所有解析/生成邏輯以它們為輸入
- `docs/` 下的規格文件是設計決策的唯一真相來源，程式實作若與文件衝突，先確認是否需要更新文件再動程式碼
- 新增模組時放在 `src/` 下，依模組切分表命名（如 `src/engine/`、`src/writer/`），避免所有邏輯塞在單一檔案

## 測試與驗證慣例
- `python verify_all.py` 是合併前的關卡：全部走確定性路徑、不呼叫模型、秒級跑完。
  紅燈就不要合併。每次 push / PR 由 GitHub Actions 自動跑
- 金管會月報進版控（公開資料，其他模組也要用），附件四不進（主辦方素材、repo 公開）。
  CI 上要用附件四的項目會顯示 `⊘ 跳過`，屬正常；
  CI 綠燈 ≠ 完整驗收，合併前仍要把附件四放進 `source/` 跑一次全綠
- `verify_*.py` 命名對應規格書 §7 的 T1–T8，回答「通過與否」
- `evalh/` 是量測骨架，回答「品質分佈與趨勢」——跑 N 次看比率，供 prompt 迭代使用。
  兩者角色不同，不要混用
- 紀律：**輸入固定，prompt 演進。** `fixtures/inputs*/` 定案後凍結；
  同時改動輸入與 prompt 就無法歸因品質變化
- 任何新增的圖表生成邏輯，必須有對應的一致性驗證（chart cache vs 內嵌 workbook）

### 測試分佈
測試目前落在兩處，各有自己的執行方式，尚未整併：

| 位置 | 範圍 | 執行方式 |
|---|---|---|
| `tests/` | 主管線（engine / recognize / ingest / 指標定義） | repo root 跑 `pytest`（`conftest.py` 已設好路徑） |
| `src/backend/tests/` | ingestion 管線 | `src/backend/` 下跑 `pytest`（該層有自己的 `pytest.ini`） |
