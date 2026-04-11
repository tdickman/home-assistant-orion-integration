#!/usr/bin/env python3
"""
Orion Sleep mattress topper — login and info retrieval.

Usage:
    python orion_info.py --email you@example.com
    python orion_info.py --phone 15132015808

Tokens are cached to ~/.orion_tokens.json.  On subsequent runs the script
reuses the cached access token (or refreshes it automatically) so you don't
have to log in again.  Pass --relogin to force a fresh login.
"""

import argparse
import asyncio
import json
import os
import signal
import ssl
import sys
import time
from datetime import date, timedelta
from pathlib import Path
from typing import Any

import requests

BASE_URL = "https://api1.orionbed.com"
TOKEN_FILE = Path.home() / ".orion_tokens.json"


# ── helpers ────────────────────────────────────────────────────────────────────


def _url(path: str) -> str:
    return f"{BASE_URL}{path}"


def _headers(token: str | None = None) -> dict:
    h = {"Content-Type": "application/json"}
    if token:
        h["Authorization"] = f"Bearer {token}"
    return h


def _pretty(label: str, data) -> None:
    print(f"\n{'=' * 60}")
    print(f"  {label}")
    print(f"{'=' * 60}")
    print(json.dumps(data, indent=2, default=str))


def _check(resp: requests.Response, context: str) -> Any:
    if not resp.ok:
        print(f"[ERROR] {context}: {resp.status_code} {resp.reason}")
        print(f"  URL: {resp.request.method} {resp.url}")
        print(f"  Request body: {resp.request.body}")
        try:
            print(json.dumps(resp.json(), indent=2))
        except Exception:
            print(f"  Response: {resp.text}")
        return None
    try:
        return resp.json()
    except Exception:
        return {}


# ── token persistence ──────────────────────────────────────────────────────────


def _save_tokens(session: dict) -> None:
    """Persist the session dict (access_token, refresh_token, expires_at)."""
    TOKEN_FILE.write_text(json.dumps(session, indent=2))
    os.chmod(TOKEN_FILE, 0o600)


def _load_tokens() -> dict | None:
    if not TOKEN_FILE.exists():
        return None
    try:
        return json.loads(TOKEN_FILE.read_text())
    except (json.JSONDecodeError, OSError):
        return None


def _token_expired(session: dict, margin_seconds: int = 60) -> bool:
    """Return True if the cached session's expires_at is within margin of now."""
    expires_at = session.get("expires_at", 0)
    return time.time() + margin_seconds >= expires_at


def _delete_tokens() -> None:
    try:
        TOKEN_FILE.unlink(missing_ok=True)
    except OSError:
        pass


# ── auth ───────────────────────────────────────────────────────────────────────


def request_code(email: str | None = None, phone: str | None = None) -> bool:
    """POST /v1/auth/code — send a verification code."""
    body: dict = {}
    if email:
        body["email"] = email
    if phone:
        body["phone"] = phone
    resp = requests.post(_url("/v1/auth/code"), json=body, headers=_headers())
    data = _check(resp, "request_code")
    if data is None:
        return False
    print("Verification code sent.")
    return True


def verify_code(
    code: str,
    email: str | None = None,
    phone: str | None = None,
) -> dict | None:
    """POST /v1/auth/verify — returns the session dict or None on failure.

    Real response shape:
        {"response": {"session": {access_token, refresh_token, expires_at, ...},
                       "user": {...}},
         "success": true}
    """
    body: dict = {"code": code}
    if email:
        body["email"] = email
    if phone:
        body["phone"] = phone
    resp = requests.post(_url("/v1/auth/verify"), json=body, headers=_headers())
    data = _check(resp, "verify_code")
    if data is None:
        return None
    # Extract session from the nested response
    session = (data.get("response") or {}).get("session")
    if not session or "access_token" not in session:
        print(f"[ERROR] Unexpected verify response: {json.dumps(data, indent=2)}")
        return None
    return session


def refresh_tokens(refresh_token: str) -> dict | None:
    """POST /v1/auth/refresh — returns a new session dict or None."""
    resp = requests.post(
        _url("/v1/auth/refresh"),
        json={"refresh_token": refresh_token},
        headers=_headers(),
    )
    data = _check(resp, "refresh_tokens")
    if data is None:
        return None
    # Try same nested structure as verify; fall back to top-level
    session = (data.get("response") or {}).get("session", data)
    if "access_token" not in session:
        print(f"[ERROR] Unexpected refresh response: {json.dumps(data, indent=2)}")
        return None
    return session


