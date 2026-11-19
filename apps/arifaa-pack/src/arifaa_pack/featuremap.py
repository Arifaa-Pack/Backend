"""`kx map`: answers "where is this implemented, and who reacts to this event?" in one command.

The map is read from the real wiring, never from source text: the app's own `create_app()` and
`container.build()` run once with every I/O edge replaced (an empty in-memory "database", an in-memory
LangGraph checkpointer, a job kicker that sends nothing), and the result is walked:

* contracts / ports: every `Protocol` class in `kx.*` with the classes that satisfy it structurally (same
  method names and parameter names), found in the composition root's namespace and in the live object graph
  of the built `Container` (`injected_at` = the attribute path of each wired instance);
* events: every event name in `kx.core.events`, its payload model (`kx.core.event_payloads.PAYLOADS`), and
  the handlers the container's `EventBus` routes it to (resolved through each package's handler table);
* routes: every route of the app's FastAPI router after its lifespan has mounted the package routers, with
  the platform functions each endpoint references (its bytecode names, resolved against its globals);
* jobs / periodic: the `JobRunner` task table and the Beat entries (`arifaa_pack.schedule`).

Nothing connects to PostgreSQL, Redis, R2 or a vendor. Settings come from the process environment (the
defaults in CI); a setting that switches wiring on or off (Chromium present, Stalwart enabled ...) changes
`injected_at`, which is why the Markdown form leaves it out. Output is sorted so two runs diff cleanly.
"""

from __future__ import annotations

import asyncio
import contextlib
import dataclasses
import dis
import functools
import importlib
import inspect
import json
import pkgutil
import re
import sys
import types
import typing
import uuid
from collections import deque
from collections.abc import AsyncIterator, Callable, Iterable, Iterator, Mapping, Sequence
from pathlib import Path
from typing import Any, Final

from fastapi import FastAPI
from fastapi.routing import APIRoute, APIWebSocketRoute

from kx.core import events as ev
from kx.core.event_payloads import PAYLOADS
from kx.core.jobs import Kick
from kx.core.settings import CoreSettings, LogicalDb

PLATFORM_PACKAGES: Final = ("kx.core", "kx.crm", "kx.automation", "kx.agents")
_OWNERS: Final = (*PLATFORM_PACKAGES, "arifaa_pack")
_EVENT_NAME: Final = re.compile(r"^[a-z_]+\.[a-z_]+\.v\d+$")
_CALL_DEPTH: Final = 3
GENERATED_BEGIN: Final = "<!-- BEGIN GENERATED: kx map --format md (do not edit by hand) -->"
GENERATED_END: Final = "<!-- END GENERATED -->"

FeatureMap = dict[str, list[dict[str, Any]]]


# ---------------------------------------------------------------- offline I/O edges
class _EmptyResult:
    """The result of any query against the empty offline database: no rows."""

    def tuples(self) -> list[Any]:
        return []

    def all(self) -> list[Any]:
        return []

    def scalars(self) -> _EmptyResult:
        return self

    def scalar_one_or_none(self) -> None:
        return None

    def first(self) -> None:
        return None

    def __iter__(self) -> Iterator[Any]:
        return iter(())


class _EmptySession:
    """Stands in for an `AsyncSession` on an empty database. Only reads are expected while wiring."""

    async def execute(self, *_: Any, **__: Any) -> _EmptyResult:
        return _EmptyResult()

    async def get(self, *_: Any, **__: Any) -> None:
        return None

    def __getattr__(self, name: str) -> Any:
        raise RuntimeError(f"kx map runs offline: session.{name} is not available")


class OfflineDatabases:
    """`kx.core.db.Databases` without engines: every transaction sees an empty database."""

    def __init__(self, settings: CoreSettings, only: set[LogicalDb] | None = None) -> None:
        self.settings = settings

    @contextlib.asynccontextmanager
    async def transaction(self, db: LogicalDb) -> AsyncIterator[Any]:
        yield _EmptySession()

    def dependency(self, db: LogicalDb) -> Callable[[], AsyncIterator[Any]]:
        async def _dep() -> AsyncIterator[Any]:
            yield _EmptySession()

        return _dep

    def engine(self, db: LogicalDb) -> Any:
        raise RuntimeError("kx map runs offline: no database engine")

    async def dispose(self) -> None:
        return None


