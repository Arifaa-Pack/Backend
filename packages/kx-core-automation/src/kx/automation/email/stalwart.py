"""D44: email through the self-hosted Stalwart mail server, over JMAP (RFC 8620 core, RFC 8621 mail).

* **Send:** the MIME message (same builder as SES, `build_mime`) is uploaded as a blob, imported into the
  account's Sent mailbox and submitted with `EmailSubmission/set`. Stalwart's outbound queue relays it through
  Amazon SES SMTP (configured in Stalwart, so quotes keep SES's inbox reputation). The id we keep is the
  message's own `Message-ID` header: bounce reports quote it back.
* **Receive:** the `automation.mail_poll` Beat job asks for inbox messages without our `$kxdone` keyword,
  downloads each raw message, hands it to the caller and then sets the keyword. No cursor table: the keyword
  is the cursor, and `first_receipt` dedupes a message seen twice (a crash between record and keyword).

Only standard JMAP methods are used (tested on Stalwart v0.14 and v1.1.1; the image is pinned, OPEN #71).
v1 caps the stored submission records per account (`maxSubmissions`, 500); `StalwartSender` clears finished
ones itself.
"""

from __future__ import annotations

from collections.abc import Awaitable, Callable
from dataclasses import dataclass
from typing import Any, Final
from urllib.parse import urlsplit, urlunsplit

import httpx

from kx.automation._shared.channels import ChannelError
from kx.automation._shared.settings import SesSettings, StalwartSettings
from kx.automation.email.channel import Attachment, build_mime
from kx.core.identity import sha256_hex
from kx.core.logging import get_logger

log = get_logger(__name__)

PROVIDER: Final = "stalwart"
DONE_KEYWORD: Final = "$kxdone"
USING: Final = ["urn:ietf:params:jmap:core", "urn:ietf:params:jmap:mail", "urn:ietf:params:jmap:submission"]
SUBMISSION_PRUNE_BATCH: Final = 400  # below the default 500 cap, so one prune always frees room


@dataclass(frozen=True, slots=True)
class _Session:
    api_url: str
    upload_url: str
    download_url: str
    account_id: str


