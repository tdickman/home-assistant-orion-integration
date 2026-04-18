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


# ── device power / zone control ───────────────────────────────────────────────
#
# Reverse-engineered from Hermes bytecode.  The app's on/off toggle calls:
#
#   PUT /v1/devices/{device_id}/live/zones/{zone_id}   body: {"on": bool, "temp"?: n}
#   PUT /v1/devices/{device_id}/live                   body: {"zones": [{"id", "on", "temp"?}, ...]}
#
# These are the canonical power-control endpoints used by the mobile app's
# UI toggle (useDeviceControlStore, lines 1027388-1027684 of the decompiled
# bundle).  They are distinct from:
#   - POST /v1/sleep-configurations/user-away   (presence / schedule override)
#   - POST /v1/devices/{id}/activate|deactivate (pairing lifecycle, not power)
#   - POST /v1/devices/{id}/action              (quiet_mode, reboot, etc.)


def _put_raw(token: str, path: str, body: dict) -> tuple[int, str]:
    """PUT a JSON body and return (status, text) without raising."""
    resp = requests.put(_url(path), json=body, headers=_headers(token))
    return resp.status_code, resp.text


def set_zone(
    token: str,
    device_ident: str,
    zone_id: str,
    on: bool | None = None,
    temp: float | None = None,
) -> tuple[int, str]:
    """PUT /v1/devices/{device_ident}/live/zones/{zone_id} — per-zone control.

    `device_ident` is whatever the server accepts in the path (id or
    serial_number — that's what we're probing).  Returns (status, text).
    """
    body: dict = {}
    if on is not None:
        body["on"] = on
    if temp is not None:
        body["temp"] = temp
    if not body:
        raise ValueError("set_zone: specify at least one of on= or temp=")
    return _put_raw(token, f"/v1/devices/{device_ident}/live/zones/{zone_id}", body)


def set_device_zones(
    token: str,
    device_ident: str,
    zones: list[dict],
) -> tuple[int, str]:
    """PUT /v1/devices/{device_ident}/live — bulk zone update.

    `zones` is a list of {"id": str, "on": bool?, "temp": float?} dicts.
    Returns (status, text) without raising.
    """
    return _put_raw(token, f"/v1/devices/{device_ident}/live", {"zones": zones})


def _zones_body(device: dict, on: bool, temp: float | None = None) -> list[dict]:
    zones_body: list[dict] = []
    for z in device.get("zones") or []:
        entry: dict = {"id": z["id"], "on": on}
        if temp is not None:
            entry["temp"] = temp
        zones_body.append(entry)
    if not zones_body:
        raise ValueError("device has no zones")
    return zones_body


def probe_power(token: str, device: dict, on: bool) -> None:
    """Probe the live power endpoints using both id and serial_number.

    Prints the server response for each variant so we can figure out which
    identifier and endpoint the API actually accepts.  The OpenAPI says
    `id`, but the WebSocket path uses `serial_number` — this probes both.
    """
    device_id = device.get("id", "")
    serial = device.get("serial_number", "")
    zone_list = device.get("zones") or []
    first_zone_id = zone_list[0]["id"] if zone_list else ""

    attempts: list[tuple[str, str, dict]] = []
    # Bulk-zone endpoint
    if device_id:
        attempts.append(
            (
                "bulk/id",
                f"/v1/devices/{device_id}/live",
                {"zones": _zones_body(device, on)},
            )
        )
    if serial and serial != device_id:
        attempts.append(
            (
                "bulk/serial",
                f"/v1/devices/{serial}/live",
                {"zones": _zones_body(device, on)},
            )
        )
    # Single-zone endpoint (only works if we have a zone id)
    if first_zone_id:
        if device_id:
            attempts.append(
                (
                    "zone/id",
                    f"/v1/devices/{device_id}/live/zones/{first_zone_id}",
                    {"on": on},
                )
            )
        if serial and serial != device_id:
            attempts.append(
                (
                    "zone/serial",
                    f"/v1/devices/{serial}/live/zones/{first_zone_id}",
                    {"on": on},
                )
            )

    print(f"\n>>> Probing power={on} for device id={device_id} serial={serial}")
    for label, path, body in attempts:
        status, text = _put_raw(token, path, body)
        print(f"  [{label:12s}] PUT {path}")
        print(f"               body={json.dumps(body)}")
        print(f"               -> {status}  {text[:300]}")
        if 200 <= status < 300:
            print(f"  [SUCCESS via {label}]")
            # Don't keep trying once one works so we don't re-toggle the bed.
            return
    print("  [ALL ATTEMPTS FAILED]")


