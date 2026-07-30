"""Top N 切片與市場期間序列：兩個「換視角但不換數字」的衍生指標。

兩者都是為了讓附件三的範例頁做得出來：

- Top N：33 家機構畫不成圓餅（12 個扇形就到上限）、做不成一頁的表。
  附件三多頁都是「Top 10 銀行」。
- 市場期間序列：月報是「機構 × 期間」交叉表，類別軸是機構，算不出趨勢；
  而 P.5（官方主打頁）要的是 12 個月的流通卡數長條 + 簽帳金額折線。

共同的紅線：**兩者都不重新計算任何數值**。Top N 若在切片後重算市占率，
分母會變成「Top 10 的總和」，每家都被高估；期間序列若自己加總而不優先取
來源報表的總計列，就會漏掉未逐家列出的機構。
"""

import pytest

from ppt_generation.data import metric_engine
from ppt_generation.data.metric_store import MetricSeries


BANKS = ["A", "B", "C", "D", "E", "總計"]
VALUES = [50.0, 40.0, 30.0, 20.0, 10.0, 150.0]


def _cross_section(categories=None, series=None, unit="張", semantic="value"):
    return MetricSeries(
        metric_key="流通卡數.value",
        name="流通卡數",
        categories=list(categories or BANKS),
        series=series or {"11412": list(VALUES)},
        unit=unit,
        semantic=semantic,
    )


def _entity_by_period(name="流通卡數", scale=1.0, total_row=True):
    """機構 × 期間交叉表：類別是機構，系列是期間。"""
    categories = ["A", "B"] + (["總計"] if total_row else [])
    periods = ["11401", "11402", "11403", "11404"]
    series = {}

    for index, period in enumerate(periods):
        a = (100.0 + index) * scale
        b = (200.0 + index * 2) * scale
        column = [a, b]

        if total_row:
            # 刻意讓總計列大於 A+B，模擬「未逐家列出的機構」
            column.append(a + b + 10.0 * scale)

        series[period] = column

    return MetricSeries(
        metric_key=f"{name}.value",
        name=name,
        categories=categories,
        series=series,
        unit="張",
    )


# ---------------------------------------------------------------------------
# derive_top
# ---------------------------------------------------------------------------
def test_top_keeps_only_n_categories():
    top = metric_engine.derive_top(_cross_section(), n=3)

    assert top.computable is True
    assert top.categories == ["A", "B", "C"]
    assert top.series["11412"] == [50.0, 40.0, 30.0]


def test_top_excludes_total_row():
    """合計列一定最大，不排除會佔掉一個名額並讓 Top 10 只剩 9 家。"""
    top = metric_engine.derive_top(_cross_section(), n=3)

    assert "總計" not in top.categories


def test_top_does_not_recompute_share():
    """市占率切片後仍是對全市場的占比。重算分母會讓每家都被高估。"""
    shares = [10.0, 8.0, 6.0, 4.0, 2.0, None]
    share_metric = _cross_section(
        series={"11412": shares}, unit="%", semantic="share"
    )
    share_metric.metric_key = "流通卡數.share"

    top = metric_engine.derive_top(share_metric, n=3)

    assert top.series["11412"] == [10.0, 8.0, 6.0]
    # 切片後總和遠小於 100%，正是「這是全市場占比」的證據
    assert sum(top.series["11412"]) < 100


def test_top_key_and_semantic_are_preserved():
    top = metric_engine.derive_top(_cross_section(), n=3)

    assert top.metric_key == "流通卡數.value.top3"
    assert top.semantic == "value"
    assert top.unit == "張"


def test_top_ranks_by_the_last_series_by_default():
    """預設依最後一個系列（通常是最新一期）排序，不是第一期。"""
    metric = _cross_section(
        categories=["A", "B", "C"],
        series={
            "11401": [10.0, 20.0, 30.0],
            "11412": [30.0, 20.0, 10.0],
        },
    )

    top = metric_engine.derive_top(metric, n=2)

    assert top.categories == ["A", "B"]


def test_top_can_rank_by_a_named_series():
    metric = _cross_section(
        categories=["A", "B", "C"],
        series={
            "11401": [10.0, 20.0, 30.0],
            "11412": [30.0, 20.0, 10.0],
        },
    )

    top = metric_engine.derive_top(metric, n=2, by="11401")

    assert top.categories == ["C", "B"]


def test_top_notes_that_it_is_a_subset():
    """看到一張只有 10 根長條的圖，讀者有權知道其實有 33 家。"""
    top = metric_engine.derive_top(_cross_section(), n=3)

    assert any("前 3 名" in note for note in top.notes)


def test_top_blocked_on_temporal_axis():
    """對月份取前 10 名會打亂時間順序，折線圖會變成無意義的鋸齒。"""
    metric = _cross_section(
        categories=["11401", "11402", "11403"],
        series={"流通卡數": [1.0, 3.0, 2.0]},
    )
    metric.axis_kind = metric_engine.AXIS_TEMPORAL

    top = metric_engine.derive_top(metric, n=2)

    assert top.computable is False


