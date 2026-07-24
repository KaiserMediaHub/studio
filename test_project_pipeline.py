import io
import os
import sys

sys.path.insert(0, os.path.dirname(__file__))
os.environ["DB_PATH"] = "/tmp/studio_test/data/test_studio_pipeline.db"
if os.path.exists(os.environ["DB_PATH"]):
    os.remove(os.environ["DB_PATH"])

import app as app_module
import database
import hemingway_client
import degas_client

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


def make_project(name="Test Project"):
    db = database.get_db()
    db.execute(
        "INSERT INTO projects (client_id, name, degas_project_id, phase) VALUES (?, ?, ?, 'intake')",
        (CLIENT_ID, name, DEGAS_PROJECT_ID)
    )
    db.commit()
    pid = db.execute("SELECT id FROM projects WHERE name = ? ORDER BY id DESC LIMIT 1", (name,)).fetchone()["id"]
    db.close()
    return pid


client = app_module.app.test_client()
with client.session_transaction() as sess:
    sess["logged_in"] = True


# ── Archive / Unarchive / Delete ─────────────────────────────────────────────

def test_archive_unarchive_delete():
    pid = make_project("Archive Test")

    resp = client.get(f"/dashboard?client_id={CLIENT_ID}")
    check("dashboard: new project visible by default", b"Archive Test" in resp.data)

    resp = client.post(f"/projects/{pid}/archive", data={"client_id": CLIENT_ID})
    check("archive: redirects", resp.status_code == 302)

    resp = client.get(f"/dashboard?client_id={CLIENT_ID}")
    check("dashboard: archived project hidden by default", b"Archive Test" not in resp.data)

    resp = client.get(f"/dashboard?client_id={CLIENT_ID}&show_archived=1")
    check("dashboard: archived project shown with show_archived=1", b"Archive Test" in resp.data)
    check("dashboard: archived pill rendered", b"Archived" in resp.data)

    resp = client.post(f"/projects/{pid}/unarchive", data={"client_id": CLIENT_ID})
    resp = client.get(f"/dashboard?client_id={CLIENT_ID}")
    check("unarchive: project visible again by default", b"Archive Test" in resp.data)

    resp = client.post(f"/projects/{pid}/delete", data={"client_id": CLIENT_ID})
    check("delete: redirects", resp.status_code == 302)
    db = database.get_db()
    row = db.execute("SELECT * FROM projects WHERE id = ?", (pid,)).fetchone()
    db.close()
    check("delete: project row actually gone", row is None)


def test_delete_unlinks_posts_not_deletes_them():
    pid = make_project("Delete With Posts")
    db = database.get_db()
    db.execute(
        "INSERT INTO posts (client_id, project_id, source, caption, status) VALUES (?, ?, 'project', 'hello', 'draft')",
        (CLIENT_ID, pid)
    )
    db.commit()
    post_id = db.execute("SELECT id FROM posts WHERE caption = 'hello'").fetchone()["id"]
    db.close()

    client.post(f"/projects/{pid}/delete", data={"client_id": CLIENT_ID})

    db = database.get_db()
    post = db.execute("SELECT * FROM posts WHERE id = ?", (post_id,)).fetchone()
    db.close()
    check("delete: post row survives project delete", post is not None)
    check("delete: post.project_id set NULL (FK ON DELETE SET NULL)", post is not None and post["project_id"] is None)


# ── Project detail page ──────────────────────────────────────────────────────

DEGAS_CLIPS = [
    {"id": 1, "filename": "a.mp4", "original_filename": "Intro.mp4", "status": "uploaded", "error_message": None, "style": None},
    {"id": 2, "filename": "b.mp4", "original_filename": "Story.mp4", "status": "transcribed", "error_message": None, "style": None},
    {"id": 3, "filename": "c.mp4", "original_filename": "CTA.mp4", "status": "exported", "error_message": None, "style": "1"},
]


