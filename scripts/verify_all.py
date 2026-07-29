"""合併前的驗收關卡 — 一條指令跑完所有不需要模型的檢查。

    python scripts/verify_all.py

刻意**全部走 mock / 確定性路徑**：不碰 ollama、不需要 GPU、秒級跑完。
理由是合併前要回答的問題是「契約和管線有沒有壞」，不是「模型好不好」。
模型品質請跑 compare_models.py，那是另一件事、另一個節奏。

全綠代表契約與管線是完整的。任何一項紅燈都代表**不該合併**。

所有檢查只依賴版控內的金管會月報，所以 CI 上跑的是完整驗收，不是打折版。
細節見 fixtures/README.md。
"""

import sys as _sys
from pathlib import Path as _Path

_sys.path.insert(0, str(_Path(__file__).resolve().parent))

import bootstrap  # noqa: E402,F401  補上 src/ 的 import 路徑

import io
import json
import subprocess
import sys
import time
from contextlib import redirect_stdout
from pathlib import Path
from typing import Callable, List, Tuple

from paths import FSC_1Y, FSC_2Y, GOLDEN, INPUTS_WRITER, REPO_ROOT

DATASETS = [("fsc_114", FSC_1Y), ("fsc_113_114", FSC_2Y)]

Check = Tuple[str, Callable[[], str]]


class Skipped(Exception):
    """這項檢查所需的資料不在。

    正常情況不該發生——資料都在版控內。會觸發代表有人刪了 fixtures/data/。
    缺資料是「這台機器沒有」不是「程式壞了」，兩者混為一談的話 CI 會永遠紅燈。
    """


def _need_dir(path: Path, what: str) -> Path:
    if path is None or not path.exists():
        raise Skipped(f"缺 {what}（{path}）")
    return path


def _pytest() -> str:
    # encoding 要明講：繁中 Windows 的 locale 是 cp950，解不了 pytest 的 UTF-8 輸出，
    # 會在讀取階段就 UnicodeDecodeError，蓋掉真正的測試失敗原因。
    r = subprocess.run(
        [sys.executable, "-m", "pytest", "tests/", "-q"],
        cwd=REPO_ROOT, capture_output=True, text=True,
        encoding="utf-8", errors="replace",
    )
    lines = (r.stdout or r.stderr).strip().splitlines()
    tail = lines[-1] if lines else "pytest 沒有輸出"
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

    target = _need_dir(dataset, name)
    provider = MockProvider()
    smap, files, route = locate(provider, target, force_model=False)
    assert "模型" not in route, f"{name} 應該走確定性辨識，實際走了 {route}"

    by_name = {f.name: f for f in files}
    wanted = [s for s in smap.sheets if s.archetype == "entity_by_period"]
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
    for name, d in DATASETS:
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
    """確定性辨識器的產出必須等於 committed 的 golden。

    golden 也是這支辨識器產生的，所以這不是「對不對」的驗證，是**回歸**驗證：
    改了 recognize.py 而 golden 沒跟著更新，就是紅燈。golden 的正確性由
    tests/ 那組斷言把關（見 fixtures/README.md）。
    """
    from contracts.sheet_map import SheetMap
    from engine.recognize import recognize_dataset
    from evalh.sheetmap_score import score

    recs = recognize_dataset(_need_dir(FSC_2Y, "fsc_113_114"))
    specs = []
    for f, rec in sorted(recs.items(), key=lambda kv: kv[0].name):
        for spec in rec.sheets:
            spec.source_file = f.name
            specs.append(spec)

    truth = SheetMap.model_validate(
        json.loads((GOLDEN / "sheet_map.json").read_text(encoding="utf-8"))
    )
    _, overall = score(SheetMap(workbook="x", sheets=specs), truth)
    assert overall == 1.0, f"辨識器與 golden 只吻合 {overall:.0%}"
    return f"{len(specs)} 張表與 golden 一致"


def _fixture_drift() -> str:
    """committed 的 writer fixture 必須等於引擎現在會產出的內容。

    真的發生過：改了 summarize.py 的格式，fixture 與 prompt 都沒跟上，
    於是 prompt 在描述一個引擎已不再產出的格式。
    """
    from tools.gen_writer_fixtures import all_briefs
    from engine.summarize import render_brief

    _need_dir(FSC_1Y, "fsc_114")
    _need_dir(FSC_2Y, "fsc_113_114")

    drift: List[str] = []
    items = all_briefs()
    for name, (brief, store) in items.items():
        path = INPUTS_WRITER / f"{name}.txt"
        expected = render_brief(brief, store)
        if not path.exists() or path.read_text(encoding="utf-8") != expected:
            drift.append(name)

    assert not drift, (
        f"這些 fixture 與引擎輸出不一致：{drift}。"
        f"若是刻意改格式，跑 python -m tools.gen_writer_fixtures --write "
        f"重新產生，**並重跑 writer 基準線**。"
    )
    return f"{len(items)} 份 fixture 與引擎一致"


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
        print("資料都在版控內，正常不該跳過——請確認 fixtures/data/ 沒有被刪。")
    else:
        print("全數通過。契約與管線是完整的，可以合併。")
        print("模型品質是另一回事，請另外跑 python -m tools.compare_models。")
    return 0


if __name__ == "__main__":
    sys.exit(main())
