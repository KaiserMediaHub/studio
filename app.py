import os

from flask import (
    Flask, render_template, request, redirect,
    url_for, session, jsonify
)

from database import init_db, get_db
import hemingway_client
import degas_client

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
