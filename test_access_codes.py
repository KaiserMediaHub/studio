"""
Tests for revocable access codes (Ben's ask, 2026-09-03): replaces the
single shared APP_PASSWORD with a table of labeled, independently-revocable
passwords. Covers: seeding from APP_PASSWORD, login with any active code,
revoked codes can't log in, revocation is real-time (kicks an active
session immediately), admin-only management page, can't strand yourself
with zero active admins.

Run: python test_access_codes.py
"""

import os
import sys

sys.path.insert(0, os.path.dirname(__file__))
os.environ["DB_PATH"] = "/tmp/studio_test/data/test_access_codes.db"
os.environ["APP_PASSWORD"] = "seed-password-123"
if os.path.exists(os.environ["DB_PATH"]):
    os.remove(os.environ["DB_PATH"])

import app as app_module
import database
import hemingway_client

database.init_db()

hemingway_client.get_clients = lambda: [{"id": 1, "name": "Test Co"}]

results = {"pass": 0, "fail": 0}


def check(name, cond, detail=""):
    if cond:
        results["pass"] += 1
        print(f"PASS  {name}")
    else:
        results["fail"] += 1
        print(f"FAIL  {name}  {detail}")


def fresh_client():
    return app_module.app.test_client()


def test_seeded_admin_code_works_with_original_password():
    c = fresh_client()
    resp = c.post("/login", data={"password": "seed-password-123"}, follow_redirects=False)
    check("seed: original APP_PASSWORD still logs in", resp.status_code == 302 and resp.headers["Location"].endswith("/dashboard"))

    with c.session_transaction() as sess:
        check("seed: session marked admin", sess.get("is_admin") is True)
        check("seed: session has an access_code_id", sess.get("access_code_id") is not None)


def test_wrong_password_rejected():
    c = fresh_client()
    resp = c.post("/login", data={"password": "definitely-wrong"})
    check("wrong password: 200 with error, no redirect", resp.status_code == 200)
    check("wrong password: error message shown", b"Incorrect password" in resp.data)


def test_admin_can_create_new_code_and_it_logs_in():
    admin = fresh_client()
    admin.post("/login", data={"password": "seed-password-123"})
    resp = admin.post("/settings/access-codes/new", data={"label": "Video Editor", "password": "vid-pass-456"})
    check("create: redirects back to access codes page", resp.status_code == 302)

    new_user = fresh_client()
    resp = new_user.post("/login", data={"password": "vid-pass-456"}, follow_redirects=False)
    check("new code: logs in successfully", resp.status_code == 302 and resp.headers["Location"].endswith("/dashboard"))
    with new_user.session_transaction() as sess:
        check("new code: session NOT marked admin", sess.get("is_admin") is False)


def test_non_admin_cannot_see_management_page():
    admin = fresh_client()
    admin.post("/login", data={"password": "seed-password-123"})
    admin.post("/settings/access-codes/new", data={"label": "Content Person", "password": "content-pass"})

    regular = fresh_client()
    regular.post("/login", data={"password": "content-pass"})
    resp = regular.get("/settings/access-codes")
    check("non-admin: 403 on access-codes page", resp.status_code == 403)

    resp = regular.post("/settings/access-codes/new", data={"label": "x", "password": "y"})
    check("non-admin: 403 on creating a code", resp.status_code == 403)


def test_revoked_code_cannot_log_in():
    admin = fresh_client()
    admin.post("/login", data={"password": "seed-password-123"})
    admin.post("/settings/access-codes/new", data={"label": "To Revoke", "password": "revoke-me-pass"})

    db = database.get_db()
    code_id = db.execute("SELECT id FROM access_codes WHERE label = 'To Revoke'").fetchone()["id"]
    db.close()

    admin.post(f"/settings/access-codes/{code_id}/revoke")

    victim = fresh_client()
    resp = victim.post("/login", data={"password": "revoke-me-pass"})
    check("revoked code: cannot log in", resp.status_code == 200 and b"Incorrect password" in resp.data)


def test_revocation_is_real_time_kicks_active_session():
    """The important one: a session already logged in with a code that gets
    revoked mid-session must be kicked out on its VERY NEXT request, not
    just blocked from logging in again."""
    admin = fresh_client()
    admin.post("/login", data={"password": "seed-password-123"})
    admin.post("/settings/access-codes/new", data={"label": "Live Session", "password": "live-pass"})

    victim = fresh_client()
    victim.post("/login", data={"password": "live-pass"})
    resp = victim.get("/dashboard")
    check("before revoke: session still works", resp.status_code == 200)

    db = database.get_db()
    code_id = db.execute("SELECT id FROM access_codes WHERE label = 'Live Session'").fetchone()["id"]
    db.close()
    admin.post(f"/settings/access-codes/{code_id}/revoke")

    resp = victim.get("/dashboard", follow_redirects=False)
    check("after revoke: same session immediately bounced to login", resp.status_code == 302 and resp.headers["Location"].endswith("/login"))