class _NullKicker:
    """`kx.core.jobs.Kicker` that sends nothing (the real one is Celery)."""

    async def kick(self, jobs: Sequence[Kick], *, resend: bool = False) -> Sequence[uuid.UUID]:
        return ()


@contextlib.asynccontextmanager
async def _memory_checkpointer(*_: Any, **__: Any) -> AsyncIterator[Any]:
    from langgraph.checkpoint.memory import InMemorySaver  # noqa: PLC0415 - only the map needs it

    yield InMemorySaver()


@contextlib.contextmanager
def _patched(target: object, **attrs: Any) -> Iterator[None]:
    old = {name: getattr(target, name) for name in attrs}
    for name, value in attrs.items():
        setattr(target, name, value)
    try:
        yield
    finally:
        for name, value in old.items():
            setattr(target, name, value)


@contextlib.asynccontextmanager
async def offline_app() -> AsyncIterator[tuple[FastAPI, Any]]:
    """The real API app with its lifespan entered over the real container, I/O edges replaced. Yields the
    app (routes mounted) and the built `Container`. Log lines go to stderr so stdout stays clean."""
    from arifaa_pack import api, container  # noqa: PLC0415 - heavy imports, only for `kx map`

    with (
        contextlib.redirect_stdout(sys.stderr),
        _patched(container, Databases=OfflineDatabases, open_checkpointer=_memory_checkpointer),
        _patched(api, build=functools.partial(container.build, kicker=_NullKicker())),
    ):
        app = api.create_app()
        async with app.router.lifespan_context(app):
            yield app, app.state.container


# ---------------------------------------------------------------- naming helpers
def _workspace_path(module_name: str) -> str:
    """Repo-relative file of a module (`packages/kx-core-crm/src/kx/crm/jobs.py`), `<module>` otherwise."""
    module = sys.modules.get(module_name)
    file = getattr(module, "__file__", None)
    if not file:
        return f"<{module_name}>"
    path = Path(file).resolve()
    depth = len(module_name.split(".")) - (0 if path.name == "__init__.py" else 1)
    src = path.parents[depth]
    root = src.parents[2] if src.name == "src" and len(src.parents) > 2 else src
    try:
        return path.relative_to(root).as_posix()
    except ValueError:
        return f"<{module_name}>"


def package_of(module_name: str) -> str:
    for prefix, name in (
        ("kx.core", "core"),
        ("kx.crm", "crm"),
        ("kx.automation", "automation"),
        ("kx.agents", "agents"),
        ("arifaa_pack", "app"),
    ):
        if module_name == prefix or module_name.startswith(prefix + "."):
            return name
    return "external"


def _owned(module_name: str | None) -> bool:
    return bool(module_name) and package_of(module_name or "") != "external"


def _unwrap(fn: Any) -> Any:
    while True:
        if isinstance(fn, functools.partial):
            fn = fn.func
        elif inspect.ismethod(fn):
            fn = fn.__func__
        elif hasattr(fn, "__wrapped__"):
            fn = fn.__wrapped__
        else:
            return fn


def ref(obj: Any) -> str:
    """`module:qualname` of a function, method or class."""
    obj = _unwrap(obj)
    return f"{getattr(obj, '__module__', '?')}:{getattr(obj, '__qualname__', type(obj).__qualname__)}"


def _where(obj: Any) -> dict[str, str]:
    obj = _unwrap(obj)
    module = getattr(obj, "__module__", "?")
    return {"name": ref(obj), "package": package_of(module), "file": _workspace_path(module)}


def _closure(fn: Any) -> dict[str, Any]:
    fn = _unwrap(fn)
    code = getattr(fn, "__code__", None)
    if code is None or not fn.__closure__:
        return {}
    return {name: cell.cell_contents for name, cell in zip(code.co_freevars, fn.__closure__, strict=True)}


