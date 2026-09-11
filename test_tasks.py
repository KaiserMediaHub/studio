"""
Tests for the internal team Tasks tracker (Ben's ask, 2026-09-11): "I think
we need to build the project management tool next." Deliberately separate
from the client_id-scoped `projects` table (Studio's video-content
pipeline) -- see database.py's `task_projects`/`tasks` tables. List view
with assignees + due dates, not a kanban board, per Ben's explicit choice.
Assignee pool reuses the Access Codes identity system, same pattern as
project assignment (test_project_assignment.py).

Covers:
- creating a task project and a task within it
- default unassigned/todo state
- status transitions (todo/in_progress/done) and completed_at bookkeeping
- assignee and due-date updates
- delete
- archived projects drop out of the main Tasks page but keep their data
- the cross-project "what's due" list excludes done tasks and archived
  projects, and sorts soonest-due-first with undated tasks last

Run: python test_tasks.py
"""

import os
import sys

sys.path.insert(0, os.path.dirname(__file__))
os.environ["DB_PATH"] = "/tmp/studio_test/data/test_tasks.db"
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


def make_task_project(name):
    resp = admin.post("/tasks/projects/new", data={"name": name})
    db = database.get_db()
    pid = db.execute(
        "SELECT id FROM task_projects WHERE name = ? ORDER BY id DESC LIMIT 1", (name,)
    ).fetchone()["id"]
    db.close()
    return resp, pid


def make_task(project_id, title, assigned_code_id=None, due_date=None):
    data = {"title": title}
    if assigned_code_id is not None:
        data["assigned_code_id"] = str(assigned_code_id)
    if due_date is not None:
        data["due_date"] = due_date
    admin.post(f"/tasks/projects/{project_id}/tasks/new", data=data)
    db = database.get_db()
    tid = db.execute(
        "SELECT id FROM tasks WHERE task_project_id = ? AND title = ? ORDER BY id DESC LIMIT 1",
        (project_id, title)
    ).fetchone()["id"]
    db.close()
    return tid


def make_code(label, password):
    admin.post("/settings/access-codes/new", data={"label": label, "password": password})
    db = database.get_db()
    cid = db.execute("SELECT id FROM access_codes WHERE label = ?", (label,)).fetchone()["id"]
    db.close()
    return cid


def test_create_task_project():
    resp, pid = make_task_project("Q4 Marketing Push")
    check("create project: redirects", resp.status_code == 302)
    resp = admin.get("/tasks")
    check("tasks page: 200 OK", resp.status_code == 200)
    check("tasks page: lists the new project", b"Q4 Marketing Push" in resp.data)


def test_create_task_default_unassigned_todo():
    _, pid = make_task_project("Website Redesign")
    tid = make_task(pid, "Draft new homepage copy")

    db = database.get_db()
    row = db.execute("SELECT * FROM tasks WHERE id = ?", (tid,)).fetchone()
    db.close()
    check("default: status is todo", row["status"] == "todo")
    check("default: unassigned", row["assigned_code_id"] is None)
    check("default: no due date", row["due_date"] is None)
    check("default: not completed", row["completed_at"] is None)


def test_create_task_with_assignee_and_due_date():
    code_id = make_code("Copywriter", "copy-pass")
    _, pid = make_task_project("Client Onboarding")
    tid = make_task(pid, "Write welcome email", assigned_code_id=code_id, due_date="2026-09-20")

    db = database.get_db()
    row = db.execute("SELECT * FROM tasks WHERE id = ?", (tid,)).fetchone()
    db.close()
    check("assigned on create", row["assigned_code_id"] == code_id)
    check("due date on create", row["due_date"] == "2026-09-20")


def test_status_transition_to_done_sets_completed_at():
    _, pid = make_task_project("Ops Cleanup")
    tid = make_task(pid, "Archive old client folders")

    resp = admin.post(f"/tasks/{tid}/update", data={"status": "done"})
    check("status update: redirects", resp.status_code == 302)

    db = database.get_db()
    row = db.execute("SELECT * FROM tasks WHERE id = ?", (tid,)).fetchone()
    db.close()
    check("done: status saved", row["status"] == "done")
    check("done: completed_at set", row["completed_at"] is not None)


def test_status_transition_back_to_todo_clears_completed_at():
    _, pid = make_task_project("Reopen Test")
    tid = make_task(pid, "Something reopened")
    admin.post(f"/tasks/{tid}/update", data={"status": "done"})

    admin.post(f"/tasks/{tid}/update", data={"status": "todo"})
    db = database.get_db()
    row = db.execute("SELECT * FROM tasks WHERE id = ?", (tid,)).fetchone()
    db.close()
    check("reopen: status back to todo", row["status"] == "todo")
    check("reopen: completed_at cleared", row["completed_at"] is None)


