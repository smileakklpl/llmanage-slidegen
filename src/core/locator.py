"""結構定位（LLM）— profiler 的純文字描述 → SheetSpec。

這支原本住在 `tools/spike_a.py`。那是錯的位置：`pipeline.py` 得
`from tools.spike_a import locate_one` 才跑得完，等於產品管線依賴一支
開發期的 spike 腳本——spike 照定義是可以隨時砍掉的東西，不該有人依賴它。

放在 `src/` 頂層而不是 `engine/`，是因為 engine 的分際是
「確定性、不碰 LLM」（見 README §目錄結構）。這支要打模型，
放進 engine 會破壞那條界線。它與 `validator.py` 同層，性質也相近：
單一職責、夾在管線中間的一段。

方向仍然是單向的：`spike_a.py` 改成從這裡 import，量它的命中率；
被量的東西住在產品碼裡，量它的工具住在 tools/。
"""

from contracts.sheet_map import SheetMap


def locate_one(provider, system: str, sheet_name: str, text: str):
    """對單一張工作表做結構定位，回傳 (SheetSpec 或 None, LLMResult)。

    schema 仍用 SheetMap 而非 SheetSpec：一來 MockProvider 是按 schema 類別名
    載 golden fixture（改成 SheetSpec 就得多維護一份，且對照組會退化成
    同一張表回四次），二來模型本來就傾向回傳帶 sheets 陣列的完整物件。
    取回後再按名稱挑出該張。
    """
    # SheetMap 刻意不給 fallback（見 fallbacks.py）：結構定位錯了不會長得像錯的，
    # total_row 填錯只會產出一份數字全錯但外觀正常的簡報。寧可失敗也不要靜默降級。
    res = provider.complete_json(system, text, SheetMap)
    if res.parsed is None:
        return None, res

    spec = res.parsed.get(sheet_name)
    if spec is None and len(res.parsed.sheets) == 1:
        # 模型只回一張但名稱抄錯。結構判斷仍可用，改名後採用並留下痕跡。
        spec = res.parsed.sheets[0]
        spec.notes.append(f"模型回填的 sheet_name 為 {spec.sheet_name!r}，已更正")
        spec.sheet_name = sheet_name
    return spec, res
