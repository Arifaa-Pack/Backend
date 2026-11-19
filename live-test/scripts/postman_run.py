"""Round-2 re-test: run the tester's Postman collection (docs/handoff/postman) with newman against the live stack.

Folder 01 (sign-in) needs a person with an authenticator app, so the harness signs in its own staff accounts
(kxclient: real MFA) and puts their tokens in a run-only copy of the local environment, with the stack's TEST
webhook secrets read from the API container. That copy lives in the scratchpad, never in the repo. The other
folders run in order (00, 02-14), as a tester would. Only a summary (counts, failing assertions; no tokens or
bodies) is written to results/postman_run.json.

Usage: python postman_run.py <collection.json> <local environment.json> <newman executable>
"""

from __future__ import annotations

import asyncio
import json
import subprocess
import sys
from pathlib import Path

import httpx

sys.path.insert(0, str(Path(__file__).resolve().parent))
import kxclient as k  # noqa: E402

RES = Path(__file__).resolve().parents[1] / "results"
FOLDERS = ["00 Health and keys", "02 Users and admin settings", "03 Contacts, companies and deals",
           "04 Products, stock and price list", "05 Quotes, approvals and change requests",
           "06 Payment, won and orders", "07 Dashboards and exports", "08 Channels admin",
           "09 Buyer simulation - WhatsApp", "10 Buyer simulation - Website chat", "11 CMS publish webhook",
           "12 Unsubscribe link", "13 Telnyx call webhooks", "14 Negative and security tests"]


def container_env(name: str) -> str:
    return subprocess.run(["docker", "exec", "kxlive-api-1", "printenv", name], capture_output=True, text=True,
                          stdin=subprocess.DEVNULL, timeout=30).stdout.strip()


async def tokens() -> dict[str, str]:
    async with httpx.AsyncClient(base_url=k.API, timeout=60) as c:
        out = {}
        for var, user in (("token_manager", "kxmgr"), ("token_sales", "kxsales1"), ("token_md", "kxmd"),
                          ("token_admin", "kxadmin")):
            out[var] = await k.login(c, user)
        out["access_token"] = out["token_manager"]
        return out


def main() -> None:
    collection, env_file, newman = sys.argv[1:4]
    env = json.loads(Path(env_file).read_text(encoding="utf-8"))
    values = {v["key"]: v for v in env["values"]}
    fill = asyncio.run(tokens())
    fill["cms_webhook_secret"] = container_env("KX_APP_CMS_WEBHOOK_SECRET")
    fill["telnyx_callback_secret"] = container_env("KX_AUTO_TELNYX__CALLBACK_SECRET")
    fill["wa_verify_token"] = container_env("KX_AUTO_WHATSAPP__VERIFY_TOKEN")
    for key, value in fill.items():
        values.setdefault(key, {"key": key, "type": "secret", "enabled": True})["value"] = value
    env["values"] = list(values.values())
    run_env = k.SP / "postman_run_env.json"  # scratchpad only: it holds tokens and test secrets
    run_env.write_text(json.dumps(env), encoding="utf-8")
    report = k.SP / "postman_run_report.json"
    cmd = [newman, "run", collection, "-e", str(run_env), "--reporters", "cli,json", "--reporter-json-export",
           str(report), "--delay-request", "150", "--timeout-request", "60000", "--color", "off"]
    for folder in FOLDERS:
        cmd += ["--folder", folder]
    done = subprocess.run(cmd, capture_output=True, text=True, encoding="utf-8", errors="replace",
                          stdin=subprocess.DEVNULL, timeout=3600, shell=newman.endswith(".cmd"))
    (k.SP / "postman_run_cli.txt").write_text(done.stdout + done.stderr, encoding="utf-8")
    run = json.loads(report.read_text(encoding="utf-8"))["run"]
    failures = [{"request": f.get("source", {}).get("name"), "assertion": f.get("error", {}).get("test"),
                 "message": (f.get("error", {}).get("message") or "")[:200]} for f in run.get("failures", [])]
    summary = {"stats": run["stats"], "failures": failures, "folders": FOLDERS}
    (RES / "postman_run.json").write_text(json.dumps(summary, indent=1, ensure_ascii=False), encoding="utf-8")
    print(json.dumps(run["stats"]))
    for f in failures:
        print("FAIL", f["request"], "|", f["assertion"], "|", f["message"][:120])


main()