# ── websocket ──────────────────────────────────────────────────────────────
#
#   URL: wss://live.api1.orionbed.com/device/<serial_number>?token=<JWT>
#
# Reverse-engineered from the Hermes bytecode in the Orion Android APK and
# then validated against the live server (see `run_ws_scenario` below, which
# opens a WS and drives REST mutations while logging every frame).
#
# Connection notes:
#   - The path is `/device/<serial_number>` (the device's serial_number, NOT
#     its UUID/id).  The server responds 400 for `/device/` (missing serial)
#     and 404 {"error":"Not Found","message":"Device not found"} for any
#     non-serial value.
#   - The token is a URL-encoded query parameter (not a header or subprotocol).
#   - Cloudflare fronts the host; the SSL context must force ALPN to
#     `http/1.1` or the WS Upgrade is rejected.
#   - Working User-Agent: `okhttp/4.12.0` (what the Android app sends).
#   - There is NO JSON subscribe/unsubscribe handshake.  The server pushes
#     messages immediately on connect.
#   - On background / shutdown, the client closes with code 1001.
#
# Event taxonomy (exhaustive as of the last capture):
#
#   live_device.snapshot     once immediately after connect; full state
#   live_device.update       on every REST-triggered state change AND
#                            ~every 2s as an idle heartbeat/refresh
#
# Both events use the envelope {"type": <event>, "payload": {...}} and share
# the same payload shape.  Summary of the payload:
#
#   payload.serial_number         string (matches the path)
#   payload.model                 e.g. "OSCT001-1"
#   payload.zones[]               setpoints (user intent):
#                                   {"id": "zone_a|zone_b", "temp": 20.5, "on": false}
#   payload.led_brightness        int (0-100)
#   payload.water_fill            string (observed "unknown")
#   payload.is_in_water_fill_mode bool
#   payload.status.online         bool
#   payload.status.firmware       {"cb": "2.6.0", "ib": "2.5.0"}
#   payload.status.firmware_update {workflow_id, started_at, updated_at,
#                                   in_progress, current_step, completed_at,
#                                   result}  (unix ms timestamps)
#   payload.status.pending_update {"is_available": bool}
#   payload.status.network        {last_seen, name, ip, rssi, uptime, mac}
#   payload.status.safety         {error, error_codes[], error_descriptions[]}
#   payload.status.zones[]        measured (distinct from setpoints):
#                                   {"id": "...", "temp": 21.9, "thermal_state": "standby"}
#   payload.status.sensors.sensor1, sensor2
#                                 {heart_rate, breath_rate, status, status_text,
#                                  sign_of_asleep, sign_of_wake_up, timestamp,
#                                  uptime, is_working, firmware_version,
#                                  hardware_version}
#   payload.timeline[]            present on live_device.update only (may be
#                                 empty).  Today's scheduled actions from
#                                 /v1/sleep-schedules:
#                                   {id, user_id, label (bedtime|phase_1|
#                                    phase_2|wake_up|turn_off), scheduled_time,
#                                    action: {zones: [...]}, created_at}
#
# Notable:
#   - set_user_away does NOT emit a distinct event type; it results in another
#     live_device.update with the zones turned off / back on.
#   - No client-to-server messages are sent or required.
#   - The ~2s idle refresh also serves as an application-level keepalive.

WS_BASE_URL = "wss://live.api1.orionbed.com"


def _ws_ssl_context() -> ssl.SSLContext:
    """Create an SSL context that forces HTTP/1.1 via ALPN.

    Cloudflare negotiates HTTP/2 by default, which prevents the
    WebSocket Upgrade handshake (RFC 6455 requires HTTP/1.1).
    """
    ctx = ssl.create_default_context()
    ctx.set_alpn_protocols(["http/1.1"])
    return ctx


async def _ws_listen_one(
    token: str,
    serial: str,
    duration: float,
    stop: asyncio.Event,
) -> None:
    """Open one /device/<serial> WebSocket and log incoming messages."""
    import websockets
    from urllib.parse import quote

    url = f"{WS_BASE_URL}/device/{quote(serial, safe='')}?token={token}"
    print(f"\nConnecting to {WS_BASE_URL}/device/{serial}?token=<JWT>")
    print(f"  Duration: {duration:.0f}s  (Ctrl+C to stop)\n")

    try:
        async with websockets.connect(
            url,
            ssl=_ws_ssl_context(),
            user_agent_header="okhttp/4.12.0",
        ) as ws:
            print(f"[CONNECTED] serial={serial}\n")

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

            print(f"\nTotal messages received: {count}")

            # Match the app's shutdown behavior (AppState -> background)
            await ws.close(1001, "client shutdown")

    except websockets.exceptions.InvalidStatus as e:
        print(f"[ERROR] WebSocket upgrade rejected: HTTP {e.response.status_code}")
        if hasattr(e.response, "body") and e.response.body:
            print(f"  Body: {e.response.body.decode('utf-8', errors='replace')}")
        if e.response.status_code == 401:
            print("  Token may be expired or invalid. Try --relogin.")
        elif e.response.status_code == 404:
            print(
                "  Device not found. The WebSocket path uses the device's"
                "\n  serial_number (not its UUID)."
            )
    except Exception as e:
        print(f"[ERROR] WebSocket failed: {type(e).__name__}: {e}")


