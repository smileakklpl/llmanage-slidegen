"""合併前的驗收關卡 — 一條指令跑完所有不需要模型的檢查。

    python verify_all.py

刻意**全部走 mock / 確定性路徑**：不碰 ollama、不需要 GPU、秒級跑完。
理由是合併前要回答的問題是「契約和管線有沒有壞」，不是「模型好不好」。
模型品質請跑 compare_models.py，那是另一件事、另一個節奏。

全綠代表：
  - 25 項單元測試通過（含轉檔器對附件四逐格比對、engine 市佔率零誤差）
  - 三個資料集都能端到端跑完（附件四 / fsc_114 / fsc_113_114）
  - FR-1.5 的開關會依資料翻轉（無基期全拒、有基期算得出）
  - 格式辨識器對附件四 100%
  - writer fixture 與引擎輸出沒有漂移

任何一項紅燈都代表**不該合併**——合併會把問題帶進共用 repo，
屆時其他模組的維護者也要跟著一起 debug。
"""

import bootstrap  # noqa: F401  補上 src/ 的 import 路徑

import io
import json
import subprocess
import sys
import time
from contextlib import redirect_stdout
from pathlib import Path
from typing import Callable, List, Tuple

from paths import DATA, GOLDEN, INPUTS_WRITER, REPO_ROOT, find_xlsx

DATASETS = [
    ("附件四", None),
    ("fsc_114", DATA / "fsc_114"),
    ("fsc_113_114", DATA / "fsc_113_114"),
]

Check = Tuple[str, Callable[[], str]]


class Skipped(Exception):
    """這項檢查所需的資料不在版控內。

    金管會月報進版控（政府公開資料，其他模組也要用），但命題素材（附件四）
    不進——那是主辦方給的東西，而這個 repo 是公開的。所以 CI 上拿得到前者、
    拿不到後者，要用附件四的項目就跑不了。

    缺資料是「這台機器沒有」，不是「程式壞了」，兩者混為一談的話 CI 會永遠紅燈，
    紅燈久了就沒人看——那比沒有 CI 更糟。

    但跳過也不能靜悄悄：跳掉的項目一律列在報表上，並在結尾標明覆蓋率打了折。
    本機把資料放好就會全部跑起來，那才是完整的驗收。
    """


def _need_dir(path: Path, what: str) -> Path:
    if path is None or not path.exists():
        raise Skipped(f"缺 {what}（{path}）")
    return path


def _need_xlsx() -> Path:
    found = find_xlsx()
    if found is None:
        raise Skipped("缺附件四（放進 source/ 或設 SLIDEGEN_XLSX）")
    return found


def _pytest() -> str:
    r = subprocess.run(
        [sys.executable, "-m", "pytest", "tests/", "-q"],
        cwd=REPO_ROOT, capture_output=True, text=True,
    )
    tail = (r.stdout or r.stderr).strip().splitlines()[-1]
    if r.returncode != 0:
        raise AssertionError(tail)
    return tail


def _layering() -> str:
    """`src/` 不得 import `evalh/` 或 `tools/`。

    產品碼依賴量測碼是反的：spike 與 harness 照定義是可以隨時砍掉的東西，
    不該有人依賴它們。這條界線曾經破過一次——pipeline.py 為了拿
    `load_provider` 和 `locate_one` 而 import evalh 與 tools，形成
    src → evalh → src 的循環，副作用是 README 寫的指令直接 ModuleNotFoundError。
    修好了不代表不會再破，所以留一條檢查守著。

    刻意用靜態掃描而非 import 測試：不必裝齊 pptx / boto3 等選用相依，
    也就不會因為環境缺套件而假性紅燈。
    """
    import re

    pattern = re.compile(r"^\s*(?:from|import)\s+(evalh|tools)\b", re.MULTILINE)
    offenders: List[str] = []
    for py in sorted((REPO_ROOT / "src").rglob("*.py")):
        for m in pattern.finditer(py.read_text(encoding="utf-8")):
            line = py.read_text(encoding="utf-8")[: m.start()].count("\n") + 1
            offenders.append(f"{py.relative_to(REPO_ROOT)}:{line} → {m.group(1)}")

    assert not offenders, (
        "產品碼 src/ 依賴了量測碼，方向反了：\n    " + "\n    ".join(offenders)
        + "\n需要的東西請搬進 src/（provider 工廠見 llm/factory.py，"
        "結構定位見 locator.py），不要反向 import。"
    )
    n = len(list((REPO_ROOT / "src").rglob("*.py")))
    return f"src/ 的 {n} 支檔案都沒有反向依賴 evalh/ 或 tools/"


def _pipeline(name: str, dataset) -> str:
    """端到端跑一次。用 mock provider，只驗管線接得起來。"""
    from contracts.narrative import PageNarrative
    from engine.metrics import build_store
    from engine.reader import read_sheet
    from engine.summarize import ranking_page, render_brief
    from pipeline import locate
    from llm.mock import MockProvider

    target = _need_dir(dataset, name) if dataset is not None else _need_xlsx()
    provider = MockProvider()
    smap, files, route = locate(provider, target, force_model=False)
    assert "模型" not in route, f"{name} 應該走確定性辨識，實際走了 {route}"

    by_name = {f.name: f for f in files}
    wanted = [s for s in smap.sheets if s.archetype == "entity_by_period"]
    if dataset is None:
        wanted = [s for s in wanted if s.sheet_name.startswith("P.7")]
    store = build_store([read_sheet(by_name.get(s.source_file, target), s) for s in wanted])

    brief_text = render_brief(ranking_page(store, "cards"), store)
    assert len(brief_text) < 1200, f"摘要 {len(brief_text)} 字元過長"

    res = provider.complete_json("", brief_text, PageNarrative)
    assert res.parsed is not None, "敘事階段沒有產出"
    return (f"{len(files)} 檔 / {len(smap.sheets)} 表，{route}；"
            f"MetricStore {len(store.computable_keys())} 可算 / "
            f"{len(store.uncomputable_keys())} 不可算；摘要 {len(brief_text)} 字元")


