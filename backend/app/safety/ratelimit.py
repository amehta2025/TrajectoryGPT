"""Rate limits per browser session and per IP for the endpoints that start AI work (SPEC §12): sliding
windows, in memory.

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

HOUR = 3600.0
# kind -> {"session": (n, window_s), "ip": (n, window_s)}. Limits are per browser session (the tab's random
# X-Session-Id), so judges sharing one network don't use up each other's quota (red-team Round 2); the per-IP
# cap is the backstop against a script that invents session ids. "figure": figure uploads (the costliest plan).
# "passcode_fail": wrong passcodes; a session waits 10 min after 5, a whole network after 50 in an hour.
DEFAULT_LIMITS = {
    "plan": {"session": (10, HOUR), "ip": (40, HOUR)},
    "figure": {"session": (3, HOUR), "ip": (15, HOUR)},
    "run": {"session": (10, HOUR), "ip": (40, HOUR)},
    "sandbox": {"session": (3, HOUR), "ip": (10, HOUR)},
    "voice": {"session": (30, HOUR), "ip": (120, HOUR)},
    "replay": {"session": (60, HOUR), "ip": (240, HOUR)},
    "passcode_fail": {"session": (5, 600.0), "ip": (50, HOUR)},
}


class RateLimited(Exception):
    def __init__(self, message: str, retry_after_s: int):
        super().__init__(message)
        self.retry_after_s = retry_after_s


def limits_for(kind: str) -> dict[str, tuple[int, float]]:
    """RATE_LIMIT_<KIND>_PER_HOUR overrides the per-IP count, RATE_LIMIT_<KIND>_PER_SESSION the per-session one."""
    out = dict(DEFAULT_LIMITS[kind])
    for scope, suffix in (("ip", "PER_HOUR"), ("session", "PER_SESSION")):
        try:
            out[scope] = (int(os.environ[f"RATE_LIMIT_{kind.upper()}_{suffix}"]), out[scope][1])
        except (KeyError, ValueError):
            pass
    return out


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
        self.hits: dict[tuple, deque] = defaultdict(deque)
        self.lock = threading.Lock()

    def _full(self, kind: str, ip: str, session: str | None, now: float) -> tuple[list[deque], tuple[str, int, int] | None]:
        """The queues to count in, and (scope, limit, seconds to wait) for the first one at its limit."""
        queues, full = [], None
        lims = limits_for(kind)
        for scope, key in (("session", (kind, ip, session)), ("ip", (kind, ip))):
            if scope == "session" and not session:
                continue  # no session id (not our frontend): only the per-IP cap applies
            limit, window = lims[scope]
            q = self.hits[key]
            while q and now - q[0] >= window:
                q.popleft()
            queues.append(q)
            if full is None and len(q) >= limit:
                full = (scope, limit, int(window - (now - q[0])) + 1 if q else int(window))
        return queues, full

    def blocked(self, kind: str, ip: str, session: str | None = None) -> int | None:
        """Seconds to wait if this session or IP is at the limit for this kind, without counting a request."""
        with self.lock:
            return (self._full(kind, ip, session, self.clock())[1] or (None, None, None))[2]

    def check(self, kind: str, ip: str, session: str | None = None) -> None:
        """Count one request; raise RateLimited when this session or this IP is over its limit for this kind."""
        now = self.clock()
        with self.lock:
            queues, full = self._full(kind, ip, session, now)
            if full:
                scope, limit, wait = full
                who = "this browser" if scope == "session" else "one network"
                per = "hour" if limits_for(kind)[scope][1] >= HOUR else f"{limits_for(kind)[scope][1] / 60:.0f} min"
                raise RateLimited(f"Limit reached: {limit} per {per} from {who}. Try again in about "
                                  f"{max(1, round(wait / 60))} min. Charts and the 3D viewer still work.", wait)
            for q in queues:
                q.append(now)
            if len(self.hits) > 50_000:  # forget idle sessions and addresses
                for k in [k for k, v in self.hits.items() if not v or now - v[-1] >= HOUR]:
                    del self.hits[k]


LIMITER = RateLimiter()
