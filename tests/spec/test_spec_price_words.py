"""M7 (security review 19 Nov): money written in words or digits split by spaces (`kx.core.price_words`).

The digit price guard (`kx.core.price_guard`) is unchanged and still passes these texts; the agents' guard
adds this detector and fails closed on any hit."""

from __future__ import annotations

from decimal import Decimal

import pytest

from kx.core.contracts import AllowedPrice
from kx.core.price_guard import check_prices
from kx.core.price_words import MONEY_CUE, collapse_spaced_digits, spelled_amounts


@pytest.mark.parametrize(
    "text",
    [
        "Can you do it for nineteen fils per piece?",  # the review's examples
        "we can do zero point one nine dirhams each",
        "خمسة عشر درهما للقطعة",
        "نقدر نسويها بخمسة عشر درهماً",
        "yes, AED nineteen per piece",
        "the price is fifteen",
        "two hundred and fifty dirhams in total",
        "twenty-five fils",
        "half price for you",
        "one dirham per piece",
        "a quarter dirham less",
        "السعر خمسة عشر",
        "بنصف درهم",
        "पंद्रह रुपये प्रति पीस",
        "दो सौ रुपये",
        "每个零点一九迪拉姆",
        "单价两块",
        "可以打八折",
    ],
)
def test_money_in_words_is_found(text: str) -> None:
    assert spelled_amounts(text), text
    assert check_prices(text, ()).ok  # the digit guard alone cannot see it: this module closes the gap


@pytest.mark.parametrize(
    "text",
    [
        "We have two options at AED 0.19 each",
        "One of our team will call you today.",
        "Which one would you like?",
        "We make three sizes: 250 ml, 500 ml and 1 litre.",
        "We print two colours per piece.",
        "We make thousands of jars a day at a good price.",
        "The price a buyer pays depends on the volume.",
        "عندنا ثلاثة مقاسات بسعر ممتاز",
        "نرسل لك عرض السعر خلال يوم واحد",
        "हम तीन साइज़ बनाते हैं",
        "价格是一样的",
        "我们每个月生产很多瓶子",
    ],
)
def test_ordinary_number_words_are_not_money(text: str) -> None:
    assert spelled_amounts(text) == [], text


def test_digits_split_by_spaces_are_joined_for_the_digit_guard() -> None:
    assert collapse_spaced_digits("AED 0 . 1 9 per piece") == "AED 0.19 per piece"
    assert collapse_spaced_digits("1 9 fils each") == "19 fils each"
    assert collapse_spaced_digits("AED 0.19 per piece") == "AED 0.19 per piece"  # nothing to join
    one = (AllowedPrice(amount=Decimal(1), currency="AED"),)
    assert check_prices("AED 1 9 for the jar", one).ok  # unchanged digit guard: "1" is approved, "9" bare
    assert not check_prices(collapse_spaced_digits("AED 1 9 for the jar"), one).ok  # AED 19 is not


def test_money_cues_decide_when_the_second_opinion_runs() -> None:
    assert MONEY_CUE.search("Our MOQ is 5,000 pieces.")
    assert MONEY_CUE.search("We can give a better deal for bigger orders.")
    assert MONEY_CUE.search("نصف السعر")
    assert MONEY_CUE.search("Thank you, our team will reply soon!") is None
