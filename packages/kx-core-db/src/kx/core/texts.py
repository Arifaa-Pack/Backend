"""Editable message texts (client decisions, 18 Nov).

One catalogue for the words the platform sends that staff may change without a developer: the AI's fixed
phrases, the buyer messages that carry quotes and summaries, and the staff alerts. Every package reads them
through `TEXTS.get(key, language)`; the built-in wording below is used until someone edits it.

* Staff (manager or MD) edit the **English** text in the CRM; the AI translates the buyer-facing ones into the
  other languages (`TRANSLATED`). Staff alerts are English only.
* An edit must keep exactly the built-in placeholders (`{name}`, `{hours}` ...) and may not contain an amount
  with a currency: amounts only ever come from placeholders filled by the code (price guard rule).
* Legal and consent wording is **not** here and cannot be edited: the call-recording notice and the opt-out
  questions (they stay in `kx.agents.registry`), opt-out confirmations (`kx.automation.optout`).
* Lookup order: the edited text in that language -> the built-in text in that language -> the edited English
  -> the built-in English. A text that cannot be filled (a broken placeholder) falls back to the built-in.

The edited texts live in the CRM database (`crm.message_texts`); each process loads them at start and every
30 seconds (`Texts.replace`).
"""

from __future__ import annotations

import string
from collections.abc import Mapping
from dataclasses import dataclass, field
from typing import Final, Literal

from kx.core.price_guard import extract_prices

Group = Literal["ai", "buyer", "staff"]
LANGUAGES: Final = ("en", "ar", "hi", "ta", "zh", "si")
TRANSLATED: Final = LANGUAGES[1:]
MAX_CHARS: Final = 2000


@dataclass(frozen=True, slots=True)
class TextDef:
    key: str
    group: Group
    en: str
    note: str
    builtin: Mapping[str, str] = field(default_factory=dict)  # other languages

    @property
    def placeholders(self) -> frozenset[str]:
        return placeholders(self.en)

    @property
    def translated(self) -> bool:
        return self.group != "staff"


def placeholders(text: str) -> frozenset[str]:
    return frozenset(name for _, name, _, _ in string.Formatter().parse(text) if name)


CATALOG: Final[dict[str, TextDef]] = {}


def _define(key: str, group: Group, en: str, note: str, **builtin: str) -> None:
    CATALOG[key] = TextDef(key, group, en, note, builtin)


# ---- AI fixed phrases (kx.agents.registry.Phrase; the key is "ai." + the phrase value) ----
_define(
    "ai.price_fallback",
    "ai",
    "Our team will confirm pricing shortly.",
    "Replaces an AI reply the price guard blocked.",
    ar="سيؤكد فريقنا الأسعار قريباً.",
)
_define(
    "ai.manager_60",
    "ai",
    "Let me check this with our sales team — you'll hear back within 60 minutes during working hours.",
    "The buyer insists below the minimum: the AI pauses and the team answers (§12.2 rule 4).",
    ar="دعني أتحقق من ذلك مع فريق المبيعات — ستتلقى رداً خلال 60 دقيقة خلال ساعات العمل.",
)
_define(
    "ai.handoff",
    "ai",
    "A member of our team will reply to you shortly.",
    "The AI hands the chat to a person.",
)
_define(
    "ai.safe_reply",
    "ai",
    "Let me check with our sales team.",
    "When the AI cannot answer (all models down, repeated failure).",
)
_define(
    "ai.quote_in_preparation",
    "ai",
    "Thank you. Your quotation is being prepared and reviewed by our team; we will send it to you soon.",
    "The requirement is complete; the quote is with the team.",
)
_define(
    "ai.quote_rejected",
    "ai",
    "Our team is reviewing your request and will contact you shortly.",
    "The salesperson sent the quote back for correction.",
)
_define(
    "ai.change_request",
    "ai",
    "Good question — I've passed this to {name}. "
    "You'll get an updated quotation within {hours} working hours.",
    "A buyer asks for a change the AI may not decide. {name} = the salesperson, {hours} = the SLA.",
    ar="سؤال جيد — لقد أحلت طلبك إلى {name}. سيصلك عرض أسعار محدّث خلال {hours} ساعات عمل.",
)
_define(
    "ai.revised_quote",
    "ai",
    "Thank you — for that quantity I'm preparing a revised quotation; {name} will send it to you shortly.",
    "A quantity in another price tier: a revised quote follows. {name} = the salesperson.",
    ar="شكراً لك — أقوم بإعداد عرض أسعار محدّث لهذه الكمية، وسيرسله لك {name} قريباً.",
)
_define(
    "ai.handover_ack",
    "ai",
    "Thanks — {name} will reply in the morning.",
    "Outside working hours on a handed-over deal (§14.6). {name} = the salesperson.",
    ar="شكراً لك — سيرد عليك {name} في الصباح.",
)
_define(
    "ai.voice_goodbye",
    "ai",
    "Of course. Thank you for calling, goodbye.",
    "The buyer clearly wants to end a website call.",
    ar="بالتأكيد. شكراً لاتصالك، مع السلامة.",
)
_define(
    "ai.phone_not_mobile",
    "ai",
    "Is that a mobile number on WhatsApp? I'd like to send you the summary there.",
    "The buyer gave a landline or an invalid number (§8.2 step 1).",
    ar="هل هذا رقم جوال على واتساب؟ أود أن أرسل لك الملخص هناك.",
)
_define(
    "ai.summary_not_arrived",
    "ai",
    "I've just sent the summary to your WhatsApp, but it doesn't seem to have arrived. "
    "Could you check the number, or would email be easier?",
    "The Requirement Summary was not delivered while the buyer is still chatting (§8.2 step 4).",
    ar="لقد أرسلت لك الملخص على واتساب للتو، لكن يبدو أنه لم يصل. هل يمكنك التحقق من الرقم، أم أن البريد "
    "الإلكتروني أسهل؟",
)

