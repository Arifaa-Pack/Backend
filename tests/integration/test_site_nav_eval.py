"""D55 / D57 eval (WP-C4): the instant resolver against the real website.

The current site (`arifaa_pack/seed`) is imported into the CMS, the CMS builds the site manifest, and every
case in `kx/agents/evals/site_nav/cases.json` runs through `instant_actions`. The bar (plan §3): no wrong
move at all, and at least 98 % of the clear requests moved at once. A case with `"expect": null` must not
move the page (precision over recall: the agent answers those). The same cases go to the website as
`docs/handoff/vectors/site-nav.json`, for its own optimistic resolver (layer 0).
"""

from __future__ import annotations

import json
import pathlib
from typing import Any

import pytest

import arifaa_pack
from kx.agents.site.models import SiteContext
from kx.agents.site.resolver import instant_actions
from kx.cms import build_manifest, import_site
from kx.core.contracts import SiteManifest

pytestmark = pytest.mark.db
SEED = pathlib.Path(arifaa_pack.__file__).resolve().parent / "seed"  # as `kx content import-site`
CASES = (
    pathlib.Path(__file__).resolve().parents[2]
    / "packages/kx-agent-framework/src/kx/agents/evals/site_nav/cases.json"
)


def _json(name: str) -> Any:
    return json.loads((SEED / name).read_text(encoding="utf-8"))


async def _manifest(databases: Any) -> SiteManifest:
    await import_site(
        databases,
        catalogue=_json("catalogue.json"),
        seed=_json("cms_seed.json"),
        clients=_json("clients.json"),
        arabic=_json("arabic.json"),
    )
    return await build_manifest(databases)


def _got(text: str, manifest: SiteManifest, context: dict[str, Any] | None) -> dict[str, Any] | None:
    found = instant_actions(text, manifest, SiteContext(**context) if context else None)
    if not found:
        return None
    action = found[0].action.model_dump(mode="json", exclude={"id", "layer", "resolved", "seconds"})
    return {k: v for k, v in action.items() if v not in (None, [], "quick")} | (
        {"view": action["view"]} if action.get("view") == "page" else {}
    )


async def test_the_instant_resolver_on_the_real_site(databases: Any) -> None:
    manifest = await _manifest(databases)
    cases = json.loads(CASES.read_text(encoding="utf-8"))["cases"]
    wrong, missed = [], []
    for case in cases:
        got = _got(case["text"], manifest, case.get("context"))
        expect = case["expect"]
        if got == expect:
            continue
        if expect is not None and got is None:
            missed.append((case["text"], expect))
        else:
            wrong.append((case["text"], expect, got))
    clear = sum(1 for c in cases if c["expect"] is not None)
    assert not wrong, f"wrong moves: {wrong}"
    assert (clear - len(missed)) / clear >= 0.98, f"missed: {missed}"
