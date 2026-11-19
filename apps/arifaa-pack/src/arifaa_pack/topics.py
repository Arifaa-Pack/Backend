"""Arifa Pack's router words (WP-C4): which pre-quote turns go to the product role and which to the
manufacturing role. Arifa makes aluminium food packaging (containers and lids, foil, trays, platters, pots,
cups, cutlery, cling film); the platform's default words were for plastic bottles and jars.

Precision over recall: a word here only picks which conversation role answers. A missed word still gets a
good answer from the qualifying role, and no word here can reach the price graphs.
"""

from __future__ import annotations

import re
from typing import Final

from kx.agents import TopicWords

ARIFA_PRODUCT: Final = re.compile(
    r"\b(?:containers?|trays?|foils?|lids?|platters?|pots?|cups?|cutlery|spoons?|forks?|knife|knives|"
    r"cling ?film|wrap|rolls?|microns?|gauge|gsm|ml|litres?|liters?|sizes?|capacity|dimensions?|"
    r"oven|freezer|microwave\w*|aluminium|aluminum|takeaway|take-away|sample|spec(?:ification)?s?|packing)\b"
    r"|حاوي[ةات]|علب|صين[يةا]|صواني|ورق (?:ال)?ألمنيوم|ورق (?:ال)?ألومنيوم|قصدير"
    r"|غطاء|أغطية|طبق|أطباق|قدر|قدور"
    r"|أكواب|كوب|ملاعق|ملعقة|شوك|سكاكين|نايلون|رول|مقاس|فرن|فريزر|ميكروويف|عينة"
    r"|铝箔|锡纸|餐盒|盖子|保鲜膜|फॉयल|कंटेनर|ढक्कन",
    re.IGNORECASE,
)
ARIFA_MANUFACTURING: Final = re.compile(
    r"\b(?:factory|machines?|(?:production|monthly|daily|factory) capacity|capacity per \w+|production|"
    r"lead time|certificat\w*|iso|haccp|halal|food grade|"
    r"moq|minimum order|custom print\w*|printing|quality control)\b"
    r"|مصنع|طاقة إنتاج|شهادة|شهادات|حلال|طباعة|工厂|产能|फैक्ट्री|உற்பத்தி",
    re.IGNORECASE,
)
ARIFA_TOPICS: Final = TopicWords(product=ARIFA_PRODUCT, manufacturing=ARIFA_MANUFACTURING)
