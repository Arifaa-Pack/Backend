"""`kx` entrypoint: api | worker | beat | voice | migrate | reindex | platform show | create-user | map |
content import-site | catalogue import-erp (one image; Coolify services api, worker, beat (exactly one),
voice, plus the one-off migrate job)."""

from __future__ import annotations

import argparse
from typing import Any

from kx.core.settings import LogicalDb


def _api(_: argparse.Namespace) -> None:
    import uvicorn  # noqa: PLC0415

    from arifaa_pack.config import app_settings  # noqa: PLC0415

    uvicorn.run(
        "arifaa_pack.api:create_app",
        factory=True,
        host="0.0.0.0",  # noqa: S104 - container port
        port=app_settings().api_port,
        proxy_headers=True,
        forwarded_allow_ips=app_settings().forwarded_allow_ips,  # the Coolify proxy only (D53)
        log_config=None,
        # infra M5: finish in-flight requests on SIGTERM, inside the stack's 30 s stop_grace_period
        timeout_graceful_shutdown=25,
    )


def _worker(args: argparse.Namespace) -> None:
    """Celery worker (D25). One event loop + container per process, built on its first task."""
    from arifaa_pack.celery_app import app  # noqa: PLC0415
    from arifaa_pack.config import app_settings  # noqa: PLC0415
    from arifaa_pack.jobqueue import ALL_QUEUES  # noqa: PLC0415

    argv = ["worker", "--loglevel=INFO", f"--concurrency={app_settings().worker_concurrency}"]
    # every queue unless told otherwise (one worker serves all; production splits AI turns off, D-6)
    argv.append(f"--queues={','.join(args.queues or ALL_QUEUES)}")
    if args.pool:
        argv.append(f"--pool={args.pool}")
    app.worker_main(argv)


def _beat(_: argparse.Namespace) -> None:
    """Celery Beat (D25): run EXACTLY ONE (ECS desired count 1). It only sends timers; due work and long
    waits are rows in automation.scheduled_jobs, so a Beat restart loses nothing."""
    from arifaa_pack.celery_app import app  # noqa: PLC0415

    app.start(["beat", "--loglevel=INFO", "--schedule=/tmp/celerybeat-schedule"])


def _voice(_: argparse.Namespace) -> None:
    """LiveKit voice worker (D52); the entry and prewarm functions live in `arifaa_pack.voice_worker`."""
    from arifaa_pack.voice_worker import run  # noqa: PLC0415 - optional voice extra

    run()


def _platform(_: argparse.Namespace) -> None:
    """D41: print the resolved paid / free version per field and the vendor order (no secrets)."""
    from arifaa_pack.container import configured_vendors  # noqa: PLC0415
    from kx.core.platform import summary  # noqa: PLC0415
    from kx.core.settings import get_settings  # noqa: PLC0415

    core = get_settings()
    rows = summary(core.modes(), configured_vendors(core))
    print(f"KX_PLATFORM_PLAN={core.platform_plan}")
    print(f"{'field':<12} {'mode':<9} {'order':<28} missing")
    for r in rows:
        print(f"{r['field']:<12} {r['mode']:<9} {r['order']:<28} {r['missing']}")


def _reindex(args: argparse.Namespace) -> None:
    """D47: re-embed every knowledge chunk whose vectors came from another model (after switching
    KX_PLATFORM__EMBEDDINGS). Search keeps working on full-text while it runs."""
    import asyncio  # noqa: PLC0415

    from arifaa_pack.container import build  # noqa: PLC0415

    async def run() -> None:
        async with build() as c:
            done = await c.knowledge_indexer.reembed_stale(batch=args.batch)
        print(f"re-embedded {done} chunks with KX_PLATFORM__EMBEDDINGS={c.modes.embeddings}")

    asyncio.run(run())


def _migrate(args: argparse.Namespace) -> None:
    """Alembic for every database (idempotent). The job queue is a table in the automation revisions now
    (automation_0003), so there is no separate queue schema to apply."""
    from arifaa_pack.migrations import setup_checkpointer, upgrade  # noqa: PLC0415

    dbs = [LogicalDb(d) for d in args.db] if args.db else list(LogicalDb)
    upgrade(dbs, args.revision)
    if LogicalDb.AGENTS in dbs and args.revision == "head":
        setup_checkpointer()  # D48: DDL never runs from app processes behind PgBouncer