def obtain_access_token(
    email: str | None = None,
    phone: str | None = None,
    force_login: bool = False,
) -> str:
    """Return a valid access token, using cache / refresh / fresh login."""

    # 1. Try cached tokens (unless --relogin)
    if not force_login:
        cached = _load_tokens()
        if cached:
            access = cached.get("access_token", "")

            if access and not _token_expired(cached):
                print("Using cached access token.")
                return access

            # Try refreshing
            refresh = cached.get("refresh_token", "")
            if refresh:
                print("Access token expired, refreshing...")
                new_session = refresh_tokens(refresh)
                if new_session:
                    _save_tokens(new_session)
                    print("Tokens refreshed.")
                    return new_session["access_token"]
                print("Refresh failed, falling back to fresh login.")

    # 2. Fresh login
    if not request_code(email=email, phone=phone):
        sys.exit(1)

    code = input("Enter the verification code: ").strip()
    session = verify_code(code, email=email, phone=phone)
    if not session:
        print("Authentication failed.")
        _delete_tokens()
        sys.exit(1)

    _save_tokens(session)
    print("Logged in successfully (tokens cached to ~/.orion_tokens.json).\n")
    return session["access_token"]


# ── data fetchers ──────────────────────────────────────────────────────────────


def get_current_user(token: str) -> Any:
    resp = requests.get(_url("/v1/auth/me"), headers=_headers(token))
    return _check(resp, "get_current_user")


def list_devices(token: str) -> Any:
    resp = requests.get(_url("/v1/devices"), headers=_headers(token))
    return _check(resp, "list_devices")


def get_session_state(token: str) -> Any:
    resp = requests.get(_url("/v1/session-state"), headers=_headers(token))
    return _check(resp, "get_session_state")


def get_sleep_schedules(token: str) -> Any:
    resp = requests.get(_url("/v1/sleep-schedules"), headers=_headers(token))
    return _check(resp, "get_sleep_schedules")


def get_sleep_config_devices(token: str) -> Any:
    """GET /v1/sleep-configurations/devices — sleep config + temp data."""
    resp = requests.get(
        _url("/v1/sleep-configurations/devices"), headers=_headers(token)
    )
    return _check(resp, "get_sleep_config_devices")


def get_insights(token: str, days: int = 7) -> Any:
    """GET /v2/insights for the last *days* days."""
    today = date.today()
    params = {
        "from": (today - timedelta(days=days)).isoformat(),
        "to": today.isoformat(),
    }
    resp = requests.get(_url("/v2/insights"), headers=_headers(token), params=params)
    return _check(resp, "get_insights")


# ── away mode ──────────────────────────────────────────────────────────────────


def set_user_away(token: str, user_id: str, is_away: bool = True) -> Any:
    """POST /v1/sleep-configurations/user-away — toggle away mode.

    Body requires: user_id (string), is_away (boolean).
    is_away=True turns the mattress off, is_away=False turns it on.
    """
    body: dict = {"user_id": user_id, "is_away": is_away}
    resp = requests.post(
        _url("/v1/sleep-configurations/user-away"),
        json=body,
        headers=_headers(token),
    )
    return _check(resp, "set_user_away")


def get_sleep_config_temperature(token: str) -> Any:
    """GET /v1/sleep-configurations/temperature — current temperature config."""
    resp = requests.get(
        _url("/v1/sleep-configurations/temperature"), headers=_headers(token)
    )
    return _check(resp, "get_sleep_config_temperature")


# ── websocket ──────────────────────────────────────────────────────────────

WS_BASE_URL = "wss://live.api1.orionbed.com"
WS_TOPIC = "live/zones/actionState"


def _ws_ssl_context() -> ssl.SSLContext:
    """Create an SSL context that forces HTTP/1.1 via ALPN.

    Cloudflare negotiates HTTP/2 by default, which prevents the
    WebSocket Upgrade handshake (RFC 6455 requires HTTP/1.1).
    """
    ctx = ssl.create_default_context()
    ctx.set_alpn_protocols(["http/1.1"])
    return ctx


