# ruff: noqa: E501, S106, PLR0915, RUF005 - a build script: long markdown f-strings, a dummy secret, one
# long render() routine, and list concatenation that reads more clearly than unpacking.
"""Generate the API documents of the hand-off pack from the code (no database, no network). Run through
`make handoff` (scripts/gen_handoff.py), or alone: `python scripts/gen_frontend_docs.py [out_dir]`.

Builds the same routers the API process mounts, exports OpenAPI 3.1, and writes, into docs/handoff/ (D60):
* CRM_API.md / WEBSITE_API.md = the hand-written head (scripts/handoff_heads/) + a reference generated per
  endpoint: method + path, purpose (route docstring), who may call it (read from the route's require_role /
  require_human_mfa dependency closures), parameters, request and response schema tables and JSON examples,
  then a "data shapes" appendix with every model;
* openapi/openapi-crm.json / openapi/openapi-website.json for a mock server (Prism).
"""

import json
import pathlib
import sys
from typing import Any

from fastapi import FastAPI
from fastapi.routing import APIRoute
from gen_tester_pack import example

from kx.agents import WebsiteDeps, website_router
from kx.automation import admin_router as channels_admin_router
from kx.cms import CmsDeps, ManifestCache, build_admin_router, build_media_router, build_public_router
from kx.core.auth import Role
from kx.core.errors import install_problem_handlers
from kx.crm.api import build_router as crm_router
from kx.crm.deps import CrmDeps
from kx.crm.quotes.pdf import Letterhead

HERE = pathlib.Path(__file__).resolve().parent
HEADS = HERE / "handoff_heads"
DEFAULT_OUT = HERE.parent / "docs" / "handoff"


class _Dummy:
    def __getattr__(self, name: str) -> Any:
        raise RuntimeError("offline build")


class _Dbs(_Dummy):
    def dependency(self, db: Any) -> Any:
        async def session() -> Any:  # never called: the app is only built to export OpenAPI
            yield None

        return session


def crm_app() -> FastAPI:
    app = FastAPI(title="Kairox CRM API", version="1.0.0")
    install_problem_handlers(app)
    deps = CrmDeps(uploader=_Dummy(), letterhead=Letterhead(name="Arifaa Pack", address=""))
    app.include_router(crm_router(_Dbs(), deps), prefix="/v1")  # type: ignore[arg-type]
    app.include_router(channels_admin_router(_Dummy()))  # type: ignore[arg-type]

    @app.get("/.well-known/jwks.json", tags=["auth"])
    async def jwks() -> dict[str, Any]:
        """The public key that verifies staff access tokens (RS256, D26)."""
        return {}

    return app


def website_app() -> FastAPI:
    app = FastAPI(title="Kairox website API", version="1.0.0")
    install_problem_handlers(app)
    deps = WebsiteDeps(
        databases=_Dummy(),  # type: ignore[arg-type]
        service=_Dummy(),  # type: ignore[arg-type]
        gateway=_Dummy(),
        secret="x",
        human_check=None,
        limits=_Dummy(),
        ai_budget_left=_Dummy(),  # type: ignore[arg-type]
    )
    app.include_router(website_router(deps))
    return app


def cms_app() -> FastAPI:
    app = FastAPI(title="Kairox CMS API", version="1.0.0")
    install_problem_handlers(app)
    deps = CmsDeps(databases=_Dbs(), preview_secret="x")  # type: ignore[arg-type]
    app.include_router(build_public_router(deps, ManifestCache(_Dummy())))  # type: ignore[arg-type]
    app.include_router(build_admin_router(deps))
    app.include_router(build_media_router(deps))
    return app


def flat_routes(items: Any, prefix: str = "") -> list[tuple[str, APIRoute]]:
    """FastAPI 0.16x keeps included routers nested (`_IncludedRouter`): walk them, adding each prefix."""
    out: list[tuple[str, APIRoute]] = []
    for item in items:
        if isinstance(item, APIRoute):
            out.append((prefix + item.path, item))
        elif hasattr(item, "original_router"):
            out += flat_routes(item.original_router.routes, prefix + item.include_context.prefix)
    return out


