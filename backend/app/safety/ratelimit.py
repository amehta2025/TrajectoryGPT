"""Per-IP rate limits for the endpoints that start AI work (SPEC §12): sliding one-hour windows, in memory.

Client IP: on Modal the ASGI peer address (request.client.host) is the real client IP. Measured on
2026-10-31 with a header-echo app: Modal strips a client-sent X-Forwarded-For and passes X-Real-IP through
untouched (spoofable), so no forwarding header is trusted by default. TRUSTED_PROXY_HOPS=N (only behind
N proxies we control that each append to X-Forwarded-For) takes the N-th address from the right instead.
IPs are kept only in memory and never logged.
"""
from __future__ import annotations

import os
import threading
import time
from collections import defaultdict, deque

WINDOW_S = 3600.0
DEFAULT_LIMITS = {"plan": 10, "run": 10, "sandbox": 3, "voice": 30, "replay": 60}  # per IP per hour


class RateLimited(Exception):
    def __init__(self, message: str, retry_after_s: int):
        super().__init__(message)
        self.retry_after_s = retry_after_s


def limit_for(kind: str) -> int:
    try:
        return int(os.environ.get(f"RATE_LIMIT_{kind.upper()}_PER_HOUR") or DEFAULT_LIMITS[kind])
    except ValueError:
        return DEFAULT_LIMITS[kind]


def client_ip(peer: str | None, forwarded_for: str | None) -> str:
    """The client's IP: the socket peer, or with TRUSTED_PROXY_HOPS=N the N-th X-Forwarded-For entry from the right."""
    try:
        hops = int(os.environ.get("TRUSTED_PROXY_HOPS") or 0)
    except ValueError:
        hops = 0
    if hops > 0 and forwarded_for:
        parts = [p.strip() for p in forwarded_for.split(",") if p.strip()]
        if len(parts) >= hops:
            return parts[-hops]
    return peer or "unknown"


class RateLimiter:
    def __init__(self, clock=time.monotonic):
        self.clock = clock
        self.hits: dict[tuple[str, str], deque] = defaultdict(deque)
        self.lock = threading.Lock()

    def check(self, kind: str, ip: str) -> None:
        """Count one request; raise RateLimited when this IP is over the hourly limit for this kind."""
        now, limit = self.clock(), limit_for(kind)
        with self.lock:
            q = self.hits[(kind, ip)]
            while q and now - q[0] >= WINDOW_S:
                q.popleft()
            if len(q) >= limit:
                wait = int(WINDOW_S - (now - q[0])) + 1
                raise RateLimited(f"Limit reached: {limit} per hour from one address. Try again in about "
                                  f"{max(1, round(wait / 60))} min. Charts and the 3D viewer still work.", wait)
            q.append(now)
            if len(self.hits) > 50_000:  # forget idle addresses
                for k in [k for k, v in self.hits.items() if not v or now - v[-1] >= WINDOW_S]:
                    del self.hits[k]


LIMITER = RateLimiter()
