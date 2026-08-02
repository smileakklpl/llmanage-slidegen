"""
文字品質檢查（錯別字、疊字、用詞）
====================================
審查 Agent 規則層的一部分，對應 reviewer.py 的「確定性檢查」。

## 為什麼需要這一層

先前產出的簡報出現「第一名名」這類重複字。追下去發現不是模型隨機寫錯，
而是一個**系統性**的邊界問題：

``placeholders.format_value()`` 產出的值**已經帶著單位或前後綴**——

| 單位 | 代入後的值 |
|---|---|
| ``名`` | ``第 3 名`` |
| ``%`` | ``12.3%`` |
| ``張`` | ``4,512,345張`` |

模型看到的是佔位符 ``{{排名.value|11412|max}}``，看不到它會展開成什麼，
於是很自然地寫成「排名第 {{…}} 名」，代入後就成了「排名第第 3 名名」。
敘事模板本身完全合法，錯誤只在代入後才出現——所以這個檢查**必須跑在
代入後的文字上**，`check_narrative()` 檢查原始模板是抓不到的。

## 檢查分三類

1. **佔位符邊界重複**（:func:`check_placeholder_boundaries`）：精準指出是哪個
   佔位符、與前後哪個字重複。這是上述 bug 的正解，錯誤訊息可直接讓
   narrative_writer 在重試時修對。
2. **疊字與疊詞**（:func:`check_duplicated_text`）：兜底網，抓邊界檢查漏掉的
   重複。中文本來就有合法疊字（漸漸、層層），因此有白名單。
3. **簡體字與錯別字**（:func:`check_character_set`）：簡體字混入繁中簡報是
   明確錯誤且模型很常犯。錯別字只收錄無爭議的幾組，不做「的／得／地」
   這類需要語境判斷的糾正——誤判會讓合法頁面被退件。
"""

from __future__ import annotations

import re
from typing import Sequence


#: 合法的中文疊字。中文的疊字是正常構詞，不能一律當錯字。
#: 只收錄商業書寫真的會用到的，寧可漏抓也不要誤判把合法頁面退件。
LEGITIMATE_REDUPLICATIONS: frozenset[str] = frozenset(
    {
        "一一",
        "上上",
        "下下",
        "人人",
        "個個",
        "種種",
        "層層",
        "步步",
        "年年",
        "月月",
        "日日",
        "天天",
        "時時",
        "處處",
        "漸漸",
        "稍稍",
        "略略",
        "頻頻",
        "屢屢",
        "多多",
        "大大",
        "小小",
        "深深",
        "緊緊",
        "牢牢",
        "正正",
        "剛剛",
        "偏偏",
        "早早",
        "遲遲",
        "區區",
        "彼彼",
    }
)

#: 不該出現在 zh-TW 簡報裡的簡體字 → 對應的正體字。
#:
#: 只收錄財經／商業敘事的高頻字。模型在 zh-TW 輸出中夾帶簡體字相當常見，
#: 而且是**無爭議**的錯誤，沒有誤判空間，所以這條規則可以放心 fail-closed。
SIMPLIFIED_CHARACTERS: dict[str, str] = {
    "优": "優",
    "势": "勢",
    "竞": "競",
    "争": "爭",
    "规": "規",
    "长": "長",
    "张": "張",
    "数": "數",
    "据": "據",
    "额": "額",
    "银": "銀",
    "应": "應",
    "该": "該",
    "现": "現",
    "发": "發",
    "达": "達",
    "过": "過",
    "关": "關",
    "键": "鍵",
    "时": "時",
    "间": "間",
    "业": "業",
    "务": "務",
    "总": "總",
    "计": "計",
    "减": "減",
    "变": "變",
    "风": "風",
    "险": "險",
    "预": "預",
    "测": "測",
    "结": "結",
    "构": "構",
    "组": "組",
    "织": "織",
    "级": "級",
    "类": "類",
    "别": "別",
    "项": "項",
    "标": "標",
    "准": "準",
    "质": "質",
    "价": "價",
    "费": "費",
    "汇": "匯",
    "报": "報",
    "财": "財",
    "经": "經",
    "营": "營",
    "资": "資",
    "产": "產",
    "负": "負",
    "债": "債",
    "动": "動",
    "净": "淨",
    "润": "潤",
    "税": "稅",
    "归": "歸",
    "属": "屬",
    "东": "東",
    "权": "權",
    "户": "戶",
    "认": "認",
    "识": "識",
    "两": "兩",
    "个": "個",
    "为": "為",
    "与": "與",
    "对": "對",
    "会": "會",
    "员": "員",
    "记": "記",
    "录": "錄",
    "开": "開",
    "闭": "閉",
    "统": "統",
    "确": "確",
    "实": "實",
    "际": "際",
    "转": "轉",
    "态": "態",
    "势": "勢",
    "题": "題",
    "显": "顯",
    "着": "著",
    "样": "樣",
    "边": "邊",
    "远": "遠",
    "进": "進",
    "运": "運",
    "选": "選",
    "适": "適",
    "验": "驗",
    "证": "證",
    "细": "細",
    "节": "節",
    "简": "簡",
    "单": "單",
    "复": "複",
    "杂": "雜",
}

