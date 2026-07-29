"""程式進入點 — 端到端跑一次管線。

    python main.py --provider mock                    # 對照組，全綠
    python main.py --provider ollama --model gemma2:9b
    python main.py --dataset fixtures/data/fsc_114

實作在 `src/core/pipeline.py`；這支只做兩件事：補上 `src/` 的 import 路徑，
然後把 argv 交給它。

之所以需要這層轉接，是因為 `src/` 不是一個可安裝的套件，得靠 `bootstrap.py`
把它塞進 sys.path。直接跑 `python src/core/pipeline.py` 是不行的——那樣 repo root
不在 sys.path 上，pipeline 的相依一個都找不到。把進入點統一在 repo root，
使用者就不必知道這件事。

（等 `src/` 改成真正的套件、有 pyproject.toml 之後，這支可以換成
console_scripts 進入點，或直接刪掉。）
"""

import sys

import bootstrap  # noqa: F401  補上 src/ 的 import 路徑


def main() -> None:
    from pipeline import main as pipeline_main

    pipeline_main()


if __name__ == "__main__":
    sys.exit(main())
