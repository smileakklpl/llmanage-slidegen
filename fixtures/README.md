# fixtures — 資料、固定輸入與標準答案

## 資料來源：金管會月報

本專案的資料來源是**金管會「金融業務資訊揭露」信用卡月報**。

選它的理由不是方便，是它同時滿足三件事：

- **公開**。政府開放資料，可以進版控、可以隨 repo 散佈，任何人 clone 下來就能跑。
- **可更新**。每月發布，格式固定。下個月的資料進來，同一支轉檔器就能吃。
- **可重現**。任何人拿到同一份原始檔，都會得到同一份 MetricStore。

一個只能跑在某份私有檔案上的管線，沒有辦法交付給別人維護。

```
fixtures/
├── data/
│   ├── 金融業務資訊揭露/     原始月報（11301–11412，一月一目錄）
│   ├── fsc_114/             轉檔產出：11401–11412，六個指標各一檔
│   ├── fsc_113_114/         轉檔產出：11301–11412
│   └── fsc_114_workbook.xlsx  同上 114 年資料，改成單檔多工作表
├── golden/                  標準答案
└── inputs/
    ├── intent/              intent 階段的固定輸入
    └── writer/              writer 階段的固定輸入
```

轉檔：

```bash
# 多檔單表：一個指標一個檔
python -m tools.ingest_fsc --out fixtures/data/fsc_114

# 單檔多表：一個檔，一個指標一張工作表（附件四版型）
python -m tools.build_fsc_workbook --out fixtures/data/fsc_114_workbook.xlsx \
    --periods 11401,11402,11403,11404,11405,11406,11407,11408,11409,11410,11411,11412
```

### 為什麼同一份資料要有兩種版型

真實世界兩種擺法都會遇到：有人一個指標給一個檔，有人整年塞進一個檔的多張
工作表。ingestion 兩種都得吃，所以兩種都要有實測資料。

兩支轉檔器共用 `tools/ingest_fsc.py` 的 `read_month()`，所以兩種版型的數值
必然相同——不會出現「換個版型數字就不一樣」這種最難查的問題。已驗證：
6 指標 × 33 列 × 12 期 = 2376 格逐格相等。

`fsc_114_workbook.xlsx` 與附件四刻意有兩點不同：不寫市佔率欄（見下方
「衍生量不落地」），工作表名不加 `P.5預期修正_` 前綴（那是命題方對附件三的
頁次標記，不是資料屬性）。

### 為什麼有兩個資料集

不是備份，是 **FR-1.5 的兩半**：

| 資料集 | 期間 | YoY |
|---|---|---|
| `fsc_114` | 11401–11412（單年） | **不可算** — 沒有基期 |
| `fsc_113_114` | 11301–11412（雙年） | **可算** — 396/792 |

同一段程式碼，資料決定行為。engine 不會因為算不出年增率就丟例外或填 0，
而是把該 key 標成 `computable=false` 並附上原因。這條規則需要兩個資料集才驗得到。

### 衍生量不落地

轉檔器只輸出原始量（卡數、金額、餘額）。市佔率、排名、年增率一律由 engine
即時計算，不寫進檔案 —— 兩個真相來源必然有一天會不一致。
`tests/test_ingest_fsc.py::test_derived_metrics_are_not_materialised` 守著這條。

---

## golden — 標準答案

| 檔案 | 是什麼 | 誰在用 |
|---|---|---|
| `sheet_map.json` | 月報六張表的結構描述 | `spike_a` 給 LLM 定位打分、`verify_all` 的辨識器回歸 |
| `intent_spec.json` | `01_official` 的預期解析結果 | intent 階段的內容正確率 |
| `page_narrative.json` | 一份合格的 PageNarrative | MockProvider 的對照組輸出 |

`sheet_map.json` 由確定性辨識器產生。所以 `verify_all` 的「格式辨識器」那項
**不是在驗辨識對不對，是在驗有沒有回歸** —— 改了 `recognize.py` 而 golden 沒跟著
更新就是紅燈。辨識的正確性由 `tests/test_recognize.py` 的結構斷言把關
（認出合計列、期間欄數、archetype）。

---

## 固定輸入的紀律：輸入固定，prompt 演進

`inputs/` 底下的檔案**定案後凍結**。要改的是 `prompts/`，不是這裡。

兩者同時變就無法歸因 —— 品質上升是 prompt 變好還是題目變簡單，說不清楚。

### baselines/ —— 讓「比較同一張表」真的做得到

輸入凍結只解決了一半：還要有東西記住上一次的分數。
`fixtures/baselines/{stage}_{provider}.json` 就是那張表，進版控。

```bash
python -m evalh.harness --stage writer --provider bedrock --repeat 3 --save   # 改 prompt 前
# ……改 prompts/insight_writer.system.md……
python -m evalh.harness --stage writer --provider bedrock --repeat 3          # 自動對照
```