# 防呆：表中若出現「簡體字與正體字相同」的項，該字每次出現都會被誤判成
# 簡體字，讓合法頁面被退件。這是手動維護對照表最容易犯的錯，
# 因此在匯入時就擋掉，而不是等到現場退件才發現。
_IDENTICAL = sorted(
    key for key, value in SIMPLIFIED_CHARACTERS.items() if key == value
)

if _IDENTICAL:
    raise ValueError(
        f"SIMPLIFIED_CHARACTERS 中這些字的簡繁寫法相同，應移除：{_IDENTICAL}"
    )

_NOT_SINGLE_CHAR = sorted(
    key for key in SIMPLIFIED_CHARACTERS if len(key) != 1
)

if _NOT_SINGLE_CHAR:
    raise ValueError(
        "SIMPLIFIED_CHARACTERS 只接受單一字元的 key（偵測是逐字進行的），"
        f"這些項永遠不會命中：{_NOT_SINGLE_CHAR}"
    )

#: 無爭議的錯別字 → 建議寫法。
#:
#: 刻意不收「的／得／地」「佔／占」「藉／借」這類需要語境或兩者皆通的情況。
#: 審查是 fail-closed 的，誤判的代價是合法頁面被退件，因此寧缺勿濫。
COMMON_TYPOS: dict[str, str] = {
    "驅勢": "趨勢",
    "曲勢": "趨勢",
    "增長率": "成長率",
    "盈收": "營收",
    "顯注": "顯著",
    "幅底": "幅度",
    "副度": "幅度",
    "持序": "持續",
    "城長": "成長",
    "衰褪": "衰退",
    "及使": "即使",
    "已及": "以及",
    "盡而": "進而",
    "盡管": "儘管",
    "在於說": "也就是說",
    "因該": "應該",
    "以經": "已經",
    "反應出": "反映出",
    "反應了": "反映了",
    "作為主要原因是": "主要原因是",
    "做出調整改變": "做出調整",
}

#: 中文字元範圍（含擴充區的常用部分）。
_CJK = r"\u3400-\u4dbf\u4e00-\u9fff"

#: 相鄰重複的中文字。
_ADJACENT_CJK_DUP = re.compile(rf"([{_CJK}])\1")

#: 相鄰重複的詞（2～4 字），例如「成長成長」「市場結構市場結構」。
_ADJACENT_WORD_DUP = re.compile(rf"([{_CJK}]{{2,4}})\1")

#: 重複的百分號或單位符號，例如代入後變成 ``12.3%%``。
_DUP_SYMBOL = re.compile(r"([%％‰])\1|([%％])\s*([%％])")


def _strip_spaces(text: str) -> str:
    return re.sub(r"\s+", "", text)


def check_duplicated_text(
    text: str,
    *,
    label: str = "",
    already_reported: frozenset[str] = frozenset(),
) -> list[str]:
    """
    偵測疊字與疊詞。

    ``label`` 用於錯誤訊息前綴（例如 ``"headline"`` 或 ``"要點 2"``）。
    ``already_reported`` 是已由邊界檢查精準回報過的字元，不再重複回報——
    同一個「第一名名」被兩條規則各報一次，只會讓重試用的 prompt 變吵，
    而邊界檢查的訊息已經指出該刪哪一個字。

    合法疊字（見 :data:`LEGITIMATE_REDUPLICATIONS`）不回報。詞級重複沒有
    白名單——「成長成長」這種在商業敘事裡沒有合法用法。
    """
    issues: list[str] = []
    prefix = f"{label}：" if label else ""
    compact = _strip_spaces(text)

    # 詞級重複先查。「市場市場」同時會觸發字級規則，先報詞級訊息更精確。
    word_dups = {
        match.group(1)
        for match in _ADJACENT_WORD_DUP.finditer(compact)
    }

    for word in sorted(word_dups):
        issues.append(
            f"{prefix}出現重複詞「{word}{word}」，請刪去其中一個"
        )

    char_dups = {
        match.group(1)
        for match in _ADJACENT_CJK_DUP.finditer(compact)
        if match.group(0) not in LEGITIMATE_REDUPLICATIONS
        and match.group(1) not in already_reported
        # 已被詞級規則涵蓋的不重複回報。
        and not any(match.group(0) in f"{word}{word}" for word in word_dups)
    }

    for char in sorted(char_dups):
        issues.append(
            f"{prefix}出現重疊字「{char}{char}」。"
            "常見原因是佔位符代入後的值已含該字，敘事又寫了一次"
        )

    for match in _DUP_SYMBOL.finditer(text):
        if match.group(0)[0] in already_reported:
            continue

        issues.append(
            f"{prefix}出現重複符號「{match.group(0)}」。"
            "佔位符代入後已含單位符號，敘事不需再寫一次"
        )

    return issues


