import io
import os
import sys

sys.path.insert(0, os.path.dirname(__file__))
os.environ["DB_PATH"] = "/tmp/studio_test/data/test_studio_clip_link.db"
if os.path.exists(os.environ["DB_PATH"]):
    os.remove(os.environ["DB_PATH"])

import app as app_module
import database
import hemingway_client
import degas_client
import postiz_client

database.init_db()

CLIENT_ID = 1
DEGAS_PROJECT_ID = 501
results = {"pass": 0, "fail": 0}


def check(name, cond, detail=""):
    if cond:
        results["pass"] += 1
        print(f"PASS  {name}")
    else:
        results["fail"] += 1
        print(f"FAIL  {name}  {detail}")


hemingway_client.get_clients = lambda: [{"id": CLIENT_ID, "name": "Epiphany"}]

client = app_module.app.test_client()
with client.session_transaction() as sess:
    sess["logged_in"] = True


def make_project(name="Link Test"):
    db = database.get_db()
    db.execute(
        "INSERT INTO projects (client_id, name, degas_project_id, phase) VALUES (?, ?, ?, 'caption_review')",
        (CLIENT_ID, name, DEGAS_PROJECT_ID)
    )
    db.commit()
    pid = db.execute("SELECT id FROM projects WHERE name = ? ORDER BY id DESC LIMIT 1", (name,)).fetchone()["id"]
    db.close()
    return pid


def link_postiz(group_id="grp-1"):
    db = database.get_db()
    db.execute(
        "INSERT OR REPLACE INTO client_postiz_groups (client_id, postiz_group_id, postiz_group_name) VALUES (?, ?, ?)",
        (CLIENT_ID, group_id, "Epiphany Group")
    )
    db.commit()
    db.close()


INTEGRATIONS = [
    {"id": "int-li", "name": "Epiphany LinkedIn", "identifier": "linkedin", "disabled": False},
    {"id": "int-yt", "name": "Epiphany YouTube", "identifier": "youtube", "disabled": False},
]


# ── _build_project_transcript: clip_id ordering ──────────────────────────────

def test_build_transcript_returns_ordered_clip_ids():
    clips = [
        {"id": 11, "status": "transcribed", "filename": "a.mp4", "original_filename": "a.mp4"},
        {"id": 12, "status": "uploaded", "filename": "b.mp4", "original_filename": "b.mp4"},  # not eligible
        {"id": 13, "status": "exported", "filename": "c.mp4", "original_filename": "c.mp4"},
    ]
    degas_client.get_clip_segments = lambda p, c: {"current": [{"start": 0, "end": 1, "text": f"text for clip {c}"}]}
    transcript, ordered_clip_ids = app_module._build_project_transcript(DEGAS_PROJECT_ID, clips)
    check("transcript includes both eligible clips", "VIDEO: 01" in transcript and "VIDEO: 02" in transcript)
    check("ordered_clip_ids skips the ineligible clip", ordered_clip_ids == [11, 13], ordered_clip_ids)


def test_write_posts_stores_clip_id_per_post():
    pid = make_project("Write Posts Link Test")
    degas_client.get_project = lambda p: {"id": p, "clips": [
        {"id": 21, "status": "transcribed", "filename": "x.mp4", "original_filename": "x.mp4"},
        {"id": 22, "status": "transcribed", "filename": "y.mp4", "original_filename": "y.mp4"},
    ]}
    degas_client.get_clip_segments = lambda p, c: {"current": [{"start": 0, "end": 1, "text": f"text {c}"}]}

    def fake_generate(client_id, transcript, style, length, context="", name=""):
        return {"batch_id": 777, "posts": [
            {"index": 0, "id": 901, "title": "x", "body": "Post about x", "error": None},
            {"index": 1, "id": 902, "title": "y", "body": "Post about y", "error": None},
        ]}
    hemingway_client.generate_from_transcript = fake_generate

    resp = client.post(f"/projects/{pid}/write-posts", data={"style": "conversational", "length": "short"})
    check("write-posts: redirects", resp.status_code == 302, resp.status_code)

    db = database.get_db()
    posts = db.execute("SELECT * FROM posts WHERE project_id = ? ORDER BY id", (pid,)).fetchall()
    db.close()
    check("write-posts: 2 posts inserted", len(posts) == 2, len(posts))
    check("write-posts: post 0 linked to clip 21", posts[0]["clip_id"] == 21, dict(posts[0]))
    check("write-posts: post 1 linked to clip 22", posts[1]["clip_id"] == 22, dict(posts[1]))


