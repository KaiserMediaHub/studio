import os
import calendar as cal_module
from datetime import datetime, timedelta, timezone

from flask import (
    Flask, render_template, request, redirect,
    url_for, session, jsonify, Response
)

from database import init_db, get_db
import hemingway_client
import degas_client
import glossary
import postiz_client

# ── Config ────────────────────────────────────────────────────────────────────
app = Flask(__name__)
app.secret_key = os.environ.get("SECRET_KEY", "change-me-in-production")

# Placeholder auth for this first skeleton: same shared-password session
# pattern already proven in Degas and Hemingway. This is NOT the final
# unified-auth story (STUDIO_SYSTEM_DESIGN.md Section 2/9, step 2) -- that
# requires Degas and Hemingway to accept Studio's session instead of their
# own login, which needs their real source confirmed first. This gets
# Studio itself running and testable while that's worked out.
APP_PASSWORD = os.environ.get("APP_PASSWORD", "studio2026")


@app.before_request
def require_login():
    public = {"login", "static", "health"}
    if request.endpoint in public:
        return
    if not session.get("logged_in"):
        return redirect(url_for("login"))


@app.route("/health")
def health():
    return jsonify({"status": "ok"})


@app.route("/login", methods=["GET", "POST"])
def login():
    error = None
    if request.method == "POST":
        if request.form.get("password") == APP_PASSWORD:
            session["logged_in"] = True
            return redirect(url_for("dashboard"))
        error = "Incorrect password — try again."
    return render_template("login.html", error=error)


@app.route("/logout")
def logout():
    session.clear()
    return redirect(url_for("login"))


# ── Clients ───────────────────────────────────────────────────────────────────
@app.route("/")
def index():
    return redirect(url_for("dashboard"))


@app.route("/dashboard")
def dashboard():
    # Corrected 7/17: no local clients table -- Studio calls Hemingway's
    # real /api/clients directly (hemingway_client.py) as the single
    # source of truth, rather than keeping a copy that can drift.
    try:
        clients = hemingway_client.get_clients()
    except hemingway_client.HemingwayError as e:
        return render_template("error.html", message=str(e)), 502

    active_client_id = request.args.get("client_id", type=int)
    show_archived = request.args.get("show_archived") == "1"
    active_client = None
    projects = []
    degas_error = None
    if clients:
        active_client = next(
            (c for c in clients if c["id"] == active_client_id),
            clients[0]
        )
        db = get_db()
        if show_archived:
            rows = db.execute(
                "SELECT * FROM projects WHERE client_id = ? ORDER BY created_at DESC",
                (active_client["id"],)
            ).fetchall()
        else:
            rows = db.execute(
                "SELECT * FROM projects WHERE client_id = ? AND archived_at IS NULL ORDER BY created_at DESC",
                (active_client["id"],)
            ).fetchall()

        # Task #7: Intake-through-Clipped phase comes from Degas's own
        # clips.status, read live rather than duplicated -- see
        # STUDIO_SYSTEM_DESIGN.md Section 4 "major simplification."
        # Drafting/Post Review are Studio-owned state (degas_project_id may
        # also be null for projects created before this was wired up).
        for p in rows:
            phase = p["phase"] or "intake"
            clips = []
            if p["degas_project_id"]:
                try:
                    degas_proj = degas_client.get_project(p["degas_project_id"])
                    clips = degas_proj.get("clips", [])
                    phase = degas_client.effective_phase(p["phase"], clips)
                except degas_client.DegasError as e:
                    degas_error = str(e)
            if phase != p["phase"]:
                db.execute("UPDATE projects SET phase = ? WHERE id = ?", (phase, p["id"]))
                db.commit()
            projects.append({
                "id": p["id"],
                "name": p["name"],
                "degas_project_id": p["degas_project_id"],
                "phase": phase,
                "clip_count": len(clips),
                "clips_exported": sum(1 for c in clips if c["status"] == "exported"),
                "created_at": p["created_at"],
                "archived_at": p["archived_at"],
            })
        db.close()

    cleanup_candidates, _cleanup_errors = _get_cleanup_candidates()

    return render_template(
        "dashboard.html",
        clients=clients,
        active_client=active_client,
        projects=projects,
        degas_error=degas_error,
        phase_labels=PHASE_LABELS,
        show_archived=show_archived,
        cleanup_count=len(cleanup_candidates),
    )


# ── Storage cleanup (task #22) ───────────────────────────────────────────────
# Nothing here ever deletes automatically in the background -- this only
# flags clips whose project is 45+ days old for Ben to review and delete
# himself from the Storage Cleanup page (or dismiss via the dashboard
# banner). Age is based on the Studio project's own created_at rather than
# asking Degas for per-clip timestamps, since Degas's existing JSON project
# endpoint doesn't return clip-level created_at and adding that would have
# meant a riskier hand-edit to an existing live route instead of a pure
# append -- see degas_client.delete_clip_media()'s docstring.
CLEANUP_THRESHOLD_DAYS = 45