def _fr15_switch() -> str:
    """FR-1.5：同一段程式碼，資料決定 YoY 能不能算。"""
    from engine.metrics import build_store
    from engine.reader import read_sheet
    from engine.recognize import recognize_dataset

    out = []
    for name, d in [("fsc_114", DATASETS[1][1]), ("fsc_113_114", DATASETS[2][1])]:
        recs = recognize_dataset(_need_dir(d, name))
        sheets = []
        for f, rec in recs.items():
            if "流通卡數" in f.name:
                sheets = [read_sheet(f, s) for s in rec.sheets]
        store = build_store(sheets)
        yoy = [k for k in store.metrics if "_yoy_" in k]
        ok = [k for k in yoy if store.get(k).computable]
        out.append(f"{name}: {len(ok)}/{len(yoy)} 可算")

    assert out[0].endswith("0/396 可算"), f"僅 114 年時 YoY 不該可算：{out[0]}"
    assert "396/792" in out[1], f"有 113 基期時 YoY 應可算：{out[1]}"
    return "；".join(out)


def _recognizer() -> str:
    from contracts.sheet_map import SheetMap
    from engine.recognize import recognize_workbook
    from evalh.sheetmap_score import score

    rec = recognize_workbook(_need_xlsx())
    truth = SheetMap.model_validate(
        json.loads((GOLDEN / "sheet_map.json").read_text(encoding="utf-8"))
    )
    _, overall = score(SheetMap(workbook="x", sheets=rec.sheets), truth)
    assert overall == 1.0, f"辨識器對附件四只有 {overall:.0%}，低於 100% 就不該走快路徑"
    return f"附件四 {overall:.0%}（{rec.kind}）"


def _fixture_drift() -> str:
    """committed 的 writer fixture 必須等於引擎現在會產出的內容。

    這是上一輪真的發生過的 bug：改了 summarize.py 的格式，fixture 和 prompt
    都沒跟上，writer 的 prompt 開始描述引擎已不再產出的格式。
    有這條檢查，漂移就會變成紅燈而不是靜默分家。
    """
    from tools.gen_writer_fixtures import _store, briefs
    from engine.summarize import render_brief

    _need_xlsx()  # _store() 會讀附件四
    store = _store()
    drift: List[str] = []
    for name, brief in briefs(store).items():
        path = INPUTS_WRITER / f"{name}.txt"
        expected = render_brief(brief, store)
        if not path.exists() or path.read_text(encoding="utf-8") != expected:
            drift.append(name)
    assert not drift, (
        f"這些 fixture 與引擎輸出不一致：{drift}。"
        f"若是刻意改格式，跑 python -m tools.gen_writer_fixtures --write "
        f"重新產生，**並重跑 writer 基準線**。"
    )
    return f"{len(briefs(store))} 份 fixture 與引擎一致"


def _harness() -> str:
    from evalh.harness import report, run

    out = []
    for stage in ("intent", "writer"):
        recs = run("mock", None, 1, stage)
        text = report(recs)
        fails = [ln for ln in text.splitlines() if "檢查項失敗數" in ln]
        assert " 0 / " in fails[0], f"{stage} 有檢查項失敗：{fails[0]}"
        out.append(f"{stage} 全綠")
    return "；".join(out)


def main() -> int:
    checks: List[Check] = [
        ("單元測試", _pytest),
        ("分層依賴方向", _layering),
        ("格式辨識器", _recognizer),
        ("FR-1.5 開關", _fr15_switch),
        ("writer fixture 漂移", _fixture_drift),
        ("eval harness", _harness),
    ]
    checks += [
        (f"端到端：{name}", (lambda n=name, d=d: _pipeline(n, d)))
        for name, d in DATASETS
    ]

    print("=" * 72)
    print("合併前驗收（全部走確定性路徑，不呼叫任何模型）")
    print("=" * 72)

    failed = skipped = 0
    for label, fn in checks:
        t0 = time.perf_counter()
        try:
            buf = io.StringIO()
            with redirect_stdout(buf):
                detail = fn()
            print(f"  ✓ {label:<22s} {detail}  ({time.perf_counter() - t0:.1f}s)")
        except Skipped as e:
            skipped += 1
            print(f"  ⊘ {label:<22s} 跳過：{e}")
        except Exception as e:
            failed += 1
            print(f"  ✗ {label:<22s} {type(e).__name__}: {str(e)[:200]}")

    print("=" * 72)
    if failed:
        print(f"{failed} 項未通過 —— 不要合併，先修好。")
        return 1

    if skipped:
        # 綠燈但覆蓋率打折。講清楚，免得有人把「CI 過了」當成「全部驗過了」。
        print(f"已跑的 {len(checks) - skipped} 項全數通過，但有 {skipped} 項因缺資料跳過。")
        print("CI 上這是正常的：金管會月報進版控，命題素材（附件四）不進，")
        print("所以要用到附件四的項目跑不了。契約、import 與 fsc 兩組資料的端到端都是綠的；")
        print("但**合併前請把附件四放進 source/ 再跑一次**，跳過的那幾項才是完整驗收。")
    else:
        print("全數通過。契約與管線是完整的，可以合併。")
        print("模型品質是另一回事，請另外跑 python -m tools.compare_models。")
    return 0


if __name__ == "__main__":
    sys.exit(main())
