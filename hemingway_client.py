"""
Server-to-server client for Hemingway's real API (verified against actual
source 7/17 -- see STUDIO_SYSTEM_DESIGN.md Section 3).

Unified-auth approach: Studio's backend logs into Hemingway once, holds
that session itself, and re-uses it for all calls. The end user's browser
only ever talks to Studio -- it never sees hemingway.kmgtools.us directly.
This avoids the cross-subdomain cookie-sharing problem entirely, rather
than trying to make one browser session span studio.kmgtools.us and
hemingway.kmgtools.us.
"""

import os
import requests

HEMINGWAY_BASE_URL = os.environ.get("HEMINGWAY_BASE_URL", "https://hemingway.kmgtools.us")
HEMINGWAY_TEAM_PASSWORD = os.environ.get("HEMINGWAY_TEAM_PASSWORD", "")

_session = requests.Session()
_authenticated = False


class HemingwayError(Exception):
    pass


def _ensure_session():
    global _authenticated
    if _authenticated:
        return
    if not HEMINGWAY_TEAM_PASSWORD:
        raise HemingwayError(
            "HEMINGWAY_TEAM_PASSWORD is not set -- Studio can't log into Hemingway. "
            "Set it in .env (same team password used to log into hemingway.kmgtools.us directly)."
        )
    try:
        resp = _session.post(
            f"{HEMINGWAY_BASE_URL}/api/login",
            json={"password": HEMINGWAY_TEAM_PASSWORD},
            timeout=10,
        )
    except requests.exceptions.RequestException as e:
        raise HemingwayError(f"Couldn't reach Hemingway at {HEMINGWAY_BASE_URL}: {e}") from e
    if resp.status_code != 200:
        raise HemingwayError(f"Hemingway login failed ({resp.status_code}): {resp.text[:200]}")
    _authenticated = True


def _request(method, path, **kwargs):
    _ensure_session()
    try:
        resp = _session.request(method, f"{HEMINGWAY_BASE_URL}{path}", timeout=15, **kwargs)
        if resp.status_code == 401:
            # Session expired or was never valid -- retry once after re-login.
            global _authenticated
            _authenticated = False
            _ensure_session()
            resp = _session.request(method, f"{HEMINGWAY_BASE_URL}{path}", timeout=15, **kwargs)
    except requests.exceptions.RequestException as e:
        raise HemingwayError(f"Couldn't reach Hemingway at {HEMINGWAY_BASE_URL}{path}: {e}") from e
    if resp.status_code >= 400:
        raise HemingwayError(f"Hemingway API error ({resp.status_code}) on {path}: {resp.text[:200]}")
    return resp


def get_clients():
    """Live client list from Hemingway -- Studio has no local copy (Section 2/3:
    'single client table, not two-and-a-half')."""
    return _request("GET", "/api/clients").json()


def get_client(client_id):
    return _request("GET", f"/api/clients/{client_id}").json()


def update_style_rules(client_id, style_rules):
    return _request(
        "PUT", f"/api/clients/{client_id}/style-rules",
        json={"style_rules": style_rules},
    ).json()


def get_batches(client_id):
    return _request("GET", f"/api/clients/{client_id}/batches").json()


def get_batch_posts(batch_id):
    return _request("GET", f"/api/batches/{batch_id}/posts").json()
