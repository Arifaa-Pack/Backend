"""Set up the live-test Stalwart v1.1.1 over its JMAP admin API (x: objects), the same steps as the web wizard
(infra/server/stacks/compose.mail.yml header), but relaying through Hostinger instead of SES because the domain's
MX/SPF are at Hostinger:
  * user account `hamthan` (hamthan@kairoxaitech.com), password = KX_MAIL_PASSWORD -> the app's JMAP login
  * Relay route `hostinger`: smtp.hostinger.com:465, implicit TLS, SMTP AUTH as Hamthan@kairoxaitech.com
  * outbound strategy: local domain -> 'local', everything else -> 'hostinger'
Admin credential: argv[1] (user:password). Secrets never printed.
"""

from __future__ import annotations

import json
import os
import sys

import httpx

sys.path.insert(0, os.path.dirname(__file__))
import envload  # noqa: E402

envload.load()
URL = os.environ.get("STALWART_URL", "http://localhost:38090/jmap")
USER, PWD = sys.argv[1].split(":", 1)
USING = ["urn:ietf:params:jmap:core", "urn:stalwart:jmap"]


def call(*calls: list) -> list:
    r = httpx.post(URL, auth=(USER, PWD), json={"using": USING, "methodCalls": list(calls)}, timeout=30)
    r.raise_for_status()
    return r.json()["methodResponses"]


def show(resp: list) -> None:
    txt = json.dumps(resp)
    for secret in (os.environ["KX_MAIL_PASSWORD"],):
        txt = txt.replace(secret, "***")
    print(txt[:1500])


domain_id = call(["x:Domain/get", {"ids": None, "properties": ["name"]}, "a"])[0][1]["list"][0]["id"]
mail_pw = os.environ["KX_MAIL_PASSWORD"]
show(call(["x:Account/set", {"create": {"u1": {
    "@type": "User", "name": "hamthan", "domainId": domain_id,
    "credentials": {"0": {"@type": "Password", "secret": mail_pw}},
}}}, "b"]))
routes = [r["name"] for r in call(["x:MtaRoute/get", {"ids": None}, "r"])[0][1]["list"]]
if "hostinger" not in routes:
  show(call(["x:MtaRoute/set", {"create": {"r1": {
    "@type": "Relay", "name": "hostinger", "description": "Hostinger SMTP (kairoxaitech.com SPF)",
    "host": "smtp.hostinger.com", "port": 465, "protocol": "smtp", "implicitTls": True, "allowInvalidCerts": False,
    "authUsername": os.environ["KX_MAIL_USER"], "authSecret": {"@type": "Value", "secret": mail_pw},
}}}, "c"]))
strategy = call(["x:MtaOutboundStrategy/get", {"ids": None}, "d"])[0][1]["list"]
route = {"match": {"0": {"if": "is_local_domain(rcpt_domain)", "then": "'local'"}}, "else": "'hostinger'"}
if strategy:
    show(call(["x:MtaOutboundStrategy/set", {"update": {strategy[0]["id"]: {"route": route}}}, "e"]))
else:
    show(call(["x:MtaOutboundStrategy/set", {"update": {"singleton": {"route": route}}}, "e"]))
show(call(["x:MtaOutboundStrategy/get", {"ids": None}, "f"], ["x:Account/get", {"ids": None, "properties": ["name"]}, "g"]))