def _code_names(code: types.CodeType) -> set[str]:
    names = set(code.co_names)
    for const in code.co_consts:
        if isinstance(const, types.CodeType):
            names |= _code_names(const)
    return names


_LOADS: Final = frozenset({"LOAD_GLOBAL", "LOAD_DEREF", "LOAD_NAME", "LOAD_FAST", "LOAD_FAST_BORROW"})


def _attribute_chains(code: types.CodeType) -> set[tuple[str, ...]]:
    """Every `name.attr[.attr ...]` the bytecode reads (nested code included), e.g. ("quotes", "revise")."""
    chains: set[tuple[str, ...]] = set()
    chain: list[str] = []
    for ins in dis.get_instructions(code):
        if ins.opname in _LOADS:
            chain = [str(ins.argval)]
        elif ins.opname == "LOAD_ATTR" and chain:
            chain.append(str(ins.argval))
            chains.add(tuple(chain))
        else:
            chain = []
    for const in code.co_consts:
        if isinstance(const, types.CodeType):
            chains |= _attribute_chains(const)
    return chains


def _module_targets(module: types.ModuleType, local: str, chains: set[tuple[str, ...]]) -> list[Any]:
    """What `local.attr` names in a module: only attributes actually read on that name, so a function name
    that two modules share is not matched in the module the code never touches."""
    out: list[Any] = []
    for chain in chains:
        if chain[0] != local:
            continue
        obj: Any = module
        for attr in chain[1:]:
            obj = getattr(obj, attr, None)
            if not isinstance(obj, types.ModuleType):
                break
        if obj is not None and not isinstance(obj, types.ModuleType):
            out.append(obj)
    return out


def calls(fn: Any, *, depth: int = _CALL_DEPTH) -> list[str]:
    """Platform functions an endpoint or job body references, outside its own module: the names in its
    bytecode resolved against its globals and closure (helpers in its own module are followed)."""
    root = _unwrap(fn)
    home = getattr(root, "__module__", "")
    found: set[str] = set()
    seen: set[int] = set()

    def visit(f: Any, level: int) -> None:
        f = _unwrap(f)
        code = getattr(f, "__code__", None)
        if code is None or id(f) in seen or level > depth:
            return
        seen.add(id(f))
        names = _code_names(code)
        chains = _attribute_chains(code)
        scope = {**{n: f.__globals__[n] for n in names if n in f.__globals__}, **_closure(f)}
        targets: list[Any] = []
        for local, value in scope.items():
            if isinstance(value, types.ModuleType):
                targets += _module_targets(value, local, chains)
            else:
                targets.append(value)
        for candidate in targets:
            target = _unwrap(candidate)
            if not inspect.isfunction(target) or not _owned(target.__module__):
                continue
            if target.__module__ == home:
                visit(target, level + 1)
            elif package_of(target.__module__) != "core":
                found.add(ref(target))

    visit(root, 0)
    return sorted(found)


# ---------------------------------------------------------------- contracts and ports
def _platform_modules() -> list[types.ModuleType]:
    mods: list[types.ModuleType] = []
    for name in PLATFORM_PACKAGES:
        pkg = importlib.import_module(name)
        mods.append(pkg)
        mods += [
            importlib.import_module(i.name) for i in pkgutil.walk_packages(pkg.__path__, prefix=name + ".")
        ]
    return mods


def protocols() -> list[type]:
    """Every Protocol class defined in the platform packages, by module then name."""
    out = {
        ref(obj): obj
        for mod in _platform_modules()
        for obj in vars(mod).values()
        if inspect.isclass(obj) and obj.__module__ == mod.__name__ and typing.is_protocol(obj)
    }
    return [out[k] for k in sorted(out)]


_POSITIONAL: Final = (inspect.Parameter.POSITIONAL_ONLY, inspect.Parameter.POSITIONAL_OR_KEYWORD)


def _signature(fn: Any) -> list[inspect.Parameter] | None:
    try:
        params = list(inspect.signature(fn).parameters.values())
    except TypeError, ValueError:
        return None
    return [p for p in params if p.name != "self"]