class JmapClient:
    """Thin JMAP client (Basic auth). The session (URLs, account id) and the mailbox / identity ids are looked
    up once and cached for the process."""

    def __init__(self, http: httpx.AsyncClient, settings: StalwartSettings) -> None:
        self._http = http
        self._settings = settings
        self._auth = httpx.BasicAuth(settings.username, settings.password.get_secret_value())
        self._session: _Session | None = None
        self._mailboxes: dict[str, str] = {}

    async def session(self) -> _Session:
        if self._session is None:
            # RFC 8620 §2.2: the well-known URL may redirect; Stalwart answers 307 -> /jmap/session, and the
            # shared client does not follow redirects by default (same origin, so the Basic auth is kept)
            r = await self._request(
                "GET", self._settings.url.rstrip("/") + "/.well-known/jmap", follow_redirects=True
            )
            data = r.json()
            account = data["primaryAccounts"]["urn:ietf:params:jmap:mail"]
            self._session = _Session(
                api_url=self._absolute(data["apiUrl"]),
                upload_url=self._absolute(data["uploadUrl"]),
                download_url=self._absolute(data["downloadUrl"]),
                account_id=account,
            )
        return self._session

    def _absolute(self, url: str) -> str:
        """Stalwart may advertise its public hostname (or its container id); we always talk to the internal
        URL we were given. Plain string surgery: the upload / download URLs are RFC 6570 templates, and
        httpx.URL would percent-encode their {accountId} / {blobId} placeholders (then never filled in)."""
        if url.startswith("/"):
            return self._settings.url.rstrip("/") + url
        public, base = urlsplit(url), urlsplit(self._settings.url)
        return urlunsplit((base.scheme, base.netloc, public.path, public.query, public.fragment))

    async def _request(self, method: str, url: str, **kwargs: Any) -> httpx.Response:
        try:
            r = await self._http.request(
                method, url, auth=self._auth, timeout=self._settings.timeout_seconds, **kwargs
            )
        except httpx.HTTPError as exc:
            raise ChannelError(PROVIDER, type(exc).__name__, retryable=True) from exc
        if r.status_code >= 400:
            raise ChannelError(PROVIDER, f"http_{r.status_code}", retryable=r.status_code >= 500)
        return r

    async def call(self, *calls: tuple[str, dict[str, Any]]) -> list[dict[str, Any]]:
        """One JMAP request; returns the argument objects in order. A method error is a ChannelError."""
        session = await self.session()
        body = {
            "using": USING,
            "methodCalls": [
                [name, {"accountId": session.account_id, **args}, f"c{i}"]
                for i, (name, args) in enumerate(calls)
            ],
        }
        r = await self._request("POST", session.api_url, json=body)
        out: list[dict[str, Any]] = []
        for name, args, _ in r.json()["methodResponses"]:
            if name == "error":
                raise ChannelError(PROVIDER, str(args.get("type", "jmap_error")), retryable=False)
            out.append(args)
        return out

    async def upload(self, data: bytes, content_type: str) -> str:
        session = await self.session()
        url = session.upload_url.replace("{accountId}", session.account_id)
        r = await self._request("POST", url, content=data, headers={"Content-Type": content_type})
        return str(r.json()["blobId"])

    async def download(self, blob_id: str, name: str = "message.eml") -> bytes:
        session = await self.session()
        url = (
            session.download_url.replace("{accountId}", session.account_id)
            .replace("{blobId}", blob_id)
            .replace("{name}", name)
            .replace("{type}", "message/rfc822")
        )
        return (await self._request("GET", url)).content

    async def mailbox(self, role: str) -> str:
        """The id of the mailbox with this role ("inbox", "sent")."""
        if role not in self._mailboxes:
            [res] = await self.call(("Mailbox/get", {"ids": None, "properties": ["id", "role"]}))
            for box in res["list"]:
                if box.get("role"):
                    self._mailboxes[str(box["role"])] = str(box["id"])
        if role not in self._mailboxes:
            raise ChannelError(PROVIDER, f"no_{role}_mailbox", retryable=False)
        return self._mailboxes[role]


