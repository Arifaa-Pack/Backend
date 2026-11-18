"""The client IP behind Cloudflare and the Coolify proxy, for per-IP rate limits (D53 website, M3 staff
sign-in). Moved from `kx.agents.website` so the CRM uses the same rule."""

from __future__ import annotations

import ipaddress
from typing import Final

from fastapi import Request

# https://www.cloudflare.com/ips-v4 and /ips-v6 (fetched 18 Nov 2026); update when Cloudflare changes them
CLOUDFLARE_NETWORKS: Final = tuple(
    ipaddress.ip_network(n)
    for n in (
        "173.245.48.0/20", "103.21.244.0/22", "103.22.200.0/22", "103.31.4.0/22", "141.101.64.0/18",
        "108.162.192.0/18", "190.93.240.0/20", "188.114.96.0/20", "197.234.240.0/22", "198.41.128.0/17",
        "162.158.0.0/15", "104.16.0.0/13", "104.24.0.0/14", "172.64.0.0/13", "131.0.72.0/22",
        "2400:cb00::/32", "2606:4700::/32", "2803:f800::/32", "2405:b500::/32", "2405:8100::/32",
        "2a06:98c0::/29", "2c0f:f248::/32",
    )
)  # fmt: skip


def _from_cloudflare(address: str) -> bool:
    try:
        ip = ipaddress.ip_address(address)
    except ValueError:
        return False
    return any(ip in net for net in CLOUDFLARE_NETWORKS if net.version == ip.version)


def client_ip(request: Request, trusted_header: str | None = None) -> str:
    """The client's address for per-IP limits (D53; M3 staff sign-in). The hop our own proxy saw is the LAST
    X-Forwarded-For entry (earlier entries are whatever the client sent), else the socket peer. Behind
    Cloudflare the visitor is in `CF-Connecting-IP` (`KX_TRUSTED_CLIENT_IP_HEADER`), but only when that hop
    is a Cloudflare address: anyone reaching the origin directly could send the header (infra H7, live test
    18 Nov) and slip past the per-IP limits."""
    forwarded = [
        part.strip() for part in request.headers.get("x-forwarded-for", "").split(",") if part.strip()
    ]
    hop = forwarded[-1] if forwarded else (request.client.host if request.client else "unknown")
    value = request.headers.get(trusted_header, "").strip() if trusted_header else ""
    return value if value and _from_cloudflare(hop) else hop