def test_update_assignee_and_due_date():
    code_id = make_code("Video Editor 2", "vid2-pass")
    _, pid = make_task_project("Assignment Update Test")
    tid = make_task(pid, "Edit promo reel")

    admin.post(f"/tasks/{tid}/update", data={"assigned_code_id": str(code_id)})
    admin.post(f"/tasks/{tid}/update", data={"due_date": "2026-10-01"})

    db = database.get_db()
    row = db.execute("SELECT * FROM tasks WHERE id = ?", (tid,)).fetchone()
    db.close()
    check("assignee updated", row["assigned_code_id"] == code_id)
    check("due date updated", row["due_date"] == "2026-10-01")

    # Clearing back to unassigned/no-due-date must also work (empty string -> NULL).
    admin.post(f"/tasks/{tid}/update", data={"assigned_code_id": ""})
    admin.post(f"/tasks/{tid}/update", data={"due_date": ""})
    db = database.get_db()
    row = db.execute("SELECT * FROM tasks WHERE id = ?", (tid,)).fetchone()
    db.close()
    check("assignee cleared back to NULL", row["assigned_code_id"] is None)
    check("due date cleared back to NULL", row["due_date"] is None)


def test_delete_task():
    _, pid = make_task_project("Delete Test")
    tid = make_task(pid, "Task to delete")

    resp = admin.post(f"/tasks/{tid}/delete", data={})
    check("delete: redirects", resp.status_code == 302)

    db = database.get_db()
    row = db.execute("SELECT * FROM tasks WHERE id = ?", (tid,)).fetchone()
    db.close()
    check("delete: row gone", row is None)


def test_archive_project_hides_from_tasks_page_but_keeps_data():
    _, pid = make_task_project("Archive Me")
    tid = make_task(pid, "A task in an archived project")

    resp = admin.post(f"/tasks/projects/{pid}/archive", data={})
    check("archive: redirects", resp.status_code == 302)

    resp = admin.get("/tasks")
    check("archived project no longer listed on /tasks", b"Archive Me" not in resp.data)

    # Data survives -- the project detail page and its task are still there.
    resp = admin.get(f"/tasks/projects/{pid}")
    check("archived project detail still reachable", resp.status_code == 200)
    check("archived project's task still shown on its own page", b"A task in an archived project" in resp.data)


def test_open_tasks_excludes_done_and_sorts_by_due_date_nulls_last():
    _, pid = make_task_project("Sort Order Test")
    make_task(pid, "No due date task")
    make_task(pid, "Due later", due_date="2026-12-01")
    make_task(pid, "Due sooner", due_date="2026-09-15")
    done_tid = make_task(pid, "Already done task", due_date="2026-09-01")
    admin.post(f"/tasks/{done_tid}/update", data={"status": "done"})

    resp = admin.get("/tasks")
    body = resp.data.decode()
    check("done task excluded from What's due", "Already done task" not in body)

    sooner_pos = body.find("Due sooner")
    later_pos = body.find("Due later")
    nodate_pos = body.find("No due date task")
    check("sooner-due task appears before later-due task",
          -1 < sooner_pos < later_pos, f"sooner={sooner_pos} later={later_pos}")
    check("undated task appears after both dated tasks",
          later_pos < nodate_pos, f"later={later_pos} nodate={nodate_pos}")


def test_open_tasks_excludes_archived_project_tasks():
    _, pid = make_task_project("Archived Source Project")
    make_task(pid, "Task that should disappear from dashboard")
    admin.post(f"/tasks/projects/{pid}/archive", data={})

    resp = admin.get("/tasks")
    check("archived project's open task not on What's due",
          b"Task that should disappear from dashboard" not in resp.data)


def test_tasks_nav_link_reachable_from_dashboard():
    resp = admin.get(f"/dashboard?client_id={CLIENT_ID}")
    check("dashboard 200", resp.status_code == 200)
    check("dashboard sidebar links to /tasks", b'href="/tasks"' in resp.data)


if __name__ == "__main__":
    test_create_task_project()
    test_create_task_default_unassigned_todo()
    test_create_task_with_assignee_and_due_date()
    test_status_transition_to_done_sets_completed_at()
    test_status_transition_back_to_todo_clears_completed_at()
    test_update_assignee_and_due_date()
    test_delete_task()
    test_archive_project_hides_from_tasks_page_but_keeps_data()
    test_open_tasks_excludes_done_and_sorts_by_due_date_nulls_last()
    test_open_tasks_excludes_archived_project_tasks()
    test_tasks_nav_link_reachable_from_dashboard()

    print(f"\nTOTAL: {results['pass']} passed, {results['fail']} failed")
    if results["fail"]:
        sys.exit(1)