def test_project_detail_renders_clips_and_actions():
    pid = make_project("Detail Test")
    degas_client.get_project = lambda pid_: {"id": pid_, "name": "x", "clips": DEGAS_CLIPS}

    resp = client.get(f"/projects/{pid}")
    body = resp.get_data(as_text=True)
    check("project_detail: 200 OK", resp.status_code == 200, f"status={resp.status_code}")
    check("project_detail: shows all 3 clip names", all(n in body for n in ["Intro.mp4", "Story.mp4", "CTA.mp4"]))
    check("project_detail: uploaded clip gets Transcribe button", "Transcribe</button>" in body)
    check("project_detail: transcribed/exported clips get Review link", body.count("Review</a>") == 2)
    check("project_detail: exported clip gets Download link", "Download</a>" in body)
    check("project_detail: Write Posts prompt shown (transcribed/exported clip present)", "Write posts from reviewed transcript" in body)


def test_project_detail_degas_error_banner():
    pid = make_project("Error Banner Test")

    def boom(pid_):
        raise degas_client.DegasError("Couldn't reach Degas")
    degas_client.get_project = boom

    resp = client.get(f"/projects/{pid}")
    body = resp.get_data(as_text=True)
    check("project_detail: 200 even when Degas errors", resp.status_code == 200)
    check("project_detail: shows Degas error banner", "Couldn't reach Degas" in body)


# ── Transcribe / status ──────────────────────────────────────────────────────

def test_transcribe_and_transcribe_all_and_status():
    pid = make_project("Transcribe Test")
    calls = {}
    degas_client.trigger_transcribe = lambda p, c: calls.setdefault("single", (p, c))
    degas_client.trigger_transcribe_all = lambda p: calls.setdefault("all", p)
    degas_client.get_clip_status = lambda p, c: {"status": "transcribing", "error": None, "elapsed": 12}

    resp = client.post(f"/projects/{pid}/clips/7/transcribe")
    check("clip_transcribe: redirects to project_detail", resp.status_code == 302 and f"/projects/{pid}" in resp.headers["Location"])
    check("clip_transcribe: calls degas_client with right ids", calls.get("single") == (DEGAS_PROJECT_ID, 7))

    resp = client.post(f"/projects/{pid}/transcribe-all")
    check("project_transcribe_all: calls degas_client with right project", calls.get("all") == DEGAS_PROJECT_ID)

    resp = client.get(f"/projects/{pid}/clips/7/status.json")
    check("clip_status_json: proxies Degas status JSON", resp.get_json() == {"status": "transcribing", "error": None, "elapsed": 12})


# ── Review / save ────────────────────────────────────────────────────────────

def test_review_page_and_save():
    pid = make_project("Review Test")
    degas_client.get_clip_segments = lambda p, c: {
        "current": [
            {"start": 0.0, "end": 2.0, "text": "Hello world"},
            {"start": 2.0, "end": 4.5, "text": "Second segment"},
        ],
        "original": [],
    }

    resp = client.get(f"/projects/{pid}/clips/9/review")
    body = resp.get_data(as_text=True)
    check("clip_review: 200 OK", resp.status_code == 200)
    check("clip_review: shows both segment texts", "Hello world" in body and "Second segment" in body)

    saved = {}
    def fake_save(p, c, segments):
        saved["args"] = (p, c, segments)
        return {"status": "saved"}
    degas_client.save_clip_segments = fake_save

    resp = client.post(f"/projects/{pid}/clips/9/review/save", data={
        "segment_text": ["Hello world EDITED", "Second segment"]
    })
    check("clip_review_save: redirects to project_detail", resp.status_code == 302 and f"/projects/{pid}" in resp.headers["Location"])
    p_arg, c_arg, segs = saved["args"]
    check("clip_review_save: correct project/clip ids forwarded", (p_arg, c_arg) == (DEGAS_PROJECT_ID, 9))
    check("clip_review_save: edited text merged in", segs[0]["text"] == "Hello world EDITED")
    check("clip_review_save: original start/end preserved", segs[0]["start"] == 0.0 and segs[0]["end"] == 2.0)
    check("clip_review_save: untouched segment text preserved", segs[1]["text"] == "Second segment")


# ── Export / export-all ──────────────────────────────────────────────────────

def test_export_and_export_all():
    pid = make_project("Export Test")
    calls = {}
    degas_client.trigger_export = lambda p, c, style: calls.setdefault("single", (p, c, style))
    degas_client.trigger_export_all = lambda p, style: calls.setdefault("all", (p, style))

    client.post(f"/projects/{pid}/clips/3/export", data={"style": "2"})
    check("clip_export: forwards project/clip/style", calls.get("single") == (DEGAS_PROJECT_ID, 3, "2"))

    client.post(f"/projects/{pid}/export-all", data={"style": "3"})
    check("project_export_all: forwards project/style", calls.get("all") == (DEGAS_PROJECT_ID, "3"))