def _get_cleanup_candidates():
    """Cross-client scan: every non-deleted clip belonging to a Studio
    project that's 45+ days old. Computed fresh on every call -- no caching
    yet, since project/clip counts are small today; worth revisiting if this
    gets slow once all 4 clients are onboarded (task #15)."""
    db = get_db()
    rows = db.execute("SELECT * FROM projects WHERE degas_project_id IS NOT NULL").fetchall()
    db.close()

    try:
        clients = hemingway_client.get_clients()
    except hemingway_client.HemingwayError:
        clients = []
    client_names = {c["id"]: c["name"] for c in clients}

    now = datetime.now(timezone.utc)
    cutoff = now - timedelta(days=CLEANUP_THRESHOLD_DAYS)
    candidates = []
    errors = []
    for proj in rows:
        try:
            created_at = datetime.strptime(proj["created_at"], "%Y-%m-%d %H:%M:%S").replace(tzinfo=timezone.utc)
        except (ValueError, TypeError):
            continue
        if created_at >= cutoff:
            continue
        try:
            degas_proj = degas_client.get_project(proj["degas_project_id"])
        except degas_client.DegasError as e:
            errors.append(f"{proj['name']}: {e}")
            continue
        age_days = (now - created_at).days
        for clip in degas_proj.get("clips", []):
            if clip["status"] == "deleted":
                continue
            candidates.append({
                "project_id": proj["id"],
                "degas_project_id": proj["degas_project_id"],
                "project_name": proj["name"],
                "client_name": client_names.get(proj["client_id"], "?"),
                "clip_id": clip["id"],
                "filename": clip.get("original_filename") or clip["filename"],
                "status": clip["status"],
                "age_days": age_days,
            })
    return candidates, errors


@app.route("/settings/storage-cleanup")
def storage_cleanup_view():
    try:
        clients = hemingway_client.get_clients()
    except hemingway_client.HemingwayError as e:
        return render_template("error.html", message=str(e)), 502
    active_client = clients[0] if clients else None

    candidates, errors = _get_cleanup_candidates()
    return render_template(
        "storage_cleanup.html",
        clients=clients,
        active_client=active_client,
        candidates=candidates,
        errors=errors,
        threshold_days=CLEANUP_THRESHOLD_DAYS,
    )


@app.route("/settings/storage-cleanup/delete", methods=["POST"])
def storage_cleanup_delete():
    """Deletes exactly the clips Ben checked and confirmed -- nothing else.
    Each is gone from Degas's disk permanently; there's no undo."""
    selected = request.form.getlist("delete_ids")
    errors = []
    for item in selected:
        try:
            degas_project_id_str, clip_id_str = item.split(":")
            degas_client.delete_clip_media(int(degas_project_id_str), int(clip_id_str))
        except (ValueError, degas_client.DegasError) as e:
            errors.append(str(e))
    if errors:
        return render_template("error.html", message="; ".join(errors[:3])), 502
    return redirect(url_for("storage_cleanup_view"))


PHASE_LABELS = {
    "intake":         "Intake",
    "transcribing":   "Transcribing",
    "caption_review": "Caption Review",
    "clipped":        "Clipped",
    "drafting":       "Drafting",
    "post_review":    "Post Review",
}


@app.route("/projects/new", methods=["POST"])
def new_project():
    name = request.form.get("name", "").strip()
    client_id = request.form.get("client_id", type=int)
    if not name or not client_id:
        return redirect(url_for("dashboard", client_id=client_id))

    try:
        clients = hemingway_client.get_clients()
        client = next((c for c in clients if c["id"] == client_id), None)
        assigned_to = client["name"] if client else ""
        degas_project_id = degas_client.create_project(name, assigned_to, client_id)
    except (hemingway_client.HemingwayError, degas_client.DegasError) as e:
        return render_template("error.html", message=str(e)), 502

    db = get_db()
    db.execute(
        "INSERT INTO projects (client_id, name, degas_project_id, phase) VALUES (?, ?, ?, 'intake')",
        (client_id, name, degas_project_id)
    )
    db.commit()
    db.close()
    return redirect(url_for("dashboard", client_id=client_id))


@app.route("/projects/<int:project_id>/advance-phase", methods=["POST"])
def advance_phase(project_id):
    """Manual phase advance for Drafting/Post Review -- these are genuinely
    new state Studio owns (Section 4), not derived from Degas, so they need
    an explicit action rather than being inferred."""
    target = request.form.get("target")
    client_id = request.form.get("client_id", type=int)
    allowed_transitions = {"clipped": "drafting", "drafting": "post_review"}

    db = get_db()
    proj = db.execute("SELECT * FROM projects WHERE id = ?", (project_id,)).fetchone()
    if proj and allowed_transitions.get(proj["phase"]) == target:
        db.execute("UPDATE projects SET phase = ? WHERE id = ?", (target, project_id))
        db.commit()
    db.close()
    return redirect(url_for("dashboard", client_id=client_id))


@app.route("/projects/<int:project_id>/archive", methods=["POST"])
def project_archive(project_id):
    client_id = request.form.get("client_id", type=int)
    db = get_db()
    db.execute("UPDATE projects SET archived_at = CURRENT_TIMESTAMP WHERE id = ?", (project_id,))
    db.commit()
    db.close()
    return redirect(url_for("dashboard", client_id=client_id))


@app.route("/projects/<int:project_id>/unarchive", methods=["POST"])
def project_unarchive(project_id):
    client_id = request.form.get("client_id", type=int)
    db = get_db()
    db.execute("UPDATE projects SET archived_at = NULL WHERE id = ?", (project_id,))
    db.commit()
    db.close()
    return redirect(url_for("dashboard", client_id=client_id))


@app.route("/projects/<int:project_id>/delete", methods=["POST"])
def project_delete(project_id):
    """Permanently removes this project from Studio only -- does not touch
    the linked Degas project or its uploaded video/exported files, which are
    a separate system Studio doesn't own and shouldn't destroy from here.
    Posts already generated/scheduled keep existing (posts.project_id ON
    DELETE SET NULL) -- deleting the project just unlinks them, it doesn't
    delete post history."""
    client_id = request.form.get("client_id", type=int)
    db = get_db()
    db.execute("DELETE FROM projects WHERE id = ?", (project_id,))
    db.commit()
    db.close()
    return redirect(url_for("dashboard", client_id=client_id))