async def _ws_connect_and_listen(
    token: str,
    serials: list[str],
    duration: float = 60.0,
) -> None:
    stop = asyncio.Event()
    loop = asyncio.get_running_loop()
    for sig in (signal.SIGINT, signal.SIGTERM):
        loop.add_signal_handler(sig, stop.set)

    # The app opens one WS per device.  Do the same — run them concurrently.
    await asyncio.gather(*(_ws_listen_one(token, s, duration, stop) for s in serials))


def run_websocket(
    token: str,
    serials: list[str],
    duration: float = 60.0,
) -> None:
    """Entry point for the --websocket flag.

    `serials` must be device serial_numbers (NOT UUIDs).
    """
    if not serials:
        print("\n[ERROR] No device serial numbers found — cannot open WebSocket.")
        print("  Make sure you have at least one device on your account.")
        return

    asyncio.run(_ws_connect_and_listen(token, serials, duration))


# ── websocket scenario (WS capture + REST edits interleaved) ───────────────
#
# --ws-scenario opens a per-device WebSocket and drives a scripted sequence of
# REST edits on the live endpoints while logging everything with monotonic
# timestamps.  Used to enumerate the server -> client event taxonomy.
#
# The scenario:
#   1. Snapshots initial zone state (on/temp) for each device.
#   2. Waits WS_SCENARIO_IDLE_SECONDS for idle baseline traffic.
#   3. Toggles each zone off / on via PUT /v1/devices/{serial}/live/zones/{zone}
#   4. Sets zone temp to a "low" then "high" value.
#   5. Bulk-turns all zones off then on via PUT /v1/devices/{serial}/live.
#   6. Toggles set_user_away(True) then set_user_away(False).
#   7. Restores the initial per-zone state captured in step 1.
#   8. Drains WS for WS_SCENARIO_TAIL_SECONDS to capture delayed pushes.
#   9. Closes the WS connections with code 1001.


WS_SCENARIO_IDLE_SECONDS = 15.0
WS_SCENARIO_STEP_SECONDS = 8.0
WS_SCENARIO_TAIL_SECONDS = 20.0
WS_SCENARIO_LOW_TEMP = 21.0
WS_SCENARIO_HIGH_TEMP = 28.0


def _scenario_log(tag: str, msg: str, *, start: float) -> None:
    t = time.monotonic() - start
    print(f"[T+{t:7.2f}s] [{tag}] {msg}", flush=True)


async def _ws_capture_one(
    token: str,
    serial: str,
    stop: asyncio.Event,
    start: float,
) -> None:
    """Open a WS and log every frame until `stop` is set."""
    import websockets
    from urllib.parse import quote

    url = f"{WS_BASE_URL}/device/{quote(serial, safe='')}?token={token}"
    _scenario_log("WS", f"connecting serial={serial}", start=start)

    try:
        async with websockets.connect(
            url,
            ssl=_ws_ssl_context(),
            user_agent_header="okhttp/4.12.0",
            ping_interval=None,  # let the server drive pings; keep our log quiet
        ) as ws:
            _scenario_log("WS", f"connected serial={serial}", start=start)
            count = 0
            while not stop.is_set():
                try:
                    async with asyncio.timeout(0.5):
                        msg = await ws.recv()
                except TimeoutError:
                    continue
                count += 1
                try:
                    data = json.loads(msg)
                    # Collapse large payloads to one line in the log for
                    # easier event-taxonomy enumeration; also print full
                    # pretty form for the first 3 messages of each kind.
                    kind = None
                    if isinstance(data, dict):
                        kind = data.get("type") or data.get("event")
                    _scenario_log(
                        f"WS/{serial[-6:]}",
                        f"#{count} type={kind!r}",
                        start=start,
                    )
                    print(json.dumps(data, indent=2), flush=True)
                except (json.JSONDecodeError, TypeError):
                    _scenario_log(
                        f"WS/{serial[-6:]}",
                        f"#{count} raw={msg!r}",
                        start=start,
                    )
            _scenario_log("WS", f"closing serial={serial} (total={count})", start=start)
            await ws.close(1001, "scenario done")
    except Exception as e:
        _scenario_log(
            "WS", f"error serial={serial}: {type(e).__name__}: {e}", start=start
        )