def who(route: APIRoute) -> str:
    """Roles from the auth dependency closures (MD passes wherever MANAGER is listed, kx.core.auth)."""
    found: list[str] = []

    def visit(dep: Any) -> None:
        call = getattr(dep, "call", None)
        qual = getattr(call, "__qualname__", "")
        if call is not None and qual.startswith(("require_role.", "require_human_mfa.")):
            roles: tuple[Role, ...] = ()
            for cell in call.__closure__ or ():
                value = cell.cell_contents
                if isinstance(value, tuple) and all(isinstance(r, Role) for r in value):
                    roles = value
            names = [r.value for r in roles]
            if "manager" in names and "md" not in names:
                names.append("md")
            text = ", ".join(names) if names else "any staff"
            if qual.startswith("require_human_mfa."):
                text += " (signed in with the authenticator app: `mfa=true`)"
            found.append(text)
        elif qual == "current_principal":
            found.append("any signed-in staff")
        for sub in getattr(dep, "dependencies", []):
            visit(sub)

    visit(route.dependant)
    specific = [f for f in found if f != "any signed-in staff"]
    return "; ".join(dict.fromkeys(specific or found)) or "public"


def _type(schema: dict[str, Any]) -> str:
    if "$ref" in schema:
        return "`" + str(schema["$ref"]).rsplit("/", 1)[-1] + "`"
    if "anyOf" in schema:
        return " or ".join(_type(s) for s in schema["anyOf"])
    if "enum" in schema:
        return " \\| ".join(f"`{v}`" for v in schema["enum"])
    if "const" in schema:
        return f"`{schema['const']}`"
    t = schema.get("type", "any")
    if t == "array":
        return f"list of {_type(schema.get('items', {}))}"
    if (
        t == "object"
        and "additionalProperties" in schema
        and isinstance(schema["additionalProperties"], dict)
    ):
        return f"map of {_type(schema['additionalProperties'])}"
    fmt = schema.get("format")
    return f"{t} ({fmt})" if fmt else str(t)


def _limits(schema: dict[str, Any]) -> str:
    bits = []
    for k, label in (
        ("minLength", "min length"),
        ("maxLength", "max length"),
        ("minimum", "≥"),
        ("exclusiveMinimum", ">"),
        ("maximum", "≤"),
        ("pattern", "pattern"),
        ("minItems", "min items"),
        ("maxItems", "max items"),
    ):
        if k in schema:
            bits.append(f"{label} `{schema[k]}`" if k == "pattern" else f"{label} {schema[k]}")
    if "default" in schema:
        bits.append(f"default `{json.dumps(schema['default'])}`")
    return "; ".join(bits)


def fields_table(schema: dict[str, Any]) -> list[str]:
    props = schema.get("properties", {})
    if not props:
        return ["(no fields)"]
    required = set(schema.get("required", []))
    rows = ["| Field | Type | Required | Notes |", "|---|---|---|---|"]
    for name, s in props.items():
        notes = " ".join(x for x in (s.get("description", ""), _limits(s)) if x)
        rows.append(f"| `{name}` | {_type(s)} | {'yes' if name in required else 'no'} | {notes} |")
    return rows