def _create_user(args: argparse.Namespace) -> None:
    """The first admin (and the break-glass account) before anyone can sign in to the CRM. Prints the
    one-time password once; the person changes it at first sign-in and enrols an authenticator app."""
    import asyncio  # noqa: PLC0415

    from arifaa_pack.container import build  # noqa: PLC0415
    from kx.crm import Actor, create_user  # noqa: PLC0415

    async def run() -> None:
        async with build() as c, c.databases.transaction(LogicalDb.CRM) as s:
            user, password = await create_user(
                s,
                c.crm_deps,
                email=args.email,
                full_name=args.name,
                role=args.role,
                phone_e164=args.phone,
                break_glass=args.break_glass,
                actor=Actor.system("cli"),
            )
        print(f"created {user.role} {user.email} ({user.id})")
        print(f"temporary password: {password}")

    asyncio.run(run())


def _first_run(args: argparse.Namespace) -> None:
    """18 Nov launch-day helper (docs/GO_LIVE_CHECKLIST.md C7): checks the databases are migrated, creates
    the MD and the admin if they do not exist yet (each one-time password printed once), shows the platform
    matrix and what is still missing. Safe to run again: existing users are left alone."""
    import asyncio  # noqa: PLC0415

    from sqlalchemy import func, select  # noqa: PLC0415

    from arifaa_pack.container import build, configured_vendors  # noqa: PLC0415
    from arifaa_pack.migrations import status  # noqa: PLC0415
    from kx.core.platform import summary  # noqa: PLC0415
    from kx.core.settings import get_settings  # noqa: PLC0415
    from kx.crm import Actor, company_letterhead, create_user  # noqa: PLC0415
    from kx.crm.models import User  # noqa: PLC0415  (ADR-0001 rule-3 baseline)

    print("1. databases")
    behind = []
    for db in LogicalDb:
        current, head = status(db)
        ok = current == head
        state = "ok" if ok else f"BEHIND (head {head}): run kx migrate"
        print(f"   {db.value:<11} {current or '-':<16} {state}")
        if not ok:
            behind.append(db.value)
    if behind:
        raise SystemExit("migrate first: kx migrate")

    async def run() -> None:
        async with build() as c:
            print("2. staff")
            for role, email, name in (
                ("md", args.md_email, args.md_name),
                ("admin", args.admin_email, args.admin_name),
            ):
                async with c.databases.transaction(LogicalDb.CRM) as s:
                    found = (
                        await s.execute(select(User).where(func.lower(User.email) == email.lower()))
                    ).scalar_one_or_none()
                    if found is not None:
                        print(f"   {role:<6} {email}: exists ({found.role}), left as it is")
                        continue
                    user, password = await create_user(
                        s, c.crm_deps, email=email, full_name=name, role=role, actor=Actor.system("first_run")
                    )
                print(f"   {role:<6} {user.email}: created, one-time password: {password}")
            print("3. letterhead printed on quotes")
            async with c.databases.transaction(LogicalDb.CRM) as s:
                lh = await company_letterhead(s, c.crm_deps)
            for field in ("name", "address", "trn", "phone", "email"):
                value = getattr(lh, field)
                flag = (
                    "  <- set it in the CRM (Settings > Company details)"
                    if not value or "REPLACE" in value
                    else ""
                )
                print(f"   {field:<8} {value or '-'}{flag}")
            print(f"   logo {'yes' if lh.logo else 'no'}, bank details {'yes' if lh.iban else 'no'}")

    asyncio.run(run())
    core = get_settings()
    print(f"4. platform (KX_PLATFORM_PLAN={core.platform_plan})")
    for r in summary(core.modes(), configured_vendors(core)):
        print(f"   {r['field']:<12} {r['mode']:<9} {r['order']:<28} {r['missing']}")
    print("Next: docs/GO_LIVE_CHECKLIST.md sections D (WhatsApp templates), E (data) and F (live tests).")


def _evals(args: argparse.Namespace) -> None:
    """18 Nov: run the eval scenarios live against ONE model vendor (e.g. the Gemini standby before switching
    it on). Each scenario goes through the real graphs, guards and price guard; exits 1 if any fails."""
    import asyncio  # noqa: PLC0415

    from arifaa_pack.config import app_settings  # noqa: PLC0415
    from kx.agents import (  # noqa: PLC0415
        AnthropicApiProvider,
        GeminiApiProvider,
        ModelProvider,
        ModelRouter,
        run_suite,
        tier_options,
    )

    s = app_settings()
    provider: ModelProvider
    if args.vendor == "gemini":
        if s.gemini_api_key is None:
            raise SystemExit("set KX_APP_GEMINI_API_KEY (a paid key: the free tier allows 20 requests a day)")
        provider = GeminiApiProvider(s.gemini_api_key)
    else:
        provider = AnthropicApiProvider(s.anthropic_api_key)
    router = ModelRouter([provider], tier_options(s.llm_effort_fast, s.llm_effort_smart, s.llm_effort_judge))
    results = asyncio.run(run_suite(models=router))
    for r in results:
        print(
            f"{'PASS' if r.passed else 'FAIL'}  {r.id}" + ("" if r.passed else f"  {'; '.join(r.failures)}")
        )
    failed = sum(not r.passed for r in results)
    print(f"{len(results) - failed}/{len(results)} passed ({args.vendor})")
    if failed:
        raise SystemExit(1)


