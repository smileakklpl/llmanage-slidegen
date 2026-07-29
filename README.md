# llmanage-slidegen
由 LLM 驅動的簡報自動生成工具，強調結構化與非結構化數據的讀取與分析能力，協助使用者快速將原始資料轉化為專業簡報。本專案為「2026 雲湧智生：臺灣生成式 AI 應用黑客松競賽」參賽作品，小組題目為「金融創新：台新新光金控_智匯數據簡報神器」，由 LLManage 團隊開發。

An LLM-powered presentation generation tool with a focus on robust data ingestion and analysis, transforming raw structured/unstructured data into polished, ready-to-use slide decks. Developed by team LLManage for the AIWave: Taiwan Generative AI Application Hackathon 2026, under the Financial Innovation track — Taishin-Shin Kong Financial Holding: Smart Data-to-Deck Assistant.

## 快速開始

```bash
pip install -r requirements.txt
python verify_all.py          # 驗收關卡：2 秒跑完，全綠代表管線與契約完整
```

一律**從 repo root 執行**（`bootstrap.py` 會補上 `src/core/` 的 import 路徑）：

```bash
python main.py --provider mock                     # 端到端：xlsx → 成品文字
python -m tools.spike_a --provider mock            # 結構定位命中率
python -m evalh.harness --stage intent             # 意圖解析品質
python -m evalh.harness --stage writer             # 敘事品質
python -m tools.compare_models --models gemma2:9b  # 多模型並排（FR-A1 驗收）
```

`verify_all.py` 每次 push / PR 由 GitHub Actions 自動跑（`.github/workflows/verify.yml`）。
所有檢查只依賴版控內的資料，**CI 跑的就是完整驗收**，不需要任何本機檔案。

## 資料

資料來源是**金管會「金融業務資訊揭露」信用卡月報**——公開、每月更新、格式固定。
clone 下來就能直接跑，不需要向任何人索取檔案。

    fixtures/data/金融業務資訊揭露/   原始月報（11301–11412）
    fixtures/data/fsc_114/           轉檔產出：單年，YoY 不可算
    fixtures/data/fsc_113_114/       轉檔產出：雙年，YoY 可算

兩個資料集不是備份，是 FR-1.5 的兩半：同一段程式碼，資料決定年增率算不算得出來。

轉檔器只輸出原始量，市佔率與排名一律由 engine 即時計算——衍生量落地就會有
兩個真相來源。細節見 [fixtures/README.md](fixtures/README.md)。

## 目錄結構

`src/` 放產品碼，依**管線階段**分四塊：`backend/`（輸入）、`core/`（計算與 LLM）、
`ppt_generation/`（輸出）、`frontend/`。量測工具與測試資料放 repo root。

四塊各自從自己的目錄執行、各有自己的 import 根目錄——`core/` 由 `bootstrap.py`
掛上，`backend/` 由該層 `pytest.ini` 的 `pythonpath = .` 掛上。所以 `core/` 底下
一律寫 `from engine.reader import ...`，不寫 `from src.core.engine.reader import ...`。

依賴方向是**單向**的：`evalh/`、`tools/`、`tests/` 可以 import `src/`，
反過來不行。`src/` 是要能單獨出貨的東西，不該依賴隨時可砍的 spike 與量測骨架。
這條規則由 `verify_all.py` 的「分層依賴方向」檢查守著。

```
llmanage-slidegen/
├── .github/workflows/        # CI：每次 push / PR 跑 verify_all.py
├── .kiro/                    # steering 指導文件與開發輔助 skill
├── verify_all.py             # 驗收關卡（CI 入口，對應規格書 §7）
├── bootstrap.py              # import 路徑設定
├── main.py                   # 程式進入點 → src/core/pipeline.py
├── requirements.txt
├── metric_definitions.json   # 指標定義（業務規則，外部化以利通用性）
│
├── src/                        # 產品碼，依管線階段分四塊
│   ├── backend/                # 【輸入】檔案上傳與 ingestion 服務
│   │   ├── app/main.py         #   FastAPI 進入點
│   │   ├── app/ingestion/      #   偵測 / 分類 / 擷取 / 正規化 / 驗證
│   │   └── tests/              #   ingestion 專屬測試（在 src/backend/ 下跑 pytest）
│   │
│   ├── core/                   # 【計算與 LLM】bootstrap.py 掛的 import 根目錄
│   │   ├── contracts/          #   跨模組 JSON 契約（規格書 §5）
│   │   │   ├── intent_spec.py  #     IntentSpec — 自然語言 → 結構化簡報訂單
│   │   │   ├── sheet_map.py    #     SheetMap — 試算表結構描述
│   │   │   ├── metric_store.py #     MetricStore — 數值真相來源，每項帶 source
│   │   │   └── narrative.py    #     PageNarrative — 敘事，含 T8 的 Claim
│   │   ├── llm/                #   LLM 統一介面與各家 adapter（FR-A1）
│   │   │   ├── base.py         #     抽象層；mock / ollama / bedrock
│   │   │   ├── factory.py      #     --provider 名稱 → adapter 實例
│   │   │   ├── repair.py       #     fence 剝除 → JSON 修補 → schema 驗證 → 重試
│   │   │   └── fallbacks.py    #     §6.2 的預設文案降級
│   │   ├── engine/             #   資料解析與指標計算（確定性，不碰 LLM）
│   │   │   ├── profiler.py     #     xlsx → 純文字結構描述
│   │   │   ├── recognize.py    #     已知格式辨識，認得就跳過 LLM 定位
│   │   │   ├── reader.py       #     SheetMap + xlsx → 表格資料
│   │   │   ├── metrics.py      #     → MetricStore（市佔率、排名、YoY 可算性）
│   │   │   └── summarize.py    #     MetricStore → 單頁摘要
│   │   ├── locator.py          #   結構定位（LLM）：profiler 文字 → SheetSpec
│   │   ├── validator.py        #   T8 敘事一致性斷言 + 佔位符代入
│   │   ├── pipeline.py         #   端到端串接
│   │   └── paths.py            #   專案路徑的唯一權威
│   │
│   ├── ppt_generation/         # 【輸出】簡報生成（規格書 §4.3 的 renderer 模組）
│   │   ├── chart_builder.py    #   ChartSpec / add_chart 封裝 + CHART_SKILLS registry
│   │   └── verification/       #   圖表與內嵌工作表一致性驗證
│   │
│   └── frontend/               # 前端（尚未開發）
│
├── prompts/                  # system prompt，外部化成檔案（規格書 §6.3）
├── evalh/                    # eval harness 與計分器
├── tools/                    # 開發工具（轉檔、spike、多模型比較）
├── tests/                    # 斷言測試（主管線，repo root 跑 pytest）
├── fixtures/                 # 資料、固定輸入、golden（見 fixtures/README.md）
│
├── docs/                     # 規格書與設計文件（設計決策的唯一真相來源）
├── source/                   # 命題原始素材與選用參照檔（不進版控）
│                             #   template.pptx、附件二/三/四
└── outputs/                  # 生成結果（可清空重生）
```

### 選用：外部交叉驗證

命題方的「附件四」參照檔**不是資料來源，缺了不影響任何驗收**。它有一欄自算的
市佔率而月報沒有，所以在它存在時可以拿來驗我們的公式。相關測試缺檔時自動 skip。
要跑就放進 `source/` 或設 `SLIDEGEN_XLSX`——細節見 [fixtures/README.md](fixtures/README.md)。