class StalwartSender:
    """`EmailVendor` over JMAP. Returns the `Message-ID` (without <>), the id bounce reports refer to."""

    def __init__(self, client: JmapClient, ses: SesSettings) -> None:
        self._jmap = client
        self._from = ses.from_address
        self._identity: str | None = None

    async def _identity_id(self) -> str:
        if self._identity is None:
            [res] = await self._jmap.call(("Identity/get", {"ids": None}))
            ids = [i for i in res["list"] if str(i.get("email", "")).lower() == self._from.lower()]
            if not ids:
                raise ChannelError(PROVIDER, "no_identity_for_from_address", retryable=False)
            self._identity = str(ids[0]["id"])
        return self._identity

    async def send(
        self,
        *,
        to_address: str,
        subject: str,
        body: str,
        attachment: Attachment | None = None,
        in_reply_to: str | None = None,
        list_unsubscribe: str | None = None,
        from_name: str | None = None,
    ) -> str:
        msg = build_mime(
            from_address=self._from,
            to_address=to_address,
            subject=subject,
            body=body,
            attachment=attachment,
            in_reply_to=in_reply_to,
            list_unsubscribe=list_unsubscribe,
            from_name=from_name,
        )
        blob = await self._jmap.upload(msg.as_bytes(), "message/rfc822")
        sent = await self._jmap.mailbox("sent")
        identity = await self._identity_id()
        imported, submitted = await self._jmap.call(
            (
                "Email/import",
                {"emails": {"m": {"blobId": blob, "mailboxIds": {sent: True}, "keywords": {"$seen": True}}}},
            ),
            ("EmailSubmission/set", {"create": {"s": self._submission(identity, "#m", to_address)}}),
        )
        if "m" not in (imported.get("created") or {}):
            raise ChannelError(PROVIDER, _first_error(imported.get("notCreated")), retryable=False)
        error = None if "s" in (submitted.get("created") or {}) else _first_error(submitted.get("notCreated"))
        if error == "overQuota" and await self.prune_submissions():
            # the Email is already in Sent: submit that one again, never a second copy
            email_id = str(imported["created"]["m"]["id"])
            [submitted] = await self._jmap.call(
                ("EmailSubmission/set", {"create": {"s": self._submission(identity, email_id, to_address)}})
            )
            error = (
                None if "s" in (submitted.get("created") or {}) else _first_error(submitted.get("notCreated"))
            )
        if error is not None:
            raise ChannelError(PROVIDER, error, retryable=True)
        return str(msg["Message-ID"]).strip().strip("<>")

    def _submission(self, identity: str, email_id: str, to_address: str) -> dict[str, Any]:
        return {
            "identityId": identity,
            "emailId": email_id,
            "envelope": {"mailFrom": {"email": self._from}, "rcptTo": [{"email": to_address}]},
        }

    async def prune_submissions(self) -> int:
        """Stalwart v1 keeps a record of every JMAP submission and refuses new ones once the account holds
        `maxSubmissions` (500 by default): after 500 quotes every send failed with `overQuota` (measured 17
        Nov on v1.1.1). We never read them back, so the finished ones (`undoStatus` final; the queued delivery
        is not affected) are deleted. Returns how many."""
        [found] = await self._jmap.call(
            ("EmailSubmission/query", {"filter": {"undoStatus": "final"}, "limit": SUBMISSION_PRUNE_BATCH})
        )
        ids = [str(i) for i in found.get("ids") or ()]
        if not ids:
            return 0
        [done] = await self._jmap.call(("EmailSubmission/set", {"destroy": ids}))
        pruned = len(done.get("destroyed") or ())
        log.info("stalwart.submissions_pruned", count=pruned)
        return pruned


def _first_error(not_created: dict[str, Any] | None) -> str:
    for value in (not_created or {}).values():
        return str(value.get("type", "not_created"))
    return "not_created"


Handler = Callable[[str, bytes], Awaitable[None]]  # (jmap email id, raw RFC 5322 bytes)


class MailboxPoller:
    """D44 receive side: inbox messages we have not handled yet, oldest first, `batch` per run."""

    def __init__(self, client: JmapClient, batch: int = 25) -> None:
        self._jmap = client
        self._batch = batch

    async def poll(self, handle: Handler) -> int:
        inbox = await self._jmap.mailbox("inbox")
        _, got = await self._jmap.call(
            (
                "Email/query",
                {
                    "filter": {"inMailbox": inbox, "notKeyword": DONE_KEYWORD},
                    "sort": [{"property": "receivedAt", "isAscending": True}],
                    "limit": self._batch,
                },
            ),
            (
                "Email/get",
                {
                    "#ids": {"resultOf": "c0", "name": "Email/query", "path": "/ids"},
                    "properties": ["id", "blobId"],
                },
            ),
        )
        handled = 0
        for email in got.get("list", []):
            raw = await self._jmap.download(str(email["blobId"]))
            await handle(str(email["id"]), raw)  # records the message (idempotent) before we mark it
            await self._jmap.call(
                ("Email/set", {"update": {email["id"]: {f"keywords/{DONE_KEYWORD}": True}}})
            )
            handled += 1
        if handled:
            log.info("mail_poll.handled", count=handled)
        return handled


def raw_key(raw: bytes) -> str:
    """Object key for the stored raw message (R2 `inbound-mail/`), content-addressed so retries overwrite."""
    return f"inbound-mail/{sha256_hex(raw)[:32]}.eml"