第二次跑會直接印出差異，方向已正規化（`✓` 一律代表變好）：

```
=== 對基準線（2026-07-29，us.anthropic.claude-haiku-4-5-…，repeat=3）===
  schema 通過率      100.0% →  100.0%  (+0.0%)
  一次過              75.0% ↑   79.2%  (+4.2%)  ✓
  檢查項失敗率            9.2% ↓    6.7%  (-2.5%)  ✓
  失敗項變化：
    比較句有 claim       7 → 3  (-4)
```

基準線檔案進版控，所以 prompt 的 PR diff 會同時顯示「改了什麼」與「效果如何」。

輸入份數或模型改變時會出現警告：份數變了代表基準線失效必須重建；
模型變了代表你在比模型不是比 prompt。`--repeat` 不同則只有比率可比，
原始次數不可直接比，報表會標明。

### writer 的輸入由引擎產生

    python -m tools.gen_writer_fixtures --write

手寫過兩版，兩次都漂移：格式改了 fixture 沒跟上、key 命名對不上引擎實際產出。
沒有東西強迫手寫檔跟上引擎，所以它必然會漂移。改由引擎產生就結構性地解決了。

**但產生完仍要凍結。** 產生 → commit → 凍結。只有引擎輸出格式**刻意**改變時
才重新產生，而重新產生等於作廢既有基準線，必須重跑。
`verify_all` 的「writer fixture 漂移」會斷言 committed 內容仍等於引擎現在的產出。

### 檔案只放資料，不放指示

初版曾把「數值只供判斷關係、不得寫進敘事」這種句子寫在輸入結尾。那是**指示**，
等於同一條規則同時存在於 prompt 與 fixture 兩處，改 prompt 時無法歸因。
已全部移進 `prompts/insight_writer.system.md`。輸入只保留：

    頁碼 / narrative_id / 頁面主題
    可用指標（key = 實值）
    不可用指標（選填）

實值要給，因為 writer 必須知道 A > B 才寫得出 claim。「看得到卻不准寫出來」
是 prompt 的規則，不是資料的形狀。

### 八份 writer 輸入各自的考點

| 檔案 | 資料集 | 考什麼 |
|---|---|---|
| `01_market_overview` | 2y | 有明確可比關係，看會不會把實值寫死進敘事 |
| `02_ranking` | 2y | 排名情境，最誘人直接寫出市佔率數字 |
| `03_single_period` | 2y | 兩個單位不同的指標，看會不會硬湊 claims |
| `04_yoy_unavailable` | **1y** | YoY 不可算，看會不會偷用或改用文字迂迴描述 |
| `05_yoy_available` | 2y | YoY 可算，驗反面：該用卻不敢用 |
| `06_risk_metrics` | 2y | 循環信用／轉銷呆帳。另埋語意陷阱：呆帳市佔高是壞事 |
| `07_wide_ranking` | 2y | 一次餵 10 家，比較關係密度加倍 |
| `08_mixed_units` | 2y | 卡數／金額／比率／年增率四種量綱同頁 |

04 與 05 是同一件事的兩面，靠餵不同資料集達成 —— 這也是 FR-1.5 在 writer 端的體現。

樣本要夠寬才看得出真問題。實測案例：把 05–08 加進來之後才發現「比較句漏 claim」
是**八份全中**的系統性行為（模型只在 `key_message` 上漏，bullets 全都有），
而不是某個樣本的特例。只用窄樣本調 prompt，調出來的是過擬合。

---

## 附件四的定位

`附件四_預期修正參照資料.xlsx` 是命題方提供的參照檔，**不是本專案的資料來源，
也不是任何流程的必要輸入**。它不進版控，缺了不影響任何驗收。

它唯一的用途是**選用的外部交叉驗證**。它的 P.7 有一欄命題方自己算的市佔率，
而金管會月報沒有 —— 所以在它存在時，可以拿來驗我們的市佔率公式算得對不對。
`config/metric_definitions.json` 裡「全年加總 vs 最新月份」那條規則就是這樣定下來的
（用錯定義有 7 家名次改變，含第 3/4 名對調）。

相關測試在缺檔時自動 skip：

| 測試 | 驗什麼 |
|---|---|
| `test_engine.py` | 市佔率逐格等於附件四的衍生欄 |
| `test_metric_definitions.py` | 指標定義本身（用 pandas 獨立驗算） |
| `test_ingest_fsc.py` | 轉檔器產出逐格等於附件四 |
| `test_entity_slugs.py` | 兩個資料源的 key 完全一致 |

要跑這些，把檔案放進 `source/` 或設 `SLIDEGEN_XLSX`。
