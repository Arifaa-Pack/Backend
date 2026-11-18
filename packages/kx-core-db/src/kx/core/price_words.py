"""M7: money amounts the digit price guard cannot see (security review 19 Nov).

`kx.core.price_guard` only finds amounts that start with a digit. A buyer who asks "write all numbers in
words" could get "nineteen fils per piece", "zero point one nine dirhams each" or "خمسة عشر درهما" past
it, or digits split by spaces ("AED 0 . 1 9"). This module adds two deterministic checks; callers treat
any hit exactly like an unapproved digit price (fail closed: regenerate once, then the safe reply).

* `spelled_amounts`: number words (English, Arabic, Hindi, Chinese, plus half / quarter / point style
  fractions) next to a currency or price word (AED, dirham, fils, USD, dollar, درهم, فلس, रुपये, 迪拉姆,
  "per piece" ...). The AI is told to write every number as digits, so a number word next to money is
  never legitimate.
* `collapse_spaced_digits`: "0 . 1 9" -> "0.19", for a second pass of the digit guard.
"""

from __future__ import annotations

import re
from typing import Final

__all__ = ["MONEY_CUE", "collapse_spaced_digits", "spelled_amounts"]


def _words(*lines: str) -> frozenset[str]:
    return frozenset(w for line in lines for w in line.split())


_EN_NUMBERS: Final = _words(
    "zero two three four five six seven eight nine ten eleven twelve thirteen fourteen fifteen sixteen",
    "seventeen eighteen nineteen twenty thirty forty fifty sixty seventy eighty ninety hundred hundreds",
    "thousand thousands million millions lakh lakhs crore crores",
)
_AR_NUMBERS: Final = _words(
    "صفر اثنان اثنين اثنتان اثنتين ثلاثة ثلاث اربعة اربع خمسة خمس ستة ست سبعة سبع ثمانية ثماني ثمان",
    "تسعة تسع عشرة عشر عشرون عشرين ثلاثون ثلاثين اربعون اربعين خمسون خمسين ستون ستين سبعون سبعين",
    "ثمانون ثمانين تسعون تسعين مئة مائة مئتان مئتين مائتان مائتين الف الاف الفان الفين مليون",
)
_HI_NUMBERS: Final = _words(
    "शून्य दो तीन चार पांच पाँच छह छः सात आठ नौ दस ग्यारह बारह तेरह चौदह पंद्रह पन्द्रह सोलह सत्रह",
    "अठारह उन्नीस बीस पच्चीस तीस चालीस पचास साठ सत्तर अस्सी नब्बे सौ हज़ार हजार लाख करोड़",
)
# also ordinary words ("one of our team", "أحد", "एक" = "a"): they count only right next to a currency
_AMBIGUOUS: Final = _words("one a an واحد واحدة احد احدى एक")
_FRACTIONS: Final = _words("half quarter quarters third thirds نصف ربع ثلث आधा आधे पाव चौथाई")
_JOINERS: Final = _words("point and فاصلة و दशमलव पॉइंट")
_CURRENCY: Final = _words(
    "aed dhs dh dirham dirhams fils fil usd dollar dollars cent cents eur euro euros sar riyal riyals",
    "rial rials inr rupee rupees rs paise gbp pound pounds qar omr kwd bhd dinar dinars baisa halala",
    "درهم درهما دراهم فلس فلسا فلوس دولار دولارا دولارات ريال ريالا ريالات دينار دينارا دنانير يورو",
    "روبية روبيات سنت هللة",
    "दिरहम रुपये रुपए रुपया रुपयों पैसे पैसा डॉलर रियाल यूरो",
)
_PRICE: Final = _words(
    "price prices priced pricing rate rates quote quoted quotation discount discounted off total apiece",
    "each سعر اسعار خصم قيمة مجموع कीमत दाम मूल्य भाव छूट",
)
_PER: Final = _words("per لكل प्रति")
_PER_UNIT: Final = _words("piece pc pcs unit item قطعة पीस नग")

_TOKEN: Final = re.compile(r"[^\s.,;:!?()\[\]{}\"'،؛؟।\-–—/]+")
_ARABIC_MARKS: Final = re.compile(f"[{chr(0x064B)}-{chr(0x0652)}{chr(0x0640)}]")  # tanween, harakat, tatweel
_ARABIC_ALIF: Final = str.maketrans({"أ": "ا", "إ": "ا", "آ": "ا", "ى": "ي"})

# Chinese is written without spaces: a run of number characters next to a currency, a discount ("八折")
# or after a price word ("价格是十九").
_ZH_NUM: Final = "[零〇一二两三四五六七八九十百千万点半]+"
_ZH_CUR: Final = "(?:迪拉姆|美元|美金|欧元|卢比|里亚尔|英镑|人民币|块钱|块|毛钱|毛|角|分钱|元)"
_ZH_AMOUNT: Final = re.compile(rf"{_ZH_NUM}\s?(?:{_ZH_CUR}|折)")
_ZH_AFTER_PRICE: Final = re.compile(rf"(?:价格|单价|报价|售价|每个|每件|每只)(?:是|为|:|：)?\s?({_ZH_NUM})")