def check_character_set(text: str, *, label: str = "") -> list[str]:
    """偵測簡體字與無爭議錯別字。"""
    issues: list[str] = []
    prefix = f"{label}：" if label else ""

    found_simplified = sorted(
        {char for char in text if char in SIMPLIFIED_CHARACTERS}
    )

    if found_simplified:
        pairs = "、".join(
            f"「{char}」應為「{SIMPLIFIED_CHARACTERS[char]}」"
            for char in found_simplified
        )
        issues.append(f"{prefix}出現簡體字：{pairs}。本簡報一律使用繁體中文")

    for wrong, right in COMMON_TYPOS.items():
        if wrong in text:
            issues.append(f"{prefix}用詞有誤：「{wrong}」應為「{right}」")

    return issues


def check_placeholder_boundaries(
    segments: Sequence[object],
    *,
    label: str = "",
) -> tuple[list[str], frozenset[str]]:
    """
    偵測「佔位符代入後與前後文字重複」。

    這是「第一名名」的正解。輸入是 :func:`placeholders.render_segments` 的
    輸出——它已經把文字切成「敘事字面」與「查表得來的值」兩種片段，
    因此只要檢查值片段的頭尾字元是否和相鄰字面片段重複即可，
    不必再去比對原始模板字串。

    ``第 3 名`` 這種**前後都有綴詞**的值，兩邊都會被檢查到：
    左邊抓「第」、右邊抓「名」。

    Args:
        segments: ``placeholders.TextSegment`` 序列。以 duck typing 取用
            ``text`` 與 ``from_metric``，避免本模組反向依賴 core。
        label: 錯誤訊息前綴。

    Returns:
        ``(違規訊息清單, 已精準回報的字元集合)``。第二項交給
        :func:`check_duplicated_text` 抑制重複回報。
    """
    issues: list[str] = []
    covered: set[str] = set()
    prefix = f"{label}：" if label else ""

    for index, segment in enumerate(segments):
        if not getattr(segment, "from_metric", False):
            continue

        value = _strip_spaces(getattr(segment, "text", ""))

        if not value:
            continue

        before = ""
        for previous in reversed(list(segments[:index])):
            before = _strip_spaces(getattr(previous, "text", ""))
            if before:
                break

        after = ""
        for following in segments[index + 1 :]:
            after = _strip_spaces(getattr(following, "text", ""))
            if after:
                break

        if before and before[-1] == value[0]:
            covered.add(value[0])
            issues.append(
                f"{prefix}佔位符代入後為「{segment.text}」，"
                f"開頭的「{value[0]}」與前一個字重複，"
                f"會顯示成「{before[-1]}{value[0]}」。"
                f"該值已自帶「{value[0]}」，請刪去佔位符前面的那一個"
            )

        if after and after[0] == value[-1]:
            covered.add(value[-1])
            issues.append(
                f"{prefix}佔位符代入後為「{segment.text}」，"
                f"結尾的「{value[-1]}」與後一個字重複，"
                f"會顯示成「{value[-1]}{after[0]}」。"
                f"該值已自帶單位「{value[-1]}」，請刪去佔位符後面的那一個"
            )

    return issues, frozenset(covered)


def check_rendered_line(
    segments: Sequence[object],
    *,
    label: str = "",
) -> list[str]:
    """
    對一行「已代入」的敘事跑完整的文字品質檢查。

    這是本模組對 reviewer 的單一入口。三類檢查一次跑完，並去重保序——
    同一個「第一名名」會同時觸發邊界規則與疊字規則，重複回報只會讓
    重試用的 prompt 變吵。邊界規則的訊息較精確，因此排在前面。
    """
    rendered = "".join(getattr(segment, "text", "") for segment in segments)

    boundary_issues, covered = check_placeholder_boundaries(
        segments, label=label
    )

    issues = [
        *boundary_issues,
        *check_duplicated_text(rendered, label=label, already_reported=covered),
        *check_character_set(rendered, label=label),
    ]

    seen: dict[str, None] = {}

    for issue in issues:
        seen.setdefault(issue, None)

    return list(seen)
