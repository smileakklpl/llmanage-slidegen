# 資料引擎與 LLM 層設計

> **版本**：v0.1
> **狀態**：engine 已驗證；Bedrock 已在 us-east-1 實測
> **對應規格書章節**：FR-1（數據解析）、FR-1.5（推導可行性）、FR-A1（LLM 統一介面）、§6.2（輸出修復）
> **相關程式**：`src/core/engine/`、`src/core/llm/`、`src/core/locator.py`

---

## 1. MetricStore key 命名

```
{實體slug}_{指標}_{期間}      ctbc_cards_11412
{實體slug}_{指標}_share       ctbc_cards_share
{實體slug}_{指標}_rank        ctbc_cards_rank
{實體slug}_{指標}_yoy_{期間}  ctbc_cards_yoy_11412
market_total_{指標}_{期間}    market_total_cards_11412
```

期間為民國年月六碼。實體 slug 由**機構名稱**決定，對照表在 `engine/metrics.py`
的 `ENTITY_SLUGS`，涵蓋月報全部 32 家發卡機構。

### 1.1 slug 不可由列序決定

早期版本對未收錄的機構退回 `bank_{列序}`。列序是來源檔案的屬性，不同來源
排序不同，同一家機構因此會拿到不同的 key。

這會使 key 失去識別能力：敘事寫 `{{bank_06_cards_share}}` 時，代入哪家機構
取決於載入了哪個檔案，與「每個數字可追溯至來源」的前提衝突。而轉檔器的逐格
比對驗的是儲存格值，偵測不到 slug 層級的錯位。

現行規則：查表命中即回傳，未命中退回名稱的 SHA-1 前六碼。
`tests/test_entity_slugs.py` 斷言月報上的機構全部命中，未命中即要求補表。

---

## 2. 三條指標規則

| 規則 | 定義 | 實作位置 |
|---|---|---|
| `market_share` | 全期間加總佔比 | `metric_definitions.json` |
| `ranking` | 依 `market_share` 降序，合計列已在 reader 排除 | 同上 |
| `yoy` | `entity[p] / entity[p-100] - 1` | 同上 |

定義外部化在 `metric_definitions.json`，不寫死在程式。業務規則會改，
不該散落各處；且該檔有獨立測試以 pandas 重算驗證。

### 2.1 market_share 用加總佔比而非最新月份佔比

兩種算法在同一份資料上結果不同：以最新月份計算時，簽帳金額有 7 家名次改變，
含第 3、4 名對調。`metric_definitions.json` 將最新月份法標記為
`wrong_but_intuitive` 並記錄誤差值。

### 2.2 YoY 不可算時標記，不丟例外也不填 0

缺基期時 `computable=false` 並附 `reason`，下游可以據此決定要不要提。
填 0 會讓簡報出現「年增率 0%」這種與事實不符的陳述，比缺值更有害。

此分支即 FR-1.5 的開關：`fsc_114`（單年）全部不可算，
`fsc_113_114`（雙年）396/792 可算。同一段程式碼，資料決定行為。

---

## 3. 摘要策略

六個指標檔 × 24 期 × 33 家機構產生近萬個 key，無法全部餵給 writer。
`engine/summarize.py` 按頁切，只送該頁需要的 key。

### 3.1 不可用指標按原因分組

省略不提時，模型會自行推導（實測輸出過 `{{cards_11412 - cards_11401}}`
這類算式）。逐一列舉又會使摘要從數百字元膨脹到上萬字元。

折衷是陳述**規則**而非清單：

```
不可用指標（engine 已判定 computable=false，不得引用也不得改用文字描述）：
  amex_spend_yoy_11401、amex_spend_yoy_11402 等 396 個：缺少基期 該期 的資料
```

模型需要的是「這一類不能碰」，不是完整 key 列表。

### 3.2 實值要給，禁寫規則放 prompt

writer 必須知道 A > B 才寫得出 claim，所以摘要一定要附實值。
「看得到但不准寫進敘事」是 prompt 的規則，不寫進資料 ——
同一條規則存在兩處會使 prompt 迭代無法歸因。

---

## 4. LLM 層

### 4.1 schema description 必須另外渲染進 prompt

約束解碼會丟失 description。Ollama（底層 llama.cpp）將 JSON Schema 轉為
GBNF grammar，grammar 只能表達結構（type / enum / properties / required），
description 與 title 無對應的文法產生式，轉換時捨棄。

