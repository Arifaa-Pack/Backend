"""`kx map` (ADR-0001 Phase 0): the map is read from the real container and must cover every contract
Protocol with its implementation and every event that has a handler, in a stable order."""

from __future__ import annotations

import inspect
import json
import pathlib
import subprocess
import sys
import typing
from typing import Any, Protocol

import pytest

from arifaa_pack import featuremap
from kx.agents import retention as agents_retention
from kx.automation import retention as automation_retention
from kx.core import contracts
from kx.core import events as ev
from kx.core.event_payloads import PAYLOADS


@pytest.fixture(scope="module")
def fmap() -> dict[str, list[dict[str, Any]]]:
    """One real run of the CLI in its own process: stdout must be clean JSON (logs go to stderr) and the
    container's settings caches and logging configuration stay out of this test process."""
    done = subprocess.run(
        [sys.executable, "-m", "arifaa_pack.cli", "map", "--format", "json"],
        capture_output=True,
        text=True,
        encoding="utf-8",
        timeout=300,
        check=False,
    )
    assert done.returncode == 0, done.stderr[-2000:]
    result: dict[str, list[dict[str, Any]]] = json.loads(done.stdout)
    return result


def _contract_protocols() -> set[str]:
    return {
        f"kx.core.contracts:{name}"
        for name, obj in vars(contracts).items()
        if inspect.isclass(obj) and obj.__module__ == contracts.__name__ and typing.is_protocol(obj)
    }


def test_every_contract_protocol_is_mapped_to_its_implementation(fmap: dict[str, Any]) -> None:
    mapped = {e["protocol"]: e for e in fmap["contracts"]}
    assert set(mapped) == _contract_protocols()
    for name, entry in mapped.items():
        assert entry["implementations"], f"{name} has no implementation in the container"
        for impl in entry["implementations"]:
            assert impl["package"] in {"crm", "automation", "agents", "app"}
            assert impl["file"].endswith(".py")
    impl_of = {k: [i["name"] for i in v["implementations"]] for k, v in mapped.items()}
    assert impl_of["kx.core.contracts:CrmGateway"] == ["kx.crm.gateway:CrmGatewayImpl"]
    assert impl_of["kx.core.contracts:GrantConsumer"] == ["kx.crm.gateway:GrantConsumerImpl"]
    # the only way out (CONTRACTS.md §3): exactly one sender is wired
    assert impl_of["kx.core.contracts:MessageSender"] == ["kx.automation.outbound.service:OutboundService"]
    sender = mapped["kx.core.contracts:MessageSender"]["implementations"][0]
    assert (
        len(sender["injected_at"]) == 1
    )  # one instance (each wired object is listed once, at its first path)


def _registered_handlers() -> dict[str, set[str]]:
    """Event -> handler names straight from each package's table (independent of the map's code)."""
    from arifaa_pack import container
    from kx.agents.handlers import AgentHandlers
    from kx.automation.handlers import EventHandlers
    from kx.crm import jobs as crm_jobs

    none: Any = None
    out: dict[str, set[str]] = {}
    for event, fn in crm_jobs.HANDLERS.items():
        out.setdefault(event, set()).add(featuremap.ref(fn))
    auto = EventHandlers(databases=none, settings=none, sender=none, ladder=none, scheduler=none)
    for event, (_, fn) in auto.registry().items():
        out.setdefault(event, set()).add(featuremap.ref(fn))
    agents = AgentHandlers(databases=none, service=none, sender=none, addresses=none)
    for event in agents.handlers():
        out.setdefault(event, set())  # wrapped in `_once`; names are checked below by package
    out.setdefault(ev.MESSAGE_TEXT_CHANGED, set()).add("arifaa_pack.texts_sync:translate_text")
    out.setdefault(ev.CATALOGUE_PRODUCT_CONTENT, set()).add(featuremap.ref(container.cms_product_draft))
    return out


