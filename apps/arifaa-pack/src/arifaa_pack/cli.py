"""`kx` entrypoint: api | worker | beat | voice | migrate | reindex | platform show | create-user (one image;
Coolify services api, worker, beat (exactly one), voice, plus the one-off migrate job)."""

from __future__ import annotations

import argparse

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
    )


def _worker(args: argparse.Namespace) -> None:
    """Celery worker (D25). One event loop + container per process, built on its first task."""
    from arifaa_pack.celery_app import app  # noqa: PLC0415
    from arifaa_pack.config import app_settings  # noqa: PLC0415

    argv = ["worker", "--loglevel=INFO", f"--concurrency={app_settings().worker_concurrency}"]
    if args.queues:
        argv.append(f"--queues={','.join(args.queues)}")
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
    from kx.crm.services.auth import create_user  # noqa: PLC0415
    from kx.crm.services.journal import Actor  # noqa: PLC0415

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


def main(argv: list[str] | None = None) -> None:
    p = argparse.ArgumentParser(prog="kx")
    sub = p.add_subparsers(dest="cmd", required=True)
    sub.add_parser("api").set_defaults(fn=_api)
    w = sub.add_parser("worker")
    w.add_argument("--queues", nargs="*", default=None)
    w.add_argument("--pool", default=None, help="celery pool (prefork default; solo for local debugging)")
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
    u = sub.add_parser("create-user", help="a staff user with a one-time password (D26)")
    u.add_argument("--email", required=True)
    u.add_argument("--name", required=True)
    u.add_argument("--role", required=True, choices=["md", "manager", "admin", "sales"])
    u.add_argument("--phone", default=None, help="E.164, for WhatsApp / call alerts")
    u.add_argument("--break-glass", action="store_true", help="emergency admin: every sign-in is alerted")
    u.set_defaults(fn=_create_user)
    args = p.parse_args(argv)
    args.fn(args)


if __name__ == "__main__":
    main()
