# writer 固定輸入 —— 已凍結，不要修改

與 `../inputs/`（intent）同一條紀律：**輸入固定，prompt 演進。**
兩者同時變，就分不清品質變化是誰造成的。

## 這些檔案由引擎產生，不要手改

    python -m engine.gen_writer_fixtures            # 預覽
    python -m engine.gen_writer_fixtures --write    # 覆寫

手寫過兩版，兩次都漂移：

1. **格式漂移**——`summarize.py` 的不可用指標從逐行列改成按原因分組之後，
   fixture 和 `insight_writer.system.md` 的格式說明都沒跟上，
   於是 prompt 在描述一個引擎已經不再產出的格式。
2. **key 漂移**——手寫的是 `ctbc_share` / `taishin_rank`，
   引擎實際產出 `ctbc_cards_share` / `taishin_cards_rank`。

沒有東西強迫手寫 fixture 跟上引擎，所以它必然會漂移。改由引擎產生就結構性地解決了。

**但產生完仍然要凍結。** 產生 → commit → 凍結；
只有在引擎輸出格式**刻意**改變時才重新產生，而重新產生等於作廢既有的
writer 基準線，必須重跑。不要為了讓數字好看而重新產生。

`verify_all.py` 的「writer fixture 漂移」會斷言 committed 的內容
仍等於引擎現在的產出——所以漂移是紅燈，不會像上次那樣安靜地分家。

## 這同時是給 A 的介面規格

由引擎產生有一個代價要講清楚：手寫版本表達的是「**A，我需要你送這種東西給我**」，
是協商中的介面；引擎產生版本表達的是「**我的引擎目前吐這種東西**」，
是單方面的實作現況。若 A 照規格書實作出不同形狀，這裡不會警告你。

所以請把本目錄的檔案當成**要跟 A 對齊的目標**：
MetricStore → writer 的摘要格式規格書沒有定義（README 待辦已記），
這四份檔案就是 B 提出的具體提案。定案前需要 A 確認：

  - key 命名：`{實體slug}_{指標}_{期間}`、`_share`、`_rank`、`_yoy_{期間}`
    （合計列用 `market_total_` 前綴，對齊規格書 §5.2 的範例）
  - 不可用指標按原因分組陳述，不逐一列舉
  - 實值直接給（writer 要判斷大小關係才寫得出 claim）

## 檔案只放資料，不放指示

初版曾把「數值只供判斷關係、不得寫進敘事」這類句子寫在每份輸入結尾。
那是**指示不是資料**，等於同一條規則同時存在於 prompt 與 fixture 兩處——
改 prompt 時無法判斷結果變化來自何者。已全部移進
`prompts/insight_writer.system.md`，本目錄只保留：

    頁碼 / narrative_id / 頁面主題
    可用指標（key = 實值）
    不可用指標（選填，engine 判定 computable=false 者）

## 四份輸入各自的考點

| 檔案 | 考什麼 |
|---|---|
| 01_p5_market_overview | 有明確可比關係，看會不會把實值寫死進敘事 |
| 02_p7_ranking | 排名情境，最誘人直接寫出市佔率數字 |
| 03_no_comparison | 兩個單位不同的指標，看會不會硬湊 claims |
| 04_yoy_unavailable | 列出不可用的 YoY key，看會不會偷用或改用文字迂迴描述 |

指標實值取自附件四，但**不需要與附件四逐格一致**——
writer 不做計算，這些值只用來讓它判斷大小關係。