def test_every_event_with_a_handler_is_mapped(fmap: dict[str, Any]) -> None:
    mapped = {e["event"]: e for e in fmap["events"]}
    assert set(PAYLOADS) <= set(mapped)
    expected = _registered_handlers()
    for event, names in expected.items():
        assert event in PAYLOADS, f"{event} is handled but has no payload model"
        got = {h["name"] for h in mapped[event]["handlers"]}
        assert got, f"{event} has a handler but the map shows none"
        assert names <= got
    for event, entry in mapped.items():  # nothing invented: a mapped handler is a registered one
        if entry["handlers"]:
            assert event in expected
    agents = {h["name"] for h in mapped[ev.MESSAGE_RECEIVED]["handlers"] if h["package"] == "agents"}
    assert agents == {"kx.agents.handlers:AgentHandlers._message"}


def test_every_event_has_a_handler_or_a_recorded_reason(fmap: dict[str, Any]) -> None:
    """Six events had no subscriber and nobody had said why (recon, 19 Nov). Now each one either has a
    handler or is in kx.core.events.NO_SUBSCRIBER with its reason, and the list cannot go stale."""
    for e in fmap["events"]:
        if e["handlers"]:
            assert e["event"] not in ev.NO_SUBSCRIBER, (
                f"{e['event']} has a handler now: drop it from NO_SUBSCRIBER"
            )
        else:
            assert e["event"] in ev.NO_SUBSCRIBER, f"{e['event']} has no handler: subscribe one or record why"
    assert set(ev.NO_SUBSCRIBER) <= {e["event"] for e in fmap["events"]}


def test_routes_jobs_and_beat_are_mapped(fmap: dict[str, Any]) -> None:
    routes = {(r["method"], r["path"]): r for r in fmap["routes"]}
    revise = routes[("POST", "/v1/deals/{deal_id}/quotes/revise")]
    assert revise["router"] == "kx.crm.deals.router"
    assert "kx.crm.quotes.service:revise_quote" in revise["calls"]
    assert ("POST", "/webhooks/whatsapp") in routes
    assert ("POST", "/v1/public/sessions") in routes
    assert {j["task"] for j in fmap["jobs"]} >= {"crm.handle_event", "agents.handle_event"}
    beat = {p["entry"]: p for p in fmap["periodic"]}
    assert beat["crm.approval_sweep"]["name"] is not None
    assert all(p["name"] is not None for p in beat.values()), "a Beat entry has no job body"


def test_output_is_sorted_for_diffs(fmap: dict[str, Any]) -> None:
    keys = {
        "contracts": lambda e: e["protocol"],
        "ports": lambda e: e["protocol"],
        "events": lambda e: e["event"],
        "routes": lambda e: (e["path"], e["method"]),
        "jobs": lambda e: e["task"],
        "periodic": lambda e: e["entry"],
    }
    for section, key in keys.items():
        values = [key(e) for e in fmap[section]]
        assert values == sorted(values), section
    assert featuremap.to_json(fmap) == featuremap.to_json(json.loads(featuremap.to_json(fmap)))


def test_feature_filter_keeps_what_mentions_the_name(fmap: dict[str, Any]) -> None:
    only = featuremap.filter_map(fmap, "change-request")
    paths = {r["path"] for r in only["routes"]}
    assert "/v1/change-requests/{cr_id}/resolve" in paths
    assert "/v1/auth/login" not in paths
    assert {e["event"] for e in only["events"]} >= {ev.CHANGE_REQUESTED}
    assert featuremap.filter_map(fmap, None) == fmap


def test_markdown_has_every_section(fmap: dict[str, Any]) -> None:
    md = featuremap.to_markdown(fmap)
    for title in (
        "## Contracts",
        "## Other ports",
        "## Events",
        "## HTTP routes",
        "## Background jobs",
        "## Beat",
    ):
        assert title in md
    assert "injected_at" not in md and "container." not in md  # settings-dependent paths stay out


class _Port(Protocol):
    name: str

    async def put(self, key: str, data: bytes) -> None: ...


class _Renamed:
    name = "x"

    async def put(self, k: str, payload: bytes) -> None:  # other names, same positions: conforms
        return None


class _KeywordsOnly:
    name = "x"

    async def put(self, **kwargs: object) -> None:
        return None


class _Sync:
    name = "x"

    def put(self, key: str, data: bytes) -> None:
        return None


