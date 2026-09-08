"""
Tests for project assignment ("who is this assigned to", Ben's ask
2026-09-03). Assignee pool is the Access Codes list, not a separate names
table. Covers: default unassigned, assign/unassign, revoked codes still
show their label on already-assigned projects but drop out of the
assignable dropdown for new assignments.

Run: python test_project_assignment.py
"""

import os
import sys

sys.path.insert(0, os.path.dirname(__file__))
os.environ["DB_PATH"] = "/tmp/studio_test/data/test_project_assignment.db"
os.environ["APP_PASSWORD"] = "seed-password-123"
if os.path.exists(os.environ["DB_PATH"]):
    os.remove(os.environ["DB_PATH"])

import app as app_module
import database
import hemingway_client

database.init_db()

CLIENT_ID = 1
results = {"pass": 0, "fail": 0}


def check(name, cond, detail=""):
    if cond:
        results["pass"] += 1
        print(f"PASS  {name}")
    else:
        results["fail"] += 1
        print(f"FAIL  {name}  {detail}")


hemingway_client.get_clients = lambda: [{"id": CLIENT_ID, "name": "Test Co"}]

admin = app_module.app.test_client()
admin.post("/login", data={"password": "seed-password-123"})


def make_project(name):
    db = database.get_db()
    db.execute(
        "INSERT INTO projects (client_id, name, phase) VALUES (?, ?, 'intake')",
        (CLIENT_ID, name)
    )
    db.commit()
    pid = db.execute("SELECT id FROM projects WHERE name = ? ORDER BY id DESC LIMIT 1", (name,)).fetchone()["id"]
    db.close()
    return pid


def make_code(label, password):
    admin.post("/settings/access-codes/new", data={"label": label, "password": password})
    db = database.get_db()
    cid = db.execute("SELECT id FROM access_codes WHERE label = ?", (label,)).fetchone()["id"]
    db.close()
    return cid


def test_default_unassigned():
    pid = make_project("Default Unassigned")
    resp = admin.get(f"/dashboard?client_id={CLIENT_ID}")
    check("dashboard: 200 OK", resp.status_code == 200)
    db = database.get_db()
    row = db.execute("SELECT assigned_code_id FROM projects WHERE id = ?", (pid,)).fetchone()
    db.close()
    check("default: assigned_code_id is NULL", row["assigned_code_id"] is None)


def test_assign_and_dropdown_shows_selection():
    code_id = make_code("Video Editor", "vid-pass")
    pid = make_project("Assign Test")

    resp = admin.post(f"/projects/{pid}/assign", data={"client_id": CLIENT_ID, "assigned_code_id": str(code_id)})
    check("assign: redirects", resp.status_code == 302)

    db = database.get_db()
    row = db.execute("SELECT assigned_code_id FROM projects WHERE id = ?", (pid,)).fetchone()
    db.close()
    check("assign: saved to DB", row["assigned_code_id"] == code_id)

    resp = admin.get(f"/dashboard?client_id={CLIENT_ID}")
    check("dashboard: shows Video Editor as selected option",
          f'<option value="{code_id}" selected>Video Editor</option>'.encode() in resp.data)


def test_unassign_clears_it():
    code_id = make_code("Content Person", "content-pass")
    pid = make_project("Unassign Test")
    admin.post(f"/projects/{pid}/assign", data={"client_id": CLIENT_ID, "assigned_code_id": str(code_id)})

    resp = admin.post(f"/projects/{pid}/assign", data={"client_id": CLIENT_ID, "assigned_code_id": ""})
    check("unassign: redirects", resp.status_code == 302)

    db = database.get_db()
    row = db.execute("SELECT assigned_code_id FROM projects WHERE id = ?", (pid,)).fetchone()
    db.close()
    check("unassign: back to NULL", row["assigned_code_id"] is None)


def test_revoked_code_still_shows_label_on_assigned_project():
    code_id = make_code("Soon Revoked", "revoke-pass")
    pid = make_project("Revoked Assignee Test")
    admin.post(f"/projects/{pid}/assign", data={"client_id": CLIENT_ID, "assigned_code_id": str(code_id)})

    admin.post(f"/settings/access-codes/{code_id}/revoke")

    db = database.get_db()
    row = db.execute("SELECT assigned_code_id FROM projects WHERE id = ?", (pid,)).fetchone()
    db.close()
    check("revoke: assignment survives revocation (not cleared)", row["assigned_code_id"] == code_id)

    resp = admin.get(f"/dashboard?client_id={CLIENT_ID}")
    check("dashboard: still shows the person's label with (revoked) tag",
          b"Soon Revoked (revoked)" in resp.data)


def test_revoked_code_not_offered_for_new_assignments():
    code_id = make_code("Gone Now", "gone-pass")
    admin.post(f"/settings/access-codes/{code_id}/revoke")

    pid = make_project("Fresh Unassigned Project")
    resp = admin.get(f"/dashboard?client_id={CLIENT_ID}")
    # A fresh unassigned project's dropdown should NOT list the revoked
    # code as a normal option (only an already-assigned project falls back
    # to showing it with the "(revoked)" suffix).
    check("dashboard: revoked code absent from a fresh project's options",
          f'<option value="{code_id}" >Gone Now</option>'.encode() not in resp.data)


if __name__ == "__main__":
    test_default_unassigned()
    test_assign_and_dropdown_shows_selection()
    test_unassign_clears_it()
    test_revoked_code_still_shows_label_on_assigned_project()
    test_revoked_code_not_offered_for_new_assignments()

    print(f"\nTOTAL: {results['pass']} passed, {results['fail']} failed")
    if results["fail"]:
        sys.exit(1)