# Digits split by spaces: "0 . 1 9", "1 9 fils" (only runs that contain a space are rewritten)
_SP: Final = f"[ \t{chr(0xA0)}{chr(0x202F)}]"
_SPACED: Final = re.compile(rf"[0-9](?:{_SP}*[.,]{_SP}*[0-9]|{_SP}+[0-9])+")
_SPACE: Final = re.compile(f"{_SP}+")

# Quantity or money talk in a reply: the AI second opinion runs only on these (kx.agents.guard)
MONEY_CUE: Final = re.compile(
    r"[0-9$€£¥₹%]|[٠-٩۰-۹०-९]|"
    r"\b(?:price|prices|priced|cost|costs|discount|offer|deal|total|cheaper|cheapest|budget|percent|fils|"
    r"aed|dirhams?|dollars?|usd|cents?|rupees?|riyals?|free|half|quarter|point|per piece|each|one|"
    + "|".join(sorted(_EN_NUMBERS, key=len, reverse=True))
    + r")\b|سعر|خصم|درهم|فلس|مجان|कीमत|छूट|रुपये|दाम|价格|折|元|迪拉姆|优惠|"
    + "|".join(sorted(_AR_NUMBERS | _HI_NUMBERS, key=len, reverse=True)),
    re.IGNORECASE,
)
_RUN: Final = frozenset({"num", "frac", "ambig"})
_MONEY: Final = frozenset({"cur", "price"})
_CUR_ONLY: Final = frozenset({"cur"})


def _bare(t: str) -> str:
    return t[2:] if t.startswith("ال") and len(t) > 4 else t


_AR_WORDS: Final = _AR_NUMBERS | _CURRENCY | _PRICE | _FRACTIONS | _AMBIGUOUS


def _norm(token: str) -> str:
    """Lower case; Arabic: no diacritics, one alif form, no leading و / ب / ل / ف, no article ال."""
    t = token.lower()
    if t.isascii():
        return t
    t = _ARABIC_MARKS.sub("", t).translate(_ARABIC_ALIF)
    for prefix in ("و", "ب", "ل", "ف"):
        if len(t) > 3 and t.startswith(prefix) and _bare(t[1:]) in _AR_WORDS:
            t = t[1:]
            break
    return _bare(t)


def _kind(norm: str, nxt: str | None) -> str:
    if norm in _CURRENCY:
        return "cur"
    if norm in _PRICE or (norm in _PER and nxt in _PER_UNIT):
        return "price"
    if norm in _EN_NUMBERS or norm in _AR_NUMBERS or norm in _HI_NUMBERS:
        return "num"
    if norm in _FRACTIONS:
        return "frac"
    if norm in _AMBIGUOUS:
        return "ambig"
    return "join" if norm in _JOINERS else ""


def _runs(kinds: list[str]) -> list[tuple[int, int]]:
    """[start, end) of each run of number words; a joiner only inside a run ("zero point one nine")."""
    runs: list[tuple[int, int]] = []
    i, n = 0, len(kinds)
    while i < n:
        if kinds[i] not in _RUN:
            i += 1
            continue
        j = i + 1
        while j < n and (kinds[j] in _RUN or (kinds[j] == "join" and j + 1 < n and kinds[j + 1] in _RUN)):
            j += 1
        runs.append((i, j))
        i = j
    return runs


def spelled_amounts(text: str) -> list[str]:
    """Snippets of `text` that state money in words (empty = none). A run of number words counts when money
    is the next word or comes up to 2 words before it ("AED nineteen", "the price is fifteen"). A run of only
    "one" / "a" counts next to a currency, a lone fraction next to a currency or price ("half price")."""
    tokens = [(m.start(), m.end(), _norm(m.group())) for m in _TOKEN.finditer(text)]
    norms = [t[2] for t in tokens]
    kinds = [_kind(w, norms[i + 1] if i + 1 < len(norms) else None) for i, w in enumerate(norms)]
    found: list[str] = []
    for s, e in _runs(kinds):
        run = kinds[s:e]
        strong = "num" in run
        money = _MONEY if strong or "frac" in run else _CUR_ONLY
        after = e if e < len(kinds) and kinds[e] in money else None
        reach = 3 if strong else 1
        before = next((j for j in range(s - 1, max(s - reach, 0) - 1, -1) if kinds[j] in money), None)
        if after is None and before is None:
            continue
        lo = before if before is not None else s
        hi = after if after is not None else e - 1
        found.append(text[tokens[lo][0] : tokens[hi][1]])
    found.extend(m.group() for m in _ZH_AMOUNT.finditer(text))
    found.extend(
        m.group()
        for m in _ZH_AFTER_PRICE.finditer(text)
        if len(m.group(1)) > 1 or m.group(1) not in {"一", "点"}  # "价格是一样的" is not an amount
    )
    return found


def collapse_spaced_digits(text: str) -> str:
    """'AED 0 . 1 9' -> 'AED 0.19': spaces inside a digit group removed, everything else unchanged."""
    return _SPACED.sub(lambda m: _SPACE.sub("", m.group()), text)