class _NoName:
    async def put(self, key: str, data: bytes) -> None:
        return None


class _NameFromInit:
    def __init__(self, name: str) -> None:
        self.name = name

    async def put(self, key: str, data: bytes) -> None:
        return None


@pytest.mark.parametrize(
    ("cls", "ok"),
    [(_Renamed, True), (_NameFromInit, True), (_KeywordsOnly, False), (_Sync, False), (_NoName, False)],
)
def test_structural_conformance(cls: type, ok: bool) -> None:
    assert featuremap.conforms(cls, _Port) is ok


def _retention_job() -> None:
    """Like the app's nightly job: one module's plumbing_rules, the other module's run_retention."""
    automation_retention.plumbing_rules  # noqa: B018
    agents_retention.run_retention  # noqa: B018


def test_calls_lists_only_what_is_read_on_each_module() -> None:
    """Both retention modules define plumbing_rules: only the one the code reads may be listed (the
    `app.retention` false positive of 19 Nov)."""
    found = featuremap.calls(_retention_job)
    assert "kx.automation.retention:plumbing_rules" in found
    assert "kx.agents.retention:run_retention" in found
    assert "kx.agents.retention:plumbing_rules" not in found


def test_package_of() -> None:
    assert featuremap.package_of("kx.crm.quotes.service") == "crm"
    assert featuremap.package_of("kx.core") == "core"
    assert featuremap.package_of("arifaa_pack.container") == "app"
    assert featuremap.package_of("kx.crmx") == "external"


# ---------------------------------------------------------------- docs/FEATURE_MAP.md (Phase 0.2)
REPO = pathlib.Path(__file__).resolve().parents[2]


def test_splice_replaces_only_the_generated_region() -> None:
    begin, end = featuremap.GENERATED_BEGIN, featuremap.GENERATED_END
    doc = f"# head\r\nhand table\r\n{begin}\r\nold\r\n{end}\r\ntail\r\n"  # a Windows checkout (CRLF)
    out = featuremap.splice(doc, "new\n")
    assert out == f"# head\nhand table\n{begin}\n\nnew\n\n{end}\ntail\n"
    assert featuremap.is_current(out, "new\n")
    assert featuremap.is_current(out.replace("\n", "\r\n"), "new\n")
    assert not featuremap.is_current(doc, "new\n")
    with pytest.raises(ValueError, match="markers"):
        featuremap.splice("no markers here", "x")


def test_cli_write_then_check(
    fmap: dict[str, Any],
    tmp_path: pathlib.Path,
    monkeypatch: pytest.MonkeyPatch,
    capsys: pytest.CaptureFixture[str],
) -> None:
    from arifaa_pack.cli import main

    monkeypatch.setattr(featuremap, "build_map", lambda: fmap)
    doc = tmp_path / "FEATURE_MAP.md"
    doc.write_text(f"# map\n{featuremap.GENERATED_BEGIN}\n{featuremap.GENERATED_END}\n", encoding="utf-8")
    with pytest.raises(SystemExit, match="stale"):
        main(["map", "--check", str(doc)])
    main(["map", "--write", str(doc)])
    main(["map", "--check", str(doc)])
    assert "is current" in capsys.readouterr().out
    text = doc.read_text(encoding="utf-8")
    assert text.startswith("# map\n") and "## HTTP routes" in text
    with pytest.raises(SystemExit, match="drop --feature"):
        main(["map", "--check", str(doc), "--feature", "quotes"])


def test_feature_map_doc_keeps_its_srs_table_and_markers() -> None:
    """The CI job (`.github/workflows/feature-map.yml`) checks the generated part; this checks the frame."""
    text = (REPO / "docs" / "FEATURE_MAP.md").read_text(encoding="utf-8")
    assert text.count(featuremap.GENERATED_BEGIN) == 1
    assert text.count(featuremap.GENERATED_END) == 1
    head = text[: text.index(featuremap.GENERATED_BEGIN)]
    for section in ("§8", "§9", "§10–11", "§12", "§13", "§14", "§15", "§16", "§17"):
        assert f"| {section} |" in head, section
