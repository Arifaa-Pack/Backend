"""OutboundService, the single way out for every message (opt-out, contact hours, rate limits,
kill switch, send grants), with its helpers.

The code is in `service.py` (OutboundService), `contact_hours.py`, `ratelimit.py` and `killswitch.py`;
import it from those modules (ADR-0001: no re-exports here).
"""