# ── project_detail: media-capable channels + per-post export gating ─────────

def test_project_detail_shows_media_capable_channels_and_gating():
    pid = make_project("Detail Gating Test")
    link_postiz()
    postiz_client.list_integrations = lambda group_id: INTEGRATIONS

    db = database.get_db()
    db.execute(
        "INSERT INTO posts (client_id, project_id, source, caption, status, hemingway_post_id, clip_id) VALUES (?, ?, 'project', ?, 'draft', ?, ?)",
        (CLIENT_ID, pid, "caption for unexported clip", 111, 31)
    )
    db.commit()
    db.close()

    degas_client.get_project = lambda p: {"clips": [
        {"id": 31, "status": "transcribed", "filename": "z.mp4", "original_filename": "z.mp4"}  # not exported
    ]}

    resp = client.get(f"/projects/{pid}")
    body = resp.get_data(as_text=True)
    check("project_detail: YouTube checkbox HIDDEN for unexported clip's post", 'value="int-yt"' not in body)
    check("project_detail: LinkedIn checkbox still shown", 'value="int-li"' in body)
    check("project_detail: shows export-first hint", "isn&#39;t exported yet" in body or "isn't exported yet" in body)


def test_project_detail_shows_youtube_when_clip_exported():
    pid = make_project("Detail Exported Test")
    link_postiz()
    postiz_client.list_integrations = lambda group_id: INTEGRATIONS

    db = database.get_db()
    db.execute(
        "INSERT INTO posts (client_id, project_id, source, caption, status, hemingway_post_id, clip_id) VALUES (?, ?, 'project', ?, 'draft', ?, ?)",
        (CLIENT_ID, pid, "caption for exported clip", 112, 32)
    )
    db.commit()
    db.close()

    degas_client.get_project = lambda p: {"clips": [
        {"id": 32, "status": "exported", "filename": "exported_clip.mp4", "original_filename": "exported_clip.mp4"}
    ]}

    resp = client.get(f"/projects/{pid}")
    body = resp.get_data(as_text=True)
    check("project_detail: YouTube checkbox SHOWN for exported clip's post", 'value="int-yt"' in body)
    check("project_detail: YouTube title pre-filled from clip filename", 'value="exported_clip"' in body, body[:0])


# ── Scheduling route: media fetch/upload + blocking ─────────────────────────

def test_schedule_youtube_blocked_when_clip_not_exported():
    pid = make_project("Schedule Block Test")
    link_postiz()
    postiz_client.list_integrations = lambda group_id: INTEGRATIONS

    db = database.get_db()
    db.execute(
        "INSERT INTO posts (client_id, project_id, source, caption, status, hemingway_post_id, clip_id) VALUES (?, ?, 'project', ?, 'draft', ?, ?)",
        (CLIENT_ID, pid, "some caption", 113, 41)
    )
    db.commit()
    post_id = db.execute("SELECT id FROM posts WHERE hemingway_post_id = 113").fetchone()["id"]
    db.close()

    degas_client.get_clip_status = lambda p, c: {"status": "transcribed"}  # not exported

    resp = client.post(f"/quick-posts/{post_id}/schedule-to-postiz", data={
        "client_id": CLIENT_ID, "channel_ids": ["int-yt"], "send_at": "2026-08-01T10:00", "youtube_title": "Test"
    })
    check("schedule: blocked with 400 when clip not exported", resp.status_code == 400, resp.status_code)
    check("schedule: error mentions exporting first", b"exported yet" in resp.data, resp.data[:200])


def test_schedule_quick_post_blocked_for_media_required_no_clip():
    db = database.get_db()
    db.execute(
        "INSERT INTO posts (client_id, source, caption, media_ref, status, hemingway_post_id) VALUES (?, 'quick', ?, ?, 'draft', ?)",
        (CLIENT_ID, "quick post caption", "https://drive.google.com/x", 114)
    )
    db.commit()
    post_id = db.execute("SELECT id FROM posts WHERE hemingway_post_id = 114").fetchone()["id"]
    db.close()
    link_postiz()
    postiz_client.list_integrations = lambda group_id: INTEGRATIONS

    resp = client.post(f"/quick-posts/{post_id}/schedule-to-postiz", data={
        "client_id": CLIENT_ID, "channel_ids": ["int-yt"], "send_at": "2026-08-01T10:00", "youtube_title": "Test"
    })
    check("schedule: quick post (no clip_id) blocked from YouTube", resp.status_code == 400, resp.status_code)
    check("schedule: error explains only project posts qualify", b"only project-sourced posts" in resp.data, resp.data[:200])