# ── Project workspace (task #21): the real Upload -> Transcribe -> Review ->
# Export -> Write Posts pipeline, built as genuine Studio screens against
# Degas's and Hemingway's real APIs, not stubs or redirects to those other
# apps -- confirmed against live server source 7/24, no changes needed on
# either Degas's or Hemingway's side.
def _build_project_transcript(degas_project_id, clips):
    """Builds a transcript string compatible with Hemingway's
    split_transcript() parser: one 'VIDEO: NN - Title' line per clip
    followed by that clip's current (possibly human-edited) segment text,
    one segment per line. Only includes clips whose transcript actually
    exists yet (transcribed/exported) -- clips still uploading/transcribing/
    erroring have nothing usable."""
    sections = []
    eligible = [c for c in clips if c["status"] in ("transcribed", "exported")]
    for idx, clip in enumerate(eligible, start=1):
        title = os.path.splitext(clip.get("original_filename") or clip["filename"])[0]
        try:
            seg_data = degas_client.get_clip_segments(degas_project_id, clip["id"])
        except degas_client.DegasError:
            continue
        segments = seg_data.get("current") or seg_data.get("original") or []
        lines = [f"VIDEO: {idx:02d} - {title}"]
        for seg in segments:
            text = (seg.get("text") or "").strip()
            if text:
                lines.append(text)
        if len(lines) > 1:
            sections.append("\n".join(lines))
    return "\n\n".join(sections)


@app.route("/projects/<int:project_id>")
def project_detail(project_id):
    db = get_db()
    proj = db.execute("SELECT * FROM projects WHERE id = ?", (project_id,)).fetchone()
    if not proj:
        db.close()
        return redirect(url_for("dashboard"))

    try:
        clients = hemingway_client.get_clients()
    except hemingway_client.HemingwayError as e:
        db.close()
        return render_template("error.html", message=str(e)), 502
    active_client = next((c for c in clients if c["id"] == proj["client_id"]), None)

    degas_clips = []
    degas_error = None
    if proj["degas_project_id"]:
        try:
            degas_proj = degas_client.get_project(proj["degas_project_id"])
            degas_clips = degas_proj.get("clips", [])
        except degas_client.DegasError as e:
            degas_error = str(e)

    project_posts = db.execute(
        "SELECT * FROM posts WHERE project_id = ? ORDER BY created_at DESC", (project_id,)
    ).fetchall()
    linked = db.execute(
        "SELECT * FROM client_postiz_groups WHERE client_id = ?", (proj["client_id"],)
    ).fetchone()
    db.close()

    channels, channels_error = _get_schedulable_channels(linked)
    can_write_posts = any(c["status"] in ("transcribed", "exported") for c in degas_clips)

    return render_template(
        "project_detail.html",
        clients=clients,
        active_client=active_client,
        project=proj,
        phase_labels=PHASE_LABELS,
        degas_clips=degas_clips,
        degas_error=degas_error,
        export_styles=degas_client.EXPORT_STYLES,
        project_posts=project_posts,
        can_write_posts=can_write_posts,
        linked=linked,
        channels=channels,
        channels_error=channels_error,
    )


@app.route("/projects/<int:project_id>/upload-chunk", methods=["POST"])
def project_upload_chunk(project_id):
    """Receives one chunk from the browser's JS uploader and forwards it to
    Degas's real chunked-upload route using Studio's own authenticated
    session -- the browser never talks to degas.kmgtools.us directly."""
    db = get_db()
    proj = db.execute("SELECT * FROM projects WHERE id = ?", (project_id,)).fetchone()
    db.close()
    if not proj or not proj["degas_project_id"]:
        return jsonify({"error": "This project isn't linked to Degas."}), 400

    file_uid = request.form.get("file_uid", "")
    chunk_index = request.form.get("chunk_index", "")
    total_chunks = request.form.get("total_chunks", "")
    filename = request.form.get("filename", "")
    file_obj = request.files.get("data")
    if not file_uid or not file_obj:
        return jsonify({"error": "missing fields"}), 400

    try:
        result = degas_client.upload_chunk(
            proj["degas_project_id"], file_uid, chunk_index, total_chunks, filename,
            file_obj.stream.read(), file_obj.content_type or "application/octet-stream",
        )
    except degas_client.DegasError as e:
        return jsonify({"error": str(e)}), 502
    return jsonify(result)


@app.route("/projects/<int:project_id>/clips/<int:clip_id>/transcribe", methods=["POST"])
def clip_transcribe(project_id, clip_id):
    db = get_db()
    proj = db.execute("SELECT * FROM projects WHERE id = ?", (project_id,)).fetchone()
    db.close()
    if proj and proj["degas_project_id"]:
        try:
            degas_client.trigger_transcribe(proj["degas_project_id"], clip_id)
        except degas_client.DegasError as e:
            return render_template("error.html", message=str(e)), 502
    return redirect(url_for("project_detail", project_id=project_id))


@app.route("/projects/<int:project_id>/transcribe-all", methods=["POST"])
def project_transcribe_all(project_id):
    db = get_db()
    proj = db.execute("SELECT * FROM projects WHERE id = ?", (project_id,)).fetchone()
    db.close()
    if proj and proj["degas_project_id"]:
        try:
            degas_client.trigger_transcribe_all(proj["degas_project_id"])
        except degas_client.DegasError as e:
            return render_template("error.html", message=str(e)), 502
    return redirect(url_for("project_detail", project_id=project_id))


@app.route("/projects/<int:project_id>/clips/<int:clip_id>/status.json")
def clip_status_json(project_id, clip_id):
    db = get_db()
    proj = db.execute("SELECT * FROM projects WHERE id = ?", (project_id,)).fetchone()
    db.close()
    if not proj or not proj["degas_project_id"]:
        return jsonify({"error": "not linked"}), 400
    try:
        return jsonify(degas_client.get_clip_status(proj["degas_project_id"], clip_id))
    except degas_client.DegasError as e:
        return jsonify({"error": str(e)}), 502


