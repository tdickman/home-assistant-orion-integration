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
import json
import os
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

    devices = list_devices(access_token)
    if devices is not None:
        _pretty("Devices", devices)

    sleep_configs = get_sleep_config_devices(access_token)
    if sleep_configs is not None:
        _pretty("Sleep Configurations (devices)", sleep_configs)

    session = get_session_state(access_token)
    if session is not None:
        _pretty("Current Session State", session)

    schedules = get_sleep_schedules(access_token)
    if schedules is not None:
        _pretty("Sleep Schedules", schedules)

    insights = get_insights(access_token, days=args.insights_days)
    if insights is not None:
        _pretty(f"Sleep Insights (last {args.insights_days} days)", insights)


if __name__ == "__main__":
    main()
