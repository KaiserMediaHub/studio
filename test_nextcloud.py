import io
import os
import sys

sys.path.insert(0, os.path.dirname(__file__))
os.environ["DB_PATH"] = "/tmp/studio_test/data/test_studio_nextcloud.db"
if os.path.exists(os.environ["DB_PATH"]):
    os.remove(os.environ["DB_PATH"])

import app as app_module
import database
import hemingway_client
import degas_client
import nextcloud_client

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

nextcloud_client.NEXTCLOUD_USERNAME = "studio"
nextcloud_client.NEXTCLOUD_APP_PASSWORD = "fake-app-password"
nextcloud_client.NEXTCLOUD_BASE_URL = "https://cloud.kmgtools.us"


class FakeResp:
    def __init__(self, status_code=200, content=b"", headers=None, text=""):
        self.status_code = status_code
        self.content = content
        self.headers = headers or {}
        self.text = text or content.decode("utf-8", "ignore")


PROPFIND_XML = b"""<?xml version="1.0"?>
<d:multistatus xmlns:d="DAV:">
  <d:response>
    <d:href>/remote.php/dav/files/studio/Epiphany/incoming/</d:href>
    <d:propstat>
      <d:prop>
        <d:resourcetype><d:collection/></d:resourcetype>
        <d:getlastmodified>Mon, 03 Aug 2026 10:00:00 GMT</d:getlastmodified>
      </d:prop>
      <d:status>HTTP/1.1 200 OK</d:status>
    </d:propstat>
  </d:response>
  <d:response>
    <d:href>/remote.php/dav/files/studio/Epiphany/incoming/clip1.mp4</d:href>
    <d:propstat>
      <d:prop>
        <d:getcontentlength>1048576</d:getcontentlength>
        <d:resourcetype/>
        <d:getlastmodified>Mon, 03 Aug 2026 10:05:00 GMT</d:getlastmodified>
      </d:prop>
      <d:status>HTTP/1.1 200 OK</d:status>
    </d:propstat>
  </d:response>
  <d:response>
    <d:href>/remote.php/dav/files/studio/Epiphany/incoming/subfolder/</d:href>
    <d:propstat>
      <d:prop>
        <d:resourcetype><d:collection/></d:resourcetype>
      </d:prop>
      <d:status>HTTP/1.1 200 OK</d:status>
    </d:propstat>
  </d:response>
</d:multistatus>"""


# ── nextcloud_client: list_folder XML parsing ────────────────────────────────

def test_list_folder_parses_propfind_correctly():
    calls = []
    def fake_request(method, url, **kwargs):
        calls.append((method, url, kwargs.get("headers")))
        return FakeResp(207, content=PROPFIND_XML)
    nextcloud_client.requests.request = fake_request

    files = nextcloud_client.list_folder("Epiphany/incoming")
    check("list_folder: PROPFIND method used", calls[0][0] == "PROPFIND")
    check("list_folder: Depth header is 1 (not recursive)", calls[0][2].get("Depth") == "1", calls[0][2])
    check("list_folder: returns exactly 1 file (self + subfolder excluded)", len(files) == 1, files)
    check("list_folder: correct filename", files[0]["name"] == "clip1.mp4", files)
    check("list_folder: correct size in bytes", files[0]["size"] == 1048576, files)


def test_list_folder_404_returns_empty_not_error():
    nextcloud_client.requests.request = lambda method, url, **kwargs: FakeResp(404, content=b"")
    files = nextcloud_client.list_folder("NewClient/incoming")
    check("list_folder: 404 (folder not created yet) returns [] not raise", files == [])


def test_list_folder_missing_credentials_raises():
    old_user, old_pass = nextcloud_client.NEXTCLOUD_USERNAME, nextcloud_client.NEXTCLOUD_APP_PASSWORD
    nextcloud_client.NEXTCLOUD_USERNAME = ""
    nextcloud_client.NEXTCLOUD_APP_PASSWORD = ""
    try:
        nextcloud_client.list_folder("Epiphany/incoming")
        check("list_folder: raises when credentials unset", False, "did not raise")
    except nextcloud_client.NextcloudError as e:
        check("list_folder: raises when credentials unset", "app password" in str(e) or "NEXTCLOUD" in str(e), str(e))
    finally:
        nextcloud_client.NEXTCLOUD_USERNAME = old_user
        nextcloud_client.NEXTCLOUD_APP_PASSWORD = old_pass


# ── nextcloud_client: ensure_folder ──────────────────────────────────────────

def test_ensure_folder_creates_each_segment():
    calls = []
    def fake_request(method, url, **kwargs):
        calls.append((method, url))
        return FakeResp(201)
    nextcloud_client.requests.request = fake_request

    nextcloud_client.ensure_folder("Epiphany/incoming")
    check("ensure_folder: MKCOL called for both segments", len(calls) == 2, calls)
    check("ensure_folder: first call is the client folder", calls[0][1].endswith("/Epiphany"), calls[0])
    check("ensure_folder: second call is the incoming subfolder", calls[1][1].endswith("/Epiphany/incoming"), calls[1])