# ── Download proxy ───────────────────────────────────────────────────────────

class FakeDegasResponse:
    def __init__(self, headers, chunks):
        self.headers = headers
        self._chunks = chunks

    def iter_content(self, chunk_size=8192):
        for c in self._chunks:
            yield c


def test_download_proxy_success_and_not_ready():
    pid = make_project("Download Test")
    degas_client.download_clip = lambda p, c: FakeDegasResponse(
        {"Content-Disposition": "attachment; filename=clip.mp4", "Content-Type": "video/mp4"},
        [b"fake", b"video", b"bytes"],
    )
    resp = client.get(f"/projects/{pid}/clips/3/download")
    check("clip_download: 200 with video content-type", resp.status_code == 200 and resp.content_type == "video/mp4")
    check("clip_download: streams the actual bytes", resp.data == b"fakevideobytes")

    degas_client.download_clip = lambda p, c: FakeDegasResponse({"Content-Type": "text/html"}, [b"<html>redirected</html>"])
    resp = client.get(f"/projects/{pid}/clips/3/download")
    body = resp.get_data(as_text=True)
    check("clip_download: not-ready case surfaces a clear message, not raw HTML", "isn" in body and "t exported yet" in body)


# ── Upload chunk proxy ───────────────────────────────────────────────────────

def test_upload_chunk_proxy():
    pid = make_project("Upload Test")
    seen = {}
    def fake_upload(p, file_uid, chunk_index, total_chunks, filename, data, content_type):
        seen["args"] = (p, file_uid, chunk_index, total_chunks, filename, data, content_type)
        return {"status": "chunk_received", "chunks": 1, "total": 2}
    degas_client.upload_chunk = fake_upload

    resp = client.post(f"/projects/{pid}/upload-chunk", data={
        "file_uid": "abc123",
        "chunk_index": "0",
        "total_chunks": "2",
        "filename": "clip.mp4",
        "data": (io.BytesIO(b"chunk-bytes"), "clip.mp4"),
    }, content_type="multipart/form-data")
    check("upload_chunk proxy: 200 OK", resp.status_code == 200)
    check("upload_chunk proxy: returns Degas's JSON as-is", resp.get_json() == {"status": "chunk_received", "chunks": 1, "total": 2})
    p_arg, file_uid, chunk_index, total_chunks, filename, data, content_type = seen["args"]
    check("upload_chunk proxy: forwards correct degas_project_id", p_arg == DEGAS_PROJECT_ID)
    check("upload_chunk proxy: forwards chunk bytes", data == b"chunk-bytes")


# ── Write posts ───────────────────────────────────────────────────────────────

def test_write_posts_success():
    pid = make_project("Write Posts Test")
    degas_client.get_project = lambda p: {"id": p, "clips": DEGAS_CLIPS}
    degas_client.get_clip_segments = lambda p, c: {
        "current": [{"start": 0, "end": 1, "text": f"segment for clip {c}"}]
    }
    generate_calls = {}
    def fake_generate(client_id, transcript, style, length, context="", name=""):
        generate_calls["args"] = (client_id, transcript, style, length, name)
        generate_calls["context"] = context
        return {"batch_id": 999, "posts": [
            {"id": 501, "title": "Intro", "body": "Post body one", "error": None},
            {"id": 502, "title": "Story", "body": "Post body two", "error": None},
        ]}
    hemingway_client.generate_from_transcript = fake_generate

    resp = client.post(f"/projects/{pid}/write-posts", data={"style": "conversational", "length": "short", "context": "launch week"})
    check("write_posts: redirects to project_detail", resp.status_code == 302 and f"/projects/{pid}" in resp.headers["Location"])
    check("write_posts: context forwarded", generate_calls.get("context") == "launch week", generate_calls.get("context"))

    client_id_arg, transcript_arg, style_arg, length_arg, name_arg = generate_calls["args"]
    check("write_posts: passes correct client_id", client_id_arg == CLIENT_ID)
    check("write_posts: transcript includes VIDEO: markers", "VIDEO: 01" in transcript_arg and "VIDEO: 02" in transcript_arg)
    check("write_posts: transcript excludes the uploaded-only clip (no transcript yet)", "Intro.mp4" not in transcript_arg or "VIDEO: 01 - Story" in transcript_arg)

    db = database.get_db()
    proj = db.execute("SELECT * FROM projects WHERE id = ?", (pid,)).fetchone()
    posts = db.execute("SELECT * FROM posts WHERE project_id = ?", (pid,)).fetchall()
    db.close()
    check("write_posts: project.hemingway_batch_id stored", proj["hemingway_batch_id"] == 999)
    check("write_posts: project phase advanced to drafting", proj["phase"] == "drafting")
    check("write_posts: both generated posts inserted with source='project'", len(posts) == 2 and all(p["source"] == "project" for p in posts))
    check("write_posts: posts linked to this project_id", all(p["project_id"] == pid for p in posts))