# ---- buyer messages that carry the quote PDF and the Requirement Summary ----
_define(
    "buyer.quote_subject",
    "buyer",
    "Quotation {number} from {seller}",
    "Email subject of a quote.",
    ar="عرض السعر {number} من {seller}",
)
_define(
    "buyer.quote_body",
    "buyer",
    "Hi {name},\n\nHere is your {what} {number} for {summary}: {currency} {total}, valid until "
    "{valid_until}. Advance payment is required to start production.{extra}\n\n— {signer}",
    "The message with the quote PDF (WhatsApp and email). {extra} = what changed + the 'open to discuss' "
    "line; {currency} {total} are filled from the approved quote.",
    ar="مرحباً {name}،\n\nإليك {what} رقم {number} لـ {summary}: {currency} {total}، صالح حتى {valid_until}. "
    "يلزم سداد دفعة مقدّمة لبدء الإنتاج.{extra}\n\n— {signer}",
)
_define(
    "buyer.quote_what_final",
    "buyer",
    "final quotation",
    "{what} in the quote message, final quote.",
    ar="عرض السعر النهائي",
)
_define(
    "buyer.quote_what_revision",
    "buyer",
    "updated quotation",
    "{what} in the quote message, revision.",
    ar="عرض السعر المحدّث",
)
_define(
    "buyer.quote_what_other",
    "buyer",
    "quotation",
    "{what} in the quote message, first quote.",
    ar="عرض السعر",
)
_define(
    "buyer.open_to_discuss_1",
    "buyer",
    "Better pricing is possible for larger quantities or repeat orders — just reply and we'll work it out "
    "together.",
    "'Open to discuss' line under a quote (one of three, picked per quote; not on the final).",
    ar="يمكن تقديم سعر أفضل للكميات الأكبر أو الطلبات المتكررة — فقط رد على هذه الرسالة وسنتفق معاً.",
)
_define(
    "buyer.open_to_discuss_2",
    "buyer",
    "If you're planning a bigger volume or regular orders, reply here and we can look at the price together.",
    "'Open to discuss' line (second variant).",
    ar="إذا كنت تخطط لكمية أكبر أو طلبات منتظمة، رد هنا وسننظر في السعر معاً.",
)
_define(
    "buyer.open_to_discuss_3",
    "buyer",
    "Happy to talk it through: if the quantity grows or you order regularly, let me know and I'll see what I "
    "can do.",
    "'Open to discuss' line (third variant).",
)
_define(
    "buyer.summary_caption",
    "buyer",
    "Hi {name}, here's the summary of what we discussed — {summary}. Our team is preparing your quotation.",
    "The words with the Requirement Summary PDF (never a code, never 'verify', §8.2 step 5).",
    ar="مرحباً {name}، إليك ملخص ما ناقشناه — {summary}. يقوم فريقنا بإعداد عرض السعر.",
)
_define(
    "buyer.summary_delivery_to",
    "buyer",
    "delivery to {place}",
    "Part of the summary line.",
    ar="التسليم إلى {place}",
)
_define(
    "buyer.summary_email_subject",
    "buyer",
    "{company} Requirement Summary {deal}",
    "Email subject of the Requirement Summary.",
)
_define(
    "buyer.summary_next_text",
    "buyer",
    "Our sales team is preparing your quotation. You'll receive it on WhatsApp shortly.",
    "Printed on the Requirement Summary PDF.",
    ar="يقوم فريق المبيعات لدينا بإعداد عرض السعر. سيصلك على واتساب قريباً.",
)
_define(
    "buyer.summary_no_prices",
    "buyer",
    "This summary lists what you asked for. Prices follow in your quotation.",
    "Printed on the Requirement Summary PDF.",
    ar="يعرض هذا الملخص ما طلبته. ستصلك الأسعار في عرض السعر.",
)