def test_ensure_folder_tolerates_already_exists():
    nextcloud_client.requests.request = lambda method, url, **kwargs: FakeResp(405)
    try:
        nextcloud_client.ensure_folder("Epiphany/incoming")
        check("ensure_folder: 405 (already exists) does not raise", True)
    except nextcloud_client.NextcloudError as e:
        check("ensure_folder: 405 (already exists) does not raise", False, str(e))


def test_ensure_folder_raises_on_real_error():
    nextcloud_client.requests.request = lambda method, url, **kwargs: FakeResp(500, text="server error")
    try:
        nextcloud_client.ensure_folder("Epiphany/incoming")
        check("ensure_folder: real error (500) raises", False, "did not raise")
    except nextcloud_client.NextcloudError:
        check("ensure_folder: real error (500) raises", True)


# ── nextcloud_client: upload_file / download_file ────────────────────────────

def test_upload_file_puts_bytes():
    calls = []
    def fake_request(method, url, **kwargs):
        calls.append((method, url, kwargs))
        return FakeResp(201)
    nextcloud_client.requests.request = fake_request

    nextcloud_client.upload_file("Epiphany/captioned/x.mp4", b"videodata", "video/mp4")
    check("upload_file: PUT method used", calls[0][0] == "PUT")
    check("upload_file: bytes forwarded", calls[0][2]["data"] == b"videodata")
    check("upload_file: content-type header set", calls[0][2]["headers"]["Content-Type"] == "video/mp4")


def test_download_file_streams():
    nextcloud_client.requests.request = lambda method, url, **kwargs: FakeResp(200, content=b"rawvideo")
    resp = nextcloud_client.download_file("Epiphany/incoming/clip1.mp4")
    check("download_file: returns response with content", resp.content == b"rawvideo")


# ── Routes: nextcloud_setup_view / link ──────────────────────────────────────

def test_nextcloud_setup_link_saves_and_creates_folders():
    ensure_calls = []
    nextcloud_client.ensure_folder = lambda path: ensure_calls.append(path)
    nextcloud_client.list_folder = lambda path: []

    resp = client.post(f"/clients/{CLIENT_ID}/nextcloud-setup/link", data={"folder_name": "Epiphany"})
    check("nextcloud_setup_link: redirects", resp.status_code == 302, resp.status_code)
    check("nextcloud_setup_link: ensures /incoming", "Epiphany/incoming" in ensure_calls, ensure_calls)
    check("nextcloud_setup_link: ensures /captioned", "Epiphany/captioned" in ensure_calls, ensure_calls)

    db = database.get_db()
    row = db.execute("SELECT * FROM client_nextcloud_folders WHERE client_id = ?", (CLIENT_ID,)).fetchone()
    db.close()
    check("nextcloud_setup_link: saved to DB", row is not None and row["folder_name"] == "Epiphany", dict(row) if row else None)


def test_nextcloud_setup_view_lists_both_folders():
    nextcloud_client.list_folder = lambda path: [{"name": "a.mp4", "size": 500, "last_modified": None}] if "incoming" in path else [{"name": "b.mp4", "size": 900, "last_modified": None}]
    resp = client.get(f"/clients/{CLIENT_ID}/nextcloud-setup")
    body = resp.get_data(as_text=True)
    check("nextcloud_setup_view: 200 OK", resp.status_code == 200)
    check("nextcloud_setup_view: shows incoming file", "a.mp4" in body)
    check("nextcloud_setup_view: shows captioned file", "b.mp4" in body)


# ── project_detail: Cloud KMG card ───────────────────────────────────────────

def make_project(name="NC Project"):
    db = database.get_db()
    db.execute(
        "INSERT INTO projects (client_id, name, degas_project_id, phase) VALUES (?, ?, ?, 'intake')",
        (CLIENT_ID, name, DEGAS_PROJECT_ID)
    )
    db.commit()
    pid = db.execute("SELECT id FROM projects WHERE name = ? ORDER BY id DESC LIMIT 1", (name,)).fetchone()["id"]
    db.close()
    return pid


def test_project_detail_shows_cloud_kmg_card_when_linked():
    pid = make_project("Cloud Card Test")
    degas_client.get_project = lambda p: {"clips": []}
    nextcloud_client.list_folder = lambda path: [{"name": "raw1.mp4", "size": 2000, "last_modified": None}]

    resp = client.get(f"/projects/{pid}")
    body = resp.get_data(as_text=True)
    check("project_detail: Cloud KMG card shown", "Cloud KMG" in body)
    check("project_detail: incoming file listed with Import button", "raw1.mp4" in body and "Import" in body)


# ── project_nextcloud_import ──────────────────────────────────────────────────

