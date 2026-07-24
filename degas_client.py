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


# Degas's own caption-burn styles (captions.py STYLES) -- the /export and
# /export-all routes take this as a plain string form field, falling back to
# "1" for anything unrecognized. Confirmed against live server 7/24.
EXPORT_STYLES = {
    "1": "Golden Word",
    "2": "Pro Bronze",
    "3": "Purple Flash",
    "4": "Clean Pill",
}


def upload_chunk(degas_project_id, file_uid, chunk_index, total_chunks, filename, chunk_bytes, content_type="application/octet-stream"):
    """Forwards one chunk of a video upload to Degas's real chunked-upload
    protocol (confirmed against live server 7/24 -- there is no non-chunked
    variant). Studio's own upload route receives chunks from the browser and
    calls this once per chunk using its own authenticated Degas session, since
    the browser never talks to degas.kmgtools.us directly.

    Returns Degas's response JSON: {"status": "chunk_received", "chunks":,
    "total":} for every chunk except the last, or {"status": "complete",
    "filename": <original>} once all chunks have arrived and been reassembled
    into a new clip (status='uploaded'). The response does NOT include the
    new clip's id -- re-fetch get_project() to find it."""
    resp = _request(
        "POST", f"/projects/{degas_project_id}/upload/chunk",
        data={
            "file_uid": file_uid,
            "chunk_index": str(chunk_index),
            "total_chunks": str(total_chunks),
            "filename": filename,
        },
        files={"data": (filename, chunk_bytes, content_type)},
    )
    return resp.json()


def delete_clip_media(degas_project_id, clip_id):
    """Storage cleanup (task #22): permanently deletes a clip's raw uploaded
    video and exported captioned file from Degas's disk, via a new route
    added directly on the live server (appended, no other code touched --
    see project notes on why this couldn't go through the normal git
    push/pull deploy this one time). The clip's DB row survives, marked
    status='deleted', so project history and phase tracking aren't lost --
    only the actual media files are gone, and this cannot be undone."""
    resp = _request("POST", f"/projects/{degas_project_id}/clips/{clip_id}/delete-media")
    return resp.json()


def get_clip_status(degas_project_id, clip_id):
    """Polls a single clip's transcription/export progress. Returns
    {"status": ..., "error": ...} normally, or {"status": "transcribing",
    "error": None, "elapsed": <seconds>} while a transcription is running."""
    resp = _request("GET", f"/projects/{degas_project_id}/clips/{clip_id}/status")
    return resp.json()


def trigger_transcribe(degas_project_id, clip_id):
    """Starts transcription for one clip. Fire-and-forget -- Degas responds
    immediately with {"status": "transcribing"} and runs Whisper in a
    background thread; poll get_clip_status() for completion."""
    resp = _request("POST", f"/projects/{degas_project_id}/clips/{clip_id}/transcribe")
    return resp.json()


def trigger_transcribe_all(degas_project_id):
    """Starts transcription for every clip in the project currently
    'uploaded' or 'error'. Degas's route is written for browser form
    submission, not API use -- it 302-redirects to the project page rather
    than returning JSON, so there's nothing meaningful to parse from the
    response. Poll get_clip_status() per clip afterward."""
    _request("POST", f"/projects/{degas_project_id}/transcribe-all")


def save_clip_segments(degas_project_id, clip_id, segments):
    """Saves edited transcript text for one clip (Caption Review). `segments`
    must be the full list of {"start": float, "end": float, "text": str}
    dicts, positionally aligned to the existing segments file -- Degas's
    /save route only actually reads .text per index, but also feeds the same
    list into update_words_from_segments(), which does need real start/end
    values, so always send the complete original start/end alongside any
    edited text rather than omitting them."""
    resp = _request(
        "POST", f"/projects/{degas_project_id}/clips/{clip_id}/save",
        json={"segments": segments},
    )
    return resp.json()


def trigger_export(degas_project_id, clip_id, style="1"):
    """Starts caption-burn export for one clip. `style` is one of
    EXPORT_STYLES's keys ("1"-"4"); unrecognized values silently fall back to
    "1" on Degas's side, not an error. Fire-and-forget, same pattern as
    transcribe -- poll get_clip_status() for 'exported'/'error'."""
    resp = _request(
        "POST", f"/projects/{degas_project_id}/clips/{clip_id}/export",
        data={"style": style},
    )
    return resp.json()


def trigger_export_all(degas_project_id, style="1"):
    """Starts caption-burn export for every clip currently 'transcribed'
    (NOT 'error', unlike transcribe-all). Same redirect-not-JSON response
    shape as transcribe_all -- poll per clip afterward."""
    _request("POST", f"/projects/{degas_project_id}/export-all", data={"style": style})


def save_all_clip_segments(degas_project_id, clips_payload):
    """Bulk save for the 'Review All' screen (task #23): saves edited
    segment text for every clip in one call via Degas's real /save-all
    route (same one its own editor-all page uses), rather than looping a
    separate save_clip_segments() call per clip. clips_payload is
    [{"clip_id": int, "segments": [{"start","end","text"}, ...]}, ...]."""
    resp = _request(
        "POST", f"/projects/{degas_project_id}/save-all",
        json={"clips": clips_payload},
    )
    return resp.json()


def download_clip(degas_project_id, clip_id):
    """Returns the raw streaming response for a captioned clip's video file,
    for Studio's own download route to proxy through to the browser (the
    browser never talks to Degas directly). Degas redirects to the project
    page instead of erroring if the export isn't ready yet -- callers should
    check for a real 'Content-Disposition' header on the response before
    treating this as a successful file, not just the status code."""
    return _request(
        "GET", f"/projects/{degas_project_id}/clips/{clip_id}/download",
        stream=True,
    )


def stream_clip_video(degas_project_id, clip_id, range_header=None):
    """Returns the raw streaming response for a clip's ORIGINAL uploaded
    video (no captions burned in) -- for inline preview during Caption
    Review, not for download. Backed by Degas's confirmed-live
    /projects/<id>/clips/<id>/video route (send_file, Flask's conditional=True
    default handles Range requests on Degas's end).

    Forwards the browser's Range header through if given, so Studio's proxy
    route can pass back a real 206 Partial Content response -- without this,
    the <video> player can still load and play the file top-to-bottom but
    users can't scrub/seek partway through a clip, which defeats the point
    of "watch the clip while reading the line in question." Degas may
    respond with a plain 200 (whole file, e.g. on first load with no Range
    yet) or 206 (a specific byte range) -- both are passed through as-is."""
    headers = {"Range": range_header} if range_header else {}
    return _request(
        "GET", f"/projects/{degas_project_id}/clips/{clip_id}/video",
        stream=True, headers=headers,
    )


def get_clip_segments(degas_project_id, clip_id):
    """Returns {original: [...], current: [...]} segments for a clip (task
    #8, glossary system) -- 'original' is the immutable as-transcribed
    snapshot, 'current' reflects any Caption Review edits."""
    resp = _request(
        "GET", f"/projects/{degas_project_id}/clips/{clip_id}/segments",
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
    # 'deleted' (storage cleanup, task #22) counts the same as 'exported' here
    # -- a clip whose media was cleaned up after 45+ days was already fully
    # exported; it shouldn't make an old, finished project look unclipped.
    if all(s in ("exported", "deleted") for s in statuses):
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
