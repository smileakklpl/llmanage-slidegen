"""合計列規則：占比的分母與排名的名次都必須排除來源報表自帶的合計列。

這條規則 `config/metric_definitions.json` 的 `ranking` 定義早有記載，
`tests/test_metric_definitions.py` 也用附件四驗過「定義本身」是對的；
這裡驗的是 `ppt_generation` 這一側的**實作**有照著做。

兩者分工：前者缺附件四就整份 skip，後者不吃任何外部檔案，
所以 CI（不放附件四）也擋得住。合計列一旦漏排，症狀是每家市占率剛好
少一半、名次整體位移一位——數字看起來很正常，靠肉眼審圖抓不到。
"""

import pytest

from ppt_generation.data import metric_engine
from ppt_generation.data.metric_store import MetricSeries


def _cross_section(
    categories,
    values,
    *,
    series_name="11412",
    axis_kind=metric_engine.AXIS_CATEGORICAL,
):
    """造一個橫斷面（entity 為類別軸）的基礎指標。"""
    return MetricSeries(
        metric_key="流通卡數.value",
        name="流通卡數",
        categories=list(categories),
        series={series_name: list(values)},
        unit="張",
        semantic="value",
        axis_kind=axis_kind,
    )


# ---------------------------------------------------------------------------
# is_total_category
# ---------------------------------------------------------------------------
@pytest.mark.parametrize(
    "label",
    [
        "總計",       # 金管會月報用這個
        "合計",
        "小計",
        "總和",
        "全體",
        "全市場",
        "市場總計",
        "總 計",      # 報表常見的字間空白
        "總　計",     # 全形空白
        "Total",
        "TOTAL",
        " total ",
        "Grand Total",
        "Subtotal",
    ],
)
def test_total_labels_are_recognized(label):
    assert metric_engine.is_total_category(label) is True


@pytest.mark.parametrize(
    "label",
    [
        "臺灣銀行",
        "中國信託",
        "台新銀行",
        "總計行庫",     # 含「總計」但不是合計列，不可誤判
        "合計金額",
        "小計欄位說明",
        "11412",        # 期間標籤
        "",
        "Totally Free Bank",
    ],
)
def test_non_total_labels_are_not_recognized(label):
    assert metric_engine.is_total_category(label) is False


def test_none_label_is_not_total():
    """類別欄可能有空值，正規化後不得意外命中。"""
    assert metric_engine.is_total_category(None) is False


# ---------------------------------------------------------------------------
# derive_share
# ---------------------------------------------------------------------------
def test_share_denominator_excludes_total_row():
    base = _cross_section(["A 銀行", "B 銀行", "總計"], [30.0, 70.0, 100.0])

    share = metric_engine.derive_share(base)

    assert share.computable is True
    assert share.unit == "%"
    assert share.semantic == "share"
    # 分母為 30 + 70 = 100，不是 30 + 70 + 100 = 200
    assert share.series["11412"] == [30.0, 70.0, None]


def test_share_without_exclusion_would_halve_every_entity():
    """反面斷言：把合計列算進分母，每家剛好少一半。

    這是實測到的症狀（臺灣銀行 0.2393% vs 正確 0.4786%）。留著這條，
    是為了讓「順手把 is_total_category 拿掉」的改動一定會紅燈。
    """
    base = _cross_section(["A 銀行", "B 銀行", "總計"], [30.0, 70.0, 100.0])

    share = metric_engine.derive_share(base)
    naive = 30.0 / (30.0 + 70.0 + 100.0) * 100

    assert share.series["11412"][0] == pytest.approx(naive * 2)


def test_share_of_total_row_is_none_not_hundred():
    """合計列不是參與競爭的實體，給它 100% 會在圖上多一根異質長條。"""
    base = _cross_section(["A 銀行", "B 銀行", "總計"], [30.0, 70.0, 100.0])

    share = metric_engine.derive_share(base)

    assert share.series["11412"][-1] is None


def test_share_records_which_rows_were_excluded():
    """排除了哪一列要寫進 notes——數字被改動過就必須留痕，這是可追溯性要求。"""
    base = _cross_section(["A 銀行", "B 銀行", "總計"], [30.0, 70.0, 100.0])

    share = metric_engine.derive_share(base)

    assert any("總計" in note for note in share.notes)


def test_share_without_total_row_is_unaffected():
    """沒有合計列的報表行為不變（回歸保護）。"""
    base = _cross_section(["A 銀行", "B 銀行"], [30.0, 70.0])

    share = metric_engine.derive_share(base)

    assert share.series["11412"] == [30.0, 70.0]
    assert share.notes == []


def test_share_blocked_on_temporal_axis():
    """各月占全年的比例不是市占率，防呆須擋下（附件三錯誤類型之一）。"""
    base = _cross_section(
        ["11401", "11402", "11403"],
        [10.0, 20.0, 30.0],
        series_name="流通卡數",
        axis_kind=metric_engine.AXIS_TEMPORAL,
    )

    share = metric_engine.derive_share(base)

    assert share.computable is False
    assert share.notes


def test_share_blocked_when_values_contain_negative():
    base = _cross_section(["A", "B", "總計"], [-30.0, 70.0, 40.0])

    share = metric_engine.derive_share(base)

    assert share.computable is False


# ---------------------------------------------------------------------------
# derive_rank
# ---------------------------------------------------------------------------
def test_rank_excludes_total_row_and_does_not_shift():
    base = _cross_section(
        ["A 銀行", "B 銀行", "C 銀行", "總計"],
        [30.0, 70.0, 50.0, 150.0],
    )

    rank = metric_engine.derive_rank(base)

    assert rank.computable is True
    assert rank.unit == "名"
    # 合計列若沒排除，它會佔第 1 名，B 就變第 2、C 第 3、A 第 4
    assert rank.series["11412"] == [3, 1, 2, None]


def test_rank_first_place_is_a_real_entity():
    base = _cross_section(
        ["A 銀行", "B 銀行", "總計"],
        [30.0, 70.0, 100.0],
    )

    rank = metric_engine.derive_rank(base)
    values = rank.series["11412"]
    winner = base.categories[values.index(1)]

    assert winner == "B 銀行"
    assert metric_engine.is_total_category(winner) is False


def test_rank_ties_share_the_smaller_number():
    """並列取較小名次（商業排名慣例），且不吃掉後續名次的連號。"""
    base = _cross_section(
        ["A", "B", "C", "總計"],
        [50.0, 50.0, 10.0, 110.0],
    )

    rank = metric_engine.derive_rank(base)

    assert rank.series["11412"] == [1, 1, 3, None]


def test_rank_records_which_rows_were_excluded():
    base = _cross_section(["A", "B", "總計"], [30.0, 70.0, 100.0])

    rank = metric_engine.derive_rank(base)

    assert any("總計" in note for note in rank.notes)


def test_rank_blocked_on_temporal_axis():
    base = _cross_section(
        ["11401", "11402", "11403"],
        [10.0, 20.0, 30.0],
        series_name="流通卡數",
        axis_kind=metric_engine.AXIS_TEMPORAL,
    )

    rank = metric_engine.derive_rank(base)

    assert rank.computable is False


def test_rank_blocked_with_single_category():
    base = _cross_section(["A 銀行"], [30.0])

    rank = metric_engine.derive_rank(base)

    assert rank.computable is False