async def _ws_connect_and_listen(
    token: str,
    device_ids: list[str],
    duration: float = 60.0,
) -> None:
    """Connect to the Orion live WebSocket, subscribe to devices, and log messages.

    Protocol (from openapi.yaml x-websocket):
      - URL: wss://live.api1.orionbed.com?token=<JWT>
      - After connect, send: {"action":"subscribe","topic":"live/zones/actionState","deviceId":"<id>"}
      - Server pushes JSON: {topic, event, data}
    """
    import websockets

    url = f"{WS_BASE_URL}?token={token}"
    print(f"\nConnecting to {WS_BASE_URL}?token=<JWT>...")
    print(f"  Devices to subscribe: {device_ids}")
    print(f"  Duration: {duration:.0f}s  (Ctrl+C to stop)\n")

    stop = asyncio.Event()
    loop = asyncio.get_running_loop()
    for sig in (signal.SIGINT, signal.SIGTERM):
        loop.add_signal_handler(sig, stop.set)

    try:
        async with websockets.connect(url, ssl=_ws_ssl_context()) as ws:
            print("[CONNECTED] WebSocket connection established.\n")

            # Subscribe to each device
            for device_id in device_ids:
                sub_msg = {
                    "action": "subscribe",
                    "topic": WS_TOPIC,
                    "deviceId": device_id,
                }
                await ws.send(json.dumps(sub_msg))
                print(f"[SUBSCRIBE] topic={WS_TOPIC}  deviceId={device_id}")

            print()

            # Listen for messages
            deadline = asyncio.get_event_loop().time() + duration
            count = 0
            while not stop.is_set():
                remaining = deadline - asyncio.get_event_loop().time()
                if remaining <= 0:
                    print(f"\n[DONE] Duration reached ({duration:.0f}s).")
                    break
                try:
                    async with asyncio.timeout(min(remaining, 1.0)):
                        msg = await ws.recv()
                        count += 1
                        ts = time.strftime("%H:%M:%S")
                        try:
                            data = json.loads(msg)
                            print(f"[{ts} #{count}] {json.dumps(data, indent=2)}")
                        except (json.JSONDecodeError, TypeError):
                            print(f"[{ts} #{count}] {msg!r}")
                except TimeoutError:
                    pass

            # Clean unsubscribe
            for device_id in device_ids:
                unsub_msg = {
                    "action": "unsubscribe",
                    "topic": WS_TOPIC,
                }
                try:
                    await ws.send(json.dumps(unsub_msg))
                except Exception:
                    pass

            print(f"\nTotal messages received: {count}")

            # Close with 1001 (going away), matching the app's behavior
            await ws.close(1001, "client shutdown")

    except websockets.exceptions.InvalidStatus as e:
        print(f"[ERROR] WebSocket upgrade rejected: HTTP {e.response.status_code}")
        if e.response.status_code == 200:
            print(
                "\n  The server returned HTTP 200 instead of 101 Switching Protocols."
                "\n  This means the Cloudflare proxy is not forwarding the WebSocket"
                "\n  upgrade to the backend. The Orion mobile app (React Native /"
                "\n  OkHttp) connects from a mobile network where Cloudflare may"
                "\n  route the traffic differently."
                "\n"
                "\n  Possible causes:"
                "\n    - Cloudflare WebSocket support is disabled for this route"
                "\n    - The backend is not listening for WebSocket upgrades at /"
                "\n    - A Cloudflare Worker or firewall rule blocks non-mobile clients"
                "\n"
                "\n  The server health check responds:"
            )
            if hasattr(e.response, "body") and e.response.body:
                print(f"    {e.response.body.decode('utf-8', errors='replace')}")
        elif e.response.status_code == 401:
            print("  Token may be expired or invalid. Try --relogin.")
    except Exception as e:
        print(f"[ERROR] WebSocket failed: {type(e).__name__}: {e}")


def run_websocket(
    token: str,
    device_ids: list[str],
    duration: float = 60.0,
) -> None:
    """Entry point for the --websocket flag."""
    if not device_ids:
        print("\n[ERROR] No device IDs found — cannot subscribe to WebSocket.")
        print("  Make sure you have at least one device on your account.")
        return

    asyncio.run(_ws_connect_and_listen(token, device_ids, duration))