async def _scenario_sleep(seconds: float, start: float, label: str) -> None:
    _scenario_log("WAIT", f"{label} — sleeping {seconds:.1f}s", start=start)
    await asyncio.sleep(seconds)


async def _scenario_rest(
    token: str,
    devices: list[dict],
    user_id: str,
    stop: asyncio.Event,
    start: float,
) -> None:
    """Drive the REST sequence.  `devices` is the list from /v1/devices."""
    # Snapshot initial state so we can restore it.
    initial: list[tuple[str, list[dict]]] = []
    for d in devices:
        serial = d.get("serial_number")
        zones = d.get("zones") or []
        snapshot = []
        for z in zones:
            # We only have the `zones[].user` from /v1/devices; actual
            # `on`/`temp` live-state comes from GET /v1/devices/{serial}/live.
            snapshot.append({"id": z["id"]})
        if serial and snapshot:
            initial.append((serial, snapshot))

    # Fetch per-serial live state to capture true on/temp for restore.
    live_initial: dict[str, dict] = {}
    for serial, _ in initial:
        try:
            resp = requests.get(
                _url(f"/v1/devices/{serial}/live"),
                headers=_headers(token),
                timeout=10,
            )
            body = _check(resp, f"get live {serial}")
            if isinstance(body, dict):
                live_initial[serial] = (body.get("response") or body).get(
                    "zones", []
                ) or body.get("zones", [])
        except Exception as e:
            _scenario_log("REST", f"failed to snapshot live {serial}: {e}", start=start)

    _scenario_log(
        "REST", f"initial live state: {json.dumps(live_initial)}", start=start
    )

    await _scenario_sleep(WS_SCENARIO_IDLE_SECONDS, start, "idle baseline")
    if stop.is_set():
        return

    # Step through each device.
    for serial, zones in initial:
        first_zone = zones[0]["id"]

        # --- single-zone power off
        status, text = set_zone(token, serial, first_zone, on=False)
        _scenario_log(
            "REST",
            f"PUT /live/zones/{first_zone} on=false -> {status} {text[:120]}",
            start=start,
        )
        await _scenario_sleep(WS_SCENARIO_STEP_SECONDS, start, "after zone off")
        if stop.is_set():
            break

        # --- single-zone power on
        status, text = set_zone(token, serial, first_zone, on=True)
        _scenario_log(
            "REST",
            f"PUT /live/zones/{first_zone} on=true -> {status} {text[:120]}",
            start=start,
        )
        await _scenario_sleep(WS_SCENARIO_STEP_SECONDS, start, "after zone on")
        if stop.is_set():
            break

        # --- single-zone temp change (low)
        status, text = set_zone(token, serial, first_zone, temp=WS_SCENARIO_LOW_TEMP)
        _scenario_log(
            "REST",
            f"PUT /live/zones/{first_zone} temp={WS_SCENARIO_LOW_TEMP} -> {status} {text[:120]}",
            start=start,
        )
        await _scenario_sleep(WS_SCENARIO_STEP_SECONDS, start, "after zone temp low")
        if stop.is_set():
            break

        # --- single-zone temp change (high)
        status, text = set_zone(token, serial, first_zone, temp=WS_SCENARIO_HIGH_TEMP)
        _scenario_log(
            "REST",
            f"PUT /live/zones/{first_zone} temp={WS_SCENARIO_HIGH_TEMP} -> {status} {text[:120]}",
            start=start,
        )
        await _scenario_sleep(WS_SCENARIO_STEP_SECONDS, start, "after zone temp high")
        if stop.is_set():
            break

        # --- bulk all zones off
        bulk_off = [{"id": z["id"], "on": False} for z in zones]
        status, text = set_device_zones(token, serial, bulk_off)
        _scenario_log(
            "REST", f"PUT /live bulk off -> {status} {text[:120]}", start=start
        )
        await _scenario_sleep(WS_SCENARIO_STEP_SECONDS, start, "after bulk off")
        if stop.is_set():
            break

        # --- bulk all zones on
        bulk_on = [{"id": z["id"], "on": True} for z in zones]
        status, text = set_device_zones(token, serial, bulk_on)
        _scenario_log(
            "REST", f"PUT /live bulk on -> {status} {text[:120]}", start=start
        )
        await _scenario_sleep(WS_SCENARIO_STEP_SECONDS, start, "after bulk on")
        if stop.is_set():
            break

    if stop.is_set():
        _scenario_log("REST", "stop requested, skipping away/restore", start=start)
        return

    # --- away mode on
    if user_id:
        result = set_user_away(token, user_id, is_away=True)
        _scenario_log(
            "REST",
            f"POST /user-away is_away=true -> {json.dumps(result)[:160]}",
            start=start,
        )
        await _scenario_sleep(WS_SCENARIO_STEP_SECONDS, start, "after set_away=true")

        result = set_user_away(token, user_id, is_away=False)
        _scenario_log(
            "REST",
            f"POST /user-away is_away=false -> {json.dumps(result)[:160]}",
            start=start,
        )
        await _scenario_sleep(WS_SCENARIO_STEP_SECONDS, start, "after set_away=false")

    if stop.is_set():
        return

    # --- restore original per-zone state
    for serial, initial_zones in live_initial.items():
        if not initial_zones:
            continue
        restore_body = []
        for z in initial_zones:
            entry = {"id": z.get("id")}
            if "on" in z or "is_on" in z:
                entry["on"] = bool(z.get("on", z.get("is_on")))
            if "temp" in z and z.get("temp") is not None:
                entry["temp"] = z["temp"]
            if entry.get("id"):
                restore_body.append(entry)
        if restore_body:
            status, text = set_device_zones(token, serial, restore_body)
            _scenario_log(
                "REST",
                f"RESTORE PUT /live {serial}: {json.dumps(restore_body)} -> {status} {text[:120]}",
                start=start,
            )

    await _scenario_sleep(WS_SCENARIO_TAIL_SECONDS, start, "tail drain")