@app.route("/projects/<int:project_id>/clips/<int:clip_id>/review")
def clip_review(project_id, clip_id):
    db = get_db()
    proj = db.execute("SELECT * FROM projects WHERE id = ?", (project_id,)).fetchone()
    db.close()
    if not proj:
        return redirect(url_for("dashboard"))

    try:
        clients = hemingway_client.get_clients()
    except hemingway_client.HemingwayError as e:
        return render_template("error.html", message=str(e)), 502
    active_client = next((c for c in clients if c["id"] == proj["client_id"]), None)

    try:
        seg_data = degas_client.get_clip_segments(proj["degas_project_id"], clip_id)
    except degas_client.DegasError as e:
        return render_template("error.html", message=str(e)), 502

    return render_template(
        "clip_review.html",
        clients=clients,
        active_client=active_client,
        project=proj,
        clip_id=clip_id,
        segments=seg_data.get("current") or [],
    )


@app.route("/projects/<int:project_id>/clips/<int:clip_id>/review/save", methods=["POST"])
def clip_review_save(project_id, clip_id):
    db = get_db()
    proj = db.execute("SELECT * FROM projects WHERE id = ?", (project_id,)).fetchone()
    db.close()
    if not proj:
        return redirect(url_for("dashboard"))

    try:
        seg_data = degas_client.get_clip_segments(proj["degas_project_id"], clip_id)
    except degas_client.DegasError as e:
        return render_template("error.html", message=str(e)), 502

    current = seg_data.get("current") or []
    texts = request.form.getlist("segment_text")
    merged = []
    for i, seg in enumerate(current):
        text = texts[i] if i < len(texts) else seg.get("text", "")
        merged.append({"start": seg.get("start", 0), "end": seg.get("end", 0), "text": text})

    try:
        degas_client.save_clip_segments(proj["degas_project_id"], clip_id, merged)
    except degas_client.DegasError as e:
        return render_template("error.html", message=str(e)), 502

    return redirect(url_for("project_detail", project_id=project_id))


@app.route("/projects/<int:project_id>/clips/<int:clip_id>/export", methods=["POST"])
def clip_export(project_id, clip_id):
    style = request.form.get("style", "1")
    db = get_db()
    proj = db.execute("SELECT * FROM projects WHERE id = ?", (project_id,)).fetchone()
    db.close()
    if proj and proj["degas_project_id"]:
        try:
            degas_client.trigger_export(proj["degas_project_id"], clip_id, style)
        except degas_client.DegasError as e:
            return render_template("error.html", message=str(e)), 502
    return redirect(url_for("project_detail", project_id=project_id))


@app.route("/projects/<int:project_id>/export-all", methods=["POST"])
def project_export_all(project_id):
    style = request.form.get("style", "1")
    db = get_db()
    proj = db.execute("SELECT * FROM projects WHERE id = ?", (project_id,)).fetchone()
    db.close()
    if proj and proj["degas_project_id"]:
        try:
            degas_client.trigger_export_all(proj["degas_project_id"], style)
        except degas_client.DegasError as e:
            return render_template("error.html", message=str(e)), 502
    return redirect(url_for("project_detail", project_id=project_id))


@app.route("/projects/<int:project_id>/clips/<int:clip_id>/download")
def clip_download(project_id, clip_id):
    db = get_db()
    proj = db.execute("SELECT * FROM projects WHERE id = ?", (project_id,)).fetchone()
    db.close()
    if not proj or not proj["degas_project_id"]:
        return render_template("error.html", message="This project isn't linked to Degas."), 400

    try:
        degas_resp = degas_client.download_clip(proj["degas_project_id"], clip_id)
    except degas_client.DegasError as e:
        return render_template("error.html", message=str(e)), 502

    content_disposition = degas_resp.headers.get("Content-Disposition")
    if not content_disposition:
        return render_template("error.html", message="This clip isn't exported yet -- export it first, then download."), 400

    return Response(
        degas_resp.iter_content(chunk_size=8192),
        content_type=degas_resp.headers.get("Content-Type", "video/mp4"),
        headers={"Content-Disposition": content_disposition},
    )


@app.route("/projects/<int:project_id>/write-posts", methods=["POST"])
def project_write_posts(project_id):
    """Generates real posts from this project's actual reviewed transcript --
    the counterpart to Quick Posts (task #11), which deliberately has no
    Degas transcript involved. This is the 'Write Captions in Hemingway
    Module' step of the pipeline Ben asked for (7/24)."""
    style = request.form.get("style", "conversational")
    length = request.form.get("length", "short")

    db = get_db()
    proj = db.execute("SELECT * FROM projects WHERE id = ?", (project_id,)).fetchone()
    if not proj:
        db.close()
        return render_template("error.html", message="Project not found."), 404
    if not proj["degas_project_id"]:
        db.close()
        return render_template("error.html", message="This project isn't linked to Degas -- nothing to write from."), 400

    try:
        degas_proj = degas_client.get_project(proj["degas_project_id"])
    except degas_client.DegasError as e:
        db.close()
        return render_template("error.html", message=str(e)), 502

    transcript = _build_project_transcript(proj["degas_project_id"], degas_proj.get("clips", []))
    if not transcript.strip():
        db.close()
        return render_template("error.html", message="No reviewed transcript text yet -- transcribe and review at least one clip before writing posts."), 400

    try:
        result = hemingway_client.generate_from_transcript(
            proj["client_id"], transcript, style=style, length=length, name=proj["name"]
        )
    except hemingway_client.HemingwayError as e:
        db.close()
        return render_template("error.html", message=str(e)), 502

    db.execute(
        "UPDATE projects SET hemingway_batch_id = ?, phase = 'drafting' WHERE id = ?",
        (result["batch_id"], project_id)
    )
    for p in result["posts"]:
        if p.get("id") and p.get("body"):
            db.execute(
                """INSERT INTO posts (client_id, project_id, source, caption, status, hemingway_post_id)
                   VALUES (?, ?, 'project', ?, 'draft', ?)""",
                (proj["client_id"], project_id, p["body"], p["id"])
            )
    db.commit()
    db.close()
    return redirect(url_for("project_detail", project_id=project_id))


