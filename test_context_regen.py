import os
import sys

sys.path.insert(0, os.path.dirname(__file__))
os.environ["DB_PATH"] = "/tmp/studio_test/data/test_studio_context.db"
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

client = app_module.app.test_client()
with client.session_transaction() as sess:
    sess["logged_in"] = True


def make_project(name="Ctx Test"):
    db = database.get_db()
    db.execute(
        "INSERT INTO projects (client_id, name, degas_project_id, phase) VALUES (?, ?, ?, 'caption_review')",
        (CLIENT_ID, name, DEGAS_PROJECT_ID)
    )
    db.commit()
    pid = db.execute("SELECT id FROM projects WHERE name = ? ORDER BY id DESC LIMIT 1", (name,)).fetchone()["id"]
    db.close()
    return pid


# ── Quick Posts: context field renders + is forwarded ───────────────────────

def test_quick_posts_form_has_context_field():
    resp = client.get(f"/clients/{CLIENT_ID}/quick-posts")
    check("quick_posts.html: renders context textarea", b'name="context"' in resp.data)


def test_quick_posts_new_forwards_context():
    seen = {}

    def fake_generate_single_post(client_id, notes, style="conversational", length="short", context=""):
        seen["client_id"] = client_id
        seen["notes"] = notes
        seen["context"] = context
        return {"batch_id": 1, "post_id": 99, "body": "generated caption", "error": None}

    hemingway_client.generate_single_post = fake_generate_single_post
    resp = client.post(
        f"/clients/{CLIENT_ID}/quick-posts/new",
        data={"drive_url": "https://drive.google.com/x", "notes": "a video about the new gym",
              "style": "conversational", "length": "short", "context": "avoid mentioning pricing"},
    )
    check("quick_posts_new: redirects on success", resp.status_code == 302, resp.status_code)
    check("quick_posts_new: context forwarded to Hemingway", seen.get("context") == "avoid mentioning pricing", seen.get("context"))


def test_quick_posts_new_empty_context_still_works():
    def fake_generate_single_post(client_id, notes, style="conversational", length="short", context=""):
        assert context == "", f"expected empty context, got {context!r}"
        return {"batch_id": 1, "post_id": 100, "body": "caption", "error": None}

    hemingway_client.generate_single_post = fake_generate_single_post
    resp = client.post(
        f"/clients/{CLIENT_ID}/quick-posts/new",
        data={"drive_url": "", "notes": "some notes", "style": "conversational", "length": "short"},
    )
    check("quick_posts_new: works fine with no context field submitted", resp.status_code == 302, resp.status_code)


# ── Project pipeline: context field renders + is forwarded ──────────────────

def test_project_detail_form_has_context_field():
    pid = make_project("Ctx Render Test")

    def fake_get_project(degas_project_id):
        return {"clips": [{"id": 1, "status": "transcribed", "filename": "a.mp4", "original_filename": "a.mp4"}]}
    degas_client.get_project = fake_get_project

    resp = client.get(f"/projects/{pid}")
    check("project_detail.html: renders context textarea", b'name="context"' in resp.data)


def test_project_write_posts_forwards_context():
    pid = make_project("Ctx Forward Test")
    seen = {}

    def fake_get_project(degas_project_id):
        return {"clips": [{"id": 1, "status": "transcribed", "filename": "a.mp4", "original_filename": "a.mp4"}]}
    degas_client.get_project = fake_get_project

    def fake_build_transcript(degas_project_id, clips):
        return "VIDEO: 01 - a.mp4\nSome real transcript text.", [1]
    app_module._build_project_transcript = fake_build_transcript

    def fake_generate_from_transcript(client_id, transcript, style="conversational", length="short", context="", name=""):
        seen["client_id"] = client_id
        seen["context"] = context
        seen["name"] = name
        return {"batch_id": 55, "posts": [{"id": 1, "body": "post body"}]}

    hemingway_client.generate_from_transcript = fake_generate_from_transcript

    resp = client.post(
        f"/projects/{pid}/write-posts",
        data={"style": "conversational", "length": "short", "context": "this is about the studio opening"},
    )
    check("project_write_posts: redirects on success", resp.status_code == 302, resp.status_code)
    check("project_write_posts: context forwarded to Hemingway", seen.get("context") == "this is about the studio opening", seen.get("context"))
    check("project_write_posts: client_id still correct", seen.get("client_id") == CLIENT_ID, seen.get("client_id"))


# ── Regenerate box: still present and unbroken in both screens ──────────────

def test_regenerate_box_present_in_both_screens():
    resp = client.get(f"/clients/{CLIENT_ID}/quick-posts")
    check("quick_posts.html: quick_posts_regenerate route referenced", b"/regenerate" in resp.data)

    pid = make_project("Regen Render Test")
    db = database.get_db()
    db.execute(
        "INSERT INTO posts (client_id, project_id, source, caption, status, hemingway_post_id) VALUES (?, ?, 'project', 'hi', 'draft', ?)",
        (CLIENT_ID, pid, 123)
    )
    db.commit()
    db.close()

    def fake_get_project(degas_project_id):
        return {"clips": []}
    degas_client.get_project = fake_get_project

    resp = client.get(f"/projects/{pid}")
    check("project_detail.html: regenerate form referenced", b"/regenerate" in resp.data)
    check("project_detail.html: regenerate label present", b"Add feedback and regenerate" in resp.data)


test_quick_posts_form_has_context_field()
test_quick_posts_new_forwards_context()
test_quick_posts_new_empty_context_still_works()
test_project_detail_form_has_context_field()
test_project_write_posts_forwards_context()
test_regenerate_box_present_in_both_screens()

print(f"\n{results['pass']} passed, {results['fail']} failed")
sys.exit(1 if results["fail"] else 0)
