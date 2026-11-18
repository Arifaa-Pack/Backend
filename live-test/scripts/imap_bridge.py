"""Test-only MX stand-in: kairoxaitech.com's MX is Hostinger, so a buyer's reply lands there, not in our local
Stalwart. This copies new messages FROM the given sender out of the Hostinger inbox (read-only: BODY.PEEK, flags
untouched) and delivers them to Stalwart over SMTP port 25, exactly as an MX hop would. From there the platform's
own Beat job (automation.mail_poll -> MailboxPoller) has to find, record and route the reply.
  python imap_bridge.py <sender> [minutes]      # polls every 20 s
"""

from __future__ import annotations

import imaplib
import json
import os
import smtplib
import sys
import time

sys.path.insert(0, os.path.dirname(__file__))
import envload  # noqa: E402

envload.load()
SENDER, MINUTES = sys.argv[1], float(sys.argv[2] if len(sys.argv) > 2 else 20)
STALWART = ("localhost", int(os.environ.get("STALWART_SMTP_PORT", "32525")))
RCPT = "hamthan@kairoxaitech.com"
done: set[bytes] = set()
log = []
end = time.time() + MINUTES * 60
since = time.strftime("%d-%b-%Y")
while time.time() < end:
    m = imaplib.IMAP4_SSL("imap.hostinger.com", 993, timeout=30)
    m.login(os.environ["KX_MAIL_USER"], os.environ["KX_MAIL_PASSWORD"])
    m.select("INBOX", readonly=True)
    _, data = m.uid("search", None, f'(FROM "{SENDER}" SINCE {since})')
    for uid in data[0].split():
        if uid in done:
            continue
        _, msg = m.uid("fetch", uid, "(BODY.PEEK[])")
        raw = msg[0][1]
        t0 = time.perf_counter()
        with smtplib.SMTP(*STALWART, timeout=30) as s:
            s.sendmail(SENDER, [RCPT], raw)
        done.add(uid)
        entry = {"uid": uid.decode(), "bytes": len(raw), "smtp_s": round(time.perf_counter() - t0, 3),
                 "at": time.strftime("%H:%M:%S")}
        log.append(entry)
        print(json.dumps(entry), flush=True)
    m.logout()
    time.sleep(20)
print(json.dumps({"bridged": len(log)}))
