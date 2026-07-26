"""專案路徑的唯一權威。

每支程式各自算 `Path(__file__).parent.parent` 的話，目錄一搬就要改六個地方，
而且每一處都是一次弄壞的機會。全部集中在這裡，之後搬動只需要改這支。

資料檔（附件四、金管會月報）不進版控——`.gitignore` 擋掉 `fixtures/data/` 與
`source/`（僅放行 template.pptx）。所以解析順序要涵蓋兩種擺法：
命題素材照專案慣例放 `source/`，衍生與測試資料放 `fixtures/data/`。
"""

import os
from pathlib import Path
from typing import List, Optional

SRC = Path(__file__).resolve().parent
REPO_ROOT = SRC.parent

PROMPTS = REPO_ROOT / "prompts"
FIXTURES = REPO_ROOT / "fixtures"
GOLDEN = FIXTURES / "golden"
DATA = FIXTURES / "data"
INPUTS_INTENT = FIXTURES / "inputs"
INPUTS_WRITER = FIXTURES / "inputs_writer"
SOURCE = REPO_ROOT / "source"
OUTPUTS = REPO_ROOT / "outputs"
METRIC_DEFS = REPO_ROOT / "metric_definitions.json"

XLSX_FILENAME = "附件四_預期修正參照資料.xlsx"
FSC_RAW = DATA / "金融業務資訊揭露"
ENV_VAR = "SLIDEGEN_XLSX"


def _candidates() -> List[Path]:
    return [
        SOURCE / XLSX_FILENAME,  # 專案慣例：命題素材放 source/
        DATA / XLSX_FILENAME,
        Path("/mnt/user-data/uploads") / XLSX_FILENAME,  # 雲端沙箱
    ]


def find_xlsx(cli_path: Optional[str] = None) -> Optional[Path]:
    """找到附件四就回傳路徑，找不到回傳 None。給 pytest 的 skipif 用。"""
    if cli_path:
        p = Path(cli_path).expanduser()
        return p if p.exists() else None

    env = os.environ.get(ENV_VAR)
    if env:
        p = Path(env).expanduser()
        return p if p.exists() else None

    return next((p for p in _candidates() if p.exists()), None)


def resolve_xlsx(cli_path: Optional[str] = None) -> Path:
    """同上，但找不到時直接報錯，並說清楚找過哪些地方。"""
    found = find_xlsx(cli_path)
    if found is not None:
        return found

    if cli_path:
        raise FileNotFoundError(f"--xlsx 指定的檔案不存在：{cli_path}")
    if os.environ.get(ENV_VAR):
        raise FileNotFoundError(f"環境變數 {ENV_VAR} 指向的檔案不存在：{os.environ[ENV_VAR]}")

    looked = "\n".join(f"  - {p}" for p in _candidates())
    raise FileNotFoundError(
        f"找不到「{XLSX_FILENAME}」。已找過：\n{looked}\n\n"
        f"三種解法擇一：\n"
        f"  1. 把檔案放進 {SOURCE}（專案慣例，命題素材的位置）\n"
        f"  2. 設環境變數 {ENV_VAR}=<檔案完整路徑>\n"
        f"  3. 執行時加 --xlsx <檔案完整路徑>"
    )