@app.route("/api/clients")
def api_clients():
    try:
        return jsonify(hemingway_client.get_clients())
    except hemingway_client.HemingwayError as e:
        return jsonify({"error": str(e)}), 502


# ── Glossary (task #8) ───────────────────────────────────────────────────────
@app.route("/clients/<int:client_id>/glossary")
def glossary_view(client_id):
    try:
        clients = hemingway_client.get_clients()
    except hemingway_client.HemingwayError as e:
        return render_template("error.html", message=str(e)), 502
    active_client = next((c for c in clients if c["id"] == client_id), None)
    if not active_client:
        return redirect(url_for("dashboard"))

    db = get_db()
    pending = db.execute(
        "SELECT * FROM glossary_terms WHERE client_id = ? AND status = 'pending' ORDER BY occurrence_count DESC",
        (client_id,)
    ).fetchall()
    confirmed_client = db.execute(
        "SELECT * FROM glossary_terms WHERE client_id = ? AND status = 'confirmed' ORDER BY term",
        (client_id,)
    ).fetchall()
    confirmed_global = db.execute(
        "SELECT * FROM glossary_terms WHERE client_id IS NULL AND status = 'confirmed' ORDER BY term"
    ).fetchall()
    db.close()

    return render_template(
        "glossary.html",
        clients=clients,
        active_client=active_client,
        pending=pending,
        confirmed_client=confirmed_client,
        confirmed_global=confirmed_global,
    )


@app.route("/clients/<int:client_id>/glossary/scan", methods=["POST"])
def glossary_scan(client_id):
    """Scans every Degas-linked project for this client, diffs each clip's
    original vs. current transcript, and records any new candidates as
    pending. Pull-on-click, not a background job -- same reasoning as the
    Postiz analytics design (task #16): cheap enough to run when asked,
    no reason to poll constantly."""
    db = get_db()
    projects = db.execute(
        "SELECT * FROM projects WHERE client_id = ? AND degas_project_id IS NOT NULL",
        (client_id,)
    ).fetchall()

    total_new = 0
    errors = []
    for proj in projects:
        try:
            degas_proj = degas_client.get_project(proj["degas_project_id"])
        except degas_client.DegasError as e:
            errors.append(str(e))
            continue
        for clip in degas_proj.get("clips", []):
            try:
                seg_data = degas_client.get_clip_segments(proj["degas_project_id"], clip["id"])
            except degas_client.DegasError as e:
                errors.append(str(e))
                continue
            candidates = glossary.detect_candidates(seg_data.get("original", []), seg_data.get("current", []))
            total_new += glossary.record_candidates(db, client_id, candidates)
    db.close()

    if errors:
        return render_template("error.html", message="; ".join(errors[:3])), 502
    return redirect(url_for("glossary_view", client_id=client_id, found=total_new))


@app.route("/glossary/<int:term_id>/confirm", methods=["POST"])
def glossary_confirm(term_id):
    client_id = request.form.get("client_id", type=int)
    category = request.form.get("category", "").strip()
    db = get_db()
    if category:
        db.execute(
            "UPDATE glossary_terms SET status = 'confirmed', category = ? WHERE id = ?",
            (category, term_id)
        )
    else:
        db.execute("UPDATE glossary_terms SET status = 'confirmed' WHERE id = ?", (term_id,))
    db.commit()
    db.close()
    return redirect(url_for("glossary_view", client_id=client_id))


@app.route("/glossary/<int:term_id>/reject", methods=["POST"])
def glossary_reject(term_id):
    client_id = request.form.get("client_id", type=int)
    db = get_db()
    db.execute("DELETE FROM glossary_terms WHERE id = ? AND status = 'pending'", (term_id,))
    db.commit()
    db.close()
    return redirect(url_for("glossary_view", client_id=client_id))


@app.route("/glossary/<int:term_id>/promote", methods=["POST"])
def glossary_promote(term_id):
    """Hoists a client-specific confirmed term to global -- Section 4:
    'a "promote to global" action lets you hoist something from a client
    glossary once you notice it's not actually client-specific.'"""
    client_id = request.form.get("client_id", type=int)
    db = get_db()
    db.execute(
        "UPDATE glossary_terms SET client_id = NULL WHERE id = ? AND status = 'confirmed'",
        (term_id,)
    )
    db.commit()
    db.close()
    return redirect(url_for("glossary_view", client_id=client_id))


# ── Quick posts (task #11) ───────────────────────────────────────────────────
QUICK_POST_STYLES = ["thought-leader", "conversational", "storyteller", "punchy"]
QUICK_POST_LENGTHS = ["super-short", "short", "medium", "long"]
# draft -> scheduled now happens for real via quick_posts_schedule_to_postiz
# (task #13) -- Postiz itself is the source of truth for that transition.
# scheduled -> published stays a manual confirm for now since Studio doesn't
# sync Postiz's publish webhook/state back yet (the calendar shows Postiz's
# real state directly instead -- see calendar_view).
QUICK_POST_TRANSITIONS = {"scheduled": "published"}


def _post_redirect(post, client_id):
    """Quick Posts (task #11) and project-sourced posts (task #21) share the
    same posts table and the same edit/regenerate/schedule actions -- this
    sends you back to wherever that post actually lives (its project page,
    or Quick Posts) instead of hardcoding Quick Posts for both."""
    if post and post["project_id"]:
        return redirect(url_for("project_detail", project_id=post["project_id"]))
    return redirect(url_for("quick_posts_view", client_id=client_id))


