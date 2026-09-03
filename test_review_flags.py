"""
Tests for the transcript/video review-flag feature (Ben's ask, 2026-08-27):
- orange "Review Transcript" / purple "Review Video" checkboxes on both
  review screens, coloring the box around video+transcript
- "Approved" clears both flags
- dashboard badge counts unresolved flags per project

Run: python test_review_flags.py
"""

import os
import sys

sys.path.insert(0, os.path.dirname(__file__))
os.environ["DB_PATH"] = "/tmp/studio_test/data/test_review_flags.db"
if os.path.exists(os.environ["DB_PATH"]):
    os.remove(os.environ["DB_PATH"])

import app as app_module
import database
import hemingway_client
import degas_client

database.init_db()

CLIENT_ID = 1
DEGAS_PROJECT_ID = 601
CLIP_ID = 701
results = {"pass": 0, "fail": 0}


def check(name, cond, detail=""):
    if cond:
        results["pass"] += 1
        print(f"PASS  {name}")
    else:
        results["fail"] += 1
        print(f"FAIL  {name}  {detail}")


hemingway_client.get_clients = lambda: [{"id": CLIENT_ID, "name": "Test Co"}]
degas_client.get_project = lambda p: {"id": p, "clips": [
    {"id": CLIP_ID, "status": "transcribed", "filename": "clip.mp4", "original_filename": "clip.mp4"},
]}
degas_client.get_clip_segments = lambda p, c: {
    "current": [{"start": 0, "end": 2, "text": "Hello world."}]
}

client = app_module.app.test_client()
with client.session_transaction() as sess:
    sess["logged_in"] = True


def make_project(name="Flag Test"):
    db = database.get_db()
    db.execute(
        "INSERT INTO projects (client_id, name, degas_project_id, phase) VALUES (?, ?, ?, 'clipped')",
        (CLIENT_ID, name, DEGAS_PROJECT_ID)
    )
    db.commit()
    pid = db.execute("SELECT id FROM projects WHERE name = ? ORDER BY id DESC LIMIT 1", (name,)).fetchone()["id"]
    db.close()
    return pid


def test_default_state_is_unflagged():
    pid = make_project("Default State")
    resp = client.get(f"/projects/{pid}/clips/{CLIP_ID}/review")
    check("clip_review: 200 OK", resp.status_code == 200)
    # Note: the CSS block always contains the literal strings "flag-transcript"
    # / "flag-video" (as selectors) -- must check the actual class attribute
    # usage on the review-layout div, not a bare substring search.
    check("clip_review: no flag classes applied by default",
          b'review-layout flag-transcript"' not in resp.data and b'review-layout flag-video"' not in resp.data)
    check("clip_review: approved checkbox disabled by default", b"disabled" in resp.data)


def test_toggle_transcript_flag_persists():
    pid = make_project("Transcript Flag")
    resp = client.post(f"/projects/{pid}/clips/{CLIP_ID}/review-flags",
                       data={"review_transcript": "on", "review_video": "", "came_from": "review"})
    check("toggle: redirects", resp.status_code == 302)

    db = database.get_db()
    row = db.execute("SELECT * FROM clip_review_flags WHERE project_id = ? AND clip_id = ?", (pid, CLIP_ID)).fetchone()
    db.close()
    check("toggle: review_transcript saved as 1", row["review_transcript"] == 1)
    check("toggle: review_video stayed 0", row["review_video"] == 0)

    resp = client.get(f"/projects/{pid}/clips/{CLIP_ID}/review")
    check("clip_review: flag-transcript class present after toggle", b'review-layout flag-transcript"' in resp.data)
    check("clip_review: approved checkbox now enabled (no 'disabled' attr on page)", b" disabled" not in resp.data)


def test_toggle_video_flag_does_not_clobber_transcript_flag():
    """Regression guard for the nested-form bug caught during build: toggling
    one flag must carry the other flag's current value forward, not reset it."""
    pid = make_project("Both Flags")
    client.post(f"/projects/{pid}/clips/{CLIP_ID}/review-flags",
               data={"review_transcript": "on", "review_video": "", "came_from": "review"})
    client.post(f"/projects/{pid}/clips/{CLIP_ID}/review-flags",
               data={"review_transcript": "on", "review_video": "on", "came_from": "review"})

    db = database.get_db()
    row = db.execute("SELECT * FROM clip_review_flags WHERE project_id = ? AND clip_id = ?", (pid, CLIP_ID)).fetchone()
    db.close()
    check("both flags: transcript still 1 after adding video flag", row["review_transcript"] == 1)
    check("both flags: video now 1", row["review_video"] == 1)

    resp = client.get(f"/projects/{pid}/clips/{CLIP_ID}/review")
    check("clip_review: flag-both class present when both set", b"flag-both" in resp.data)