def test_top_blocked_when_not_enough_categories():
    """類別數沒超過 N 就沒有切的必要，應直接用原指標。"""
    top = metric_engine.derive_top(_cross_section(), n=10)

    assert top.computable is False


def test_top_carries_evidence_of_kept_rows_only():
    top = metric_engine.derive_top(_cross_section(), n=3)

    assert all(
        key.split("|", 1)[-1] in top.categories for key in top.evidence
    )


# ---------------------------------------------------------------------------
# 期間標籤辨識
# ---------------------------------------------------------------------------
@pytest.mark.parametrize(
    "label",
    ["11401", "11412", "114", "2026", "202601", "1月", "12月", "2026-01"],
)
def test_period_labels(label):
    assert metric_engine.is_period_label(label) is True


@pytest.mark.parametrize("label", ["中國信託", "11413", "台新銀行", "合計"])
def test_non_period_labels(label):
    assert metric_engine.is_period_label(label) is False


def test_roc_period_columns_are_detected_as_temporal_axis():
    """轉置後類別軸是 11401…，不認得的話趨勢與外推會全被防呆擋掉。"""
    kind = metric_engine.detect_axis_kind(["11401", "11402", "11403"])

    assert kind == metric_engine.AXIS_TEMPORAL


# ---------------------------------------------------------------------------
# build_market_timeline
# ---------------------------------------------------------------------------
def test_timeline_transposes_to_a_temporal_axis():
    timeline = metric_engine.build_market_timeline(
        [_entity_by_period("流通卡數"), _entity_by_period("當月簽帳金額", 5.0)]
    )

    assert timeline is not None
    assert timeline.axis_kind == metric_engine.AXIS_TEMPORAL
    assert timeline.categories == ["11401", "11402", "11403", "11404"]
    assert set(timeline.series) == {"流通卡數", "當月簽帳金額"}


def test_timeline_prefers_the_source_total_row():
    """
    來源報表的「總計」列是主管機關公告的數字，涵蓋未逐家列出的機構。
    自己加總會少掉那一塊——這裡刻意讓總計列比 A+B 多 10。
    """
    timeline = metric_engine.build_market_timeline([_entity_by_period()])

    assert timeline.series["流通卡數"][0] == 100.0 + 200.0 + 10.0


def test_timeline_falls_back_to_summing_entities():
    timeline = metric_engine.build_market_timeline(
        [_entity_by_period(total_row=False)]
    )

    assert timeline.series["流通卡數"][0] == 300.0


def test_timeline_records_how_each_total_was_obtained():
    """數字是取來的還是加出來的，必須留痕（可追溯性）。"""
    timeline = metric_engine.build_market_timeline([_entity_by_period()])

    assert any("總計" in note for note in timeline.notes)


def test_timeline_has_no_unit():
    """卡數與金額不同單位，謊稱一個共同單位比留空更糟。"""
    timeline = metric_engine.build_market_timeline(
        [_entity_by_period("流通卡數"), _entity_by_period("當月簽帳金額", 5.0)]
    )

    assert timeline.unit is None


def test_timeline_key_ends_with_value():
    """衍生鍵是用 replace('.value', suffix) 換出來的，少了尾巴會相撞。"""
    timeline = metric_engine.build_market_timeline([_entity_by_period()])

    assert timeline.metric_key.endswith(".value")


def test_timeline_skips_metrics_with_different_periods():
    other = _entity_by_period("有效卡數")
    other.series = {"11301": [1.0, 2.0, 3.0], "11302": [1.0, 2.0, 3.0]}

    timeline = metric_engine.build_market_timeline(
        [_entity_by_period("流通卡數"), other]
    )

    assert set(timeline.series) == {"流通卡數"}
    assert any("不一致" in note for note in timeline.notes)


def test_timeline_returns_none_for_non_cross_tab_data():
    """資料不是交叉表時要明確回 None，而不是硬湊出一個假的時間軸。"""
    metric = _cross_section(
        categories=["A", "B"], series={"流通卡數": [1.0, 2.0]}
    )

    assert metric_engine.build_market_timeline([metric]) is None


def test_timeline_unlocks_growth_and_forecast():
    """
    這才是做轉置的理由：交叉表上被防呆擋掉的趨勢類指標，轉置後可算。
    FR-2.6 的「未來趨勢推測」章節一律引用 forecast，沒有它那章就空了。
    """
    timeline = metric_engine.build_market_timeline([_entity_by_period()])

    growth = metric_engine.derive_period_growth(timeline)
    forecast = metric_engine.derive_forecast(timeline, periods=2)

    assert growth.computable is True
    assert forecast.computable is True
    assert forecast.categories[-2:] == ["預測+1", "預測+2"]


def test_forecast_categories_are_labelled_as_estimates():
    """外推值要標成「預測」，不能與實際觀測值混在一起。"""
    timeline = metric_engine.build_market_timeline([_entity_by_period()])

    forecast = metric_engine.derive_forecast(timeline, periods=3)

    assert all(
        category.startswith("預測")
        for category in forecast.categories[-3:]
    )
    assert any("外推值" in note for note in forecast.notes)