def _get_schedulable_channels(linked, allowed_identifiers=None):
    """Shared by quick_posts_view and calendar_view -- returns (channels,
    channels_error) for whichever client's Postiz group is passed in,
    filtered to a set of identifiers Studio actually knows how to schedule
    to. Defaults to the text-only set (Quick Posts has no media upload UI);
    calendar_view passes MEDIA_CAPABLE_IDENTIFIERS since it has one. Factored
    out so both screens can't drift on what "available channels" means."""
    if not linked:
        return [], None
    allowed = allowed_identifiers or postiz_client.SUPPORTED_SCHEDULE_IDENTIFIERS
    try:
        all_integrations = postiz_client.list_integrations(linked["postiz_group_id"])
        channels = [
            i for i in all_integrations
            if i["identifier"] in allowed and not i.get("disabled")
        ]
        return channels, None
    except postiz_client.PostizError as e:
        return [], str(e)


@app.route("/clients/<int:client_id>/quick-posts")
def quick_posts_view(client_id):
    try:
        clients = hemingway_client.get_clients()
    except hemingway_client.HemingwayError as e:
        return render_template("error.html", message=str(e)), 502
    active_client = next((c for c in clients if c["id"] == client_id), None)
    if not active_client:
        return redirect(url_for("dashboard"))

    db = get_db()
    quick_posts = db.execute(
        "SELECT * FROM posts WHERE client_id = ? AND source = 'quick' ORDER BY created_at DESC",
        (client_id,)
    ).fetchall()
    linked = db.execute(
        "SELECT * FROM client_postiz_groups WHERE client_id = ?", (client_id,)
    ).fetchone()
    db.close()

    channels, channels_error = _get_schedulable_channels(linked)

    return render_template(
        "quick_posts.html",
        clients=clients,
        active_client=active_client,
        quick_posts=quick_posts,
        styles=QUICK_POST_STYLES,
        lengths=QUICK_POST_LENGTHS,
        linked=linked,
        channels=channels,
        channels_error=channels_error,
    )


@app.route("/clients/<int:client_id>/quick-posts/new", methods=["POST"])
def quick_posts_new(client_id):
    """Creates a quick post: point at a Drive folder, describe what's in it,
    Hemingway writes the caption in that client's voice. No Degas transcript
    involved -- Section 9's 'Quick-post design, finalized.'"""
    drive_url = request.form.get("drive_url", "").strip()
    notes = request.form.get("notes", "").strip()
    style = request.form.get("style", "conversational")
    length = request.form.get("length", "short")

    if not notes:
        return render_template("error.html", message="Notes can't be empty -- Hemingway needs something to write about."), 400

    try:
        result = hemingway_client.generate_single_post(client_id, notes, style, length)
    except hemingway_client.HemingwayError as e:
        return render_template("error.html", message=str(e)), 502

    db = get_db()
    db.execute(
        """INSERT INTO posts (client_id, source, caption, media_ref, status, hemingway_post_id)
           VALUES (?, 'quick', ?, ?, 'draft', ?)""",
        (client_id, result["body"], drive_url, result["post_id"])
    )
    db.commit()
    db.close()
    return redirect(url_for("quick_posts_view", client_id=client_id))


@app.route("/quick-posts/<int:post_id>/regenerate", methods=["POST"])
def quick_posts_regenerate(post_id):
    client_id = request.form.get("client_id", type=int)
    instruction = request.form.get("instruction", "").strip()

    db = get_db()
    post = db.execute("SELECT * FROM posts WHERE id = ?", (post_id,)).fetchone()
    if not post or not post["hemingway_post_id"]:
        db.close()
        return render_template("error.html", message="Can't regenerate -- no linked Hemingway post found."), 400

    try:
        result = hemingway_client.rewrite_post(post["hemingway_post_id"], instruction)
    except hemingway_client.HemingwayError as e:
        db.close()
        return render_template("error.html", message=str(e)), 502

    db.execute("UPDATE posts SET caption = ? WHERE id = ?", (result["body"], post_id))
    db.commit()
    db.close()
    return _post_redirect(post, client_id)


@app.route("/quick-posts/<int:post_id>/advance", methods=["POST"])
def quick_posts_advance(post_id):
    """Minimal 3-state tracking: Draft -> Scheduled -> Published (Section 9).
    Manual for now -- becomes a real Postiz push once task #12 is built."""
    client_id = request.form.get("client_id", type=int)
    db = get_db()
    post = db.execute("SELECT * FROM posts WHERE id = ?", (post_id,)).fetchone()
    target = QUICK_POST_TRANSITIONS.get(post["status"]) if post else None
    if target:
        db.execute("UPDATE posts SET status = ? WHERE id = ?", (target, post_id))
        db.commit()
    db.close()
    return _post_redirect(post, client_id)


@app.route("/quick-posts/<int:post_id>/schedule-to-postiz", methods=["POST"])
def quick_posts_schedule_to_postiz(post_id):
    """Real draft -> scheduled transition (task #13): pushes the post to
    Postiz on the channels you pick, at the time you pick, instead of just
    flipping a local status flag. Postiz becomes the source of truth for
    whether/when this actually goes out."""
    client_id = request.form.get("client_id", type=int)
    channel_ids = request.form.getlist("channel_ids")
    send_at = request.form.get("send_at", "").strip()

    if not channel_ids or not send_at:
        return render_template("error.html", message="Pick at least one channel and a send time."), 400

    db = get_db()
    post = db.execute("SELECT * FROM posts WHERE id = ?", (post_id,)).fetchone()
    linked = db.execute("SELECT * FROM client_postiz_groups WHERE client_id = ?", (client_id,)).fetchone()
    if not post or not linked:
        db.close()
        return render_template("error.html", message="Can't schedule -- no linked Postiz group for this client."), 400

    try:
        integrations = postiz_client.list_integrations(linked["postiz_group_id"])
        by_id = {i["id"]: i for i in integrations}
        posts_payload = []
        for cid in channel_ids:
            integ = by_id.get(cid)
            if integ:
                posts_payload.append(postiz_client.build_post_item(cid, integ["identifier"], post["caption"]))
        if not posts_payload:
            db.close()
            return render_template("error.html", message="None of the selected channels could be matched to a connected integration."), 400

        # send_at comes from an HTML datetime-local input ("YYYY-MM-DDTHH:MM"),
        # which has no timezone. Treated as UTC for now -- mapping it to the
        # client's actual local timezone is a real gap, not handled yet.
        date_iso = f"{send_at}:00.000Z" if len(send_at) == 16 else send_at
        result = postiz_client.create_post("schedule", date_iso, posts_payload)
    except postiz_client.PostizError as e:
        db.close()
        return render_template("error.html", message=str(e)), 502

    postiz_ids = ",".join(str(r.get("postId")) for r in result)
    db.execute(
        "UPDATE posts SET status = 'scheduled', postiz_post_id = ?, scheduled_for = ? WHERE id = ?",
        (postiz_ids, send_at, post_id)
    )
    db.commit()
    db.close()
    return _post_redirect(post, client_id)