def render(
    app: FastAPI, title: str, prefix_filter: tuple[str, ...], exclude: tuple[str, ...] = ()
) -> tuple[str, dict[str, Any]]:
    spec = app.openapi()
    comps = spec.get("components", {}).get("schemas", {})
    routes = {(path, m): r for path, r in flat_routes(app.routes) for m in r.methods or ()}
    lines = [f"## {title}", ""]
    by_tag: dict[str, list[tuple[str, str, dict[str, Any]]]] = {}
    for path, ops in spec["paths"].items():
        if not path.startswith(prefix_filter) or path.startswith(exclude):
            continue
        for method, op in ops.items():
            tag = (op.get("tags") or ["other"])[0]
            by_tag.setdefault(tag, []).append((method.upper(), path, op))
    used: set[str] = set()
    for tag in sorted(by_tag):
        lines += [f"### {tag}", ""]
        for method, path, op in by_tag[tag]:
            route = routes.get((path, method))
            lines += [f"#### `{method} {path}`", ""]
            doc = (route.description if route else op.get("description", "")) or op.get("summary", "")
            if doc:
                lines += [" ".join(doc.split()), ""]
            caller = who(route) if route else "public"
            if caller == "public" and any(
                q.get("in") == "header" and q["name"] == "authorization" for q in op.get("parameters", [])
            ):
                caller = "the website session: header `Authorization: Bearer <session_token>`"
            lines += [f"**Who:** {caller}", ""]
            params = [
                p for p in op.get("parameters", []) if p.get("in") != "header" or p["name"] != "authorization"
            ]
            if params:
                lines += [
                    "**Parameters**",
                    "",
                    "| Name | In | Type | Required | Notes |",
                    "|---|---|---|---|---|",
                ]
                for p in params:
                    s = p.get("schema", {})
                    lines.append(
                        f"| `{p['name']}` | {p['in']} | {_type(s)} | {'yes' if p.get('required') else 'no'} | {_limits(s)} |"
                    )
                lines.append("")
            body = op.get("requestBody", {}).get("content", {})
            for ctype, media in body.items():
                ref = media.get("schema", {}).get("$ref")
                name = ref.rsplit("/", 1)[-1] if ref else None
                lines += [
                    f"**Request body** (`{ctype}`): "
                    + (f"`{name}`" if name else _type(media.get("schema", {}))),
                    "",
                ]
                if name:
                    used.add(name)
                    lines += fields_table(comps[name]) + [""]
                if ctype == "application/json":
                    lines += _json_block("Example request", media.get("schema", {}), comps)
            for code, resp in op.get("responses", {}).items():
                if code == "422" and "HTTPValidationError" in json.dumps(resp):
                    continue
                content = resp.get("content", {})
                first: dict[str, Any] = next(iter(content.values()), {}) if content else {}
                schema = first.get("schema", {})
                shown = _type(schema) if schema else "(no body)"
                lines.append(f"**Response {code}:** {shown}")
                for ref in _refs(schema):
                    used.add(ref)
                if schema and code.startswith("2") and "application/json" in content:
                    lines += [""] + _json_block(f"Example response {code}", schema, comps)
            lines += ["", "---", ""]
    # schemas appendix: everything referenced, transitively
    pending = list(used)
    while pending:
        name = pending.pop()
        for ref in _refs(comps.get(name, {})):
            if ref not in used:
                used.add(ref)
                pending.append(ref)
    lines += [f"## {title}: data shapes", ""]
    for name in sorted(used):
        if name in ("HTTPValidationError", "ValidationError"):
            continue
        schema = comps[name]
        lines += [f"### `{name}`", ""]
        if schema.get("description"):
            lines += [" ".join(schema["description"].split()), ""]
        if "enum" in schema:
            lines += ["One of: " + ", ".join(f"`{v}`" for v in schema["enum"]), ""]
        else:
            lines += fields_table(schema) + [""]
    return "\n".join(lines), spec


def _refs(schema: Any) -> list[str]:
    out: list[str] = []
    if isinstance(schema, dict):
        for k, v in schema.items():
            if k == "$ref" and isinstance(v, str):
                out.append(v.rsplit("/", 1)[-1])
            else:
                out += _refs(v)
    elif isinstance(schema, list):
        for v in schema:
            out += _refs(v)
    return out


_SAMPLE_UUID = "0195f0c2-7a3e-7c41-9b1d-2f4e6a8c0d12"


