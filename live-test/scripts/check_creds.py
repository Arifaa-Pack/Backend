import os, sys, ssl, smtplib, imaplib, json, time, httpx
sys.path.insert(0, os.path.dirname(__file__)); import envload; envload.load()
out = {}
with httpx.Client(timeout=30) as h:
    r = h.get("https://api.groq.com/openai/v1/models", headers={"Authorization": f"Bearer {os.environ['GROQ_API_KEY']}"})
    out["groq"] = (r.status_code, sorted(m["id"] for m in r.json().get("data", [])) if r.is_success else r.text[:200])
    r = h.get("https://generativelanguage.googleapis.com/v1beta/models?pageSize=200", headers={"x-goog-api-key": os.environ["GEMINI_API_KEY"]})
    out["gemini"] = (r.status_code, [m["name"] for m in r.json().get("models", []) if "generateContent" in m.get("supportedGenerationMethods", [])] if r.is_success else r.text[:200])
u, p = os.environ["KX_MAIL_USER"], os.environ["KX_MAIL_PASSWORD"]
for name, pw in (("as-given", p), ("no-dot", p.rstrip("."))):
    try:
        s = smtplib.SMTP_SSL("smtp.hostinger.com", 465, context=ssl.create_default_context(), timeout=20); s.login(u, pw); s.quit(); out[f"smtp_{name}"] = "OK"
    except Exception as e: out[f"smtp_{name}"] = f"FAIL {type(e).__name__}: {str(e)[:120]}"
    try:
        m = imaplib.IMAP4_SSL("imap.hostinger.com", 993, timeout=20); m.login(u, pw); typ, d = m.select("INBOX"); out[f"imap_{name}"] = f"OK inbox={d[0].decode()}"; m.logout()
    except Exception as e: out[f"imap_{name}"] = f"FAIL {type(e).__name__}: {str(e)[:120]}"
print(json.dumps(out, indent=1))