def test_write_posts_no_reviewed_transcript():
    pid = make_project("Write Posts Empty Test")
    degas_client.get_project = lambda p: {"id": p, "clips": [
        {"id": 1, "filename": "a.mp4", "original_filename": "a.mp4", "status": "uploaded", "error_message": None, "style": None}
    ]}
    resp = client.post(f"/projects/{pid}/write-posts", data={"style": "conversational", "length": "short"})
    body = resp.get_data(as_text=True)
    check("write_posts: 400 when nothing transcribed/reviewed yet", resp.status_code == 400)
    check("write_posts: clear message about needing a reviewed transcript", "reviewed transcript" in body)


def test_write_posts_not_linked_to_degas():
    db = database.get_db()
    db.execute("INSERT INTO projects (client_id, name, degas_project_id, phase) VALUES (?, 'No Degas', NULL, 'intake')", (CLIENT_ID,))
    db.commit()
    pid = db.execute("SELECT id FROM projects WHERE name = 'No Degas'").fetchone()["id"]
    db.close()
    resp = client.post(f"/projects/{pid}/write-posts", data={"style": "conversational", "length": "short"})
    check("write_posts: 400 when project not linked to Degas", resp.status_code == 400)


# ── Project-sourced post actions redirect to project, not Quick Posts ───────

def test_project_post_actions_redirect_to_project():
    pid = make_project("Redirect Test")
    db = database.get_db()
    db.execute(
        """INSERT INTO posts (client_id, project_id, source, caption, status, hemingway_post_id)
           VALUES (?, ?, 'project', 'original caption', 'draft', 777)""",
        (CLIENT_ID, pid)
    )
    db.commit()
    post_id = db.execute("SELECT id FROM posts WHERE caption = 'original caption'").fetchone()["id"]
    db.close()

    resp = client.post(f"/quick-posts/{post_id}/edit", data={"client_id": CLIENT_ID, "caption": "edited caption"})
    check("quick_posts_edit: project-sourced post redirects to project_detail", resp.status_code == 302 and f"/projects/{pid}" in resp.headers["Location"])

    hemingway_client.rewrite_post = lambda hpid, instruction="": {"id": hpid, "title": "x", "body": "regenerated body"}
    resp = client.post(f"/quick-posts/{post_id}/regenerate", data={"client_id": CLIENT_ID, "instruction": ""})
    check("quick_posts_regenerate: project-sourced post redirects to project_detail", resp.status_code == 302 and f"/projects/{pid}" in resp.headers["Location"])


# ── Run all ───────────────────────────────────────────────────────────────────

test_archive_unarchive_delete()
test_delete_unlinks_posts_not_deletes_them()
test_project_detail_renders_clips_and_actions()
test_project_detail_degas_error_banner()
test_transcribe_and_transcribe_all_and_status()
test_review_page_and_save()
test_export_and_export_all()
test_download_proxy_success_and_not_ready()
test_upload_chunk_proxy()
test_write_posts_success()
test_write_posts_no_reviewed_transcript()
test_write_posts_not_linked_to_degas()
test_project_post_actions_redirect_to_project()

print()
print(f"TOTAL: {results['pass']} passed, {results['fail']} failed")
if results["fail"]:
    sys.exit(1)