def test_nextcloud_import_chunks_and_uploads_to_degas():
    pid = make_project("Import Test")

    class FakeDownload:
        content = b"x" * (10 * 1024 * 1024)  # 10MB -- should split into 2 chunks of 8MB default
    nextcloud_client.download_file = lambda path: FakeDownload()

    chunk_calls = []
    def fake_upload_chunk(degas_project_id, file_uid, chunk_index, total_chunks, filename, chunk_bytes, content_type):
        chunk_calls.append((degas_project_id, chunk_index, total_chunks, filename, len(chunk_bytes)))
        return {"status": "chunk_received"} if chunk_index < total_chunks - 1 else {"status": "complete", "filename": filename}
    degas_client.upload_chunk = fake_upload_chunk

    resp = client.post(f"/projects/{pid}/nextcloud-import", data={"filename": "raw1.mp4"})
    check("nextcloud_import: redirects on success", resp.status_code == 302, resp.status_code)
    check("nextcloud_import: exactly 2 chunks sent for 10MB file", len(chunk_calls) == 2, chunk_calls)
    check("nextcloud_import: first chunk is full 8MB", chunk_calls[0][4] == 8 * 1024 * 1024, chunk_calls[0])
    check("nextcloud_import: second chunk is the 2MB remainder", chunk_calls[1][4] == 2 * 1024 * 1024, chunk_calls[1])
    check("nextcloud_import: correct degas_project_id forwarded", chunk_calls[0][0] == DEGAS_PROJECT_ID)


def test_nextcloud_import_blocked_without_linked_folder():
    db = database.get_db()
    db.execute(
        "INSERT INTO projects (client_id, name, degas_project_id, phase) VALUES (?, ?, ?, 'intake')",
        (999, "No Link Client Project", DEGAS_PROJECT_ID)
    )
    db.commit()
    pid = db.execute("SELECT id FROM projects WHERE name = 'No Link Client Project'").fetchone()["id"]
    db.close()

    resp = client.post(f"/projects/{pid}/nextcloud-import", data={"filename": "x.mp4"})
    check("nextcloud_import: 400 when client has no linked folder", resp.status_code == 400, resp.status_code)


def test_nextcloud_import_empty_file_blocked():
    pid = make_project("Empty File Test")

    class FakeEmptyDownload:
        content = b""
    nextcloud_client.download_file = lambda path: FakeEmptyDownload()

    resp = client.post(f"/projects/{pid}/nextcloud-import", data={"filename": "empty.mp4"})
    check("nextcloud_import: 400 when downloaded file is empty", resp.status_code == 400, resp.status_code)


# ── clip_archive_to_cloud ─────────────────────────────────────────────────────

def test_archive_to_cloud_uploads_exported_clip():
    pid = make_project("Archive Test")

    class FakeExportedResp:
        headers = {"Content-Disposition": 'attachment; filename="final_clip.mp4"', "Content-Type": "video/mp4"}
        content = b"exported-video-bytes"
    degas_client.download_clip = lambda p, c: FakeExportedResp()

    ensure_calls = []
    upload_calls = []
    nextcloud_client.ensure_folder = lambda path: ensure_calls.append(path)
    nextcloud_client.upload_file = lambda path, data, content_type: upload_calls.append((path, data, content_type))

    resp = client.post(f"/projects/{pid}/clips/99/archive-to-cloud")
    check("archive_to_cloud: redirects on success", resp.status_code == 302, resp.status_code)
    check("archive_to_cloud: ensures /captioned exists", "Epiphany/captioned" in ensure_calls, ensure_calls)
    check("archive_to_cloud: uploads to correct path with correct filename", upload_calls and upload_calls[0][0] == "Epiphany/captioned/final_clip.mp4", upload_calls)
    check("archive_to_cloud: uploads the exported bytes", upload_calls and upload_calls[0][1] == b"exported-video-bytes", upload_calls)


def test_archive_to_cloud_blocked_when_not_exported():
    pid = make_project("Archive Not Exported Test")

    class FakeNotExportedResp:
        headers = {}  # no Content-Disposition -- Degas redirects instead of erroring when not ready
        content = b""
    degas_client.download_clip = lambda p, c: FakeNotExportedResp()

    resp = client.post(f"/projects/{pid}/clips/99/archive-to-cloud")
    check("archive_to_cloud: 400 when clip isn't exported yet", resp.status_code == 400, resp.status_code)


test_list_folder_parses_propfind_correctly()
test_list_folder_404_returns_empty_not_error()
test_list_folder_missing_credentials_raises()
test_ensure_folder_creates_each_segment()
test_ensure_folder_tolerates_already_exists()
test_ensure_folder_raises_on_real_error()
test_upload_file_puts_bytes()
test_download_file_streams()
test_nextcloud_setup_link_saves_and_creates_folders()
test_nextcloud_setup_view_lists_both_folders()
test_project_detail_shows_cloud_kmg_card_when_linked()
test_nextcloud_import_chunks_and_uploads_to_degas()
test_nextcloud_import_blocked_without_linked_folder()
test_nextcloud_import_empty_file_blocked()
test_archive_to_cloud_uploads_exported_clip()
test_archive_to_cloud_blocked_when_not_exported()

print(f"\n{results['pass']} passed, {results['fail']} failed")
sys.exit(1 if results["fail"] else 0)
