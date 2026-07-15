"""
Server-to-server client for Degas Clips's real API (task #7, KMG Studio
phase tracking -- STUDIO_SYSTEM_DESIGN.md Section 4).

Same "unified front door" pattern as hemingway_client.py: Studio's backend
logs into Degas once and holds that session itself. The end user's browser
never talks to degas.kmgtools.us directly.

One real difference from Hemingway: Degas's /login is a plain Flask form
route (request.form.get("password")), not a JSON API endpoint, so this
client posts form-encoded data rather than JSON.
"""

import os
import requests

DEGAS_BASE_URL = os.environ.get("DEGAS_BASE_URL", "https://degas.kmgtools.us")
DEGAS_TEAM_PASSWORD = os.environ.get("DEGAS_TEAM_PASSWORD", "")

_session = requests.Session()
_authenticated = False


class DegasError(Exception):
    pass


def _ensure_session():
    global _authenticated
    if _authenticated:
        return
    if not DEGAS_TEAM_PASSWORD:
        raise DegasError(
            "DEGAS_TEAM_PASSWORD is not set -- Studio can't log into Degas. "
            "Set it in .env (same team password used to log into degas.kmgtools.us directly)."
        )
    try:
        resp = _session.post(
            f"{DEGAS_BASE_URL}/login",
            data={"password": DEGAS_TEAM_PASSWORD},
            timeout=10,
            allow_redirects=True,
        )
    except requests.exceptions.RequestException as e:
        raise DegasError(f"Couldn't reach Degas at {DEGAS_BASE_URL}: {e}") from e
    # Degas's /login doesn't return a real error status on bad password --
    # it re-renders the login page (200) with an error message in the HTML.
    # Detect failure by checking whether we ended up back on /login.
    if resp.url.rstrip("/").endswith("/login"):
        raise DegasError("Degas login failed -- check DEGAS_TEAM_PASSWORD in .env")
    _authenticated = True


def _request(method, path, **kwargs):
    _ensure_session()
    try:
        resp = _session.request(method, f"{DEGAS_BASE_URL}{path}", timeout=15, **kwargs)
        if resp.url.rstrip("/").endswith("/login"):
            # Session expired -- retry once after re-login.
            global _authenticated
            _authenticated = False
            _ensure_session()
            resp = _session.request(method, f"{DEGAS_BASE_URL}{path}", timeout=15, **kwargs)
    except requests.exceptions.RequestException as e:
        raise DegasError(f"Couldn't reach Degas at {DEGAS_BASE_URL}{path}: {e}") from e
    if resp.status_code >= 400:
        raise DegasError(f"Degas API error ({resp.status_code}) on {path}: {resp.text[:200]}")
    return resp


def create_project(name, assigned_to, client_id):
    """Creates a project in Degas linked to this Hemingway/Studio client_id.
    Returns the new Degas project's id."""
    resp = _request(
        "POST", "/projects/new",
        data={"name": name, "assigned_to": assigned_to, "client_id": str(client_id)},
        headers={"Accept": "application/json"},
    )
    return resp.json()["id"]


def get_project(degas_project_id):
    """Returns {id, name, assigned_to, client_id, clips: [...]} for a Degas project."""
    resp = _request(
        "GET", f"/projects/{degas_project_id}",
        headers={"Accept": "application/json"},
    )
    return resp.json()


# Phase order used to decide whether a Degas-derived phase should overwrite
# Studio's stored phase, or whether Studio's own manual progress (Drafting
# onward) should win. See STUDIO_SYSTEM_DESIGN.md Section 4: "Intake through
# Clipped, Studio reads Degas's state rather than duplicating it; Drafting
# onward is genuinely new state Studio owns."
DEGAS_DERIVED_PHASES = ("intake", "transcribing", "caption_review", "clipped")
PHASE_ORDER = ("intake", "transcribing", "caption_review", "clipped", "drafting", "post_review")


def compute_degas_phase(clips):
    """Maps a list of Degas clip dicts (each with a 'status' key) to one of
    Studio's Intake/Transcribing/Caption Review/Clipped phases."""
    if not clips:
        return "intake"
    statuses = [c["status"] for c in clips]
    if any(s in ("uploaded", "transcribing") for s in statuses):
        return "transcribing"
    if all(s == "exported" for s in statuses):
        return "clipped"
    if any(s in ("transcribed", "exporting", "error") for s in statuses):
        return "caption_review"
    return "intake"


def effective_phase(stored_phase, clips):
    """Reconciles Studio's stored phase with what Degas's clip statuses say.
    Once a project has been manually advanced to Drafting or Post Review,
    Degas's status (which will just sit at 'exported' forever) must not
    downgrade it back -- see PHASE_ORDER comment above."""
    stored_phase = stored_phase or "intake"
    if stored_phase not in DEGAS_DERIVED_PHASES:
        return stored_phase
    return compute_degas_phase(clips)
