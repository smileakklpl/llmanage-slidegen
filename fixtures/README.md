# fixtures — 可重現的正式管線輸入

## 資料來源

本專案以金管會「金融業務資訊揭露」信用卡月報作為主要可重現資料集。資料公開、每月更新且格式固定，適合驗證 ingestion、確定性 metric engine 與 PPT/XLSX 三方一致性。

```text
fixtures/data/
├── 金融業務資訊揭露/       原始月報（11301–11412，一月一目錄）← 真相來源，唯讀
├── fsc_114/                11401–11412，多檔單表版型
├── fsc_113_114/            11301–11412，多檔單表版型
├── fsc_114_workbook.xlsx   114 年同源資料，單檔多工作表版型
└── 旅遊資料/               旅遊資料_2025-01..12.xlsx（12 檔）
```

### `旅遊資料/` 的定位（待釐清）

這 12 個檔案**目前沒有任何程式碼或測試引用**，也沒有對應的產生工具，來源不明。

跨領域測試（`tests/test_cross_domain_pipeline.py`）驗證的是餐飲／旅遊／股價三種
領域，但它的旅遊資料是在 `tmp_path` 內**即時合成**的，並非讀取這個目錄。

因此這批檔案屬於孤兒資料：可能是早期手動蒐集的驗證素材。要嘛補上來源說明與
產生方式，要嘛移除，否則它會讓「fixtures 皆可重現」這句話不成立。

`fsc_114/` 與 `fsc_114_workbook.xlsx` 數值同源，分別驗證現實中常見的兩種 Excel 擺法。正式 `scripts/verify_all.py` 使用 `fsc_114_workbook.xlsx`，從 backend ingestion 一路跑到 generation orchestrator、原生 PPT、稽核 XLSX 與 T1。

## 重新產生資料

Windows PowerShell，從 repo root 執行：

```powershell
python -m tools.ingest_fsc --out fixtures/data/fsc_114 --periods 11401,11402,11403,11404,11405,11406,11407,11408,11409,11410,11411,11412
python -m tools.build_fsc_workbook --out fixtures/data/fsc_114_workbook.xlsx --periods 11401,11402,11403,11404,11405,11406,11407,11408,11409,11410,11411,11412
```

兩支工具共用 `tools.ingest_fsc.read_month()`，只輸出來源原始量。市占率、排名、期間成長率、YoY 與預測一律由 `ppt_generation.data.metric_engine` 即時計算，不把衍生量寫回 fixture，避免形成第二個數值真相來源。

## FR-1.5 防呆

正式 engine 的 YoY 行為由 `tests/test_metric_engine_views.py` 直接驗證：

- 只有單一年度系列時，衍生指標存在但 `computable=false` 並附原因。
- 有兩個年度系列時，依 `(當年 - 去年) / 去年 × 100%` 計算。
- 支援完整西元年與民國三位年度標籤，但不會把 `11401` 這類年月誤判成年度。

## 附件四的定位

`附件四_預期修正參照資料.xlsx` 是命題方參照檔，不是 production pipeline 的必要輸入。若存在於 `source/`，或由 `SLIDEGEN_XLSX` 指定，以下測試會做額外交叉驗證：

- `tests/test_metric_definitions.py`：用 pandas 獨立重算市占率定義。
- `tests/test_ingest_fsc.py`：用 openpyxl 逐格比較轉檔結果。

缺少附件四時只跳過外部參照測試；正式 full-pipeline smoke 仍使用版控內的公開資料完整執行。

## 模型比較

模型比較不再使用獨立 golden/contracts stack。`python -m tools.compare_models` 會對每個模型跑相同 Excel、prompt、章節與完整 generation pipeline，並以 reviewer fail-closed 與 T1 通過作為成功條件。所有 A/B 產物寫到系統 temp，不會修改 `outputs/`。
