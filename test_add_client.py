"""
Tests for adding a client directly from Studio (Ben's ask: "Can we make it
to where I can add new clients on the KMG Studio. From there I'd like it to
retroactively be added to Hemingway.").

Studio has no local clients table -- Hemingway is the single source of truth
(see hemingway_client.py). So "add from Studio, appears in Hemingway" isn't a
sync problem: the new /clients/new route just calls Hemingway's existing
POST /api/clients directly. There's nothing to keep in sync because there's
only ever one copy of the record.

degas_client and hemingway_client are mocked so this runs without either
real server.

Run with: python -m pytest test_add_client.py -v
(or just: python test_add_client.py)
"""

import os
import sys
from unittest.mock import patch

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))

os.environ.setdefault("SECRET_KEY", "test")
os.environ.setdefault("APP_PASSWORD", "test")
os.environ.setdefault("DB_PATH", "/tmp/studio_test_add_client.db")

import app as studio_app
import hemingway_client


def _setup_db(db_path):
    if os.path.exists(db_path):
        os.remove(db_path)
    studio_app.DB_PATH = db_path
    import database
    database.DB_PATH = db_path
    database.init_db()


def _client():
    client = studio_app.app.test_client()
    with client.session_transaction() as sess:
        sess["logged_in"] = True
    return client


def test_new_client_calls_hemingway_and_redirects_to_new_client_dashboard():
    db_path = "/tmp/studio_test_add_client_1.db"
    _setup_db(db_path)

    with patch.object(hemingway_client, "create_client", return_value={"id": 7, "name": "New Co"}) as mock_create, \
         patch.object(hemingway_client, "get_clients", return_value=[{"id": 7, "name": "New Co", "style_rules": ""}]):
        resp = _client().post("/clients/new", data={"name": "New Co"})

    mock_create.assert_called_once_with("New Co")
    assert resp.status_code == 302
    assert "client_id=7" in resp.headers["Location"]

    os.remove(db_path)


def test_new_client_strips_whitespace_and_rejects_blank_name():
    db_path = "/tmp/studio_test_add_client_2.db"
    _setup_db(db_path)

    with patch.object(hemingway_client, "create_client") as mock_create:
        resp = _client().post("/clients/new", data={"name": "   "})

    mock_create.assert_not_called()
    assert resp.status_code == 302  # redirected back to dashboard, no crash

    os.remove(db_path)


def test_new_client_surfaces_hemingway_errors_instead_of_crashing():
    db_path = "/tmp/studio_test_add_client_3.db"
    _setup_db(db_path)

    with patch.object(hemingway_client, "create_client",
                       side_effect=hemingway_client.HemingwayError("Couldn't reach Hemingway")):
        resp = _client().post("/clients/new", data={"name": "New Co"})

    assert resp.status_code == 502
    assert b"Couldn&#39;t reach Hemingway" in resp.data or b"Couldn't reach Hemingway" in resp.data

    os.remove(db_path)


def test_no_local_client_row_is_created_studio_stays_thin():
    # Guards the architecture: Studio must NOT grow a local clients table as
    # a side effect of this feature. If it ever does, every other place that
    # calls hemingway_client.get_clients() as the source of truth silently
    # starts reading stale data instead.
    db_path = "/tmp/studio_test_add_client_4.db"
    _setup_db(db_path)
    import database
    db = database.get_db()
    tables = {r["name"] for r in db.execute(
        "SELECT name FROM sqlite_master WHERE type='table'"
    )}
    db.close()
    assert "clients" not in tables, (
        "Studio grew a local 'clients' table -- it should keep calling "
        "Hemingway's /api/clients as the single source of truth instead"
    )
    os.remove(db_path)


if __name__ == '__main__':
    test_new_client_calls_hemingway_and_redirects_to_new_client_dashboard()
    print('PASS: test_new_client_calls_hemingway_and_redirects_to_new_client_dashboard')
    test_new_client_strips_whitespace_and_rejects_blank_name()
    print('PASS: test_new_client_strips_whitespace_and_rejects_blank_name')
    test_new_client_surfaces_hemingway_errors_instead_of_crashing()
    print('PASS: test_new_client_surfaces_hemingway_errors_instead_of_crashing')
    test_no_local_client_row_is_created_studio_stays_thin()
    print('PASS: test_no_local_client_row_is_created_studio_stays_thin')
    print('\nALL TESTS PASSED')