def test_cannot_revoke_last_active_admin():
    c = fresh_client()
    c.post("/login", data={"password": "seed-password-123"})
    db = database.get_db()
    admin_id = db.execute("SELECT id FROM access_codes WHERE label = 'Admin (original)'").fetchone()["id"]
    active_admins = db.execute("SELECT COUNT(*) AS n FROM access_codes WHERE is_admin=1 AND revoked_at IS NULL").fetchone()["n"]
    db.close()
    check("sanity: exactly one active admin before this test", active_admins == 1, active_admins)

    resp = c.post(f"/settings/access-codes/{admin_id}/revoke")
    check("cannot strand: revoking last admin is rejected", resp.status_code == 400)

    db = database.get_db()
    row = db.execute("SELECT revoked_at FROM access_codes WHERE id = ?", (admin_id,)).fetchone()
    db.close()
    check("cannot strand: admin code still active in DB", row["revoked_at"] is None)


def test_second_admin_allows_revoking_first():
    c = fresh_client()
    c.post("/login", data={"password": "seed-password-123"})
    c.post("/settings/access-codes/new", data={"label": "Second Admin", "password": "second-admin-pass", "is_admin": "on"})

    db = database.get_db()
    original_id = db.execute("SELECT id FROM access_codes WHERE label = 'Admin (original)'").fetchone()["id"]
    db.close()

    # Important: revoke using the SECOND admin's own session, not `c` (which
    # is still logged in as the code we're about to revoke) -- revoking
    # your own active code immediately kicks your own session per the
    # real-time check, which would make the follow-up unrevoke silently
    # bounce to /login instead of actually running.
    second_admin = fresh_client()
    second_admin.post("/login", data={"password": "second-admin-pass"})

    resp = second_admin.post(f"/settings/access-codes/{original_id}/revoke")
    check("with 2 admins: revoking one succeeds", resp.status_code == 302)

    # Restore original admin so later tests (which log in with the seed
    # password) aren't broken by this test's side effect -- these tests
    # share one persistent DB across the whole file, same pattern as
    # test_project_pipeline.py.
    second_admin.post(f"/settings/access-codes/{original_id}/unrevoke")


def test_unrevoke_restores_login():
    admin = fresh_client()
    admin.post("/login", data={"password": "seed-password-123"})
    admin.post("/settings/access-codes/new", data={"label": "Unrevoke Test", "password": "unrevoke-pass"})
    db = database.get_db()
    code_id = db.execute("SELECT id FROM access_codes WHERE label = 'Unrevoke Test'").fetchone()["id"]
    db.close()
    admin.post(f"/settings/access-codes/{code_id}/revoke")

    blocked = fresh_client()
    resp = blocked.post("/login", data={"password": "unrevoke-pass"})
    check("unrevoke test: blocked while revoked", b"Incorrect password" in resp.data)

    admin.post(f"/settings/access-codes/{code_id}/unrevoke")
    restored = fresh_client()
    resp = restored.post("/login", data={"password": "unrevoke-pass"}, follow_redirects=False)
    check("unrevoke test: works again after restore", resp.status_code == 302 and resp.headers["Location"].endswith("/dashboard"))


def test_passwords_stored_hashed_not_plaintext():
    db = database.get_db()
    row = db.execute("SELECT password_hash FROM access_codes WHERE label = 'Admin (original)'").fetchone()
    db.close()
    check("security: password not stored in plaintext", "seed-password-123" not in row["password_hash"])
    check("security: looks like a werkzeug hash", row["password_hash"].startswith(("pbkdf2:", "scrypt:")))


if __name__ == "__main__":
    test_seeded_admin_code_works_with_original_password()
    test_wrong_password_rejected()
    test_admin_can_create_new_code_and_it_logs_in()
    test_non_admin_cannot_see_management_page()
    test_revoked_code_cannot_log_in()
    test_revocation_is_real_time_kicks_active_session()
    test_cannot_revoke_last_active_admin()
    test_second_admin_allows_revoking_first()
    test_unrevoke_restores_login()
    test_passwords_stored_hashed_not_plaintext()

    print(f"\nTOTAL: {results['pass']} passed, {results['fail']} failed")
    if results["fail"]:
        sys.exit(1)
