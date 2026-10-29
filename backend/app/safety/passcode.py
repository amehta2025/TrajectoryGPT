"""DEMO_PASSCODE gate for the AI endpoints (SPEC §12). Charts and 3D stay public.

The frontend sends the passcode in the X-Demo-Passcode header. When DEMO_PASSCODE is unset the gate is
open (local development), unless REQUIRE_PASSCODE=1 (set on every deploy), which fails closed.
"""
import hmac
import os

HEADER = "X-Demo-Passcode"


def passcode_ok(given: str | None) -> bool:
    expected = os.environ.get("DEMO_PASSCODE", "")
    if not expected:
        return os.environ.get("REQUIRE_PASSCODE") != "1"
    return bool(given) and hmac.compare_digest(given.encode(), expected.encode())