def test_schedule_youtube_success_uploads_exported_clip():
    pid = make_project("Schedule Success Test")
    link_postiz()
    postiz_client.list_integrations = lambda group_id: INTEGRATIONS

    db = database.get_db()
    db.execute(
        "INSERT INTO posts (client_id, project_id, source, caption, status, hemingway_post_id, clip_id) VALUES (?, ?, 'project', ?, 'draft', ?, ?)",
        (CLIENT_ID, pid, "caption ready to post", 115, 51)
    )
    db.commit()
    post_id = db.execute("SELECT id FROM posts WHERE hemingway_post_id = 115").fetchone()["id"]
    db.close()

    degas_client.get_clip_status = lambda p, c: {"status": "exported"}

    class FakeDownloadResp:
        content = b"fake-video-bytes"
    degas_client.download_clip = lambda p, c: FakeDownloadResp()

    upload_calls = []
    def fake_upload(file_obj, filename, content_type):
        upload_calls.append((filename, content_type, file_obj.read()))
        return {"id": "media-1", "path": "/uploads/media-1"}
    postiz_client.upload_file = fake_upload

    create_post_calls = []
    def fake_create_post(post_type, date_iso, posts, **kw):
        create_post_calls.append(posts)
        return [{"postId": "p1", "integration": {"id": "int-yt"}}]
    postiz_client.create_post = fake_create_post

    resp = client.post(f"/quick-posts/{post_id}/schedule-to-postiz", data={
        "client_id": CLIENT_ID, "channel_ids": ["int-yt"], "send_at": "2026-08-01T10:00", "youtube_title": "My Great Clip"
    })
    check("schedule: redirects on success", resp.status_code == 302, resp.status_code)
    check("schedule: upload_file called exactly once", len(upload_calls) == 1, upload_calls)
    check("schedule: uploaded the exported clip's bytes", upload_calls[0][2] == b"fake-video-bytes" if upload_calls else False)
    check("schedule: youtube post item carries the uploaded media", create_post_calls and create_post_calls[0][0]["value"][0]["image"] == [{"id": "media-1", "path": "/uploads/media-1"}], create_post_calls)
    check("schedule: youtube title forwarded", create_post_calls and create_post_calls[0][0]["settings"]["title"] == "My Great Clip", create_post_calls)

    db = database.get_db()
    post = db.execute("SELECT * FROM posts WHERE id = ?", (post_id,)).fetchone()
    db.close()
    check("schedule: post marked scheduled", post["status"] == "scheduled", post["status"])


def test_schedule_text_only_channel_never_touches_media():
    pid = make_project("Text Only Schedule Test")
    link_postiz()
    postiz_client.list_integrations = lambda group_id: INTEGRATIONS

    db = database.get_db()
    db.execute(
        "INSERT INTO posts (client_id, project_id, source, caption, status, hemingway_post_id, clip_id) VALUES (?, ?, 'project', ?, 'draft', ?, ?)",
        (CLIENT_ID, pid, "text only caption", 116, 61)
    )
    db.commit()
    post_id = db.execute("SELECT id FROM posts WHERE hemingway_post_id = 116").fetchone()["id"]
    db.close()

    upload_calls = []
    postiz_client.upload_file = lambda *a, **kw: upload_calls.append(1) or {"id": "should-not-happen", "path": "x"}
    postiz_client.create_post = lambda post_type, date_iso, posts, **kw: [{"postId": "p2", "integration": {"id": "int-li"}}]
    degas_client.get_clip_status = lambda p, c: (_ for _ in ()).throw(AssertionError("should not check clip status for text-only channel"))

    resp = client.post(f"/quick-posts/{post_id}/schedule-to-postiz", data={
        "client_id": CLIENT_ID, "channel_ids": ["int-li"], "send_at": "2026-08-01T10:00"
    })
    check("schedule: text-only channel succeeds", resp.status_code == 302, resp.status_code)
    check("schedule: text-only channel never calls upload_file", len(upload_calls) == 0, upload_calls)


test_build_transcript_returns_ordered_clip_ids()
test_write_posts_stores_clip_id_per_post()
test_project_detail_shows_media_capable_channels_and_gating()
test_project_detail_shows_youtube_when_clip_exported()
test_schedule_youtube_blocked_when_clip_not_exported()
test_schedule_quick_post_blocked_for_media_required_no_clip()
test_schedule_youtube_success_uploads_exported_clip()
test_schedule_text_only_channel_never_touches_media()

print(f"\n{results['pass']} passed, {results['fail']} failed")
sys.exit(1 if results["fail"] else 0)
