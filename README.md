# llmanage-slidegen
由 LLM 驅動的簡報自動生成工具，強調結構化與非結構化數據的讀取與分析能力，協助使用者快速將原始資料轉化為專業簡報。本專案為「2026 雲湧智生：臺灣生成式 AI 應用黑客松競賽」參賽作品，小組題目為「金融創新：台新新光金控_智匯數據簡報神器」，由 LLManage 團隊開發。

An LLM-powered presentation generation tool with a focus on robust data ingestion and analysis, transforming raw structured/unstructured data into polished, ready-to-use slide decks. Developed by team LLManage for the AIWave: Taiwan Generative AI Application Hackathon 2026, under the Financial Innovation track — Taishin-Shin Kong Financial Holding: Smart Data-to-Deck Assistant.

## 快速開始

```bash
pip install -r requirements.txt
python verify_all.py          # 驗收關卡：2 秒跑完，全綠代表管線與契約完整
```

一律**從 repo root 執行**（`bootstrap.py` 會補上 `src/` 的 import 路徑）：

```bash
python main.py --provider mock                     # 端到端：xlsx → 成品文字
python -m tools.spike_a --provider mock            # 結構定位命中率
python -m evalh.harness --stage intent             # 意圖解析品質
python -m evalh.harness --stage writer             # 敘事品質
python -m tools.compare_models --models gemma2:9b  # 多模型並排（FR-A1 驗收）
```

`verify_all.py` 每次 push / PR 會由 GitHub Actions 自動跑（`.github/workflows/verify.yml`）。
金管會月報進版控，所以 CI 上連兩組資料集的端到端都會跑；
只有需要附件四的項目會顯示 `⊘ 跳過`（命題素材不進版控）。
**完整驗收仍需把附件四放進 `source/` 後在本機跑一次。**

## 目錄結構

`src/` 放產品碼，依規格書 §4.3 的模組切分命名；量測工具與測試資料放 repo root。

依賴方向是**單向**的：`evalh/`、`tools/`、`tests/` 可以 import `src/`，
反過來不行。`src/` 是要能單獨出貨的東西，不該依賴隨時可砍的 spike 與量測骨架。
這條規則由 `verify_all.py` 的「分層依賴方向」檢查守著。

```
llmanage-slidegen/
├── .github/workflows/        # CI：每次 push / PR 跑 verify_all.py
├── verify_all.py             # 驗收關卡（CI 入口，對應規格書 §7）
├── bootstrap.py              # import 路徑設定
├── main.py                   # 程式進入點 → src/pipeline.py
├── requirements.txt
├── metric_definitions.json   # 指標定義（業務規則，外部化以利通用性）
│
├── src/                      # 產品碼
│   ├── contracts/            # 跨模組 JSON 契約（規格書 §5）
│   │   ├── intent_spec.py    #   IntentSpec — 自然語言 → 結構化簡報訂單
│   │   ├── sheet_map.py      #   SheetMap — 試算表結構描述
│   │   ├── metric_store.py   #   MetricStore — 數值真相來源，每項帶 source
│   │   └── narrative.py      #   PageNarrative — 敘事，含 T8 的 Claim
│   ├── llm/                  # LLM 統一介面與各家 adapter（FR-A1）
│   │   ├── base.py           #   抽象層；mock / ollama / bedrock
│   │   ├── factory.py        #   --provider 名稱 → adapter 實例
│   │   ├── repair.py         #   fence 剝除 → JSON 修補 → schema 驗證 → 重試
│   │   └── fallbacks.py      #   §6.2 的預設文案降級
│   ├── engine/               # 資料解析與指標計算（確定性，不碰 LLM）
│   │   ├── profiler.py       #   xlsx → 純文字結構描述
│   │   ├── recognize.py      #   已知格式辨識，認得就跳過 LLM 定位
│   │   ├── reader.py         #   SheetMap + xlsx → 表格資料
│   │   ├── metrics.py        #   → MetricStore（市佔率、排名、YoY 可算性）
│   │   └── summarize.py      #   MetricStore → 單頁摘要
│   ├── renderer/             # 簡報生成
│   │   ├── chart_builder.py  #   ChartSpec / add_chart 封裝
│   │   └── verify_chart_consistency.py
│   ├── locator.py            # 結構定位（LLM）：profiler 文字 → SheetSpec
│   ├── validator.py          # T8 敘事一致性斷言 + 佔位符代入
│   ├── pipeline.py           # 端到端串接
│   └── paths.py              # 專案路徑的唯一權威
│
├── prompts/                  # system prompt，外部化成檔案（規格書 §6.3）
├── evalh/                    # eval harness 與計分器
├── tools/                    # 開發工具（轉檔、spike、多模型比較）
├── tests/                    # 以附件四為標準答案的斷言測試
├── fixtures/                 # 固定測試輸入、golden 檔、資料集
│                             #   data/ 只有金管會月報進版控，附件四不進
│
├── docs/                     # 規格書與設計文件（設計決策的唯一真相來源）
├── source/                   # 命題原始素材（唯讀，不進版控）
└── outputs/                  # 生成結果（可清空重生）
```

金管會月報（政府公開資料）已進版控，clone 下來就能直接用：
`fixtures/data/金融業務資訊揭露/` 是原始月報，`fsc_114/`、`fsc_113_114/` 是轉檔產出。
要自行重轉：`python -m tools.ingest_fsc --out fixtures/data/fsc_114`。

命題素材（附件四）**不進版控**，需自行放入 `source/`（或設 `SLIDEGEN_XLSX`）。