# ── main ───────────────────────────────────────────────────────────────────────


def main() -> None:
    parser = argparse.ArgumentParser(
        description="Log in to Orion Sleep and print mattress topper info."
    )
    group = parser.add_mutually_exclusive_group(required=True)
    group.add_argument("--email", help="Email address for login")
    group.add_argument("--phone", help="Phone number for login")
    parser.add_argument(
        "--insights-days",
        type=int,
        default=7,
        help="Number of days of insights to fetch (default: 7)",
    )
    parser.add_argument(
        "--relogin",
        action="store_true",
        help="Force a fresh login, ignoring cached tokens",
    )
    parser.add_argument(
        "--set-away",
        action="store_true",
        help="Turn off the mattress (set user away) then show state",
    )
    parser.add_argument(
        "--set-present",
        action="store_true",
        help="Turn on the mattress (undo away) then show state",
    )
    parser.add_argument(
        "--websocket",
        action="store_true",
        help="Connect to the live WebSocket and log real-time device messages",
    )
    parser.add_argument(
        "--ws-duration",
        type=float,
        default=60.0,
        help="How long to listen on the WebSocket in seconds (default: 60)",
    )
    args = parser.parse_args()

    # Obtain a valid access token (cached / refreshed / fresh login)
    access_token = obtain_access_token(
        email=args.email,
        phone=args.phone,
        force_login=args.relogin,
    )

    # Fetch and display mattress topper information
    user = get_current_user(access_token)
    if user is not None:
        _pretty("User Profile", user)

    devices_data = list_devices(access_token)
    if devices_data is not None:
        _pretty("Devices", devices_data)

    # Extract device list for away mode actions
    device_list = []
    if devices_data:
        raw = devices_data
        if isinstance(raw, dict):
            raw = raw.get("response", raw)
            if isinstance(raw, dict):
                raw = raw.get("devices", [raw])
        if isinstance(raw, list):
            device_list = raw

    sleep_configs = get_sleep_config_devices(access_token)
    if sleep_configs is not None:
        _pretty("Sleep Configurations (devices)", sleep_configs)

    # Try to GET the temperature config
    temp_config = get_sleep_config_temperature(access_token)
    if temp_config is not None:
        _pretty("Sleep Configurations (temperature)", temp_config)

    session = get_session_state(access_token)
    if session is not None:
        _pretty("Current Session State", session)

    schedules = get_sleep_schedules(access_token)
    if schedules is not None:
        _pretty("Sleep Schedules", schedules)

    insights = get_insights(access_token, days=args.insights_days)
    if insights is not None:
        _pretty(f"Sleep Insights (last {args.insights_days} days)", insights)

    # ── Away mode actions ──────────────────────────────────────────────
    if args.set_away or args.set_present:
        # Get user_id from the user profile
        user_id = ""
        if user:
            resp_data = user.get("response", user)
            user_id = resp_data.get("id", "")

        if not user_id:
            print("\n[ERROR] No user_id found — cannot set away/present.")
        else:
            print(f"\nUsing user_id: {user_id}")

            if args.set_away:
                print("\n>>> Setting user AWAY (is_away=True, turning off)...")
                result = set_user_away(access_token, user_id, is_away=True)
                _pretty("set_user_away response", result)
            elif args.set_present:
                print("\n>>> Setting user PRESENT (is_away=False, turning on)...")
                result = set_user_away(access_token, user_id, is_away=False)
                _pretty("set_user_away (present) response", result)

            # Re-fetch state after the change to see what differs
            print("\n>>> Re-fetching state after change...")
            devices2 = list_devices(access_token)
            if devices2 is not None:
                _pretty("Devices (after)", devices2)
            schedules2 = get_sleep_schedules(access_token)
            if schedules2 is not None:
                _pretty("Sleep Schedules (after)", schedules2)

    # ── WebSocket ─────────────────────────────────────────────────────
    if args.websocket:
        # Extract device IDs from the already-fetched device list
        ws_device_ids = [d.get("id") for d in device_list if d.get("id")]
        run_websocket(access_token, ws_device_ids, duration=args.ws_duration)


if __name__ == "__main__":
    main()
