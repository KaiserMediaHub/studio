"""
Tests for the Podcast Page Generator tab (Ben's ask, 2026-09-17 -- merging
the standalone podcast-page-generator tool into Studio, with a chunked
upload + async status-polling rewrite to fix the tool's original Cloudflare
524 timeout, since Whisper transcription can take 2-3 minutes).

Run: python test_podcast.py
"""

import io
import json
import os
import sys
import tempfile

sys.path.insert(0, os.path.dirname(__file__))
os.environ["DB_PATH"] = "/tmp/studio_test/data/test_podcast.db"
if os.path.exists(os.environ["DB_PATH"]):
    os.remove(os.environ["DB_PATH"])

import app as app_module
import database
import hemingway_client
import degas_client
import podcast_client

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

# Isolate generated output pages from the real podcast_output/ folder.
_test_output_dir = tempfile.mkdtemp(prefix="studio_podcast_test_")
app_module.PODCAST_OUTPUT_FOLDER = _test_output_dir

client = app_module.app.test_client()
with client.session_transaction() as sess:
    sess["logged_in"] = True


FAKE_CONTENT = {
    "title_suggestions": ["Episode One: The Beginning", "A Great Start", "Title Three"],
    "pull_quotes": ["This is a great quote.", "Another insightful line."],
    "description": "A two sentence description of the episode. It covers the main topic.",
    "key_topics": ["podcasting", "growth", "strategy"],
}


def test_podcast_view_loads():
    resp = client.get("/podcast")
    check("podcast_view: 200 OK", resp.status_code == 200, resp.status_code)
    check("podcast_view: shows the page title", b"Podcast Page Generator" in resp.data)


def test_upload_chunk_partial_passes_through():
    # Degas itself owns the chunk-counting (it's the one reassembling the
    # file), so Studio's proxy route calls it on every chunk, not just the
    # last one -- it just passes through whatever Degas reports until Degas
    # says the upload is complete.
    degas_client.upload_audio_chunk = lambda *a, **kw: {"status": "chunk_received", "chunks": 1, "total": 2}
    data = {
        "file_uid": "uid1", "chunk_index": "0", "total_chunks": "2", "filename": "ep.mp3",
        "youtube_url": "https://www.youtube.com/watch?v=abc12345678",
        "data": (io.BytesIO(b"chunk-bytes"), "ep.mp3"),
    }
    resp = client.post("/podcast/upload-chunk", data=data, content_type="multipart/form-data")
    check("upload-chunk: 200 on partial chunk", resp.status_code == 200, resp.status_code)
    body = resp.get_json()
    check("upload-chunk: passes through chunk_received status", body.get("status") == "chunk_received", body)
    check("upload-chunk: no job_id yet on a partial chunk", "job_id" not in body, body)


def test_upload_chunk_completes_creates_job():
    degas_client.upload_audio_chunk = lambda *a, **kw: {"status": "complete", "job_id": "degas-job-1"}

    data = {
        "file_uid": "uid2", "chunk_index": "0", "total_chunks": "1", "filename": "ep.mp3",
        "youtube_url": "https://www.youtube.com/watch?v=dQw4w9WgXcQ",
        "cta_text": "Follow the show", "cta_url": "https://example.com/follow",
        "primary_color": "#111111", "secondary_color": "#222222", "accent_color": "#333333",
        "logo_orientation": "square", "logo_data_uri": "",
        "data": (io.BytesIO(b"final-chunk-bytes"), "ep.mp3"),
    }
    resp = client.post("/podcast/upload-chunk", data=data, content_type="multipart/form-data")
    check("upload-chunk: 200 on final chunk", resp.status_code == 200, resp.status_code)
    body = resp.get_json()
    check("upload-chunk: returns complete status", body.get("status") == "complete", body)
    check("upload-chunk: returns a studio job_id", bool(body.get("job_id")), body)

    db = database.get_db()
    row = db.execute("SELECT * FROM podcast_jobs WHERE id = ?", (body["job_id"],)).fetchone()
    db.close()
    check("upload-chunk: job row created", row is not None)
    check("upload-chunk: degas_job_id stored", row["degas_job_id"] == "degas-job-1", dict(row) if row else None)
    check("upload-chunk: initial status transcribing", row["status"] == "transcribing")
    form = json.loads(row["form_json"])
    check("upload-chunk: youtube_id parsed into form_json", form["youtube_id"] == "dQw4w9WgXcQ", form)
    check("upload-chunk: cta_text stored", form["cta_text"] == "Follow the show", form)

    return body["job_id"]


