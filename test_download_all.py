"""
Tests for the "Download All" button (project_download_all route) -- zips
every exported clip in a project into one download. Built to a temp file on
disk rather than buffered in memory, since projects can have several large
video files and this server doesn't have RAM to spare (see Degas's
transcription.py accuracy-history comment for how close that got once).

degas_client is mocked so this runs without a real Degas server.

Run with: python -m pytest test_download_all.py -v
(or just: python test_download_all.py)
"""

import io
import os
import sys
import zipfile
from unittest.mock import patch, MagicMock

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))

os.environ.setdefault("SECRET_KEY", "test")
os.environ.setdefault("APP_PASSWORD", "test")
os.environ.setdefault("DB_PATH", "/tmp/studio_test_download_all.db")

import app as studio_app
import degas_client


def _fake_download_resp(content, content_disposition="attachment; filename=clip.mp4"):
    resp = MagicMock()
    resp.headers = {"Content-Disposition": content_disposition, "Content-Type": "video/mp4"}
    resp.iter_content = lambda chunk_size=8192: iter([content])
    return resp


def _setup_project(db_path):
    if os.path.exists(db_path):
        os.remove(db_path)
    studio_app.DB_PATH = db_path
    import database
    database.DB_PATH = db_path
    database.init_db()
    db = database.get_db()
    db.execute(
        "INSERT INTO projects (id, client_id, name, degas_project_id) VALUES (1, 1, 'Epiphany Launch', 42)"
    )
    db.commit()
    db.close()


def test_download_all_builds_zip_with_all_exported_clips():
    db_path = "/tmp/studio_test_download_all_1.db"
    _setup_project(db_path)

    fake_project = {
        "clips": [
            {"id": 1, "status": "exported", "original_filename": "intro.mp4"},
            {"id": 2, "status": "exported", "original_filename": "outro.mp4"},
            {"id": 3, "status": "transcribed", "original_filename": "raw_only.mp4"},  # not exported -- skipped
        ]
    }

    with patch.object(degas_client, "get_project", return_value=fake_project), \
         patch.object(degas_client, "download_clip", side_effect=[
             _fake_download_resp(b"INTRO_VIDEO_BYTES"),
             _fake_download_resp(b"OUTRO_VIDEO_BYTES"),
         ]):
        client = studio_app.app.test_client()
        with client.session_transaction() as sess:
            sess["logged_in"] = True
        resp = client.get("/projects/1/clips/download-all")

    assert resp.status_code == 200, resp.data
    assert resp.headers["Content-Type"] == "application/zip"
    assert "Epiphany Launch - clips.zip" in resp.headers.get("Content-Disposition", "")

    zf = zipfile.ZipFile(io.BytesIO(resp.data))
    names = sorted(zf.namelist())
    assert names == ["intro.mp4", "outro.mp4"], names
    assert zf.read("intro.mp4") == b"INTRO_VIDEO_BYTES"
    assert zf.read("outro.mp4") == b"OUTRO_VIDEO_BYTES"

    os.remove(db_path)


def test_download_all_dedupes_identical_filenames():
    db_path = "/tmp/studio_test_download_all_2.db"
    _setup_project(db_path)

    fake_project = {
        "clips": [
            {"id": 1, "status": "exported", "original_filename": "clip.mp4"},
            {"id": 2, "status": "exported", "original_filename": "clip.mp4"},
        ]
    }

    with patch.object(degas_client, "get_project", return_value=fake_project), \
         patch.object(degas_client, "download_clip", side_effect=[
             _fake_download_resp(b"FIRST"),
             _fake_download_resp(b"SECOND"),
         ]):
        client = studio_app.app.test_client()
        with client.session_transaction() as sess:
            sess["logged_in"] = True
        resp = client.get("/projects/1/clips/download-all")

    zf = zipfile.ZipFile(io.BytesIO(resp.data))
    names = sorted(zf.namelist())
    assert names == ["clip (1).mp4", "clip.mp4"], names

    os.remove(db_path)


def test_download_all_skips_a_clip_that_fails_without_failing_the_whole_zip():
    db_path = "/tmp/studio_test_download_all_3.db"
    _setup_project(db_path)

    fake_project = {
        "clips": [
            {"id": 1, "status": "exported", "original_filename": "good.mp4"},
            {"id": 2, "status": "exported", "original_filename": "broken.mp4"},
        ]
    }

    def flaky_download(degas_project_id, clip_id):
        if clip_id == 2:
            raise degas_client.DegasError("Couldn't reach Degas")
        return _fake_download_resp(b"GOOD_BYTES")

    with patch.object(degas_client, "get_project", return_value=fake_project), \
         patch.object(degas_client, "download_clip", side_effect=flaky_download):
        client = studio_app.app.test_client()
        with client.session_transaction() as sess:
            sess["logged_in"] = True
        resp = client.get("/projects/1/clips/download-all")

    assert resp.status_code == 200
    zf = zipfile.ZipFile(io.BytesIO(resp.data))
    assert zf.namelist() == ["good.mp4"]

    os.remove(db_path)


def test_download_all_errors_cleanly_with_no_exported_clips():
    db_path = "/tmp/studio_test_download_all_4.db"
    _setup_project(db_path)

    fake_project = {"clips": [{"id": 1, "status": "transcribed", "original_filename": "raw.mp4"}]}

    with patch.object(degas_client, "get_project", return_value=fake_project):
        client = studio_app.app.test_client()
        with client.session_transaction() as sess:
            sess["logged_in"] = True
        resp = client.get("/projects/1/clips/download-all")

    assert resp.status_code == 400
    assert b"No exported clips" in resp.data

    os.remove(db_path)


if __name__ == "__main__":
    test_download_all_builds_zip_with_all_exported_clips()
    print("PASS: test_download_all_builds_zip_with_all_exported_clips")
    test_download_all_dedupes_identical_filenames()
    print("PASS: test_download_all_dedupes_identical_filenames")
    test_download_all_skips_a_clip_that_fails_without_failing_the_whole_zip()
    print("PASS: test_download_all_skips_a_clip_that_fails_without_failing_the_whole_zip")
    test_download_all_errors_cleanly_with_no_exported_clips()
    print("PASS: test_download_all_errors_cleanly_with_no_exported_clips")
    print("\nALL TESTS PASSED")