結果是 description 有送進 payload，但模型讀不到。實測影響：帶描述的欄位
會被填入預設值或空陣列。`llm/base.py::schema_doc()` 把欄位說明渲染成文字
一併送出。

Bedrock 的 `toolConfig` 走另一條路徑，schema 原樣進 context，描述讀得到；
但重複送出對兩邊都無害。

### 4.2 輸出修復分四段

```
raw_text ──► ① fence 剝除 ──► ② JSON 容錯抽取 ──► ③ schema 驗證
                                                        │
                                            失敗 ◄───────┘
                                              │
                          ④ 帶錯誤訊息重試（上限 2 次）
                                              │
                                        仍失敗 ► fallback 預設文案
```

四段實作在 `llm/repair.py`，各自可獨立測試，不散進各家 adapter。

### 4.3 SheetMap 不提供 fallback

降級的前提是「錯了看得出來」。敘事錯了讀得出來，結構定位錯了不會 ——
`total_row` 填錯會產出一份數字全錯但外觀完全正常的簡報。

因此 `llm/fallbacks.py` 對敘事類 schema 提供預設文案，對 `SheetMap`
刻意不提供，定位失敗即中止管線。

---

## 5. Amazon Bedrock 接入

### 5.1 現況

| 項目 | 值 |
|---|---|
| 路徑 | `bedrock-runtime` + `converse`（legacy） |
| model id | `us.anthropic.claude-haiku-4-5-20251001-v1:0` |
| region | us-east-1 |
| 結構化輸出 | `toolConfig` + `toolChoice` 強制單一 tool |

Mantle client（`anthropic.claude-opus-5` 這類不帶版本的新式 id）在目前帳號
全部回 403/404，尚未開通。`us.` 前綴為必要，省略會得到 `ValidationException`。

### 5.2 實測結果

```
python -m evalh.harness --provider bedrock --stage writer --repeat 3

schema 通過率 : 100%（8 份輸入 × 3 次）
fallback      : 0%
p50 延遲      : 3.2–5.0s
```

`toolConfig` 強制 schema 穩定，`repair.py` 的 fence 剝除在這條路徑上
退化為安全網而非主要路徑。

### 5.3 新帳號接入的兩道門

兩者的錯誤訊息都不直接指向成因：

| 症狀 | 成因 | 處理 |
|---|---|---|
| `ResourceNotFoundException: Model use case details have not been submitted` | 未提交 Anthropic use case 表單 | Console 的 Model access 頁面已停用，表單改由 Playgrounds 首次呼叫時觸發；約 15 分鐘生效。`bedrock.get_use_case_for_model_access()` 可查狀態 |
| STS 呼叫即失敗，訊息提及 CRT | `aws login` / SSO 的 credential provider 需要 CRT | `pip install "botocore[crt]"`（AWS CLI 自帶，Python 環境的 botocore 沒有） |

### 5.4 待驗證

- `ThrottlingException` 在平行呼叫下的觸發點與錯誤形狀（harness 目前串行）
- prompt caching 實際命中率
- instance role 的 `bedrock:InvokeModel` 權限邊界

### 5.5 換模型時的相容性

`inferenceConfig.temperature` 在 Haiku 4.5 / Opus 4.5 / Sonnet 4.5 合法，
Opus 4.7 之後的模型已移除該參數，送出會得到 400。改用新模型時必須一併移除，
並改以 `output_config.effort` 控制推理深度。

---

## 6. 分層依賴

```
tests/  ──┐
tools/  ──┼──►  src/          （允許）
evalh/  ──┘

src/  ──╳──►  evalh/ tools/   （禁止，verify_all 靜態掃描）
```

`src/` 需能單獨出貨；`tools/` 下的 spike 依定義可隨時移除，量測骨架不應
成為產品的必要相依。違反時 `verify_all.py` 的「分層依賴方向」會紅燈並指出
檔案與行號。

---

## 7. 輸出編碼

報表使用的 ✓ ✗ 與中文在繁體 Windows 預設的 cp950 下無法編碼。輸出導向管線
或檔案時（CI log、`> out.txt`）會 `UnicodeEncodeError` 中止，且錯誤訊息會
覆蓋真正的失敗原因；互動式終端機不會顯現此問題。

處理位置：

| 位置 | 處理 |
|---|---|
| `bootstrap.py` | 將 stdout / stderr 釘為 UTF-8 |
| `verify_all.py` | 呼叫 pytest 的 subprocess 明指 `encoding="utf-8"` |