def test_upload_chunk_rejects_bad_youtube_url():
    degas_client.upload_audio_chunk = lambda *a, **kw: {"status": "complete", "job_id": "degas-job-2"}
    data = {
        "file_uid": "uid3", "chunk_index": "0", "total_chunks": "1", "filename": "ep.mp3",
        "youtube_url": "not-a-real-url",
        "data": (io.BytesIO(b"bytes"), "ep.mp3"),
    }
    resp = client.post("/podcast/upload-chunk", data=data, content_type="multipart/form-data")
    check("upload-chunk: 400 for unparseable YouTube URL", resp.status_code == 400, resp.status_code)


def test_status_still_transcribing():
    degas_client.upload_audio_chunk = lambda *a, **kw: {"status": "complete", "job_id": "degas-job-3"}
    data = {
        "file_uid": "uid4", "chunk_index": "0", "total_chunks": "1", "filename": "ep.mp3",
        "youtube_url": "https://youtu.be/aaaaaaaaaaa",
        "data": (io.BytesIO(b"bytes"), "ep.mp3"),
    }
    job_id = client.post("/podcast/upload-chunk", data=data, content_type="multipart/form-data").get_json()["job_id"]

    degas_client.get_audio_transcription_status = lambda job: {"status": "transcribing", "transcript": None, "error": None}
    resp = client.get(f"/podcast/status/{job_id}")
    check("status: 200 while transcribing", resp.status_code == 200)
    check("status: reports transcribing", resp.get_json().get("status") == "transcribing")
    return job_id


def test_status_completes_generation_and_serves_output():
    degas_client.upload_audio_chunk = lambda *a, **kw: {"status": "complete", "job_id": "degas-job-4"}
    data = {
        "file_uid": "uid5", "chunk_index": "0", "total_chunks": "1", "filename": "ep.mp3",
        "youtube_url": "https://www.youtube.com/watch?v=bbbbbbbbbbb",
        "cta_text": "Subscribe now", "cta_url": "https://example.com",
        "data": (io.BytesIO(b"bytes"), "ep.mp3"),
    }
    job_id = client.post("/podcast/upload-chunk", data=data, content_type="multipart/form-data").get_json()["job_id"]

    degas_client.get_audio_transcription_status = lambda job: {
        "status": "done", "transcript": "This is the full episode transcript.", "error": None
    }
    call_count = {"n": 0}
    def fake_generate(transcript, cta_text):
        call_count["n"] += 1
        check("generate_episode_content: receives the transcript", transcript == "This is the full episode transcript.")
        check("generate_episode_content: receives the cta_text", cta_text == "Subscribe now")
        return dict(FAKE_CONTENT)
    podcast_client.generate_episode_content = fake_generate

    resp = client.get(f"/podcast/status/{job_id}")
    check("status: 200 on completion", resp.status_code == 200, resp.status_code)
    body = resp.get_json()
    check("status: reports done", body.get("status") == "done", body)
    check("status: title_suggestions passed through", body.get("title_suggestions") == FAKE_CONTENT["title_suggestions"])
    check("status: has download_url", "/podcast/download/" in (body.get("download_url") or ""), body)
    check("status: has preview_url", "/podcast/preview/" in (body.get("preview_url") or ""), body)

    db = database.get_db()
    row = db.execute("SELECT * FROM podcast_jobs WHERE id = ?", (job_id,)).fetchone()
    db.close()
    check("status: row moved to done", row["status"] == "done")
    check("status: result_json cached", json.loads(row["result_json"])["filename"] == body["filename"])

    # Preview/download should actually serve the rendered file.
    preview_resp = client.get(body["preview_url"])
    check("preview: 200 OK", preview_resp.status_code == 200, preview_resp.status_code)
    check("preview: contains the transcript", b"This is the full episode transcript." in preview_resp.data)
    check("preview: contains Schema.org PodcastEpisode markup", b"PodcastEpisode" in preview_resp.data)

    download_resp = client.get(body["download_url"])
    check("download: 200 OK", download_resp.status_code == 200)
    check("download: Content-Disposition is attachment",
          "attachment" in download_resp.headers.get("Content-Disposition", ""))

    # A repeat poll after completion must NOT call Claude again -- cached
    # result only, or this silently burns API spend on every dashboard
    # refresh.
    resp2 = client.get(f"/podcast/status/{job_id}")
    check("status: repeat poll returns 200", resp2.status_code == 200)
    check("status: repeat poll does not re-call Claude", call_count["n"] == 1, call_count["n"])
    check("status: repeat poll still returns done", resp2.get_json().get("status") == "done")