def _annotated_class(annotation: Any, fn: Any) -> type | None:
    """The class an annotation names (strings resolved in the function's module), None for anything else
    (a union, a Literal, an alias, a name only imported for type checking)."""
    if isinstance(annotation, str):
        try:
            annotation = eval(annotation, getattr(fn, "__globals__", {}))  # noqa: S307 - own source only
        except Exception:
            return None
    return annotation if isinstance(annotation, type) else None


def _compatible(want: inspect.Parameter, want_fn: Any, have: inspect.Parameter, have_fn: Any) -> bool:
    """Both annotations name classes -> one must be a subclass of the other; otherwise accepted."""
    a, b = _annotated_class(want.annotation, want_fn), _annotated_class(have.annotation, have_fn)
    return a is None or b is None or issubclass(a, b) or issubclass(b, a)


def _accepts(want_fn: Any, have_fn: Any) -> bool:
    """`have_fn` can be called the way the protocol's method is called: the same positional parameters (by
    position, any name, compatible class) and the same keyword-only ones (by name), or *args / **kwargs."""
    want, have = _signature(want_fn), _signature(have_fn)
    if want is None or have is None:
        return True
    var_pos = any(p.kind is inspect.Parameter.VAR_POSITIONAL for p in have)
    var_kw = any(p.kind is inspect.Parameter.VAR_KEYWORD for p in have)
    positional = [p for p in have if p.kind in _POSITIONAL]
    by_name = {p.name: p for p in have}
    index = 0
    for p in want:
        if p.kind in _POSITIONAL:
            if index < len(positional):
                if not _compatible(p, want_fn, positional[index], have_fn):
                    return False
            elif not var_pos:
                return False
            index += 1
        elif p.kind is inspect.Parameter.KEYWORD_ONLY:
            match = by_name.get(p.name)
            if match is None and not var_kw:
                return False
            if match is not None and not _compatible(p, want_fn, match, have_fn):
                return False
    return True


def _has_data_member(cls: type, member: str, sample: object | None) -> bool:
    if hasattr(cls, member) or (sample is not None and hasattr(sample, member)):
        return True
    if dataclasses.is_dataclass(cls) and member in {f.name for f in dataclasses.fields(cls)}:
        return True
    if any(member in getattr(k, "__annotations__", {}) for k in cls.__mro__):
        return True
    init = _signature(
        getattr(cls, "__init__", None)
    )  # `self.model = model`: a constructor argument of the same name
    return init is not None and member in {p.name for p in init}


def conforms(cls: type, proto: type, sample: object | None = None) -> bool:
    """Structural check: every protocol member exists; each method accepts the protocol's call (see
    `_accepts`) and is a coroutine where the protocol's is. `sample` is a wired instance, if any."""
    if typing.is_protocol(cls) or cls is proto:
        return False
    for member in typing.get_protocol_members(proto):
        want = getattr(proto, member, None)
        if not inspect.isfunction(want):
            if not _has_data_member(cls, member, sample):
                return False
            continue
        have = getattr(cls, member, None)
        if not callable(have):
            return False
        if not _accepts(want, have):
            return False
        is_async = inspect.iscoroutinefunction(have) or inspect.isasyncgenfunction(have)
        if inspect.iscoroutinefunction(want) and inspect.isfunction(have) and not is_async:
            return False
    return True


def _children(obj: Any) -> Iterable[tuple[str, Any]]:
    if isinstance(obj, Mapping):
        return [(f"[{k!r}]", v) for k, v in obj.items() if isinstance(k, str)]
    if isinstance(obj, list | tuple):
        return [(f"[{i}]", v) for i, v in enumerate(obj)]
    if isinstance(obj, functools.partial):
        return [("func", obj.func), *((f"args[{i}]", a) for i, a in enumerate(obj.args))] + [
            (f"keywords[{k!r}]", v) for k, v in obj.keywords.items()
        ]
    if inspect.ismethod(obj):
        return [("__self__", obj.__self__)]
    if inspect.isfunction(obj):
        return [(f"<{n}>", v) for n, v in _closure(obj).items()]
    if dataclasses.is_dataclass(obj) and not isinstance(obj, type):
        return [(f.name, getattr(obj, f.name, None)) for f in dataclasses.fields(obj)]
    attrs = dict(getattr(obj, "__dict__", {}))
    for klass in type(obj).__mro__:
        for slot in getattr(klass, "__slots__", ()):
            if isinstance(slot, str) and hasattr(obj, slot):
                attrs.setdefault(slot, getattr(obj, slot))
    return list(attrs.items())


