"""D55 / D56 / D61: the tools that let the assistant move the website, answer from the catalogue, and ask its
two specialists, all inside one turn.

* **Site tools** (website channels only; `RoleMiddleware` hides them on WhatsApp, email and phone). The
  model chooses *what* to show; the backend checks it against the site manifest (`kx.agents.site.actions`)
  and sends it to the browser at once (`AgentContext.on_action`), so the page moves while the reply is still
  being written. An action the instant resolver already sent this turn is not sent again (same intent id).
* **Catalogue tools.** `find_products` searches the site manifest (ids, names, references, families, packing;
  never a price). `product_variants` asks the CRM for a product's sellable items (D61): packing, measure,
  availability as a word and the quantities where better pricing starts, never an amount.
* **Specialists** (D56). One assistant speaks for the turn; for a question that needs product or factory
  knowledge it asks `ask_product_specialist` / `ask_manufacturing_specialist`: a fast-model call that sees
  only approved facts (knowledge base, manifest, variants) and answers the assistant, not the buyer. The
  model may call both at once (the tool node runs them in parallel). Their cost is metered on the same run,
  and their words reach the buyer only through the assistant's reply, which the price guard checks.
"""

from __future__ import annotations

import re
from typing import Final, Literal

from langchain.tools import ToolRuntime, tool
from langchain_core.messages import HumanMessage, SystemMessage

from kx.agents.runtime.context import AgentContext
from kx.agents.runtime.runs import UsageMeter
from kx.agents.site.actions import resolve_action
from kx.agents.site.models import (
    AddToEnquiry,
    AskContact,
    EnquiryFill,
    FactoryTour,
    FillEnquiry,
    Navigate,
    ShowFamily,
    ShowProduct,
    SiteAction,
)
from kx.core.contracts import ManifestProduct, SiteManifest, VariantView

Runtime = ToolRuntime[AgentContext]

_MAX_FOUND: Final = 8
_WORD: Final = re.compile(r"[\w؀-ۿ]+")
_AVAILABILITY: Final = {"in_stock": "in stock", "limited": "limited stock", "made_to_order": "made to order"}


async def _show(ctx: AgentContext, action: SiteAction) -> str:
    if ctx.manifest is None:
        return "The website map is not available right now: answer in words only."
    checked = resolve_action(action, ctx.manifest, ctx.site)
    if checked is None:
        return "The website has no such page, section or product. Do not mention one; answer in words."
    if any(a.id == checked.id for a in ctx.log.site_actions):
        return "Already on the visitor's screen: do not move the page again."
    ctx.log.site_actions.append(checked)
    if ctx.on_action is not None:
        await ctx.on_action(checked)
    where = checked.resolved.path_en if checked.resolved else checked.type
    return f"Shown on the website ({where}). Refer to it in one short phrase, do not describe the page."


@tool
async def show_family(family: str, runtime: Runtime) -> str:
    """Open a product family in the website catalogue (family ids: containers, foil, platters, cups-trays,
    pots, microwave, cutlery, cling-film)."""
    return await _show(runtime.context, ShowFamily(family=family))


@tool
async def show_product(product_id: str, runtime: Runtime) -> str:
    """Open one product on the website (an id from find_products, e.g. ac-8325)."""
    return await _show(runtime.context, ShowProduct(product_id=product_id))


@tool
async def go_to_page(page: str, runtime: Runtime, section: str | None = None) -> str:
    """Go to a website page (home, products, about, quality, clients, contact), optionally to one section."""
    return await _show(runtime.context, Navigate(page=page, section=section))


@tool
async def start_factory_tour(runtime: Runtime, chapter: str | None = None) -> str:
    """Play the factory film, optionally from a chapter (e.g. forming, lids, foil, quality, warehouse)."""
    return await _show(runtime.context, FactoryTour(chapter=chapter))


@tool
async def add_to_enquiry_list(product_ids: list[str], runtime: Runtime) -> str:
    """Add products the visitor wants to their enquiry list on the website (ids from find_products)."""
    return await _show(runtime.context, AddToEnquiry(product_ids=product_ids[:10]))


@tool
async def fill_enquiry_desk(fill: EnquiryFill, runtime: Runtime) -> str:
    """Pre-fill the enquiry form on the Contact page with what the visitor told you (their words; never a
    price). The visitor checks it and presses send."""
    return await _show(runtime.context, FillEnquiry(fill=fill))


@tool
async def ask_for_contact(runtime: Runtime) -> str:
    """Show the contact card in the chat (name, phone, email, with WhatsApp and email ticks). Use it once the
    visitor has had something useful from you and wants a quotation, details or a call."""
    result = await _show(runtime.context, AskContact())
    if result.startswith("Shown"):
        return (
            "The contact card is in the chat. Ask in one friendly sentence for their name and phone or "
            "email, and say they can tick WhatsApp or email for the quotation. Never press; never ask twice."
        )
    return result


def _score(words: set[str], product: ManifestProduct, family_words: set[str]) -> int:
    text = f"{product.id} {product.ref} {product.catalogue_code or ''} {product.name} {product.packing or ''}"
    own = {w.lower() for w in _WORD.findall(text)} | family_words
    return len(words & own)


