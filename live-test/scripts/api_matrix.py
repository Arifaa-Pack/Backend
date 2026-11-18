"""API part 1: authentication + role matrix over every operation in the live OpenAPI (Postman-style).

For each operation: no token, a forged token (alg=none, wrong key, tampered claims), then each staff role.
Path ids are random UUIDs and bodies are {} so nothing real is changed (expected answers: 401 without auth,
403 when the role may not, 404/422 when the role passed the check and the input was rejected).
Skips the few calls with side effects on the caller itself (logout, TOTP enrol, password reset mails, AI
translate-all). Output: results/api_matrix.json
"""

from __future__ import annotations

import asyncio
import base64
import json
import sys
import time
import uuid
from pathlib import Path

import httpx

sys.path.insert(0, str(Path(__file__).parent))
import kxclient as k  # noqa: E402

OUT = Path(__file__).resolve().parents[1] / "results"
SKIP = {("POST", "/v1/auth/logout"), ("POST", "/v1/auth/totp/enrol"), ("POST", "/v1/auth/password/reset/start"),
        ("POST", "/v1/texts/translate-missing"), ("POST", "/v1/auth/password")}
PUBLIC = ("/.well-known/", "/v1/auth/login", "/v1/auth/mfa/", "/v1/auth/refresh", "/v1/auth/password/reset",
          "/v1/public/")


def b64(d: dict) -> str:
    return base64.urlsafe_b64encode(json.dumps(d).encode()).rstrip(b"=").decode()


def forged(real: str) -> dict[str, str]:
    h, p, s = real.split(".")
    claims = json.loads(base64.urlsafe_b64decode(p + "=" * (-len(p) % 4)))
    head = json.loads(base64.urlsafe_b64decode(h + "=" * (-len(h) % 4)))
    esc = dict(claims, role="md", roles=["md", "admin"])
    return {
        "alg_none": f"{b64(dict(head, alg='none'))}.{b64(esc)}.",
        "tampered_role": f"{h}.{b64(esc)}.{s}",
        "bad_signature": f"{h}.{p}.{s[:-4]}AAAA",
        "garbage": "not.a.jwt",
        "expired_shape": f"{h}.{b64(dict(claims, exp=int(time.time()) - 3600))}.{s}",
    }


async def call(c: httpx.AsyncClient, m: str, path: str, token: str | None) -> tuple[int, float]:
    h = {"Authorization": f"Bearer {token}"} if token else {}
    t0 = time.perf_counter()
    try:
        r = await c.request(m, path, headers=h, json={} if m in ("POST", "PUT", "PATCH") else None)
        return r.status_code, (time.perf_counter() - t0) * 1000
    except httpx.HTTPError:
        return 0, (time.perf_counter() - t0) * 1000


async def main() -> None:
    spec = json.loads((OUT / "openapi_live.json").read_text())
    ops = [(m.upper(), p) for p, o in spec["paths"].items() for m in o]
    async with httpx.AsyncClient(base_url=k.API, timeout=60) as c:
        tokens = {u: await k.login(c, u) for u in ("kxmd", "kxmgr", "kxsales1", "kxadmin")}
        fakes = forged(tokens["kxsales1"])
        rows = []
        for m, p in ops:
            if (m, p) in SKIP:
                continue
            path = p
            for seg in [s for s in p.split("/") if s.startswith("{")]:
                path = path.replace(seg, "entity" if seg == "{entity}" else ("x" if seg == "{key}" else str(uuid.uuid4())))
            row: dict = {"method": m, "path": p, "public": p.startswith(PUBLIC)}
            row["anon"], _ = await call(c, m, path, None)
            for name, tok in fakes.items():
                row[f"forged_{name}"], _ = await call(c, m, path, tok)
            for u, tok in tokens.items():
                row[u], row[f"{u}_ms"] = await call(c, m, path, tok)
            rows.append(row)
    leaks = [r for r in rows if not r["public"] and (r["anon"] not in (401, 403)
             or any(r[f"forged_{n}"] not in (401, 403) for n in fakes))]
    summary = {"operations": len(rows), "skipped": sorted(f"{m} {p}" for m, p in SKIP),
               "non_public_reachable_without_valid_token": [(r["method"], r["path"], r["anon"],
                    {n: r[f'forged_{n}'] for n in fakes}) for r in leaks],
               "server_errors_5xx": [(r["method"], r["path"], {u: r[u] for u in tokens}) for r in rows
                                     if any(500 <= r[u] < 600 for u in tokens) or r["anon"] >= 500]}
    (OUT / "api_matrix.json").write_text(json.dumps({"summary": summary, "rows": rows}, indent=1))
    print(json.dumps(summary, indent=1)[:3000])


if __name__ == "__main__":
    asyncio.run(main())