def _walkable(obj: Any) -> bool:
    if isinstance(obj, Mapping | list | tuple | functools.partial) or inspect.ismethod(obj):
        return True
    if inspect.isfunction(obj):
        return _owned(obj.__module__)
    return not isinstance(obj, type) and _owned(type(obj).__module__)


def wired_instances(root: Any) -> list[tuple[str, Any]]:
    """(attribute path, object) for every platform object reachable from the container, breadth first
    (each object once, at its shortest path)."""
    seen = {id(root)}
    queue: deque[tuple[str, Any]] = deque([("container", root)])
    found: list[tuple[str, Any]] = []
    while queue:
        path, obj = queue.popleft()
        for name, child in _children(obj):
            if child is None or id(child) in seen or not _walkable(child):
                continue
            seen.add(id(child))
            sep = "" if name.startswith("[") else "."
            child_path = f"{path}{sep}{name}"
            container_like = isinstance(child, Mapping | list | tuple | functools.partial)
            if not container_like and not inspect.isroutine(child):  # a wired platform object
                found.append((child_path, child))
            queue.append((child_path, child))
    return found


def _root_classes() -> list[type]:
    """Classes the composition root can wire: imported by, or defined in, the app's wiring modules."""
    from arifaa_pack import adapters, container, jobqueue  # noqa: PLC0415

    return [
        obj
        for mod in (container, adapters, jobqueue)
        for obj in vars(mod).values()
        if inspect.isclass(obj) and _owned(obj.__module__)
    ]


def _ports(c: Any) -> tuple[list[dict[str, Any]], list[dict[str, Any]]]:
    instances = [(p, o) for p, o in wired_instances(c) if type(o).__module__ != __name__]
    classes = {ref(k): k for k in _root_classes() if k.__module__ != __name__}
    classes |= {ref(type(o)): type(o) for _, o in instances}
    samples = {type(o): o for _, o in reversed(instances)}
    contracts: list[dict[str, Any]] = []
    others: list[dict[str, Any]] = []
    for proto in protocols():
        impls = []
        for name in sorted(classes):
            cls = classes[name]
            if not conforms(cls, proto, samples.get(cls)):
                continue
            at = sorted(p for p, o in instances if type(o) is cls)
            impls.append({**_where(cls), "injected_at": at})
        entry = {"protocol": ref(proto), "file": _workspace_path(proto.__module__), "implementations": impls}
        (contracts if proto.__module__ == "kx.core.contracts" else others).append(entry)
    return contracts, others


# ---------------------------------------------------------------- events
def event_names() -> dict[str, str]:
    """Event name -> constant in `kx.core.events` (plus any payload registered without a constant)."""
    names = {
        v: f"kx.core.events.{k}"
        for k, v in vars(ev).items()
        if k.isupper() and isinstance(v, str) and _EVENT_NAME.match(v)
    }
    for name in PAYLOADS:
        names.setdefault(name, "")
    return names