@app.route("/quick-posts/<int:post_id>/edit", methods=["POST"])
def quick_posts_edit(post_id):
    """Manual caption edit -- you don't always need Hemingway to touch it,
    sometimes a typo fix is faster by hand."""
    client_id = request.form.get("client_id", type=int)
    caption = request.form.get("caption", "")
    db = get_db()
    post = db.execute("SELECT * FROM posts WHERE id = ?", (post_id,)).fetchone()
    db.execute("UPDATE posts SET caption = ? WHERE id = ?", (caption, post_id))
    db.commit()
    db.close()
    return _post_redirect(post, client_id)


# ── Postiz setup (task #12) ──────────────────────────────────────────────────
@app.route("/clients/<int:client_id>/calendar")
def calendar_view(client_id):
    """Single-client calendar (task #13, Section 6: 'strictly single-client
    view, no cross-client rollup'). Reads Postiz's own /posts endpoint live
    for the selected month -- Postiz's `state` field (QUEUE/PUBLISHED/ERROR/
    DRAFT) is the real status, not something Studio tracks separately.

    Renders a full month grid (Sun-Sat, always 6 weeks) even when empty, and
    every day cell can open the create-post form scoped to that date -- Ben's
    ask (7/24) after seeing Hey Orca's calendar, where "Add Post" lives on the
    calendar itself rather than needing a separate Quick Posts screen first."""
    try:
        clients = hemingway_client.get_clients()
    except hemingway_client.HemingwayError as e:
        return render_template("error.html", message=str(e)), 502
    active_client = next((c for c in clients if c["id"] == client_id), None)
    if not active_client:
        return redirect(url_for("dashboard"))

    today = datetime.now(timezone.utc)
    month_str = request.args.get("month", "")
    try:
        year, month = map(int, month_str.split("-"))
    except ValueError:
        year, month = today.year, today.month
    month_key = f"{year:04d}-{month:02d}"

    start = datetime(year, month, 1, tzinfo=timezone.utc)
    last_day = cal_module.monthrange(year, month)[1]
    end = datetime(year, month, last_day, 23, 59, 59, tzinfo=timezone.utc)
    prev_month = (start - timedelta(days=1)).strftime("%Y-%m")
    next_month = (end + timedelta(days=1)).strftime("%Y-%m")

    # Sun-Sat grid, always full weeks (padded with adjacent-month dates),
    # same shape as Hey Orca's month view -- firstweekday=6 makes Sunday the
    # first column.
    weeks = cal_module.Calendar(firstweekday=6).monthdatescalendar(year, month)

    db = get_db()
    linked = db.execute(
        "SELECT * FROM client_postiz_groups WHERE client_id = ?", (client_id,)
    ).fetchone()
    db.close()

    # Media-capable set here (not the text-only default) -- the calendar's
    # Add Post modal has a file upload step, so Instagram/YouTube are real
    # options here even though Quick Posts doesn't offer them.
    channels, channels_error = _get_schedulable_channels(linked, postiz_client.MEDIA_CAPABLE_IDENTIFIERS)

    days = {}
    postiz_error = None
    if not linked:
        postiz_error = f"{active_client['name']} isn't linked to a Postiz group yet -- set that up first."
    else:
        try:
            resp = postiz_client.list_posts(
                start.strftime("%Y-%m-%dT00:00:00.000Z"),
                end.strftime("%Y-%m-%dT23:59:59.000Z"),
                customer=linked["postiz_group_id"],
            )
            for p in resp.get("posts", []):
                day = p["publishDate"][:10]
                time_str = p["publishDate"][11:16]
                days.setdefault(day, []).append({
                    "id": p["id"],
                    "content": p["content"],
                    "time": time_str,
                    "state": p["state"],
                    "channel_name": p.get("integration", {}).get("name", ""),
                    "channel_platform": p.get("integration", {}).get("providerIdentifier", ""),
                    "release_url": p.get("releaseURL"),
                })
        except postiz_client.PostizError as e:
            postiz_error = str(e)

    return render_template(
        "calendar.html",
        clients=clients,
        active_client=active_client,
        linked=linked,
        channels=channels,
        channels_error=channels_error,
        media_required_identifiers=list(postiz_client.MEDIA_REQUIRED_IDENTIFIERS),
        days=days,
        weeks=weeks,
        current_month=month,
        today_str=today.strftime("%Y-%m-%d"),
        postiz_error=postiz_error,
        month_label=start.strftime("%B %Y"),
        month_key=month_key,
        prev_month=prev_month,
        next_month=next_month,
    )