async def _run_ws_scenario(
    token: str,
    devices: list[dict],
    user_id: str,
) -> None:
    start = time.monotonic()
    stop = asyncio.Event()
    loop = asyncio.get_running_loop()
    for sig in (signal.SIGINT, signal.SIGTERM):
        loop.add_signal_handler(sig, stop.set)

    serials = [d["serial_number"] for d in devices if d.get("serial_number")]
    if not serials:
        print("[ERROR] no serials — cannot run scenario")
        return

    ws_tasks = [
        asyncio.create_task(_ws_capture_one(token, s, stop, start)) for s in serials
    ]
    try:
        await _scenario_rest(token, devices, user_id, stop, start)
    finally:
        stop.set()
        await asyncio.gather(*ws_tasks, return_exceptions=True)


def run_ws_scenario(
    token: str,
    devices: list[dict],
    user_id: str,
) -> None:
    asyncio.run(_run_ws_scenario(token, devices, user_id))


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
        "--power-on",
        action="store_true",
        help="Probe PUT /v1/devices/<ident>/live with on=true against each "
        "device, trying id then serial_number (stops at first 2xx).",
    )
    parser.add_argument(
        "--power-off",
        action="store_true",
        help="Probe PUT /v1/devices/<ident>/live with on=false against each "
        "device, trying id then serial_number (stops at first 2xx).",
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
    parser.add_argument(
        "--ws-scenario",
        action="store_true",
        help="Run a scripted sequence of REST edits (zone on/off, temp, bulk, "
        "user-away) while logging WebSocket messages, to enumerate the event "
        "taxonomy. Restores the original zone state at the end.",
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

    # ── Power probe (PUT /v1/devices/<ident>/live) ───────────────────
    if args.power_on or args.power_off:
        desired = args.power_on  # True for power-on, False for power-off
        for device in device_list:
            probe_power(access_token, device, on=desired)

        # Re-fetch to show the post-probe state
        print("\n>>> Re-fetching devices after power probe...")
        devices_after = list_devices(access_token)
        if devices_after is not None:
            _pretty("Devices (after power probe)", devices_after)

    # ── WebSocket ─────────────────────────────────────────────────────
    if args.websocket:
        # The WS path is /device/<serial_number>?token=<JWT> (reverse-engineered
        # from the Android APK's Hermes bytecode).  Use serial_number, not id.
        ws_serials = [
            d.get("serial_number") for d in device_list if d.get("serial_number")
        ]
        run_websocket(access_token, ws_serials, duration=args.ws_duration)

    # ── WebSocket scenario (interleaved capture) ──────────────────────
    if args.ws_scenario:
        user_id = ""
        if user:
            resp_data = user.get("response", user)
            user_id = resp_data.get("id", "")
        run_ws_scenario(access_token, device_list, user_id)


if __name__ == "__main__":
    main()
