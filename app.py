import os

from flask import (
    Flask, render_template, request, redirect,
    url_for, session, jsonify
)

from database import init_db, get_db
import hemingway_client
import degas_client
import glossary

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
    active_client = None
    projects = []
    degas_error = None
    if clients:
        active_client = next(
            (c for c in clients if c["id"] == active_client_id),
            clients[0]
        )
        db = get_db()
        rows = db.execute(
            "SELECT * FROM projects WHERE client_id = ? ORDER BY created_at DESC",
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
            })
        db.close()

    return render_template(
        "dashboard.html",
        clients=clients,
        active_client=active_client,
        projects=projects,
        degas_error=degas_error,
        phase_labels=PHASE_LABELS,
    )


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
QUICK_POST_TRANSITIONS = {"draft": "scheduled", "scheduled": "published"}


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
    db.close()

    return render_template(
        "quick_posts.html",
        clients=clients,
        active_client=active_client,
        quick_posts=quick_posts,
        styles=QUICK_POST_STYLES,
        lengths=QUICK_POST_LENGTHS,
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
    return redirect(url_for("quick_posts_view", client_id=client_id))


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
    return redirect(url_for("quick_posts_view", client_id=client_id))


@app.route("/quick-posts/<int:post_id>/edit", methods=["POST"])
def quick_posts_edit(post_id):
    """Manual caption edit -- you don't always need Hemingway to touch it,
    sometimes a typo fix is faster by hand."""
    client_id = request.form.get("client_id", type=int)
    caption = request.form.get("caption", "")
    db = get_db()
    db.execute("UPDATE posts SET caption = ? WHERE id = ?", (caption, post_id))
    db.commit()
    db.close()
    return redirect(url_for("quick_posts_view", client_id=client_id))


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
