"""專案路徑的唯一權威。目錄搬動只需要改這支。"""

import os
from pathlib import Path
from typing import List, Optional

CORE = Path(__file__).resolve().parent   # src/core
SRC = CORE.parent                        # src
REPO_ROOT = SRC.parent

PROMPTS = REPO_ROOT / "prompts"
FIXTURES = REPO_ROOT / "fixtures"
GOLDEN = FIXTURES / "golden"
DATA = FIXTURES / "data"
INPUTS_INTENT = FIXTURES / "inputs" / "intent"
INPUTS_WRITER = FIXTURES / "inputs" / "writer"
BASELINES = FIXTURES / "baselines"
SOURCE = REPO_ROOT / "source"
OUTPUTS = REPO_ROOT / "outputs"
METRIC_DEFS = REPO_ROOT / "metric_definitions.json"

# 金管會月報：原始檔與轉檔產出。這是本專案的資料來源。
FSC_RAW = DATA / "金融業務資訊揭露"
FSC_1Y = DATA / "fsc_114"        # 11401–11412，單年 → YoY 不可算
FSC_2Y = DATA / "fsc_113_114"    # 11301–11412，雙年 → YoY 可算

# 命題方提供的參照檔。非必要，僅供 tests/ 做外部交叉驗證（見 fixtures/README.md）。
XLSX_FILENAME = "附件四_預期修正參照資料.xlsx"
ENV_VAR = "SLIDEGEN_XLSX"


def _candidates() -> List[Path]:
    return [SOURCE / XLSX_FILENAME, DATA / XLSX_FILENAME]


def find_xlsx(cli_path: Optional[str] = None) -> Optional[Path]:
    """找到參照檔就回傳路徑，找不到回傳 None。給 pytest 的 skipif 用。"""
    if cli_path:
        p = Path(cli_path).expanduser()
        return p if p.exists() else None

    env = os.environ.get(ENV_VAR)
    if env:
        p = Path(env).expanduser()
        return p if p.exists() else None

    return next((p for p in _candidates() if p.exists()), None)


def resolve_xlsx(cli_path: Optional[str] = None) -> Path:
    """同上，但找不到時報錯並列出找過的位置。"""
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