def _map(args: argparse.Namespace) -> None:
    """ADR-0001 Phase 0: where every contract, event handler, route, job and Beat entry is implemented, read
    from the real container built offline (no database, Redis or network). `--format json` is sorted for
    diffs; `--feature` keeps the entries that mention a name (e.g. `quotes`, `change-requests`)."""
    import pathlib  # noqa: PLC0415
    import sys  # noqa: PLC0415

    from arifaa_pack import featuremap  # noqa: PLC0415

    if (args.write or args.check) and args.feature:
        raise SystemExit("--write / --check take the whole map: drop --feature")

    fmap = featuremap.filter_map(featuremap.build_map(), args.feature)
    target = args.write or args.check
    if target is None:
        sys.stdout.write(featuremap.to_json(fmap) if args.format == "json" else featuremap.to_markdown(fmap))
        return
    # docs/FEATURE_MAP.md: the generated region between its markers is the full Markdown map
    path = pathlib.Path(target)
    document = path.read_text(encoding="utf-8")
    generated = featuremap.to_markdown(fmap)
    if args.check:
        if not featuremap.is_current(document, generated):
            raise SystemExit(f"{target} is stale: run `kx map --write {target}` and commit it")
        print(f"{target} is current")
        return
    path.write_text(featuremap.splice(document, generated), encoding="utf-8", newline="\n")
    print(f"wrote {target}")


def _catalogue(args: argparse.Namespace) -> None:
    """WP-C9 (D61): load Arifa's ERP stock list (CSV or .xlsx, the ERP's own columns) into the CRM: every
    item and its closing stock; packing / raw material kept but not sellable; new sellable items linked to
    their website product where it is safe. Prices are never touched. `--dry-run` saves nothing."""
    import asyncio  # noqa: PLC0415
    import pathlib  # noqa: PLC0415
    from datetime import UTC, datetime  # noqa: PLC0415

    from kx.core.db import Databases  # noqa: PLC0415
    from kx.core.settings import get_settings  # noqa: PLC0415
    from kx.crm import Actor, erp_summary, import_erp  # noqa: PLC0415

    data = pathlib.Path(args.file).read_bytes()

    async def run() -> None:
        dbs = Databases(get_settings(), only={LogicalDb.CRM})
        try:
            async with dbs.transaction(LogicalDb.CRM) as s, s.begin_nested() as nested:
                report = await import_erp(s, data, actor=Actor.system("cli"), now=datetime.now(UTC))
                if args.dry_run:
                    await nested.rollback()
        finally:
            await dbs.dispose()
        for key, value in erp_summary(report).items():
            print(f"{key}: {value}")
        print("dry run: nothing saved" if args.dry_run else "saved")

    asyncio.run(run())


def _content(args: argparse.Namespace) -> None:
    """WP-C2 (D54): load the current website's content into the CMS and publish it, so the client edits it
    from day one and the AI's site map and knowledge base are filled. Defaults to the Arifa seed shipped with
    the app (arifaa_pack/seed: the website's catalogue.json and clients.json, the page layouts with their
    section ids, tour chapters, stations, synonyms, the Arabic dictionary). Safe to run again: unchanged
    documents stay."""
    import asyncio  # noqa: PLC0415
    import json  # noqa: PLC0415
    import pathlib  # noqa: PLC0415

    from kx.cms import import_site  # noqa: PLC0415
    from kx.core.db import Databases  # noqa: PLC0415
    from kx.core.settings import get_settings  # noqa: PLC0415

    seed_dir = pathlib.Path(__file__).resolve().parent / "seed"

    def load(path: str | None, default: str) -> dict[str, Any]:
        data: dict[str, Any] = json.loads(
            pathlib.Path(path or seed_dir / default).read_text(encoding="utf-8")
        )
        return data

    async def run() -> None:
        dbs = Databases(get_settings(), only={LogicalDb.CMS})
        try:
            report = await import_site(
                dbs,
                catalogue=load(args.catalogue, "catalogue.json"),
                seed=load(args.seed, "cms_seed.json"),
                clients=None if args.no_clients else load(args.clients, "clients.json"),
                arabic=load(args.arabic, "arabic.json"),
            )
        finally:
            await dbs.dispose()
        print("published:", report.published or "nothing new")
        print("unchanged:", report.unchanged or "-")
        for problem in report.problems:
            print("PROBLEM:", problem)
        if report.problems:
            raise SystemExit(1)

    asyncio.run(run())