def find_in_manifest(
    manifest: SiteManifest, query: str, family: str | None = None
) -> list[tuple[ManifestProduct, int]]:
    """The products that fit the visitor's words best (pure; used by the tool and the specialists)."""
    words = {w.lower() for w in _WORD.findall(query)}
    fam_words: dict[str, set[str]] = {}
    for f in manifest.families:
        bag = {w.lower() for w in _WORD.findall(f"{f.id} {f.name} " + " ".join(f.names.values()))}
        for syn in f.synonyms.values():
            bag |= {w.lower() for s in syn for w in _WORD.findall(s)}
        fam_words[f.id] = bag
    scored = [
        (p, _score(words, p, fam_words.get(p.family, set())))
        for p in manifest.products
        if family is None or p.family == family
    ]
    scored = [x for x in scored if x[1] > 0] or ([(p, 0) for p, _ in scored] if family else [])
    scored.sort(key=lambda x: (-x[1], x[0].range != "trade", x[0].id))
    return scored[:_MAX_FOUND]


def _line(p: ManifestProduct) -> str:
    bits = [p.id, p.name, f"ref {p.ref}", p.family]
    if p.packing:
        bits.append(p.packing)
    bits.append("retail pack" if p.range == "self" else "trade")
    return " | ".join(bits)


@tool
async def find_products(query: str, runtime: Runtime, family: str | None = None) -> str:
    """Search the website catalogue by the visitor's words (name, reference, size, family). Returns product
    ids to show, with name, reference, family and packing. Never prices."""
    ctx = runtime.context
    if ctx.manifest is None:
        return "The catalogue is not available right now; use search_knowledge."
    found = find_in_manifest(ctx.manifest, query, family)
    if not found:
        return "No product on the website matches. Ask what they need it for, or say the team will check."
    return "\n".join(_line(p) for p, _ in found)


def describe_variants(variants: tuple[VariantView, ...]) -> str:
    """What the AI may say about a product's sellable items (D61): never an amount."""
    if not variants:
        return "No packings are listed for it yet: say the sales team will confirm packing and availability."
    lines = []
    for v in variants:
        parts = [v.description, f"packing {v.packing or v.unit}", _AVAILABILITY[v.availability]]
        if v.moq:
            parts.append(f"minimum order {v.moq:,} {v.unit}")
        if v.quantity_breaks:
            parts.append("better pricing from " + ", ".join(f"{q:,.0f}" for q in v.quantity_breaks))
        if not v.quotable:
            parts.append("the sales team prices it")
        lines.append("- " + "; ".join(parts))
    return "\n".join(lines) + "\nNever state or estimate a price; the quotation has it."


@tool
async def product_variants(product_id: str, runtime: Runtime) -> str:
    """The packings / sizes Arifa sells for a website product, with availability (in stock, limited, made
    to order) and the quantities where better pricing starts. Never amounts."""
    ctx = runtime.context
    return describe_variants(await ctx.gateway.product_variants(product_id))


SPECIALIST_PROMPTS: Final[dict[str, str]] = {
    "product": (
        "You are Arifa Pack's product specialist. Answer the sales assistant's question about aluminium "
        "packaging (containers, lids, foil, trays, platters, pots, cutlery, cling film): sizes, "
        "capacities, packing, which product suits which food or use, oven / freezer / microwave "
        "suitability. Use ONLY the facts given. If they do not answer it, say 'not in our approved content'. "
        "Never a price, discount or delivery promise. Three short sentences at most, in English; the "
        "assistant translates."
    ),
    "manufacturing": (
        "You are Arifa Pack's manufacturing specialist. Answer the sales assistant's question about the "
        "factory: machines, processes, quality checks, certificates, capacity, custom printing. Use ONLY the "
        "facts given. If they do not answer it, say 'not in our approved content'. Never a price, a delivery "
        "date or a capacity promise beyond the facts. Three short sentences at most, in English."
    ),
}


async def _ask(ctx: AgentContext, kind: Literal["product", "manufacturing"], question: str) -> str:
    if ctx.specialist_model is None:
        return "No specialist available: use search_knowledge and find_products yourself."
    facts: list[str] = []
    if ctx.knowledge is not None:
        result = await ctx.knowledge.search(question, "en")
        facts += [f"[{h.title}] {h.text}" for h in result.hits[:4]] if not result.weak else []
    if kind == "product" and ctx.manifest is not None:
        found = find_in_manifest(ctx.manifest, question)[:4]
        facts += [_line(p) for p, _ in found]
        for p, _ in found[:2]:
            facts.append(
                f"Packings of {p.id}:\n" + describe_variants(await ctx.gateway.product_variants(p.id))
            )
    if not facts:
        return "Not in our approved content: say the team will confirm."
    messages = [
        SystemMessage(SPECIALIST_PROMPTS[kind]),
        HumanMessage(f"Question: {question[:500]}\n\nApproved facts:\n" + "\n\n".join(facts)),
    ]
    reply = await ctx.specialist_model.runnable().ainvoke(
        messages, config={"callbacks": [UsageMeter(ctx.log)], "run_name": f"specialist_{kind}"}
    )
    text = reply.text if hasattr(reply, "text") else str(reply.content)
    return f"{kind} specialist (for you, not the buyer; check before you use it): {text.strip()[:1200]}"


@tool
async def ask_product_specialist(question: str, runtime: Runtime) -> str:
    """Ask the product specialist (sizes, capacities, packing, which product fits which food or use). You
    may ask both specialists at once for a mixed question."""
    return await _ask(runtime.context, "product", question)


@tool
async def ask_manufacturing_specialist(question: str, runtime: Runtime) -> str:
    """Ask the manufacturing specialist (machines, quality, certificates, capacity, printing)."""
    return await _ask(runtime.context, "manufacturing", question)


WEB_TOOLS: Final = (
    show_family,
    show_product,
    go_to_page,
    start_factory_tour,
    add_to_enquiry_list,
    fill_enquiry_desk,
    ask_for_contact,
    find_products,
    product_variants,
    ask_product_specialist,
    ask_manufacturing_specialist,
)
