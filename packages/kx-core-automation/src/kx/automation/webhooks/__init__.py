"""Provider webhooks (WhatsApp, Telnyx): verify the request, record it in one transaction, queue
the slow work.

The code is in `router.py`; import it from there (ADR-0001: no re-exports here).
"""