def _handler(c: Any, task: str, event: str) -> dict[str, Any]:
    """The function that runs for `event` behind the bus task `task`, through the package's own table."""
    from arifaa_pack import container  # noqa: PLC0415
    from kx.automation import jobs as auto_jobs  # noqa: PLC0415
    from kx.crm import jobs as crm_jobs  # noqa: PLC0415

    spec = c.runner.specs.get(task)
    inbox: str | None = None
    fn: Any = spec.handler if spec is not None else None
    if task == container.CRM_EVENT:  # crm_jobs.dispatch runs HANDLERS[event] in a CRM transaction
        fn = crm_jobs.HANDLERS.get(event, fn)
    elif task == container.AGENTS_EVENT and spec is not None:  # AgentHandlers.handlers(), each in `_once`
        wrapped = _closure(spec.handler).get("agent_handlers", {}).get(event)
        if wrapped is not None:
            inner = _closure(wrapped)
            fn, inbox = inner.get("handler", wrapped), inner.get("name")
    elif task == auto_jobs.EVENT:  # EventHandlers.registry(): (inbox name, handler)
        entry = c.automation_runtime.handlers.registry().get(event)
        if entry is not None:
            inbox, fn = entry
    out: dict[str, Any] = {"task": task}
    out |= _where(fn) if fn is not None else {"name": "<unregistered task>", "package": "?", "file": "?"}
    if inbox:
        out["inbox"] = inbox
    return out


def _events(c: Any) -> list[dict[str, Any]]:
    names = event_names()
    routed = getattr(c.bus, "_routes", {})  # EventBus keeps no public list; catch routes to unknown names
    for name in routed:
        names.setdefault(name, "")
    out = []
    for name in sorted(names):
        payload = PAYLOADS.get(name)
        handlers = sorted(
            (_handler(c, task, name) for task in c.bus.subscribers(name)),
            key=lambda h: (h["package"], h["task"], h["name"]),
        )
        out.append(
            {
                "event": name,
                "constant": names[name],
                "payload": ref(payload) if payload is not None else None,
                "handlers": handlers,
            }
        )
    return out


# ---------------------------------------------------------------- routes, jobs, Beat
def _flat_routes(items: Iterable[Any], prefix: str = "") -> list[tuple[str, Any]]:
    """FastAPI keeps included routers nested (`_IncludedRouter`): walk them, adding each prefix."""
    out: list[tuple[str, Any]] = []
    for item in items:
        if isinstance(item, APIRoute | APIWebSocketRoute):
            out.append((prefix + item.path, item))
        elif hasattr(item, "original_router"):
            out += _flat_routes(item.original_router.routes, prefix + item.include_context.prefix)
    return out


def _routes(app: FastAPI) -> list[dict[str, Any]]:
    out: list[dict[str, Any]] = []
    for path, route in _flat_routes(app.router.routes):
        methods = (
            sorted((route.methods or set()) - {"HEAD"}) if isinstance(route, APIRoute) else ["WEBSOCKET"]
        )
        endpoint = _unwrap(route.endpoint)
        module = getattr(endpoint, "__module__", "?")
        entry = {
            "path": path,
            "package": package_of(module),
            "router": module,
            "endpoint": ref(endpoint),
            "file": _workspace_path(module),
            "calls": calls(endpoint),
        }
        out += [{"method": method, **entry} for method in methods]
    return sorted(out, key=lambda r: (r["path"], r["method"]))


def _jobs(c: Any) -> list[dict[str, Any]]:
    out = []
    for task in sorted(c.runner.specs):
        spec = c.runner.specs[task]
        out.append(
            {
                "task": task,
                **_where(spec.handler),
                "max_attempts": spec.max_attempts,
                "calls": calls(spec.handler),
            }
        )
    return out


def _periodic(c: Any) -> list[dict[str, Any]]:
    from arifaa_pack.schedule import beat_schedule  # noqa: PLC0415

    schedule = {name: entry["schedule"] for name, entry in beat_schedule().items()}
    out = []
    for name in sorted(set(c.periodic) | set(schedule)):
        body = c.periodic.get(name)
        when = schedule.get(name)
        where = _where(body) if body is not None else {"name": None, "package": "?", "file": "?"}
        out.append(
            {
                "entry": name,
                "schedule": str(when) if when is not None else None,
                **where,
                "calls": calls(body) if body is not None else [],
            }
        )
    return out


