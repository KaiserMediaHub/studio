"""
Tests for two calendar additions (Ben's asks, 2026-08-24):

1. Delete a scheduled/draft/error post -- Postiz's public API has no way to
   edit or move a post once created (Update Post Settings only merges
   provider settings and explicitly leaves content/publishDate untouched;
   confirmed against docs.postiz.com), so delete is the only real "undo"
   Studio can offer. See calendar_delete_post() in app.py.

2. Multi-image (carousel) posts from Calendar -> Add Post. Postiz's `image`
   field was always a list -- Studio's upload handling just never sent more
   than one file. Now the file input accepts multiple files and all of them
   get uploaded and appended in selection order.

postiz_client and hemingway_client are mocked so this runs without either
real server.

Run with: python -m pytest test_calendar_delete_and_multi_image.py -v
(or just: python test_calendar_delete_and_multi_image.py)
"""

import io
import os
import sys
from unittest.mock import patch, MagicMock

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))

os.environ.setdefault("SECRET_KEY", "test")
os.environ.setdefault("APP_PASSWORD", "test")
os.environ.setdefault("DB_PATH", "/tmp/studio_test_calendar_media.db")

import app as studio_app
import postiz_client
import hemingway_client


def _setup_db(db_path, client_id=1):
    if os.path.exists(db_path):
        os.remove(db_path)
    studio_app.DB_PATH = db_path
    import database
    database.DB_PATH = db_path
    database.init_db()
    db = database.get_db()
    db.execute(
        "INSERT INTO client_postiz_groups (client_id, postiz_group_id, postiz_group_name) VALUES (?, 'grp1', 'Epiphany')",
        (client_id,)
    )
    db.commit()
    db.close()


def _client():
    client = studio_app.app.test_client()
    with client.session_transaction() as sess:
        sess["logged_in"] = True
    return client


def test_delete_post_calls_postiz_and_redirects():
    db_path = "/tmp/studio_test_calendar_media_1.db"
    _setup_db(db_path)

    with patch.object(postiz_client, "delete_post", return_value={"id": "post-9"}) as mock_delete:
        resp = _client().post(
            "/clients/1/calendar/posts/post-9/delete",
            data={"month_key": "2026-08"},
        )

    mock_delete.assert_called_once_with("post-9")
    assert resp.status_code == 302
    assert "month=2026-08" in resp.headers["Location"]
    os.remove(db_path)


def test_delete_post_surfaces_postiz_errors():
    db_path = "/tmp/studio_test_calendar_media_2.db"
    _setup_db(db_path)

    with patch.object(postiz_client, "delete_post", side_effect=postiz_client.PostizError("Postiz API error (404)")):
        resp = _client().post("/clients/1/calendar/posts/bad-id/delete", data={"month_key": "2026-08"})

    assert resp.status_code == 502
    os.remove(db_path)


def _fake_file(name, content=b"fake-image-bytes"):
    return (io.BytesIO(content), name)


def test_multi_image_uploads_all_files_in_order():
    db_path = "/tmp/studio_test_calendar_media_3.db"
    _setup_db(db_path)

    fake_integrations = [{"id": "chan1", "identifier": "instagram", "name": "IG"}]
    upload_calls = []

    def fake_upload(stream, filename, content_type):
        upload_calls.append(filename)
        return {"id": f"media-{filename}", "path": f"/uploads/{filename}"}

    built_items = []

    def fake_build_post_item(integration_id, identifier, content, image=None, extra=None):
        built_items.append({"image": image})
        return {"integration": {"id": integration_id}, "value": [{"content": content, "image": image}], "settings": {}}

    with patch.object(postiz_client, "list_integrations", return_value=fake_integrations), \
         patch.object(postiz_client, "upload_file", side_effect=fake_upload), \
         patch.object(postiz_client, "build_post_item", side_effect=fake_build_post_item), \
         patch.object(postiz_client, "create_post", return_value=[{"postId": "p1", "integration": "chan1"}]):
        resp = _client().post(
            "/clients/1/calendar/create-post",
            data={
                "caption": "Carousel test",
                "channel_ids": ["chan1"],
                "send_at": "2026-08-25T09:00",
                "month_key": "2026-08",
                "media": [_fake_file("first.jpg"), _fake_file("second.jpg"), _fake_file("third.jpg")],
            },
            content_type="multipart/form-data",
        )

    assert resp.status_code == 302, resp.data
    assert upload_calls == ["first.jpg", "second.jpg", "third.jpg"], (
        "files must upload in selection order -- that order becomes carousel order"
    )
    assert len(built_items[0]["image"]) == 3
    assert built_items[0]["image"][0]["path"] == "/uploads/first.jpg"
    assert built_items[0]["image"][2]["path"] == "/uploads/third.jpg"

    os.remove(db_path)


def test_single_image_still_works_backward_compatible():
    db_path = "/tmp/studio_test_calendar_media_4.db"
    _setup_db(db_path)

    fake_integrations = [{"id": "chan1", "identifier": "linkedin", "name": "LI"}]

    with patch.object(postiz_client, "list_integrations", return_value=fake_integrations), \
         patch.object(postiz_client, "upload_file", return_value={"id": "m1", "path": "/uploads/one.jpg"}) as mock_upload, \
         patch.object(postiz_client, "create_post", return_value=[{"postId": "p1", "integration": "chan1"}]):
        resp = _client().post(
            "/clients/1/calendar/create-post",
            data={
                "caption": "Single image",
                "channel_ids": ["chan1"],
                "send_at": "2026-08-25T09:00",
                "month_key": "2026-08",
                "media": [_fake_file("one.jpg")],
            },
            content_type="multipart/form-data",
        )

    assert resp.status_code == 302, resp.data
    mock_upload.assert_called_once()
    os.remove(db_path)


def test_youtube_rejects_multiple_files():
    db_path = "/tmp/studio_test_calendar_media_5.db"
    _setup_db(db_path)

    fake_integrations = [{"id": "chan1", "identifier": "youtube", "name": "YT"}]

    with patch.object(postiz_client, "list_integrations", return_value=fake_integrations), \
         patch.object(postiz_client, "upload_file") as mock_upload:
        resp = _client().post(
            "/clients/1/calendar/create-post",
            data={
                "caption": "Two videos",
                "channel_ids": ["chan1"],
                "send_at": "2026-08-25T09:00",
                "month_key": "2026-08",
                "youtube_title": "Test video",
                "media": [_fake_file("a.mp4"), _fake_file("b.mp4")],
            },
            content_type="multipart/form-data",
        )

    assert resp.status_code == 400
    assert b"exactly one video" in resp.data
    mock_upload.assert_not_called()
    os.remove(db_path)


if __name__ == '__main__':
    test_delete_post_calls_postiz_and_redirects()
    print('PASS: test_delete_post_calls_postiz_and_redirects')
    test_delete_post_surfaces_postiz_errors()
    print('PASS: test_delete_post_surfaces_postiz_errors')
    test_multi_image_uploads_all_files_in_order()
    print('PASS: test_multi_image_uploads_all_files_in_order')
    test_single_image_still_works_backward_compatible()
    print('PASS: test_single_image_still_works_backward_compatible')
    test_youtube_rejects_multiple_files()
    print('PASS: test_youtube_rejects_multiple_files')
    print('\nALL TESTS PASSED')