# Readable example strings for the documents (the tester pack writes "QA test" so a tester sees test data).
_DOC_STRINGS: dict[str, str] = {
    "text": "Do you have 8325 containers with lids? I need 20,000 a month in Dubai.",
    "reply": "Yes: 8325 is our 1-litre aluminium container with lid, packed 1 x 1000 pcs.",
    "session_token": "eyJhbGciOiJIUzI1NiJ9.eyJzaWQiOiIuLi4ifQ.signature",
    "token": "eyJhbGciOiJIUzI1NiJ9.eyJzdWIiOiIuLi4ifQ.signature",
    "name": "Aisha Khan",
    "company": "Gulf Kitchens LLC",
    "city": "Dubai",
    "title": "Aluminium Container & Lid",
    "slug": "about",
    "path": "/products",
    "url": "https://www.arifapack.com/products",
}


def _example(schema: dict[str, Any], comps: dict[str, Any]) -> Any:
    """A realistic example (the tester pack's generator), with Postman `{{variables}}` made concrete and the
    tester's placeholder text replaced by field-specific sample text."""

    def concrete(value: Any, key: str = "") -> Any:
        if isinstance(value, dict):
            return {k: concrete(v, k) for k, v in value.items()}
        if isinstance(value, list):
            return [concrete(v, key) for v in value]
        if isinstance(value, str) and value.startswith("{{") and value.endswith("}}"):
            return _SAMPLE_UUID
        if value == "QA test":
            return _DOC_STRINGS.get(key, f"<{key or 'text'}>")
        return value

    return concrete(example(schema, comps))


def _json_block(label: str, schema: dict[str, Any], comps: dict[str, Any]) -> list[str]:
    value = _example(schema, comps)
    if value in (None, {}, []):
        return []
    text = json.dumps(value, indent=2, ensure_ascii=False)
    if len(text) > 2500:  # very large shapes: the tables above say it all
        return []
    return [f"<details><summary>{label}</summary>", "", "```json", text, "```", "", "</details>", ""]


def _doc(head: str, body: str) -> str:
    return (HEADS / head).read_text(encoding="utf-8").rstrip() + "\n\n" + body + "\n"


def write_api_doc(
    out: pathlib.Path,
    *,
    app: FastAPI,
    title: str,
    head: str,
    md_name: str,
    openapi_name: str,
    prefixes: tuple[str, ...],
    exclude: tuple[str, ...] = (),
) -> int:
    """One `*_API.md` + its OpenAPI file; returns the number of paths documented."""
    md, spec = render(app, title, prefixes, exclude)
    (out / md_name).write_text(_doc(head, md), encoding="utf-8")
    (out / "openapi").mkdir(parents=True, exist_ok=True)
    (out / "openapi" / openapi_name).write_text(json.dumps(spec, indent=2) + "\n", encoding="utf-8")
    return len(spec["paths"])


def main(out: pathlib.Path = DEFAULT_OUT) -> None:
    out.mkdir(parents=True, exist_ok=True)
    crm = write_api_doc(
        out,
        app=crm_app(),
        title="CRM endpoint reference",
        head="crm_head.md",
        md_name="CRM_API.md",
        openapi_name="openapi-crm.json",
        prefixes=("/v1", "/.well-known"),
    )
    web = write_api_doc(
        out,
        app=website_app(),
        title="Website endpoint reference",
        head="web_head.md",
        md_name="WEBSITE_API.md",
        openapi_name="openapi-website.json",
        prefixes=("/v1/public",),
    )
    cms = write_api_doc(
        out,
        app=cms_app(),
        title="CMS endpoint reference",
        head="cms_head.md",
        md_name="CMS_API.md",
        openapi_name="openapi-cms.json",
        prefixes=("/v1/content", "/v1/cms"),
    )
    print("CRM_API.md:", crm, "paths; WEBSITE_API.md:", web, "paths; CMS_API.md:", cms, "paths")


if __name__ == "__main__":
    main(pathlib.Path(sys.argv[1]) if len(sys.argv) > 1 else DEFAULT_OUT)