# ---------------------------------------------------------------- the map
async def collect() -> FeatureMap:
    async with offline_app() as (app, c):
        contracts, ports = _ports(c)
        return {
            "contracts": contracts,
            "ports": ports,
            "events": _events(c),
            "routes": _routes(app),
            "jobs": _jobs(c),
            "periodic": _periodic(c),
        }


def build_map() -> FeatureMap:
    return asyncio.run(collect())


def _norm(text: str) -> str:
    return re.sub(r"[-\s]+", "_", text.lower())


def filter_map(fmap: FeatureMap, feature: str | None) -> FeatureMap:
    """Keep the entries whose text mentions `feature` (case-insensitive; `-`, `_` and spaces alike)."""
    if not feature:
        return fmap
    needle = _norm(feature)
    return {k: [e for e in v if needle in _norm(json.dumps(e))] for k, v in fmap.items()}


def to_json(fmap: FeatureMap) -> str:
    return json.dumps(fmap, indent=2, sort_keys=True, ensure_ascii=False) + "\n"


def _cell(values: Iterable[str]) -> str:
    items = [f"`{v}`" for v in values]
    return "<br>".join(items) if items else "-"


def to_markdown(fmap: FeatureMap) -> str:
    """Tables per section. `injected_at` is left out: it depends on optional settings."""
    lines = [
        "## Contracts (`kx.core.contracts`)",
        "",
        "| Protocol | Implemented by | File |",
        "| --- | --- | --- |",
    ]

    def port_rows(entries: list[dict[str, Any]]) -> None:
        for e in entries:
            names, files = (
                _cell(i["name"] for i in e["implementations"]),
                _cell(i["file"] for i in e["implementations"]),
            )
            lines.append(f"| `{e['protocol']}` | {names} | {files} |")

    port_rows(fmap.get("contracts", []))
    lines += ["", "## Other ports (Protocols inside a package)", ""]
    lines += ["| Protocol | Implemented by | File |", "| --- | --- | --- |"]
    port_rows(fmap.get("ports", []))
    lines += ["", "## Events: who reacts", "", "| Event | Payload | Handlers |", "| --- | --- | --- |"]
    for e in fmap.get("events", []):
        handlers = [f"{h['package']}: {h['name']}" for h in e["handlers"]]
        lines.append(
            f"| `{e['event']}` | {_cell([e['payload']] if e['payload'] else [])} | {_cell(handlers)} |"
        )
    lines += ["", "## HTTP routes", "", "| Method | Path | Endpoint | Calls |", "| --- | --- | --- | --- |"]
    for r in fmap.get("routes", []):
        lines.append(f"| {r['method']} | `{r['path']}` | `{r['endpoint']}` | {_cell(r['calls'])} |")
    lines += ["", "## Background jobs (task table)", "", "| Task | Handler | Calls |", "| --- | --- | --- |"]
    for j in fmap.get("jobs", []):
        lines.append(f"| `{j['task']}` | `{j['name']}` | {_cell(j['calls'])} |")
    lines += ["", "## Beat entries", "", "| Entry | Schedule | Body | Calls |", "| --- | --- | --- | --- |"]
    for p in fmap.get("periodic", []):
        lines.append(
            f"| `{p['entry']}` | {p['schedule'] or '-'} | {_cell([p['name']] if p['name'] else [])} "
            f"| {_cell(p['calls'])} |"
        )
    return "\n".join(lines) + "\n"


def splice(document: str, generated: str) -> str:
    """The document with its generated region (between the two markers) replaced; the rest is kept. Line
    endings are normalised to LF, so a Windows checkout and CI compare equal."""
    text = document.replace("\r\n", "\n")
    start, end = text.find(GENERATED_BEGIN), text.find(GENERATED_END)
    if start < 0 or end < start:
        raise ValueError(f"{GENERATED_BEGIN!r} ... {GENERATED_END!r} markers not found")
    return text[: start + len(GENERATED_BEGIN)] + "\n\n" + generated + "\n" + text[end:]


def is_current(document: str, generated: str) -> bool:
    """True when the document's generated region already equals `generated` (line endings ignored)."""
    return splice(document, generated) == document.replace("\r\n", "\n")