# ---- staff alerts (English) ----
_define(
    "staff.deal_assigned",
    "staff",
    "New deal {deal} assigned to you",
    "WhatsApp / CRM alert to the salesperson.",
)
_define("staff.deal_takeover", "staff", "The MD took over deal {deal}", "To the salesperson and the manager.")
_define("staff.deal_hand_back", "staff", "Deal {deal} was handed back to {name}", "After an MD takeover.")
_define("staff.deal_reassigned", "staff", "Deal {deal} was reassigned to {name}", "To the new and old owner.")
_define("staff.deal_won", "staff", "Deal {deal} is WON by {name}.", "To the manager and the MD.")
_define("staff.deal_won_unassigned", "staff", "Deal {deal} is WON.", "WON on a deal with no salesperson.")
_define(
    "staff.final_quote_salesperson",
    "staff",
    "{what} approved and sent. The buyer is yours for payment and the order.",
    "To the salesperson. {what} = 'Final quote (subject)' or 'The final quote'.",
)
_define("staff.final_quote_manager", "staff", "{what} approved and sent.", "To the manager.")
_define(
    "staff.approval_title", "staff", "Quote {quote} for {total}", "Approval alert, reminder and escalation."
)
_define(
    "staff.special_price_title",
    "staff",
    "A special price on quote {quote} for {total}",
    "Special-price approval alert (manager).",
)
_define(
    "staff.change_request_title",
    "staff",
    "A buyer {type} change request on deal {deal}",
    "Change-request alert to the salesperson.",
)
_define(
    "staff.call_intro",
    "staff",
    "Connecting you to the buyer for {company}.",
    "Spoken to the salesperson when a click-to-call connects.",
)


class _Keep(dict[str, object]):
    def __missing__(self, key: str) -> str:
        return "{" + key + "}"


def _lang(language: str | None) -> str:
    lang = (language or "en").strip().lower().replace("_", "-").split("-")[0]
    return lang if lang in LANGUAGES else "en"


class Texts:
    """The texts in use by this process: the catalogue plus the edits loaded from the CRM."""

    def __init__(self) -> None:
        self._edits: dict[tuple[str, str], str] = {}

    def replace(self, edits: Mapping[tuple[str, str], str]) -> None:
        self._edits = {k: v for k, v in edits.items() if k[0] in CATALOG}

    def has(self, key: str, language: str | None) -> bool:
        """A text exists in this language (edited or built-in), not only through the English fallback."""
        lang = _lang(language)
        d = CATALOG[key]
        return bool(self._edits.get((key, lang)) or (d.en if lang == "en" else d.builtin.get(lang)))

    def raw(self, key: str, language: str | None) -> str:
        d = CATALOG[key]
        lang = _lang(language)
        for text in (
            self._edits.get((key, lang)),
            d.en if lang == "en" else d.builtin.get(lang),
            self._edits.get((key, "en")),
            d.en,
        ):
            if text:
                return text
        return d.en

    def get(self, key: str, language: str | None = "en", /, **values: object) -> str:
        """The text, with `values` filled in (placeholders without a value stay as they are)."""
        text = self.raw(key, language)
        if not values:
            return text
        try:
            return text.format_map(_Keep(values))
        except ValueError, IndexError, AttributeError:  # a broken edit never breaks a message
            d = CATALOG[key]
            return (d.builtin.get(_lang(language)) or d.en).format_map(_Keep(values))


TEXTS: Final = Texts()


def check_edit(key: str, text: str) -> list[str]:
    """Problems with a staff edit or an AI translation (empty = fine)."""
    d = CATALOG.get(key)
    if d is None:
        return [f"unknown text {key!r}"]
    problems: list[str] = []
    body = text.strip()
    if not body:
        problems.append("the text is empty")
    if len(body) > MAX_CHARS:
        problems.append(f"longer than {MAX_CHARS} characters")
    try:
        found = placeholders(body)
    except ValueError:
        return [*problems, "a { or } is not closed; write placeholders like {name}"]
    if found != d.placeholders:
        missing, extra = sorted(d.placeholders - found), sorted(found - d.placeholders)
        if missing:
            problems.append("keep the placeholders " + ", ".join("{" + m + "}" for m in missing))
        if extra:
            problems.append("unknown placeholders " + ", ".join("{" + e + "}" for e in extra))
    if any(m.currency is not None and not m.is_percentage for m in extract_prices(body)):
        problems.append("no amounts with a currency: prices only come from the approved quote")
    return problems
