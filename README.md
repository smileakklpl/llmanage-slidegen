# llmanage-slidegen
由 LLM 驅動的簡報自動生成工具，強調結構化與非結構化數據的讀取與分析能力，協助使用者快速將原始資料轉化為專業簡報。本專案為「2026 雲湧智生：臺灣生成式 AI 應用黑客松競賽」參賽作品，小組題目為「金融創新：台新新光金控_智匯數據簡報神器」，由 LLManage 團隊開發。

An LLM-powered presentation generation tool with a focus on robust data ingestion and analysis, transforming raw structured/unstructured data into polished, ready-to-use slide decks. Developed by team LLManage for the AIWave: Taiwan Generative AI Application Hackathon 2026, under the Financial Innovation track — Taishin-Shin Kong Financial Holding: Smart Data-to-Deck Assistant.

## 目錄結構

```
llmanage-slidegen/
├── README.md                          # 專案說明
├── main.py                            # 程式進入點（尚未實作）
├── requirements.txt                   # Python 套件依賴
├── docs/                              # 專案文件
│   ├── 智匯數據簡報神器_開發規格書_v0.3.md
│   └── 圖表原生性與資料同步設計.md      # PPT 圖表與 Excel 資料同步機制設計
├── source/                            # 命題原始素材（競賽單位提供）
│   ├── (台新新光金控) 命題文件 - 雲湧智生：臺灣生成式 AI 應用黑客松競賽.pdf
│   ├── template.pptx                  # 台新新光金控簡報模板
│   ├── 附件二_系統提示詞.docx           # 智匯數據簡報神器指令稿
│   ├── 附件三_信用卡範例簡報及錯誤說明.pptx
│   └── 附件四_預期修正參照資料.xlsx
├── src/                               # 原始碼
│   ├── chart_builder.py               # 圖表生成模組（ChartSpec / add_chart 封裝）
│   └── verify_chart_consistency.py    # 圖表與內嵌工作表一致性驗證腳本
└── outputs/                            # 生成輸出結果（測試/示範用）
    └── demo_chart.pptx
```