def test_approve_clears_both_flags():
    pid = make_project("Approve Test")
    client.post(f"/projects/{pid}/clips/{CLIP_ID}/review-flags",
               data={"review_transcript": "on", "review_video": "on", "came_from": "review"})
    resp = client.post(f"/projects/{pid}/clips/{CLIP_ID}/review-flags/approve",
                       data={"came_from": "review"})
    check("approve: redirects", resp.status_code == 302)

    db = database.get_db()
    row = db.execute("SELECT * FROM clip_review_flags WHERE project_id = ? AND clip_id = ?", (pid, CLIP_ID)).fetchone()
    db.close()
    check("approve: review_transcript cleared", row["review_transcript"] == 0)
    check("approve: review_video cleared", row["review_video"] == 0)

    resp = client.get(f"/projects/{pid}/clips/{CLIP_ID}/review")
    check("clip_review: no flag classes after approve",
          b'review-layout flag-transcript"' not in resp.data and b'review-layout flag-video"' not in resp.data and b'review-layout flag-both"' not in resp.data)


def _project_row_html(resp, project_name):
    """The dashboard lists every project for the client on one page, and
    earlier tests in this run leave their own (unrelated) flags behind --
    a page-wide substring search for the badge would pick those up too.
    Scope the check to just this project's row."""
    html = resp.data.decode()
    marker = f">{project_name}<"
    start = html.find(marker)
    if start == -1:
        return ""
    end = html.find("proj-row", start + len(marker))
    return html[start:end if end != -1 else start + 2000]


def test_dashboard_badge_reflects_unresolved_flags():
    pid = make_project("Dashboard Badge Isolated")
    resp = client.get(f"/dashboard?client_id={CLIENT_ID}")
    check("dashboard: no badge before any flag set", "review-needed-badge" not in _project_row_html(resp, "Dashboard Badge Isolated"))

    client.post(f"/projects/{pid}/clips/{CLIP_ID}/review-flags",
               data={"review_transcript": "on", "review_video": "", "came_from": "review"})
    resp = client.get(f"/dashboard?client_id={CLIENT_ID}")
    check("dashboard: badge appears after flagging", "review-needed-badge" in _project_row_html(resp, "Dashboard Badge Isolated"))

    client.post(f"/projects/{pid}/clips/{CLIP_ID}/review-flags/approve", data={"came_from": "review"})
    resp = client.get(f"/dashboard?client_id={CLIENT_ID}")
    check("dashboard: badge disappears after approving", "review-needed-badge" not in _project_row_html(resp, "Dashboard Badge Isolated"))


def test_review_all_shows_flag_state_per_clip():
    pid = make_project("Review All Flags")
    client.post(f"/projects/{pid}/clips/{CLIP_ID}/review-flags",
               data={"review_transcript": "", "review_video": "on", "came_from": "review_all"})
    resp = client.get(f"/projects/{pid}/review-all")
    check("review_all: 200 OK", resp.status_code == 200)
    check("review_all: flag-video class present for flagged clip", b"flag-video" in resp.data)
    check("review_all: clip anchor id present for scroll target", f'id="clip-{CLIP_ID}"'.encode() in resp.data)


def test_came_from_review_all_redirects_with_anchor():
    pid = make_project("Redirect Test")
    resp = client.post(f"/projects/{pid}/clips/{CLIP_ID}/review-flags",
                       data={"review_transcript": "on", "review_video": "", "came_from": "review_all"})
    check("redirect: goes to review-all with clip anchor",
          resp.headers.get("Location", "").endswith(f"/projects/{pid}/review-all#clip-{CLIP_ID}"),
          resp.headers.get("Location"))


if __name__ == "__main__":
    test_default_state_is_unflagged()
    test_toggle_transcript_flag_persists()
    test_toggle_video_flag_does_not_clobber_transcript_flag()
    test_approve_clears_both_flags()
    test_dashboard_badge_reflects_unresolved_flags()
    test_review_all_shows_flag_state_per_clip()
    test_came_from_review_all_redirects_with_anchor()

    print(f"\nTOTAL: {results['pass']} passed, {results['fail']} failed")
    if results["fail"]:
        sys.exit(1)