def main(argv: list[str] | None = None) -> None:  # noqa: PLR0915 - one parser per command
    p = argparse.ArgumentParser(prog="kx")
    sub = p.add_subparsers(dest="cmd", required=True)
    sub.add_parser("api").set_defaults(fn=_api)
    w = sub.add_parser("worker")
    w.add_argument("--queues", nargs="*", default=None)
    w.add_argument(
        "--pool", default=None, help="celery pool: prefork (default), threads (AI turns: I/O-bound), solo"
    )
    w.set_defaults(fn=_worker)
    sub.add_parser("beat").set_defaults(fn=_beat)
    sub.add_parser("voice").set_defaults(fn=_voice)
    pl = sub.add_parser("platform", help="D41 paid / free version per field")
    pl.add_argument("action", choices=["show"])
    pl.set_defaults(fn=_platform)
    r = sub.add_parser("reindex", help="D47 re-embed knowledge chunks of another model")
    r.add_argument("--batch", type=int, default=16)
    r.set_defaults(fn=_reindex)
    m = sub.add_parser("migrate")
    m.add_argument("--db", nargs="*", choices=[d.value for d in LogicalDb])
    m.add_argument("--revision", default="head")
    m.set_defaults(fn=_migrate)
    fr = sub.add_parser("first-run", help="launch day: check migrations, create the MD and admin (18 Nov)")
    fr.add_argument("--md-email", required=True)
    fr.add_argument("--md-name", required=True)
    fr.add_argument("--admin-email", required=True)
    fr.add_argument("--admin-name", required=True)
    fr.set_defaults(fn=_first_run)
    ev = sub.add_parser("evals", help="run the eval scenarios live against one model vendor (18 Nov)")
    ev.add_argument("--vendor", choices=["claude", "gemini"], default="gemini")
    ev.set_defaults(fn=_evals)
    u = sub.add_parser("create-user", help="a staff user with a one-time password (D26)")
    u.add_argument("--email", required=True)
    u.add_argument("--name", required=True)
    u.add_argument("--role", required=True, choices=["md", "manager", "admin", "sales", "content_editor"])
    u.add_argument("--phone", default=None, help="E.164, for WhatsApp / call alerts")
    u.add_argument("--break-glass", action="store_true", help="emergency admin: every sign-in is alerted")
    u.set_defaults(fn=_create_user)
    cg = sub.add_parser("catalogue", help="D61 CRM: import the ERP stock list (WP-C9)")
    cg.add_argument("action", choices=["import-erp"])
    cg.add_argument("file", help="the ERP export (CSV or .xlsx): Category, Item Description, Unit, ClosStk")
    cg.add_argument("--dry-run", action="store_true", help="show what would change, save nothing")
    cg.set_defaults(fn=_catalogue)
    ct = sub.add_parser("content", help="D54 CMS: import the current website's content (WP-C2)")
    ct.add_argument("action", choices=["import-site"])
    ct.add_argument(
        "--catalogue", help="the website's src/data/catalogue.json (default: the app's seed copy)"
    )
    ct.add_argument("--clients", help="the website's src/data/clients.json (default: the app's seed copy)")
    ct.add_argument(
        "--seed", help="site settings, page layouts, chapters, stations, synonyms (cms_seed.json)"
    )
    ct.add_argument("--arabic", help="English -> Arabic dictionary as JSON (default: arabic.json)")
    ct.add_argument("--no-clients", action="store_true", help="skip industries, stories and client logos")
    ct.set_defaults(fn=_content)
    mp = sub.add_parser("map", help="where each contract, event, route and job is implemented (ADR-0001)")
    mp.add_argument("--format", choices=["md", "json"], default="md")
    mp.add_argument("--feature", default=None, help="keep entries mentioning this name, e.g. quotes")
    out = mp.add_mutually_exclusive_group()
    out.add_argument(
        "--write", metavar="FILE", help="refresh the generated region of FILE (docs/FEATURE_MAP.md)"
    )
    out.add_argument("--check", metavar="FILE", help="exit 1 if the generated region of FILE is stale (CI)")
    mp.set_defaults(fn=_map)
    args = p.parse_args(argv)
    args.fn(args)


if __name__ == "__main__":
    main()