@app.route("/clients/<int:client_id>/calendar/create-post", methods=["POST"])
def calendar_create_post(client_id):
    """Direct "Add Post" from the calendar (task #13 follow-up, 7/24): types
    a caption and picks channels right on the calendar, like Hey Orca's
    'Create Post(s)' modal, instead of going through Quick Posts first. No
    Hemingway involved here -- this is for a caption you're writing yourself;
    Quick Posts is still the path when you want Hemingway's help drafting it.

    Now handles an optional media file (task #18): uploaded once to Postiz
    and attached to every selected channel that accepts it, since Instagram
    and YouTube both require real media -- see
    postiz_client.MEDIA_REQUIRED_IDENTIFIERS."""
    caption = request.form.get("caption", "").strip()
    channel_ids = request.form.getlist("channel_ids")
    send_at = request.form.get("send_at", "").strip()
    month_key = request.form.get("month_key", "")
    youtube_title = request.form.get("youtube_title", "").strip()
    media_file = request.files.get("media")

    if not caption or not channel_ids or not send_at:
        return render_template("error.html", message="A caption, at least one channel, and a send time are all required."), 400

    db = get_db()
    linked = db.execute("SELECT * FROM client_postiz_groups WHERE client_id = ?", (client_id,)).fetchone()
    if not linked:
        db.close()
        return render_template("error.html", message="This client isn't linked to a Postiz group yet -- set that up first."), 400

    try:
        integrations = postiz_client.list_integrations(linked["postiz_group_id"])
        by_id = {i["id"]: i for i in integrations}
        selected = [by_id[cid] for cid in channel_ids if cid in by_id]
        if not selected:
            db.close()
            return render_template("error.html", message="None of the selected channels could be matched to a connected integration."), 400

        needs_media = any(i["identifier"] in postiz_client.MEDIA_REQUIRED_IDENTIFIERS for i in selected)
        image = []
        if media_file and media_file.filename:
            uploaded = postiz_client.upload_file(media_file.stream, media_file.filename, media_file.content_type)
            image = [{"id": uploaded["id"], "path": uploaded["path"]}]
        elif needs_media:
            db.close()
            needed = [i["identifier"] for i in selected if i["identifier"] in postiz_client.MEDIA_REQUIRED_IDENTIFIERS]
            return render_template("error.html", message=f"{', '.join(needed)} requires an image or video attached -- none was uploaded."), 400

        posts_payload = [
            postiz_client.build_post_item(
                i["id"], i["identifier"], caption,
                image=image, extra={"title": youtube_title},
            )
            for i in selected
        ]

        date_iso = f"{send_at}:00.000Z" if len(send_at) == 16 else send_at
        result = postiz_client.create_post("schedule", date_iso, posts_payload)
    except postiz_client.PostizError as e:
        db.close()
        return render_template("error.html", message=str(e)), 502

    postiz_ids = ",".join(str(r.get("postId")) for r in result)
    db.execute(
        """INSERT INTO posts (client_id, source, caption, status, postiz_post_id, scheduled_for)
           VALUES (?, 'quick', ?, 'scheduled', ?, ?)""",
        (client_id, caption, postiz_ids, send_at)
    )
    db.commit()
    db.close()
    return redirect(url_for("calendar_view", client_id=client_id, month=month_key))


@app.route("/clients/<int:client_id>/postiz-setup")
def postiz_setup_view(client_id):
    """Links a Hemingway client to a Postiz customer group. Real scheduling
    (picking a channel + send time and pushing a post) lands with the
    calendar (task #13) -- this screen is just the one-time account linkage
    each client needs before that can work."""
    try:
        clients = hemingway_client.get_clients()
    except hemingway_client.HemingwayError as e:
        return render_template("error.html", message=str(e)), 502
    active_client = next((c for c in clients if c["id"] == client_id), None)
    if not active_client:
        return redirect(url_for("dashboard"))

    connected = None
    groups = []
    postiz_error = None
    try:
        connected = postiz_client.is_connected()
        if connected:
            groups = postiz_client.list_groups()
    except postiz_client.PostizError as e:
        postiz_error = str(e)

    db = get_db()
    linked = db.execute(
        "SELECT * FROM client_postiz_groups WHERE client_id = ?", (client_id,)
    ).fetchone()
    db.close()

    integrations = []
    integrations_error = None
    if linked:
        try:
            integrations = postiz_client.list_integrations(linked["postiz_group_id"])
        except postiz_client.PostizError as e:
            integrations_error = str(e)

    return render_template(
        "postiz_setup.html",
        clients=clients,
        active_client=active_client,
        connected=connected,
        postiz_error=postiz_error,
        groups=groups,
        linked=linked,
        integrations=integrations,
        integrations_error=integrations_error,
    )


@app.route("/clients/<int:client_id>/postiz-setup/link", methods=["POST"])
def postiz_setup_link(client_id):
    group_id = request.form.get("group_id", "").strip()
    group_name = request.form.get("group_name", "").strip()
    if not group_id:
        return redirect(url_for("postiz_setup_view", client_id=client_id))

    db = get_db()
    db.execute(
        """INSERT INTO client_postiz_groups (client_id, postiz_group_id, postiz_group_name, updated_at)
           VALUES (?, ?, ?, CURRENT_TIMESTAMP)
           ON CONFLICT(client_id) DO UPDATE SET
             postiz_group_id = excluded.postiz_group_id,
             postiz_group_name = excluded.postiz_group_name,
             updated_at = CURRENT_TIMESTAMP""",
        (client_id, group_id, group_name)
    )
    db.commit()
    db.close()
    return redirect(url_for("postiz_setup_view", client_id=client_id))


# ── Startup ───────────────────────────────────────────────────────────────────
_initialized = False


@app.before_request
def ensure_initialized():
    global _initialized
    if not _initialized:
        init_db()
        _initialized = True


if __name__ == "__main__":
    port = int(os.environ.get("PORT", 5000))
    app.run(host="0.0.0.0", port=port, debug=False)