def test_status_degas_error_marks_job_error():
    degas_client.upload_audio_chunk = lambda *a, **kw: {"status": "complete", "job_id": "degas-job-5"}
    data = {
        "file_uid": "uid6", "chunk_index": "0", "total_chunks": "1", "filename": "ep.mp3",
        "youtube_url": "https://www.youtube.com/watch?v=ccccccccccc",
        "data": (io.BytesIO(b"bytes"), "ep.mp3"),
    }
    job_id = client.post("/podcast/upload-chunk", data=data, content_type="multipart/form-data").get_json()["job_id"]

    degas_client.get_audio_transcription_status = lambda job: {"status": "error", "transcript": None, "error": "ffmpeg failed"}
    resp = client.get(f"/podcast/status/{job_id}")
    body = resp.get_json()
    check("status: surfaces degas error", body.get("status") == "error" and body.get("error") == "ffmpeg failed", body)

    db = database.get_db()
    row = db.execute("SELECT status, error_message FROM podcast_jobs WHERE id = ?", (job_id,)).fetchone()
    db.close()
    check("status: row marked error", row["status"] == "error" and row["error_message"] == "ffmpeg failed", dict(row))


def test_status_claude_failure_marks_job_error():
    degas_client.upload_audio_chunk = lambda *a, **kw: {"status": "complete", "job_id": "degas-job-6"}
    data = {
        "file_uid": "uid7", "chunk_index": "0", "total_chunks": "1", "filename": "ep.mp3",
        "youtube_url": "https://www.youtube.com/watch?v=ddddddddddd",
        "data": (io.BytesIO(b"bytes"), "ep.mp3"),
    }
    job_id = client.post("/podcast/upload-chunk", data=data, content_type="multipart/form-data").get_json()["job_id"]

    degas_client.get_audio_transcription_status = lambda job: {"status": "done", "transcript": "hello", "error": None}
    def fake_fail(transcript, cta_text):
        raise podcast_client.PodcastContentError("Claude API call failed: rate limited")
    podcast_client.generate_episode_content = fake_fail

    resp = client.get(f"/podcast/status/{job_id}")
    body = resp.get_json()
    check("status: surfaces Claude error", body.get("status") == "error" and "rate limited" in body.get("error", ""), body)


def test_status_unknown_job_404():
    resp = client.get("/podcast/status/does-not-exist")
    check("status: 404 for unknown job_id", resp.status_code == 404)


if __name__ == "__main__":
    test_podcast_view_loads()
    test_upload_chunk_partial_passes_through()
    test_upload_chunk_completes_creates_job()
    test_upload_chunk_rejects_bad_youtube_url()
    test_status_still_transcribing()
    test_status_completes_generation_and_serves_output()
    test_status_degas_error_marks_job_error()
    test_status_claude_failure_marks_job_error()
    test_status_unknown_job_404()

    print()
    print(f"TOTAL: {results['pass']} passed, {results['fail']} failed")
    if results["fail"]:
        sys.exit(1)
